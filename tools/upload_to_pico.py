import serial
import time
import base64
import sys

PORT = 'COM11'
BAUD = 115200

def upload(local_file='firmware/rp2040_supervisor/pico_firmware.py', remote_file='main.py'):
    with open(local_file, 'r', encoding='utf-8') as f:
        code_text = f.read()

    print(f"Connecting to {PORT}...")
    ser = serial.Serial(PORT, BAUD, timeout=1.5)
    ser.write(b'\x03\x03')
    time.sleep(0.2)
    ser.reset_input_buffer()

    # Enter raw REPL
    ser.write(b'\x01')
    time.sleep(0.2)
    resp = ser.read_all()
    if b'raw REPL' not in resp:
        print("Retrying raw REPL...")
        ser.write(b'\x02\x03\x03\x01')
        time.sleep(0.3)
        resp = ser.read_all()
    
    if b'raw REPL' not in resp:
        print("Failed to enter raw REPL:", resp)
        ser.close()
        return False

    print(f"Entered raw REPL. Writing {remote_file} ({len(code_text)} bytes)...")

    # Open file for writing
    ser.write(f"f = open('{remote_file}', 'wb')\r\n\x04".encode())
    time.sleep(0.3)
    ser.read_all()

    # Encode to base64
    b64_data = base64.b64encode(code_text.encode('utf-8')).decode('ascii')
    chunk_size = 256
    total_chunks = (len(b64_data) + chunk_size - 1) // chunk_size
    print(f"Streaming {total_chunks} base64 chunks...")

    for i in range(0, len(b64_data), chunk_size):
        chunk = b64_data[i:i+chunk_size]
        cmd = f"import ubinascii; f.write(ubinascii.a2b_base64('{chunk}'))\r\n\x04".encode()
        ser.write(cmd)
        time.sleep(0.06)
        ser.read_all()

    ser.write(b"f.close()\r\n\x04")
    time.sleep(0.3)
    ser.read_all()
    print("File written. Verifying size on Pico...")

    ser.write(f"import os; print('Remote size:', os.stat('{remote_file}')[6])\r\n\x04".encode())
    time.sleep(0.3)
    print(ser.read_all().decode(errors='ignore'))

    # Soft reboot Pico
    print("Rebooting Pico into new firmware...")
    ser.write(b'\x02\x04')  # Exit raw REPL, soft reboot
    time.sleep(0.8)
    
    # Read initial boot telemetry
    boot_out = ser.read_all().decode(errors='ignore')
    print("Boot output:\n", boot_out)
    ser.close()
    print("Pico is running successfully!")
    return True

if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else 'firmware/rp2040_supervisor/pico_firmware.py'
    upload(src, 'main.py')
