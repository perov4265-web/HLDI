/*
 * HLDI_Firmware.ino  —  HLDI laser direct-imaging controller firmware.
 *
 * Arduino (STM32duino) port of the original STM32F103 firmware by AlphaCrow.
 *   forum:    https://www.radiokot.ru/forum/viewtopic.php?f=8&t=119089
 *   telegram: https://t.me/+NOX32LwJiS83MWQy
 *
 * Target: STM32F103C8 "Blue Pill" (the same MCU family as the original),
 * built with the STM32duino core (board "Generic STM32F1 series" /
 * BluePill F103C8).  This is the faithful target because the design depends
 * on the STM32 hardware timers (encoder capture, PWM, timer interconnect)
 * that an 8-bit AVR cannot reproduce.
 *
 * The sketch wires up the protocol codec and the motor / stepper / laser
 * modules and dispatches the binary commands exactly as the firmware's
 * TabExecCMD[] did.
 */
#include "hldi_protocol.h"
#include "hldi_config.h"
#include "hldi_proto.h"
#include "hldi_motor.h"
#include "hldi_stepper.h"
#include "hldi_laser.h"

// ---- default configuration (port of tab_cnf in hldi.c) ----
static HldiConfig cfg = {
    /*kprp*/ 525, /*kint*/ 7, /*kdif*/ 1, /*kcom*/ 1000,
    /*rsx */ 600, /*unx */ 25400, /*rsy*/ 400, /*uny*/ 1000,
    /*spdx*/ 400, /*spdy*/ 3000,
    /*vmin*/ MIN_PWM, /*vmax*/ MAX_PWM, /*qimg*/ 1, /*nlas*/ 1,
    /*gflg*/ 0, 0, 0, 0, 0
};

static HldiStatus  status  = {0, 0, 0, 0};
static HldiMcuInfo mcuInfo;

static HldiMotor   motor(cfg);
static HldiStepper stepper(cfg);
static HldiLaser   laser(cfg);

// exposure / debug buffers (lbuf0 in the original)
static uint8_t  lbuf0[SIZEBUFUNI * 2];
static uint32_t fDebug = 0;

static HldiProto *proto = nullptr;

// ---- command handlers (port of the EX_CMD_* functions) ----
static void dispatch(uint8_t cmd, uint32_t prm,
                     const uint8_t *payload, uint16_t len) {
    switch (cmd) {
        case CMD_ACK:                              proto->ack(); break;
        case CMD_INFO: {                           // EX_CMD_INFO
            mcuInfo.pcnf  = (uint32_t)(uintptr_t)&cfg;
            mcuInfo.lbuf  = (uint32_t)(uintptr_t)lbuf0;
            mcuInfo.sbuf  = (uint32_t)(uintptr_t)&status;
            mcuInfo.slbuf = SIZEBUFUNI;
            mcuInfo.verl  = HLDI_VERSION;
            mcuInfo.verh  = 0;
            proto->send_block((uint8_t *)&mcuInfo, sizeof(mcuInfo), CMD_INFO);
            break;
        }
        case CMD_CONF:                             // EX_CMD_CONF
            if (cfg.nlas == 0) cfg.nlas = 1; else if (cfg.nlas > 4) cfg.nlas = 4;
            if (cfg.qimg == 0) cfg.qimg = 1; else if (cfg.qimg > 4) cfg.qimg = 4;
            laser.selectBpp(cfg.nlas);
            proto->ack();
            break;
        case CMD_BOOT:                             // EX_CMD_BOOT — reboot
            proto->ack();
            HLDI_SERIAL.flush();
            NVIC_SystemReset();
            break;
        case CMD_READ: {                           // EX_CMD_READ
            uint8_t rlen = payload ? payload[0] : 0;
            proto->send_block((uint8_t *)(uintptr_t)prm, rlen, CMD_READ);
            break;
        }
        case CMD_WRITE:                            // EX_CMD_WRITE
            if (prm && payload) memcpy((void *)(uintptr_t)prm, payload, len);
            proto->ack();
            break;
        case CMD_MVS:  motor.stop(); proto->ack();              break; // stop
        case CMD_MVX:  motor.moveTo((int32_t)prm);  proto->ack(); break;
        case CMD_MVY:  stepper.moveTo((int32_t)prm); proto->ack(); break;
        case CMD_STX:  motor.stepBy((int32_t)prm);  proto->ack(); break;
        case CMD_STY:  stepper.stepBy((int32_t)prm); proto->ack(); break;
        case CMD_SPX:  motor.setSpeed((int32_t)prm);  proto->ack(); break;
        case CMD_SPY:  stepper.setSpeed((int32_t)prm); proto->ack(); break;
        case CMD_LSR:  laser.ctrl((int32_t)prm); proto->ack();    break;
        case CMD_LPTR: laser.setBuffer((uint8_t *)(uintptr_t)prm); proto->ack(); break;
        case CMD_AXSX: motor.setCoord((int32_t)prm);  proto->ack(); break;
        case CMD_AXSY: stepper.setCoord((int32_t)prm); proto->ack(); break;
        case CMD_DBG:  fDebug = prm; proto->ack();    break;
        default: break;
    }
}

static HldiProto protoInst(HLDI_SERIAL, dispatch);

void setup() {
    HLDI_SERIAL.begin(HLDI_BAUD);
    proto = &protoInst;

    pinMode(PIN_LED0, OUTPUT);
    pinMode(PIN_LED1, OUTPUT);

    laser.begin();
    motor.begin();
    stepper.begin();
    laser.ctrl(0);                 // laser off

    proto->ack();                  // ready handshake, as the original main()
}

void loop() {
    // poll the serial port and feed the protocol codec
    while (HLDI_SERIAL.available())
        proto->feed((uint8_t)HLDI_SERIAL.read());

    // service the carriage PID / encoder and refresh the status block
    motor.service();
    status.dx = motor.position();
    status.dy = stepper.position();
    status.sp = motor.speed();
    status.state = (motor.moving() ? ST_MVX : 0) |
                   (stepper.moving() ? ST_MVY : 0);
}
