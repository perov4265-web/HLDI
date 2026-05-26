/*
 * hldi_axis_avr.h  —  simplified STEP/DIR axis for the AVR build.
 *
 * Drives one axis through a stepper driver (STEP/DIR/EN) with non-blocking
 * timed stepping so the serial protocol loop stays responsive.  Position is
 * tracked open-loop in steps; the unit<->step conversion is identical to the
 * firmware CalcStepX/Y so the same micron-based commands work unchanged.
 *
 * This replaces the STM32's closed-loop DC-motor + encoder (X) and the
 * timer-clocked parallel stepper (Y) with a single uniform model that an
 * Arduino can run reliably.
 */
#ifndef HLDI_AXIS_AVR_H
#define HLDI_AXIS_AVR_H

#include <Arduino.h>

class HldiAxisAvr {
public:
    void begin(uint8_t stepPin, uint8_t dirPin, uint8_t enPin,
               long resolution, long unit, uint32_t maxRateHz) {
        _step = stepPin; _dir = dirPin; _en = enPin;
        _res = resolution; _unit = unit;
        _periodUs = 1000000UL / maxRateHz;
        pinMode(_step, OUTPUT); pinMode(_dir, OUTPUT); pinMode(_en, OUTPUT);
        digitalWrite(_en, LOW);             // enable driver (active low)
        digitalWrite(_step, LOW);
        _pos = 0; _target = 0; _last = micros(); _high = false;
    }

    // unit (micron) <-> step, matching firmware CalcStepX/Y
    long calcStep(long pos_um) const {
        long s = (pos_um < 0) ? -1 : 1;
        return (pos_um * _res * 2 / _unit + s) / 2;
    }

    void moveTo(long pos_um) { _target = calcStep(pos_um); applyDir(); }
    void stepBy(long steps)  { _target = _pos + steps; applyDir(); }
    void setCoord(long pos_um) { _pos = calcStep(pos_um); _target = _pos; }
    void setRate(uint32_t hz) { if (hz) _periodUs = 1000000UL / hz; }
    void stop() { _target = _pos; }

    bool moving() const { return _pos != _target; }
    long position() const { return _pos; }

    // call frequently from loop(): emits step pulses toward the target
    void service() {
        if (_pos == _target) return;
        uint32_t now = micros();
        if ((uint32_t)(now - _last) < (_periodUs >> 1)) return;
        _last = now;
        if (!_high) {
            digitalWrite(_step, HIGH);
            _high = true;
        } else {
            digitalWrite(_step, LOW);
            _high = false;
            _pos += (_target > _pos) ? 1 : -1;   // count on falling edge
        }
    }

private:
    void applyDir() {
        digitalWrite(_dir, (_target >= _pos) ? HIGH : LOW);
    }

    uint8_t _step = 0, _dir = 0, _en = 0;
    long _res = 1, _unit = 1;
    long _pos = 0, _target = 0;
    uint32_t _periodUs = 250, _last = 0;
    bool _high = false;
};

#endif // HLDI_AXIS_AVR_H
