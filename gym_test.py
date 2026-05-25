import struct
import re
import time

class DSJ2Memory:
    def __init__(self, pid, base_addr):
        self.pid = pid
        self.base_addr = base_addr
        self.ram_size = 16 * 1024 * 1024
        self.mem_path = f"/proc/{self.pid}/mem"
        
        # The signatures
        self.player_sig = b'\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x29\x5c\x20\x42\x00\x00'
        self.wind_sig_short = b'\x26\x7f\x3f\x03'
        
        # Active addresses for the current jump
        self.player_address = None
        self.wind_address = None

    def wait_for_jump(self, timeout=10.0):
        """Loops continuously until the jump is fully loaded in RAM."""
        print("Waiting for jumper to spawn in-game...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                with open(self.mem_path, 'rb') as f:
                    f.seek(self.base_addr)
                    ram_dump = f.read(self.ram_size)
                    
                    p_offset = ram_dump.find(self.player_sig)
                    if p_offset != -1:
                        self.player_address = self.base_addr + p_offset
                        
                    w_offset = ram_dump.find(self.wind_sig_short)
                    if w_offset != -1:
                        self.wind_address = self.base_addr + w_offset + len(self.wind_sig_short)

                    if self.player_address and self.wind_address:
                        print(f"Jump Ready! Player: {hex(self.player_address)} | Wind: {hex(self.wind_address)}")
                        return True
            except PermissionError:
                print("CRITICAL: Run with sudo!")
                return False
                
            time.sleep(0.1)
            
        print("Timeout: Could not find signatures. Are you in the main menu?")
        return False

    def get_state(self):
        """Reads the live physics state."""
        state = {
            "x_vel": 0.0, "y_vel": 0.0, "speed": 0.0,
            "y_pos": 0.0, "x_pos": 0.0, "tilt": 0.0,
            "wind_speed": 0.0, "wind_dir": 0.0  # Added Wind Direction
        }
        
        try:
            with open(self.mem_path, 'rb') as f:
                # 1. Read Player Physics
                if self.player_address:
                    f.seek(self.player_address)
                    player_block = f.read(128)
                    
                    state["x_vel"] = struct.unpack('<f', player_block[36:40])[0]
                    state["y_vel"] = struct.unpack('<f', player_block[40:44])[0]
                    state["speed"] = struct.unpack('<f', player_block[44:48])[0]
                    state["y_pos"] = struct.unpack('<f', player_block[84:88])[0]
                    state["x_pos"] = struct.unpack('<f', player_block[100:104])[0]
                    state["tilt"]  = struct.unpack('<f', player_block[124:128])[0]
                
                # 2. Read Wind String
                if self.wind_address:
                    f.seek(self.wind_address)
                    wind_bytes = f.read(12).split(b'\x00')[0] # Extended read slightly
                    wind_str = wind_bytes.decode('ascii', errors='ignore')
                    
                    # Extract ALL numbers from the string (e.g. "2.85" and "0.0")
                    numbers = re.findall(r"[-+]?\d*\.\d+|\d+", wind_str)
                    if len(numbers) >= 1:
                        state["wind_speed"] = float(numbers[0])
                    if len(numbers) >= 2:
                        # Sometimes the second number in the string relates to angle/direction
                        state["wind_dir"] = float(numbers[1])
                        
        except Exception:
            pass # Ignore mid-frame read errors
            
        return state

    def run_episode(self):
        """
        Monitors the jump, detects the exact landing frame, and returns the score.
        This is your Reinforcement Learning episode loop!
        """
        if not self.wait_for_jump():
            return None

        in_flight = False
        prev_y_vel = 0.0
        final_score = 0.0
        
        print("\n--- EPISODE STARTED ---")
        
        try:
            while True:
                state = self.get_state()
                current_y_vel = state['y_vel']
                
                # 1. Takeoff Detection: High vertical speed and moving away from start gate
                if not in_flight and current_y_vel > 2.0 and state['x_pos'] > 10.0:
                    in_flight = True
                    print("\n[!] TAKEOFF DETECTED! Skier is airborne.")
                
                # 2. Landing Impact Detection
                if in_flight:
                    y_delta = current_y_vel - prev_y_vel
                    
                    # A sudden violent change in Y velocity (delta > 2.0) indicates snow impact
                    if abs(y_delta) > 2.0:
                        final_score = state['x_pos']
                        print(f"\n[!!!] TOUCHDOWN! Impact Frame Captured.")
                        print(f"Final Raw Engine Score: {final_score:.2f}")
                        break # End the episode!
                        
                prev_y_vel = current_y_vel
                
                # Print live state over itself
                print(f"  DIST: {state['x_pos']:8.2f} | Y-VEL: {current_y_vel:5.2f} | TILT: {state['tilt']:5.2f} | WIND: {state['wind_speed']}m/s", end="\r")
                time.sleep(0.05) # 20Hz polling
                
        except KeyboardInterrupt:
            print("\nEpisode aborted by user.")
            
        return final_score

# --- TEST THE EPISODE LOOP ---
if __name__ == "__main__":
    # --- UPDATE THESE TO YOUR CURRENT SESSION ---
    PID = 11670                   
    BASE = 0x7025ccbff010         
    # ------------------------------------------
    
    env = DSJ2Memory(PID, BASE)
    score = env.run_episode()
    
    if score:
        print(f"\nEpisode Complete. Feeding reward of {score:.2f} to RL Agent.")