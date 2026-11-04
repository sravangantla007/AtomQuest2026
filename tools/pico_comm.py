import serial
import time
import sys

PORT = 'COM11'
BAUD = 115200

def reset_repl(ser):
    ser.write(b'\x03\x03\x02') # Ctrl-C, Ctrl-C, Ctrl-B (normal REPL)
    time.sleep(0.15)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

def enter_raw_repl(ser):
    reset_repl(ser)
    ser.write(b'\x01') # Ctrl-A (Raw REPL)
    time.sleep(0.15)
    resp = ser.read_all()
    if b'raw REPL' not in resp:
        ser.write(b'\x03\x01')
        time.sleep(0.2)
        resp = ser.read_all()
    return b'raw REPL' in resp

def upload_file(local_path, remote_path='main.py'):
    with open(local_path, 'r', encoding='utf-8') as f:
        content = f.read()

    print(f"Connecting to {PORT}...")
    ser = serial.Serial(PORT, BAUD, timeout=2.0, write_timeout=2.0)
    
    if not enter_raw_repl(ser):
        print("Failed to enter raw REPL!")
        ser.close()
        return False

    print(f"Uploading {local_path} -> {remote_path} ({len(content)} bytes)...")
    
    # Open file for writing
    ser.write(f"f = open('{remote_path}', 'w')\r\n\x04".encode())
    time.sleep(0.2)
    ser.read_all()

    # Stream chunks of 256 bytes
    chunk_size = 256
    for i in range(0, len(content), chunk_size):
        chunk = content[i:i+chunk_size]
        escaped = repr(chunk)
        cmd = f"f.write({escaped})\r\n\x04".encode()
        ser.write(cmd)
        time.sleep(0.08)
        ser.read_all()

    # Close file
    ser.write(b"f.close()\r\n\x04")
    time.sleep(0.2)
    res = ser.read_all().decode(errors='ignore')
    print("Write complete.")

    # Check file size
    ser.write(f"import os; print('Remote Size:', os.stat('{remote_path}')[6])\r\n\x04".encode())
    time.sleep(0.3)
    stat_res = ser.read_all().decode(errors='ignore')
    print(stat_res)

    # Soft reboot Pico into new firmware
    print("Soft-rebooting Pico...")
    ser.write(b'\x02\x04') # Ctrl-B (exit raw), Ctrl-D (soft reboot)
    time.sleep(0.5)
    ser.close()
    print("Pico is now running the new firmware!")
    return True

if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else 'firmware/rp2040_supervisor/pico_firmware.py'
    dest = sys.argv[2] if len(sys.argv) > 2 else 'main.py'
    upload_file(src, dest)
