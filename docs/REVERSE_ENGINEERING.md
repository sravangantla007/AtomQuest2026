# Reverse Engineering & Firmware Mod: Atomberg Renesa BLDC Fan
**Project:** NodeVAYU — The Remote-Free Climate Intelligence Node  
**Event:** atomQUEST 2026 | Track 1: Air, Reimagined for the Next Decade  
**Author:** Gantla Venkata Sravan (EE24B013), Dept. of Electrical Engineering, IIT Tirupati  
**Repository:** [github.com/sravangantla007/AtomQuest2026](https://github.com/sravangantla007/AtomQuest2026)  

---

## 1. Executive Summary & Objective

Commercial smart fans rely on proprietary cloud gateways, mobile apps, or basic discrete infrared remotes (Levels 1 to 6). To transform an off-the-shelf **Atomberg BLDC ceiling fan** into **NodeVAYU**—an autonomous, remote-free climate intelligence node driven by mmWave radar and TinyML—we performed complete silicon-level reverse engineering of the motor controller PCB.

By extracting the factory binary over SWIM, analyzing the disassembled machine code, and sniffing the real-time I2C bus traffic to the 3-phase motor driver, we completely bypassed the proprietary closed-source firmware and built an open-source, continuous-speed motor actuation firmware with sub-millisecond safety latches and non-volatile state preservation.

```
+-----------------------------------------------------------------------------------+
|                            REVERSE ENGINEERING PIPELINE                           |
+-----------------------------------------------------------------------------------+
|  1. Teardown & Silicon Identification: STM8S003F3 MCU + TI DRV10983 3-Phase BLDC  |
|                                         │                                         |
|  2. Firmware Extraction (SWIM Protocol): 8 KB Flash + 128B EEPROM + Option Bytes  |
|                                         │                                         |
|  3. Static Analysis: Full Disassembly to Assembly (fan_firmware_disassembly.asm)  |
|                                         │                                         |
|  4. Dynamic Protocol Sniffing: Saleae Logic analyzer on I2C bus (SDA/SCL)         |
|     - Captured Motor Startup Sequence, Steady State Polling, & Power Loss Rails   |
|                                         │                                         |
|  5. Custom Firmware Development: NodeVAYU Actuation Core (PlatformIO + SDCC)      |
|     - Continuous 9-bit Speed Control (0..511 counts <=> 0..1450 RPM)              |
|     - 38 kHz Pulse-Distance IR Demodulation (Address 0xAF, 4-bit CRC-4)           |
|     - 6 Circular HUD Lighting Modes & Single-Byte Wear-Free EEPROM Retention      |
|                                         │                                         |
|  6. Binary Artifact Generation: Flash HEX & raw BIN (96.8% Flash Utilization)     |
+-----------------------------------------------------------------------------------+
```

---

## 2. Hardware Architecture & PCB Teardown

Physical dissection of the Atomberg motor control canopy board revealed the following components:

```
                                  +24V BLDC Rail
                                        │
                                        ▼
             ┌─────────────────────────────────────────────────────┐
             │      PC6 Comparator (16.5V Zener + BJT Inverter)    │
             │       - Fast Hardware Vbus Drop / Power-Loss Sense  │
             └──────────────────────────┬──────────────────────────┘
                                        │
                                        ▼
  ┌──────────────────────┐         ┌───────────────────────────┐         ┌──────────────────────┐
  │   38 kHz Demodulator │         │        STM8S003F3P6       │   I2C   │      TI DRV10983     │
  │   IR Sensor (Active  ├────────►│     Main Microcontroller  ├────────►│  3-Phase Sensorless  │
  │   LOW on Pin PA3)    │         │ (8KB Flash, 1KB SRAM)     │         │      BLDC Driver     │
  └──────────────────────┘         └─────────────┬─────────────┘         └──────────┬───────────┘
                                                 │                                  │
                                                 │ 6 Circular LEDs                  │ 3-Phase (U,V,W)
                                                 ▼                                  ▼
                                    ┌─────────────────────────┐            ┌──────────────────┐
                                    │ Circular LED Ring HUD   │            │ BLDC Motor Coils │
                                    │ (PA1, PC5, PC4, PC3,    │            └──────────────────┘
                                    │  PC7, PD2)              │
                                    └─────────────────────────┘
```

### Complete Pin Assignment Matrix

| STM8 Pin | Hardware Function | Peripheral / Electrical Connection | Role in NodeVAYU Firmware |
| :---: | :--- | :--- | :--- |
| **PA1** | LED 1 (Output) | Direct Drive (Active HIGH) | Circular HUD LED 1 / Vbus Good / Speed Level 1 |
| **PA3** | IR Demodulator (Input) | TSOP/Demodulator output (Active LOW) | Pulse-Distance 32-bit frame capture (`0xAF` protocol) |
| **PC3** | LED 4 (Output) | Direct Drive (Active HIGH) | Circular HUD LED 4 / Steady State Indicator / Level 4 |
| **PC4** | LED 3 (Output) | Direct Drive (Active HIGH) | Circular HUD LED 3 / Accel Ramping / Level 3 |
| **PC5** | LED 2 (Output) | Direct Drive (Active HIGH) | Circular HUD LED 2 / I2C Bus Indicator / Level 2 |
| **PC6** | Fast Power Sense (Input)| 16.5V Zener + NPN BJT Inverter | Vbus Brownout / AC Wall-Cut Sense (< 2ms cutoff) |
| **PC7** | LED 5 (Output) | Direct Drive (Active HIGH) | Circular HUD LED 5 / Fault (Stall/Temp) / Level 5 |
| **PD2** | LED 6 (Output) | Direct Drive (Active HIGH) | Circular HUD LED 6 / Overcurrent Trip / Level 6 |
| **PD3** | Current Sense (Analog) | AIN4 / Op-Amp output across low-side shunt | Hardware Overcurrent Trip at 950 counts (~2.95A) |
| **PD5** | UART1 TX (Output) | 57,600 baud, 8N1 | Diagnostic Telemetry & Motor Register Logging |
| **PB4** | I2C SCL | Open Drain with pull-up | Clock line to TI DRV10983 driver |
| **PB5** | I2C SDA | Open Drain with pull-up | Data line to TI DRV10983 driver |

---

## 3. Reverse-Engineering the Factory Baseline

### 3.1 Binary Extraction via SWIM
Using an ST-Link V2 programmer and OpenOCD/`stm8flash` via the Single Wire Interface Module (SWIM), the factory memory was extracted:
* **Flash ROM:** `0x8000` to `0x9FFF` (8,192 bytes) $\rightarrow$ [`binaries/factory_dumps/fan_firmware_original.bin`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/fan_firmware_original.bin)
* **Data EEPROM:** `0x4000` to `0x407F` (128 bytes) $\rightarrow$ [`binaries/factory_dumps/fan_eeprom_original.bin`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/fan_eeprom_original.bin)
* **Option Bytes:** `0x4800` to `0x483F` (64 bytes) $\rightarrow$ [`binaries/factory_dumps/fan_opt.bin`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/fan_opt.bin)
* **Static Disassembly:** Full assembly generation via SDCC `sdasstm8` $\rightarrow$ [`binaries/factory_dumps/fan_firmware_disassembly.asm`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/binaries/factory_dumps/fan_firmware_disassembly.asm)

### 3.2 I2C Protocol Sniffing with Saleae Logic
To understand how the STM8 initializes and commands the TI DRV10983 driver, logic analyzer probes were attached to PB4 (SCL) and PB5 (SDA). Multiple operational scenarios were logged:
1. **Motor Startup & Normal Operation:** [`captures/logic_traces/atomberg i2c with motor.sal`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/captures/logic_traces/atomberg%20i2c%20with%20motor.sal)
2. **Bench Testing without Motor Connected (Fault Handling):** [`captures/logic_traces/atomberg i2c nomotor.sal`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/captures/logic_traces/atomberg%20i2c%20nomotor.sal)
3. **Power-Cut Transient at ~40s:** [`captures/logic_traces/atomberg i2c with motor switch off at ~40 sec.sal`](file:///c:/Users/gvsra/OneDrive/Documents/GitHub/AtomQuest2026/captures/logic_traces/atomberg%20i2c%20with%20motor%20switch%20off%20at%20~40%20sec.sal)

### 3.3 Key Findings from I2C Decoding
* **Driver I2C Address:** `0x52` (7-bit address)
* **Speed Register Mapping:**
  - Register `0x00` (`SpeedCtrl1`): Bit 0 holds Speed[8] (MSB).
  - Register `0x01` (`SpeedCtrl2`): Bits 7..0 hold Speed[7:0] (LSB).
  - Total resolution: 9-bit continuous range ($0 \dots 511$).
* **Closed-Loop Speed Feedback:**
  - Read from Registers `0x11` and `0x12` (`MotorSpeed1` and `MotorSpeed2`).
  - Motor Electrical Period $T_{\text{period}} = (\text{Reg}[0\text{x}11] \ll 8) \mid \text{Reg}[0\text{x}12]$.
* **Fault Detection:**
  - Register `0x10` (`SysOptStatus`): Bit 0 = Lock Detection (Motor Stall), Bit 4 = Current Limit, Bit 5 = Over Temperature.

---

## 4. The New NodeVAYU Firmware Implementation

Using the decoded register maps and timings, we authored an entirely new firmware in C for the STM8S003F3:

### 4.1 Continuous 9-Bit Speed Control Loop
Rather than restricting the fan to 6 coarse factory speeds, the new firmware exposes a 9-bit stepless API ($0 \dots 511$):
$$\text{Speed Counts} = \text{round}\left(\frac{\text{RPM}_{\text{target}} \times 511}{1450}\right)$$

### 4.2 Pulse-Distance Demodulator (`0xAF` Protocol)
The active-LOW demodulator signal on PA3 is decoded with a tight non-blocking state machine:
* Header: $9000\,\mu\text{s}$ mark + $4500\,\mu\text{s}$ space
* Bit 0: $560\,\mu\text{s}$ mark + $560\,\mu\text{s}$ space ($1120\,\mu\text{s}$)
* Bit 1: $560\,\mu\text{s}$ mark + $1690\,\mu\text{s}$ space ($2250\,\mu\text{s}$)
* 4-Bit Longitudinal CRC:
  $$\text{CRC} = (\text{ADDR} \oplus \text{CMD} \oplus \text{DATA\_H\_LOW} \oplus \text{DATA\_L}) \ \& \ 0\text{x}0\text{F}$$

### 4.3 Wear-Free EEPROM State Engine
* Speed state is saved to Data EEPROM address `0x4001`.
* **Zero-Write on Power-Off:** When `CMD_ALL_OFF` is received, the fan motor stops, but EEPROM retains the user's last running speed. On power-on, the fan automatically ramps back to the previous operating speed.

### 4.4 Hardware Safety Latches
1. **Brownout Cutoff:** Pin PC6 triggers within $<2\,\text{ms}$ when the 24V bus drops below 16.5V, smoothly parking the DRV10983 before logic rails collapse.
2. **Overcurrent Trip:** Analog pin PD3 reads the low-side shunt; if ADC $> 950$ (~$2.95\,\text{A}$ peak), the driver is immediately set to Hi-Z coasting and LED 6 latches to indicate a trip.

---

## 5. Build Metrics & Binary Artifacts

The custom firmware compiles under SDCC with PlatformIO:
* **RAM Usage:** 292 bytes / 1,024 bytes (**28.5%**)
* **Flash Usage:** 7,929 bytes / 8,192 bytes (**96.8%**)

### Flashing the New Firmware

To flash NodeVAYU onto the fan board using ST-Link V2:
```bash
# Using stm8flash on Windows / Linux:
stm8flash -c stlinkv2 -p stm8s003f3 -w binaries/nodevayu_release/nodevayu_fan_firmware.hex
```

### Restoring Original Factory Firmware

To revert to the stock factory firmware at any time:
```bash
stm8flash -c stlinkv2 -p stm8s003f3 -w binaries/factory_dumps/fan_firmware_original.bin
```
