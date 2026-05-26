/*
 * hldi_proto.h  —  packet framing codec.
 *
 * Faithful port of the firmware's CHECK_INPUT_BIN() (receive + dispatch) and
 * SendBlock()/ACK() (transmit) from `comport.c` / `prot.c`.
 *
 * Receiving: bytes are fed one at a time to feed().  A frame ends at CHAREND;
 * the length byte (located just before the 2-byte checksum) is validated, the
 * start marker checked, the checksum summed exactly as the firmware does, and
 * on success the command id and the payload (first word = "prm") are handed to
 * the dispatch callback.
 *
 * Transmitting: send_block() reproduces SendBlock() byte-for-byte, including
 * the quirk that the length byte is emitted *after* the payload.
 */
#ifndef HLDI_PROTO_H
#define HLDI_PROTO_H

#include <Arduino.h>
#include <string.h>
#include "hldi_protocol.h"

class HldiProto {
public:
    // dispatch callback: (cmd, prm, payloadPtr, payloadLen)
    typedef void (*Dispatch)(uint8_t cmd, uint32_t prm,
                             const uint8_t *payload, uint16_t len);

    HldiProto(Stream &port, Dispatch cb) : _port(port), _cb(cb), _ci(0) {}

    // Feed one received byte.  Mirrors CHECK_INPUT_BIN's double-buffer scan:
    // the original writes into a 2x ring so a wrapped packet stays contiguous.
    void feed(uint8_t chr) {
        _buf[_ci] = chr;
        _buf[_ci + SIZEBUFRX] = chr;
        uint16_t ci = _ci + 1;

        if (chr == CHAREND) {
            // pointer to the second half, at the RSHDRE (len,ks,ks,cend) start
            uint8_t *p = &_buf[SIZEBUFRX] + (ci - 4);
            uint16_t len = p[0];                         // LEN field
            if (len >= RSHDR_SIZE && len < SIZEBUFRX) {
                uint8_t *start = &_buf[SIZEBUFRX] + (ci - len);  // first byte
                if (start[0] == CHARBEG) {
                    uint32_t ks = 0;
                    uint16_t n = len - 4;                // bytes up to ks
                    for (uint16_t i = 0; i <= n; i++)    // cbeg..last payload
                        ks += start[i];
                    uint16_t got = start[n + 1] | (start[n + 2] << 8);
                    if ((ks & 0xFFFF) == got) {
                        uint8_t cid = start[1];
                        if (cid < CMD_COUNT) {
                            uint16_t dlen = len - (2 + 4); // data length
                            const uint8_t *data = start + 2;
                            uint32_t prm = 0;
                            if (dlen >= 4)
                                memcpy(&prm, data, 4);
                            const uint8_t *rest = (dlen > 4) ? data + 4 : data;
                            if (_cb) _cb(cid, prm, rest,
                                         (dlen > 4) ? dlen - 4 : 0);
                        }
                    }
                }
            }
        }
        _ci = (ci >= SIZEBUFRX) ? 0 : ci;
    }

    // Send a framed block (port of SendBlock).
    void send_block(const uint8_t *ptr, uint16_t len, uint8_t id) {
        _port.write((uint8_t)CHARBEG);
        uint32_t kc = CHARBEG + len + id + RSHDR_SIZE;
        _port.write(id);
        for (uint16_t i = 0; i < len; i++) {
            uint8_t c = ptr[i];
            _port.write(c);
            kc += c;
        }
        _port.write((uint8_t)(len + RSHDR_SIZE));
        _port.write((uint8_t)(kc & 0xFF));
        _port.write((uint8_t)(kc >> 8));
        _port.write((uint8_t)CHAREND);
    }

    // ACK: an empty CMD_ACK block (port of ACK()).
    void ack() { send_block(nullptr, 0, CMD_ACK); }

private:
    Stream  &_port;
    Dispatch _cb;
    uint8_t  _buf[SIZEBUFRX * 2];
    uint16_t _ci;
};

#endif // HLDI_PROTO_H
