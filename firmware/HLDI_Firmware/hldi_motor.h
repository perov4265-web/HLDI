/*
 * hldi_motor.h  —  carriage DC motor, quadrature encoder and PID regulator.
 *
 * Port of `motor.c` + `capture.c`.  The carriage is driven by an H-bridge on
 * TIM1 (PWM) and its position is read from a quadrature encoder on TIM4.  A
 * PID loop (SpeedRegulator) holds the requested speed; CheckStop halts the
 * motor when the target position is reached.
 *
 * The original wires the encoder capture interrupt as the master clock that
 * both updates position and (in the laser module) triggers per-pixel
 * exposure.  Here the encoder is read with STM32 hardware quadrature mode on
 * TIM4 and the PID runs from a periodic update, which preserves the control
 * behaviour while fitting the Arduino HardwareTimer model.
 */
#ifndef HLDI_MOTOR_H
#define HLDI_MOTOR_H

#include <Arduino.h>
#include <HardwareTimer.h>
#include "hldi_config.h"
#include "hldi_protocol.h"

class HldiMotor {
public:
    explicit HldiMotor(HldiConfig &cfg) : _cfg(cfg) {}

    void begin() {
        pinMode(PIN_MOTOR_FWD, OUTPUT);
        pinMode(PIN_MOTOR_REV, OUTPUT);
        pinMode(PIN_MOTOR_PWM, OUTPUT);
        digitalWrite(PIN_MOTOR_FWD, LOW);
        digitalWrite(PIN_MOTOR_REV, LOW);

        // motor PWM on TIM1
        _pwm = new HardwareTimer(TIM1);
        _pwm->setPWM(1, PIN_MOTOR_PWM, PWM_FREQ, 0);

        beginEncoder();
        _dirX = 0; _stateX = false; _speedMotor = 0;
        _iVal = 0; _dVal = 0; _curPos = 0; _laserPos = 0; _curSpeed = 0;
    }

    // ---- unit <-> step conversions (CalcStepX) ----
    int32_t calcStepX(int32_t pos_um) const {
        int s = (pos_um < 0) ? -1 : 1;
        return (pos_um * _cfg.rsx * 2 / _cfg.unx + s) / 2;
    }

    // ---- commands ----
    void moveTo(int32_t pos_um)  { motor(_cfg.spdx, calcStepX(pos_um)); }
    void stepBy(int32_t steps)   { motor(_cfg.spdx, _curPos + steps); }
    void setSpeed(int32_t v)     { _cfg.spdx = v; }
    void setCoord(int32_t pos_um){ _curPos = calcStepX(pos_um); }
    void stop()                  { motorBreak(); }

    bool moving() const          { return _stateX; }
    int32_t position() const     { return _curPos; }
    int32_t speed() const        { return _curSpeed; }

    // Periodic service: read encoder, update speed, run the PID, check stop.
    void service() {
        int32_t cnt = (int32_t)(int16_t)_enc->getCount();
        int32_t delta = cnt - _lastEncCnt;
        _lastEncCnt = cnt;
        _curPos += delta;
        if (delta) _dirEnc = (delta > 0) ? 1 : -1;

        // rough speed estimate from position delta per service tick
        _curSpeed = delta * _speedScale;

        if (_stateX) {
            speedRegulator();
            checkStop(_curPos);
        }
    }

private:
    void beginEncoder() {
        // TIM4 hardware quadrature decode on PIN_ENC1 / PIN_ENC2
        _enc = new HardwareTimer(TIM4);
        TIM_TypeDef *inst = TIM4;
        // configure encoder mode (both edges) via LL where the core allows;
        // STM32duino exposes setMode for input capture — we use quadrature
        // by setting the timer to encoder mode through the register.
        inst->SMCR = TIM_SMCR_SMS_0 | TIM_SMCR_SMS_1;   // encoder mode 3
        inst->CCMR1 = (TIM_CCMR1_CC1S_0 | TIM_CCMR1_CC2S_0);
        inst->CCER = TIM_CCER_CC1E | TIM_CCER_CC2E;
        inst->ARR = 0xFFFF;
        inst->CNT = 0;
        inst->CR1 |= TIM_CR1_CEN;
        _lastEncCnt = 0;
    }

    void dirSet(int dir) {
        digitalWrite(PIN_MOTOR_FWD, LOW);
        digitalWrite(PIN_MOTOR_REV, LOW);
        _dirX = dir;
        if (dir > 0) digitalWrite(PIN_MOTOR_FWD, HIGH);
        else if (dir < 0) digitalWrite(PIN_MOTOR_REV, HIGH);
    }

    void pwmSet(int val) {
        val += _cfg.vmin;
        if (val < 0) val = 0;
        if (val > _cfg.vmax) val = _cfg.vmax;
        _voltage = val;
        _pwm->setCaptureCompare(1, val, RESOLUTION_8B_COMPARE_FORMAT);
    }

    void speedRegulator() {
        // port of SpeedRegulator(): vm = (err*Kp + I*Ki + dErr*Kd) / Kcom
        int32_t serr = _speedMotor - _curSpeed;
        _iVal += serr;
        int32_t dval = serr - _dVal;
        int32_t vm = (serr * _cfg.kprp + _iVal * _cfg.kint + dval * _cfg.kdif)
                     / _cfg.kcom;
        _dVal = serr;
        pwmSet(vm);
    }

    void motorBreak() {
        dirSet(0);
        _stateX = false;
        _speedMotor = 0;
        pwmSet(-_cfg.vmin);     // force PWM to 0
    }

    void checkStop(int32_t pos) {
        if (_stateX &&
            (((_dirX < 0) && (pos <= _laserPos)) ||
             ((_dirX > 0) && (pos >= _laserPos))))
            motorBreak();
    }

    void motor(int32_t speed, int32_t pos) {
        if (_curPos == pos) return;
        _iVal = 0; _dVal = 0;
        int dir = (pos < _curPos) ? -1 : 1;
        _laserPos = pos;
        _speedMotor = speed;
        _stateX = true;
        dirSet(dir);
        speedRegulator();
    }

    HldiConfig    &_cfg;
    HardwareTimer *_pwm = nullptr;
    HardwareTimer *_enc = nullptr;

    volatile int32_t _curPos = 0;       // CurrLaserPos (encoder steps)
    int32_t _laserPos = 0;              // target position
    int32_t _speedMotor = 0;
    int32_t _curSpeed = 0;
    int32_t _iVal = 0, _dVal = 0;       // PID accumulators
    int     _dirX = 0, _dirEnc = 0;
    int     _voltage = 0;
    bool    _stateX = false;
    int32_t _lastEncCnt = 0;
    static const int _speedScale = 50;  // tick-delta -> approx mm/s scale
};

#endif // HLDI_MOTOR_H
