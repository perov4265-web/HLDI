/*
 * hldi_config.h  —  hardware pin map and timing constants.
 *
 * Ported from the firmware `hldi.h` peripheral section to STM32duino pin
 * naming for the "Blue Pill" (STM32F103C8T6), which is the same MCU family
 * the original used (STM32F103, 72 MHz).
 *
 * The original drives four hardware timers in a tightly-coupled real-time
 * configuration:
 *   TIM1  — H-bridge PWM for the carriage DC motor (+ PID speed regulation)
 *   TIM4  — quadrature encoder capture (master timing reference)
 *   TIM2  — laser PWM, slaved to the encoder for per-pixel exposure
 *   SysTick — parallel stepper for the table (accel/decel tables)
 *   TIM3  — motion timeout watchdog
 *
 * Pin assignments below mirror the original (port/pin in hldi.h) translated
 * to the PXn names STM32duino uses.
 */
#ifndef HLDI_CONFIG_H
#define HLDI_CONFIG_H

// ---- system clock ----
#define HLDI_XTAL_HZ        8000000UL
#define HLDI_SYSCLK_HZ      72000000UL      // 8 MHz * 9 (PLL)

// ---- serial link ----
// USART1 on PA9 (TX) / PA10 (RX).  Speed is firmware-built: 115200 (LoSpeed)
// or 921600 (HiSpeed).  Default here matches the PC program's default.
#define HLDI_SERIAL         Serial1
#define HLDI_BAUD           115200

// ---- carriage DC motor (H-bridge on TIM1) ----
// pinPWM=13, pinPWMN=14 on port B  -> PB13 / PB14 (TIM1_CH1N / CH2N)
// pinPWRF=12, pinPWRB=15 on port B -> PB12 / PB15 (direction enables)
#define PIN_MOTOR_PWM       PB13            // TIM1 CH1N — forward PWM
#define PIN_MOTOR_PWMN      PB14            // TIM1 CH2N — reverse PWM
#define PIN_MOTOR_FWD       PB12            // forward enable
#define PIN_MOTOR_REV       PB15            // reverse enable

#define PWM_FREQ            20000           // motor PWM frequency (Hz)
#define PWM_RES             256             // motor PWM levels
#define PWR_VLT             14              // motor supply voltage
#define MIN_VLT             6               // motor break-away voltage
#define MAX_PWM             (PWM_RES - 1)
#define MIN_PWM             (MIN_VLT * PWM_RES / PWR_VLT - 1)

// ---- parallel stepper for the table (port A, pins 4..7) ----
#define PIN_STP0            PA5
#define PIN_STP1            PA4
#define PIN_STP2            PA6
#define PIN_STP3            PA7
// optional STEP/DIR outputs (port B 0/1)
#define PIN_STEP            PB0
#define PIN_DIR             PB1

#define FT_STEP             HLDI_SYSCLK_HZ  // step timer base frequency
#define FT_OUT              2000            // step rate while idle (timeout)
#define STOT                (6 * FT_OUT)    // stepper power-off timeout (~6 s)
#define MINSPEED            280             // minimum step speed
#define BREAKSPEED          (FT_STEP / (MINSPEED - 2) - 1)

// ---- quadrature encoder (TIM4 on PB6 / PB8) ----
#define PIN_ENC1            PB6             // TIM4_CH1
#define PIN_ENC2            PB8             // TIM4_CH3
#define FTCAP               12000000UL      // encoder capture count frequency
#define STROKE              4               // encoder counts per lamp stripe

// ---- lasers (TIM2 PWM on PA0..PA3) ----
#define NLASERS             4
#define PIN_LSR0            PA0             // TIM2_CH1
#define PIN_LSR1            PA1             // TIM2_CH2
#define PIN_LSR2            PA2             // TIM2_CH3
#define PIN_LSR3            PA3             // TIM2_CH4
#define LSR_FREQ            500             // laser PWM frequency (Hz)
#define LSR_RES             1000            // laser PWM levels
#define FTLSR               (FTCAP * 2)     // laser counter frequency

// ---- status LEDs (port B 10/11) ----
#define PIN_LED0            PB10
#define PIN_LED1            PB11

// ---- exposure buffer ----
#define SIZEBUFUNI          4096            // half of the paired exposure buffer

// ---- motion timeout (TIM3) ----
#define TOT_TIME            1000            // carriage motion timeout (ms)

#endif // HLDI_CONFIG_H
