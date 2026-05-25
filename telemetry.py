import struct
import re
import time

class DSJ2MemoryDirect:
    def __init__(self, pid, base_addr):
        self.pid = pid
        self.base_addr = base_addr
        self.mem_path = f"/proc/{self.pid}/mem"
        
        # Fixed static offsets discovered from your data verification
        self.WIND_STRING_ADDR = self.base_addr + 0x27363
        self.PLAYER_STRUCT_ADDR = self.base_addr + 0x29bd0

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
                
                # 2. Read and clean the wind text string
                f.seek(self.WIND_STRING_ADDR)
                # Read 12 bytes to ensure we capture the secondary direction number
                wind_bytes = f.read(12).split(b'\x00')[0]
                wind_str = wind_bytes.decode('ascii', errors='ignore')
                
                # Extract ALL distinct numbers from the string (e.g. "2.85" and "0.0")
                numbers = re.findall(r"[-+]?\d*\.\d+|\d+", wind_str)
                if len(numbers) >= 1:
                    state["wind_speed"] = float(numbers[0])
                if len(numbers) >= 2:
                    state["wind_dir"] = float(numbers[1])
                    
        except Exception:
            pass # Catch mid-frame screen transition resets gracefully
            
        return state

if __name__ == "__main__":
    # --- CURRENT ACTIVE SESSION CONFIG ---
    PID = 11670                   
    BASE = 0x7025ccbff010         
    # -------------------------------------
    
    game = DSJ2MemoryDirect(PID, BASE)
    
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
                        final_score = state['x_pos']
                        print(f"\n[!!!] TOUCHDOWN at frame {flight_frames}. Impact captured.")
                        print(f"Final Raw Engine Score (Distance): {final_score:.2f}")
                        break

                prev_y_vel = current_y_vel
                prev_y_pos = current_y_pos

                # Live status line
                flight_status = "AIRBORNE" if in_flight else "ON RAMP "
                print(
                    f"  [{flight_status}] DIST: {state['x_pos']:8.2f} | "
                    f"Y-VEL: {current_y_vel:6.2f} | Y-POS: {current_y_pos:8.2f} | "
                    f"TILT: {state['tilt']:5.2f} | WIND: {state['wind_speed']:.2f} m/s | "
                    f"DIR: {state['wind_dir']:.2f}",
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