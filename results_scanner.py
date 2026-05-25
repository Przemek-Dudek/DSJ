"""
results_scanner.py
==================
Locates the RAM addresses of the official jump distance and judge scores
in DSJ2 running under DOSBox.

Results appear during the landing animation while physics are still active.
Strategy:
  1. Auto-detects takeoff and captures a mid-flight BASELINE snapshot
     (before the game has written any result values).
  2. Auto-detects landing, then waits ~1.5 s for the results to populate.
  3. Captures a POST-LANDING snapshot.
  4. Diffs the two snapshots to isolate addresses written at landing time.
  5. Asks for the values you see on screen and scans for them in multiple
     encodings (float32, float64, int16/int32 scaled).
  6. Cross-references scan hits against the diff to surface the most likely
     candidate addresses.

Run on 2-3 jumps. Addresses that appear consistently are confirmed.
Once confirmed, add them to telemetry.py.
"""

import struct
import time

# --- SESSION CONFIG ---
PID  = 11670
BASE = 0x7025ccbff010
# ----------------------

PLAYER_STRUCT = BASE + 0x29bd0
MEM_PATH      = f"/proc/{PID}/mem"
RAM_SIZE      = 16 * 1024 * 1024

# Takeoff detection (mirrors telemetry.py)
TAKEOFF_STREAK   = 2
TAKEOFF_Y_THRESH = 0.05
TAKEOFF_SPD_MIN  = 10.0

# Landing detection
LAND_GRACE = 20
LAND_DELTA = 2.0

# After landing, wait this many frames before snapshotting
# (gives the game time to write result values)
POST_LAND_WAIT_FRAMES = 30   # ~1.5 s at 20 Hz

# Mid-flight frame at which we take the baseline snapshot
BASELINE_FRAME = 10


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _read_block():
    try:
        with open(MEM_PATH, "rb") as f:
            f.seek(PLAYER_STRUCT)
            return f.read(128)
    except Exception:
        return None


def _read_ram():
    try:
        with open(MEM_PATH, "rb") as f:
            f.seek(BASE)
            return f.read(RAM_SIZE)
    except Exception as e:
        print(f"  [RAM read error] {e}")
        return None


def _phys(block):
    if block is None or len(block) < 128:
        return None
    return {
        "speed": struct.unpack("<f", block[44:48])[0],
        "y_vel": struct.unpack("<f", block[40:44])[0],
        "y_pos": struct.unpack("<f", block[84:88])[0],
    }


def _active(p):
    return p is not None and 0.001 < p["speed"] < 200.0


# ---------------------------------------------------------------------------
# Scanner core
# ---------------------------------------------------------------------------

def _find_all(ram, needle):
    """Return every offset where needle appears in ram."""
    offsets, start = [], 0
    while True:
        idx = ram.find(needle, start)
        if idx == -1:
            break
        offsets.append(idx)
        start = idx + 1
    return offsets


def _scan_value(ram, val):
    """
    Scan ram for val in all relevant encodings.
    Returns list of (ram_offset, encoding_label).
    """
    hits = []

    # float32
    for off in _find_all(ram, struct.pack("<f", val)):
        hits.append((off, "float32"))

    # float64
    for off in _find_all(ram, struct.pack("<d", val)):
        hits.append((off, "float64"))

    # int16 × 2  (judge scores: 16.5 -> 33)
    iv = round(val * 2)
    if -32768 <= iv <= 32767:
        for off in _find_all(ram, struct.pack("<h", iv)):
            hits.append((off, f"int16×2 ({iv})"))

    # int16 × 10  (54.0 -> 540, or 16.5 -> 165)
    iv = round(val * 10)
    if -32768 <= iv <= 32767:
        for off in _find_all(ram, struct.pack("<h", iv)):
            hits.append((off, f"int16×10 ({iv})"))

    # int32 × 10
    iv = round(val * 10)
    for off in _find_all(ram, struct.pack("<i", iv)):
        hits.append((off, f"int32×10 ({iv})"))

    # int32 × 100
    iv = round(val * 100)
    for off in _find_all(ram, struct.pack("<i", iv)):
        hits.append((off, f"int32×100 ({iv})"))

    return hits


def _diff(ram_a, ram_b):
    """Return set of 4-byte-aligned offsets where the two snapshots differ."""
    changed = set()
    for i in range(0, RAM_SIZE - 3, 4):
        if ram_a[i:i+4] != ram_b[i:i+4]:
            changed.add(i)
    return changed


def _in_diff(offset, diff_set):
    """True if any of the 4 bytes at offset are covered by the diff set."""
    return any(offset - k in diff_set for k in range(4))


def _print_hits(hits, diff_set, label):
    if not hits:
        print(f"  No hits for {label}.")
        return

    in_diff  = [(o, e) for o, e in hits if _in_diff(o, diff_set)]
    not_diff = [(o, e) for o, e in hits if not _in_diff(o, diff_set)]

    print(f"  {label}: {len(hits)} total hits, "
          f"{len(in_diff)} also changed at landing  ← focus here")

    rows = in_diff if in_diff else not_diff[:8]
    note = "" if in_diff else "  (no diff hits — showing first hits)"
    if note:
        print(note)

    print(f"    {'RAM offset':>12}  {'Abs addr':>16}  {'Encoding':<20}")
    print(f"    {'-'*12}  {'-'*16}  {'-'*20}")
    for o, enc in sorted(rows)[:20]:
        marker = "  *** CHANGED AT LANDING" if _in_diff(o, diff_set) else ""
        print(f"    {hex(o):>12}  {hex(BASE + o):>16}  {enc:<20}{marker}")
    print()


def _find_clusters(hits_per_val, vals, window=64):
    """
    Find groups where ALL judge values appear as float32 within `window` bytes
    of each other.  Returns list of {val: offset} dicts.
    """
    if not vals:
        return []

    primary = vals[0]
    anchors = [o for o, enc in hits_per_val.get(primary, []) if enc == "float32"]

    clusters = []
    for anchor in anchors:
        group = {primary: anchor}
        for v in vals[1:]:
            nearby = [o for o, enc in hits_per_val.get(v, [])
                      if enc == "float32" and abs(o - anchor) <= window]
            if nearby:
                group[v] = min(nearby, key=lambda o: abs(o - anchor))
        if len(group) == len(vals):
            clusters.append(group)
    return clusters


# ---------------------------------------------------------------------------
# Main session loop
# ---------------------------------------------------------------------------

def run():
    print("=" * 60)
    print("  DSJ2 Results Scanner")
    print("  Finds: jump distance + judge score RAM addresses")
    print("=" * 60)
    print()

    run_num = 0

    while True:
        run_num += 1
        print(f"--- Run {run_num} ---  Waiting for jump to start...")

        # Wait for physics to go active
        while True:
            p = _phys(_read_block())
            if _active(p):
                break
            time.sleep(0.1)

        in_flight     = False
        flight_frames = 0
        rising_streak = 0
        prev_y_pos    = 0.0
        prev_y_vel    = 0.0
        baseline_ram  = None
        landed        = False

        while True:
            block = _read_block()
            p = _phys(block)

            if not _active(p):
                print("  Physics went inactive unexpectedly — aborting run.")
                break

            if not in_flight:
                dy = p["y_pos"] - prev_y_pos
                if dy > TAKEOFF_Y_THRESH and p["speed"] > TAKEOFF_SPD_MIN:
                    rising_streak += 1
                else:
                    rising_streak = 0

                if rising_streak >= TAKEOFF_STREAK:
                    in_flight     = True
                    flight_frames = 0
                    rising_streak = 0
                    print("  Takeoff detected.")

            else:
                flight_frames += 1

                # Baseline: mid-flight, before any result is written
                if flight_frames == BASELINE_FRAME:
                    print(f"  Capturing mid-flight baseline (frame {BASELINE_FRAME})...")
                    baseline_ram = _read_ram()
                    print(f"  Baseline: {'OK' if baseline_ram else 'FAILED'}")

                # Landing detection
                y_delta = p["y_vel"] - prev_y_vel
                if flight_frames > LAND_GRACE and abs(y_delta) > LAND_DELTA:
                    print(f"  Landing detected (frame {flight_frames}).")
                    landed = True
                    break

            prev_y_pos = p["y_pos"]
            prev_y_vel = p["y_vel"]
            time.sleep(0.05)

        if not landed or baseline_ram is None:
            print("  Incomplete run — trying again.\n")
            time.sleep(0.5)
            continue

        # Wait for results to populate
        print(f"  Waiting {POST_LAND_WAIT_FRAMES} frames (~{POST_LAND_WAIT_FRAMES*0.05:.1f}s) "
              f"for results to appear...")
        for _ in range(POST_LAND_WAIT_FRAMES):
            time.sleep(0.05)

        print("  Capturing post-landing snapshot...")
        results_ram = _read_ram()
        if not results_ram:
            print("  Snapshot failed — trying again.\n")
            continue

        # Diff
        diff_set = _diff(baseline_ram, results_ram)
        print(f"  Diff: {len(diff_set)} 4-byte addresses changed between "
              f"mid-flight and post-landing.")
        print()

        # ---- User input ----
        dist_str   = input("  Distance shown on screen (e.g.  54.0 ): ").strip()
        judges_str = input("  Judge scores, space-separated (e.g.  16.5 17.0 16.5 ): ").strip()
        print()

        try:
            dist_val = float(dist_str)
        except ValueError:
            print("  Invalid distance — skipping run.\n")
            continue

        judge_vals = []
        for s in judges_str.split():
            try:
                judge_vals.append(float(s))
            except ValueError:
                pass

        # ---- Distance scan ----
        print("  --- DISTANCE ---")
        dist_hits = _scan_value(results_ram, dist_val)
        _print_hits(dist_hits, diff_set, f"{dist_val} m")

        # ---- Judge score scans ----
        print("  --- JUDGE SCORES ---")
        hits_per_val = {}
        for jv in judge_vals:
            h = _scan_value(results_ram, jv)
            hits_per_val[jv] = h
            _print_hits(h, diff_set, f"score {jv}")

        # ---- Cluster detection ----
        clusters = _find_clusters(hits_per_val, judge_vals)
        if clusters:
            print(f"  --- JUDGE ARRAY CANDIDATES (float32 clusters) ---")
            for i, c in enumerate(clusters[:6]):
                print(f"  Cluster {i+1}:")
                for v, off in sorted(c.items(), key=lambda x: x[1]):
                    print(f"    score={v:5.1f}  offset={hex(off):>12}  "
                          f"abs={hex(BASE + off):>16}"
                          f"{'  *** IN DIFF' if _in_diff(off, diff_set) else ''}")
        else:
            print("  No float32 judge clusters found — inspect int16/int32 hits above.")

        print()
        if input("  Another jump? (y/n): ").strip().lower() != "y":
            break

    print("\nDone. Note stable addresses above and integrate into telemetry.py.")


if __name__ == "__main__":
    run()
