/*
 * hldi_link_esp32.h  —  WiFi + BLE transport for the controller.
 *
 * Presents a single byte stream (rx queue + tx) to the protocol codec while
 * underneath it runs:
 *
 *  - WiFi station with SoftAP fallback and automatic reconnect.
 *  - A TCP server on HLDI_TCP_PORT carrying the binary protocol frames.
 *  - A UDP discovery responder on HLDI_DISC_PORT: answers broadcast probes and
 *    also emits an unsolicited JSON beacon every HLDI_BEACON_PERIOD ms.
 *  - A BLE UART service (Nordic-style) advertising HLDI_BLE_SERVICE_UUID, whose
 *    RX/TX characteristics carry the same binary frames.
 *
 * Incoming bytes from whichever transport is active are pushed into a ring
 * buffer; outgoing bytes go to the currently connected transport.  This lets
 * the existing HldiProto codec work unchanged over any link.
 */
#ifndef HLDI_LINK_ESP32_H
#define HLDI_LINK_ESP32_H

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include "hldi_discovery.h"
#include "hldi_wifi_cfg.h"

#define HLDI_RX_RING   512

class HldiLink {
public:
    explicit HldiLink(HldiWifiCfg &cfg, uint8_t version)
        : _cfg(cfg), _ver(version), _server(HLDI_TCP_PORT) {}

    void begin() {
        _deviceName = _cfg.name.length() ? _cfg.name : defaultName();
        startWiFi();
        _server.begin();
        _server.setNoDelay(true);
        _udp.begin(HLDI_DISC_PORT);
        startBLE();
        _lastBeacon = 0;
        _lastWifiCheck = 0;
    }

    // --- stream-like API used by the protocol codec ---
    int available() { return _rxCount; }
    int read() {
        if (!_rxCount) return -1;
        uint8_t b = _rx[_rxTail];
        _rxTail = (_rxTail + 1) % HLDI_RX_RING;
        _rxCount--;
        return b;
    }
    size_t write(uint8_t b) { return writeBuf(&b, 1); }
    size_t writeBuf(const uint8_t *data, size_t n) {
        if (_client && _client.connected())
            return _client.write(data, n);          // WiFi TCP
        if (_bleConnected && _txChar) {              // BLE notify
            _txChar->setValue((uint8_t *)data, n);
            _txChar->notify();
            return n;
        }
        return 0;
    }
    void flush() { if (_client) _client.flush(); }

    // --- service: call frequently from loop() ---
    void service() {
        manageWiFi();
        acceptTcp();
        pumpTcp();
        sendBeacon();
        answerProbe();
    }

    String ip() const { return WiFi.localIP().toString(); }
    const String &name() const { return _deviceName; }

    // push received bytes into the ring (called by BLE callback too)
    void pushRx(const uint8_t *data, size_t n) {
        for (size_t i = 0; i < n; i++) {
            if (_rxCount < HLDI_RX_RING) {
                _rx[_rxHead] = data[i];
                _rxHead = (_rxHead + 1) % HLDI_RX_RING;
                _rxCount++;
            }
        }
    }
    void setBleConnected(bool c) { _bleConnected = c; }

private:
    static String defaultName() {
        uint64_t mac = ESP.getEfuseMac();
        char buf[16];
        snprintf(buf, sizeof(buf), HLDI_BLE_NAME_PREFIX "%04X",
                 (unsigned)(mac & 0xFFFF));
        return String(buf);
    }

    void startWiFi() {
        WiFi.mode(WIFI_AP_STA);
        WiFi.setAutoReconnect(true);
        if (_cfg.ssid.length())
            WiFi.begin(_cfg.ssid.c_str(), _cfg.pass.c_str());
        if (_cfg.apFallback) {
            String ap = _cfg.apSsid.length() ? _cfg.apSsid : _deviceName;
            if (_cfg.apPass.length())
                WiFi.softAP(ap.c_str(), _cfg.apPass.c_str());
            else
                WiFi.softAP(ap.c_str());
        }
    }

    void manageWiFi() {
        uint32_t now = millis();
        if (now - _lastWifiCheck < 2000) return;     // check every 2 s
        _lastWifiCheck = now;
        if (_cfg.ssid.length() && WiFi.status() != WL_CONNECTED) {
            // automatic reconnect attempt
            WiFi.disconnect();
            WiFi.begin(_cfg.ssid.c_str(), _cfg.pass.c_str());
        }
    }

    void acceptTcp() {
        if (_server.hasClient()) {
            if (_client && _client.connected()) {
                _server.available().stop();           // one client at a time
            } else {
                _client = _server.available();
                _client.setNoDelay(true);
            }
        }
    }

    void pumpTcp() {
        while (_client && _client.connected() && _client.available()) {
            uint8_t b = _client.read();
            pushRx(&b, 1);
        }
    }

    void sendBeacon() {
        if (WiFi.status() != WL_CONNECTED) return;
        uint32_t now = millis();
        if (now - _lastBeacon < HLDI_BEACON_PERIOD) return;
        _lastBeacon = now;
        broadcastBeacon();
    }

    void answerProbe() {
        int sz = _udp.parsePacket();
        if (sz <= 0) return;
        char buf[64];
        int n = _udp.read(buf, sizeof(buf) - 1);
        if (n <= 0) return;
        buf[n] = 0;
        if (strncmp(buf, HLDI_PROBE, strlen(HLDI_PROBE)) == 0) {
            String json = beaconJson();
            _udp.beginPacket(_udp.remoteIP(), _udp.remotePort());
            _udp.write((const uint8_t *)json.c_str(), json.length());
            _udp.endPacket();
        }
    }

    void broadcastBeacon() {
        String json = beaconJson();
        IPAddress bcast(255, 255, 255, 255);
        _udp.beginPacket(bcast, HLDI_DISC_PORT);
        _udp.write((const uint8_t *)json.c_str(), json.length());
        _udp.endPacket();
    }

    String beaconJson() {
        String mac = WiFi.macAddress();
        String j = "{\"";
        j += HLDI_BEACON_KEY; j += "\":1,\"name\":\"" + _deviceName + "\",";
        j += "\"ver\":" + String(_ver) + ",";
        j += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
        j += "\"port\":" + String(HLDI_TCP_PORT) + ",";
        j += "\"mac\":\"" + mac + "\",\"transport\":\"wifi\"}";
        return j;
    }

    // ---- BLE ----
    class RxCb : public BLECharacteristicCallbacks {
    public:
        explicit RxCb(HldiLink *l) : _l(l) {}
        void onWrite(BLECharacteristic *c) override {
            std::string v = c->getValue();
            if (v.size()) _l->pushRx((const uint8_t *)v.data(), v.size());
        }
        HldiLink *_l;
    };
    class SrvCb : public BLEServerCallbacks {
    public:
        explicit SrvCb(HldiLink *l) : _l(l) {}
        void onConnect(BLEServer *) override { _l->setBleConnected(true); }
        void onDisconnect(BLEServer *s) override {
            _l->setBleConnected(false);
            s->getAdvertising()->start();             // keep advertising
        }
        HldiLink *_l;
    };

    void startBLE() {
        BLEDevice::init(_deviceName.c_str());
        BLEServer *srv = BLEDevice::createServer();
        srv->setCallbacks(new SrvCb(this));
        BLEService *svc = srv->createService(HLDI_BLE_SERVICE_UUID);
        _txChar = svc->createCharacteristic(
            HLDI_BLE_TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
        _txChar->addDescriptor(new BLE2902());
        BLECharacteristic *rx = svc->createCharacteristic(
            HLDI_BLE_RX_UUID, BLECharacteristic::PROPERTY_WRITE |
                              BLECharacteristic::PROPERTY_WRITE_NR);
        rx->setCallbacks(new RxCb(this));
        svc->start();
        BLEAdvertising *adv = BLEDevice::getAdvertising();
        adv->addServiceUUID(HLDI_BLE_SERVICE_UUID);
        adv->setScanResponse(true);
        adv->start();
    }

    HldiWifiCfg &_cfg;
    uint8_t      _ver;
    String       _deviceName;

    WiFiServer   _server;
    WiFiClient   _client;
    WiFiUDP      _udp;
    uint32_t     _lastBeacon = 0, _lastWifiCheck = 0;

    BLECharacteristic *_txChar = nullptr;
    volatile bool _bleConnected = false;

    uint8_t  _rx[HLDI_RX_RING];
    volatile uint16_t _rxHead = 0, _rxTail = 0, _rxCount = 0;
};

#endif // HLDI_LINK_ESP32_H
