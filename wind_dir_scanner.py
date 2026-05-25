"""
wind_dir_scanner.py — Locate wind direction in DSJ2 DOSBox RAM.

DSJ2 displays wind speed AND direction in its UI, but the direction has not
yet been found in the wind ASCII string.  This tool scans a ±512-byte window
around the known wind-string address, printing every plausible candidate value
(floats, signed ints, signed bytes) that could encode a direction or heading.

HOW TO USE
----------
1. Set PID and BASE below to match the current DOSBox session.
2. Start DSJ2, load a hill, and note the wind direction shown on screen.
3. Run this script — it will print all candidate values every 0.5 s.
4. Jump to a hill with a DIFFERENT wind direction and watch which value changes.
5. The address and offset of that value is your wind direction field.

Output columns:
  offset  — byte offset relative to WIND_STRING_ADDR (negative = before)
  float32 — 4-byte little-endian float at that offset
  int16   — 2-byte little-endian signed int at that offset
  int8    — 1-byte signed int at that offset
"""

import struct
import time
import os

# --- CURRENT ACTIVE SESSION CONFIG ---
PID  = 11670
BASE = 0x7025ccbff010
# -------------------------------------

WIND_STRING_ADDR = BASE + 0x27363
MEM_PATH = f"/proc/{PID}/mem"

# Scan this many bytes before and after the wind string address
SCAN_BEFORE = 512
SCAN_AFTER  = 512
SCAN_SIZE   = SCAN_BEFORE + SCAN_AFTER

# Only report float values that look like a plausible direction
# (angle in degrees, or a small signed value like -1/0/+1)
FLOAT_RANGE = (-360.0, 360.0)
INT16_RANGE = (-360, 360)

def read_window():
    start = WIND_STRING_ADDR - SCAN_BEFORE
    try:
        with open(MEM_PATH, "rb") as f:
            f.seek(start)
            return f.read(SCAN_SIZE)
    except Exception as e:
        print(f"  [read error] {e}")
        return None


def parse_wind_string(data):
    """Extract the known wind-speed string for reference."""
    string_offset = SCAN_BEFORE  # the wind string is at index SCAN_BEFORE in our window
    raw = data[string_offset:string_offset + 16].split(b'\x00')[0]
    return raw.decode('ascii', errors='ignore').strip()


def scan_candidates(data):
    """Return a list of (offset_from_wind_addr, float32, int16, int8) for every 4-byte
    aligned position in the window that has a plausible float or int value."""
    results = []
    for i in range(0, SCAN_SIZE - 3, 1):          # step by 1 byte for thorough coverage
        offset_from_wind = i - SCAN_BEFORE         # signed offset relative to wind string

        # --- float32 (only at 4-byte alignment to reduce noise) ---
        if i % 4 == 0:
            f32 = struct.unpack_from('<f', data, i)[0]
            f32_ok = (FLOAT_RANGE[0] <= f32 <= FLOAT_RANGE[1]
                      and not (f32 == 0.0)
                      and abs(f32) > 0.001)
        else:
            f32 = None
            f32_ok = False

        # --- int16 (only at 2-byte alignment) ---
        if i % 2 == 0:
            i16 = struct.unpack_from('<h', data, i)[0]
            i16_ok = INT16_RANGE[0] <= i16 <= INT16_RANGE[1] and i16 != 0
        else:
            i16 = None
            i16_ok = False

        # --- int8 ---
        i8 = struct.unpack_from('b', data, i)[0]
        i8_ok = (i8 != 0 and abs(i8) >= 1)

        if f32_ok or i16_ok:
            results.append((offset_from_wind, f32, i16, i8))

    return results


def main():
    print(f"Wind Direction Scanner — scanning {SCAN_BEFORE} bytes before / {SCAN_AFTER} bytes after wind string")
    print(f"Wind string address: 0x{WIND_STRING_ADDR:x}")
    print(f"Scan window:         0x{WIND_STRING_ADDR - SCAN_BEFORE:x} – 0x{WIND_STRING_ADDR + SCAN_AFTER:x}")
    print()
    print("Switch hills to see which value changes with wind direction.")
    print("Press Ctrl+C to stop.\n")

    # Take a baseline snapshot so we can highlight CHANGES
    baseline_data = read_window()
    if baseline_data is None:
        print("Could not read memory. Is DOSBox running with the correct PID/BASE?")
        return

    prev_candidates = {r[0]: r for r in scan_candidates(baseline_data)}
    prev_wind_str = parse_wind_string(baseline_data)

    try:
        while True:
            data = read_window()
            if data is None:
                time.sleep(0.5)
                continue

            wind_str = parse_wind_string(data)
            candidates = {r[0]: r for r in scan_candidates(data)}

            # Find values that CHANGED since the last frame
            changed_offsets = set()
            for off, row in candidates.items():
                prev = prev_candidates.get(off)
                if prev is None:
                    changed_offsets.add(off)
                    continue
                if row[1] != prev[1] or row[2] != prev[2]:   # float32 or int16 changed
                    changed_offsets.add(off)

            os.system('clear')
            print(f"  Wind string @ +0x00: \"{wind_str}\"   (prev: \"{prev_wind_str}\")")
            print()
            print(f"  {'offset':>8}  {'float32':>10}  {'int16':>6}  {'int8':>5}  {'changed?':>8}")
            print(f"  {'-'*8}  {'-'*10}  {'-'*6}  {'-'*5}  {'-'*8}")

            for off in sorted(candidates.keys()):
                _, f32, i16, i8 = candidates[off]
                changed_marker = " <<< CHANGED" if off in changed_offsets else ""
                f32_str = f"{f32:10.4f}" if f32 is not None else f"{'---':>10}"
                i16_str = f"{i16:6d}"    if i16 is not None else f"{'---':>6}"
                print(f"  {off:+8d}  {f32_str}  {i16_str}  {i8:5d}  {changed_marker}")

            print(f"\n  [scanning every 0.5 s — Ctrl+C to quit]")

            prev_candidates = candidates
            prev_wind_str = wind_str
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nScanner stopped.")


if __name__ == "__main__":
    main()
