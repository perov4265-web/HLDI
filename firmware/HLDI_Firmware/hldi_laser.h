/*
 * hldi_laser.h  —  laser PWM control and per-pixel exposure from RAM.
 *
 * Port of `laser.c`.  Up to NLASERS lasers are driven by TIM2 PWM channels.
 * LaserCTRL(val) sets the behaviour, exactly like the original:
 *   val > 0 : constant power, level = LSR_RES - val   (focusing / "laser on")
 *   val == 0: off
 *   val < 0 : exposure from the active RAM bitmap, synchronised to the encoder
 *
 * The from-RAM path reads one bit (1bpp), two bits (2bpp) or a nibble (4bpp)
 * per pixel from the active buffer via the FlashLaser_{1,2,4}p selectors and
 * enables the corresponding laser channels.
 */
#ifndef HLDI_LASER_H
#define HLDI_LASER_H

#include <Arduino.h>
#include <HardwareTimer.h>
#include "hldi_config.h"
#include "hldi_protocol.h"

class HldiLaser {
public:
    explicit HldiLaser(HldiConfig &cfg) : _cfg(cfg) {}

    void begin() {
        _ch[0] = PIN_LSR0; _ch[1] = PIN_LSR1;
        _ch[2] = PIN_LSR2; _ch[3] = PIN_LSR3;
        _pwm = new HardwareTimer(TIM2);
        for (int i = 0; i < NLASERS; i++)
            _pwm->setPWM(i + 1, _ch[i], LSR_FREQ, 0);
        _laser = 0x7FFFFFFF;
        _actBuffer = nullptr;
        ctrl(0);
        selectBpp(_cfg.qimg);
    }

    // ---- commands ----
    void setBuffer(uint8_t *buf) { _actBuffer = buf; }
    void ctrl(int32_t val) {
        if (_laser == val) return;
        if (val < 0) {
            // exposure-from-RAM mode: power starts at 0, encoder ISR drives it
            setPower(0, 0x0);
        } else {
            int level = LSR_RES - val;          // val>0 power, val==0 off
            if (val == 0) level = LSR_RES;      // full period = off
            setPower(level, 0xF);
        }
        _laser = val;
    }
    bool exposing() const { return _laser < 0; }

    // Per-pixel exposure step (called from the encoder sync, port of
    // RunLaserIRQ): read the bitmap at `pix` and enable matching channels.
    void flashPixel(int pix, int maxPos, int laserValPWM) {
        uint8_t bits = 0;
        if (pix >= 0 && pix < maxPos) bits = readBits(pix);
        setPower(laserValPWM, bits);
    }

    void selectBpp(uint8_t nlas) {
        _bpp = (nlas >= 4) ? 4 : (nlas >= 2 ? 2 : 1);
    }

private:
    // FlashLaser_{1,2,4}p selectors from laser.c
    uint8_t readBits(int pos) const {
        if (!_actBuffer) return 0;
        switch (_bpp) {
            case 4:  return (_actBuffer[pos >> 1] >> (4 - ((pos << 2) & 4))) & 0xF;
            case 2:  return (_actBuffer[pos >> 2] >> (6 - ((pos << 1) & 6))) & 0x3;
            default: return (_actBuffer[pos >> 3] >> (7 - (pos & 7))) & 0x1;
        }
    }

    void setPower(int val, uint8_t bits) {
        // LaserPWM: all off first, then enable selected channels at `val`
        for (int i = 0; i < NLASERS; i++) {
            int duty = (bits & (1 << i)) ? val : 0;
            _pwm->setCaptureCompare(i + 1, duty, RESOLUTION_12B_COMPARE_FORMAT);
        }
    }

    HldiConfig    &_cfg;
    HardwareTimer *_pwm = nullptr;
    uint32_t _ch[NLASERS];
    uint8_t *_actBuffer = nullptr;
    volatile int32_t _laser = 0;
    uint8_t  _bpp = 1;
};

#endif // HLDI_LASER_H
