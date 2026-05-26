/*
 * hldi_config_avr.h  —  pin map and constants for the simplified AVR build.
 *
 * This is the AVR (Arduino Uno / Nano / Mega) variant of the HLDI controller.
 *
 *   *** IMPORTANT — read this ***
 * The original controller is an STM32F103 whose design depends on four
 * tightly-coupled hardware timers (encoder capture driving per-pixel laser
 * timing, a 20 kHz H-bridge PWM with PID, etc.).  An 8-bit AVR at 16 MHz
 * cannot reproduce that real-time behaviour.  This build therefore implements
 * a *functional subset* that speaks the exact same serial protocol and is
 * useful for bench testing, protocol bring-up, jogging the axes and simple
 * jobs — but it is NOT a substitute for the STM32 firmware on a real machine.
 *
 * Simplifications vs. the STM32 firmware:
 *   - carriage motor driven open-loop via STEP/DIR (a stepper driver) instead
 *     of a closed-loop DC motor + quadrature encoder + PID;
 *   - laser power via analogWrite() (8-bit Timer PWM) instead of the 1000-level
 *     centred PWM;
 *   - per-pixel exposure is streamed from the buffer at a fixed step rate, not
 *     encoder-synchronised.
 *
 * Pins below assume an Uno/Nano; for a Mega they map the same names.
 */
#ifndef HLDI_CONFIG_AVR_H
#define HLDI_CONFIG_AVR_H

// ---- serial link ----
// Uno/Nano have a single hardware UART (Serial); on a Mega you can use
// Serial1 to keep the USB free.
#if defined(__AVR_ATmega2560__) || defined(__AVR_ATmega1280__)
  #define HLDI_SERIAL   Serial1
#else
  #define HLDI_SERIAL   Serial
#endif
#define HLDI_BAUD       115200

// ---- carriage X (STEP/DIR stepper driver, e.g. A4988/DRV8825) ----
#define PIN_X_STEP      3
#define PIN_X_DIR       4
#define PIN_X_EN        5     // enable (active low)

// ---- table Y (STEP/DIR stepper driver) ----
#define PIN_Y_STEP      6
#define PIN_Y_DIR       7
#define PIN_Y_EN        8

// ---- laser (PWM-capable pin) ----
#define PIN_LASER       9     // Timer1 OC1A on Uno — analogWrite capable

// ---- status LED ----
#define PIN_LED         13

// ---- motion limits / rates ----
#define X_MAX_RATE_HZ   4000  // max carriage step rate
#define Y_MAX_RATE_HZ   3000  // max table step rate
#define MIN_RATE_HZ     200   // start/stop rate (no ramp on AVR by default)

// ---- laser PWM resolution on AVR (analogWrite is 8-bit) ----
#define LSR_RES_AVR     255

#endif // HLDI_CONFIG_AVR_H
