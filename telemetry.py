import os
import sys
import struct
import re
import time

# ── Process / memory auto-discovery ──────────────────────────────────────────
PROCESS_NAME   = "dosbox"         # case-insensitive cmdline substring
WIND_SPEED_OFF = 0x27363          # confirmed offset of wind-speed ASCII string
DEFAULT_BASE   = 0x7ac6c6fff010   # last-known-good base; verified/re-discovered at startup


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
    Confirm base is the correct DOS RAM start.

    Returns the wind-speed string if the ASCII string at BASE+WIND_SPEED_OFF
    looks valid (digits and dots), a fallback message if the address is merely
    readable, or None if the address is unreadable.
    """
    try:
        with open(f'/proc/{pid}/mem', 'rb') as f:
            f.seek(base + WIND_SPEED_OFF)
            raw = f.read(16)
    except OSError:
        return None

    stripped = raw.split(b'\x00')[0]
    s = stripped.decode('ascii', errors='ignore').strip()
    if len(s) >= 3 and all(c in '0123456789.' for c in s):
        return s                                       # confirmed: wind string present

    return "(readable – navigate to hill to confirm)"


def find_base_auto(pid: int):
    """
    Scan /proc/<pid>/maps for rw-p anonymous regions >= 8 MB (DOS RAM is 16 MB).
    Probe each candidate; prefer the one with a valid wind string, fall back to
    any readable one.
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
                    if size >= 8 * 1024 * 1024:
                        candidates.append((start, size))
                except ValueError:
                    pass
    except OSError:
        pass

    candidates.sort(key=lambda x: -x[1])   # largest region first

    best_fallback = None
    for base, _ in candidates:
        ws = verify_base(pid, base)
        if ws is None:
            continue
        if 'readable' not in ws:            # strong match: wind string present
            return base, ws
        if best_fallback is None:
            best_fallback = (base, ws)

    if best_fallback:
        return best_fallback
    return None, None


# ─────────────────────────────────────────────────────────────────────────────

class DSJ2MemoryDirect:
    def __init__(self, pid, base_addr):
        self.pid = pid
        self.base_addr = base_addr
        self.mem_path = f"/proc/{self.pid}/mem"
        
        # Fixed static offsets discovered from your data verification
        self.WIND_STRING_ADDR   = self.base_addr + 0x27363
        # Wind direction: f32 at +0x29674; transform: (-57.46 * raw + 361.81) % 360
        # Source: exhaustive RAM scan (7 snapshots, MAE 8.8°, CORE — changed in all snaps)
        self.WIND_DIR_ADDR      = self.base_addr + 0x29674
        self.PLAYER_STRUCT_ADDR = self.base_addr + 0x29bd0

        # Final jump distance result (float32, written to RAM at landing)
        # Source: 4-run scanner — present as CHANGED AT LANDING in runs 1, 2, 4; first hit in run 3
        self.DISTANCE_RESULT_ADDR = self.base_addr + 0x29be2

        # Five judge score slots (float32 each, uniform stride 0x109 = 265 bytes)
        # Source: 4-run scanner — cross-validated against manually entered scores all 4 runs
        self.JUDGE_SCORE_ADDRS = [
            self.base_addr + 0x2969d,  # Judge 1
            self.base_addr + 0x297a6,  # Judge 2
            self.base_addr + 0x298af,  # Judge 3
            self.base_addr + 0x299b8,  # Judge 4
            self.base_addr + 0x29ac1,  # Judge 5
        ]

    def get_results(self):
        """Reads the final jump distance and all 5 judge scores after landing.
        Call ~1.5 s after touchdown to give the game time to write results.
        Returns (distance_m: float, scores: list[float])."""
        distance = 0.0
        scores = [0.0] * 5
        try:
            with open(self.mem_path, 'rb') as f:
                f.seek(self.DISTANCE_RESULT_ADDR)
                distance = struct.unpack('<f', f.read(4))[0]
                for i, addr in enumerate(self.JUDGE_SCORE_ADDRS):
                    f.seek(addr)
                    scores[i] = struct.unpack('<f', f.read(4))[0]
        except Exception:
            pass
        return distance, scores

    def is_jump_active(self):
        """Checks if the game has currently loaded active physics data into the struct"""
        try:
            with open(self.mem_path, 'rb') as f:
                # Seek directly to the total speed variable (+44 bytes inside struct)
                f.seek(self.PLAYER_STRUCT_ADDR + 44)
                speed = struct.unpack('<f', f.read(4))[0]
                
                # If speed is a valid positive float and not exactly zero or garbage menu bytes,
                # the physics simulator loop is running!
                return 0.001 < speed < 200.0
        except Exception:
            return False

    def get_state(self):
        """Reads the entire physics block and wind string simultaneously"""
        state = {
            "x_vel": 0.0, "y_vel": 0.0, "speed": 0.0,
            "y_pos": 0.0, "x_pos": 0.0, "tilt": 0.0,
            "wind_speed": 0.0, "wind_dir": 0.0
        }
        
        try:
            with open(self.mem_path, 'rb') as f:
                # 1. Read the 128-byte player physics block in a single operation
                f.seek(self.PLAYER_STRUCT_ADDR)
                player_block = f.read(128)
                
                state["x_vel"] = struct.unpack('<f', player_block[36:40])[0]
                state["y_vel"] = struct.unpack('<f', player_block[40:44])[0]
                state["speed"] = struct.unpack('<f', player_block[44:48])[0]
                state["y_pos"] = struct.unpack('<f', player_block[84:88])[0]
                state["x_pos"] = struct.unpack('<f', player_block[100:104])[0]
                state["tilt"]  = struct.unpack('<f', player_block[124:128])[0]
                
                # 2. Wind speed — first number from the ASCII string only.
                #    The string format is "X.X.Y.Y" where X.X is speed and
                #    ".Y.Y" is a constant suffix unrelated to direction.
                f.seek(self.WIND_STRING_ADDR)
                wind_bytes = f.read(12).split(b'\x00')[0]
                wind_str = wind_bytes.decode('ascii', errors='ignore')
                numbers = re.findall(r"[-+]?\d*\.\d+|\d+", wind_str)
                if numbers:
                    state["wind_speed"] = float(numbers[0])

                # 3. Wind direction — f32 raw value, linear regression transform.
                f.seek(self.WIND_DIR_ADDR)
                raw_dir = struct.unpack('<f', f.read(4))[0]
                state["wind_dir"] = (-57.46 * raw_dir + 361.81) % 360

        except Exception:
            pass # Catch mid-frame screen transition resets gracefully
            
        return state

if __name__ == "__main__":
    print(f"  Searching for '{PROCESS_NAME}' process...", end=' ', flush=True)
    pid = find_pid(PROCESS_NAME)
    if pid is None:
        print("NOT FOUND.\n  Start DOSBox with DSJ2 loaded, then re-run.")
        sys.exit(1)
    print(f"PID = {pid}")

    print(f"  Verifying BASE_ADDR = 0x{DEFAULT_BASE:016x}...", end=' ', flush=True)
    ws = verify_base(pid, DEFAULT_BASE)
    if ws:
        base = DEFAULT_BASE
        print(f"OK  (wind: \"{ws}\")")
    else:
        print("failed.")
        print("  Scanning /proc/maps for correct base address...")
        base, ws = find_base_auto(pid)
        if base is None:
            print("  [error] Cannot locate DOS RAM base.")
            print("  Make sure DSJ2 is loaded and at the pre-jump screen.")
            sys.exit(1)
        print(f"  Discovered BASE = 0x{base:016x}  (wind: \"{ws}\")")

    game = DSJ2MemoryDirect(pid, base)
    print("Direct Kernel Telemetry Connected.")
    print("Monitoring DOSBox emulated system RAM...")
    
    try:
        in_flight = False
        prev_y_vel = 0.0
        prev_y_pos = 0.0
        flight_frames = 0        # frames elapsed since confirmed takeoff
        rising_streak = 0        # consecutive frames where y_pos is increasing

        while True:
            if game.is_jump_active():
                state = game.get_state()
                current_y_vel = state['y_vel']
                current_y_pos = state['y_pos']

                # --- 1. TAKEOFF DETECTION ---
                # On the ramp, y_pos continuously decreases (skier going downhill).
                # The moment the skier kicks off the table, y_pos switches to increasing.
                # Require 2 consecutive rising frames + meaningful speed to avoid noise.
                if not in_flight:
                    y_pos_delta = current_y_pos - prev_y_pos
                    if y_pos_delta > 0.05 and state['speed'] > 10.0:
                        rising_streak += 1
                    else:
                        rising_streak = 0

                    if rising_streak >= 2:
                        in_flight = True
                        flight_frames = 0
                        rising_streak = 0
                        print(f"\n\n[!] TAKEOFF DETECTED at x={state['x_pos']:.2f}m — skier is airborne.")

                # --- 2. LANDING DETECTION ---
                elif in_flight:
                    flight_frames += 1
                    y_delta = current_y_vel - prev_y_vel

                    # Grace period: ignore the first 20 frames (~1 s) after takeoff so the
                    # kick transient cannot re-trigger as a false landing.
                    # After that, a sudden y_vel change indicates snow impact.
                    if flight_frames > 20 and abs(y_delta) > 2.0:
                        print(f"\n[!!!] TOUCHDOWN at frame {flight_frames}. Impact captured.")
                        print("      Waiting 1.5 s for results to appear in RAM...")
                        time.sleep(1.5)
                        distance, scores = game.get_results()
                        total = sum(scores)
                        print()
                        print(f"  Distance : {distance:.1f} m")
                        print(f"  Judges   : {'  '.join(f'{s:.1f}' for s in scores)}")
                        print(f"  Total    : {total:.1f}")
                        print()
                        break

                prev_y_vel = current_y_vel
                prev_y_pos = current_y_pos

                # Live status line
                flight_status = "AIRBORNE" if in_flight else "ON RAMP "
                print(
                    f"  [{flight_status}] DIST: {state['x_pos']:8.2f} | "
                    f"Y-VEL: {current_y_vel:6.2f} | Y-POS: {current_y_pos:8.2f} | "
                    f"TILT: {state['tilt']:5.2f} | WIND: {state['wind_speed']:.2f} m/s @ {state['wind_dir']:.0f}°",
                    end="\r"
                )

                time.sleep(0.05)  # 20 Hz polling
            else:
                # Not in an active jump — reset all tracking state
                in_flight = False
                prev_y_vel = 0.0
                prev_y_pos = 0.0
                flight_frames = 0
                rising_streak = 0
                print("Waiting for jump to start (currently in menu or scoreboard)..." + " "*20, end="\r")
                time.sleep(0.2)

    except KeyboardInterrupt:
        print("\nTelemetry session ended by user.")