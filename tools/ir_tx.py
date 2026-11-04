"""
===============================================================================
 Atomberg BLDC Fan Mod: RP2040 MicroPython IR Base Station Controller
 Hardware: Raspberry Pi Pico (RP2040)
 IR LED on GPIO 16 (HW-220 transmitter / NPN driver)
 Carrier: 38.0 kHz Hardware PWM (33% duty cycle)
 Custom Firmware by sravangantla007
 More Info: https://github.com/sravangantla007/AtomQuest2026
===============================================================================
"""

import time
from machine import Pin, PWM

# Hardware Settings
IR_TX_PIN = 16
CARRIER_FREQ = 38000
DUTY_33_PCT = int(65535 * 0.33)  # 33% duty cycle for maximum IR burst efficiency

# Unique Atomberg Fan Address
FAN_IR_ADDR = 0xAF

# Command Set
CMD_ALL_OFF        = 0x00
CMD_SET_RAW_SPEED  = 0x01
CMD_SET_LEVEL      = 0x02
CMD_TOGGLE_POWER   = 0x03
CMD_SET_LIGHT_MODE = 0x04
CMD_STEP_UP        = 0x05
CMD_STEP_DOWN      = 0x06
CMD_POWER_ON       = 0x07

# Calibrated Fan Levels (Matching Atomberg Factory Tuning)
LEVEL_INFO = {
    0: {"counts": 0,   "rpm": 0,    "name": "OFF / Coasting"},
    1: {"counts": 200, "rpm": 650,  "name": "Level 1: Gentle Breeze (39% PWM)"},
    2: {"counts": 265, "rpm": 900,  "name": "Level 2: Low-Medium (52% PWM)"},
    3: {"counts": 330, "rpm": 1050, "name": "Level 3: Medium (65% PWM)"},
    4: {"counts": 395, "rpm": 1200, "name": "Level 4: Medium-High (77% PWM)"},
    5: {"counts": 455, "rpm": 1350, "name": "Level 5: High (89% PWM)"},
    6: {"counts": 511, "rpm": 1450, "name": "Level 6: Full Throttle Turbo (100% PWM)"}
}

LIGHT_MODES = {
    0: "Debug Diagnostic HUD (Vbus, I2C, Startup, Steady, Fault, OC)",
    1: "Speed Level Gauge (1 to 6 LEDs mapped from speed)",
    2: "Dark Mode (Off in steady state, wakes for 3s on remote command)",
    3: "Night Light Full (All 6 LEDs ON steady)",
    4: "Night Light Half (3 LEDs ON in equilateral triangle pattern)",
    5: "Single LED Beacon (Only LED 1 ON steady subtle night light)"
}


class AtombergIRTransmitter:
    def __init__(self, pin_num=IR_TX_PIN):
        self.pin_num = pin_num
        self.pwm = PWM(Pin(pin_num))
        self.pwm.freq(CARRIER_FREQ)
        self.pwm.duty_u16(0)  # Carrier initially OFF
        self.current_level = 0
        self.current_light_mode = 1

    def _carrier_on(self):
        self.pwm.duty_u16(DUTY_33_PCT)

    def _carrier_off(self):
        self.pwm.duty_u16(0)

    def _encode_packet(self, cmd: int, data_l: int, data_h_low: int = 0) -> int:
        addr = FAN_IR_ADDR
        # 4-bit checksum: ADDR ^ CMD ^ DATA_H_LOW ^ DATA_L
        checksum = (addr ^ cmd ^ data_h_low ^ data_l) & 0x0F
        data_h = (checksum << 4) | (data_h_low & 0x0F)
        # 32-bit packet word
        return (addr << 24) | (cmd << 16) | (data_h << 8) | (data_l & 0xFF)

    @micropython.native
    def _send_single_frame(self, packet: int):
        # 1. NEC Leader: 9000us mark + 4500us space
        self._carrier_on()
        time.sleep_us(9000)
        self._carrier_off()
        time.sleep_us(4500)

        # 2. 32 Data Bits (MSB first)
        for bit in range(31, -1, -1):
            self._carrier_on()
            time.sleep_us(560)
            self._carrier_off()

            if (packet >> bit) & 0x01:
                time.sleep_us(1690)
            else:
                time.sleep_us(560)

        # 3. Stop Bit: 560us mark
        self._carrier_on()
        time.sleep_us(560)
        self._carrier_off()

    def send_packet(self, packet: int, repeats: int = 1):
        """Transmits single IR command packet."""
        for r in range(repeats):
            self._send_single_frame(packet)
            if r < repeats - 1:
                time.sleep_ms(35)
        time.sleep_ms(20)

    def set_level(self, level: int):
        """Sets fan speed level 1 to 6 (0 = off)."""
        if not (0 <= level <= 6):
            print(f"[Error] Invalid level {level}. Use 0 to 6.")
            return

        info = LEVEL_INFO[level]
        self.current_level = level

        if level == 0:
            packet = self._encode_packet(CMD_ALL_OFF, data_l=0x00)
            print(f"[IR TX] Command: FAN OFF (Coasting down) -> 0x{packet:08X}")
        else:
            packet = self._encode_packet(CMD_SET_LEVEL, data_l=level)
            print(f"[IR TX] Command: {info['name']} (~{info['rpm']} RPM) -> Packet: 0x{packet:08X}")

        self.send_packet(packet)

    def send_speed(self, speed_9bit: int):
        """Sets continuous 9-bit DRV10983 motor speed (0 to 511)."""
        speed_9bit = max(0, min(511, speed_9bit))
        data_l = speed_9bit & 0xFF
        data_h_low = (speed_9bit >> 8) & 0x01
        packet = self._encode_packet(CMD_SET_RAW_SPEED, data_l=data_l, data_h_low=data_h_low)
        pct = (speed_9bit * 100) // 511
        print(f"[IR TX] Command: Raw Speed = {speed_9bit}/511 ({pct}%) -> Packet: 0x{packet:08X}")
        self.send_packet(packet)

    def set_light_mode(self, mode: int):
        """Sets the steady-state lighting mode (0 to 5)."""
        if not (0 <= mode <= 5):
            print(f"[Error] Invalid lighting mode {mode}. Choose 0 to 5:")
            for k, v in LIGHT_MODES.items():
                print(f"  {k}: {v}")
            return

        self.current_light_mode = mode
        packet = self._encode_packet(CMD_SET_LIGHT_MODE, data_l=mode)
        print(f"[IR TX] Command: Set Mode {mode} -> {LIGHT_MODES[mode]} (Packet: 0x{packet:08X})")
        self.send_packet(packet)

    def step_up(self):
        """Sends native Step UP command to MCU (increments state 1..6)."""
        packet = self._encode_packet(CMD_STEP_UP, data_l=0x00)
        print(f"[IR TX] Command: Step UP (+) -> Packet: 0x{packet:08X}")
        self.send_packet(packet)

    def step_down(self):
        """Sends native Step DOWN command to MCU (decrements state 6..1)."""
        packet = self._encode_packet(CMD_STEP_DOWN, data_l=0x00)
        print(f"[IR TX] Command: Step DOWN (-) -> Packet: 0x{packet:08X}")
        self.send_packet(packet)

    def toggle_power(self):
        """Sends native Power Toggle (ON/OFF). ON restores last running state."""
        packet = self._encode_packet(CMD_TOGGLE_POWER, data_l=0x00)
        print(f"[IR TX] Command: Toggle Power ON/OFF -> Packet: 0x{packet:08X}")
        self.send_packet(packet)

    def power_on(self):
        """Sends explicit Power ON command to restore last running state."""
        packet = self._encode_packet(CMD_POWER_ON, data_l=0x00)
        print(f"[IR TX] Command: Power ON (Restore last state) -> Packet: 0x{packet:08X}")
        self.send_packet(packet)

    def fan_off(self):
        """Sends Power OFF command (coasting down to stop)."""
        packet = self._encode_packet(CMD_ALL_OFF, data_l=0x00)
        print(f"[IR TX] Command: Power OFF (Coasting down) -> Packet: 0x{packet:08X}")
        self.send_packet(packet)


# Interactive Console in Thonny
if __name__ == "__main__":
    tx = AtombergIRTransmitter(IR_TX_PIN)
    print("=" * 72)
    print("  Atomberg Fan Mod: Closed-Loop Motor Control Base Station (RP2040)")
    print("  Custom Firmware by sravangantla007")
    print("  Project: https://github.com/sravangantla007/AtomQuest2026")
    print("=" * 72)
    print("  Hardware Safety Features:")
    print("    * Overcurrent Trip locked at 950 counts (~3.06V / ~2.95A peak)")
    print("      -> Auto-shuts down fan & latches LED 6 until next IR command")
    print("    * Thermal OverTemp (Tj > 155C): 45-second mandatory cooldown")
    print("    * Non-Volatile Memory: Automatically remembers last set speed & mode")
    print("=" * 72)
    print("  Lighting Modes (Applied in Steady State):")
    print("    mode 0 / mode debug    : Diagnostic HUD")
    print("    mode 1 / mode level    : Speed Level Bar Gauge (1..6 LEDs)")
    print("    mode 2 / mode dark     : Stealth Dark (wakes 3s on IR command)")
    print("    mode 3 / mode night    : Night Light Full (6 LEDs steady)")
    print("    mode 4 / mode triangle : Night Light Half (3 LEDs equilateral triangle)")
    print("    mode 5 / mode single   : Single Subtle LED Beacon (Only LED 1 ON)")
    print("=" * 72)
    print("Commands:")
    print("  1 to 6           : Set speed level (1: 200, 2: 265, 3: 330, 4: 395, 5: 455, 6: 511)")
    print("  on               : Power ON (restores last active speed)")
    print("  0, off, stop     : Turn fan off (gentle decel to stop, remembers speed)")
    print("  toggle, p, power : Toggle ON / OFF (ON restores last state)")
    print("  +, up / -, down  : Native MCU Step UP / DOWN")
    print("  speed <0-511>    : Set raw continuous 9-bit speed directly")
    print("  mode <0-5/name>  : Switch lighting mode")
    print("=" * 72 + "\n")

    while True:
        try:
            line = input("fan> ").strip().lower()
            if not line:
                continue

            if line in ("0", "1", "2", "3", "4", "5", "6"):
                tx.set_level(int(line))
            elif line.startswith("level "):
                tx.set_level(int(line.split()[1]))
            elif line.startswith("speed "):
                tx.send_speed(int(line.split()[1]))
            elif line in ("on", "start"):
                tx.power_on()
            elif line in ("off", "stop", "0"):
                tx.fan_off()
            elif line in ("p", "power", "toggle"):
                tx.toggle_power()
            elif line in ("+", "up"):
                tx.step_up()
            elif line in ("-", "down"):
                tx.step_down()
            elif line.startswith("mode"):
                parts = line.split()
                if len(parts) == 1:
                    print(f"Current mode: {tx.current_light_mode} ({LIGHT_MODES[tx.current_light_mode]})")
                    print("Options: mode 0 (debug), mode 1 (level), mode 2 (dark), mode 3 (night), mode 4 (half), mode 5 (single)")
                else:
                    arg = parts[1]
                    if arg in ("0", "debug"):
                        tx.set_light_mode(0)
                    elif arg in ("1", "level", "speed"):
                        tx.set_light_mode(1)
                    elif arg in ("2", "dark", "stealth"):
                        tx.set_light_mode(2)
                    elif arg in ("3", "night", "full"):
                        tx.set_light_mode(3)
                    elif arg in ("4", "half", "triangle"):
                        tx.set_light_mode(4)
                    elif arg in ("5", "single", "one", "beacon"):
                        tx.set_light_mode(5)
                    else:
                        print("Unknown mode. Use 0 (debug), 1 (level), 2 (dark), 3 (night), 4 (triangle), 5 (single)")
            else:
                print("Commands: 1..6, on, off, toggle, +, -, speed <0-511>, mode <0-5>")
        except Exception as e:
            print(f"Error: {e}")
