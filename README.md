# NodeVAYU: The Remote-Free Climate Intelligence Node

**Event:** atomQUEST 2026 | Track 1: Air, Reimagined for the Next Decade  
**Author:** Gantla Venkata Sravan (EE24B013), Dept. of Electrical Engineering, IIT Tirupati  
**Repository:** [github.com/sravangantla007/AtomQuest2026](https://github.com/sravangantla007/AtomQuest2026)  

---

## 1. Project Overview & The Story Behind NodeVAYU

Commercial smart fans rely on cumbersome mobile applications, cloud gateways, or basic IR remotes with 6 coarse speeds. **NodeVAYU** reimagines air comfort by transforming an off-the-shelf **Atomberg Renesa BLDC ceiling fan** into an autonomous, remote-free climate intelligence node.

To unlock full continuous motor control ($0 \dots 1450\,\text{RPM}$) without cloud dependencies:
1. **Silicon Reverse Engineering:** We tapped into the STM8S003F3 microcontroller via SWIM, dumped the factory firmware, disassembled the machine code, and sniffed the real-time I2C bus traffic between the MCU and the Texas Instruments DRV10983 3-phase sensorless motor driver using a Saleae Logic analyzer.
2. **Custom Open-Source Actuation Core:** We wrote a brand new firmware from scratch in C for the STM8S003F3 that replaces the factory proprietary code. It introduces continuous 9-bit speed control ($0 \dots 511$), a custom 38 kHz optical pulse-distance protocol with 4-bit CRC, wear-free EEPROM retention, and sub-millisecond hardware safety latches.
3. **Edge AI Supervisor (RP2040):** An intelligent base station combining a **24 GHz FMCW Radar** (HLK-LD2450) and a **Digital Temperature & Humidity Sensor** (HW-220 / HTU21D) runs a TinyML comfort engine that adjusts fan RPM based on occupant presence, range, and environmental heat index over the 38 kHz optical link.

```
┌────────────────────────────────────────────────────────┐
│          NodeVAYU Edge AI Supervisor (RP2040)          │
│   • 24 GHz FMCW Radar (HLK-LD2450 via UART0)           │
│   • Factory-calibrated Digital T/RH Sensor (HW-220)    │
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

## 2. Silicon-Level Reverse Engineering & Binary Artifacts

For complete transparency and reproducible engineering, the repository preserves both the **dumped factory baseline** and the **new compiled NodeVAYU binaries**:

### 2.1 Factory Baseline Dumps (Stock Atomberg Controller)

| File | Size | Description |
| :--- | :---: | :--- |
| [`binaries/factory_dumps/fan_firmware_original.bin`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/fan_firmware_original.bin) | 8 KB | Raw Flash ROM dump (`0x8000`–`0x9FFF`) via ST-Link SWIM |
| [`binaries/factory_dumps/fan_eeprom_original.bin`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/fan_eeprom_original.bin) | 128 B | Stock Data EEPROM dump (`0x4000`–`0x407F`) |
| [`binaries/factory_dumps/fan_opt.bin`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/fan_opt.bin) | 64 B | Factory Option Bytes (`0x4800`–`0x483F`) |
| [`binaries/factory_dumps/fan_firmware_disassembly.asm`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/fan_firmware_disassembly.asm) | ~202 KB | Complete static assembly disassembly of original firmware |
| [`binaries/factory_dumps/factory_firmware_backup.bin`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/factory_firmware_backup.bin) | 8 KB | Verified secondary backup dump |

### 2.2 New Generated Binaries (Custom NodeVAYU Actuation Core)

| File | Size | Description |
| :--- | :---: | :--- |
| [`binaries/nodevayu_release/nodevayu_fan_firmware.bin`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/nodevayu_release/nodevayu_fan_firmware.bin) | 8 KB | Raw flash binary ready for flashing to `0x8000` |
| [`binaries/nodevayu_release/nodevayu_fan_firmware.hex`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/nodevayu_release/nodevayu_fan_firmware.hex) | 22.4 KB | Intel HEX binary for ST-Link / `stm8flash` / OpenOCD |
| [`binaries/nodevayu_release/nodevayu_fan_firmware.elf`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/nodevayu_release/nodevayu_fan_firmware.elf) | 14.6 KB | ELF executable with complete debug symbols |
| [`binaries/nodevayu_release/nodevayu_fan_firmware.map`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/nodevayu_release/nodevayu_fan_firmware.map) | 18.5 KB | Linker map: **96.8% Flash** (7,929 / 8,192 B), **28.5% RAM** (292 / 1,024 B) |

### 2.3 Logic Analyzer Captures & I2C Traces

| Capture File | Format | Event / Scenario Captured |
| :--- | :---: | :--- |
| [`captures/logic_traces/atomberg i2c with motor.sal`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/captures/logic_traces/atomberg%20i2c%20with%20motor.sal) | Saleae .sal | Full motor startup ramp, speed transitions, and steady state |
| [`captures/logic_traces/atomberg i2c nomotor.sal`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/captures/logic_traces/atomberg%20i2c%20nomotor.sal) | Saleae .sal | Bench test without motor coils (fault status register polling) |
| [`captures/logic_traces/atomberg i2c with motor switch off at ~40 sec.sal`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/captures/logic_traces/atomberg%20i2c%20with%20motor%20switch%20off%20at%20~40%20sec.sal) | Saleae .sal | Mains power cut at 40s mark: captures BJT comparator cutoff |
| [`captures/csv/`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/captures/csv/) | CSV | Decoded I2C transactions (Read/Write registers to DRV10983 at `0x52`) |

*For full engineering details, see the [Reverse Engineering Technical Dossier](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/docs/REVERSE_ENGINEERING.md).*

---

## 3. Hardware Wiring Map (RP2040 Supervisor)

The NodeVAYU base station interfaces the mmWave radar, digital sensor, and IR transmitter on the Raspberry Pi Pico (RP2040):

| Subsystem | Signal | Pico GPIO | Pico Physical Pin | Peripheral / Notes |
| :--- | :--- | :---: | :---: | :--- |
| **HLK-LD2450 Radar** | **RX** (Radar Input) | **GP28** | Pin 34 | RP2040 UART0 TX (`256,000` baud) |
| | **TX** (Radar Output)| **GP29** | Pin 35 | RP2040 UART0 RX (`256,000` baud) |
| | **VCC** (5V) | **VBUS** | Pin 40 | Requires 5V supply (~200mA peak) |
| | **GND** | **GND** | Pin 38 | System Ground |
| **HW-220 (HTU21D)** | **SDA** | **GP26** | Pin 31 | RP2040 I2C1 SDA (`100` kHz, Addr: `0x40`) |
| | **SCL** | **GP27** | Pin 32 | RP2040 I2C1 SCL (`100` kHz) |
| | **VCC** (3.3V) | **3V3 OUT**| Pin 36 | 1.5V – 3.6V logic supply |
| | **GND** | **GND** | Pin 33 | System Ground |
| **IR Transmitter** | **IR Data Out** | **GP16** | Pin 21 | 38.0 kHz Hardware PWM (33% duty cycle) |
| | **GND** | **GND** | Pin 23 / 28 | IR Driver Ground |
| **Activity Indicator** | **LED** | **GP25** / `LED` | – | Onboard activity blinker |

---

## 4. Hardware Verification Firmware

A self-contained MicroPython script is provided at **[`main.py`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/main.py)** (also mirrored at [`firmware/rp2040_supervisor/main.py`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/firmware/rp2040_supervisor/main.py)):

### Testing Connections
1. Upload [`main.py`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/main.py) to the Raspberry Pi Pico via Thonny or `mpremote`.
2. Open the serial terminal at `115,200` baud:
   - **HW-220 Probe:** Confirms I2C address `0x40` responds, performs soft reset, and reads ambient $T$ (°C) and $RH$ (%).
   - **HLK-LD2450 Probe:** Opens UART0 at `256,000` baud and decodes 3D spatial target frames ($X, Y$ in mm, distance in m, radial velocity in cm/s).
   - **IR Actuation Link:** Initializes GP16 hardware PWM at $38.0\,\text{kHz}$ with $33\%$ duty cycle.
   - **Interactive Menu:** Press keys `1`–`6` (Speed Levels), `0` (Off), `p` (Toggle Power), or `b` (150ms carrier burst for phone camera check).

---

## 5. Repository Structure

```
AtomQuest2026/
├── binaries/
│   ├── factory_dumps/                  # Extracted original STM8 firmware, EEPROM & ASM
│   │   ├── fan_firmware_original.bin
│   │   ├── fan_eeprom_original.bin
│   │   ├── fan_opt.bin
│   │   └── fan_firmware_disassembly.asm
│   └── nodevayu_release/               # Compiled custom NodeVAYU STM8 firmware
│       ├── nodevayu_fan_firmware.bin
│       ├── nodevayu_fan_firmware.hex
│       ├── nodevayu_fan_firmware.elf
│       └── nodevayu_fan_firmware.map
├── captures/
│   ├── logic_traces/                   # Saleae Logic .sal I2C captures
│   │   ├── atomberg i2c with motor.sal
│   │   ├── atomberg i2c nomotor.sal
│   │   └── atomberg i2c with motor switch off at ~40 sec.sal
│   └── csv/                            # Decoded I2C register transaction logs
│       ├── with_motor_i2c.csv
│       ├── nomotor_i2c.csv
│       └── switch_off_i2c.csv
├── dashboard/
│   └── index.html                      # Real-time Web Dashboard (Radar, T/RH, Fan HUD, Web Serial)
├── docs/
│   ├── NODEVAYU_IR_INTERFACE_SPEC.md   # Official Hardware & Interface Control Document (ICD)
│   └── REVERSE_ENGINEERING.md          # Technical Dossier: Silicon Teardown & Protocol Analysis
├── firmware/
│   ├── rp2040_supervisor/              # RP2040 MicroPython & C Edge Firmware
│   │   └── main.py
│   └── stm8_fan_controller/            # Custom STM8S003F3 C Firmware (PlatformIO)
│       ├── platformio.ini
│       ├── include/drv10983.h
│       └── src/ (main.c, drv10983.c)
├── tools/
│   ├── analyze_i2c.py                  # I2C capture parsing script
│   ├── parse_map.py                    # Linker memory map analyzer
│   ├── parse_test.py                   # Protocol test harness
│   └── ir_tx.py                        # Standalone MicroPython IR driver
├── main.py                             # MicroPython verification script (Pico root)
├── verify_hardware.py                  # Verification CLI utility
└── README.md
```

---

## 6. How to Flash the Custom Fan Firmware

Using an ST-Link V2 programmer connected to the STM8 SWIM header:
```bash
# Flash NodeVAYU custom firmware:
stm8flash -c stlinkv2 -p stm8s003f3 -w binaries/nodevayu_release/nodevayu_fan_firmware.hex

# To revert back to original factory firmware:
stm8flash -c stlinkv2 -p stm8s003f3 -w binaries/factory_dumps/fan_firmware_original.bin
```
