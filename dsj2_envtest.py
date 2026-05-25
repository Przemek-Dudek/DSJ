import time
import struct

# --- UPDATE THESE ---
PID = 11670                     
BASE_ADDRESS = 0x7025ccbff010   
# --------------------

RAM_SIZE = 16 * 1024 * 1024 # 16 MB

def get_ram_dump():
    try:
        with open(f"/proc/{PID}/mem", 'rb') as f:
            f.seek(BASE_ADDRESS)
            return f.read(RAM_SIZE)
    except PermissionError:
        print("CRITICAL: Run with sudo!")
        exit(1)

if __name__ == "__main__":
    print("🎯 Custom Physics Scanner Initialized.")
    print("Get ready! Start a jump, pause the game, and get ready to unpause.")
    print("Starting in 3 seconds...")
    time.sleep(3)
    
    print("\n📸 Taking Snapshot 1...")
    s1 = get_ram_dump()
    time.sleep(0.5)
    
    print("📸 Taking Snapshot 2...")
    s2 = get_ram_dump()
    time.sleep(0.5)
    
    print("📸 Taking Snapshot 3...")
    s3 = get_ram_dump()
    time.sleep(0.5)
    
    print("📸 Taking Snapshot 4...")
    s4 = get_ram_dump()

    print("\n🔍 Analyzing 16MB of RAM for strictly increasing floats...")
    results = []

    # Iterate through all 16MB in 4-byte chunks
    # (This loop might take 5-10 seconds to process 4 million floats)
    for i in range(0, RAM_SIZE, 4):
        try:
            # Unpack the 4 bytes into a little-endian float for each snapshot
            v1 = struct.unpack('<f', s1[i:i+4])[0]
            v2 = struct.unpack('<f', s2[i:i+4])[0]
            v3 = struct.unpack('<f', s3[i:i+4])[0]
            v4 = struct.unpack('<f', s4[i:i+4])[0]

            # FILTER 1: Sanity Check. X coordinates won't be millions or infinites.
            if -5000.0 < v1 < 5000.0:
                
                # FILTER 2: Did it strictly increase every 0.5 seconds?
                if v1 < v2 < v3 < v4:
                    
                    # FILTER 3: Did it actually move a meaningful amount? 
                    # (Filters out microscopic floating-point noise)
                    if (v4 - v1) > 0.5:
                        results.append((i, v1, v4))
        except:
            # Skip bytes that can't be decoded into floats
            continue

    print(f"\n✅ Scan Complete! Found {len(results)} matches.")
    
    # Print the top 20 results
    for offset, start_val, end_val in results[:20]:
        target_addr = BASE_ADDRESS + offset
        print(f"Offset: {hex(offset)} | Address: {hex(target_addr)} | Value: {start_val:.2f} -> {end_val:.2f}")