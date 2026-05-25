"""
wind_dir_scanner.py
===================
Locates the wind direction value in DSJ2 DOSBox RAM.

Wind speed is already confirmed at 0x27363 (ASCII string).
Wind direction is an absolute compass arrow (0-360°), random each round,
independent of hill.  Because it changes every time you re-enter a hill,
a same-hill re-entry gives a perfect controlled diff: geometry is frozen,
only wind data changes.

MODES
-----
Default (no args):
  Full snapshot diff — re-enter the same hill up to 3 times and compare
  RAM snapshots.  Best for initial discovery.

--monitor:
  Live mode — reads the confirmed candidate addresses every 0.5 s and
  prints their values.  Use this to map stored values to displayed degrees
  by navigating hills with known directions (ideally 0°, 90°, 180°, 270°).

--hexdump:
  Live hex dump of the 128 bytes around the wind string (±64 bytes of
  0x27363).  Changed bytes are highlighted in [brackets].  A side panel
  shows every 2-byte and 4-byte aligned value in the window that falls in
  the range 0–360 — these are direction candidates.  Load different hills
  and watch which bytes change alongside the direction arrow.

Once the address and encoding are confirmed, add to telemetry.py:
  self.WIND_DIR_ADDR = self.base_addr + <offset>
"""

import struct
import sys
import time
import os

# --- SESSION CONFIG ---
PID  = 11670
BASE = 0x7025ccbff010
# ----------------------

WIND_STRING_OFF = 0x27363          # known: wind speed ASCII string
MEM_PATH        = f"/proc/{PID}/mem"
RAM_SIZE        = 16 * 1024 * 1024

# Candidate direction addresses surfaced by the 3-snapshot intersection.
# Format: (offset_from_BASE, label, notes)
CANDIDATES = [
    (0x26ee4, "cand-A", "i16=21/18/11 for 270°/165°/0° — closest to wind string"),
    (0x28fac, "cand-B", "byte[2] changes alongside cand-C"),
    (0x28fb0, "cand-C", "i16=94/79/81 for 270°/165°/0°"),
    (0x295f0, "cand-D", "i16=332/401/277 — possibly speed-related"),
    (0x295f4, "cand-E", "adjacent to cand-D"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_ram():
    try:
        with open(MEM_PATH, "rb") as f:
            f.seek(BASE)
            return f.read(RAM_SIZE)
    except Exception as e:
        print(f"  [RAM read error] {e}")
        return None


def read_wind_string():
    try:
        with open(MEM_PATH, "rb") as f:
            f.seek(BASE + WIND_STRING_OFF)
            raw = f.read(16).split(b"\x00")[0]
        return raw.decode("ascii", errors="ignore").strip()
    except Exception:
        return "?"


def diff_rams(a, b):
    """Return list of (offset, old_bytes, new_bytes) for every changed 4-byte chunk."""
    changed = []
    for i in range(0, RAM_SIZE - 3, 4):
        oa, ob = a[i:i+4], b[i:i+4]
        if oa != ob:
            changed.append((i, oa, ob))
    return changed


def compact(b4):
    """One-line summary: hex + float32 (if meaningful) + first int16."""
    h = b4.hex()
    try:
        f = struct.unpack("<f", b4)[0]
        f_str = f"f32={f:8.3f}" if -9999 < f < 9999 else "f32=---     "
    except Exception:
        f_str = "f32=---     "
    i16 = struct.unpack("<h", b4[:2])[0]
    return f"{h}  {f_str}  i16={i16:6d}"


# ---------------------------------------------------------------------------
# Live monitor
# ---------------------------------------------------------------------------

def read_candidates():
    """Read wind string + all candidate addresses in one pass."""
    try:
        with open(MEM_PATH, "rb") as f:
            # Wind string
            f.seek(BASE + WIND_STRING_OFF)
            raw = f.read(16).split(b"\x00")[0]
            wind_str = raw.decode("ascii", errors="ignore").strip()

            # Candidates
            values = []
            for off, label, _ in CANDIDATES:
                f.seek(BASE + off)
                b4 = f.read(4)
                i8  = struct.unpack("b",  b4[0:1])[0]
                u8  = struct.unpack("B",  b4[0:1])[0]
                i16 = struct.unpack("<h", b4[0:2])[0]
                u16 = struct.unpack("<H", b4[0:2])[0]
                i32 = struct.unpack("<i", b4)[0]
                try:
                    f32 = struct.unpack("<f", b4)[0]
                    f32_s = f"{f32:10.4f}" if -9999 < f32 < 9999 else "       ---"
                except Exception:
                    f32_s = "       ---"
                values.append((label, b4.hex(), i8, u8, i16, u16, i32, f32_s))
        return wind_str, values
    except Exception as e:
        return f"[read error: {e}]", []


def monitor():
    """
    Continuously display candidate address values.
    Navigate hills with different wind directions and watch which candidate
    tracks the compass arrow shown in the game.
    """
    print("=" * 72)
    print("  DSJ2 Wind Direction Monitor  —  live candidate readout")
    print("=" * 72)
    print()
    print("Navigate to different hills and note which value tracks the direction")
    print("arrow.  Target 0°, 90°, 180°, 270° for a clean arithmetic pattern.")
    print("Ctrl+C to stop.")
    print()

    prev_values = {}

    try:
        while True:
            wind_str, values = read_candidates()

            os.system("clear")
            print(f"  Wind string: \"{wind_str}\"")
            print()
            print(f"  {'Label':<8}  {'hex':<10}  {'i8':>5}  {'u8':>5}  "
                  f"{'i16':>7}  {'u16':>6}  {'i32':>10}  {'f32':>12}  note")
            print("  " + "-" * 80)

            for label, hexv, i8, u8, i16, u16, i32, f32_s in values:
                changed = hexv != prev_values.get(label, hexv)
                marker = "  <<< CHANGED" if changed else ""
                # Find the note for this label
                note = next((n for o, l, n in CANDIDATES if l == label), "")
                short_note = note[:30]
                print(f"  {label:<8}  {hexv:<10}  {i8:>5}  {u8:>5}  "
                      f"{i16:>7}  {u16:>6}  {i32:>10}  {f32_s}{marker}")
                if changed:
                    print(f"           └─ {short_note}")
                prev_values[label] = hexv

            print()
            print("  Mapping so far (fill in manually as you navigate hills):")
            print("  | Displayed deg | cand-A (i16) | cand-B hex | cand-C (i16) |")
            print("  |     0°        |              |            |              |")
            print("  |    90°        |              |            |              |")
            print("  |   180°        |              |            |              |")
            print("  |   270°        |              |            |              |")
            print()
            print("  [refreshing every 0.5 s — Ctrl+C to quit]")

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 66)
    print("  DSJ2 Wind Direction Scanner  —  same-hill re-entry diff")
    print("=" * 66)
    print()
    print("Wind is random each round.  Re-entering the same hill freezes")
    print("the geometry so only wind-related memory changes in the diff.")
    print()

    known = {"wind string (speed)": WIND_STRING_OFF}
    snapshots = []   # list of (ram, wind_string, speed_str, dir_degrees)

    for attempt in range(1, 4):          # up to 3 snapshots
        label = {1: "FIRST", 2: "SECOND", 3: "THIRD"}[attempt]

        if attempt == 1:
            input(f"Go to the pre-jump screen of any hill, press Enter: ")
        else:
            input(f"Back out to menu, re-enter the SAME hill (new wind), press Enter: ")

        wstr = read_wind_string()
        print(f"  Wind string: \"{wstr}\"")

        spd_raw = input(f"  Wind SPEED shown on screen (e.g. 3.0): ").strip()
        dir_raw = input(f"  Wind DIRECTION shown on screen in degrees (e.g. 340): ").strip()

        print(f"  Reading {label} snapshot ({RAM_SIZE // 1024 // 1024} MB)...")
        ram = read_ram()
        if ram is None:
            print("  Failed — check PID/BASE and run with sudo.")
            return
        print(f"  {label} snapshot: OK")
        print()

        try:
            dir_deg = float(dir_raw)
        except ValueError:
            dir_deg = None

        snapshots.append((ram, wstr, spd_raw, dir_deg))

        # After the second snapshot, start showing diffs
        if len(snapshots) >= 2:
            prev_ram, prev_wstr, prev_spd, prev_dir = snapshots[-2]
            curr_ram, curr_wstr, curr_spd, curr_dir = snapshots[-1]

            diffs = diff_rams(prev_ram, curr_ram)

            print(f"  --- Diff: snapshot {attempt-1} → snapshot {attempt} ---")
            print(f"  Wind: \"{prev_wstr}\" ({prev_spd} m/s, {prev_dir}°)")
            print(f"    →   \"{curr_wstr}\" ({curr_spd} m/s, {curr_dir}°)")
            print(f"  {len(diffs)} 4-byte address(es) changed")
            print()

            # Validate: wind string region must be in diff
            speed_in_diff = any(abs(d[0] - WIND_STRING_OFF) < 16 for d in diffs)
            if speed_in_diff:
                print("  [OK] Wind string in diff.")
            else:
                print("  [!] Wind string NOT in diff — did you re-enter the same hill?")
            print()

            # Single compact table — all changed addresses
            hdr = f"  {'offset':>10}  {'abs addr':>16}  {'old':>30}  {'new':>30}"
            print(hdr)
            print("  " + "-" * (len(hdr) - 2))
            for off, old_b, new_b in sorted(diffs):
                tag = "  ← SPEED" if abs(off - WIND_STRING_OFF) < 16 else ""
                print(f"  0x{off:08x}  0x{BASE+off:014x}  "
                      f"{compact(old_b):>30}  {compact(new_b):>30}{tag}")
            print()

        # After third snapshot, do a 3-way intersection
        if len(snapshots) == 3:
            d01 = {d[0] for d in diff_rams(snapshots[0][0], snapshots[1][0])}
            d12 = {d[0] for d in diff_rams(snapshots[1][0], snapshots[2][0])}
            d02 = {d[0] for d in diff_rams(snapshots[0][0], snapshots[2][0])}
            stable = d01 & d12 & d02

            print(f"  === 3-snapshot intersection: {len(stable)} address(es) changed "
                  f"across ALL three re-entries ===")
            print()
            if stable:
                print(f"  {'RAM offset':>12}  {'Abs addr':>16}  "
                      f"{'Snap1':>20}  {'Snap2':>20}  {'Snap3':>20}")
                print(f"  {'-'*12}  {'-'*16}  {'-'*20}  {'-'*20}  {'-'*20}")
                for off in sorted(stable):
                    b1 = snapshots[0][0][off:off+4]
                    b2 = snapshots[1][0][off:off+4]
                    b3 = snapshots[2][0][off:off+4]
                    tag = "  ← KNOWN SPEED" if abs(off - WIND_STRING_OFF) < 16 else ""

                    def short(b):
                        try:
                            f = struct.unpack("<f", b)[0]
                            if -1e6 < f < 1e6:
                                return f"f32={f:.3f}"
                        except Exception:
                            pass
                        i16 = struct.unpack("<h", b[:2])[0]
                        return f"i16={i16} hex={b.hex()}"

                    print(f"  0x{off:>10x}  0x{BASE+off:>14x}  "
                          f"{short(b1):>20}  {short(b2):>20}  {short(b3):>20}{tag}")
            else:
                print("  No consistent changes found across all three.")
                print("  Try with more re-entries or check that you used the same hill.")
            print()

        if attempt < 3:
            again = input("  Do another re-entry to narrow down further? (y/n): ").strip().lower()
            if again != "y":
                break
        print()

    print("Done.")
    print()
    print("Once you identify the direction address, add it to telemetry.py as:")
    print("  self.WIND_DIR_ADDR = self.base_addr + <offset>")
    print("and read it as float32 or int16 depending on the encoding seen above.")


# ---------------------------------------------------------------------------
# Hex dump mode
# ---------------------------------------------------------------------------

# How many bytes before/after the wind string to include in the dump.
DUMP_BEFORE = 96
DUMP_AFTER  = 64
DUMP_SIZE   = DUMP_BEFORE + DUMP_AFTER   # 160 bytes total

# Known trig-float offset: 5 bytes before the wind string.
# Observed values: 0.8898 (dir~0°) and 0.9965 (dir~180°) — likely sin/cos.
TRIG_FLOAT_OFF = WIND_STRING_OFF - 5   # = 0x2735e


def _read_dump_window():
    start = BASE + WIND_STRING_OFF - DUMP_BEFORE
    try:
        with open(MEM_PATH, "rb") as f:
            f.seek(start)
            data = f.read(DUMP_SIZE)
            # Also read wind string for display
            f.seek(BASE + WIND_STRING_OFF)
            raw = f.read(16).split(b"\x00")[0]
            wind_str = raw.decode("ascii", errors="ignore").strip()
        return data, wind_str
    except Exception as e:
        return None, f"[error: {e}]"


def _candidates_in_window(data):
    """
    Scan the dump window for candidate direction values.

    Two categories:
      - Integer candidates: uint16/int16/uint32 in (0, 360] — direct degree encoding.
      - Trig candidates:    float32 in [-1.0, 1.0] — pre-computed sin/cos encoding.
        (Also still reports float32 in [1.0, 360.0] for raw-degree float encoding.)

    Returns list of (window_offset, size_bytes, value, encoding_label).
    """
    hits = []
    for i in range(0, DUMP_SIZE - 1, 2):
        u16 = struct.unpack_from("<H", data, i)[0]
        if 0 < u16 <= 360:
            hits.append((i, 2, u16, f"uint16={u16}"))
        i16 = struct.unpack_from("<h", data, i)[0]
        if i16 != u16 and 0 < i16 <= 360:
            hits.append((i, 2, i16, f"int16={i16}"))

    for i in range(0, DUMP_SIZE - 3, 4):
        try:
            f32 = struct.unpack_from("<f", data, i)[0]
            if 1.0 <= f32 <= 360.0:
                hits.append((i, 4, f32, f"f32={f32:.2f}"))
            elif -1.0 <= f32 <= 1.0 and not (-1e-6 < f32 < 1e-6):
                # Non-zero trig-range float: likely a pre-computed sin/cos value.
                hits.append((i, 4, f32, f"trig={f32:.6f}"))
        except Exception:
            pass
        u32 = struct.unpack_from("<I", data, i)[0]
        if 0 < u32 <= 360:
            hits.append((i, 4, u32, f"uint32={u32}"))

    return hits


def hexdump():
    """
    Live 128-byte hex dump centred on the wind string.
    Changed bytes are shown in [xx] brackets.
    A side panel lists every in-window value in the range 0–360.
    """
    print("=" * 72)
    print("  DSJ2 Wind Direction — live hex dump around wind string (0x27363)")
    print("=" * 72)
    print()
    print(f"  Dump window: BASE+0x{WIND_STRING_OFF - DUMP_BEFORE:05x} "
          f"→ BASE+0x{WIND_STRING_OFF + DUMP_AFTER - 1:05x}  ({DUMP_SIZE} bytes)")
    print(f"  Wind string:     BASE+0x{WIND_STRING_OFF:05x}  (marked with >>)")
    print(f"  Trig float (confirmed changing): BASE+0x{TRIG_FLOAT_OFF:05x}  (-5 from string)")
    print()
    print("  Two side panels:")
    print("    [1] Integer/degree values 1–360  — direct angle encoding candidates")
    print("    [2] Float32 values in [-1, 1]    — sin/cos pre-computed candidates")
    print("        Columns: raw float  |  acos(value)°  |  asin(value)°")
    print()
    print("  Collect calibration points: navigate to wind showing exactly")
    print("  0° / 90° / 180° / 270° and note the 'confirmed changing' float value.")
    print("  Ctrl+C to stop.")
    print()

    prev_data = None

    try:
        while True:
            data, wind_str = _read_dump_window()
            if data is None:
                time.sleep(2)
                continue

            os.system("clear")
            print(f"  Wind string: \"{wind_str}\"   "
                  f"(BASE+0x{WIND_STRING_OFF:05x})")
            print()

            # --- hex dump rows (16 bytes each) ---
            COLS = 16
            string_col = DUMP_BEFORE % COLS   # column of wind string start within its row

            for row_start in range(0, DUMP_SIZE, COLS):
                abs_off = WIND_STRING_OFF - DUMP_BEFORE + row_start
                row_bytes = data[row_start:row_start + COLS]
                prev_row  = prev_data[row_start:row_start + COLS] if prev_data else row_bytes

                # Build hex part with change markers
                hex_parts = []
                for col, (b, pb) in enumerate(zip(row_bytes, prev_row)):
                    global_off = row_start + col
                    # Mark the wind string start position
                    at_string = (global_off == DUMP_BEFORE)
                    changed   = (b != pb)
                    h = f"{b:02x}"
                    if at_string:
                        hex_parts.append(f">>{h}<<" if changed else f">>{h}  ")
                    elif changed:
                        hex_parts.append(f"[{h}]  ")
                    else:
                        hex_parts.append(f" {h}  ")

                hex_str = "".join(hex_parts)
                print(f"  +{abs_off:04x}  {hex_str}")

            print()

            # --- side panel: values 0–360 in window ---
            candidates = _candidates_in_window(data)
            prev_candidates = _candidates_in_window(prev_data) if prev_data else candidates
            prev_map = {(c[0], c[2].__class__): c[2] for c in prev_candidates}

            # --- Integer / degree candidates ---
            int_cands = [(w, s, v, l) for w, s, v, l in candidates
                         if not l.startswith("trig=")]
            trig_cands = [(w, s, v, l) for w, s, v, l in candidates
                          if l.startswith("trig=")]

            if int_cands:
                print(f"  Integer/degree values (1–360) found in window:")
                print(f"  {'win-off':>8}  {'abs addr':>16}  {'value':>22}  note")
                print(f"  {'-'*8}  {'-'*16}  {'-'*22}  {'-'*16}")
                for woff, sz, val, label in sorted(int_cands):
                    abs_addr = BASE + WIND_STRING_OFF - DUMP_BEFORE + woff
                    rel = woff - DUMP_BEFORE
                    key = (woff, val.__class__)
                    changed = prev_map.get(key) != val
                    marker = "  <<< CHANGED" if changed else ""
                    print(f"  {woff:>8d}  0x{abs_addr:>14x}  {label:>22}  "
                          f"({rel:+d} from string){marker}")
            else:
                print("  No integer/degree values (1–360) in window.")

            print()

            # --- Trig-range floats (sin/cos candidates) ---
            # Always show the confirmed changing float at TRIG_FLOAT_OFF first,
            # then any other trig-range floats found in the window.
            print(f"  Trig-range floats [-1, 1] (sin/cos candidates):")
            print(f"  {'win-off':>8}  {'abs addr':>16}  {'float32':>12}  "
                  f"  {'acos°':>7}  {'asin°':>7}  note")
            print(f"  {'-'*8}  {'-'*16}  {'-'*12}  {'-'*7}  {'-'*7}  {'-'*18}")

            import math

            def trig_row(woff, abs_addr, f32, note, prev_f32=None):
                try:
                    ac = f"{math.degrees(math.acos(max(-1.0, min(1.0, f32)))):.1f}"
                except Exception:
                    ac = "  ---"
                try:
                    as_ = f"{math.degrees(math.asin(max(-1.0, min(1.0, f32)))):.1f}"
                except Exception:
                    as_ = "  ---"
                rel = woff - DUMP_BEFORE
                changed = (prev_f32 is not None and abs(prev_f32 - f32) > 1e-7)
                marker = "  <<< CHANGED" if changed else ""
                print(f"  {woff:>8d}  0x{abs_addr:>14x}  {f32:>12.6f}  "
                      f"  {ac:>7}  {as_:>7}  ({rel:+d} from string) {note}{marker}")

            # Always show confirmed float at TRIG_FLOAT_OFF
            trig_woff = TRIG_FLOAT_OFF - (WIND_STRING_OFF - DUMP_BEFORE)  # window offset
            if 0 <= trig_woff <= DUMP_SIZE - 4:
                f32_now = struct.unpack_from("<f", data, trig_woff)[0]
                f32_prev = struct.unpack_from("<f", prev_data, trig_woff)[0] \
                           if prev_data else f32_now
                trig_row(trig_woff,
                         BASE + TRIG_FLOAT_OFF,
                         f32_now,
                         "** confirmed changing **",
                         f32_prev)

            # Any other trig-range floats in the window (excluding the one above)
            for woff, sz, val, label in sorted(trig_cands):
                if woff == trig_woff:
                    continue   # already printed above
                abs_addr = BASE + WIND_STRING_OFF - DUMP_BEFORE + woff
                key = (woff, float)
                prev_val = prev_map.get(key)
                trig_row(woff, abs_addr, val, "", prev_val)

            print()

            # --- cand-C reference (outside window, read separately) ---
            try:
                with open(MEM_PATH, "rb") as mf:
                    mf.seek(BASE + 0x28fb0)
                    cc_bytes = mf.read(4)
                cc_i16 = struct.unpack("<h", cc_bytes[:2])[0]
                cc_f32 = struct.unpack("<f", cc_bytes)[0]
                wind_spd_now = wind_str.split(".")[0] + "." + wind_str[wind_str.index(".")+1] \
                               if "." in wind_str else "?"
                print(f"  cand-C  (0x28fb0, outside window):  "
                      f"i16={cc_i16:6d}  f32={cc_f32:.4f}   "
                      f"[confirmed: speed×trig component]")
            except Exception:
                pass

            print()
            print("  Calibration guide: navigate to hills showing exactly 0° / 90° / 180° / 270°")
            print("  and record the 'confirmed changing' float value for each.")
            print()
            print("  [refreshing every 2 s — Ctrl+C to quit]")

            prev_data = data
            time.sleep(2)

    except KeyboardInterrupt:
        print("\nHex dump stopped.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--monitor" in sys.argv:
        monitor()
    elif "--hexdump" in sys.argv:
        hexdump()
    else:
        main()
