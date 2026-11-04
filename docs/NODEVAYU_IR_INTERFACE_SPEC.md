# NodeVAYU: Hardware & Interface Control Document (ICD)
## Infrared (IR) Actuation Link Specification: RP2040 Edge AI Supervisor $\leftrightarrow$ STM8 Motor Core

**Project:** NodeVAYU — The Remote-Free Climate Intelligence Node  
**Event:** atomQUEST 2026 | Track 1: Air, Reimagined for the Next Decade  
**Author:** Gantla Venkata Sravan (Roll No: EE24B013), Dept. of Electrical Engineering, IIT Tirupati  
**Repository:** [github.com/sravangantla007/AtomQuest2026](https://github.com/sravangantla007/AtomQuest2026)  
**Document Revision:** 1.0.0 (Release)  
**Date:** November 4, 2026  

---

## 1. System Topology & Architecture

This document is the definitive interface contract for any firmware or autonomous agent developing software for the **RP2040 Edge-AI Supervisor** (or secondary controller) communicating with the **STM8S003F3 Actuation Core** on the Atomberg BLDC fan.

```
┌────────────────────────────────────────────────────────┐
│          NodeVAYU Edge AI Supervisor (RP2040)          │
│   • 24 GHz FMCW Radar (Range gating & Micro-Doppler)   │
│   • Factory-calibrated Digital T/RH Sensor (I2C)       │
│   • TinyML Adaptation Engine: RPM_target = Base + ML   │
│   • 38 kHz Hardware PWM IR Transmitter (GPIO 16)       │
└───────────────────────────┬────────────────────────────┘
                            │ Infrared Optical Link (38 kHz Carrier)
                            │ Pulse-Distance 32-Bit Protocol (Addr: 0xAF)
                            ▼
┌────────────────────────────────────────────────────────┐
│            Atomberg Fan Controller (STM8S003F3)        │
│   • Optical Demodulator: Active-LOW on Pin PA3         │
│   • TI DRV10983 3-Phase Sensorless BLDC Driver (I2C)   │
│   • Non-Volatile EEPROM State Memory (Wear-Free)       │
│   • 6-LED Circular HUD & Real-Time Safety Latch        │
└────────────────────────────────────────────────────────┘
```

---

## 2. Physical Layer (PHY) & Electrical Timings

The physical layer uses a pulse-distance modulated infrared link modulated on a $38.0\,\text{kHz}$ carrier frequency (NEC timing variant).

### 2.1 Optical & Electrical Characteristics
* **Carrier Frequency ($f_c$):** $38.0\,\text{kHz}$ ($\pm 1\%$, Period $T = 26.316\,\mu\text{s}$)
* **Carrier Duty Cycle:** $33.3\%$ ($8.77\,\mu\text{s}$ LED ON, $17.54\,\mu\text{s}$ LED OFF)
* **Demodulated Logic Level (at STM8 Pin PA3):**
  * **Carrier Present (Burst / Mark):** Pin PA3 is **LOW** ($0\,\text{V}$)
  * **Carrier Absent (Silence / Space):** Pin PA3 is **HIGH** ($3.3\,\text{V}$, internal pull-up)

```
                       |<------------------- BIT PERIOD ------------------->|
Transmitter (IR LED):  [=== 38 kHz Carrier ===] [              Silence             ]
Receiver Output (PA3): |________ LOW _________|‾‾‾‾‾‾‾‾‾‾‾‾‾‾ HIGH ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾|
```

### 2.2 Strict Bit Timings

| Symbol | Transmitter State | Receiver Output (PA3) | Nominal Duration | Firmware Acceptance Window |
| :--- | :--- | :--- | :---: | :---: |
| **Leader Mark (Start)** | $38\,\text{kHz}$ Carrier ON | **LOW** ($0\,\text{V}$) | **$9,000\,\mu\text{s}$** | $7,000\,\mu\text{s} \dots 11,000\,\mu\text{s}$ |
| **Leader Space (Start)** | Silence (Carrier OFF) | **HIGH** ($3.3\,\text{V}$) | **$4,500\,\mu\text{s}$** | $3,000\,\mu\text{s} \dots 5,500\,\mu\text{s}$ |
| **Data '0' Mark** | $38\,\text{kHz}$ Carrier ON | **LOW** ($0\,\text{V}$) | **$560\,\mu\text{s}$** | $< 1,200\,\mu\text{s}$ |
| **Data '0' Space** | Silence (Carrier OFF) | **HIGH** ($3.3\,\text{V}$) | **$560\,\mu\text{s}$** | $< 1,000\,\mu\text{s}$ |
| **Data '1' Mark** | $38\,\text{kHz}$ Carrier ON | **LOW** ($0\,\text{V}$) | **$560\,\mu\text{s}$** | $< 1,200\,\mu\text{s}$ |
| **Data '1' Space** | Silence (Carrier OFF) | **HIGH** ($3.3\,\text{V}$) | **$1,690\,\mu\text{s}$** | $1,000\,\mu\text{s} \dots 2,500\,\mu\text{s}$ |
| **Stop Mark (Trailer)** | $38\,\text{kHz}$ Carrier ON | **LOW** ($0\,\text{V}$) | **$560\,\mu\text{s}$** | Required trailing pulse after Bit 0 |

* **Total Bit '0' Duration:** $560\,\mu\text{s} + 560\,\mu\text{s} = \mathbf{1,120\,\mu\text{s}}$
* **Total Bit '1' Duration:** $560\,\mu\text{s} + 1,690\,\mu\text{s} = \mathbf{2,250\,\mu\text{s}}$
* **Total Frame Duration:** $\approx 67.5\,\text{ms}$
* **Inter-Frame Silence (Cooldown):** Minimum **$40.0\,\text{ms}$** between consecutive packet transmissions.

---

## 3. Frame Format & Checksum Architecture

Each command frame consists of exactly **32 bits**, transmitted **Most Significant Bit (MSB, Bit 31) first**.

### 3.1 32-Bit Frame Structure

```
 31            24 23            16 15     12 11        8 7             0
┌────────────────┬────────────────┬─────────┬───────────┬───────────────┐
│  ADDR (0xAF)   │      CMD       │   CRC   │ DATA_H_LO │    DATA_L     │
│    (8 bits)    │    (8 bits)    │ (4 bits)│  (4 bits) │   (8 bits)    │
└────────────────┴────────────────┴─────────┴───────────┴───────────────┘
                                  └─────────┬───────────┘
                                       DATA_H (8 bits)
```

$$\text{Packet Word} = (\text{ADDR} \ll 24) \mid (\text{CMD} \ll 16) \mid (\text{DATA\_H} \ll 8) \mid \text{DATA\_L}$$

### 3.2 Field Definitions
1. **`ADDR` [Bits 31..24]:** Atomberg Fan Network Address = `0xAF` (`1010 1111b`).
2. **`CMD` [Bits 23..16]:** 8-bit Command Opcode ($0\text{x}00 \dots 0\text{x}07$).
3. **`DATA_H` [Bits 15..8]:**
   - **`CRC` [Bits 15..12]:** 4-bit longitudinal parity checksum:
     $$\text{CRC} = (\text{ADDR} \oplus \text{CMD} \oplus \text{DATA\_H\_LOW} \oplus \text{DATA\_L}) \ \& \ 0\text{x}0\text{F}$$
   - **`DATA_H_LOW` [Bits 11..8]:** High payload bits. For continuous raw speed (`CMD_SET_RAW_SPEED`), this is Bit 8 of the 9-bit speed word (`(speed >> 8) & 0x01`). For all other commands, this is `0x0`.
4. **`DATA_L` [Bits 7..0]:** 8-bit command payload (Lower 8 bits of speed, mode number, or level number).

---

## 4. Complete Command Matrix & Hex Reference

| Opcode (`CMD`) | Command Name | `DATA_H_LOW` | `DATA_L` | 32-Bit Hex Packet | Detailed Behavior |
| :---: | :--- | :---: | :---: | :---: | :--- |
| **`0x00`** | `CMD_ALL_OFF` | `0x0` | `0x00` | `0xAF00F000` | Smooth deceleration to stop. **Preserves previous running speed in EEPROM.** |
| **`0x01`** | `CMD_SET_RAW_SPEED` | `(spd>>8)&1` | `spd&0xFF` | *Dynamic (see §5)* | **Continuous 9-bit speed ($0 \dots 511$).** Core API for NodeVAYU TinyML comfort head! |
| **`0x02`** | `CMD_SET_LEVEL` | `0x0` | `0x01`..`0x06` | *See §6.1* | Direct discrete level set ($1 \dots 6$). |
| **`0x03`** | `CMD_TOGGLE_POWER` | `0x0` | `0x00` | `0xAF03C000` | **Power Toggle:** If spinning, turns OFF. If stopped, **restores last saved speed**. |
| **`0x04`** | `CMD_SET_LIGHT_MODE` | `0x0` | `0x00`..`0x05` | *See §6.2* | Set steady-state LED lighting mode ($0 \dots 5$). |
| **`0x05`** | `CMD_STEP_UP` | `0x0` | `0x00` | `0xAF05A000` | Increment speed state ($1 \to 2 \dots \to 6$). Clamps at 6. Starts at 1 if stopped. |
| **`0x06`** | `CMD_STEP_DOWN` | `0x0` | `0x00` | `0xAF069000` | Decrement speed state ($6 \to 5 \dots \to 1$). Clamps at 1 (never stops fan). |
| **`0x07`** | `CMD_POWER_ON` | `0x0` | `0x00` | `0xAF078000` | **Explicit ON:** Ramps fan directly to last configured speed level. |

---

## 5. Continuous Speed Control API (`CMD_SET_RAW_SPEED`)

This is the primary operational command used by the **NodeVAYU Edge AI Supervisor** to execute Equation (2) from the research paper:

$$\text{RPM}_{\text{target}} = \text{clamp}\left(\text{RPM}_{\text{base}}(T, \text{RH}) + \Delta\text{RPM}_{\text{learned}}, \, \text{RPM}_{\text{min}}, \, \text{RPM}_{\text{max}}\right)$$

### 5.1 Count to RPM Conversion
* **Speed Range:** $0 \dots 511$ counts ($0\text{x}000 \dots 0\text{x}1\text{FF}$)
* **Hardware Output:** 9-bit PWM duty cycle fed to DRV10983 driver registers `0x01` and `0x00`.
* **Formula:**
  $$\text{Speed Counts} = \text{round}\left(\frac{\text{RPM}_{\text{target}} \times 511}{1450}\right)$$
  $$\text{Estimated RPM} = \text{round}\left(\frac{\text{Speed Counts} \times 1450}{511}\right)$$

### 5.2 Automatic Circular LED HUD Quantization
When continuous raw speed commands are sent, the STM8 actuation core automatically quantizes the speed into the circular LED display without dropping out of closed-loop drive:

| Continuous Speed Counts | Equivalent RPM | Active Circular LEDs | Fan Operating Phase |
| :---: | :---: | :---: | :--- |
| **`0`** | $0\,\text{RPM}$ | **0 LEDs** | Stopped / Hi-Z Coasting |
| **`1` to `234`** | $50 \dots 660\,\text{RPM}$ | **1 LED** | Low / Gentle Breeze (Nominal Level 1 = 200) |
| **`235` to `299`** | $665 \dots 850\,\text{RPM}$ | **2 LEDs** | Low-Medium (Nominal Level 2 = 265) |
| **`300` to `364`** | $855 \dots 1030\,\text{RPM}$ | **3 LEDs** | Medium (Nominal Level 3 = 330) |
| **`365` to `424`** | $1035 \dots 1205\,\text{RPM}$ | **4 LEDs** | Medium-High (Nominal Level 4 = 395) |
| **`425` to `484`** | $1210 \dots 1375\,\text{RPM}$ | **5 LEDs** | High (Nominal Level 5 = 455) |
| **`485` to `511`** | $1380 \dots 1450\,\text{RPM}$ | **6 LEDs** | Full Throttle Turbo (Nominal Level 6 = 511) |

---

## 6. Discrete Levels & Lighting Modes

### 6.1 Discrete Speed Levels (`CMD_SET_LEVEL`, `0x02`)
| Level | Raw Counts | Nominal RPM | 32-Bit Hex Packet | Binary Packet (MSB First) |
| :---: | :---: | :---: | :---: | :---: |
| **1** | 200 | ~650 RPM | `0xAF02C001` | `10101111 00000010 11000000 00000001` |
| **2** | 265 | ~900 RPM | `0xAF02F002` | `10101111 00000010 11110000 00000010` |
| **3** | 330 | ~1050 RPM | `0xAF02E003` | `10101111 00000010 11100000 00000011` |
| **4** | 395 | ~1200 RPM | `0xAF029004` | `10101111 00000010 10010000 00000100` |
| **5** | 455 | ~1350 RPM | `0xAF028005` | `10101111 00000010 10000000 00000101` |
| **6** | 511 | ~1450 RPM | `0xAF02B006` | `10101111 00000010 10110000 00000110` |

### 6.2 Lighting Modes (`CMD_SET_LIGHT_MODE`, `0x04`)
| Mode | Name | Visual Behavior | 32-Bit Hex Packet |
| :---: | :--- | :--- | :---: |
| **`0`** | **Debug HUD** | Diagnostic mode: Vbus (LED 1), I2C (LED 2), Startup (LED 3), Steady (LED 4), Fault (LED 5), Overcurrent (LED 6) | `0xAF04B000` |
| **`1`** | **Level Gauge** | Default: Shows 1..6 LEDs matching fan speed; 0 LEDs when fan is stopped | `0xAF04A001` |
| **`2`** | **Stealth Dark** | All LEDs completely OFF in steady state; wakes for 3 seconds on command | `0xAF049002` |
| **`3`** | **Night Light Full**| All 6 LEDs continuously illuminated in steady state | `0xAF048003` |
| **`4`** | **Night Light Half**| 3 LEDs illuminated in equilateral triangle (LED 1, LED 3, LED 5) | `0xAF04F004` |
| **`5`** | **Single Beacon** | Only LED 1 illuminated as a subtle low-glare night beacon | `0xAF04E005` |

---

## 7. State Machine, Non-Volatile Memory & Safety Rules

1. **Non-Volatile Speed Retention:**
   - Active speed ($1 \dots 6$) is automatically written to STM8 internal Data EEPROM at address `0x4001` using single-byte wear verification.
   - **The OFF state (`active_fan_level = 0`) never writes 0 to EEPROM.** The EEPROM always retains the user's preferred operating speed.
   - On AC mains restoration or `CMD_POWER_ON` / `CMD_TOGGLE_POWER`, the MCU reads the saved level and smoothly ramps back to that exact speed.
2. **Brownout & Power-Loss Immunity:**
   - The hardware BJT comparator on pin PC6 senses $24\,\text{V}$ rail drop below $16.5\,\text{V}$.
   - Debounced for $2.0\,\text{ms}$ to completely filter motor startup PWM transients while reacting in $< 2\,\text{ms}$ to genuine AC wall switch cuts.
3. **Overcurrent & Thermal Safety:**
   - Analog shunt on pin PD3 hardware-trips at $950$ ADC counts ($\approx 2.95\,\text{A}$ peak), cutting drive and latching LED 6.
   - DRV10983 junction temperature over $155^\circ\text{C}$ locks the driver into a 45-second cooling state before accepting any speed commands.

---

## 8. Reference RP2040 Driver Code

### 8.1 MicroPython Implementation (for Raspberry Pi Pico)

```python
"""
NodeVAYU Actuation Interface Driver (MicroPython)
Platform: RP2040 (Raspberry Pi Pico) on GPIO 16
"""

import time
from machine import Pin, PWM

class NodeVayuActuator:
    ADDR = 0xAF
    CMD_ALL_OFF        = 0x00
    CMD_SET_RAW_SPEED  = 0x01
    CMD_SET_LEVEL      = 0x02
    CMD_TOGGLE_POWER   = 0x03
    CMD_SET_LIGHT_MODE = 0x04
    CMD_STEP_UP        = 0x05
    CMD_STEP_DOWN      = 0x06
    CMD_POWER_ON       = 0x07

    def __init__(self, tx_pin: int = 16):
        self.pwm = PWM(Pin(tx_pin))
        self.pwm.freq(38000)
        self.pwm.duty_u16(0)
        self.DUTY_33 = int(65535 * 0.33)

    def encode_packet(self, cmd: int, data_l: int, data_h_low: int = 0) -> int:
        crc = (self.ADDR ^ cmd ^ data_h_low ^ data_l) & 0x0F
        data_h = (crc << 4) | (data_h_low & 0x0F)
        return (self.ADDR << 24) | (cmd << 16) | (data_h << 8) | (data_l & 0xFF)

    @micropython.native
    def transmit_packet(self, packet: int):
        # 1. Leader (9000us ON, 4500us OFF)
        self.pwm.duty_u16(self.DUTY_33)
        time.sleep_us(9000)
        self.pwm.duty_u16(0)
        time.sleep_us(4500)

        # 2. 32 Data bits (MSB first)
        for bit in range(31, -1, -1):
            self.pwm.duty_u16(self.DUTY_33)
            time.sleep_us(560)
            self.pwm.duty_u16(0)
            if (packet >> bit) & 0x01:
                time.sleep_us(1690)
            else:
                time.sleep_us(560)

        # 3. Stop bit (560us ON)
        self.pwm.duty_u16(self.DUTY_33)
        time.sleep_us(560)
        self.pwm.duty_u16(0)
        
        # 4. Mandatory inter-frame cooldown
        time.sleep_ms(40)

    def set_speed_rpm(self, target_rpm: float):
        """Continuous stepless speed control for NodeVAYU TinyML loop."""
        counts = max(0, min(511, round((target_rpm * 511.0) / 1450.0)))
        pkt = self.encode_packet(
            self.CMD_SET_RAW_SPEED,
            data_l=counts & 0xFF,
            data_h_low=(counts >> 8) & 0x01
        )
        self.transmit_packet(pkt)

    def set_level(self, level: int):
        """Discrete level 1..6 (0 = off)."""
        if level == 0:
            self.transmit_packet(self.encode_packet(self.CMD_ALL_OFF, 0x00))
        elif 1 <= level <= 6:
            self.transmit_packet(self.encode_packet(self.CMD_SET_LEVEL, level))

    def toggle_power(self):
        self.transmit_packet(self.encode_packet(self.CMD_TOGGLE_POWER, 0x00))

    def power_on(self):
        self.transmit_packet(self.encode_packet(self.CMD_POWER_ON, 0x00))

    def set_light_mode(self, mode: int):
        if 0 <= mode <= 5:
            self.transmit_packet(self.encode_packet(self.CMD_SET_LIGHT_MODE, mode))
```

### 8.2 C / C++ Reference Function (for ESP-IDF or Pico-SDK)

```c
#include <stdint.h>

#define FAN_IR_ADDR 0xAF

uint32_t nodevayu_build_packet(uint8_t cmd, uint8_t data_l, uint8_t data_h_low) {
    uint8_t crc = (FAN_IR_ADDR ^ cmd ^ data_h_low ^ data_l) & 0x0F;
    uint8_t data_h = (crc << 4) | (data_h_low & 0x0F);
    return ((uint32_t)FAN_IR_ADDR << 24) |
           ((uint32_t)cmd << 16) |
           ((uint32_t)data_h << 8) |
           (uint32_t)data_l;
}
```

