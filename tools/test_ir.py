"""
===============================================================================
 NodeVAYU: IR Transmitter Hardware Diagnostic & Verification Tool (MicroPython)
 Repository: https://github.com/sravangantla007/AtomQuest2026
 Pin: Pico GPIO 16 (Physical Pin 21)
===============================================================================

This utility helps you debug and verify why the IR LED isn't transmitting:
 1. Human Eye vs IR: IR light (940nm) is INVISIBLE to the human eye!
    Use a phone camera (front selfie camera usually has no IR cut filter)
    to see the purple/white glow.
 2. Electrical Check: Verifies GPIO 16 voltage with a multimeter.
 3. PWM Carrier Check: Emits continuous 38 kHz bursts to verify carrier.
 4. Auto-Transmit Loop: Periodically sends valid Atomberg fan packets
    (Power ON, Level 1..6, Off) while flashing the onboard Pico LED.
"""

import time
from machine import Pin, PWM

# =============================================================================
# PIN DEFINITIONS
# =============================================================================
IR_PIN_NUM = 16  # GPIO 16 (Pico Pin 21)

# Onboard LED detection
try:
    led = Pin("LED", Pin.OUT)
except Exception:
    try:
        led = Pin(25, Pin.OUT)
    except Exception:
        led = None


def blink_led(times=1, delay_ms=50):
    if not led:
        return
    for _ in range(times):
        led.value(1)
        time.sleep_ms(delay_ms)
        led.value(0)
        time.sleep_ms(delay_ms)


# =============================================================================
# HARDWARE PWM 38 kHz DRIVER
# =============================================================================
class IRTestBench:
    FAN_IR_ADDR        = 0xAF
    CMD_ALL_OFF        = 0x00
    CMD_SET_RAW_SPEED  = 0x01
    CMD_SET_LEVEL      = 0x02
    CMD_TOGGLE_POWER   = 0x03
    CMD_POWER_ON       = 0x07

    def __init__(self, pin_num=IR_PIN_NUM):
        self.pin_num = pin_num
        self.pwm = PWM(Pin(pin_num))
        self.pwm.freq(38000)
        self.pwm.duty_u16(0)
        # 50% duty provides maximum optical power for direct pin or simple transistor drive
        self.duty_50 = 32768
        self.duty_33 = 21845

    def carrier_on(self, duty=None):
        self.pwm.duty_u16(duty if duty is not None else self.duty_50)

    def carrier_off(self):
        self.pwm.duty_u16(0)

    def encode_packet(self, cmd: int, data_l: int, data_h_low: int = 0) -> int:
        crc = (self.FAN_IR_ADDR ^ cmd ^ data_h_low ^ data_l) & 0x0F
        data_h = (crc << 4) | (data_h_low & 0x0F)
        return ((self.FAN_IR_ADDR << 24) |
                (cmd << 16) |
                (data_h << 8) |
                (data_l & 0xFF))

    @micropython.native
    def send_packet_raw(self, packet: int, duty: int = 32768):
        """Transmits 32-bit custom Atomberg frame using native RP2040 machine code."""
        # 1. Leader (9000us carrier, 4500us space)
        self.pwm.duty_u16(duty)
        time.sleep_us(9000)
        self.pwm.duty_u16(0)
        time.sleep_us(4500)

        # 2. 32 Data bits (MSB first)
        for bit in range(31, -1, -1):
            self.pwm.duty_u16(duty)
            time.sleep_us(560)
            self.pwm.duty_u16(0)
            if (packet >> bit) & 0x01:
                time.sleep_us(1690)
            else:
                time.sleep_us(560)

        # 3. Stop bit
        self.pwm.duty_u16(duty)
        time.sleep_us(560)
        self.pwm.duty_u16(0)

        # 4. Mandatory cooldown
        time.sleep_ms(45)


# =============================================================================
# DIAGNOSTIC TESTS
# =============================================================================

def test_1_camera_burst(bench: IRTestBench):
    """
    Test 1: Slow, bright camera flash (1 Hz).
    Turn on phone camera (front selfie camera) and point at the IR LED.
    You should see a distinct PURPLE/WHITE flickering glow.
    """
    print("\n" + "="*60)
    print(" [TEST 1] CAMERA VISIBILITY FLASH (50% Duty @ 38 kHz)")
    print(" Point your smartphone camera at the IR LED on GP16.")
    print(" You should see a purple/violet glow flashing at 1 Hz.")
    print(" (Press Ctrl+C to return to menu)")
    print("="*60)
    count = 0
    while True:
        count += 1
        print(f" -> Pulse #{count}: LED ON (500ms)...", end="")
        if led:
            led.value(1)
        bench.carrier_on(bench.duty_50)
        time.sleep_ms(500)

        print(" LED OFF (500ms)")
        bench.carrier_off()
        if led:
            led.value(0)
        time.sleep_ms(500)


def test_2_continuous_carrier(bench: IRTestBench):
    """
    Test 2: Continuous 38.0 kHz carrier.
    Measure Pin 21 (GP16) with an oscilloscope or multimeter (DC voltage ~1.65V).
    """
    print("\n" + "="*60)
    print(" [TEST 2] CONTINUOUS 38 kHz CARRIER")
    print(" - Frequency: 38,000 Hz")
    print(" - Duty: 50% (Average DC voltage on GP16 should read ~1.65V)")
    print(" (Press Ctrl+C to return to menu)")
    print("="*60)
    if led:
        led.value(1)
    bench.carrier_on(bench.duty_50)
    try:
        while True:
            time.sleep(1)
    finally:
        bench.carrier_off()
        if led:
            led.value(0)


def test_3_dc_voltage(bench: IRTestBench):
    """
    Test 3: Pure DC High/Low toggle (No PWM, standard GPIO).
    Verify with a multimeter:
      - Phase 1: 3.3V solid
      - Phase 2: 0.0V solid
    """
    print("\n" + "="*60)
    print(" [TEST 3] DC GPIO VOLTAGE VERIFICATION")
    print(" Testing GP16 as standard Digital Output (Multimeter check):")
    print(" - Connect Red probe to Pico Pin 21 (GP16)")
    print(" - Connect Black probe to Pico Pin 23 or 38 (GND)")
    print(" (Press Ctrl+C to return to menu)")
    print("="*60)
    bench.carrier_off()
    bench.pwm.deinit()  # Free pin from PWM peripheral

    pin = Pin(IR_PIN_NUM, Pin.OUT)
    while True:
        print(" -> GP16 = HIGH (3.3V) for 2 seconds...")
        pin.value(1)
        if led:
            led.value(1)
        time.sleep(2)

        print(" -> GP16 = LOW  (0.0V) for 2 seconds...")
        pin.value(0)
        if led:
            led.value(0)
        time.sleep(2)


def test_4_auto_fan_loop(bench: IRTestBench):
    """
    Test 4: Automated Atomberg Fan Control Transmission Loop.
    Sends standard commands every 2.5 seconds:
      1. POWER ON (0xAF078000)
      2. LEVEL 1  (0xAF02C001)
      3. LEVEL 3  (0xAF02E003)
      4. LEVEL 5  (0xAF028005)
      5. FAN OFF  (0xAF00F000)
    """
    print("\n" + "="*60)
    print(" [TEST 4] AUTOMATIC ATOMBERG FAN TRANSMISSION LOOP")
    print(" Transmitting ICD-compliant packets every 2.5 seconds.")
    print(" Point IR LED at the Atomberg fan receiver (PA3).")
    print(" (Press Ctrl+C to stop)")
    print("="*60)

    commands = [
        ("POWER ON", bench.CMD_POWER_ON, 0x00),
        ("LEVEL 1 (Gentle Breeze, ~650 RPM)", bench.CMD_SET_LEVEL, 0x01),
        ("LEVEL 3 (Medium, ~1050 RPM)", bench.CMD_SET_LEVEL, 0x03),
        ("LEVEL 5 (High, ~1350 RPM)", bench.CMD_SET_LEVEL, 0x05),
        ("FAN ALL OFF (Coasting down)", bench.CMD_ALL_OFF, 0x00),
    ]

    while True:
        for name, cmd, data_l in commands:
            pkt = bench.encode_packet(cmd, data_l)
            print(f"\n[IR TX] Transmitting: {name}")
            print(f"        Packet Word: 0x{pkt:08X}")

            # Transmit frame twice (like standard remotes for range reliability)
            blink_led(2, 30)
            bench.send_packet_raw(pkt, duty=bench.duty_50)
            time.sleep_ms(40)
            bench.send_packet_raw(pkt, duty=bench.duty_50)

            time.sleep(2.5)


def print_menu():
    print("\n" + "="*60)
    print("        NodeVAYU IR LED Diagnostic Tool (GPIO 16)")
    print("="*60)
    print("  [1] Camera Flash Test (1 Hz carrier flash - look via phone camera)")
    print("  [2] Continuous 38 kHz Carrier Output (Oscilloscope / TSOP check)")
    print("  [3] DC Voltage Test (Toggles solid 3.3V / 0V for multimeter)")
    print("  [4] Automated Fan Command Loop (Sends Power/Level/Off every 2.5s)")
    print("  [q] Exit")
    print("="*60)


def main():
    bench = IRTestBench(IR_PIN_NUM)
    print_menu()

    while True:
        try:
            choice = input("\nEnter choice [1-4, q]: ").strip().lower()
            if choice == '1':
                test_1_camera_burst(bench)
            elif choice == '2':
                test_2_continuous_carrier(bench)
            elif choice == '3':
                test_3_dc_voltage(bench)
                # Re-init PWM after DC test
                bench = IRTestBench(IR_PIN_NUM)
            elif choice == '4':
                test_4_auto_fan_loop(bench)
            elif choice == 'q':
                print("Exiting.")
                break
            else:
                print_menu()
        except KeyboardInterrupt:
            bench.carrier_off()
            if led:
                led.value(0)
            print("\n[Stopped test. Back to menu]")
            print_menu()


if __name__ == "__main__":
    main()
