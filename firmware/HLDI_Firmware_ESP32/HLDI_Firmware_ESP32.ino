/*
 * HLDI_Firmware_ESP32.ino  —  ESP32 build of the HLDI controller.
 *
 * Adds wireless connectivity to the HLDI controller: the same binary protocol
 * runs over WiFi (TCP) and Bluetooth Low Energy, with automatic device
 * discovery (a UDP beacon that answers broadcast probes and advertises over
 * BLE), USB-serial WiFi setup, a receive buffer and automatic WiFi reconnect.
 *
 * Original HLDI by AlphaCrow:
 *   https://www.radiokot.ru/forum/viewtopic.php?f=8&t=119089
 *   https://t.me/+NOX32LwJiS83MWQy
 *
 * Like the AVR build, the motion here is a functional STEP/DIR subset (the
 * encoder-synchronised real-time laser timing remains specific to the STM32
 * build).  The value of this build is the wireless transport + discovery.
 *
 * Shares hldi_protocol.h / hldi_proto.h with the other builds, so it is
 * byte-for-byte protocol-compatible with the PC software.
 */
#include "hldi_protocol.h"
#include "hldi_proto.h"
#include "hldi_discovery.h"
#include "hldi_wifi_cfg.h"
#include "hldi_link_esp32.h"

// ---- pin map (ESP32 dev board) ----
#define PIN_X_STEP   25
#define PIN_X_DIR    26
#define PIN_Y_STEP   27
#define PIN_Y_DIR    14
#define PIN_LASER    32          // LEDC PWM channel
#define PIN_LED       2          // onboard LED

#define LSR_CH        0          // LEDC channel
#define LSR_FREQ_HZ   5000
#define LSR_BITS      10         // 0..1023, matches the firmware LSR_RES=1000
#define X_RATE_HZ     4000
#define Y_RATE_HZ     3000

// ---- configuration (same defaults as the firmware tab_cnf) ----
static HldiConfig cfg = {
    525, 7, 1, 1000, 600, 25400, 400, 1000, 400, 3000,
    0, 255, 1, 1, 0, 0, 0, 0, 0
};
static HldiStatus  status  = {0, 0, 0, 0};
static HldiMcuInfo mcuInfo;

static uint8_t  lbuf0[1024];
static uint8_t *actBuffer = lbuf0;
static uint32_t fDebug = 0;

static HldiWifiCfg       wifiCfg;
static HldiConfigConsole console(wifiCfg);
static HldiLink         *link = nullptr;
static HldiProto        *proto = nullptr;

// ---- minimal non-blocking STEP/DIR axis (same model as the AVR build) ----
struct Axis {
    uint8_t step, dir; long res, unit, pos, target; uint32_t periodUs, last;
    bool high;
    void begin(uint8_t s, uint8_t d, long r, long u, uint32_t hz) {
        step = s; dir = d; res = r; unit = u;
        periodUs = 1000000UL / hz; pos = target = 0; last = micros(); high = false;
        pinMode(step, OUTPUT); pinMode(dir, OUTPUT);
    }
    long calc(long um) const { long s = um < 0 ? -1 : 1; return (um*res*2/unit+s)/2; }
    void moveTo(long um) { target = calc(um); digitalWrite(dir, target>=pos); }
    void stepBy(long n)  { target = pos+n;    digitalWrite(dir, target>=pos); }
    void setCoord(long um){ pos = target = calc(um); }
    void stop() { target = pos; }
    bool moving() const { return pos != target; }
    void service() {
        if (pos == target) return;
        uint32_t now = micros();
        if ((uint32_t)(now-last) < (periodUs>>1)) return;
        last = now;
        if (!high) { digitalWrite(step, HIGH); high = true; }
        else { digitalWrite(step, LOW); high = false; pos += (target>pos)?1:-1; }
    }
};
static Axis axisX, axisY;

static void setLaser(int32_t val) {
    int duty = 0;
    if (val > 0) duty = (val > 1023) ? 1023 : val;
    ledcWrite(LSR_CH, duty);
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
            mcuInfo.verl  = HLDI_VERSION; mcuInfo.verh = 0;
            proto->send_block((uint8_t *)&mcuInfo, sizeof(mcuInfo), CMD_INFO);
            break;
        }
        case CMD_CONF:
            if (!cfg.nlas) cfg.nlas = 1; else if (cfg.nlas > 4) cfg.nlas = 4;
            if (!cfg.qimg) cfg.qimg = 1; else if (cfg.qimg > 4) cfg.qimg = 4;
            proto->ack(); break;
        case CMD_BOOT: proto->ack(); delay(50); ESP.restart(); break;
        case CMD_READ: {
            uint8_t rlen = payload ? payload[0] : 0;
            proto->send_block((uint8_t *)(uintptr_t)prm, rlen, CMD_READ);
            break;
        }
        case CMD_WRITE:
            if (prm && payload) memcpy((void *)(uintptr_t)prm, payload, len);
            proto->ack(); break;
        case CMD_MVS:  axisX.stop(); axisY.stop(); proto->ack(); break;
        case CMD_MVX:  axisX.moveTo((int32_t)prm); proto->ack(); break;
        case CMD_MVY:  axisY.moveTo((int32_t)prm); proto->ack(); break;
        case CMD_STX:  axisX.stepBy((int32_t)prm); proto->ack(); break;
        case CMD_STY:  axisY.stepBy((int32_t)prm); proto->ack(); break;
        case CMD_SPX:  cfg.spdx = (int32_t)prm; proto->ack(); break;
        case CMD_SPY:  cfg.spdy = (int32_t)prm; proto->ack(); break;
        case CMD_LSR:  setLaser((int32_t)prm); proto->ack(); break;
        case CMD_LPTR: actBuffer = (uint8_t *)(uintptr_t)prm; proto->ack(); break;
        case CMD_AXSX: axisX.setCoord((int32_t)prm); proto->ack(); break;
        case CMD_AXSY: axisY.setCoord((int32_t)prm); proto->ack(); break;
        case CMD_DBG:  fDebug = prm; proto->ack(); break;
        default: break;
    }
}

// the codec writes through the active link (WiFi TCP or BLE)
class LinkStream : public Stream {
public:
    int available() override { return link->available(); }
    int read() override      { return link->read(); }
    int peek() override      { return -1; }
    size_t write(uint8_t b) override { return link->write(b); }
    size_t write(const uint8_t *b, size_t n) override { return link->writeBuf(b, n); }
};
static LinkStream linkStream;
static HldiProto protoInst(linkStream, dispatch);

void setup() {
    console.begin();                 // USB config console
    wifiCfg.load();

    pinMode(PIN_LED, OUTPUT);
    ledcSetup(LSR_CH, LSR_FREQ_HZ, LSR_BITS);
    ledcAttachPin(PIN_LASER, LSR_CH);
    setLaser(0);

    axisX.begin(PIN_X_STEP, PIN_X_DIR, cfg.rsx, cfg.unx, X_RATE_HZ);
    axisY.begin(PIN_Y_STEP, PIN_Y_DIR, cfg.rsy, cfg.uny, Y_RATE_HZ);

    static HldiLink l(wifiCfg, HLDI_VERSION);
    link = &l;
    proto = &protoInst;
    link->begin();

    Serial.printf("HLDI ESP32 ready: %s @ %s (TCP %d) + BLE\n",
                  link->name().c_str(), link->ip().c_str(), HLDI_TCP_PORT);
    proto->ack();
}

void loop() {
    if (console.poll()) {            // settings changed over USB -> restart link
        wifiCfg.save();
        Serial.println(F("WiFi settings updated; rebooting to apply."));
        delay(100); ESP.restart();
    }

    link->service();                 // WiFi/BLE transport + discovery beacon

    while (link->available())        // feed the protocol codec
        proto->feed((uint8_t)link->read());

    axisX.service();
    axisY.service();

    status.dx = axisX.pos;
    status.dy = axisY.pos;
    status.sp = cfg.spdx;
    status.state = (axisX.moving() ? ST_MVX : 0) |
                   (axisY.moving() ? ST_MVY : 0);
}
