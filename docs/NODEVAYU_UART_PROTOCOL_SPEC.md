# NodeVAYU: Host $\leftrightarrow$ Supervisor UART Protocol Specification

**Document Version:** 1.0.0  
**Project:** NodeVAYU — The Remote-Free Climate Intelligence Node  
**Event:** atomQUEST 2026 | Track 1: Air, Reimagined for the Next Decade  
**Target Hardware:** Raspberry Pi Pico (RP2040) $\leftrightarrow$ Host Machine (PC / Dashboard Server)  
**Date:** November 2026  

---

## 1. Physical & Data Link Layer

* **Interface:** USB Virtual COM Port (CDC-ACM) or Hardware UART (`UART0` / `UART1`)
* **Baud Rate:** `115,200` bps
* **Data Bits:** `8`
* **Parity:** None (`N`)
* **Stop Bits:** `1`
* **Flow Control:** None
* **Framing / Delimiter:** Line-delimited (`\n` / ASCII `0x0A` or `\r\n`)

---

## 2. Telemetry Stream: RP2040 Supervisor $\to$ Host

The RP2040 supervisor continuously streams structured telemetry frames at **$5\,\text{Hz}$ ($200\,\text{ms}$ interval)** or **$10\,\text{Hz}$ ($100\,\text{ms}$ interval)**. Each frame is a single-line UTF-8 JSON object.

### 2.1 Telemetry JSON Schema

```json
{
  "type": "telemetry",
  "ts": 1730745230,
  "uptime_ms": 28450,
  "env": {
    "temp_c": 28.45,
    "rh_pct": 58.20,
    "heat_index_c": 30.12,
    "dew_point_c": 19.34
  },
  "radar": {
    "connected": true,
    "total_frames": 1420,
    "targets": [
      {
        "id": 1,
        "x_mm": 350,
        "y_mm": 2100,
        "dist_m": 2.13,
        "speed_cms": -14,
        "res_mm": 750
      }
    ]
  },
  "fan": {
    "power": true,
    "level": 3,
    "counts": 330,
    "rpm": 1050,
    "mode": 1,
    "last_packet": "0xAF02E003",
    "last_crc": 14
  }
}
```

### 2.2 Telemetry Fields Definition

| Path | Type | Unit | Description |
| :--- | :---: | :---: | :--- |
| `type` | `string` | — | Constant `"telemetry"` for packet discriminator. |
| `uptime_ms` | `uint32` | ms | Supervisor system uptime in milliseconds. |
| **`env.temp_c`** | `float` | °C | Ambient temperature from HW-220 / HTU21D (Resolution: 0.01°C). |
| **`env.rh_pct`** | `float` | % | Relative humidity from HW-220 / HTU21D (Range: $0 \dots 100\%$). |
| **`env.heat_index_c`** | `float` | °C | Rothfusz / Steadman heat index (Apparent temperature felt by occupant). |
| **`env.dew_point_c`** | `float` | °C | Calculated dew point temperature. |
| **`radar.connected`** | `bool` | — | `true` if UART0 receiving valid LD2450 30-byte frames. |
| **`radar.total_frames`** | `uint32` | — | Cumulative count of valid 30-byte mmWave tracking frames decoded. |
| **`radar.targets`** | `array` | — | Up to 3 simultaneous tracked targets (`id: 1..3`). Empty array `[]` if field clear. |
| `targets[].id` | `uint8` | — | Target tracking ID ($1 \dots 3$). |
| `targets[].x_mm` | `int16` | mm | Lateral coordinate relative to sensor normal axis ($-6000 \dots +6000$). Negative = Left, Positive = Right. |
| `targets[].y_mm` | `int16` | mm | Radial distance along boresight ($0 \dots 6000\,\text{mm}$). |
| `targets[].dist_m` | `float` | m | Euclidean distance $\sqrt{x^2 + y^2} / 1000.0$. |
| `targets[].speed_cms` | `int16` | cm/s | Radial velocity. Negative = Approaching sensor, Positive = Moving away. |
| `targets[].res_mm` | `uint16` | mm | Distance resolution / signal energy gate. |
| **`fan.power`** | `bool` | — | `true` if motor is energized/spinning; `false` if coasting/off. |
| **`fan.level`** | `uint8` | — | Discrete fan speed level ($0 = \text{OFF}$, $1 \dots 6$). |
| **`fan.counts`** | `uint16` | counts | Continuous 9-bit DRV10983 register counts ($0 \dots 511$). |
| **`fan.rpm`** | `uint16` | RPM | Estimated motor shaft speed ($0 \dots 1450\,\text{RPM}$). |
| **`fan.mode`** | `uint8` | — | Active circular LED HUD lighting mode ($0 \dots 5$). |
| **`fan.last_packet`** | `string` | Hex | Exact 32-bit big-endian hex packet transmitted over 38 kHz IR. |
| **`fan.last_crc`** | `uint8` | — | Extracted 4-bit CRC ($0 \dots 15$) of the last transmitted frame. |

---

## 3. Command Interface: Host $\to$ RP2040 Supervisor

Commands sent from the Host to the Pico use **line-delimited ASCII strings** (case-insensitive, whitespace-tolerant).

### 3.1 Command Matrix

| Command String | Parameters | Example | Resulting Action & 32-Bit IR Packet |
| :--- | :--- | :--- | :--- |
| **`speed <n>`** | `n` $\in [0 \dots 511]$ | `speed 330` | Sets continuous 9-bit DRV10983 throttle. Packet: `0xAF01` + `(CRC<<4\|spd>>8)` + `spd_low`. |
| **`level <n>`** | `n` $\in [0 \dots 6]$ | `level 3` | Sets discrete speed level. Level 0 turns fan OFF. |
| **`on`** | None | `on` | Powers ON the fan, restoring last configured speed (`0xAF078000`). |
| **`off`** | None | `off` | Turns fan OFF; coasts to stop without clearing saved speed (`0xAF00F000`). |
| **`toggle`** | None | `toggle` | Toggles fan power state (`0xAF03C000`). |
| **`+`** or **`step_up`** | None | `+` | Steps motor speed up by 1 discrete level (`0xAF05A000`). |
| **`-`** or **`step_down`**| None | `-` | Steps motor speed down by 1 discrete level (`0xAF069000`). |
| **`mode <m>`** | `m` $\in [0 \dots 5]$ | `mode 1` | Sets circular LED HUD mode (`0xAF04` + `(CRC<<4)` + `mode`). |
| **`carrier <sec>`** | `sec` $\in [0.1 \dots 10.0]$ | `carrier 3` | Emits steady 38.0 kHz square wave for optical camera check. |
| **`repeat <r>`** | `r` $\in [1 \dots 5]$ | `repeat 2` | Configures frame repeat count per transmission (default: 1). |

### 3.2 Lighting Mode Map

* **`mode 0`**: **Diagnostic HUD** (LED 1: Vbus, LED 2: I2C, LED 3: Ramp, LED 4: Steady, LED 5: Fault, LED 6: Overcurrent).
* **`mode 1`**: **Speed Level Gauge** (Default: 1 to 6 LEDs matching speed; 0 LEDs when stopped).
* **`mode 2`**: **Stealth Dark** (All LEDs OFF in steady state; wakes 3s on IR command).
* **`mode 3`**: **Night Light Full** (All 6 LEDs continuously illuminated).
* **`mode 4`**: **Night Light Half** (3 LEDs in equilateral triangle pattern: LEDs 1, 3, 5).
* **`mode 5`**: **Single Beacon** (Only LED 1 subtly illuminated).

### 3.3 Command Acknowledgment (RP2040 $\to$ Host)

Upon receiving and executing a command, the Pico responds with an ACK line:

```json
{"type":"ack","cmd":"speed 330","status":"ok","packet":"0xAF014A4A","crc":4}
```

If an invalid command or parameter is provided:

```json
{"type":"ack","cmd":"speed 600","status":"error","message":"Value out of range (0-511)"}
```

---

## 4. Optical 32-Bit IR Packet Construction (Reference)

For all motor commands transmitted over the 38.0 kHz optical link:

$$\text{CRC} = (\text{ADDR} \oplus \text{CMD} \oplus \text{DATA\_H\_LOW} \oplus \text{DATA\_L}) \ \& \ \texttt{0x0F}$$
$$\text{DATA\_H} = (\text{CRC} \ll 4) \mid (\text{DATA\_H\_LOW} \ \& \ \texttt{0x0F})$$
$$\text{Packet} = (\text{ADDR} \ll 24) \mid (\text{CMD} \ll 16) \mid (\text{DATA\_H} \ll 8) \mid \text{DATA\_L}$$

Where $\text{ADDR} = \texttt{0xAF}$.
