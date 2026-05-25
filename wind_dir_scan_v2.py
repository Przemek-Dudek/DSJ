"""
wind_dir_scan_v2.py
===================
User-guided wind direction finder for DSJ2 DOSBox RAM.

APPROACH
--------
Each time you enter the same hill the geometry is frozen but wind is re-randomised.
This script takes a BASELINE snapshot (first hill entry), then records 7 more
snapshots each paired with your visual reading of the wind direction arrow.

Scale: 0–360°, where 0° = East / +X axis, 90° = North / +Y axis, etc.

Output is wind_dir_data.txt — open it and look for addresses whose values
correlate with your angle inputs.  The SUMMARY TABLE at the bottom lists only
addresses that changed in every single data point (strongest candidates).

HOW TO USE
----------
1. Start DSJ2 in DOSBox.  Update PID and BASE below (check with:
       pgrep dosbox
       cat /proc/<PID>/maps | grep -i "rw-p" | head -5
2. Run:  sudo python wind_dir_scan_v2.py
3. Follow prompts:
       - BASELINE: stand at top of hill, note the wind direction, press ENTER
       - DATA POINTS 1–7: exit to menu, re-enter SAME hill, note new direction,
         press ENTER, then type the angle you saw
4. Open wind_dir_data.txt for analysis.

SCAN REGION
-----------
BASE + 0x25000  to  BASE + 0x2d000  (32 KB)
Covers all known wind-related candidates:
  0x26ee4  cand-A   (i16 changes with wind)
  0x2735e  trig     (confirmed changing float, likely sin/cos of direction)
  0x27363  SPEED    (confirmed: wind speed ASCII string)
  0x28fac  cand-B
  0x28fb0  cand-C   (confirmed: speed x trig component)
  0x295f0  cand-D
  0x295f4  cand-E
"""

import struct
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Session config — update these every time DOSBox is restarted
# ---------------------------------------------------------------------------
PID  = 11670
BASE = 0x7025ccbff010
# ---------------------------------------------------------------------------

SCAN_OFF   = 0x25000
SCAN_SIZE  = 0x8000          # 32 KB — covers all known wind candidates

MEM_PATH        = f"/proc/{PID}/mem"
WIND_STRING_OFF = 0x27363    # confirmed: wind speed ASCII string

NUM_DATAPOINTS  = 7
OUTPUT_FILE     = "wind_dir_data.txt"

# Known offsets — annotated in output when a changed address matches one
KNOWN = {
    0x26ee4: "cand-A",
    0x2735e: "trig-float (near wind string)",
    0x27363: "WIND SPEED STRING",
    0x28fac: "cand-B",
    0x28fb0: "cand-C (speed x trig)",
    0x295f0: "cand-D",
    0x295f4: "cand-E",
}


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def read_region():
    """Read SCAN_SIZE bytes starting at BASE+SCAN_OFF.  Exits on failure."""
    addr = BASE + SCAN_OFF
    try:
        with open(MEM_PATH, "rb") as f:
            f.seek(addr)
            data = f.read(SCAN_SIZE)
        if len(data) != SCAN_SIZE:
            print(f"  [error] short read: got {len(data)} of {SCAN_SIZE} bytes")
            sys.exit(1)
        return data
    except PermissionError:
        print("  [error] Permission denied — run with sudo.")
        sys.exit(1)
    except FileNotFoundError:
        print(f"  [error] /proc/{PID}/mem not found — is DOSBox running?  Check PID.")
        sys.exit(1)
    except Exception as e:
        print(f"  [error] {e}")
        sys.exit(1)


def read_wind_string():
    """Read wind speed ASCII string from confirmed address."""
    try:
        with open(MEM_PATH, "rb") as f:
            f.seek(BASE + WIND_STRING_OFF)
            raw = f.read(16).split(b"\x00")[0]
        return raw.decode("ascii", errors="ignore").strip()
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Diff and interpretation
# ---------------------------------------------------------------------------

def diff_snaps(baseline, current):
    """Return sorted list of scan-relative offsets (4-byte aligned) that differ."""
    changed = []
    for i in range(0, SCAN_SIZE - 3, 4):
        if baseline[i:i + 4] != current[i:i + 4]:
            changed.append(i)
    return changed


def interpret(b4):
    """Decode 4 bytes into every useful integer/float interpretation."""
    return {
        "hex":    b4.hex(),
        "i8":     struct.unpack("b",  b4[0:1])[0],
        "u8":     struct.unpack("B",  b4[0:1])[0],
        "i16_lo": struct.unpack("<h", b4[0:2])[0],
        "i16_hi": struct.unpack("<h", b4[2:4])[0],
        "u16_lo": struct.unpack("<H", b4[0:2])[0],
        "u16_hi": struct.unpack("<H", b4[2:4])[0],
        "i32":    struct.unpack("<i", b4)[0],
        "u32":    struct.unpack("<I", b4)[0],
        "f32":    struct.unpack("<f", b4)[0],
    }


def fmt_f32(f):
    if f != f:
        return "           nan"
    if abs(f) > 1e15:
        return "          huge"
    return f"{f:14.6f}"


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def write_output(baseline_snap, datapoints):
    """Write the full analysis to OUTPUT_FILE."""

    lines = []
    SEP  = "=" * 100
    DASH = "-" * 100

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines += [
        SEP,
        "  WIND DIRECTION SCANNER v2  —  DSJ2 DOSBox RAM",
        f"  Run:         {now_str}",
        f"  PID:         {PID}",
        f"  BASE:        0x{BASE:016x}",
        f"  Scan region: BASE+0x{SCAN_OFF:05x}  to  BASE+0x{SCAN_OFF + SCAN_SIZE:05x}  "
        f"({SCAN_SIZE} bytes / {SCAN_SIZE // 1024} KB)",
        f"  Data points: {len(datapoints)}",
        SEP,
        "",
        "  Known offsets in scan region:",
    ]
    for off, label in sorted(KNOWN.items()):
        lines.append(f"    BASE+0x{off:05x}  {label}")
    lines += ["", ""]

    # -----------------------------------------------------------------------
    # Per data point sections
    # -----------------------------------------------------------------------
    COL_HDR = (
        f"  {'RAM offset':>12}  {'old bytes':>10}  {'new bytes':>10}  "
        f"{'i8':>4}  {'u8':>4}  "
        f"{'i16lo':>7}  {'i16hi':>7}  "
        f"{'u16lo':>6}  {'u16hi':>6}  "
        f"{'i32':>12}  {'u32':>12}  "
        f"{'f32':>14}  note"
    )

    for dp_idx, dp in enumerate(datapoints, 1):
        lines += [
            SEP,
            f"  DATA POINT {dp_idx} / {len(datapoints)}",
            f"  Wind direction (visual):       {dp['direction']:.1f} deg",
            f"  Wind string at snapshot time:  \"{dp['wind_string']}\"",
            f"  Changed 4-byte chunks vs baseline:  {len(dp['changed'])}",
            "",
        ]

        if not dp["changed"]:
            lines += [
                "  (no changes detected — snapshot matches baseline exactly)",
                "  Did you re-enter the SAME hill?",
                "",
            ]
            continue

        lines.append(COL_HDR)
        lines.append("  " + "-" * (len(COL_HDR) - 2))

        for scan_off in sorted(dp["changed"]):
            ram_off = SCAN_OFF + scan_off
            old_b   = baseline_snap[scan_off:scan_off + 4]
            new_b   = dp["snapshot"][scan_off:scan_off + 4]
            ni      = interpret(new_b)

            # Annotation: match against known offsets (within 4 bytes)
            note = ""
            for known_off, known_label in KNOWN.items():
                if abs(ram_off - known_off) < 4:
                    note = f"<-- {known_label}"
                    break

            lines.append(
                f"  0x{ram_off:08x}    {old_b.hex():>10}  {new_b.hex():>10}  "
                f"{ni['i8']:>4}  {ni['u8']:>4}  "
                f"{ni['i16_lo']:>7}  {ni['i16_hi']:>7}  "
                f"{ni['u16_lo']:>6}  {ni['u16_hi']:>6}  "
                f"{ni['i32']:>12}  {ni['u32']:>12}  "
                f"{fmt_f32(ni['f32']):>14}  {note}"
            )

        lines.append("")

    # -----------------------------------------------------------------------
    # Summary table
    # -----------------------------------------------------------------------
    lines += [
        SEP,
        "  SUMMARY TABLE",
        "  Only addresses that changed in EVERY one of the 7 data points.",
        "  These are the strongest wind-direction candidates.",
        "",
    ]

    all_sets  = [set(dp["changed"]) for dp in datapoints]
    in_all    = sorted(all_sets[0].intersection(*all_sets[1:]))

    if not in_all:
        lines += [
            "  No addresses changed in all data points.",
            "  Possible reasons:",
            "    - Noise: some changes are game-state transients, not wind.",
            "    - Check that you used the SAME hill each re-entry.",
            "    - Wind address might lie outside the scan region (0x25000-0x2d000).",
            "",
        ]
    else:
        # Direction header row
        dir_header  = f"  {'RAM offset':>12}  "
        dir_header += "  ".join(f"{dp['direction']:>8.1f}d" for dp in datapoints)
        lines += [dir_header, "  " + "-" * len(dir_header)]

        # f32 sub-table
        lines.append("  [f32]")
        for scan_off in in_all:
            ram_off  = SCAN_OFF + scan_off
            note     = KNOWN.get(ram_off, "")
            row      = f"  0x{ram_off:08x}  "
            for dp in datapoints:
                b4  = dp["snapshot"][scan_off:scan_off + 4]
                f32 = struct.unpack("<f", b4)[0]
                if abs(f32) > 1e9 or f32 != f32:
                    cell = "       ---"
                else:
                    cell = f"{f32:10.4f}"
                row += f"  {cell}"
            if note:
                row += f"   ({note})"
            lines.append(row)

        lines.append("")

        # i16 lo sub-table
        lines.append("  [i16 low word]")
        for scan_off in in_all:
            ram_off = SCAN_OFF + scan_off
            note    = KNOWN.get(ram_off, "")
            row     = f"  0x{ram_off:08x}  "
            for dp in datapoints:
                b4  = dp["snapshot"][scan_off:scan_off + 4]
                i16 = struct.unpack("<h", b4[:2])[0]
                row += f"  {i16:>10}"
            if note:
                row += f"   ({note})"
            lines.append(row)

        lines.append("")

        # i16 hi sub-table
        lines.append("  [i16 high word]")
        for scan_off in in_all:
            ram_off = SCAN_OFF + scan_off
            note    = KNOWN.get(ram_off, "")
            row     = f"  0x{ram_off:08x}  "
            for dp in datapoints:
                b4  = dp["snapshot"][scan_off:scan_off + 4]
                i16 = struct.unpack("<h", b4[2:4])[0]
                row += f"  {i16:>10}"
            if note:
                row += f"   ({note})"
            lines.append(row)

        lines.append("")

        # u16 lo sub-table
        lines.append("  [u16 low word]")
        for scan_off in in_all:
            ram_off = SCAN_OFF + scan_off
            note    = KNOWN.get(ram_off, "")
            row     = f"  0x{ram_off:08x}  "
            for dp in datapoints:
                b4  = dp["snapshot"][scan_off:scan_off + 4]
                u16 = struct.unpack("<H", b4[:2])[0]
                row += f"  {u16:>10}"
            if note:
                row += f"   ({note})"
            lines.append(row)

        lines.append("")

        # hex sub-table
        lines.append("  [raw hex]")
        for scan_off in in_all:
            ram_off = SCAN_OFF + scan_off
            note    = KNOWN.get(ram_off, "")
            row     = f"  0x{ram_off:08x}  "
            for dp in datapoints:
                b4 = dp["snapshot"][scan_off:scan_off + 4]
                row += f"  {b4.hex():>10}"
            if note:
                row += f"   ({note})"
            lines.append(row)

    lines += [
        "",
        SEP,
        f"  End of scan  —  {len(datapoints)} data points  —  {now_str}",
        SEP,
        "",
    ]

    text = "\n".join(lines)
    with open(OUTPUT_FILE, "w") as fh:
        fh.write(text)

    return len(in_all)


# ---------------------------------------------------------------------------
# Interactive session
# ---------------------------------------------------------------------------

def main():
    print()
    print("=" * 66)
    print("  DSJ2 Wind Direction Scanner v2  —  user-guided memory diff")
    print("=" * 66)
    print()
    print("  METHOD")
    print("  ------")
    print("  Same hill, 7 re-entries.  Baseline snapshot first, then one")
    print("  snapshot per entry paired with the angle you read visually.")
    print("  Scale: 0=East(+X)  90=North(+Y)  180=West  270=South")
    print()
    print(f"  PID  = {PID}")
    print(f"  BASE = 0x{BASE:016x}")
    print(f"  Scan = BASE+0x{SCAN_OFF:05x}  ..  BASE+0x{SCAN_OFF + SCAN_SIZE:05x}  ({SCAN_SIZE // 1024} KB)")
    print(f"  Out  = {OUTPUT_FILE}")
    print()

    # -----------------------------------------------------------------------
    # Baseline
    # -----------------------------------------------------------------------
    print("-" * 66)
    print("  BASELINE")
    print("  Stand at the top of a hill.  Note the wind direction arrow.")
    print("  Press ENTER when ready to capture the baseline snapshot.")
    input("  [Enter] ")

    wstr_base = read_wind_string()
    print(f"  Wind string: \"{wstr_base}\"")
    print(f"  Reading {SCAN_SIZE // 1024} KB baseline snapshot...")
    baseline_snap = read_region()
    print("  Baseline captured.")
    print()

    # -----------------------------------------------------------------------
    # 7 data points
    # -----------------------------------------------------------------------
    datapoints = []

    for dp_num in range(1, NUM_DATAPOINTS + 1):
        print("-" * 66)
        print(f"  DATA POINT {dp_num} / {NUM_DATAPOINTS}")
        print("  Exit to menu, re-enter the SAME hill.  Wait for the wind")
        print("  indicator to appear.  Note the direction, then press ENTER.")
        input("  [Enter] ")

        wstr = read_wind_string()
        print(f"  Wind string: \"{wstr}\"")
        print(f"  Reading snapshot...")
        snap = read_region()

        while True:
            raw = input("  Visual wind direction (0–360, 0=East): ").strip()
            try:
                direction = float(raw)
                if 0.0 <= direction <= 360.0:
                    break
                print("  Value must be between 0 and 360.")
            except ValueError:
                print(f"  Could not parse '{raw}' — enter a number, e.g. 135")

        changed = diff_snaps(baseline_snap, snap)
        print(f"  Changes vs baseline: {len(changed)} 4-byte chunk(s)")
        print()

        datapoints.append({
            "direction":   direction,
            "wind_string": wstr,
            "snapshot":    snap,
            "changed":     changed,
        })

    # -----------------------------------------------------------------------
    # Write output
    # -----------------------------------------------------------------------
    print("=" * 66)
    print("  All 7 data points collected.")
    print(f"  Writing {OUTPUT_FILE} ...")
    n_consistent = write_output(baseline_snap, datapoints)
    print(f"  Done.  {n_consistent} address(es) changed in all 7 data points.")
    print(f"  Open {OUTPUT_FILE} and check the SUMMARY TABLE at the bottom.")
    print()


if __name__ == "__main__":
    main()
