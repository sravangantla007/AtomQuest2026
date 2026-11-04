import zipfile
import struct

with zipfile.ZipFile("captures/atomberg i2c nomotor.sal", "r") as z:
    d = z.read("digital-0.bin")
    print("digital-0 length:", len(d))
    print("Header fields:")
    magic = d[:8]
    version, data_type, initial_state = struct.unpack("<iii", d[8:20])
    print(f"magic={magic}, version={version}, data_type={data_type}, initial_state={initial_state}")

    # Inspect bytes 20 to 100
    for offset in range(20, 80, 8):
        chunk = d[offset:offset+8]
        u64 = struct.unpack("<Q", chunk)[0]
        i64 = struct.unpack("<q", chunk)[0]
        f64 = struct.unpack("<d", chunk)[0]
        u32_1, u32_2 = struct.unpack("<II", chunk)
        print(f"Offset {offset:2d}: hex={chunk.hex()} | u64={u64:18d} | i64={i64:18d} | f64={f64:14.6e} | u32=({u32_1}, {u32_2})")
