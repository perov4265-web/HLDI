/*
 * hldi_stepper.h  —  table (Y axis) stepper driver.
 *
 * Port of `step.c`.  The table is a parallel-driven stepper (4 phase pins,
 * via StepTable[]) with a constant-acceleration ramp from hldi_accel_table.h.
 * The original clocks steps from the SysTick interrupt; here a dedicated
 * HardwareTimer (TIM3) ISR plays the same role, calling step() each tick and
 * re-arming itself with the current ramp delay.
 */
#ifndef HLDI_STEPPER_H
#define HLDI_STEPPER_H

#include <Arduino.h>
#include <HardwareTimer.h>
#include "hldi_config.h"
#include "hldi_protocol.h"
#include "hldi_accel_table.h"

class HldiStepper {
public:
    explicit HldiStepper(HldiConfig &cfg) : _cfg(cfg) {}

    void begin() {
        _pins[0] = PIN_STP0; _pins[1] = PIN_STP1;
        _pins[2] = PIN_STP2; _pins[3] = PIN_STP3;
        for (int i = 0; i < 4; i++) pinMode(_pins[i], OUTPUT);
        setPhase(0);

        _timer = new HardwareTimer(TIM3);
        _timer->setOverflow(FT_OUT, HERTZ_FORMAT);   // idle rate
        _timer->attachInterrupt([this]() { this->isr(); });
        _timer->resume();

        _dirY = 0; _stateY = false; _stepCnt = 0;
        _accelPos = 0; _curPos = 0; _curSpeedDelay = BREAKSPEED;
    }

    // ---- unit <-> step conversions (CalcStepY) ----
    int32_t calcStepY(int32_t pos_um) const {
        int s = (pos_um < 0) ? -1 : 1;
        return (pos_um * _cfg.rsy * 2 / _cfg.uny + s) / 2;
    }

    // ---- commands ----
    void stepBy(int32_t steps)    { steps_(steps); }
    void moveTo(int32_t pos_um)   { steps_(calcStepY(pos_um) - _curPos); }
    void setSpeed(int32_t v)      { _cfg.spdy = v; }
    void setCoord(int32_t pos_um) { _curPos = calcStepY(pos_um); }

    bool moving() const           { return _stateY; }
    int32_t position() const      { return _curPos; }

private:
    static const unsigned StepTable(int i) {
        // forward/back 8-phase pattern from step.c (bit order STP3..STP0)
        static const uint8_t tab[8] = {
            0b1000, 0b1010, 0b0010, 0b0110,
            0b0100, 0b0101, 0b0001, 0b1001 };
        return tab[i & 7];
    }

    void setPhase(int pos) {
        uint8_t bits = StepTable(pos & 7);
        digitalWrite(_pins[0], (bits >> 0) & 1);
        digitalWrite(_pins[1], (bits >> 1) & 1);
        digitalWrite(_pins[2], (bits >> 2) & 1);
        digitalWrite(_pins[3], (bits >> 3) & 1);
    }

    void rearm(uint32_t delayTicks) {
        // convert a FT_STEP-based delay into a timer overflow value
        uint32_t hz = FT_STEP / (delayTicks + 1);
        if (hz < 1) hz = 1;
        _timer->setOverflow(hz, HERTZ_FORMAT);
    }

    void stepBreak() {
        _dirY = 0; _stateY = false; _stepCnt = 0;
        _stepTot = STOT;
        rearm(FT_STEP / FT_OUT - 1);
    }

    void accelStep(int32_t reqDelay) {
        if (_accelPos < (int)ACCEL_TABLE_LEN) {
            _accelPos++;
            uint32_t d = FT_STEP / AccelSpeed[_accelPos < (int)ACCEL_TABLE_LEN
                                              ? _accelPos : ACCEL_TABLE_LEN - 1] - 1;
            _curSpeedDelay = (d <= (uint32_t)reqDelay) ? reqDelay : d;
        } else {
            _reqDelay = _curSpeedDelay;
        }
    }

    void deccelStep(int32_t reqDelay) {
        if (_accelPos > 0) {
            _accelPos--;
            uint32_t d = FT_STEP / AccelSpeed[_accelPos] - 1;
            _curSpeedDelay = (d >= (uint32_t)reqDelay) ? reqDelay : d;
        } else {
            _reqDelay = _curSpeedDelay;
            stepBreak();
        }
    }

    void changePos(int dir) {
        _dirY = dir;
        if (_stepCnt <= _accelPos) _reqDelay = BREAKSPEED;
        if (!_stepCnt) { stepBreak(); return; }
        _stateY = true;
        _curPos += dir;
        _stepCnt--;
        setPhase(_curPos);
        if ((int32_t)_curSpeedDelay < _reqDelay) deccelStep(_reqDelay);
        else if ((int32_t)_curSpeedDelay > _reqDelay) accelStep(_reqDelay);
        rearm(_curSpeedDelay);
    }

    void steps_(int32_t rpos) {
        _reqDelay = BREAKSPEED;
        if (!rpos || _dirY) return;
        _accelPos = 0;
        _curSpeedDelay = AccelSpeed[0] ? (FT_STEP / AccelSpeed[0] - 1) : BREAKSPEED;
        uint32_t sp = _cfg.spdy ? _cfg.spdy : 1;
        uint32_t d = FT_STEP / sp - 1;
        if (d > _curSpeedDelay) d = _curSpeedDelay;
        _reqDelay = d;
        if (rpos > 0) { _stepCnt = rpos;  changePos(+1); }
        else          { _stepCnt = -rpos; changePos(-1); }
    }

    void isr() {
        if (_dirY) changePos(_dirY);
        else if (_stepTot) {            // idle timeout: de-energise coils
            if (!--_stepTot) {
                for (int i = 0; i < 4; i++) digitalWrite(_pins[i], LOW);
            }
        }
    }

    HldiConfig    &_cfg;
    HardwareTimer *_timer = nullptr;
    uint32_t _pins[4];
    volatile int32_t  _curPos = 0;
    volatile int32_t  _stepCnt = 0;
    volatile int      _dirY = 0;
    volatile bool     _stateY = false;
    volatile int      _accelPos = 0;
    volatile uint32_t _curSpeedDelay = 0;
    volatile int32_t  _reqDelay = 0;
    volatile uint32_t _stepTot = 0;
};

#endif // HLDI_STEPPER_H
