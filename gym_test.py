import struct
import re
import time

class DSJ2Memory:
    def __init__(self, pid, base_addr):
        self.pid = pid
        self.base_addr = base_addr
        self.ram_size = 16 * 1024 * 1024
        self.mem_path = f"/proc/{self.pid}/mem"
        
        # The signatures we extracted
        self.player_sig = b'\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x29\x5c\x20\x42\x00\x00'
        
        # We dropped the first byte (\xe1) just in case it's a dynamic color code
        self.wind_sig_short = b'\x26\x7f\x3f\x03'
        
        # Active addresses for the current jump
        self.player_address = None
        self.wind_address = None

    def wait_for_jump(self, timeout=10.0):
        """
        Loops continuously until both the player and wind signatures are found.
        This completely solves menu transitions and loading screens!
        """
        print("Waiting for jumper to spawn in-game...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                with open(self.mem_path, 'rb') as f:
                    f.seek(self.base_addr)
                    ram_dump = f.read(self.ram_size)
                    
                    # Look for the Player
                    p_offset = ram_dump.find(self.player_sig)
                    if p_offset != -1:
                        self.player_address = self.base_addr + p_offset
                        
                    # Look for the Wind
                    w_offset = ram_dump.find(self.wind_sig_short)
                    if w_offset != -1:
                        self.wind_address = self.base_addr + w_offset + len(self.wind_sig_short)

                    # If we found BOTH, the jump is officially ready to start
                    if self.player_address and self.wind_address:
                        print(f"Jump Ready! Found Player ({hex(self.player_address)}) and Wind ({hex(self.wind_address)})")
                        return True
                        
            except PermissionError:
                print("CRITICAL: Run with sudo!")
                return False
                
            # Wait 0.1 seconds before checking RAM again
            time.sleep(0.1)
            
        print("Timeout: Could not find signatures. Are you in the main menu?")
        return False

    def get_state(self):
        """Run this on env.step() to get the live observations"""
        state = {
            "x_vel": 0.0, "y_vel": 0.0, "speed": 0.0,
            "y_pos": 0.0, "x_pos": 0.0, "tilt": 0.0,
            "wind": 0.0
        }
        
        try:
            with open(self.mem_path, 'rb') as f:
                # 1. Read the Player Physics Block (128 bytes)
                if self.player_address:
                    f.seek(self.player_address)
                    player_block = f.read(128)
                    
                    # Unpack the floats using our exact byte offsets
                    state["x_vel"] = struct.unpack('<f', player_block[36:40])[0]
                    state["y_vel"] = struct.unpack('<f', player_block[40:44])[0]
                    state["speed"] = struct.unpack('<f', player_block[44:48])[0]
                    state["y_pos"] = struct.unpack('<f', player_block[84:88])[0]
                    state["x_pos"] = struct.unpack('<f', player_block[100:104])[0]
                    state["tilt"]  = struct.unpack('<f', player_block[124:128])[0]
                
                # 2. Read the Wind String
                if self.wind_address:
                    f.seek(self.wind_address)
                    wind_bytes = f.read(8).split(b'\x00')[0]
                    wind_str = wind_bytes.decode('ascii', errors='ignore')
                    match = re.search(r"[-+]?\d*\.\d+|\d+", wind_str)
                    if match:
                        state["wind"] = float(match.group(0))
                        
        except Exception as e:
            # Failsafe for mid-jump memory destruction
            pass 
            
        return state

# --- TEST THE CLASS ---
if __name__ == "__main__":
    # --- UPDATE THESE TO YOUR CURRENT SESSION ---
    PID = 11670                   
    BASE = 0x7025ccbff010         
    # ------------------------------------------
    
    game = DSJ2Memory(PID, BASE)
    
    # 1. Wait for the jump to start
    success = game.wait_for_jump(timeout=20.0) 
    
    if success:
        # 2. Read the state
        print("\nLive State:")
        state = game.get_state()
        for k, v in state.items():
            print(f"  {k.upper()}: {v:.2f}")