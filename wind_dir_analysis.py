#!/usr/bin/env python3
"""
wind_dir_analysis.py
====================
Exhaustive wind-direction RAM-address finder for Deluxe Ski Jump 2 in DOSBox.

KEY ASSUMPTION: NONE.
The visual wind angle is a DISPLAY VALUE computed by the game from something
in RAM — it may be an RNG seed, a tick counter, a trig component, or a raw
angle in non-degree units.  We make NO assumption about the encoding.

APPROACH
--------
1. Snapshot the full 16 MB DOS RAM (baseline + 7 angle-tagged snapshots).
2. Diff every snapshot against the baseline — any address that encodes wind
   direction MUST change when the wind changes.
3. Run four exhaustive analysis passes on the changed-address set:
     A. Direct / scaled / modular / trig transforms
     B. Spearman rank correlation
     C. Linear regression  (angle = a*v + b)
     D. Vector-pair atan2  (f32, i16, u16, i8 — catches sin/cos storage)
4. Print a ranked report.

Raw snapshots are always saved to an .npz file so the analysis can be
re-run offline with adjusted thresholds.

Usage:
    sudo python3 wind_dir_analysis.py
"""

import os
import sys
import struct
import math
import time
import glob
from datetime import datetime

import gzip
import pickle

try:
    import numpy as np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
BASE_ADDR          = 0x7025ccbff010   # default; verified / re-discovered at runtime
RAM_SIZE           = 16 * 1024 * 1024 # 16 MB full DOS RAM snapshot
N_SNAPSHOTS        = 7                # angle-tagged snapshots to collect
MAE_THRESHOLD      = 15.0             # degrees — max mean circular error to report
SPEARMAN_THRESHOLD = 0.85             # |r| floor for Spearman table
PROCESS_NAME       = "dosbox"         # case-insensitive cmdline substring
WIND_SPEED_OFF     = 0x27363          # confirmed offset of wind-speed ASCII string
                                      # (used only for BASE_ADDR verification)
SAVE_DIR           = "."              # directory for .npz snapshot files
# ══════════════════════════════════════════════════════════════════════════════


# ─── CIRCULAR ANGLE UTILITIES ─────────────────────────────────────────────────

def circ_err(a: float, b: float) -> float:
    """Shortest angular distance between a and b in degrees (result 0–180)."""
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def angular_mae(preds, actuals) -> float:
    """Mean circular angular error over paired (prediction, actual) sequences."""
    if not preds or len(preds) != len(actuals):
        return float('inf')
    return sum(circ_err(p, a) for p, a in zip(preds, actuals)) / len(preds)


# ─── STATISTICS UTILITIES ─────────────────────────────────────────────────────

def spearman_r(xs, ys) -> float:
    """
    Spearman rank correlation with average-rank tie handling.
    Returns 0 if either sequence is constant or has fewer than 3 elements.
    """
    n = len(xs)
    if n < 3:
        return 0.0

    def _ranks(seq):
        order = sorted(range(n), key=lambda i: seq[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and seq[order[j]] == seq[order[j + 1]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = _ranks(xs), _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num  = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx   = math.sqrt(sum((v - mx) ** 2 for v in rx))
    dy   = math.sqrt(sum((v - my) ** 2 for v in ry))
    return 0.0 if dx * dy < 1e-12 else num / (dx * dy)


def linear_fit(xs, ys):
    """
    OLS fit: returns (slope, intercept) for y ≈ slope*x + intercept.
    Returns (None, None) when degenerate.
    """
    n = len(xs)
    if n < 2:
        return None, None
    sx  = sum(xs);  sy  = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    d   = n * sxx - sx * sx
    if abs(d) < 1e-12:
        return None, None
    a = (n * sxy - sx * sy) / d
    b = (sy - a * sx) / n
    return a, b


# ─── PROCESS + MEMORY HELPERS ─────────────────────────────────────────────────

def find_pid(name: str):
    """Return PID of the first process whose cmdline contains name (case-insensitive)."""
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            with open(f'/proc/{entry}/cmdline', 'rb') as f:
                cmd = f.read().replace(b'\x00', b' ').decode(errors='ignore')
            if name.lower() in cmd.lower():
                return int(entry)
        except OSError:
            pass
    return None


def verify_base(pid: int, base: int):
    """
    Try to confirm base is the correct DOS RAM start.

    Strategy (in order):
      1. Best: wind-speed ASCII string at BASE+WIND_SPEED_OFF looks valid
         (digits and dots, e.g. "2.1.0.0") → game is at pre-jump screen.
      2. Acceptable: address is readable at all → game is at menu / loading.
         Returns "(readable – navigate to hill to confirm)" in this case.
      3. Failure: address unreadable (PermissionError, wrong PID, etc.) → None.
    """
    try:
        with open(f'/proc/{pid}/mem', 'rb') as f:
            f.seek(base + WIND_SPEED_OFF)
            raw = f.read(16)
    except OSError:
        return None                                   # address not readable

    stripped = raw.split(b'\x00')[0]
    s = stripped.decode('ascii', errors='ignore').strip()
    if len(s) >= 3 and all(c in '0123456789.' for c in s):
        return s                                       # confirmed: wind string present

    # Readable but no wind string — game is probably at menu/title screen.
    return "(readable – navigate to hill to confirm)"


def find_base_auto(pid: int):
    """
    Scan /proc/<pid>/maps for rw-p anonymous regions ≥ 8 MB
    (DOS emulated RAM is 16 MB; filter out smaller regions).
    Probe each as a candidate BASE_ADDR; prefer the one with a valid wind string,
    fall back to any readable one.
    Returns (base_addr, status_string) or (None, None).
    """
    candidates = []
    try:
        with open(f'/proc/{pid}/maps') as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2 or 'rw' not in parts[1]:
                    continue
                try:
                    s, e = parts[0].split('-')
                    start, size = int(s, 16), int(e, 16) - int(s, 16)
                    if size >= 8 * 1024 * 1024:        # ≥ 8 MB: candidate for DOS RAM
                        candidates.append((start, size))
                except ValueError:
                    pass
    except OSError:
        pass

    # Sort: largest region first (most likely to be the 16 MB DOS RAM block)
    candidates.sort(key=lambda x: -x[1])

    best_fallback = None
    for base, _ in candidates:
        ws = verify_base(pid, base)
        if ws is None:
            continue
        if 'readable' not in ws:                       # strong match: wind string present
            return base, ws
        if best_fallback is None:
            best_fallback = (base, ws)                 # weak match: keep as fallback

    if best_fallback:
        return best_fallback
    return None, None


def read_ram(pid: int, base: int) -> bytes:
    """Read RAM_SIZE bytes from BASE via /proc/<pid>/mem.  Exits on failure."""
    try:
        with open(f'/proc/{pid}/mem', 'rb') as f:
            f.seek(base)
            data = f.read(RAM_SIZE)
        if len(data) < RAM_SIZE // 2:
            print(f"\n  [error] Very short read: {len(data)} of {RAM_SIZE} bytes.")
            sys.exit(1)
        if len(data) < RAM_SIZE:
            data = data + bytes(RAM_SIZE - len(data))   # pad to full size
        return data
    except PermissionError:
        print("\n  [error] Permission denied — run: sudo python3 wind_dir_analysis.py")
        sys.exit(1)
    except OSError as e:
        print(f"\n  [error] Memory read failed: {e}")
        sys.exit(1)


def read_wind_string(pid: int, base: int) -> str:
    try:
        with open(f'/proc/{pid}/mem', 'rb') as f:
            f.seek(base + WIND_SPEED_OFF)
            return f.read(16).split(b'\x00')[0].decode('ascii', errors='ignore').strip()
    except OSError:
        return '?'


# ─── SNAPSHOT SAVE / LOAD ─────────────────────────────────────────────────────
# Format: gzip-compressed pickle of a plain dict.
# No numpy dependency; compresses 128 MB of DOS RAM to ~30–60 MB typically.

def save_snapshots(pid: int, base: int, baseline: bytes, snapshots) -> str:
    """
    Save baseline + N snapshots to a gzip-compressed pickle file (.pkl.gz).
    snapshots: list of (angle, bytes) tuples.
    Returns the path written.
    """
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SAVE_DIR, f"dsj2_wind_{ts}.pkl.gz")

    payload = {
        'pid':       pid,
        'base_addr': base,
        'ram_size':  len(baseline),
        'baseline':  baseline,
        'snapshots': snapshots,          # list of (angle, bytes)
    }
    print(f"  Compressing and writing {len(snapshots)+1} × "
          f"{len(baseline)//1024//1024} MB snapshots...", end=' ', flush=True)
    with gzip.open(path, 'wb', compresslevel=1) as f:  # level-1: fast, still ~40% smaller
        pickle.dump(payload, f, protocol=4)
    mb = os.path.getsize(path) / 1024 / 1024
    print(f"{mb:.1f} MB")
    return path


def load_snapshots(path: str):
    """
    Load a .pkl.gz file written by save_snapshots.
    Returns (pid, base, baseline_bytes, [(angle, snap_bytes), ...]).
    """
    print(f"  Decompressing {path}...", end=' ', flush=True)
    with gzip.open(path, 'rb') as f:
        payload = pickle.load(f)
    print("OK")
    pid       = payload['pid']
    base      = payload['base_addr']
    baseline  = payload['baseline']
    snapshots = payload['snapshots']    # list of (angle, bytes)
    return pid, base, baseline, snapshots


def find_save_files():
    return sorted(glob.glob(os.path.join(SAVE_DIR, "dsj2_wind_*.pkl.gz")))


# ─── INTERACTIVE SNAPSHOT COLLECTION ──────────────────────────────────────────

def collect_snapshots(pid: int, base: int):
    """
    Interactively collect one baseline + N_SNAPSHOTS angle-tagged 16 MB snapshots.
    Returns (baseline_bytes, [(angle, snap_bytes), ...]).
    """
    print()
    print("─" * 72)
    print("  SNAPSHOT COLLECTION")
    print("─" * 72)
    print()
    print("  Workflow: enter a hill, note the wind direction arrow, type the angle,")
    print("  press Enter.  Re-enter the SAME hill for each subsequent snapshot so")
    print("  only wind-related memory changes.")
    print()
    print("  Angle: read exactly what the game displays (0–360).  We make no")
    print("  assumptions about convention; use whatever the game shows.")
    print()

    # Baseline
    input("  ── BASELINE ──  Stand at the pre-jump screen, press Enter: ")
    print("  Reading 16 MB baseline...", end=' ', flush=True)
    baseline = read_ram(pid, base)
    ws = read_wind_string(pid, base)
    print(f"OK  (wind string: \"{ws}\")")
    print()

    snapshots = []
    for i in range(1, N_SNAPSHOTS + 1):
        print(f"  ── Snapshot {i}/{N_SNAPSHOTS} ──")
        print("  Exit to menu, re-enter the SAME hill.  Wait for the wind arrow.")

        while True:
            raw = input("  Type the angle shown (0–360) and press Enter: ").strip()
            try:
                angle = int(round(float(raw)))
                if 0 <= angle <= 360:
                    break
                print("  Out of range — enter a value from 0 to 360.")
            except ValueError:
                print(f"  Cannot parse '{raw}' — enter a number.")

        print(f"  Reading 16 MB snapshot...", end=' ', flush=True)
        data = read_ram(pid, base)
        ws   = read_wind_string(pid, base)
        print(f"OK  (wind string: \"{ws}\")")
        snapshots.append((angle, data))
        print()

    return baseline, snapshots


# ─── DIFF ─────────────────────────────────────────────────────────────────────

def build_candidate_sets(baseline: bytes, snapshots):
    """
    Diff each snapshot against baseline using 4-byte aligned comparison.

    Returns:
      union_offsets : sorted list of byte offsets (4-byte aligned) changed in ≥ 1 snap
      core_offsets  : sorted list changed in ALL snapshots
      per_snap_sets : list of sets of byte offsets (one per snapshot)
    """
    n_chunks = RAM_SIZE // 4
    # Work in 4-byte chunks for speed
    if _HAS_NP:
        base_u32 = np.frombuffer(baseline[:n_chunks * 4], dtype=np.uint32)
        per_snap_sets = []
        for _, snap in snapshots:
            snap_u32 = np.frombuffer(snap[:n_chunks * 4], dtype=np.uint32)
            diff_chunks = set((np.where(base_u32 != snap_u32)[0] * 4).tolist())
            per_snap_sets.append(diff_chunks)
    else:
        per_snap_sets = []
        for _, snap in snapshots:
            diff = set()
            for i in range(0, n_chunks * 4, 4):
                if baseline[i:i+4] != snap[i:i+4]:
                    diff.add(i)
            per_snap_sets.append(diff)

    union_set = set()
    for s in per_snap_sets:
        union_set |= s

    core_set = per_snap_sets[0].copy()
    for s in per_snap_sets[1:]:
        core_set &= s

    return sorted(union_set), sorted(core_set), per_snap_sets


# ─── VALUE EXTRACTION HELPERS ─────────────────────────────────────────────────

def _u8(data, off):
    return data[off] if off < len(data) else None

def _i8(data, off):
    return struct.unpack_from('b', data, off)[0] if off < len(data) else None

def _u16(data, off):
    return struct.unpack_from('<H', data, off)[0] if off + 2 <= len(data) else None

def _i16(data, off):
    return struct.unpack_from('<h', data, off)[0] if off + 2 <= len(data) else None

def _u32(data, off):
    return struct.unpack_from('<I', data, off)[0] if off + 4 <= len(data) else None

def _i32(data, off):
    return struct.unpack_from('<i', data, off)[0] if off + 4 <= len(data) else None

def _f32(data, off):
    if off + 4 > len(data):
        return None
    v = struct.unpack_from('<f', data, off)[0]
    return None if (math.isnan(v) or math.isinf(v) or abs(v) > 1e9) else v

def _nibble_swap(v: int) -> int:
    return ((v & 0x0F) << 4) | ((v >> 4) & 0x0F)

_BIT_REV_TABLE = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))

def _collect_vals(snapshots, getter, byte_off):
    """
    Return list of values (one per snapshot) at byte_off, or None if any is None.
    """
    vals = [getter(snap, byte_off) for _, snap in snapshots]
    return None if any(v is None for v in vals) else vals


# ─── PASS A: DIRECT / SCALED / MODULAR / TRIG ─────────────────────────────────

# Integer scale factors applied as  (value × k) % 360
_INT_SCALES = [
    ('×1',          1.0),
    ('×2',          2.0),
    ('×0.5',        0.5),
    ('×4',          4.0),
    ('×360/255',    360.0 / 255.0),
    ('×360/128',    360.0 / 128.0),
    ('×360/64',     360.0 / 64.0),
    ('×180/255',    180.0 / 255.0),
    ('×180/128',    180.0 / 128.0),
    ('÷10',         0.1),
    ('÷100',        0.01),
    ('÷256',        1.0 / 256.0),
    ('÷1000',       1.0 / 1000.0),
    ('÷1024',       1.0 / 1024.0),
    ('÷65536',      1.0 / 65536.0),
    ('÷10000',      1.0 / 10000.0),
    ('÷360',        1.0 / 360.0),       # value already in 0–1 fraction of circle
]

# Additive offsets applied as  (value + c) % 360
_MOD_OFFSETS = [0, 45, 90, 135, 180, 225, 270, 315]


def _apply_int_transforms(vals):
    """
    Yield (transform_name, predicted_angles_list) for every integer transform.
    vals: list of numeric values (one per snapshot).
    """
    fv = [float(v) for v in vals]

    # Scale transforms
    for name, k in _INT_SCALES:
        yield name, [(v * k) % 360.0 for v in fv]

    # Modular offset transforms
    for c in _MOD_OFFSETS:
        label = f'+{c}%360' if c else '%360'
        yield label, [(v + c) % 360.0 for v in fv]

    # Byte-manipulation transforms (only for values that fit in a byte)
    iv = [int(v) & 0xFF for v in vals]
    yield 'nibble_swap×360/255', [float(_nibble_swap(b)) * (360.0 / 255.0) for b in iv]
    yield 'bit_rev×360/255',     [float(_BIT_REV_TABLE[b]) * (360.0 / 255.0) for b in iv]

    # Byte-swap for 16-bit values
    iv16 = [int(v) & 0xFFFF for v in vals]
    yield 'byteswap÷65535×360', [
        float(((b & 0xFF) << 8) | (b >> 8)) * (360.0 / 65535.0) % 360.0
        for b in iv16
    ]


def _apply_f32_transforms(vals):
    """Yield (transform_name, predicted_angles_list) for float32 values."""
    # Direct degree-scale variants
    for name, k in [('×1', 1.0), ('×2', 2.0), ('×0.5', 0.5), ('×4', 4.0),
                    ('×10', 10.0), ('×0.1', 0.1), ('×100', 100.0), ('×0.01', 0.01)]:
        yield name, [(v * k) % 360.0 for v in vals]

    # Radians → degrees
    deg = [math.degrees(v) % 360.0 for v in vals]
    yield 'rad2deg', deg
    for c in [45, 90, 135, 180, 225, 270, 315]:
        yield f'rad2deg+{c}', [(d + c) % 360.0 for d in deg]

    # Direct degree offsets
    for c in [45, 90, 135, 180, 225, 270, 315]:
        yield f'+{c}%360', [(v + c) % 360.0 for v in vals]

    # Trig inverses — only meaningful when |v| ≤ 1
    if all(abs(v) <= 1.0 for v in vals):
        def _safe_acos(v):
            return math.degrees(math.acos(max(-1.0, min(1.0, v))))
        def _safe_asin(v):
            return math.degrees(math.asin(max(-1.0, min(1.0, v))))

        yield 'acos_deg',          [_safe_acos(v) for v in vals]
        yield 'acos_deg+180',      [(_safe_acos(v) + 180.0) % 360.0 for v in vals]
        yield 'asin_deg',          [_safe_asin(v) % 360.0 for v in vals]
        yield '180-asin_deg',      [(180.0 - _safe_asin(v)) % 360.0 for v in vals]
        yield '360-acos_deg',      [(360.0 - _safe_acos(v)) % 360.0 for v in vals]

    # atan (all real values)
    yield 'atan_deg',  [math.degrees(math.atan(v)) % 360.0 for v in vals]
    yield 'atan_deg+180', [(math.degrees(math.atan(v)) + 180.0) % 360.0 for v in vals]


def analyze_pass_a(snapshots, union_offsets, angles):
    """
    For every candidate offset, test every transform on every numeric type.
    Reports (offset, type, transform, MAE, predictions) for MAE < MAE_THRESHOLD.
    """
    candidates = []
    n = len(snapshots)

    print(f"  [Pass A] Transforms on {len(union_offsets)} candidate offsets "
          f"({n} snapshots)...")

    for off in union_offsets:

        # ── u8 / i8 at each of the 4 byte positions within the 4-byte chunk ──
        for sub in range(4):
            boff = off + sub

            for dtype, getter in [('u8', _u8), ('i8', _i8)]:
                vals = _collect_vals(snapshots, getter, boff)
                if vals is None or max(vals) == min(vals):
                    continue
                for tname, preds in _apply_int_transforms(vals):
                    if any(math.isnan(p) or math.isinf(p) for p in preds):
                        continue
                    mae = angular_mae(preds, angles)
                    if mae < MAE_THRESHOLD:
                        candidates.append(
                            (mae, boff, dtype, tname, [int(p) % 360 for p in preds]))

        # ── u16 / i16 at byte positions 0 and 2 ──
        for sub in range(0, 4, 2):
            boff = off + sub
            for dtype, getter in [('u16', _u16), ('i16', _i16)]:
                vals = _collect_vals(snapshots, getter, boff)
                if vals is None or max(vals) == min(vals):
                    continue
                for tname, preds in _apply_int_transforms(vals):
                    if any(math.isnan(p) or math.isinf(p) for p in preds):
                        continue
                    mae = angular_mae(preds, angles)
                    if mae < MAE_THRESHOLD:
                        candidates.append(
                            (mae, boff, dtype, tname, [int(p) % 360 for p in preds]))

        # ── u32 / i32 at the 4-byte-aligned offset ──
        for dtype, getter in [('u32', _u32), ('i32', _i32)]:
            vals = _collect_vals(snapshots, getter, off)
            if vals is None or max(vals) == min(vals):
                continue
            for tname, preds in _apply_int_transforms(vals):
                if any(math.isnan(p) or math.isinf(p) for p in preds):
                    continue
                mae = angular_mae(preds, angles)
                if mae < MAE_THRESHOLD:
                    candidates.append(
                        (mae, off, dtype, tname, [int(p) % 360 for p in preds]))

        # ── f32 ──
        vals = _collect_vals(snapshots, _f32, off)
        if vals is not None and max(vals) != min(vals):
            for tname, preds in _apply_f32_transforms(vals):
                if any(math.isnan(p) or math.isinf(p) for p in preds):
                    continue
                mae = angular_mae(preds, angles)
                if mae < MAE_THRESHOLD:
                    candidates.append(
                        (mae, off, 'f32', tname, [int(p) % 360 for p in preds]))

    print(f"  [Pass A] Done — {len(candidates)} hits.")
    return candidates


# ─── PASS B: SPEARMAN RANK CORRELATION ────────────────────────────────────────

def analyze_pass_b(snapshots, union_offsets, angles):
    """
    Compute Spearman r between each (offset, type) value sequence and the
    angle sequence.  Reports (|r|, offset, dtype, r, values) for |r| ≥ threshold.
    """
    candidates = []

    print(f"  [Pass B] Spearman correlation on {len(union_offsets)} offsets...")

    for off in union_offsets:
        checks = []
        # sub-byte types
        for sub in range(4):
            boff = off + sub
            checks += [('u8', _u8, boff), ('i8', _i8, boff)]
        # 16-bit types
        for sub in range(0, 4, 2):
            boff = off + sub
            checks += [('u16', _u16, boff), ('i16', _i16, boff)]
        # 32-bit types
        checks += [('u32', _u32, off), ('i32', _i32, off), ('f32', _f32, off)]

        for dtype, getter, boff in checks:
            vals = _collect_vals(snapshots, getter, boff)
            if vals is None or max(vals) == min(vals):
                continue
            r = spearman_r(vals, angles)
            if abs(r) >= SPEARMAN_THRESHOLD:
                candidates.append((abs(r), boff, dtype, r, vals))

    print(f"  [Pass B] Done — {len(candidates)} correlated addresses.")
    return candidates


# ─── PASS C: LINEAR REGRESSION ────────────────────────────────────────────────

def analyze_pass_c(snapshots, union_offsets, angles):
    """
    OLS fit: visual_angle = a*v + b.  Reports when circular MAE < MAE_THRESHOLD.
    Catches linear encodings not covered by the fixed scale grid in Pass A.
    """
    candidates = []
    ang_f = [float(a) for a in angles]

    print(f"  [Pass C] Linear regression on {len(union_offsets)} offsets...")

    for off in union_offsets:
        checks = []
        for sub in range(4):
            boff = off + sub
            checks += [('u8', _u8, boff), ('i8', _i8, boff)]
        for sub in range(0, 4, 2):
            boff = off + sub
            checks += [('u16', _u16, boff), ('i16', _i16, boff)]
        checks += [('u32', _u32, off), ('i32', _i32, off), ('f32', _f32, off)]

        for dtype, getter, boff in checks:
            vals = _collect_vals(snapshots, getter, boff)
            if vals is None:
                continue
            fv = [float(v) for v in vals]
            if max(fv) - min(fv) < 1e-9:
                continue
            a, b = linear_fit(fv, ang_f)
            if a is None:
                continue
            preds = [(a * v + b) % 360.0 for v in fv]
            mae = angular_mae(preds, ang_f)
            if mae < MAE_THRESHOLD:
                tname = f'linreg(×{a:.4g}+{b:.2f})'
                candidates.append(
                    (mae, boff, dtype, tname, [int(p) % 360 for p in preds]))

    print(f"  [Pass C] Done — {len(candidates)} regression hits.")
    return candidates


# ─── PASS D: VECTOR-PAIR ATAN2 ────────────────────────────────────────────────

def analyze_pass_d(snapshots, union_offsets, angles):
    """
    For every candidate offset, probe nearby offsets (±stride × 1..4) for a
    second component and compute atan2(v2, v1) and atan2(v1, v2) with
    rotational offsets 0°, 90°, 180°, 270°.

    Tests f32 (stride 4), i16/u16 (stride 2), i8/u8 (stride 1) pairs.
    The second component need NOT be in the candidate set — wind components
    may move together in a way where one barely changes.

    atan2 is scale-invariant so integer pairs need no normalisation.
    """
    candidates = []

    print(f"  [Pass D] Vector-pair atan2 on {len(union_offsets)} offsets...")

    def _test_pair(off1, off2, xv, yv, dtype_label):
        """Try atan2(y,x) and atan2(x,y) with 4 rotational offsets."""
        if max(xv) == min(xv) and max(yv) == min(yv):
            return                          # both constant — useless pair
        for (aa, bb, orient) in [(yv, xv, 'atan2(y,x)'), (xv, yv, 'atan2(x,y)')]:
            for rot in [0, 90, 180, 270]:
                preds = []
                ok = True
                for x_val, y_val in zip(bb, aa):
                    try:
                        raw = math.degrees(math.atan2(float(y_val), float(x_val)))
                        preds.append((raw + rot + 360.0) % 360.0)
                    except (ValueError, OverflowError):
                        ok = False
                        break
                if not ok:
                    continue
                mae = angular_mae(preds, angles)
                if mae < MAE_THRESHOLD:
                    rot_s = f'+{rot}°' if rot else ''
                    tname = f'{orient}{rot_s} [@+{off2-off1:+d}]'
                    candidates.append(
                        (mae, off1, f'{dtype_label}pair', tname,
                         [int(p) % 360 for p in preds]))

    # Build value caches for each type at every UNION offset
    # (and nearby neighbours, even outside UNION)
    def _probe(getter, off):
        """Read values at 'off' across all snapshots.  Returns list or None."""
        return _collect_vals(snapshots, getter, off)

    type_configs = [
        ('f32', _f32,  4, [4, 8, 12, 16, -4, -8, -12, -16]),
        ('i16', _i16,  2, [2, 4, 6, 8, -2, -4, -6, -8]),
        ('u16', _u16,  2, [2, 4, 6, 8, -2, -4, -6, -8]),
        ('i8',  _i8,   1, [1, 2, 3, 4, -1, -2, -3, -4]),
        ('u8',  _u8,   1, [1, 2, 3, 4, -1, -2, -3, -4]),
    ]

    for off1 in union_offsets:
        for dtype, getter, stride, deltas in type_configs:
            # Sub-byte offset for byte-width types
            for sub in ([0] if stride > 1 else range(4)):
                boff1 = off1 + sub
                xv = _probe(getter, boff1)
                if xv is None or max(xv) == min(xv):
                    continue

                for delta in deltas:
                    boff2 = boff1 + delta
                    if boff2 < 0 or boff2 + stride > RAM_SIZE:
                        continue
                    yv = _probe(getter, boff2)
                    if yv is None:
                        continue
                    _test_pair(boff1, boff2, xv, yv, dtype)

    print(f"  [Pass D] Done — {len(candidates)} pair hits.")
    return candidates


# ─── REPORT ───────────────────────────────────────────────────────────────────

def print_report(mae_cands, sp_cands, angles, union_offsets, core_offsets,
                 per_snap_sets, npz_path):
    core_set = set(core_offsets)
    n = len(angles)

    # Per-snapshot change counts for diff summary
    counts = [len(s) for s in per_snap_sets]

    print()
    print("═" * 112)
    print("  DIFF SUMMARY")
    print("═" * 112)
    print(f"  Changed in ALL {n} snapshots (CORE) : {len(core_offsets):5d} offsets")
    print(f"  Changed in ≥ 1  snapshot  (UNION)  : {len(union_offsets):5d} offsets")
    print(f"  Changes per snapshot: {counts}")
    print()
    if core_offsets:
        shown = core_offsets[:40]
        print("  CORE offsets (4-byte aligned, first 40):")
        line = "    " + "  ".join(f"0x{o:05x}" for o in shown)
        print(line)
        if len(core_offsets) > 40:
            print(f"    … and {len(core_offsets) - 40} more")
    print(f"\n  Snapshot file : {npz_path}")
    print()

    # ── Deduplicate MAE candidates ──
    best_mae = {}
    for item in mae_cands:
        key = (item[1], item[2], item[3])
        if key not in best_mae or item[0] < best_mae[key][0]:
            best_mae[key] = item
    sorted_mae = sorted(best_mae.values(), key=lambda x: x[0])

    # ── MAE table ──
    print("═" * 112)
    print(f"  TOP CANDIDATES  (mean circular error < {MAE_THRESHOLD:.0f}°,  "
          f"sorted by MAE ascending)")
    print("═" * 112)
    HDR = (f"  {'rk':>3}  {'*':1}  {'offset':>8}  {'type':>8}  "
           f"{'transform':<36}  {'MAE°':>5}  predictions  vs  actuals")
    print(HDR)
    print("  " + "─" * 108)

    actuals_str = str(angles)
    for rank, (mae, off, dtype, tname, preds) in enumerate(sorted_mae[:60], 1):
        star = '*' if off in core_set else ' '
        print(f"  {rank:>3}  {star}  0x{off:05x}    {dtype:>8}  "
              f"{tname:<36}  {mae:>5.1f}  {preds}  vs  {actuals_str}")

    if not sorted_mae:
        print(f"  (no candidates under {MAE_THRESHOLD}° — try increasing MAE_THRESHOLD)")

    # ── Spearman table ──
    best_sp = {}
    for item in sp_cands:
        key = (item[1], item[2])
        if key not in best_sp or item[0] > best_sp[key][0]:
            best_sp[key] = item
    sorted_sp = sorted(best_sp.values(), key=lambda x: -x[0])

    print()
    print("═" * 112)
    print(f"  SPEARMAN RANK CORRELATIONS  (|r| ≥ {SPEARMAN_THRESHOLD},  "
          f"sorted by |r| descending)")
    print("═" * 112)
    HDR2 = (f"  {'rk':>3}  {'*':1}  {'offset':>8}  {'type':>8}  "
            f"{'|r|':>6}  {'r':>7}  values across snapshots")
    print(HDR2)
    print("  " + "─" * 100)

    for rank, (abs_r, off, dtype, r, vals) in enumerate(sorted_sp[:40], 1):
        star = '*' if off in core_set else ' '
        vfmt = [round(v, 4) if isinstance(v, float) else v for v in vals]
        print(f"  {rank:>3}  {star}  0x{off:05x}    {dtype:>8}  "
              f"{abs_r:>6.3f}  {r:>7.3f}  {vfmt}")

    if not sorted_sp:
        print(f"  (none found — try lowering SPEARMAN_THRESHOLD)")

    # ── Legend ──
    print()
    print("═" * 112)
    print("  LEGEND")
    print("═" * 112)
    print("  *         = address changed in ALL snapshots (CORE) — highest confidence")
    print("  MAE°      = mean circular angular error between transform output and your angles")
    print("  f32pair   = atan2 of two consecutive float32 values (x/y wind-vector components)")
    print("  i16pair   = atan2 of two consecutive int16 values  (fixed-point sin/cos)")
    print("  rad2deg   = stored value is in radians; converted to degrees before comparison")
    print("  linreg    = linear fit: angle ≈ a×value + b")
    print()
    print("  HOW TO USE RESULTS")
    print("  1. Find the top-ranked CORE (*) candidate with the lowest MAE.")
    print("  2. Note its offset and transform.")
    print("  3. Add to telemetry.py:")
    print("       self.WIND_DIR_ADDR = self.base_addr + <offset>")
    print("  4. At runtime: read the value, apply the transform, result mod 360 = angle.")
    print()


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print()
    print("╔══════════════════════════════════════════════════════════════════════════╗")
    print("║  DSJ2 Wind Direction Analyzer  —  full 16 MB RAM exhaustive search      ║")
    print("║  No encoding assumed.  Finds wind address anywhere in DOS RAM.          ║")
    print("╚══════════════════════════════════════════════════════════════════════════╝")
    print()

    # ── 1. Find PID ────────────────────────────────────────────────────────────
    print(f"  Searching for '{PROCESS_NAME}' process...", end=' ', flush=True)
    pid = find_pid(PROCESS_NAME)
    if pid is None:
        print("NOT FOUND.\n  Start DOSBox with DSJ2 loaded, then re-run.")
        sys.exit(1)
    print(f"PID = {pid}")

    # ── 2. Verify / discover BASE_ADDR ────────────────────────────────────────
    print(f"  Verifying BASE_ADDR = 0x{BASE_ADDR:016x}...", end=' ', flush=True)
    ws = verify_base(pid, BASE_ADDR)
    if ws:
        base = BASE_ADDR
        print(f"OK  (wind string: \"{ws}\")")
    else:
        print("failed.")
        print("  Scanning /proc/maps for correct base address...")
        base, ws = find_base_auto(pid)
        if base is None:
            print("  [error] Cannot locate DOS RAM base.")
            print("  Make sure DSJ2 is loaded and at the pre-jump screen.")
            sys.exit(1)
        print(f"  Discovered BASE = 0x{base:016x}  (wind string: \"{ws}\")")

    print(f"  Snapshot: 0x{base:016x} → 0x{base + RAM_SIZE:016x}  "
          f"({RAM_SIZE // 1024 // 1024} MB)")

    # ── 3. Load existing snapshots or collect fresh ────────────────────────────
    baseline  = None
    snapshots = None
    npz_path  = "(not saved)"

    save_files = find_save_files()
    if save_files:
        newest = save_files[-1]
        print()
        ans = input(
            f"  Found: {os.path.basename(newest)}\n"
            f"  Load it (skip recollection)? [Y/n]: "
        ).strip().lower()
        if ans in ('', 'y', 'yes'):
            print(f"  Loading {newest}...", end=' ', flush=True)
            _, _, baseline, snapshots = load_snapshots(newest)
            npz_path = newest
            angles_loaded = [a for a, _ in snapshots]
            print(f"OK  —  {len(snapshots)} snapshots, angles: {angles_loaded}")

    if snapshots is None:
        baseline, snapshots = collect_snapshots(pid, base)
        print("  Saving raw snapshots...", end=' ', flush=True)
        npz_path = save_snapshots(pid, base, baseline, snapshots)
        print(f"saved → {npz_path}")

    angles = [a for a, _ in snapshots]
    print(f"\n  Angles for this session: {angles}")

    # ── 4. Diff → candidate sets ───────────────────────────────────────────────
    print()
    print("─" * 72)
    print("  Building diff candidate sets (full 16 MB)...")
    t0 = time.time()
    union_offsets, core_offsets, per_snap_sets = build_candidate_sets(baseline, snapshots)
    dt_diff = time.time() - t0
    print(f"  CORE  (all {len(snapshots)} snaps) : {len(core_offsets):5d} offsets")
    print(f"  UNION (≥ 1 snap)  : {len(union_offsets):5d} offsets")
    print(f"  Diff completed in {dt_diff:.2f}s")

    if not union_offsets:
        print("\n  [error] No differences found — did you re-enter the SAME hill?")
        sys.exit(1)

    # ── 5. Analysis passes ─────────────────────────────────────────────────────
    print()
    print("─" * 72)
    print("  Running analysis passes...")
    print("─" * 72)
    t1 = time.time()

    cands_a = analyze_pass_a(snapshots, union_offsets, angles)
    cands_b = analyze_pass_b(snapshots, union_offsets, angles)
    cands_c = analyze_pass_c(snapshots, union_offsets, angles)
    cands_d = analyze_pass_d(snapshots, union_offsets, angles)

    dt_analysis = time.time() - t1
    all_mae = cands_a + cands_c + cands_d

    print()
    print(f"  All passes done in {dt_analysis:.1f}s")
    print(f"    A (transforms)  : {len(cands_a):4d} hits")
    print(f"    B (Spearman)    : {len(cands_b):4d} hits")
    print(f"    C (regression)  : {len(cands_c):4d} hits")
    print(f"    D (pair atan2)  : {len(cands_d):4d} hits")

    # ── 6. Report ──────────────────────────────────────────────────────────────
    print_report(all_mae, cands_b, angles, union_offsets, core_offsets,
                 per_snap_sets, npz_path)


if __name__ == "__main__":
    main()
