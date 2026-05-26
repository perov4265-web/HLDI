/*
 * HLDI_Firmware_AVR.ino  —  simplified AVR build of the HLDI controller.
 *
 * Arduino (AVR: Uno / Nano / Mega) variant, sharing the exact serial protocol
 * with the STM32 firmware and the PC software.  See hldi_config_avr.h for the
 * important note on what this build can and cannot do — it is a functional
 * subset for bench testing and simple jobs, not a replacement for the STM32
 * firmware on a real machine.
 *
 * Original HLDI by AlphaCrow:
 *   https://www.radiokot.ru/forum/viewtopic.php?f=8&t=119089
 *   https://t.me/+NOX32LwJiS83MWQy
 *
 * The protocol codec (hldi_proto.h) and the command/struct definitions
 * (hldi_protocol.h) are the *same files* used by the STM32 build, so the two
 * stay byte-for-byte compatible.
 */
#include "hldi_protocol.h"
#include "hldi_config_avr.h"
#include "hldi_proto.h"
#include "hldi_axis_avr.h"

// ---- configuration (same defaults as the firmware tab_cnf) ----
static HldiConfig cfg = {
    /*kprp*/ 525, /*kint*/ 7, /*kdif*/ 1, /*kcom*/ 1000,
    /*rsx */ 600, /*unx */ 25400, /*rsy*/ 400, /*uny*/ 1000,
    /*spdx*/ 400, /*spdy*/ 3000,
    /*vmin*/ 0, /*vmax*/ 255, /*qimg*/ 1, /*nlas*/ 1,
    /*gflg*/ 0, 0, 0, 0, 0
};

static HldiStatus  status  = {0, 0, 0, 0};
static HldiMcuInfo mcuInfo;

static HldiAxisAvr axisX;     // carriage
static HldiAxisAvr axisY;     // table

// small exposure/scratch buffer (AVR RAM is tiny; 512 B is plenty for tests)
static uint8_t lbuf0[512];
static uint8_t *actBuffer = lbuf0;
static uint32_t fDebug = 0;
static int32_t laserVal = 0;

static HldiProto *proto = nullptr;

static void setLaser(int32_t val) {
    laserVal = val;
    int duty;
    if (val <= 0) duty = 0;                       // off / from-RAM idle
    else duty = (val > LSR_RES_AVR) ? LSR_RES_AVR : val;
    analogWrite(PIN_LASER, duty);
    digitalWrite(PIN_LED, duty > 0);
}

static void dispatch(uint8_t cmd, uint32_t prm,
                     const uint8_t *payload, uint16_t len) {
    switch (cmd) {
        case CMD_ACK:  proto->ack(); break;
        case CMD_INFO: {
            mcuInfo.pcnf  = (uint32_t)(uintptr_t)&cfg;
            mcuInfo.lbuf  = (uint32_t)(uintptr_t)lbuf0;
            mcuInfo.sbuf  = (uint32_t)(uintptr_t)&status;
            mcuInfo.slbuf = sizeof(lbuf0) / 2;
            mcuInfo.verl  = HLDI_VERSION;
            mcuInfo.verh  = 0;
            proto->send_block((uint8_t *)&mcuInfo, sizeof(mcuInfo), CMD_INFO);
            break;
        }
        case CMD_CONF:
            if (cfg.nlas == 0) cfg.nlas = 1; else if (cfg.nlas > 4) cfg.nlas = 4;
            if (cfg.qimg == 0) cfg.qimg = 1; else if (cfg.qimg > 4) cfg.qimg = 4;
            proto->ack();
            break;
        case CMD_BOOT:
            proto->ack();
            HLDI_SERIAL.flush();
            // software reset via watchdog
            asm volatile ("jmp 0");
            break;
        case CMD_READ: {
            uint8_t rlen = payload ? payload[0] : 0;
            proto->send_block((uint8_t *)(uintptr_t)prm, rlen, CMD_READ);
            break;
        }
        case CMD_WRITE:
            if (prm && payload) memcpy((void *)(uintptr_t)prm, payload, len);
            proto->ack();
            break;
        case CMD_MVS:  axisX.stop(); axisY.stop(); proto->ack();      break;
        case CMD_MVX:  axisX.moveTo((int32_t)prm); proto->ack();      break;
        case CMD_MVY:  axisY.moveTo((int32_t)prm); proto->ack();      break;
        case CMD_STX:  axisX.stepBy((int32_t)prm); proto->ack();      break;
        case CMD_STY:  axisY.stepBy((int32_t)prm); proto->ack();      break;
        case CMD_SPX:  cfg.spdx = (int32_t)prm; axisX.setRate(X_MAX_RATE_HZ); proto->ack(); break;
        case CMD_SPY:  cfg.spdy = (int32_t)prm; axisY.setRate(Y_MAX_RATE_HZ); proto->ack(); break;
        case CMD_LSR:  setLaser((int32_t)prm); proto->ack();          break;
        case CMD_LPTR: actBuffer = (uint8_t *)(uintptr_t)prm; proto->ack(); break;
        case CMD_AXSX: axisX.setCoord((int32_t)prm); proto->ack();    break;
        case CMD_AXSY: axisY.setCoord((int32_t)prm); proto->ack();    break;
        case CMD_DBG:  fDebug = prm; proto->ack();                    break;
        default: break;
    }
}

static HldiProto protoInst(HLDI_SERIAL, dispatch);

void setup() {
    HLDI_SERIAL.begin(HLDI_BAUD);
    proto = &protoInst;

    pinMode(PIN_LED, OUTPUT);
    pinMode(PIN_LASER, OUTPUT);
    setLaser(0);

    axisX.begin(PIN_X_STEP, PIN_X_DIR, PIN_X_EN, cfg.rsx, cfg.unx, X_MAX_RATE_HZ);
    axisY.begin(PIN_Y_STEP, PIN_Y_DIR, PIN_Y_EN, cfg.rsy, cfg.uny, Y_MAX_RATE_HZ);

    proto->ack();                 // ready handshake (as the original main())
}

void loop() {
    while (HLDI_SERIAL.available())
        proto->feed((uint8_t)HLDI_SERIAL.read());

    axisX.service();
    axisY.service();

    status.dx = axisX.position();
    status.dy = axisY.position();
    status.sp = cfg.spdx;
    status.state = (axisX.moving() ? ST_MVX : 0) |
                   (axisY.moving() ? ST_MVY : 0);
}
