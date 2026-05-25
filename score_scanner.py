import struct

# --- UPDATE THESE ---
PID = 11670                     
BASE_ADDRESS = 0x7025ccbff010   
# --------------------

RAM_SIZE = 16 * 1024 * 1024 
MIN_TARGET = 36.95
MAX_TARGET = 37.10

def deep_scan():
    try:
        with open(f"/proc/{PID}/mem", 'rb') as f:
            f.seek(BASE_ADDRESS)
            ram_dump = f.read(RAM_SIZE)
            
            print(f"🔍 Deep Scanning RAM for {MIN_TARGET} - {MAX_TARGET}...")
            
            double_matches = []
            int_matches = []
            
            # 1. SCAN FOR 64-BIT DOUBLES (8 bytes)
            for i in range(0, RAM_SIZE - 8, 4):
                try:
                    val = struct.unpack('<d', ram_dump[i:i+8])[0]
                    if MIN_TARGET <= val <= MAX_TARGET:
                        double_matches.append((i, val))
                except Exception:
                    pass
                    
            # 2. SCAN FOR SCALED INTEGERS (4 bytes)
            # Checking x10, x100, x1000, and x10000 scales
            scales = [10, 100, 1000, 10000]
            
            for i in range(0, RAM_SIZE - 4, 4):
                try:
                    val = struct.unpack('<i', ram_dump[i:i+4])[0]
                    for scale in scales:
                        if (MIN_TARGET * scale) <= val <= (MAX_TARGET * scale):
                            int_matches.append((i, val, scale))
                except Exception:
                    pass

            # --- PRINT RESULTS ---
            print(f"\n✅ Scan Complete!")
            
            if double_matches:
                print("\n--- 64-BIT DOUBLE PRECISION MATCHES ---")
                for offset, val in double_matches:
                    print(f"Offset: {hex(offset)} | Value: {val:.6f}")
            else:
                print("\n[-] No 64-bit Double matches found.")
                
            if int_matches:
                print("\n--- SCALED INTEGER MATCHES ---")
                for offset, val, scale in int_matches:
                    print(f"Offset: {hex(offset)} | Memory Value: {val} (Scale: x{scale})")
            else:
                print("\n[-] No Scaled Integer matches found.")

    except PermissionError:
        print("CRITICAL: Run with sudo!")

if __name__ == "__main__":
    deep_scan()