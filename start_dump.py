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
            "wind": 0.0
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
                wind_bytes = f.read(8).split(b'\x00')[0]
                wind_str = wind_bytes.decode('ascii', errors='ignore')
                match = re.search(r"[-+]?\d*\.\d+|\d+", wind_str)
                if match:
                    state["wind"] = float(match.group(0))
        except Exception:
            pass # Catch mid-frame screen transition resets gracefully
            
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
        while True:
            if game.is_jump_active():
                state = game.get_state()
                print("\n>>> LIVE PHYSICS TELEMETRY <<<")
                print(f"  DISTANCE (X_POS): {state['x_pos']:8.2f} m")
                print(f"  ALTITUDE (Y_POS): {state['y_pos']:8.2f} m")
                print(f"  TOTAL SPEED     : {state['speed']:8.2f} m/s (X: {state['x_vel']:.2f}, Y: {state['y_vel']:.2f})")
                print(f"  SKIER TILT      : {state['tilt']:8.2f} rad")
                print(f"  ENVIRONMENT WIND: {state['wind']:8.2f} m/s")
                time.sleep(0.1) # Poll at 10Hz to prevent terminal spamming
            else:
                print("Waiting for jump to start (currently in menu or scoreboard)...", end="\r")
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nTelemetry session ended.")