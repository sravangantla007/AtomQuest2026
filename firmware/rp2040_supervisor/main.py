"""
===============================================================================
 NodeVAYU / Atomberg Fan Mod: RP2040 MicroPython IR & Sensor Testbench
 Repository: https://github.com/sravangantla007/AtomQuest2026
 Hardware: Raspberry Pi Pico (RP2040)
 
 Pinout Configuration:
 -----------------------------------------------------------------------------
  - IR LED Transmitter : GPIO 16 (Physical Pin 21) -> 38.0 kHz Hardware PWM
  - HW-220 (HTU21D)    : I2C1 (GP26 SDA [Pin 31], GP27 SCL [Pin 32] @ 100 kHz)
  - HLK-LD2450 Radar   : UART0 (GP28 TX [Pin 34], GP29 RX [Pin 35] @ 256,000 baud)
  - Onboard Activity LED: GP25 (Standard Pico) or 'LED' (Pico W)

 Safety & Control Policy:
 -----------------------------------------------------------------------------
  - ZERO autonomous IR transmissions. All IR transmissions require explicit
    user input via the Thonny interactive shell prompt.
===============================================================================
"""

import time
import math
import sys
from machine import Pin, PWM, I2C, UART

# =============================================================================
# PIN DEFINITIONS
# =============================================================================
PIN_IR_TX    = 16   # Physical Pin 21
PIN_I2C_SDA  = 26   # Physical Pin 31
PIN_I2C_SCL  = 27   # Physical Pin 32
PIN_RADAR_TX = 28   # Physical Pin 34 (Pico TX -> LD2450 RX)
PIN_RADAR_RX = 29   # Physical Pin 35 (Pico RX <- LD2450 TX)

# Onboard LED
try:
    led = Pin("LED", Pin.OUT)
except Exception:
    try:
        led = Pin(25, Pin.OUT)
    except Exception:
        led = None


def blink_activity(ms=30):
    if led:
        led.value(1)
        time.sleep_ms(ms)
        led.value(0)


# =============================================================================
# 1. ATOMBERG IR TRANSMITTER (38.0 kHz PWM, 32-bit Pulse-Distance Protocol)
# =============================================================================
class AtombergIRTransmitter:
    """
    Precision IR Transmitter for Atomberg BLDC Fans.
    Protocol: Custom 32-bit Pulse-Distance Modulation
      - Carrier: 38.0 kHz (33% or 50% duty cycle)
      - Leader: 9000us mark + 4500us space
      - Bit 0 : 560us mark  + 560us space
      - Bit 1 : 560us mark  + 1690us space
      - Stop  : 560us mark
      - Address: 0xAF
      - Checksum: (ADDR ^ CMD ^ DATA_H_LOW ^ DATA_L) & 0x0F
    """
    FAN_IR_ADDR = 0xAF

    # Commands
    CMD_ALL_OFF        = 0x00
    CMD_SET_RAW_SPEED  = 0x01
    CMD_SET_LEVEL      = 0x02
    CMD_TOGGLE_POWER   = 0x03
    CMD_SET_LIGHT_MODE = 0x04
    CMD_STEP_UP        = 0x05
    CMD_STEP_DOWN      = 0x06
    CMD_POWER_ON       = 0x07

    LEVEL_MAP = {
        0: (0,   "OFF / Coasting down"),
        1: (200, "Level 1: Gentle Breeze (39% PWM, ~650 RPM)"),
        2: (265, "Level 2: Low-Medium (52% PWM, ~900 RPM)"),
        3: (330, "Level 3: Medium (65% PWM, ~1050 RPM)"),
        4: (395, "Level 4: Medium-High (77% PWM, ~1200 RPM)"),
        5: (455, "Level 5: High (89% PWM, ~1350 RPM)"),
        6: (511, "Level 6: Turbo Full Throttle (100% PWM, ~1450 RPM)")
    }

    LIGHT_MODES = {
        0: "Debug Diagnostic HUD (Vbus, I2C, Steady, Fault, OC)",
        1: "Speed Level Gauge (1..6 LEDs mapped from speed)",
        2: "Dark / Stealth Mode (LEDs off in steady state)",
        3: "Night Light Full (All 6 LEDs ON)",
        4: "Night Light Half (3 LEDs in triangle pattern)",
        5: "Single LED Beacon (Only LED 1 ON)"
    }

    def __init__(self, pin_num: int = PIN_IR_TX):
        self.pin_num = pin_num
        self.duty_percent = 33
        self.repeats = 1
        self._init_pwm()

    def _init_pwm(self):
        self.pwm = PWM(Pin(self.pin_num))
        self.pwm.freq(38000)
        self.set_duty(self.duty_percent)
        self.carrier_off()

    def set_duty(self, pct: int):
        self.duty_percent = max(5, min(95, pct))
        self.duty_u16_val = int(65535 * (self.duty_percent / 100.0))

    def carrier_on(self):
        self.pwm.duty_u16(self.duty_u16_val)

    def carrier_off(self):
        self.pwm.duty_u16(0)

    def set_dc(self, level: int):
        """De-init PWM and set direct DC logic level (0 or 1) for hardware debugging."""
        try:
            self.pwm.deinit()
        except Exception:
            pass
        p = Pin(self.pin_num, Pin.OUT)
        p.value(1 if level else 0)
        # Re-initialize PWM reference
        if not level:
            self._init_pwm()

    def burst_carrier(self, seconds: float = 3.0):
        """Turn on continuous 38 kHz carrier for phone camera visibility test."""
        self._init_pwm()
        print(f"[IR DIAG] Emitting continuous 38.0 kHz carrier ({self.duty_percent}% duty) for {seconds}s...")
        print("          Point your phone camera (or front selfie camera) at the IR LED now.")
        if led:
            led.value(1)
        self.carrier_on()
        t_end = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
        while time.ticks_diff(t_end, time.ticks_ms()) > 0:
            time.sleep_ms(20)
        self.carrier_off()
        if led:
            led.value(0)
        print("[IR DIAG] Carrier test finished.")

    def encode_packet(self, cmd: int, data_l: int, data_h_low: int = 0) -> int:
        addr = self.FAN_IR_ADDR
        checksum = (addr ^ cmd ^ data_h_low ^ data_l) & 0x0F
        data_h = (checksum << 4) | (data_h_low & 0x0F)
        return (addr << 24) | (cmd << 16) | (data_h << 8) | (data_l & 0xFF)

    @micropython.native
    def _send_single_frame(self, packet: int, duty: int):
        # 1. Leader (9000us mark, 4500us space)
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

        # 3. Stop bit (560us mark)
        self.pwm.duty_u16(duty)
        time.sleep_us(560)
        self.pwm.duty_u16(0)

    def transmit(self, packet: int, description: str = ""):
        self._init_pwm()
        print(f"[IR TX] 0x{packet:08X} ({description}) | Duty: {self.duty_percent}% | Repeats: {self.repeats}")
        blink_activity(40)
        for r in range(self.repeats):
            self._send_single_frame(packet, self.duty_u16_val)
            if r < self.repeats - 1:
                time.sleep_ms(40)
        time.sleep_ms(15)

    def cmd_level(self, level: int):
        if not (0 <= level <= 6):
            print(f"[Error] Invalid level {level}. Allowed: 0 to 6.")
            return
        if level == 0:
            self.cmd_off()
            return
        counts, desc = self.LEVEL_MAP[level]
        pkt = self.encode_packet(self.CMD_SET_LEVEL, data_l=level)
        self.transmit(pkt, f"Level {level} -> {desc}")

    def cmd_off(self):
        pkt = self.encode_packet(self.CMD_ALL_OFF, data_l=0x00)
        self.transmit(pkt, "FAN ALL OFF (Gentle coast)")

    def cmd_on(self):
        pkt = self.encode_packet(self.CMD_POWER_ON, data_l=0x00)
        self.transmit(pkt, "POWER ON (Restore last running speed)")

    def cmd_toggle(self):
        pkt = self.encode_packet(self.CMD_TOGGLE_POWER, data_l=0x00)
        self.transmit(pkt, "TOGGLE POWER (ON/OFF)")

    def cmd_step_up(self):
        pkt = self.encode_packet(self.CMD_STEP_UP, data_l=0x00)
        self.transmit(pkt, "STEP UP (+1 speed level)")

    def cmd_step_down(self):
        pkt = self.encode_packet(self.CMD_STEP_DOWN, data_l=0x00)
        self.transmit(pkt, "STEP DOWN (-1 speed level)")

    def cmd_speed(self, speed_9bit: int):
        speed_9bit = max(0, min(511, speed_9bit))
        data_l = speed_9bit & 0xFF
        data_h_low = (speed_9bit >> 8) & 0x01
        pkt = self.encode_packet(self.CMD_SET_RAW_SPEED, data_l=data_l, data_h_low=data_h_low)
        pct = (speed_9bit * 100) // 511
        self.transmit(pkt, f"Raw Speed {speed_9bit}/511 ({pct}%)")

    def cmd_light_mode(self, mode: int):
        if not (0 <= mode <= 5):
            print(f"[Error] Invalid mode {mode}. Choose 0 to 5:")
            for k, v in self.LIGHT_MODES.items():
                print(f"  {k}: {v}")
            return
        pkt = self.encode_packet(self.CMD_SET_LIGHT_MODE, data_l=mode)
        self.transmit(pkt, f"Mode {mode} -> {self.LIGHT_MODES[mode]}")


# =============================================================================
# 2. HW-220 / HTU21D DIGITAL TEMPERATURE & HUMIDITY DRIVER
# =============================================================================
class HW220_HTU21D:
    I2C_ADDR = 0x40
    CMD_TRIGGER_TEMP = 0xF3
    CMD_TRIGGER_HUMID = 0xF5
    CMD_SOFT_RESET = 0xFE

    def __init__(self, i2c: I2C):
        self.i2c = i2c
        self.connected = False

    def probe(self) -> bool:
        try:
            devs = self.i2c.scan()
            self.connected = (self.I2C_ADDR in devs)
            return self.connected
        except Exception:
            self.connected = False
            return False

    def soft_reset(self):
        try:
            self.i2c.writeto(self.I2C_ADDR, bytes([self.CMD_SOFT_RESET]))
            time.sleep_ms(20)
        except Exception:
            pass

    def read_temperature(self):
        try:
            self.i2c.writeto(self.I2C_ADDR, bytes([self.CMD_TRIGGER_TEMP]))
            time.sleep_ms(55)
            data = self.i2c.readfrom(self.I2C_ADDR, 3)
            raw = (data[0] << 8) | (data[1] & 0xFC)
            return round(-46.85 + (175.72 * raw / 65536.0), 2)
        except Exception:
            return None

    def read_humidity(self):
        try:
            self.i2c.writeto(self.I2C_ADDR, bytes([self.CMD_TRIGGER_HUMID]))
            time.sleep_ms(35)
            data = self.i2c.readfrom(self.I2C_ADDR, 3)
            raw = (data[0] << 8) | (data[1] & 0xF0)
            rh = -6.0 + (125.0 * raw / 65536.0)
            return round(max(0.0, min(100.0, rh)), 2)
        except Exception:
            return None

    def read_both(self):
        t = self.read_temperature()
        rh = self.read_humidity()
        return t, rh


# =============================================================================
# 3. HLK-LD2450 24 GHz mmWave RADAR PARSER
# =============================================================================
class LD2450Radar:
    FRAME_HEADER = b'\xAA\xFF\x03\x00'
    FRAME_TAIL   = b'\x55\xCC'
    FRAME_LEN    = 30

    def __init__(self, uart: UART):
        self.uart = uart
        self.buffer = bytearray()
        self.total_frames = 0

    @staticmethod
    def _decode_signed_16(b_low: int, b_high: int) -> int:
        val = b_low | ((b_high & 0x7F) << 8)
        if b_high & 0x80:
            val = -val
        return val

    def read_targets(self):
        if self.uart.any():
            incoming = self.uart.read()
            if incoming:
                self.buffer.extend(incoming)

        if len(self.buffer) > 256:
            self.buffer = self.buffer[-128:]

        latest = None
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

            self.total_frames += 1
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
                        "dist_m": round(dist_mm / 1000.0, 2),
                        "speed_cms": spd,
                        "res_mm": res
                    })
            latest = targets
        return latest


# =============================================================================
# BANNER & HELP
# =============================================================================
def print_banner(sensor_ok: bool, radar_ok: bool, tx_pin: int):
    print("=" * 74)
    print("  NodeVAYU / Atomberg Fan Controller Testbench (RP2040 MicroPython)")
    print("  Repository: https://github.com/sravangantla007/AtomQuest2026")
    print("=" * 74)
    print(f"  [HARDWARE STATUS]")
    print(f"    - IR Transmitter : GP{tx_pin} (Pin 21) | 38.0 kHz PWM | SAFE (STANDBY)")
    print(f"    - HW-220 I2C1    : GP26(SDA), GP27(SCL) -> {'[ONLINE @ 0x40]' if sensor_ok else '[NOT DETECTED]'}")
    print(f"    - LD2450 Radar   : GP28(TX), GP29(RX)   -> {'[STREAMING]' if radar_ok else '[INITIALIZED]'}")
    print("=" * 74)
    print("  SAFETY POLICY: No IR signals are transmitted without your explicit input.")
    print("=" * 74)
    print("  FAN COMMANDS:")
    print("    1 .. 6           : Set Speed Level (1:Gentle, 2:Low, 3:Med, 4:Med-Hi, 5:Hi, 6:Turbo)")
    print("    0, off, stop     : Turn fan OFF (coasting down to stop)")
    print("    on, start        : Power ON (restore last active speed)")
    print("    toggle, p, power : Toggle Power ON / OFF")
    print("    +, up            : Increment speed (+1)")
    print("    -, down          : Decrement speed (-1)")
    print("    speed <0-511>    : Raw DRV10983 9-bit speed (e.g. 'speed 330')")
    print("    mode <0-5>       : Switch light mode (0:Debug, 1:Gauge, 2:Dark, 3:Night, 4:Half, 5:Beacon)")
    print("=" * 74)
    print("  IR HARDWARE DIAGNOSTICS:")
    print("    carrier [sec]    : Emit 38kHz PWM burst for N sec (default 3s) for phone camera test")
    print("    dc high          : Set GP16 steady HIGH (+3.3V) for multimeter/LED polarity test")
    print("    dc low           : Set GP16 steady LOW (0V)")
    print("    duty <33|50>     : Switch carrier duty cycle (default: 33%)")
    print("    repeat <1-5>     : Set packet repeat count per transmission (default: 1)")
    print("=" * 74)
    print("  ENVIRONMENT & RADAR TELEMETRY:")
    print("    sensor           : Read live HW-220 temperature & relative humidity")
    print("    radar            : Poll HLK-LD2450 radar for target distance & speed")
    print("    scan             : Re-scan I2C bus for devices")
    print("    help, ?          : Re-print this menu")
    print("=" * 74 + "\n")


# =============================================================================
# MAIN INTERACTIVE SHELL
# =============================================================================
def main():
    # 1. Initialize I2C1
    i2c = I2C(1, sda=Pin(PIN_I2C_SDA), scl=Pin(PIN_I2C_SCL), freq=100000)
    sensor = HW220_HTU21D(i2c)
    sensor_ok = sensor.probe()
    if sensor_ok:
        sensor.soft_reset()

    # 2. Initialize UART0 for LD2450
    try:
        uart0 = UART(0, baudrate=256000, tx=Pin(PIN_RADAR_TX), rx=Pin(PIN_RADAR_RX), rxbuf=512)
    except TypeError:
        uart0 = UART(0, baudrate=256000, tx=Pin(PIN_RADAR_TX), rx=Pin(PIN_RADAR_RX))
    radar = LD2450Radar(uart0)

    # Listen for radar for 0.5s to check connectivity
    t_start = time.ticks_ms()
    radar_ok = False
    while time.ticks_diff(time.ticks_ms(), t_start) < 500:
        if uart0.any():
            radar_ok = True
            break
        time.sleep_ms(20)

    # 3. Initialize IR Transmitter
    ir = AtombergIRTransmitter(PIN_IR_TX)

    # Print Clean Banner
    print_banner(sensor_ok, radar_ok, PIN_IR_TX)

    # Interactive Loop (Thonny Shell input)
    while True:
        try:
            line = input("fan> ").strip().lower()
            if not line:
                continue

            # --- Discrete Levels ---
            if line in ("0", "1", "2", "3", "4", "5", "6"):
                ir.cmd_level(int(line))

            elif line.startswith("level "):
                try:
                    ir.cmd_level(int(line.split()[1]))
                except ValueError:
                    print("Usage: level <0-6>")

            # --- Raw Speed ---
            elif line.startswith("speed "):
                try:
                    val = int(line.split()[1])
                    ir.cmd_speed(val)
                except ValueError:
                    print("Usage: speed <0-511>")

            # --- Power Commands ---
            elif line in ("on", "start"):
                ir.cmd_on()

            elif line in ("off", "stop"):
                ir.cmd_off()

            elif line in ("p", "power", "toggle"):
                ir.cmd_toggle()

            elif line in ("+", "up"):
                ir.cmd_step_up()

            elif line in ("-", "down"):
                ir.cmd_step_down()

            # --- Lighting Modes ---
            elif line.startswith("mode"):
                parts = line.split()
                if len(parts) == 1:
                    print("Lighting Modes:")
                    for k, v in ir.LIGHT_MODES.items():
                        print(f"  mode {k}: {v}")
                else:
                    try:
                        m = int(parts[1])
                        ir.cmd_light_mode(m)
                    except ValueError:
                        print("Usage: mode <0-5>")

            # --- Diagnostics: Carrier Burst for Phone Camera ---
            elif line.startswith("carrier") or line.startswith("burst"):
                parts = line.split()
                secs = 3.0
                if len(parts) > 1:
                    try:
                        secs = float(parts[1])
                    except ValueError:
                        secs = 3.0
                ir.burst_carrier(secs)

            # --- Diagnostics: Steady DC Level ---
            elif line in ("dc high", "high", "dc 1", "3v3"):
                print(f"[IR DIAG] Setting GP{PIN_IR_TX} steady HIGH (+3.3V). Check with voltmeter or standard LED.")
                ir.set_dc(1)

            elif line in ("dc low", "low", "dc 0", "0v"):
                print(f"[IR DIAG] Setting GP{PIN_IR_TX} steady LOW (0V).")
                ir.set_dc(0)

            # --- Duty Cycle Config ---
            elif line.startswith("duty"):
                parts = line.split()
                if len(parts) > 1:
                    try:
                        val = int(parts[1])
                        ir.set_duty(val)
                        print(f"[CONFIG] Carrier duty cycle set to {ir.duty_percent}% (duty_u16 = {ir.duty_u16_val})")
                    except ValueError:
                        print("Usage: duty <33|50|pct>")
                else:
                    print(f"Current duty cycle: {ir.duty_percent}%")

            # --- Frame Repeat Config ---
            elif line.startswith("repeat"):
                parts = line.split()
                if len(parts) > 1:
                    try:
                        r = int(parts[1])
                        ir.repeats = max(1, min(10, r))
                        print(f"[CONFIG] Repeat count set to {ir.repeats}")
                    except ValueError:
                        print("Usage: repeat <1-5>")
                else:
                    print(f"Current repeats: {ir.repeats}")

            # --- Telemetry: HW-220 Temperature & Humidity ---
            elif line in ("sensor", "temp", "env"):
                if sensor.probe():
                    t, rh = sensor.read_both()
                    print(f"[ENV SENSOR] Temperature: {t} °C | Relative Humidity: {rh} %")
                else:
                    print("[ENV SENSOR] HW-220 sensor not responding at I2C address 0x40.")

            # --- Telemetry: LD2450 Radar ---
            elif line in ("radar", "radar read", "targets"):
                t_end = time.ticks_add(time.ticks_ms(), 500)
                tgts = None
                while time.ticks_diff(t_end, time.ticks_ms()) > 0:
                    res = radar.read_targets()
                    if res is not None:
                        tgts = res
                    time.sleep_ms(20)

                if tgts is not None:
                    if len(tgts) == 0:
                        print(f"[RADAR] Zone clear (0 targets detected) | Total frames parsed: {radar.total_frames}")
                    else:
                        print(f"[RADAR] Detected {len(tgts)} target(s):")
                        for t in tgts:
                            print(f"  Target #{t['id']}: Dist={t['dist_m']}m (X={t['x_mm']}mm, Y={t['y_mm']}mm) | Speed={t['speed_cms']} cm/s")
                else:
                    print(f"[RADAR] No new tracking frame in last 500ms (Total frames received: {radar.total_frames}).")

            # --- I2C Bus Scan ---
            elif line in ("scan", "i2c"):
                devs = i2c.scan()
                print(f"[I2C BUS] Found {len(devs)} device(s): {[hex(d) for d in devs]}")
                if 0x40 in devs:
                    print("          -> HW-220 / HTU21D present at 0x40")

            # --- Help ---
            elif line in ("help", "h", "?"):
                print_banner(sensor.probe(), radar_ok, PIN_IR_TX)

            else:
                print(f"Unknown command: '{line}'. Type 'help' for command list.")

        except (KeyboardInterrupt, EOFError):
            print("\n[Exiting interactive loop]")
            break
        except Exception as e:
            print(f"[Error] {e}")


if __name__ == "__main__":
    main()
