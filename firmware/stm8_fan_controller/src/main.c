#include <Arduino.h>
#include <Wire.h>
#include <EEPROM.h>
#include "drv10983.h"

/**
 * ============================================================================
 * Atomberg BLDC Fan Mod: Complete Closed-Loop Motor Control & Diagnostic HUD
 * Custom Firmware by sravangantla007
 * Project Repo: https://github.com/sravangantla007/AtomQuest2026
 * ============================================================================
 * 
 * 6 Physical LEDs Assignment (Circular Order):
 *   LED 1 (PA1): Vbus Power Good / Morse Dot / Level 1 / Beacon
 *   LED 2 (PC5): I2C Link / Level 2
 *   LED 3 (PC4): Accel Ramping / Morse Dash Part 1 / Level 3 / Triangle 2
 *   LED 4 (PC3): Steady State Running / Level 4
 *   LED 5 (PC7): Fault (Stall / OverTemp) / Morse Dash Part 2 / Level 5 / Triangle 3
 *   LED 6 (PD2): Overcurrent Trip / Level 6
 * 
 * Inputs:
 *   PA3: IR Receiver (Demodulated 38 kHz NEC frame)
 *   PC6: 16.5V Zener + BJT Inverter (Hardware Vbus fast comparator)
 *   PD3: Low-side Current Shunt Op-Amp Output (AIN4 / A2)
 *   PD5: UART1 TX (57,600 baud, 8N1)
 */

#define PIN_LED_VBUS        PA1
#define PIN_LED_I2C         PC5
#define PIN_LED_STARTUP     PC4
#define PIN_LED_STEADY      PC3
#define PIN_LED_FAULT       PC7
#define PIN_LED_OVERCURRENT PD2

#define PIN_IR_RECV         PA3
#define PIN_VBUS_BJT        PC6
#define PIN_CURRENT_SENSE   PD3  // Analog AIN4 / A2

#define FAN_IR_ADDR         0xAF

// IR Command Set
#define CMD_ALL_OFF         0x00
#define CMD_SET_RAW_SPEED   0x01
#define CMD_SET_LEVEL       0x02
#define CMD_TOGGLE_POWER    0x03
#define CMD_SET_LIGHT_MODE  0x04
#define CMD_STEP_UP         0x05
#define CMD_STEP_DOWN       0x06
#define CMD_POWER_ON        0x07

// Active-HIGH LEDs
#define LED_ON  HIGH
#define LED_OFF LOW

// Direct Port Output Registers for low-overhead, zero-bloat pin control
#define PA_ODR_REG (*(volatile uint8_t*)0x5000)
#define PC_ODR_REG (*(volatile uint8_t*)0x500A)
#define PD_ODR_REG (*(volatile uint8_t*)0x500F)

// Direct register access for PA3 IR receiver
#define PA_IDR_REG (*(volatile uint8_t*)0x5001)
#define IR_PIN_IS_LOW()  ((PA_IDR_REG & 0x08) == 0)
#define IR_PIN_IS_HIGH() ((PA_IDR_REG & 0x08) != 0)

// Direct register access for PC6 Vbus BJT comparator
#define PC_IDR_REG (*(volatile uint8_t*)0x500B)
#define VBUS_BJT_POWER_LOSS() ((PC_IDR_REG & 0x40) != 0)

// Hardlocked Hardware Overcurrent Trip Threshold
// 950 counts = 3.06V on AIN4 (~2.95A peak motor current)
#define OC_THRESHOLD        950

// EEPROM Storage Map (Data EEPROM: 0x4000..0x407F)
#define EEPROM_MAGIC_VAL    0xA7
#define EEPROM_ADDR_MAGIC   0
#define EEPROM_ADDR_LEVEL   1
#define EEPROM_ADDR_MODE    2
#define EEPROM_ADDR_SPD_H   3
#define EEPROM_ADDR_SPD_L   4

// Lighting Modes
typedef enum {
  LIGHT_MODE_DEBUG          = 0, // Diagnostic HUD (Vbus, I2C, Startup, Steady, Fault, OC)
  LIGHT_MODE_LEVEL          = 1, // Speed mapped to 1..6 LEDs (Default; 0 LEDs when stopped)
  LIGHT_MODE_DARK           = 2, // Dark in steady state; animates & shows level on command then reverts
  LIGHT_MODE_NIGHT_FULL     = 3, // All 6 LEDs ON continuously in steady state
  LIGHT_MODE_NIGHT_TRIANGLE = 4, // 3 LEDs ON in equilateral triangle pattern (LED 1, 3, 5)
  LIGHT_MODE_SINGLE         = 5  // Single LED subtle night light (Only LED 1 ON steady)
} LightMode_t;

// Calibrated 9-bit speed presets (0 to 511)
static const uint16_t PRESET_SPEEDS[7] = {
  0,    // Level 0: OFF
  200,  // Level 1: Gentle Breeze (~650 RPM)
  265,  // Level 2: Low-Medium (~900 RPM)
  330,  // Level 3: Medium (~1050 RPM)
  395,  // Level 4: Medium-High (~1200 RPM)
  455,  // Level 5: High (~1350 RPM)
  511   // Level 6: Maximum Throttle (100% PWM)
};

static bool i2c_link_ok = false;
static bool vbus_healthy = true;
static bool overcurrent_tripped = false;
static uint8_t oc_debounce_cnt = 0;

static LightMode_t current_light_mode = LIGHT_MODE_LEVEL;
static uint8_t  active_fan_level      = 0;     // Current commanded speed level (0..6)
static uint16_t saved_user_speed      = 0;     // Last commanded speed (0..511)

// Safe single-byte EEPROM write with read-before-write check & 10ms hardware cycle settle
static void write_eeprom_if_changed(uint8_t addr, uint8_t val) {
  if (EEPROM_read(addr) != val) {
    EEPROM_write(addr, val);
    delay(10); // Guarantee 10ms for STM8 6.5ms tPROG cycle to complete safely
  }
}

static void save_settings_to_eeprom(void) {
  // Only persist operating speeds (never overwrite EEPROM with 0 / OFF)
  if (active_fan_level >= 1 && active_fan_level <= 6) {
    write_eeprom_if_changed(EEPROM_ADDR_MAGIC, EEPROM_MAGIC_VAL);
    write_eeprom_if_changed(EEPROM_ADDR_LEVEL, active_fan_level);
    write_eeprom_if_changed(EEPROM_ADDR_SPD_H, (uint8_t)((saved_user_speed >> 8) & 0x01));
    write_eeprom_if_changed(EEPROM_ADDR_SPD_L, (uint8_t)(saved_user_speed & 0xFF));
  }
  write_eeprom_if_changed(EEPROM_ADDR_MODE, (uint8_t)current_light_mode);
}

// UART1 Transmitter (57,600 baud, 8N1, TX on PD5)
static void uart_init_57600(void) {
  *(volatile uint8_t*)0x5233 = 0x06; // UART1_BRR2
  *(volatile uint8_t*)0x5232 = 0x11; // UART1_BRR1
  *(volatile uint8_t*)0x5235 = 0x08; // UART1_CR2: TEN (Transmitter enable)
}

static void uart_print(const char* s) {
  while (*s) {
    while (!((*(volatile uint8_t*)0x5230) & 0x80)); // Wait TXE
    *(volatile uint8_t*)0x5231 = *s++;              // UART1_DR
  }
}

// Low-level circular LED mask control via fast direct register writes:
// Bit 0 = LED 1 (PA1)
// Bit 1 = LED 2 (PC5)
// Bit 2 = LED 3 (PC4)
// Bit 3 = LED 4 (PC3)
// Bit 4 = LED 5 (PC7)
// Bit 5 = LED 6 (PD2)
static void set_led_mask(uint8_t mask) {
  // LED 1: PA1 (bit 0 -> bit 1 of Port A)
  if (mask & (1 << 0)) PA_ODR_REG |= 0x02; else PA_ODR_REG &= ~0x02;

  // LEDs 2..5 on Port C (Bits 3, 4, 5, 7):
  uint8_t c_mask = 0;
  if (mask & (1 << 1)) c_mask |= 0x20;
  if (mask & (1 << 2)) c_mask |= 0x10;
  if (mask & (1 << 3)) c_mask |= 0x08;
  if (mask & (1 << 4)) c_mask |= 0x80;
  PC_ODR_REG = (PC_ODR_REG & ~0xB8) | c_mask;

  // LED 6: PD2 (bit 5 -> bit 2 of Port D)
  if (mask & (1 << 5)) PD_ODR_REG |= 0x04; else PD_ODR_REG &= ~0x04;
}

// Speed bar gauge: turns on N consecutive LEDs (0 to 6)
static void set_led_bar(uint8_t n_leds) {
  uint8_t mask = 0;
  uint8_t i;
  if (n_leds > 6) n_leds = 6;
  for (i = 0; i < n_leds; i++) {
    mask |= (1 << i);
  }
  set_led_mask(mask);
}

// Convert 9-bit speed (0..511) to 0..6 LED count
static uint8_t speed_to_led_count(uint16_t spd) {
  if (spd == 0) return 0;
  if (spd < 235) return 1;
  if (spd < 300) return 2;
  if (spd < 365) return 3;
  if (spd < 425) return 4;
  if (spd < 485) return 5;
  return 6;
}

// Smooth counting transition animation from from_lvl to to_lvl
static void play_level_transition(uint8_t from_lvl, uint8_t to_lvl) {
  if (to_lvl > 6) to_lvl = 6;
  if (from_lvl > 6) from_lvl = 6;

  if (from_lvl == to_lvl) {
    set_led_mask(0x00);
    delay(40);
    set_led_bar(to_lvl);
    delay(500);
    return;
  }

  uint8_t cur = from_lvl;
  while (cur != to_lvl) {
    if (cur < to_lvl) {
      cur++;
    } else {
      cur--;
    }
    set_led_bar(cur);
    *(volatile uint8_t*)0x50E0 = 0xAA; // Kick IWDG
    delay(140); // 140ms per step gives smooth, deliberate visual counting
  }
  delay(800); // 800ms target confirmation hold before steady mode
}

// Sets a discrete speed level (0..6), persists state, and plays transition animation
static void apply_new_level(uint8_t new_lvl) {
  if (new_lvl > 6) new_lvl = 6;
  uint8_t old_lvl = active_fan_level;
  if (new_lvl == old_lvl) return;

  active_fan_level = new_lvl;
  if (new_lvl > 0) {
    saved_user_speed = PRESET_SPEEDS[new_lvl];
    save_settings_to_eeprom();
    drv10983_set_target_speed(saved_user_speed);
  } else {
    saved_user_speed = 0;
    drv10983_stop();
    // Do NOT write 0 to EEPROM! EEPROM retains the last chosen speed level.
  }
  play_level_transition(old_lvl, new_lvl);
}

static void init_all_pins(void) {
  pinMode(PIN_LED_VBUS, OUTPUT);
  pinMode(PIN_LED_I2C, OUTPUT);
  pinMode(PIN_LED_STARTUP, OUTPUT);
  pinMode(PIN_LED_STEADY, OUTPUT);
  pinMode(PIN_LED_FAULT, OUTPUT);
  pinMode(PIN_LED_OVERCURRENT, OUTPUT);

  set_led_mask(0x00);

  pinMode(PIN_IR_RECV, INPUT_PULLUP);
  pinMode(PIN_VBUS_BJT, INPUT_PULLUP);
}

// -------------------------------------------------------------
// Boot Sequence: Animation, Morse "RAVAN" & UART Banner
// -------------------------------------------------------------
static void play_boot_animation(void) {
  uint8_t c, i;
  // Phase 1: Rapid single-LED circular spin (2 cycles)
  for (c = 0; c < 2; c++) {
    for (i = 0; i < 6; i++) {
      set_led_mask(1 << i);
      delay(35);
    }
  }
  // Phase 2: Dual-LED trailing comet (1 cycle)
  for (i = 0; i < 6; i++) {
    uint8_t m = (1 << i) | (1 << ((i + 1) % 6));
    set_led_mask(m);
    delay(30);
  }
  // Phase 3: Bloom flash
  set_led_mask(0x3F);
  delay(80);
  set_led_mask(0x00);
  delay(50);
}

// Morse Timing: Dot = LED 1; Dash = LED 3 + LED 5 combined.
// Duration for RAVAN tuned to complete in 2.0 seconds (~2010 ms).
static void morse_dot(void) {
  set_led_mask(1 << 0);
  delay(60);
  set_led_mask(0x00);
  delay(40);
}

static void morse_dash(void) {
  set_led_mask((1 << 2) | (1 << 4));
  delay(130);
  set_led_mask(0x00);
  delay(40);
}

static void play_morse_ravan(void) {
  // R: . - . (370ms)
  morse_dot(); morse_dash(); morse_dot();
  delay(90);

  // A: . - (270ms)
  morse_dot(); morse_dash();
  delay(90);

  // V: . . . - (470ms)
  morse_dot(); morse_dot(); morse_dot(); morse_dash();
  delay(90);

  // A: . - (270ms)
  morse_dot(); morse_dash();
  delay(90);

  // N: - . (270ms)
  morse_dash(); morse_dot();
  delay(90);
}

/**
 * NEC IR Frame Decoder on PA3
 */
static uint8_t decode_nec_frame(uint32_t* out_packet) {
  uint32_t t_start;
  uint32_t raw_data = 0;
  uint8_t bit_idx;

  // 1. Leader Mark (~9000 us LOW)
  t_start = micros();
  while (IR_PIN_IS_LOW()) {
    if ((micros() - t_start) > 12000) return 0;
  }
  uint32_t leader_low = micros() - t_start;
  if (leader_low < 7000 || leader_low > 11000) return 0;

  // 2. Leader Space (~4500 us HIGH)
  t_start = micros();
  while (IR_PIN_IS_HIGH()) {
    if ((micros() - t_start) > 6000) return 0;
  }
  uint32_t leader_high = micros() - t_start;
  if (leader_high < 3000 || leader_high > 5500) return 0;

  // 3. Receive 32 Data Bits (MSB first)
  for (bit_idx = 0; bit_idx < 32; bit_idx++) {
    t_start = micros();
    while (IR_PIN_IS_LOW()) {
      if ((micros() - t_start) > 1200) return 0;
    }

    t_start = micros();
    while (IR_PIN_IS_HIGH()) {
      if ((micros() - t_start) > 2500) return 0;
    }
    uint32_t space_dur = micros() - t_start;

    raw_data <<= 1;
    if (space_dur > 1000) {
      raw_data |= 1;
    }
  }

  // 4. Verify Frame
  uint8_t addr   = (uint8_t)((raw_data >> 24) & 0xFF);
  uint8_t cmd    = (uint8_t)((raw_data >> 16) & 0xFF);
  uint8_t data_h = (uint8_t)((raw_data >> 8) & 0xFF);
  uint8_t data_l = (uint8_t)(raw_data & 0xFF);

  if (addr != FAN_IR_ADDR) return 0;

  uint8_t recv_crc = (data_h >> 4) & 0x0F;
  uint8_t data_h_low = data_h & 0x0F;
  uint8_t calc_crc = (addr ^ cmd ^ data_h_low ^ data_l) & 0x0F;

  if (recv_crc != calc_crc) return 0;

  *out_packet = raw_data;
  return 1;
}

// -------------------------------------------------------------
// Unified LED Display & Animation Engine
// -------------------------------------------------------------
static void update_led_display(uint32_t now, bool is_fault) {
  // 1. HARDWARE FAULT: Force Diagnostic HUD immediately
  if (is_fault) {
    uint8_t fault_mask = 0;
    if (vbus_healthy) fault_mask |= (1 << 0); // LED 1
    if (i2c_link_ok) {
      fault_mask |= (1 << 1); // LED 2 solid
    } else if ((now / 150) % 2) {
      fault_mask |= (1 << 1); // LED 2 blink if I2C dropped
    }
    if (drv10983_is_overtemp()) {
      uint16_t phase = (now % 1000);
      if ((phase < 120) || (phase > 240 && phase < 360)) {
        fault_mask |= (1 << 4); // LED 5 double blink for OverTemp lockout
      }
    } else if (drv10983_get_state() == MOTOR_STATE_FAULT) {
      fault_mask |= (1 << 4); // LED 5 solid for rotor stall
    }
    if (overcurrent_tripped) {
      fault_mask |= (1 << 5); // LED 6 solid for overcurrent trip
    }
    set_led_mask(fault_mask);
    return;
  }

  // 2. ACTIVE STEADY LIGHTING MODE
  switch (current_light_mode) {
    case LIGHT_MODE_LEVEL:
      // Level Gauge: Shows 1..6 LEDs when spinning.
      // IF MOTOR IS STOPPED / NOT SPINNING: NO LEDS GLOW (0 LEDs)!
      if (drv10983_get_state() == MOTOR_STATE_STOPPED || active_fan_level == 0) {
        set_led_mask(0x00);
      } else {
        set_led_bar(active_fan_level);
      }
      break;

    case LIGHT_MODE_DARK:
      // Dark in steady state
      set_led_mask(0x00);
      break;

    case LIGHT_MODE_NIGHT_FULL:
      // All 6 LEDs ON continuously
      set_led_mask(0x3F);
      break;

    case LIGHT_MODE_NIGHT_TRIANGLE:
      // Triangle pattern: LEDs 1, 3, 5 ON (Pins PA1, PC4, PC7)
      set_led_mask((1 << 0) | (1 << 2) | (1 << 4));
      break;

    case LIGHT_MODE_SINGLE:
      // Single subtle LED beacon: Only LED 1 ON (Pin PA1)
      set_led_mask(1 << 0);
      break;

    case LIGHT_MODE_DEBUG:
      {
        uint8_t dbg_mask = (1 << 0) | (1 << 1); // LED 1 (Vbus) + LED 2 (I2C)
        MotorState_t st = drv10983_get_state();
        if (st == MOTOR_STATE_STARTUP_KICK || st == MOTOR_STATE_RAMPING) {
          dbg_mask |= (1 << 2); // LED 3
        }
        if (st == MOTOR_STATE_STEADY_STATE) {
          dbg_mask |= (1 << 3); // LED 4
        }
        set_led_mask(dbg_mask);
      }
      break;
  }
}

void setup(void) {
  init_all_pins();

  // Bounded boot stabilization gate:
  // Hold off until 24V Vbus is active and stable via PC6 (or 1000ms max timeout)
  uint8_t vbus_settle_cnt = 0;
  while (VBUS_BJT_POWER_LOSS() && vbus_settle_cnt < 100) {
    *(volatile uint8_t*)0x50E0 = 0xAA; // Feed hardware watchdog
    delay(10);
    vbus_settle_cnt++;
  }
  delay(150); // Additional 150ms for 24V bulk caps & DRV10983 internal charge pump to fully settle

  // 1. Initialize Serial Console & Transmit Boot Banner
  uart_init_57600();
  uart_print("\r\ncustom firmware by sravangantla007\r\n");
  uart_print("https://github.com/sravangantla007/AtomQuest2026\r\n");

  // 2. Play Custom Boot Animation followed by Morse "RAVAN" (~2.0s)
  play_boot_animation();
  play_morse_ravan();

  // 3. Initialize DRV10983 driver over I2C
  i2c_link_ok = drv10983_init();
  vbus_healthy = (drv10983_read_vbus_raw() >= 120);

  // 4. Restore Non-Volatile Speed & Lighting Mode from EEPROM
  uint8_t restored_level = 0;
  uint16_t restored_speed = 0;
  if (EEPROM_read(EEPROM_ADDR_MAGIC) == EEPROM_MAGIC_VAL) {
    restored_level = EEPROM_read(EEPROM_ADDR_LEVEL);
    restored_speed = ((uint16_t)(EEPROM_read(EEPROM_ADDR_SPD_H) & 0x01) << 8) | EEPROM_read(EEPROM_ADDR_SPD_L);
    uint8_t saved_mode = EEPROM_read(EEPROM_ADDR_MODE);
    if (saved_mode <= 5) {
      current_light_mode = (LightMode_t)saved_mode;
    } else {
      current_light_mode = LIGHT_MODE_LEVEL; // Default to Level Gauge
    }
  } else {
    current_light_mode = LIGHT_MODE_LEVEL;
    active_fan_level = 1; // Default to Level 1 on first factory boot
    saved_user_speed = PRESET_SPEEDS[1];
    save_settings_to_eeprom();
    restored_level = 1;
  }

  // Resume running at last remembered speed
  if (restored_level >= 1 && restored_level <= 6) {
    uart_print("Boot: Restoring speed level\r\n");
    active_fan_level = restored_level;
    saved_user_speed = PRESET_SPEEDS[restored_level];
    drv10983_set_target_speed(saved_user_speed);
    play_level_transition(0, active_fan_level);
  } else if (restored_speed > 0 && restored_speed <= SPEED_MAX) {
    uart_print("Boot: Restoring raw speed\r\n");
    saved_user_speed = restored_speed;
    active_fan_level = speed_to_led_count(restored_speed);
    drv10983_set_target_speed(restored_speed);
    play_level_transition(0, active_fan_level);
  } else {
    uart_print("Boot: Fan at rest\r\n");
    active_fan_level = 0;
    saved_user_speed = 0;
    set_led_mask(0x00);
  }
}

void loop(void) {
  static uint32_t last_service_time = 0;
  static uint32_t last_safety_poll_time = 0;
  uint32_t packet = 0;
  uint32_t now = millis();

  // -------------------------------------------------------------
  // 0. Hardware Watchdog Kick & Rapid Power-Off Check
  // -------------------------------------------------------------
  *(volatile uint8_t*)0x50E0 = 0xAA; // Kick IWDG

  // Hardware Power-Loss Check via PC6 BJT Inverter:
  // Pin PC6 goes HIGH when 24V bus drops below 16.5V.
  // Debounced for 2ms to filter PWM switching transients while reacting instantly to real AC power-off.
  if (VBUS_BJT_POWER_LOSS()) {
    delay(2);
    if (VBUS_BJT_POWER_LOSS()) {
      if (vbus_healthy || drv10983_get_state() != MOTOR_STATE_STOPPED) {
        vbus_healthy = false;
        drv10983_rapid_shutdown();
        set_led_mask(0x00);
      }
    }
  }

  // -------------------------------------------------------------
  // 1. Process Incoming IR Commands
  // -------------------------------------------------------------
  if (IR_PIN_IS_LOW()) {
    if (decode_nec_frame(&packet)) {
      uint8_t cmd    = (uint8_t)((packet >> 16) & 0xFF);
      uint8_t data_h = (uint8_t)((packet >> 8) & 0xFF);
      uint8_t data_l = (uint8_t)(packet & 0xFF);

      // Clear any latching overcurrent trip on user remote command
      overcurrent_tripped = false;
      oc_debounce_cnt = 0;

      switch (cmd) {
        case CMD_SET_LEVEL: // 0x02: Direct Level 1 to 6
          if (data_l >= 1 && data_l <= 6) {
            apply_new_level(data_l);
          }
          break;

        case CMD_STEP_UP: // 0x05: Increment speed state (1..6)
          if (active_fan_level < 6) {
            apply_new_level(active_fan_level == 0 ? 1 : (active_fan_level + 1));
          }
          break;

        case CMD_STEP_DOWN: // 0x06: Decrement speed state (6..1)
          if (active_fan_level > 1) {
            apply_new_level(active_fan_level - 1);
          }
          break;

        case CMD_TOGGLE_POWER: // 0x03: Toggle ON/OFF (ON restores last saved state)
          if (active_fan_level > 0) {
            apply_new_level(0);
          } else {
            uint8_t lvl = EEPROM_read(EEPROM_ADDR_LEVEL);
            apply_new_level((lvl >= 1 && lvl <= 6) ? lvl : 1);
          }
          break;

        case CMD_POWER_ON: // 0x07: Explicit Power ON (restores last saved state)
          if (active_fan_level == 0) {
            uint8_t lvl = EEPROM_read(EEPROM_ADDR_LEVEL);
            apply_new_level((lvl >= 1 && lvl <= 6) ? lvl : 1);
          }
          break;

        case CMD_ALL_OFF: // 0x00: Coast to stop
          apply_new_level(0);
          break;

        case CMD_SET_RAW_SPEED: // 0x01: Precise continuous 9-bit speed (0 to 511)
          {
            uint16_t spd = ((uint16_t)(data_h & 0x01) << 8) | data_l;
            uint8_t old_level = active_fan_level;
            saved_user_speed = spd;
            active_fan_level = speed_to_led_count(spd);
            if (active_fan_level > 0) {
              save_settings_to_eeprom();
              drv10983_set_target_speed(spd);
            } else {
              drv10983_stop();
            }
            play_level_transition(old_level, active_fan_level);
          }
          break;

        case CMD_SET_LIGHT_MODE: // 0x04: Set Lighting Mode (0..5)
          if (data_l <= 5) {
            current_light_mode = (LightMode_t)data_l;
            save_settings_to_eeprom();
            // Acknowledge new lighting mode: show data_l + 1 LEDs for 800ms
            set_led_bar(data_l + 1);
            delay(800);
          }
          break;

        default:
          break;
      }
    }
  }

  // -------------------------------------------------------------
  // 2. Periodic Motor Service Engine (every 100ms)
  // -------------------------------------------------------------
  if (now - last_service_time >= 100) {
    last_service_time = now;
    drv10983_service_100ms();
  }

  // -------------------------------------------------------------
  // 3. Fast Power & Overcurrent Safety Monitor (every 50ms / 20 Hz)
  // -------------------------------------------------------------
  if (now - last_safety_poll_time >= 50) {
    last_safety_poll_time = now;

    // A. Check Vbus Voltage Telemetry from DRV10983 Reg 0x1A:
    uint8_t raw_vbus = drv10983_read_vbus_raw();
    if (VBUS_BJT_POWER_LOSS() || (raw_vbus > 0 && raw_vbus < 120)) {
      vbus_healthy = false;
      if (drv10983_get_state() != MOTOR_STATE_STOPPED) {
        drv10983_rapid_shutdown();
      }
    } else if (raw_vbus >= 248 && raw_vbus != 0xFF) {
      // Overvoltage surge (>29.0V): cut drive immediately
      vbus_healthy = false;
      if (drv10983_get_state() != MOTOR_STATE_STOPPED) {
        drv10983_rapid_shutdown();
      }
    } else if (raw_vbus >= 120 && raw_vbus != 0xFF) {
      vbus_healthy = true;
    }

    // B. Check Current Shunt Amplifier on PD3 (AIN4):
    // Shunt is non-inverting: higher current = higher ADC reading
    if (drv10983_get_state() != MOTOR_STATE_STOPPED && !overcurrent_tripped) {
      int shunt_adc = analogRead(PIN_CURRENT_SENSE);
      if (shunt_adc > OC_THRESHOLD) {
        oc_debounce_cnt++;
        if (oc_debounce_cnt >= 2) {
          overcurrent_tripped = true;
          drv10983_trigger_fault();
        }
      } else {
        if (oc_debounce_cnt > 0) oc_debounce_cnt--;
      }
    }
  }

  // -------------------------------------------------------------
  // 4. Unified Display & Animation Engine
  // -------------------------------------------------------------
  MotorState_t st = drv10983_get_state();
  bool is_fault = (st == MOTOR_STATE_FAULT) ||
                  drv10983_is_overtemp() ||
                  overcurrent_tripped ||
                  !i2c_link_ok ||
                  !vbus_healthy;

  update_led_display(now, is_fault);
}
