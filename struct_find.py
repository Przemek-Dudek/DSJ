import struct

# --- UPDATE THESE ---
PID = 11670                     
BASE_ADDRESS = 0x7025ccbff010   
# --------------------

# We are going to start reading a bit before the first variable (0x29be8)
STRUCT_START = BASE_ADDRESS + 0x29bd0
READ_LENGTH = 128 # Read 128 bytes to capture the whole cluster

def dump_struct():
    try:
        with open(f"/proc/{PID}/mem", 'rb') as f:
            f.seek(STRUCT_START)
            raw_bytes = f.read(READ_LENGTH)
            
            print("--- JUMPER STRUCT DUMP ---")
            print(f"Raw Hex: {raw_bytes.hex()}\n")
            
            print("--- FLOATS IN THIS STRUCT ---")
            # Iterate through the block 4 bytes at a time and print the floats
            for i in range(0, READ_LENGTH, 4):
                chunk = raw_bytes[i:i+4]
                try:
                    val = struct.unpack('<f', chunk)[0]
                    # Only print if it looks like a sane physics number
                    if -2000.0 < val < 2000.0 and val != 0.0:
                        offset = 0x29bd0 + i
                        print(f"Offset: {hex(offset)} | Value: {val:.2f}")
                except:
                    pass

    except PermissionError:
        print("CRITICAL: Run with sudo!")

if __name__ == "__main__":
    dump_struct()