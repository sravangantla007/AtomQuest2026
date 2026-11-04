"""
===============================================================================
 NodeVAYU — RP2040 Climate Intelligence Supervisor & IR Remote Core
 Project: atomQUEST 2026 | Track 1: Air, Reimagined for the Next Decade
 Repository: https://github.com/sravangantla007/AtomQuest2026

 Hardware Pinout Specification:
 -----------------------------------------------------------------------------
  - IR Transmitter LED : GPIO 16 (Physical Pin 21) -> 38.0 kHz Hardware PWM
  - HW-220 / HTU21D    : I2C1 (GP26 SDA [Pin 31], GP27 SCL [Pin 32] @ 100 kHz)
  - HLK-LD2450 Radar   : UART0 (GP28 TX [Pin 34], GP29 RX [Pin 35] @ 256,000 baud)
  - Onboard Status LED : GP25 (Pico) or 'LED' (Pico W)

 Protocol: Atomberg 32-Bit NEC Variant, MSB First (Address: 0xAF)
 Transmission Mode: Single Sequence per Press (50% Duty Cycle: 32768)
 Interface: Interactive Thonny Shell & USB Serial JSON Bridge
===============================================================================
"""

import time
import math
import sys
import select

if not hasattr(time, "sleep_us"):
    time.sleep_us = lambda us: time.sleep(us / 1_000_000.0)
if not hasattr(time, "sleep_ms"):
    time.sleep_ms = lambda ms: time.sleep(ms / 1_000.0)

try:
    from machine import Pin, PWM, I2C, UART
    IS_MICROPYTHON = True
except ImportError:
    IS_MICROPYTHON = False
    print("[INFO] Running in host Python environment.")

# =============================================================================
# 1. HARDWARE PIN CONFIGURATION
# =============================================================================
IR_PIN      = 16   # Physical Pin 21 (38 kHz Carrier PWM)
I2C1_SDA    = 26   # Physical Pin 31 (HW-220 SDA)
I2C1_SCL    = 27   # Physical Pin 32 (HW-220 SCL)
RADAR_TX    = 28   # Physical Pin 34 (Pico TX -> LD2450 RX)
RADAR_RX    = 29   # Physical Pin 35 (Pico RX <- LD2450 TX)

IR_ADDRESS  = 0xAF
DUTY_50     = 32768  # 50% duty cycle: delivers full optical punch to TSOP sensor

# 38 kHz IR Carrier Setup
if IS_MICROPYTHON:
    ir = PWM(Pin(IR_PIN))
    ir.freq(38000)
    ir.duty_u16(0)
else:
    ir = None

# Onboard Status LED
led = None
if IS_MICROPYTHON:
    for candidate in ("LED", 25):
        try:
            led = Pin(candidate, Pin.OUT)
            break
        except Exception:
            pass


# =============================================================================
# 2. COMMAND SET (EXACT MATCH WITH STM8 src/main.c)
# =============================================================================
CMD_ALL_OFF        = 0x00
CMD_SET_RAW_SPEED  = 0x01
CMD_SET_LEVEL      = 0x02
CMD_TOGGLE_POWER   = 0x03
CMD_SET_LIGHT_MODE = 0x04
CMD_STEP_UP        = 0x05
CMD_STEP_DOWN      = 0x06
CMD_POWER_ON       = 0x07

# Calibrated Fan Levels (Matching STM8 PRESET_SPEEDS)
LEVEL_INFO = {
    0: {"counts": 0,   "rpm": 0,    "name": "OFF / Coasting"},
    1: {"counts": 200, "rpm": 650,  "name": "Level 1: Gentle Breeze (~650 RPM)"},
    2: {"counts": 265, "rpm": 900,  "name": "Level 2: Low-Medium (~900 RPM)"},
    3: {"counts": 330, "rpm": 1050, "name": "Level 3: Medium (~1050 RPM)"},
    4: {"counts": 395, "rpm": 1200, "name": "Level 4: Medium-High (~1200 RPM)"},
    5: {"counts": 455, "rpm": 1350, "name": "Level 5: High (~1350 RPM)"},
    6: {"counts": 511, "rpm": 1450, "name": "Level 6: Turbo Full Throttle (100% PWM, ~1450 RPM)"}
}

LIGHT_MODES = {
    0: "Debug Diagnostic HUD",
    1: "Speed Level Gauge (1..6 LEDs; Default)",
    2: "Stealth Dark Mode (LEDs off in steady state)",
    3: "Night Light Full (All 6 LEDs ON steady)",
    4: "Night Light Triangle (LEDs 1, 3, 5 ON steady)",
    5: "Single Subtle LED Beacon (Only LED 1 ON steady)"
}

# Runtime Fan State
fan_state = {
    "power": False,
    "level": 0,
    "counts": 0,
    "rpm": 0,
    "mode": 1,
    "last_packet": "0xAF00F000",
    "last_crc": 15
}


# =============================================================================
# 3. LOW LEVEL IR FUNCTIONS (50% DUTY, CLEAN TRANSITIONS)
# =============================================================================
def ir_mark(duration_us):
    """38 kHz carrier ON at 50% duty cycle."""
    if ir:
        ir.duty_u16(DUTY_50)
        time.sleep_us(duration_us)
        ir.duty_u16(0)


def ir_space(duration_us):
    """Carrier OFF (dark)."""
    if ir:
        ir.duty_u16(0)
    time.sleep_us(duration_us)


# =============================================================================
# 4. CUSTOM FRAME ENCODER & TRANSMITTER
#
# Frame Format (32 bits, MSB first):
#   [ADDRESS:8] [COMMAND:8] [DATA_H:8] [DATA_L:8]
#
#   DATA_H:
#     Bits 7..4 = CRC-4: (ADDR ^ CMD ^ DATA_H_LOW ^ DATA_L) & 0x0F
#     Bits 3..0 = DATA_H_LOW (Bit 8 of 9-bit speed or 0)
# =============================================================================
def make_frame(command, data_h=0, data_l=0):
    data_h_low = data_h & 0x0F
    crc = (IR_ADDRESS ^ command ^ data_h_low ^ data_l) & 0x0F
    data_h_val = (crc << 4) | data_h_low

    frame = (
        (IR_ADDRESS << 24) |
        (command << 16) |
        (data_h_val << 8) |
        (data_l & 0xFF)
    )
    return frame, crc


def transmit_frame(frame):
    """Transmits strictly 1 single NEC frame with clean 40ms post-cooldown."""
    if led:
        led.value(1)

    # 1. Leader: 9ms mark + 4.5ms space
    ir_mark(9000)
    ir_space(4500)

    # 2. 32 bits, MSB first
    for i in range(31, -1, -1):
        bit = (frame >> i) & 1
        ir_mark(560)
        if bit:
            ir_space(1690)
        else:
            ir_space(560)

    # 3. Stop mark: 560us
    ir_mark(560)
    if ir:
        ir.duty_u16(0)

    if led:
        led.value(0)

    # Mandatory cooldown for TSOP receiver AGC settle
    time.sleep_ms(40)


def send_command(command, data_h=0, data_l=0, description=""):
    frame, crc = make_frame(command, data_h, data_l)
    fan_state["last_packet"] = "0x{:08X}".format(frame)
    fan_state["last_crc"] = crc

    print("[TX] 0x{:08X} | CMD:0x{:02X} CRC:0x{:X} DATA:0x{:02X}{:02X} {}".format(
        frame, command, crc, (frame >> 8) & 0xFF, frame & 0xFF, description
    ))
    transmit_frame(frame)
    return frame, crc


# =============================================================================
# 5. HIGH LEVEL FAN CONTROLS
# =============================================================================
def fan_off():
    fan_state["power"] = False
    fan_state["level"] = 0
    fan_state["counts"] = 0
    fan_state["rpm"] = 0
    return send_command(CMD_ALL_OFF, 0, 0, "-> Power OFF (Coasting down)")


def fan_on():
    fan_state["power"] = True
    if fan_state["level"] == 0:
        fan_state["level"] = 3
        fan_state["counts"] = 330
        fan_state["rpm"] = 1050
    return send_command(CMD_POWER_ON, 0, 0, "-> Power ON (Restore last state)")


def fan_toggle():
    fan_state["power"] = not fan_state["power"]
    if fan_state["power"]:
        if fan_state["level"] == 0:
            fan_state["level"] = 3
            fan_state["counts"] = 330
            fan_state["rpm"] = 1050
    else:
        fan_state["level"] = 0
        fan_state["counts"] = 0
        fan_state["rpm"] = 0
    return send_command(CMD_TOGGLE_POWER, 0, 0, "-> Power Toggle (ON/OFF)")


def set_level(level):
    if not (1 <= level <= 6):
        print("ERROR: Level must be between 1 and 6")
        return None, None
    info = LEVEL_INFO[level]
    fan_state["power"] = True
    fan_state["level"] = level
    fan_state["counts"] = info["counts"]
    fan_state["rpm"] = info["rpm"]
    return send_command(CMD_SET_LEVEL, 0, level, "-> Set Level {} (~{} RPM)".format(level, info['rpm']))


def speed_up():
    if fan_state["level"] < 6:
        new_lvl = max(1, fan_state["level"] + 1)
        fan_state["level"] = new_lvl
        fan_state["power"] = True
        fan_state["counts"] = LEVEL_INFO[new_lvl]["counts"]
        fan_state["rpm"] = LEVEL_INFO[new_lvl]["rpm"]
    return send_command(CMD_STEP_UP, 0, 0, "-> Step UP (+1)")


def speed_down():
    if fan_state["level"] > 1:
        new_lvl = fan_state["level"] - 1
        fan_state["level"] = new_lvl
        fan_state["counts"] = LEVEL_INFO[new_lvl]["counts"]
        fan_state["rpm"] = LEVEL_INFO[new_lvl]["rpm"]
    return send_command(CMD_STEP_DOWN, 0, 0, "-> Step DOWN (-1)")


def set_raw_speed(speed):
    speed = max(0, min(511, int(speed)))
    data_h = (speed >> 8) & 0x01
    data_l = speed & 0xFF
    pct = (speed * 100) // 511
    fan_state["power"] = (speed > 0)
    fan_state["counts"] = speed
    fan_state["rpm"] = round((speed * 1450) / 511)
    return send_command(CMD_SET_RAW_SPEED, data_h, data_l, "-> Raw Speed {}/511 ({}%)".format(speed, pct))


def set_light_mode(mode):
    if not (0 <= mode <= 5):
        print("ERROR: Mode must be 0 to 5")
        return None, None
    fan_state["mode"] = mode
    return send_command(CMD_SET_LIGHT_MODE, 0, mode, "-> Mode {} ({})".format(mode, LIGHT_MODES[mode]))


def test_carrier(seconds=3.0):
    """Continuous carrier for smartphone camera check."""
    print("[TEST] Emitting 38 kHz carrier (50% duty) for {}s...".format(seconds))
    print("       Point smartphone camera at the IR LED now.")
    if led:
        led.value(1)
    if ir:
        ir.duty_u16(DUTY_50)
    t_end = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        time.sleep_ms(20)
    if ir:
        ir.duty_u16(0)
    if led:
        led.value(0)
    print("[TEST] Done.")


# =============================================================================
# 6. HW-220 / HTU21D DIGITAL TEMPERATURE & HUMIDITY DRIVER
# =============================================================================
class HW220_Sensor:
    I2C_ADDR = 0x40
    CMD_TRIGGER_TEMP = 0xF3
    CMD_TRIGGER_HUMID = 0xF5
    CMD_SOFT_RESET = 0xFE

    def __init__(self, i2c_bus):
        self.i2c = i2c_bus
        self.connected = False
        self.temp_c = 28.5
        self.rh_pct = 58.0
        self.heat_index_c = 30.2
        self.dew_point_c = 19.4
        self.last_read = 0

    def probe(self):
        if not self.i2c:
            return False
        try:
            devs = self.i2c.scan()
            self.connected = (self.I2C_ADDR in devs)
            return self.connected
        except Exception:
            self.connected = False
            return False

    def read_all(self):
        if not self.i2c:
            return self.temp_c, self.rh_pct

        try:
            # Read Temp
            self.i2c.writeto(self.I2C_ADDR, bytes([self.CMD_TRIGGER_TEMP]))
            time.sleep_ms(55)
            data = self.i2c.readfrom(self.I2C_ADDR, 3)
            raw_t = (data[0] << 8) | (data[1] & 0xFC)
            self.temp_c = round(-46.85 + (175.72 * raw_t / 65536.0), 2)

            # Read Humidity
            self.i2c.writeto(self.I2C_ADDR, bytes([self.CMD_TRIGGER_HUMID]))
            time.sleep_ms(30)
            data = self.i2c.readfrom(self.I2C_ADDR, 3)
            raw_h = (data[0] << 8) | (data[1] & 0xF0)
            rh = -6.0 + (125.0 * raw_h / 65536.0)
            self.rh_pct = round(max(0.0, min(100.0, rh)), 2)

            # Compute Heat Index & Dew Point
            t = self.temp_c
            h = self.rh_pct
            self.heat_index_c = round(t + 0.33 * (h / 100.0 * 6.105 * math.exp(17.27 * t / (237.7 + t))) - 4.0, 1)
            b = 17.625
            c = 243.04
            gamma = (b * t / (c + t)) + math.log(max(0.01, h / 100.0))
            self.dew_point_c = round((c * gamma) / (b - gamma), 1)
            self.connected = True
        except Exception:
            pass

        return self.temp_c, self.rh_pct


# =============================================================================
# 7. HLK-LD2450 24 GHz mmWave RADAR PARSER
# =============================================================================
class LD2450_Radar:
    FRAME_HEADER = b'\xAA\xFF\x03\x00'
    FRAME_TAIL   = b'\x55\xCC'
    FRAME_LEN    = 30

    def __init__(self, uart_bus):
        self.uart = uart_bus
        self.buffer = bytearray()
        self.targets = []
        self.total_frames = 0
        self.connected = False

    @staticmethod
    def _decode_signed_16(b_low, b_high):
        val = b_low | ((b_high & 0x7F) << 8)
        if b_high & 0x80:
            val = -val
        return val

    def update(self):
        if not self.uart or not self.uart.any():
            return self.targets

        incoming = self.uart.read()
        if incoming:
            self.buffer.extend(incoming)

        if len(self.buffer) > 256:
            self.buffer = self.buffer[-128:]

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
            self.connected = True
            new_targets = []

            for t_idx in range(3):
                offset = 4 + (t_idx * 8)
                x = self._decode_signed_16(frame[offset], frame[offset + 1])
                y = self._decode_signed_16(frame[offset + 2], frame[offset + 3])
                spd = self._decode_signed_16(frame[offset + 4], frame[offset + 5])
                res = frame[offset + 6] | (frame[offset + 7] << 8)

                if x != 0 or y != 0:
                    dist_mm = math.sqrt(x * x + y * y)
                    new_targets.append({
                        "id": t_idx + 1,
                        "x_mm": x,
                        "y_mm": y,
                        "dist_m": round(dist_mm / 1000.0, 2),
                        "speed_cms": spd,
                        "res_mm": res
                    })

            self.targets = new_targets

        return self.targets


# =============================================================================
# 8. HARDWARE INITIALIZATION
# =============================================================================
def init_peripherals():
    i2c = None
    uart0 = None
    sensor = None
    radar = None

    if IS_MICROPYTHON:
        # 1. Initialize I2C1
        try:
            i2c = I2C(1, sda=Pin(I2C1_SDA), scl=Pin(I2C1_SCL), freq=100000)
            sensor = HW220_Sensor(i2c)
            if sensor.probe():
                sensor.read_all()
        except Exception as e:
            print("[WARN] I2C1 init issue:", e)

        # 2. Initialize UART0
        try:
            try:
                uart0 = UART(0, baudrate=256000, tx=Pin(RADAR_TX), rx=Pin(RADAR_RX), rxbuf=512)
            except TypeError:
                uart0 = UART(0, baudrate=256000, tx=Pin(RADAR_TX), rx=Pin(RADAR_RX))
            radar = LD2450_Radar(uart0)
        except Exception as e:
            print("[WARN] UART0 init issue:", e)

    if not sensor:
        sensor = HW220_Sensor(None)
    if not radar:
        radar = LD2450_Radar(None)

    return sensor, radar


# =============================================================================
# 9. TELEMETRY PACKET BUILDER (MATCHING NODEVAYU_UART_PROTOCOL_SPEC.md)
# =============================================================================
def build_telemetry_json(sensor, radar):
    # Format targets array
    tgts_str = "[" + ",".join([
        '{"id":%d,"x_mm":%d,"y_mm":%d,"dist_m":%.2f,"speed_cms":%d,"res_mm":%d}' % (
            t["id"], t["x_mm"], t["y_mm"], t["dist_m"], t["speed_cms"], t["res_mm"]
        ) for t in radar.targets
    ]) + "]"

    pkt_json = (
        '{"type":"telemetry","uptime_ms":%d,'
        '"env":{"temp_c":%.2f,"rh_pct":%.2f,"heat_index_c":%.1f,"dew_point_c":%.1f},'
        '"radar":{"connected":%s,"total_frames":%d,"targets":%s},'
        '"fan":{"power":%s,"level":%d,"counts":%d,"rpm":%d,"mode":%d,"last_packet":"%s","last_crc":%d}}\n'
    ) % (
        time.ticks_ms() if IS_MICROPYTHON else int(time.time() * 1000),
        sensor.temp_c, sensor.rh_pct, sensor.heat_index_c, sensor.dew_point_c,
        "true" if radar.connected else "false", radar.total_frames, tgts_str,
        "true" if fan_state["power"] else "false",
        fan_state["level"], fan_state["counts"], fan_state["rpm"], fan_state["mode"],
        fan_state["last_packet"], fan_state["last_crc"]
    )
    return pkt_json


# =============================================================================
# 10. INTERACTIVE REPL SHELL & UART EVENT LOOP
# =============================================================================
def help_menu():
    print("""
=================================================
          ATOMQUEST RP2040 SUPERVISOR SHELL
=================================================

POWER:
  on, start          Restore last saved speed
  off, stop, 0       Coast fan to stop
  toggle, p, power   Toggle power ON/OFF

SPEED:
  1 .. 6             Set level directly (e.g. '1', '3', '6')
  level 1..6         Set level (e.g. 'level 3')
  up, +              Increase speed (+1)
  down, -            Decrease speed (-1)

RAW SPEED:
  speed <0-511>      Direct 9-bit speed (e.g. 'speed 330')

LIGHTING:
  mode <0-5>         Switch HUD mode (0=Diag, 1=Level, 2=Dark, 3=Full, 4=Tri, 5=Beacon)

TELEMETRY & SENSORS:
  sensor             Read live HW-220 Temperature & Humidity
  radar              Poll HLK-LD2450 mmWave Radar targets
  status             Show comprehensive status summary
  stream <on|off>    Toggle 5 Hz periodic JSON telemetry stream (for Web Dashboard)
  json               Print single telemetry JSON line

DIAGNOSTICS:
  test               Emit 3s carrier (phone camera check)
  help, ?            Show this menu
  quit, exit         Exit shell

=================================================
""")


def execute_command_string(line, sensor, radar, streaming_active):
    parts = line.strip().split()
    if not parts:
        return streaming_active

    cmd = parts[0].lower()

    if cmd in ("1", "2", "3", "4", "5", "6"):
        set_level(int(cmd))

    elif cmd in ("0", "off", "stop"):
        fan_off()

    elif cmd in ("on", "start"):
        fan_on()

    elif cmd in ("toggle", "p", "power"):
        fan_toggle()

    elif cmd in ("up", "+", "step_up"):
        speed_up()

    elif cmd in ("down", "-", "step_down"):
        speed_down()

    elif cmd == "level":
        if len(parts) == 2:
            try:
                set_level(int(parts[1]))
            except ValueError:
                print("Usage: level 1..6")
        else:
            print("Usage: level 1..6")

    elif cmd == "speed":
        if len(parts) == 2:
            try:
                set_raw_speed(int(parts[1]))
            except ValueError:
                print("Usage: speed 0..511")
        else:
            print("Usage: speed 0..511")

    elif cmd in ("mode", "light"):
        if len(parts) == 2:
            try:
                set_light_mode(int(parts[1]))
            except ValueError:
                print("Usage: mode 0..5")
        else:
            print("Usage: mode 0..5")

    elif cmd in ("test", "burst", "carrier"):
        secs = 3.0
        if len(parts) > 1:
            try:
                secs = float(parts[1])
            except ValueError:
                pass
        test_carrier(secs)

    elif cmd in ("sensor", "temp", "env"):
        sensor.read_all()
        print("[ENV] Temp: {} °C | RH: {} % | Heat Index: {} °C | Dew Point: {} °C".format(
            sensor.temp_c, sensor.rh_pct, sensor.heat_index_c, sensor.dew_point_c
        ))

    elif cmd in ("radar", "targets"):
        tgts = radar.update()
        if not tgts:
            print("[RADAR] Zone Clear (0 targets). Total frames: {}".format(radar.total_frames))
        else:
            print("[RADAR] Detected {} target(s):".format(len(tgts)))
            for t in tgts:
                print("  Target #{}: Dist={}m (X={}mm, Y={}mm) | Speed={} cm/s".format(
                    t['id'], t['dist_m'], t['x_mm'], t['y_mm'], t['speed_cms']
                ))

    elif cmd == "status":
        sensor.read_all()
        print("-" * 52)
        print("FAN STATUS: Power={} | Level={} | Speed={}/511 (~{} RPM) | Mode={}".format(
            "ON" if fan_state["power"] else "OFF",
            fan_state["level"], fan_state["counts"], fan_state["rpm"], fan_state["mode"]
        ))
        print("LAST PACKET: {} (CRC: 0x{:X})".format(fan_state["last_packet"], fan_state["last_crc"]))
        print("ENVIRONMENT: {} °C | {} % RH | Heat Index: {} °C".format(
            sensor.temp_c, sensor.rh_pct, sensor.heat_index_c
        ))
        print("RADAR STREAM: Frames={} | Targets={}".format(radar.total_frames, len(radar.targets)))
        print("-" * 52)

    elif cmd == "stream":
        if len(parts) > 1:
            arg = parts[1].lower()
            if arg in ("on", "1", "start"):
                streaming_active = True
                print("[STREAM] Periodic 5 Hz JSON telemetry ENABLED.")
            elif arg in ("off", "0", "stop"):
                streaming_active = False
                print("[STREAM] Periodic telemetry DISABLED. Shell quiet.")
        else:
            streaming_active = not streaming_active
            print("[STREAM] Telemetry stream is now:", "ENABLED" if streaming_active else "DISABLED")

    elif cmd in ("json", "telemetry"):
        sys.stdout.write(build_telemetry_json(sensor, radar))

    elif cmd in ("help", "?"):
        help_menu()

    else:
        print("Unknown command '{}'. Type 'help'.".format(line))

    return streaming_active


def run_supervisor():
    sensor, radar = init_peripherals()

    print("\n" + "=" * 62)
    print("  ATOMQUEST — RP2040 CLIMATE INTELLIGENCE SUPERVISOR")
    print("  IR Pin: GPIO 16 (Pin 21) | 38.0 kHz PWM (50% Duty)")
    print("  HW-220: GP26(SDA), GP27(SCL) ->", "[ONLINE]" if sensor.connected else "[SCANNING]")
    print("  Radar : GP28(TX), GP29(RX)   ->", "[READY]")
    print("=" * 62)
    print("Type 'help' for command reference.\n")

    # Non-blocking terminal poller
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)

    streaming_active = False  # Keep quiet for Thonny by default; enable via 'stream on'
    line_buf = ""
    last_sensor_poll = time.ticks_ms() if IS_MICROPYTHON else 0
    last_telem_stream = time.ticks_ms() if IS_MICROPYTHON else 0

    if not streaming_active:
        sys.stdout.write("ATOMQUEST> ")

    while True:
        try:
            now = time.ticks_ms() if IS_MICROPYTHON else int(time.time() * 1000)

            # 1. Update radar frames continuously
            radar.update()

            # 2. Periodic sensor poll (every 2.0s)
            if time.ticks_diff(now, last_sensor_poll) >= 2000:
                last_sensor_poll = now
                sensor.read_all()

            # 3. Stream JSON telemetry if enabled (every 200ms = 5 Hz)
            if streaming_active and time.ticks_diff(now, last_telem_stream) >= 200:
                last_telem_stream = now
                sys.stdout.write(build_telemetry_json(sensor, radar))

            # 4. Check for user input from Serial / Thonny Shell
            if poller.poll(0):
                ch = sys.stdin.read(1)
                if ch:
                    if ch in ("\r", "\n"):
                        sys.stdout.write("\n")
                        executed_line = line_buf.strip()
                        line_buf = ""
                        if executed_line:
                            streaming_active = execute_command_string(executed_line, sensor, radar, streaming_active)
                        if not streaming_active:
                            sys.stdout.write("ATOMQUEST> ")

                    elif ch in ("\x08", "\x7f"):  # Backspace handling
                        if len(line_buf) > 0:
                            line_buf = line_buf[:-1]
                            sys.stdout.write("\b \b")

                    else:
                        line_buf += ch
                        if not streaming_active:
                            sys.stdout.write(ch)

            time.sleep_ms(10)

        except KeyboardInterrupt:
            print("\n[Interrupted by user]")
            if ir:
                ir.duty_u16(0)
            break
        except Exception as e:
            print("\nError in main loop:", e)
            time.sleep_ms(500)


if __name__ == "__main__":
    run_supervisor()
