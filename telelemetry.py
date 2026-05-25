import struct
import re
import time

class DSJ2MemoryDirect:
    def __init__(self, pid, base_addr):
        self.pid = pid
        self.base_addr = base_addr
        self.mem_path = f"/proc/{self.pid}/mem"
        
        self.WIND_STRING_ADDR = self.base_addr + 0x27363
        self.PLAYER_STRUCT_ADDR = self.base_addr + 0x29bd0

    def is_jump_active(self):
        try:
            with open(self.mem_path, 'rb') as f:
                f.seek(self.PLAYER_STRUCT_ADDR + 44)
                speed = struct.unpack('<f', f.read(4))[0]
                return 0.001 < speed < 200.0
        except Exception:
            return False

    def get_state(self):
        state = {
            "x_vel": 0.0, "y_vel": 0.0, "speed": 0.0,
            "y_pos": 0.0, "x_pos": 0.0, "tilt": 0.0,
            "wind": 0.0
        }
        
        try:
            with open(self.mem_path, 'rb') as f:
                # 1. Physics Block
                f.seek(self.PLAYER_STRUCT_ADDR)
                player_block = f.read(128)
                
                state["x_vel"] = struct.unpack('<f', player_block[36:40])[0]
                state["y_vel"] = struct.unpack('<f', player_block[40:44])[0]
                state["speed"] = struct.unpack('<f', player_block[44:48])[0]
                state["y_pos"] = struct.unpack('<f', player_block[84:88])[0]
                state["x_pos"] = struct.unpack('<f', player_block[100:104])[0]
                state["tilt"]  = struct.unpack('<f', player_block[124:128])[0]
                
                # 2. Wind Speed String (Removed the dud secondary number parser)
                f.seek(self.WIND_STRING_ADDR)
                wind_bytes = f.read(8).split(b'\x00')[0]
                wind_str = wind_bytes.decode('ascii', errors='ignore')
                match = re.search(r"[-+]?\d*\.\d+|\d+", wind_str)
                if match:
                    state["wind"] = float(match.group(0))
        except Exception:
            pass 
            
        return state

if __name__ == "__main__":
    # --- CURRENT ACTIVE SESSION CONFIG ---
    PID = 11670                   
    BASE = 0x7025ccbff010         
    # -------------------------------------
    
    game = DSJ2MemoryDirect(PID, BASE)
    
    print("🚀 Direct Kernel Telemetry Connected.")
    print("Monitoring DOSBox emulated system RAM...")
    
    try:
        in_flight = False
        prev_y_vel = 0.0
        flight_frames = 0
        
        while True:
            if game.is_jump_active():
                state = game.get_state()
                current_y_vel = state['y_vel']
                y_delta = current_y_vel - prev_y_vel
                
                # 1. TAKEOFF DETECTION (Using violent physics delta, exactly like landing)
                if not in_flight:
                    # Must be moving fast enough to be on the ramp, and experience a violent spike
                    if state['speed'] > 10.0 and abs(y_delta) > 2.0:
                        in_flight = True
                        flight_frames = 0
                        print("\n\n[!] TAKEOFF DETECTED! (Violent Y-Velocity Delta)")
                
                # 2. LANDING DETECTION
                elif in_flight:
                    flight_frames += 1
                    # Give it a 0.5s (10 frame) grace period so takeoff doesn't trigger landing
                    if flight_frames > 10 and abs(y_delta) > 2.0:
                        final_score = state['x_pos']
                        print(f"\n[!!!] TOUCHDOWN! Impact Frame Captured.")
                        print(f"Final Raw Engine Score (Distance): {final_score:.2f}")
                        break 
                        
                prev_y_vel = current_y_vel
                
                # Live status output
                flight_status = "AIRBORNE" if in_flight else "ON RAMP "
                print(f"  [{flight_status}] DIST: {state['x_pos']:8.2f} | Y-VEL: {current_y_vel:5.2f} | TILT: {state['tilt']:5.2f} | WIND: {state['wind']:4.2f} m/s", end="\r")
                
                time.sleep(0.05)
            else:
                in_flight = False
                prev_y_vel = 0.0
                print("Waiting for jump to start (currently in menu or scoreboard)..." + " "*10, end="\r")
                time.sleep(0.2)
                
    except KeyboardInterrupt:
        print("\nTelemetry session ended by user.")