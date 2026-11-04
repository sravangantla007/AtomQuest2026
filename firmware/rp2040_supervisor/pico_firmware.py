"""
===============================================================================
 NodeVAYU Edge AI Supervisor: Production Firmware (MicroPython)
 Repository: https://github.com/sravangantla007/AtomQuest2026
 Board: Raspberry Pi Pico (RP2040)

 Peripherals:
  - HLK-LD2450 24GHz mmWave Radar : UART0 (GP28 TX, GP29 RX @ 256,000 bps)
  - HW-220 / HTU21D Temp & RH     : I2C1 (GP26 SDA, GP27 SCL @ 100 kHz)
  - 38 kHz Infrared Actuation Link : GPIO 16 (Hardware PWM @ 38.0 kHz)
  - USB Serial Control & Telemetry: 115,200 baud JSON stream
===============================================================================
"""

import time
import math
import sys
import select
from machine import Pin, PWM, I2C, UART

# Pins
PIN_IR_TX    = 16
PIN_I2C_SDA  = 26
PIN_I2C_SCL  = 27
PIN_RADAR_TX = 28
PIN_RADAR_RX = 29

# Onboard LED
try:
    led = Pin("LED", Pin.OUT)
except Exception:
    try:
        led = Pin(25, Pin.OUT)
    except Exception:
        led = None

def blink(times=1, d_ms=30):
    if not led:
        return
    for _ in range(times):
        led.value(1)
        time.sleep_ms(d_ms)
        led.value(0)
        time.sleep_ms(d_ms)

# =============================================================================
# 1. 38 kHz IR ACTUATION ENGINE (ICD 0xAF COMPLIANT)
# =============================================================================
class IRActuator:
    ADDR               = 0xAF
    CMD_ALL_OFF        = 0x00
    CMD_SET_RAW_SPEED  = 0x01
    CMD_SET_LEVEL      = 0x02
    CMD_TOGGLE_POWER   = 0x03
    CMD_SET_LIGHT_MODE = 0x04
    CMD_POWER_ON       = 0x07

    PRESETS = [0, 200, 265, 330, 395, 455, 511]

    def __init__(self, pin_num=PIN_IR_TX):
        self.pwm = PWM(Pin(pin_num))
        self.pwm.freq(38000)
        self.pwm.duty_u16(0)
        self.duty_on = 32768  # 50% duty cycle for maximum optical photon yield

    def encode(self, cmd, data_l, data_h_low=0):
        crc = (self.ADDR ^ cmd ^ data_h_low ^ data_l) & 0x0F
        data_h = (crc << 4) | (data_h_low & 0x0F)
        return ((self.ADDR << 24) | (cmd << 16) | (data_h << 8) | (data_l & 0xFF)) & 0xFFFFFFFF

    @micropython.native
    def _send_bits(self, packet: int):
        # 1. Leader (9000us mark, 4500us space)
        self.pwm.duty_u16(self.duty_on)
        time.sleep_us(9000)
        self.pwm.duty_u16(0)
        time.sleep_us(4500)

        # 2. 32 Data bits
        for bit in range(31, -1, -1):
            self.pwm.duty_u16(self.duty_on)
            time.sleep_us(560)
            self.pwm.duty_u16(0)
            if (packet >> bit) & 0x01:
                time.sleep_us(1690)
            else:
                time.sleep_us(560)

        # 3. Stop bit
        self.pwm.duty_u16(self.duty_on)
        time.sleep_us(560)
        self.pwm.duty_u16(0)

    def transmit(self, packet: int, repeats: int = 2):
        blink(repeats, 25)
        for r in range(repeats):
            self._send_bits(packet)
            if r < repeats - 1:
                time.sleep_ms(40)
        time.sleep_ms(20)

    def burst_carrier(self, ms: int = 500):
        self.pwm.duty_u16(self.duty_on)
        time.sleep_ms(ms)
        self.pwm.duty_u16(0)


# =============================================================================
# 2. HW-220 / HTU21D SENSOR DRIVER
# =============================================================================
class TempHumidSensor:
    ADDR = 0x40

    def __init__(self, i2c: I2C):
        self.i2c = i2c
        self.temp_c = 28.0
        self.rh = 55.0
        self.last_read = 0
        self.init_sensor()

    def init_sensor(self):
        try:
            self.i2c.writeto(self.ADDR, b'\xFE')  # Soft reset
            time.sleep_ms(25)
        except Exception:
            pass

    def update(self):
        now = time.ticks_ms()
        if time.ticks_diff(now, self.last_read) < 1500:
            return
        self.last_read = now

        try:
            # Trigger Temp
            self.i2c.writeto(self.ADDR, b'\xF3')
            time.sleep_ms(55)
            raw_t = self.i2c.readfrom(self.ADDR, 3)
            val_t = ((raw_t[0] << 8) | raw_t[1]) & 0xFFFC
            self.temp_c = round(-46.85 + (175.72 * (val_t / 65536.0)), 2)

            # Trigger Humidity
            self.i2c.writeto(self.ADDR, b'\xF5')
            time.sleep_ms(35)
            raw_h = self.i2c.readfrom(self.ADDR, 3)
            val_h = ((raw_h[0] << 8) | raw_h[1]) & 0xFFFC
            rh = -6.0 + (125.0 * (val_h / 65536.0))
            self.rh = round(max(0.0, min(100.0, rh)), 2)
        except Exception:
            pass


# =============================================================================
# 3. HLK-LD2450 mmWAVE RADAR DRIVER
# =============================================================================
class RadarLD2450:
    HEADER = b'\xAA\xFF\x03\x00'
    TAIL   = b'\x55\xCC'
    LEN    = 30

    def __init__(self, uart: UART):
        self.uart = uart
        self.buf = bytearray()
        self.targets = []

    def _decode_s16(self, low, high):
        v = low | (high << 8)
        return -(v & 0x7FFF) if (v & 0x8000) else v

    def update(self):
        if self.uart.any():
            chunk = self.uart.read()
            if chunk:
                self.buf.extend(chunk)

        if len(self.buf) > 256:
            self.buf = self.buf[-128:]

        while True:
            idx = self.buf.find(self.HEADER)
            if idx == -1:
                if len(self.buf) > 3:
                    self.buf = self.buf[-3:]
                break

            if len(self.buf) < idx + self.LEN:
                if idx > 0:
                    self.buf = self.buf[idx:]
                break

            frame = self.buf[idx : idx + self.LEN]
            self.buf = self.buf[idx + self.LEN :]

            if frame[28:30] != self.TAIL:
                continue

            parsed = []
            for i in range(3):
                o = 4 + (i * 8)
                x = self._decode_s16(frame[o], frame[o+1])
                y = self._decode_s16(frame[o+2], frame[o+3])
                spd = self._decode_s16(frame[o+4], frame[o+5])
                if x != 0 or y != 0:
                    d_m = round(math.sqrt(x*x + y*y) / 1000.0, 2)
                    parsed.append({"id": i+1, "x": x, "y": y, "dist": d_m, "speed": spd})

            self.targets = parsed


# =============================================================================
# 4. MAIN LOOP & SERIAL COMMAND BRIDGE
# =============================================================================
def main():
    # Setup Peripherals
    ir = IRActuator(PIN_IR_TX)
    i2c = I2C(1, sda=Pin(PIN_I2C_SDA), scl=Pin(PIN_I2C_SCL), freq=100000)
    sensor = TempHumidSensor(i2c)

    try:
        uart0 = UART(0, baudrate=256000, tx=Pin(PIN_RADAR_TX), rx=Pin(PIN_RADAR_RX), rxbuf=1024)
    except Exception:
        uart0 = UART(0, baudrate=256000, tx=Pin(PIN_RADAR_TX), rx=Pin(PIN_RADAR_RX))
    radar = RadarLD2450(uart0)

    # Initial Fan State
    current_level = 3
    current_counts = 330
    current_rpm = 1050
    current_mode = 1
    last_packet_hex = "0xAF02E003"

    # Send initial boot level 3 command to fan
    init_pkt = ir.encode(ir.CMD_SET_LEVEL, 3)
    ir.transmit(init_pkt, repeats=2)

    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)

    last_telem_out = 0
    input_line_buf = ""

    while True:
        now = time.ticks_ms()

        # Update sensor and radar
        sensor.update()
        radar.update()

        # Check for serial commands from host bridge
        if poller.poll(0):
            ch = sys.stdin.read(1)
            if ch:
                if ch in ('\n', '\r'):
                    cmd_line = input_line_buf.strip()
                    input_line_buf = ""
                    if cmd_line:
                        # Process command
                        try:
                            # Commands: "level <1-6>", "speed <0-511>", "off", "on", "toggle", "mode <0-5>", "carrier <ms>"
                            parts = cmd_line.split()
                            verb = parts[0].lower()

                            if verb == "level" and len(parts) > 1:
                                lvl = int(parts[1])
                                if 1 <= lvl <= 6:
                                    current_level = lvl
                                    current_counts = ir.PRESETS[lvl]
                                    current_rpm = round((current_counts * 1450) / 511)
                                    pkt = ir.encode(ir.CMD_SET_LEVEL, lvl)
                                    last_packet_hex = "0x%08X" % pkt
                                    ir.transmit(pkt)
                                elif lvl == 0:
                                    current_level = 0
                                    current_counts = 0
                                    current_rpm = 0
                                    pkt = ir.encode(ir.CMD_ALL_OFF, 0)
                                    last_packet_hex = "0x%08X" % pkt
                                    ir.transmit(pkt)

                            elif verb == "speed" and len(parts) > 1:
                                spd = max(0, min(511, int(parts[1])))
                                current_counts = spd
                                current_rpm = round((spd * 1450) / 511)
                                pkt = ir.encode(ir.CMD_SET_RAW_SPEED, spd & 0xFF, (spd >> 8) & 0x01)
                                last_packet_hex = "0x%08X" % pkt
                                ir.transmit(pkt)

                            elif verb in ("off", "stop"):
                                current_level = 0
                                current_counts = 0
                                current_rpm = 0
                                pkt = ir.encode(ir.CMD_ALL_OFF, 0)
                                last_packet_hex = "0x%08X" % pkt
                                ir.transmit(pkt)

                            elif verb in ("on", "start"):
                                if current_level == 0:
                                    current_level = 3
                                    current_counts = 330
                                    current_rpm = 1050
                                pkt = ir.encode(ir.CMD_POWER_ON, 0)
                                last_packet_hex = "0x%08X" % pkt
                                ir.transmit(pkt)

                            elif verb in ("toggle", "power"):
                                if current_counts > 0:
                                    current_counts = 0
                                    current_rpm = 0
                                    current_level = 0
                                else:
                                    current_level = 3
                                    current_counts = 330
                                    current_rpm = 1050
                                pkt = ir.encode(ir.CMD_TOGGLE_POWER, 0)
                                last_packet_hex = "0x%08X" % pkt
                                ir.transmit(pkt)

                            elif verb == "mode" and len(parts) > 1:
                                m = int(parts[1])
                                if 0 <= m <= 5:
                                    current_mode = m
                                    pkt = ir.encode(ir.CMD_SET_LIGHT_MODE, m)
                                    last_packet_hex = "0x%08X" % pkt
                                    ir.transmit(pkt)

                            elif verb == "carrier":
                                ms = int(parts[1]) if len(parts) > 1 else 1000
                                ir.burst_carrier(ms)

                        except Exception as e:
                            pass
                else:
                    input_line_buf += ch

        # Emit JSON Telemetry Line every 300ms
        if time.ticks_diff(now, last_telem_out) >= 300:
            last_telem_out = now

            # Heat index calculation
            t = sensor.temp_c
            h = sensor.rh
            hi = round(t + 0.33 * (h / 100.0 * 6.105 * math.exp(17.27 * t / (237.7 + t))) - 4.0, 1)

            # Targets JSON
            tgts_str = "[" + ",".join([
                '{"id":%d,"x":%d,"y":%d,"dist":%.2f,"speed":%d}' % (tgt["id"], tgt["x"], tgt["y"], tgt["dist"], tgt["speed"])
                for tgt in radar.targets
            ]) + "]"

            # Clean JSON output line
            telem_json = (
                '{"type":"telemetry","temp":%.2f,"rh":%.2f,"heat_index":%.1f,'
                '"targets":%s,'
                '"fan":{"level":%d,"rpm":%d,"counts":%d,"mode":%d,"last_pkt":"%s"}}\n'
            ) % (t, h, hi, tgts_str, current_level, current_rpm, current_counts, current_mode, last_packet_hex)

            sys.stdout.write(telem_json)

        time.sleep_ms(15)

if __name__ == "__main__":
    main()
