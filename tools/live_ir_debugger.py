"""
Interactive Live IR Debugger for Raspberry Pi Pico (RP2040)
Directly tests GPIO 16 over USB Serial (COM11).
"""

import serial
import time
import sys

PORT = 'COM11'
BAUD = 115200

def connect():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1.0)
        # Interrupt any running code to get REPL
        ser.write(b'\x03\x03\r\n')
        time.sleep(0.2)
        ser.reset_input_buffer()
        return ser
    except Exception as e:
        print(f"[Error] Could not open {PORT}: {e}")
        return None

def exec_code(ser, code):
    ser.write(b'\x01')  # Raw REPL
    time.sleep(0.1)
    ser.read_all()
    ser.write(code.encode('utf-8') + b'\x04')
    time.sleep(0.3)
    out = bytearray()
    start = time.time()
    while time.time() - start < 3.0:
        if ser.in_waiting:
            out.extend(ser.read(ser.in_waiting))
            if out.endswith(b'\x04>') or out.endswith(b'>'):
                break
        time.sleep(0.05)
    ser.write(b'\x02')  # Exit raw
    time.sleep(0.1)
    return out.decode('utf-8', errors='ignore')

def test_dc_high(ser):
    print("\n[TEST 1] Setting GPIO 16 to SOLID HIGH (3.3V)...")
    code = """
from machine import Pin, PWM
try:
    pwm = PWM(Pin(16))
    pwm.deinit()
except:
    pass
p = Pin(16, Pin.OUT)
p.value(1)
try:
    led = Pin('LED', Pin.OUT)
    led.value(1)
except:
    try:
        Pin(25, Pin.OUT).value(1)
    except:
        pass
print("PIN 16 IS NOW SOLID 3.3V (HIGH)")
"""
    print(exec_code(ser, code))
    print("Verification:")
    print(" - Multimeter Red on Pin 21 (GP16), Black on Pin 23/38 (GND) -> Should read ~3.3V")
    print(" - Anode on GP16, Cathode on GND -> If working, IR LED is continuously ON (check with camera!)")

def test_dc_low(ser):
    print("\n[TEST 2] Setting GPIO 16 to SOLID LOW (0.0V)...")
    code = """
from machine import Pin, PWM
try:
    pwm = PWM(Pin(16))
    pwm.deinit()
except:
    pass
p = Pin(16, Pin.OUT)
p.value(0)
try:
    led = Pin('LED', Pin.OUT)
    led.value(0)
except:
    try:
        Pin(25, Pin.OUT).value(0)
    except:
        pass
print("PIN 16 IS NOW SOLID 0.0V (LOW)")
"""
    print(exec_code(ser, code))

def test_1hz_blink(ser):
    print("\n[TEST 3] Running 1 Hz DC Blink on GPIO 16 for 10 seconds...")
    code = """
import time
from machine import Pin, PWM
try:
    PWM(Pin(16)).deinit()
except:
    pass
p = Pin(16, Pin.OUT)
try:
    led = Pin('LED', Pin.OUT)
except:
    led = Pin(25, Pin.OUT)

for i in range(10):
    p.value(1)
    led.value(1)
    time.sleep_ms(500)
    p.value(0)
    led.value(0)
    time.sleep_ms(500)
    print("Blink", i+1, "done")
"""
    print(exec_code(ser, code))

def test_continuous_carrier(ser, duty_pct=50):
    duty_val = int(65535 * (duty_pct / 100.0))
    print(f"\n[TEST 4] Emitting continuous 38.0 kHz carrier on GPIO 16 ({duty_pct}% duty) for 8 seconds...")
    code = f"""
import time
from machine import Pin, PWM
pwm = PWM(Pin(16))
pwm.freq(38000)
pwm.duty_u16({duty_val})
try:
    Pin('LED', Pin.OUT).value(1)
except:
    pass
print("38 kHz carrier active on GP16...")
time.sleep(8)
pwm.duty_u16(0)
try:
    Pin('LED', Pin.OUT).value(0)
except:
    pass
print("Carrier stopped.")
"""
    print(exec_code(ser, code))

def test_fan_packets(ser, cmd_name='POWER_ON', cmd_code=0x07, data_l=0x00, repeats=5):
    print(f"\n[TEST 5] Transmitting {cmd_name} packets ({repeats} times) to Atomberg fan...")
    code = f"""
import time
from machine import Pin, PWM

ADDR = 0xAF
CMD = {cmd_code}
DATA_L = {data_l}
DATA_H_LOW = 0

crc = (ADDR ^ CMD ^ DATA_H_LOW ^ DATA_L) & 0x0F
data_h = (crc << 4) | (DATA_H_LOW & 0x0F)
packet = ((ADDR << 24) | (CMD << 16) | (data_h << 8) | DATA_L) & 0xFFFFFFFF

pwm = PWM(Pin(16))
pwm.freq(38000)
pwm.duty_u16(0)
duty = 32768  # 50% duty

try:
    led = Pin('LED', Pin.OUT)
except:
    led = Pin(25, Pin.OUT)

@micropython.native
def transmit_frame(pkt):
    # Leader
    pwm.duty_u16(duty)
    time.sleep_us(9000)
    pwm.duty_u16(0)
    time.sleep_us(4500)
    # 32 data bits
    for bit in range(31, -1, -1):
        pwm.duty_u16(duty)
        time.sleep_us(560)
        pwm.duty_u16(0)
        if (pkt >> bit) & 0x01:
            time.sleep_us(1690)
        else:
            time.sleep_us(560)
    # Stop bit
    pwm.duty_u16(duty)
    time.sleep_us(560)
    pwm.duty_u16(0)

print("Packet word: 0x%08X" % packet)
for r in range({repeats}):
    led.value(1)
    transmit_frame(packet)
    time.sleep_ms(40)
    transmit_frame(packet)
    led.value(0)
    print("Burst", r + 1, "sent")
    time.sleep_ms(600)

print("All bursts sent!")
"""
    print(exec_code(ser, code))

if __name__ == '__main__':
    ser = connect()
    if not ser:
        sys.exit(1)

    print("Connected to Pico on COM11!")
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == 'high':
            test_dc_high(ser)
        elif arg == 'low':
            test_dc_low(ser)
        elif arg == 'blink':
            test_1hz_blink(ser)
        elif arg == 'carrier':
            test_continuous_carrier(ser)
        elif arg == 'power':
            test_fan_packets(ser, 'POWER_ON', 0x07, 0x00)
        elif arg == 'level1':
            test_fan_packets(ser, 'LEVEL_1', 0x02, 0x01)
        elif arg == 'level3':
            test_fan_packets(ser, 'LEVEL_3', 0x02, 0x03)
        elif arg == 'off':
            test_fan_packets(ser, 'FAN_OFF', 0x00, 0x00)
    else:
        test_continuous_carrier(ser)
    ser.close()

