#include <Arduino.h>
#include <Wire.h>
#include "drv10983.h"

// 12 Factory Calibrated Motor Parameters from Logic Analyzer Reverse Engineering
static const uint8_t FACTORY_MOTOR_PARAMS[NUM_MOTOR_PARAMS] = {
  0x48, // 0x20: Rm = 0.72 Ohm
  0xE9, // 0x21: Kt = 66.8 mV/Hz
  0x6B, // 0x22: AdvAngle = 15 deg
  0x00, // 0x23: ISD/IPD Off
  0xE4, // 0x24: Open loop current = 1.2A
  0xFF, // 0x25: Hand-off Thr = Max
  0x37, // 0x26: Accel Rate = 0.76 Hz/s^2
  0xE5, // 0x27: Current limit = 2.1A
  0x0F, // 0x28: Fault retry wait = 5s
  0xFA, // 0x29: f_PWM = 25kHz, AVS on
  0x4F, // 0x2A: Dead time = 440ns
  0x3E  // 0x2B: Thermal shutdown = 155C
};

static MotorState_t current_state = MOTOR_STATE_STOPPED;
static uint16_t current_speed = 0;
static uint16_t target_speed = 0;
static uint32_t kickstart_start_time = 0;

static uint8_t  cached_status = 0;
static uint8_t  cached_fault_code = 0;
static uint8_t  cached_vbus_raw = 0;

// Thermal Shutdown (OverTemp) Protection:
// Requires silicon cooling period (45 seconds) before any restart retry is permitted.
static bool     is_thermal_shutdown = false;
static uint32_t thermal_cooldown_deadline = 0;
static uint8_t  stall_debounce_cnt = 0;

static uint8_t i2c_write_reg(uint8_t reg, uint8_t val) {
  Wire_beginTransmission(DRV10983_I2C_ADDR);
  Wire_write(reg);
  Wire_write(val);
  return Wire_endTransmission();
}

static uint8_t i2c_read_reg(uint8_t reg) {
  Wire_beginTransmission(DRV10983_I2C_ADDR);
  Wire_write(reg);
  Wire_endTransmission1(false); // Repeated start
  Wire_requestFrom(DRV10983_I2C_ADDR, 1);
  if (Wire_available()) {
    return Wire_read();
  }
  return 0xFF;
}

bool drv10983_init(void) {
  uint8_t i;
  bool mismatch_detected = false;

  Wire_begin();
  delay(50); // Driver stabilization

  // 1. Verify DRV10983 responds on I2C address 0x52
  Wire_beginTransmission(DRV10983_I2C_ADDR);
  if (Wire_endTransmission() != 0) {
    return false;
  }

  // 2. Unlock EEPROM: Write 0xE0 to Reg 0x03
  i2c_write_reg(DRV_REG_EE_CTRL, 0xE0);
  delay(10);

  // 3. Read & Verify all 12 parameters
  for (i = 0; i < NUM_MOTOR_PARAMS; i++) {
    uint8_t reg = DRV_REG_PARAM_START + i;
    if (i2c_read_reg(reg) != FACTORY_MOTOR_PARAMS[i]) {
      mismatch_detected = true;
      break;
    }
  }

  // 4. Only rewrite if a mismatch is detected
  if (mismatch_detected) {
    for (i = 0; i < NUM_MOTOR_PARAMS; i++) {
      i2c_write_reg(DRV_REG_PARAM_START + i, FACTORY_MOTOR_PARAMS[i]);
    }
  }

  // 5. Relock EEPROM: Write 0xC0 to Reg 0x03
  i2c_write_reg(DRV_REG_EE_CTRL, 0xC0);
  delay(10);

  drv10983_set_raw_speed(0);
  current_state = MOTOR_STATE_STOPPED;

  // Initial telemetry read so registers are populated immediately at boot
  uint8_t init_v = i2c_read_reg(DRV_REG_SUPPLY_VOLT);
  cached_vbus_raw = (init_v != 0xFF) ? init_v : 200; // Default nominal 23.4V if read dropped

  uint8_t init_s = i2c_read_reg(DRV_REG_STATUS);
  cached_status = (init_s != 0xFF) ? init_s : 0x00;

  uint8_t init_f = i2c_read_reg(DRV_REG_FAULT_CODE);
  cached_fault_code = (init_f != 0xFF) ? init_f : 0x00;

  return true;
}

void drv10983_set_raw_speed(uint16_t speed_9bit) {
  if (speed_9bit > SPEED_MAX) speed_9bit = SPEED_MAX;

  uint8_t reg1 = 0x80 | (uint8_t)((speed_9bit >> 8) & 0x01);
  uint8_t reg0 = (uint8_t)(speed_9bit & 0xFF);

  i2c_write_reg(DRV_REG_SPEED_CTRL2, reg1);
  i2c_write_reg(DRV_REG_SPEED_CTRL1, reg0);

  current_speed = speed_9bit;
}

void drv10983_set_target_speed(uint16_t target_9bit) {
  // If in OverTemp thermal lockout, REJECT restart until cooldown has elapsed!
  if (is_thermal_shutdown) {
    if (millis() < thermal_cooldown_deadline) {
      // Still cooling down: reject command to prevent silicon destruction
      return;
    } else {
      // Cooldown timer expired: check if hardware thermal sensor has cleared
      if ((cached_status & 0x80) == 0) {
        is_thermal_shutdown = false;
        current_state = MOTOR_STATE_STOPPED;
      } else {
        // Still physically hot: extend lockout
        thermal_cooldown_deadline = millis() + 15000;
        return;
      }
    }
  }

  if (target_9bit > SPEED_MAX) target_9bit = SPEED_MAX;
  target_speed = target_9bit;

  // Clear normal stall fault on a new valid command
  stall_debounce_cnt = 0;
  if (current_state == MOTOR_STATE_FAULT && !is_thermal_shutdown) {
    current_state = MOTOR_STATE_STOPPED;
  }

  if (target_speed > 0) {
    if (current_state == MOTOR_STATE_STOPPED) {
      current_state = MOTOR_STATE_STARTUP_KICK;
      kickstart_start_time = millis();
      drv10983_set_raw_speed(SPEED_KICKSTART);
    } else {
      if (target_speed > current_speed) {
        current_state = MOTOR_STATE_RAMPING;
      } else if (target_speed < current_speed) {
        current_state = MOTOR_STATE_DECEL;
      } else {
        current_state = MOTOR_STATE_STEADY_STATE;
      }
    }
  } else {
    if (current_state != MOTOR_STATE_STOPPED) {
      current_state = MOTOR_STATE_DECEL;
    }
  }
}

void drv10983_stop(void) {
  drv10983_set_target_speed(0);
}

void drv10983_service_100ms(void) {
  uint8_t v = i2c_read_reg(DRV_REG_SUPPLY_VOLT);
  if (v != 0xFF) cached_vbus_raw = v;

  uint8_t s = i2c_read_reg(DRV_REG_STATUS);
  if (s != 0xFF) cached_status = s;

  uint8_t f = i2c_read_reg(DRV_REG_FAULT_CODE);
  if (f != 0xFF) cached_fault_code = f;

  // 1. OVER-TEMPERATURE FAULT CHECK (Bit 7 of Reg 0x10 or Reg 0x1E)
  // Trip temperature: Tj > 155 deg C
  if ((cached_status & 0x80) != 0 || (cached_fault_code & 0x80) != 0) {
    if (!is_thermal_shutdown) {
      is_thermal_shutdown = true;
      // Enforce mandatory 45-second cooling lockout: DO NOT RETRY IMMEDIATELY!
      thermal_cooldown_deadline = millis() + 45000;
    }
    current_state = MOTOR_STATE_FAULT;
    drv10983_set_raw_speed(0); // Cut all stator drive immediately
    return;
  }

  // 2. CHECK IF THERMAL LOCKOUT HAS EXPIRED
  if (is_thermal_shutdown) {
    if (millis() >= thermal_cooldown_deadline && (cached_status & 0x80) == 0) {
      is_thermal_shutdown = false;
      current_state = MOTOR_STATE_STOPPED;
    } else {
      current_state = MOTOR_STATE_FAULT;
      drv10983_set_raw_speed(0);
      return;
    }
  }

  // 3. MECHANICAL STALL / LOCK FAULT CHECK (Bits 5..1 of 0x1E)
  // Mask 0x3E: Ignores transient current limit chopping (Bit 0 Lock0)
  // Requires sustained fault (15 consecutive cycles = 1.5s) to avoid false trips
  // during normal motor deceleration or Back-EMF transient shifts.
  uint8_t mechanical_stall = cached_fault_code & 0x3E;
  if (mechanical_stall != 0 && current_state != MOTOR_STATE_STOPPED) {
    if (stall_debounce_cnt < 15) {
      stall_debounce_cnt++;
    } else {
      current_state = MOTOR_STATE_FAULT;
      drv10983_set_raw_speed(0);
      return;
    }
  } else {
    if (stall_debounce_cnt > 0) stall_debounce_cnt--;
    if (current_state == MOTOR_STATE_FAULT && !is_thermal_shutdown) {
      current_state = MOTOR_STATE_STOPPED;
    }
  }

  // 4. MOTOR STATE MACHINE
  switch (current_state) {
    case MOTOR_STATE_STOPPED:
      if (target_speed > 0) {
        current_state = MOTOR_STATE_STARTUP_KICK;
        kickstart_start_time = millis();
        drv10983_set_raw_speed(SPEED_KICKSTART);
      }
      break;

    case MOTOR_STATE_STARTUP_KICK:
      if (millis() - kickstart_start_time >= 1000) {
        current_state = MOTOR_STATE_RAMPING;
        drv10983_set_raw_speed(SPEED_MIN_RUN);
      }
      break;

    case MOTOR_STATE_RAMPING:
      if (current_speed < target_speed) {
        uint16_t next_spd = current_speed + 2;
        if (next_spd >= target_speed) {
          next_spd = target_speed;
          current_state = MOTOR_STATE_STEADY_STATE;
        }
        drv10983_set_raw_speed(next_spd);
      } else {
        current_state = MOTOR_STATE_STEADY_STATE;
      }
      break;

    case MOTOR_STATE_STEADY_STATE:
      if (target_speed > current_speed) {
        current_state = MOTOR_STATE_RAMPING;
      } else if (target_speed < current_speed) {
        current_state = MOTOR_STATE_DECEL;
      }
      break;

    case MOTOR_STATE_DECEL:
      if (target_speed > 0) {
        // GENTLE SPEED STEP-DOWN (e.g. Level 2 -> Level 1):
        // Decelerates at -20 counts/s and stops PRECISELY at target_speed
        if (current_speed > target_speed + 2) {
          drv10983_set_raw_speed(current_speed - 2);
        } else {
          drv10983_set_raw_speed(target_speed);
          current_state = MOTOR_STATE_STEADY_STATE;
        }
      } else {
        // SHUTDOWN TO ZERO:
        // Decelerate down to minimum speed, then coast to stop cleanly
        if (current_speed > SPEED_MIN_RUN) {
          drv10983_set_raw_speed(current_speed - 5);
        } else if (current_speed > 0) {
          drv10983_set_raw_speed(0);
          current_state = MOTOR_STATE_STOPPED;
        } else {
          current_state = MOTOR_STATE_STOPPED;
        }
      }
      break;

    case MOTOR_STATE_FAULT:
      break;
  }
}

uint8_t drv10983_read_vbus_raw(void) {
  return cached_vbus_raw;
}

uint8_t drv10983_read_fault_code(void) {
  return cached_fault_code;
}

uint8_t drv10983_read_status(void) {
  return cached_status;
}

bool drv10983_is_overtemp(void) {
  return is_thermal_shutdown;
}

MotorState_t drv10983_get_state(void) {
  return current_state;
}

uint16_t drv10983_get_current_speed(void) {
  return current_speed;
}

uint16_t drv10983_get_target_speed(void) {
  return target_speed;
}

void drv10983_trigger_fault(void) {
  current_state = MOTOR_STATE_FAULT;
  drv10983_set_raw_speed(0);
}

void drv10983_rapid_shutdown(void) {
  current_speed = 0;
  target_speed = 0;
  current_state = MOTOR_STATE_STOPPED;
  drv10983_set_raw_speed(0);
}
