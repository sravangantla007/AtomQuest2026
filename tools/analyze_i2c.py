import zipfile
import struct
import os

sal_path = r"C:\Users\gvsra\OneDrive\Documents\logic 2\atomberg i2c nomotor.sal"
print("Opening:", sal_path)

with zipfile.ZipFile(sal_path, 'r') as z:
    for name in ["digital-0.bin", "digital-1.bin"]:
        data = z.read(name)
        print(f"\n--- {name} (size: {len(data)}) ---")
        magic = data[:8]
        v, dtype, init_state = struct.unpack("<iii", data[8:20])
        print(f"Magic: {magic}, Version: {v}, Type: {dtype}, Initial State: {init_state}")
        print("First 64 bytes (hex):")
        print(" ".join(f"{b:02x}" for b in data[:64]))
