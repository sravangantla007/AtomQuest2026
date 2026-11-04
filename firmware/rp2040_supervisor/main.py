"""
===============================================================================
 NodeVAYU Edge AI Supervisor: Hardware Verification Firmware (MicroPython)
 Repository: https://github.com/sravangantla007/AtomQuest2026
 Project: atomQUEST 2026 | Track 1: Air, Reimagined for the Next Decade
 Target Board: Raspberry Pi Pico (RP2040)

 Pinout Wiring Specification:
 -----------------------------------------------------------------------------
 1. HLK-LD2450 24GHz mmWave Radar (UART0 @ 256,000 baud):
    - Pico GP28 (Pin 34) -> Radar RX (UART0 TX)
    - Pico GP29 (Pin 35) -> Radar TX (UART0 RX)
    - Pico VBUS (Pin 40) -> Radar 5V (Note: LD2450 requires 5V supply, ~200mA)
    - Pico GND  (Pin 38) -> Radar GND
 
 2. HW-220 / HTU21D Digital Temp & RH Sensor (I2C1 @ 100 kHz, Addr: 0x40):
    - Pico GP26 (Pin 31) -> HW-220 SDA
    - Pico GP27 (Pin 32) -> HW-220 SCL
    - Pico 3V3  (Pin 36) -> HW-220 VCC (1.5V - 3.6V supply)
    - Pico GND  (Pin 33) -> HW-220 GND

 3. Infrared Transmitter (38 kHz Hardware PWM):
    - Pico GP16 (Pin 21) -> IR LED Anode (Long leg) or Driver Base
    - Pico GND  (Pin 23 or 38) -> IR LED Cathode (Short leg / flat side)
    - Compliant with NODEVAYU_IR_INTERFACE_SPEC.md (Address: 0xAF, NEC timing)

 4. Onboard Activity LED:
    - Pico GP25 (Standard Pico) or 'LED' (Pico W)
===============================================================================
"""

import time
import math
import sys
import select
from machine import Pin, PWM, I2C, UART

# =============================================================================
# PIN CONFIGURATION
# =============================================================================
PIN_RADAR_TX   = 28   # Pico UART0 TX -> LD2450 RX
PIN_RADAR_RX   = 29   # Pico UART0 RX <- LD2450 TX
PIN_I2C_SDA    = 26   # Pico I2C1 SDA -> HW-220 SDA
PIN_I2C_SCL    = 27   # Pico I2C1 SCL -> HW-220 SCL
PIN_IR_TX      = 16   # Pico PWM -> 38 kHz IR LED (Physical Pin 21)

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
# 1. HW-220 / HTU21D DIGITAL TEMPERATURE & HUMIDITY DRIVER
# =============================================================================
class HW220_HTU21D:
    I2C_ADDR = 0x40  # 64 decimal
    CMD_TRIGGER_TEMP_NOHOLD = 0xF3
    CMD_TRIGGER_HUMID_NOHOLD = 0xF5
    CMD_SOFT_RESET = 0xFE
    CMD_READ_USER_REG = 0xE7

    def __init__(self, i2c: I2C):
        self.i2c = i2c
        self.connected = False

    def probe(self) -> bool:
        """Scan I2C bus and confirm presence of 0x40."""
        try:
            devices = self.i2c.scan()
            self.connected = (self.I2C_ADDR in devices)
            return self.connected
        except Exception:
            self.connected = False
            return False

    def soft_reset(self):
        """Soft-reset the sensor to clear registers."""
        try:
            self.i2c.writeto(self.I2C_ADDR, bytes([self.CMD_SOFT_RESET]))
            time.sleep_ms(20)
        except Exception:
            pass

    def read_temperature(self) -> float:
        """Read temperature in Celsius."""
        self.i2c.writeto(self.I2C_ADDR, bytes([self.CMD_TRIGGER_TEMP_NOHOLD]))
        time.sleep_ms(55)  # 14-bit conversion takes up to 50ms
        raw = self.i2c.readfrom(self.I2C_ADDR, 3)
        raw_val = (raw[0] << 8) | raw[1]
        raw_val &= 0xFFFC  # Clear lower 2 status bits
        temp_c = -46.85 + (175.72 * (raw_val / 65536.0))
        return round(temp_c, 2)

    def read_humidity(self) -> float:
        """Read relative humidity in %."""
        self.i2c.writeto(self.I2C_ADDR, bytes([self.CMD_TRIGGER_HUMID_NOHOLD]))
        time.sleep_ms(30)  # 12-bit conversion takes up to 25ms
        raw = self.i2c.readfrom(self.I2C_ADDR, 3)
        raw_val = (raw[0] << 8) | raw[1]
        raw_val &= 0xFFFC  # Clear lower 2 status bits
        rh = -6.0 + (125.0 * (raw_val / 65536.0))
        rh = max(0.0, min(100.0, rh))
        return round(rh, 2)

    def read_both(self):
        """Returns (temperature_c, humidity_rh) or (None, None) on error."""
        try:
            t = self.read_temperature()
            h = self.read_humidity()
            return t, h
        except Exception:
            return None, None


# =============================================================================
# 2. HLK-LD2450 24GHz mmWAVE MULTI-TARGET TRACKING RADAR DRIVER
# =============================================================================
class LD2450Radar:
    FRAME_HEADER = b'\xAA\xFF\x03\x00'
    FRAME_TAIL   = b'\x55\xCC'
    FRAME_LEN    = 30

    def __init__(self, uart: UART):
        self.uart = uart
        self.buffer = bytearray()
        self.total_frames_received = 0
        self.total_bytes_received = 0

    def _decode_signed_16(self, low_byte: int, high_byte: int) -> int:
        val = low_byte | (high_byte << 8)
        # LD2450 protocol specifies sign bit at bit 15 (sign-magnitude)
        if val & 0x8000:
            return -(val & 0x7FFF)
        return val

    def update(self):
        """Poll UART buffer and extract latest tracking frame."""
        if self.uart.any():
            incoming = self.uart.read()
            if incoming:
                self.total_bytes_received += len(incoming)
                self.buffer.extend(incoming)

        # Discard stale buffer overflow (> 256 bytes) to keep latency under 100ms
        if len(self.buffer) > 256:
            self.buffer = self.buffer[-128:]

        latest_targets = None

        while True:
            idx = self.buffer.find(self.FRAME_HEADER)
            if idx == -1:
                if len(self.buffer) > 3:
                    self.buffer = self.buffer[-3:]
                break

            if len(self.buffer) < idx + self.FRAME_LEN:
                if idx > 0:
                    self.buffer = self.buffer[idx:]
                break

            frame = self.buffer[idx : idx + self.FRAME_LEN]
            self.buffer = self.buffer[idx + self.FRAME_LEN :]

            if frame[28:30] != self.FRAME_TAIL:
                continue

            self.total_frames_received += 1
            targets = []

            for t_idx in range(3):
                offset = 4 + (t_idx * 8)
                x = self._decode_signed_16(frame[offset], frame[offset + 1])
                y = self._decode_signed_16(frame[offset + 2], frame[offset + 3])
                spd = self._decode_signed_16(frame[offset + 4], frame[offset + 5])
                res = frame[offset + 6] | (frame[offset + 7] << 8)

                if x != 0 or y != 0:
                    dist_mm = math.sqrt(x * x + y * y)
                    targets.append({
                        "id": t_idx + 1,
                        "x_mm": x,
                        "y_mm": y,
                        "speed_cms": spd,
                        "dist_m": round(dist_mm / 1000.0, 2),
                        "resolution_mm": res
                    })

            latest_targets = targets

        return latest_targets


# =============================================================================
# 3. INFRARED ACTUATOR (ICD COMPLIANT - 38 kHz HARDWARE PWM)
# =============================================================================
class NodeVayuIRTransmitter:
    """
    Transmits 32-bit Pulse-Distance custom IR frames according to:
    NODEVAYU_IR_INTERFACE_SPEC.md (Address: 0xAF, Carrier: 38.0 kHz)
    """
    FAN_IR_ADDR        = 0xAF
    CMD_ALL_OFF        = 0x00
    CMD_SET_RAW_SPEED  = 0x01
    CMD_SET_LEVEL      = 0x02
    CMD_TOGGLE_POWER   = 0x03
    CMD_SET_LIGHT_MODE = 0x04
    CMD_STEP_UP        = 0x05
    CMD_STEP_DOWN      = 0x06
    CMD_POWER_ON       = 0x07

    def __init__(self, tx_pin: int = PIN_IR_TX):
        self.tx_pin = tx_pin
        self.pwm = PWM(Pin(tx_pin))
        self.pwm.freq(38000)
        self.pwm.duty_u16(0)
        # 50% duty (32768) maximizes optical power and range
        self.DUTY_50 = 32768
        self.DUTY_33 = 21845

    def carrier_on(self, duty: int = None):
        self.pwm.duty_u16(duty if duty is not None else self.DUTY_50)

    def carrier_off(self):
        self.pwm.duty_u16(0)

    def encode_packet(self, cmd: int, data_l: int, data_h_low: int = 0) -> int:
        """Calculates 4-bit XOR checksum and packs into a 32-bit big-endian integer."""
        crc = (self.FAN_IR_ADDR ^ cmd ^ data_h_low ^ data_l) & 0x0F
        data_h = (crc << 4) | (data_h_low & 0x0F)
        return ((self.FAN_IR_ADDR << 24) |
                (cmd << 16) |
                (data_h << 8) |
                (data_l & 0xFF))

    @micropython.native
    def _send_raw_bits(self, packet: int, duty: int):
        """Native RP2040 machine code generation to ensure exact microsecond bit timings."""
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
                time.sleep_us(1690)  # Bit 1
            else:
                time.sleep_us(560)   # Bit 0

        # 3. Stop bit
        self.pwm.duty_u16(duty)
        time.sleep_us(560)
        self.pwm.duty_u16(0)

    def transmit_packet(self, packet: int, repeats: int = 2):
        """Transmits packet with duplicate frames (standard for reliable IR range)."""
        if led:
            led.value(1)
        for r in range(repeats):
            self._send_raw_bits(packet, self.DUTY_50)
            time.sleep_ms(40)  # Mandatory inter-frame cooldown
        if led:
            led.value(0)

    def burst_carrier_test(self, duration_ms: int = 300):
        """Generates continuous 38 kHz carrier burst for phone camera verification."""
        if led:
            led.value(1)
        self.carrier_on(self.DUTY_50)
        time.sleep_ms(duration_ms)
        self.carrier_off()
        if led:
            led.value(0)

    def send_power_on(self):
        pkt = self.encode_packet(self.CMD_POWER_ON, 0x00)
        self.transmit_packet(pkt)
        return pkt

    def send_toggle_power(self):
        pkt = self.encode_packet(self.CMD_TOGGLE_POWER, 0x00)
        self.transmit_packet(pkt)
        return pkt

    def send_all_off(self):
        pkt = self.encode_packet(self.CMD_ALL_OFF, 0x00)
        self.transmit_packet(pkt)
        return pkt

    def send_level(self, level: int):
        if level < 1 or level > 6:
            return 0
        pkt = self.encode_packet(self.CMD_SET_LEVEL, level)
        self.transmit_packet(pkt)
        return pkt

    def send_raw_speed(self, speed_counts: int):
        speed_counts = max(0, min(511, speed_counts))
        pkt = self.encode_packet(
            self.CMD_SET_RAW_SPEED,
            data_l=speed_counts & 0xFF,
            data_h_low=(speed_counts >> 8) & 0x01
        )
        self.transmit_packet(pkt)
        return pkt


# =============================================================================
# HARDWARE INITIALIZATION & VERIFICATION
# =============================================================================
def init_hardware():
    print("\n" + "="*68)
    print("  NodeVAYU Edge AI Supervisor: Hardware Diagnostic & Verification")
    print("  Raspberry Pi Pico (RP2040) Firmware")
    print("="*68)

    # 1. Initialize I2C1 for HW-220
    print("\n[1/3] Initializing I2C1 for HW-220 (HTU21D)...")
    print(f"      Pins: SDA = GP{PIN_I2C_SDA}, SCL = GP{PIN_I2C_SCL} @ 100 kHz")
    i2c = I2C(1, sda=Pin(PIN_I2C_SDA), scl=Pin(PIN_I2C_SCL), freq=100000)
    devices = i2c.scan()
    print(f"      I2C Bus Scan found {len(devices)} device(s): {[hex(d) for d in devices]}")

    sensor = HW220_HTU21D(i2c)
    if sensor.probe():
        sensor.soft_reset()
        t, rh = sensor.read_both()
        print(f"      [PASS] HW-220 (HTU21D) verified at address 0x40!")
        print(f"             Live Reading: T = {t} °C | RH = {rh} %")
    else:
        print("      [WARN] HW-220 NOT detected at 0x40.")
        print("             Troubleshooting:")
        print("             - Ensure HW-220 VCC is connected to 3.3V (Pico Pin 36) and GND to GND.")
        print(f"             - Check SDA -> GP{PIN_I2C_SDA} (Pico Pin 31) and SCL -> GP{PIN_I2C_SCL} (Pico Pin 32).")

    # 2. Initialize UART0 for HLK-LD2450 Radar
    print("\n[2/3] Initializing UART0 for HLK-LD2450 mmWave Radar...")
    print(f"      Pins: TX = GP{PIN_RADAR_TX} (Pico Pin 34), RX = GP{PIN_RADAR_RX} (Pico Pin 35)")
    print("      Baudrate: 256,000 bps (Standard LD2450 tracking stream)")
    try:
        uart0 = UART(0, baudrate=256000, tx=Pin(PIN_RADAR_TX), rx=Pin(PIN_RADAR_RX), rxbuf=1024)
    except TypeError:
        uart0 = UART(0, baudrate=256000, tx=Pin(PIN_RADAR_TX), rx=Pin(PIN_RADAR_RX))

    radar = LD2450Radar(uart0)

    print("      Listening for incoming radar telemetry stream (1.5s window)...")
    start_t = time.ticks_ms()
    received_any = False
    valid_frames = 0
    while time.ticks_diff(time.ticks_ms(), start_t) < 1500:
        targets = radar.update()
        if radar.total_bytes_received > 0 and not received_any:
            received_any = True
        if targets is not None:
            valid_frames += 1
        time.sleep_ms(20)

    if valid_frames > 0:
        print(f"      [PASS] HLK-LD2450 active! Received {radar.total_bytes_received} bytes ({valid_frames} valid frames).")
    elif received_any:
        print(f"      [PASS-RAW] Received {radar.total_bytes_received} bytes over UART, but framing in progress.")
    else:
        print("      [WARN] 0 bytes received from HLK-LD2450.")
        print("             Troubleshooting:")
        print("             - HLK-LD2450 requires 5V power (connect to Pico VBUS Pin 40, ~200mA peak).")
        print("             - Verify crossover: Radar TX -> Pico RX (GP29), Radar RX -> Pico TX (GP28).")

    # 3. Initialize IR Transmitter PWM on GP16
    print(f"\n[3/3] Initializing 38 kHz Hardware PWM IR Transmitter on GP{PIN_IR_TX} (Pico Pin 21)...")
    ir = NodeVayuIRTransmitter(PIN_IR_TX)
    print("      [PASS] 38.0 kHz carrier PWM initialized (50% Duty Boost).")
    print("      Emitting visible 300ms 38 kHz test burst (Check via phone camera)...")
    ir.burst_carrier_test(300)

    # Immediately transmit Power ON + Level 1 to test fan responsiveness
    print("      Sending initial test packet: [POWER ON] + [LEVEL 1]...")
    ir.send_power_on()
    time.sleep_ms(100)
    ir.send_level(1)

    blink_led(3, 60)
    print("\n" + "="*68)
    print("  Hardware Initialization Complete!")
    print("="*68)
    return sensor, radar, ir


# =============================================================================
# INTERACTIVE TELEMETRY & VERIFICATION LOOP
# =============================================================================
def print_help():
    print("\n--- Interactive Key Commands (Send character via Serial Terminal) ---")
    print("  [1] - [6] : Transmit discrete Speed Level 1 to 6 to Atomberg fan")
    print("  [0]       : Transmit Fan ALL OFF command (0xAF00F000)")
    print("  [p]       : Transmit Toggle Power command (0xAF03C000)")
    print("  [o]       : Transmit Explicit Power ON command (0xAF078000)")
    print("  [a]       : Toggle AUTO-TRANSMIT mode (cycles fan levels automatically)")
    print("  [b]       : Emit 500ms 38 kHz carrier burst (Phone camera check)")
    print("  [s]       : Run single-shot sensor read (HW-220 T/RH)")
    print("  [h] / [?] : Show this help menu")
    print("  [q]       : Exit verification script\n")


def main():
    sensor, radar, ir = init_hardware()
    print_help()

    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)

    last_sensor_read = time.ticks_ms()
    last_radar_print = time.ticks_ms()
    last_auto_tx     = time.ticks_ms()
    auto_cycle_level = 1
    auto_tx_enabled  = False

    current_temp = None
    current_rh = None

    if sensor.connected:
        current_temp, current_rh = sensor.read_both()

    while True:
        now = time.ticks_ms()

        # Update radar tracking
        targets = radar.update()
        if targets is not None and time.ticks_diff(now, last_radar_print) >= 500:
            last_radar_print = now
            if len(targets) == 0:
                print(f"[RADAR] No target in zone | Total Frames: {radar.total_frames_received}")
            else:
                for tgt in targets:
                    print(f"[RADAR] Target #{tgt['id']}: Dist={tgt['dist_m']}m (X={tgt['x_mm']}mm, Y={tgt['y_mm']}mm) | Speed={tgt['speed_cms']} cm/s")

        # Periodic sensor update (every 2.0s)
        if time.ticks_diff(now, last_sensor_read) >= 2000:
            last_sensor_read = now
            if sensor.connected:
                t, h = sensor.read_both()
                if t is not None:
                    current_temp, current_rh = t, h
                    print(f"[ENV SENSOR] Temperature: {current_temp:.1f} °C  |  Relative Humidity: {current_rh:.1f} %")
            elif sensor.probe():
                print("[ENV SENSOR] HW-220 dynamically detected on I2C bus!")

        # Auto-transmit cycle if enabled
        if auto_tx_enabled and time.ticks_diff(now, last_auto_tx) >= 3500:
            last_auto_tx = now
            pkt = ir.send_level(auto_cycle_level)
            print(f"[AUTO TX] Sent LEVEL {auto_cycle_level} -> 0x{pkt:08X}")
            auto_cycle_level += 1
            if auto_cycle_level > 6:
                auto_cycle_level = 1

        # Check for serial user command
        if poller.poll(0):
            ch = sys.stdin.read(1)
            if not ch:
                continue

            if ch in ('1', '2', '3', '4', '5', '6'):
                lvl = int(ch)
                pkt = ir.send_level(lvl)
                print(f"[IR TX -> FAN] Sent LEVEL {lvl} (Packet: 0x{pkt:08X})")

            elif ch == '0':
                pkt = ir.send_all_off()
                print(f"[IR TX -> FAN] Sent FAN ALL OFF (Packet: 0x{pkt:08X})")

            elif ch in ('p', 'P'):
                pkt = ir.send_toggle_power()
                print(f"[IR TX -> FAN] Sent TOGGLE POWER (Packet: 0x{pkt:08X})")

            elif ch in ('o', 'O'):
                pkt = ir.send_power_on()
                print(f"[IR TX -> FAN] Sent POWER ON (Packet: 0x{pkt:08X})")

            elif ch in ('a', 'A'):
                auto_tx_enabled = not auto_tx_enabled
                status_str = "ENABLED (Cycling Levels 1-6 every 3.5s)" if auto_tx_enabled else "DISABLED"
                print(f"[IR TX] AUTO-TRANSMIT Mode: {status_str}")

            elif ch in ('b', 'B'):
                print("[IR TX] Emitting 500ms 38 kHz burst on GP16 (look at LED via phone camera)...")
                ir.burst_carrier_test(500)

            elif ch in ('s', 'S'):
                if sensor.connected:
                    t, h = sensor.read_both()
                    print(f"[ENV SENSOR] Manual poll -> T: {t} °C, RH: {h} %")
                else:
                    print("[ENV SENSOR] HW-220 not connected.")

            elif ch in ('h', 'H', '?'):
                print_help()

            elif ch in ('q', 'Q'):
                print("\nExiting hardware verification loop. Goodbye!")
                break

        time.sleep_ms(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Ctrl+C] Stopped by user.")
