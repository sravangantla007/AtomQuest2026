# NodeVAYU: The Remote-Free Climate Intelligence Node
**Event:** atomQUEST 2026 | Track 1: Air, Reimagined for the Next Decade  
**Author:** Gantla Venkata Sravan (EE24B013), Dept. of Electrical Engineering, IIT Tirupati  
**Repository:** [github.com/sravangantla007/AtomQuest2026](https://github.com/sravangantla007/AtomQuest2026)  

---

## 1. System Architecture

NodeVAYU pairs an **RP2040 Edge-AI Supervisor** with the **STM8S003F3 Actuation Core** inside an Atomberg BLDC fan:

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

## 2. Hardware Wiring Map (RP2040 Supervisor)

| Subsystem | Signal | Pico GPIO | Pico Physical Pin | Peripheral / Notes |
| :--- | :--- | :---: | :---: | :--- |
| **HLK-LD2450 Radar** | RX (Radar Input) | **GP28** | Pin 34 | UART0 TX (`256,000` baud) |
| | TX (Radar Output) | **GP29** | Pin 35 | UART0 RX (`256,000` baud) |
| | VCC (5V) | **VBUS** | Pin 40 | Requires 5V supply (~200mA peak) |
| | GND | **GND** | Pin 38 | System Ground |
| **HW-220 (HTU21D)** | SDA | **GP26** | Pin 31 | I2C1 SDA (`100` kHz, Addr: `0x40`) |
| | SCL | **GP27** | Pin 32 | I2C1 SCL (`100` kHz) |
| | VCC (3.3V) | **3V3 OUT** | Pin 36 | 1.5V – 3.6V logic supply |
| | GND | **GND** | Pin 33 | System Ground |
| **IR Transmitter** | IR Data Out | **GP16** | Pin 21 | 38.0 kHz Hardware PWM (33% duty) |
| | GND | **GND** | Pin 23 / 28 | IR Driver Ground |
| **Activity Indicator** | LED | **GP25** / `LED` | – | Onboard activity blinker |

---

## 3. Directory Layout

```
AtomQuest2026/
├── docs/
│   └── NODEVAYU_IR_INTERFACE_SPEC.md   # Official Hardware & Interface Control Document (ICD)
├── firmware/
│   ├── rp2040_supervisor/              # RP2040 MicroPython & C Edge Firmware
│   │   └── main.py                     # All-in-one hardware verification & live diagnostic script
│   └── stm8_fan_controller/            # Custom STM8S003F3 Fan Firmware (PlatformIO)
│       ├── platformio.ini
│       ├── include/drv10983.h
│       └── src/ (main.c, drv10983.c)
├── tools/
│   ├── ir_tx.py                        # Standalone MicroPython IR driver
│   └── analyze_i2c.py                  # Saleae Logic I2C analyzer script
├── main.py                             # MicroPython verification script (Pico root)
├── verify_hardware.py                  # Verification CLI utility
└── README.md
```

---

## 4. Hardware Verification Firmware

Run [`main.py`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/main.py) on the Raspberry Pi Pico to verify all connected hardware:

1. **HW-220 (HTU21D) Test:** Scans I2C1, queries address `0x40`, performs soft-reset, and streams real-time ambient temperature (°C) and relative humidity (%).
2. **HLK-LD2450 Radar Test:** Listens on UART0 at `256,000` bps, parses 30-byte tracking frames, and outputs 3D spatial coordinates ($X, Y$ in mm, distance in m, radial velocity in cm/s).
3. **IR Actuation Link:** Configures GP16 hardware PWM at 38.0 kHz (33% duty) and provides an interactive terminal keyboard menu to test fan commands (`0`=Off, `1`..`6`=Level, `p`=Toggle Power, `b`=Phone camera optical burst).
