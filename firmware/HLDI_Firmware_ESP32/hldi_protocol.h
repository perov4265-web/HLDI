/*
 * hldi_protocol.h  —  HLDI controller protocol and configuration.
 *
 * Faithful port of the original firmware header `HARD/FIRMWARE/SRC/hldi.h`
 * (author: AlphaCrow).  These values are the single source of truth shared
 * between the controller firmware and the PC program; both must agree.
 *
 *   forum:    https://www.radiokot.ru/forum/viewtopic.php?f=8&t=119089
 *   telegram: https://t.me/+NOX32LwJiS83MWQy
 *
 * Frame layout produced by SendBlock() / parsed by CHECK_INPUT_BIN():
 *
 *   +--------+--------+===========+--------+--------+--------+--------+
 *   | CBEG   | CID    |  PAYLOAD  | LEN    | KS lo  | KS hi  | CEND   |
 *   +--------+--------+===========+--------+--------+--------+--------+
 *     0xD6     id        n bytes    n+6      checksum (16)    0xE7
 *
 *   LEN  = payload_len + sizeof(RSHDR)   (RSHDR = 6: cbeg,cid,len,ks[2],cend)
 *   KS   = (CBEG + CID + LEN + sum(payload)) & 0xFFFF, little-endian
 *
 * Note the transmit order: the length byte is sent AFTER the payload, not
 * before it (this matches the original SendBlock exactly).
 */
#ifndef HLDI_PROTOCOL_H
#define HLDI_PROTOCOL_H

#include <stdint.h>

#define HLDI_VERSION        36          // _VERSION — firmware version

// ---- framing markers (from hldi.h) ----
#define CHARBEG             0xD6
#define CHAREND             0xE7

#define RSHDR_SIZE          6           // cbeg + cid + len + ks[2] + cend
#define SIZEBUFRX           128         // command receive buffer half-size
#define MAXLENRS            112         // max useful payload per packet

// ---- command identifiers (sequential, from hldi.h) ----
enum HldiCmd {
    CMD_ACK   = 0,   // acknowledge
    CMD_INFO  = 1,   // send MCU info block
    CMD_CONF  = 2,   // reconfigure from CONFIG block
    CMD_BOOT  = 3,   // reboot (bootloader / firmware update)
    CMD_READ  = 4,   // read a RAM block to the PC
    CMD_WRITE = 5,   // write a RAM block from the PC
    CMD_MVS   = 6,   // stop motion
    CMD_MVX   = 7,   // move carriage to absolute position (microns)
    CMD_MVY   = 8,   // move table to absolute position (microns)
    CMD_STX   = 9,   // step carriage by N encoder steps
    CMD_STY   = 10,  // step table by N motor steps
    CMD_SPX   = 11,  // set carriage speed
    CMD_SPY   = 12,  // set table speed
    CMD_LSR   = 13,  // laser control (power / from-RAM exposure)
    CMD_LPTR  = 14,  // set active exposure buffer pointer
    CMD_AXSX  = 15,  // set carriage coordinate X
    CMD_AXSY  = 16,  // set table coordinate Y
    CMD_DBG   = 17,  // debug enable/disable
    CMD_COUNT
};

// ---- debug modes ----
#define DEB_MODE_PID   1
#define DEB_MODE_LSR   2
#define DEB_MODE_TST   3

// ---- bitfield flag positions in CONFIG.gflg ----
#define GFLAG_LPWRT    1   // laser power controlled by time (else by percent)

// Status bits (SINFO.state)
#define ST_MVX 1           // carriage moving
#define ST_MVY 2           // table moving

#pragma pack(push, 1)

// Device configuration block (CONFIG in hldi.h).  Field order and offsets
// are preserved exactly so CMD_WRITE/CMD_READ from the PC stay compatible.
typedef struct {
    int32_t  kprp;   // 00 PID proportional
    int32_t  kint;   // 04 PID integral
    int32_t  kdif;   // 08 PID differential
    int32_t  kcom;   // 0C common divisor
    int32_t  rsx;    // 10 encoder resolution (dots)
    int32_t  unx;    // 14 carriage unit (microns)
    int32_t  rsy;    // 18 lead-screw resolution (steps)
    int32_t  uny;    // 1C table unit (microns)
    int32_t  spdx;   // 20 carriage speed
    int32_t  spdy;   // 24 table speed
    uint8_t  vmin;   // 28 min motor PWM
    uint8_t  vmax;   // 29 max motor PWM
    uint8_t  qimg;   // 2A resolution multiplier (Qual)
    uint8_t  nlas;   // 2B number of lasers
    uint32_t gflg;   // 2C bit flags (GFLAGS)
    uint32_t res1;
    uint32_t res2;
    uint32_t res3;
    uint32_t res4;
} HldiConfig;        // 64 bytes

// Status / info block (SINFO in hldi.h)
typedef struct {
    int32_t  dx;     // carriage position (encoder steps)
    int32_t  dy;     // table position (motor steps)
    int32_t  sp;     // current carriage speed
    uint32_t state;  // BSTATE: bit0 = stmx, bit1 = stmy
} HldiStatus;

// MCU info block returned by CMD_INFO (MCUINFO in hldi.h)
typedef struct {
    uint32_t pcnf;   // pointer to config buffer
    uint32_t lbuf;   // pointer to laser buffer
    uint32_t sbuf;   // pointer to status buffer
    uint16_t slbuf;  // half-size of laser buffer
    int8_t   verl;   // version low
    int8_t   verh;   // version high
} HldiMcuInfo;

#pragma pack(pop)

#endif // HLDI_PROTOCOL_H
