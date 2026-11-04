#ifndef DRV10983_H
#define DRV10983_H

#include <stdint.h>
#include <stdbool.h>

// DRV10983 7-Bit I2C Address
#define DRV10983_I2C_ADDR         0x52

// Registers
#define DRV_REG_SPEED_CTRL1       0x00  // Speed bits 7..0 (Latch write)
#define DRV_REG_SPEED_CTRL2       0x01  // OverRide (bit 7) + Speed bit 8
#define DRV_REG_DEV_CTRL          0x02  // Device control
#define DRV_REG_EE_CTRL           0x03  // EEPROM shadow register unlock/lock
#define DRV_REG_STATUS            0x10  // Status register (Bit 7: OverTemp TSD)
#define DRV_REG_MOTOR_SPEED1      0x11  // Motor speed period MSB
#define DRV_REG_MOTOR_SPEED2      0x12  // Motor speed period LSB
#define DRV_REG_SUPPLY_VOLT       0x1A  // Vbus ADC reading: V = Raw * 30 / 256
#define DRV_REG_SPEED_CMD_READ    0x1B  // Readback of applied speed command
#define DRV_REG_FAULT_CODE        0x1E  // Fault code register (Lock detection)

// Motor Parameter Shadow Registers (0x20..0x2B)
#define DRV_REG_PARAM_START       0x20
#define NUM_MOTOR_PARAMS          12

// Speed Limits (9-bit: 0 to 511)
#define SPEED_OFF                 0
#define SPEED_KICKSTART           300   // Inertia breakaway pulse
#define SPEED_MIN_RUN             200   // Minimum sustainable speed (~650 RPM)
#define SPEED_MAX                 511   // 100% full speed (~1450 RPM)

// Motor Controller States
typedef enum {
  MOTOR_STATE_STOPPED = 0,
  MOTOR_STATE_STARTUP_KICK,    // 1-second 300 count pulse
  MOTOR_STATE_RAMPING,         // Accelerating towards target (+20 counts/s)
  MOTOR_STATE_STEADY_STATE,    // Target speed reached
  MOTOR_STATE_DECEL,           // Smoothly decelerating towards target or 0
  MOTOR_STATE_FAULT            // Driver or hardware fault
} MotorState_t;

// API Functions
bool         drv10983_init(void);
void         drv10983_set_raw_speed(uint16_t speed_9bit);
void         drv10983_set_target_speed(uint16_t target_9bit);
void         drv10983_stop(void);
void         drv10983_service_100ms(void);
uint8_t      drv10983_read_vbus_raw(void);
uint8_t      drv10983_read_fault_code(void);
uint8_t      drv10983_read_status(void);
bool         drv10983_is_overtemp(void);
MotorState_t drv10983_get_state(void);
uint16_t     drv10983_get_current_speed(void);
uint16_t     drv10983_get_target_speed(void);
void         drv10983_trigger_fault(void);
void         drv10983_rapid_shutdown(void);

#endif // DRV10983_H
