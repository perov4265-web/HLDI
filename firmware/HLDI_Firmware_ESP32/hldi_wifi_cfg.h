/*
 * hldi_wifi_cfg.h  —  WiFi credentials store + USB configuration console.
 *
 * Credentials are persisted in NVS (Preferences) so they survive reboots, and
 * can be set over the USB serial port with a tiny text console — type `help`
 * in a serial monitor at HLDI_USB_BAUD.  Commands:
 *
 *   help                       show commands
 *   wifi <ssid> <password>     set station credentials
 *   ap [ssid] [password]       enable SoftAP fallback (default if no STA)
 *   name <device-name>         set the advertised device name
 *   show                       print current settings
 *   save                       persist settings to NVS
 *   reboot                     restart the controller
 *
 * The binary command protocol is NOT exposed on the USB port; USB is reserved
 * for configuration so the binary link stays on WiFi/BLE.
 */
#ifndef HLDI_WIFI_CFG_H
#define HLDI_WIFI_CFG_H

#include <Arduino.h>
#include <Preferences.h>

#define HLDI_USB_BAUD   115200

struct HldiWifiCfg {
    String ssid;
    String pass;
    String apSsid;
    String apPass;
    String name;
    bool   apFallback = true;

    void load() {
        Preferences p;
        p.begin("hldi", true);
        ssid     = p.getString("ssid", "");
        pass     = p.getString("pass", "");
        apSsid   = p.getString("apssid", "");
        apPass   = p.getString("appass", "");
        name     = p.getString("name", "");
        apFallback = p.getBool("apfb", true);
        p.end();
    }

    void save() {
        Preferences p;
        p.begin("hldi", false);
        p.putString("ssid", ssid);
        p.putString("pass", pass);
        p.putString("apssid", apSsid);
        p.putString("appass", apPass);
        p.putString("name", name);
        p.putBool("apfb", apFallback);
        p.end();
    }
};

// A minimal line-oriented console driven from the USB serial port.
class HldiConfigConsole {
public:
    explicit HldiConfigConsole(HldiWifiCfg &cfg) : _cfg(cfg) {}

    void begin() {
        Serial.begin(HLDI_USB_BAUD);
        Serial.println();
        Serial.println(F("HLDI ESP32 — config console.  Type 'help'."));
    }

    // call from loop(); returns true if a setting changed (e.g. to reconnect)
    bool poll() {
        bool changed = false;
        while (Serial.available()) {
            char c = (char)Serial.read();
            if (c == '\n' || c == '\r') {
                if (_line.length()) { changed |= handle(_line); _line = ""; }
            } else if (_line.length() < 160) {
                _line += c;
            }
        }
        return changed;
    }

private:
    static String nextTok(String &s) {
        s.trim();
        int sp = s.indexOf(' ');
        if (sp < 0) { String t = s; s = ""; return t; }
        String t = s.substring(0, sp);
        s = s.substring(sp + 1);
        return t;
    }

    bool handle(String line) {
        String cmd = nextTok(line);
        cmd.toLowerCase();
        if (cmd == "help") {
            Serial.println(F("commands: wifi <ssid> <pass> | ap [ssid] [pass] |"
                             " name <n> | show | save | reboot"));
        } else if (cmd == "wifi") {
            _cfg.ssid = nextTok(line);
            _cfg.pass = line; _cfg.pass.trim();
            Serial.printf("STA set: ssid=%s\n", _cfg.ssid.c_str());
            return true;
        } else if (cmd == "ap") {
            _cfg.apFallback = true;
            _cfg.apSsid = nextTok(line);
            _cfg.apPass = line; _cfg.apPass.trim();
            Serial.println(F("SoftAP fallback enabled."));
            return true;
        } else if (cmd == "name") {
            _cfg.name = line; _cfg.name.trim();
            Serial.printf("name=%s\n", _cfg.name.c_str());
            return true;
        } else if (cmd == "show") {
            Serial.printf("ssid=%s name=%s apFallback=%d\n",
                          _cfg.ssid.c_str(), _cfg.name.c_str(), _cfg.apFallback);
        } else if (cmd == "save") {
            _cfg.save();
            Serial.println(F("saved."));
        } else if (cmd == "reboot") {
            Serial.println(F("rebooting..."));
            Serial.flush();
            ESP.restart();
        } else {
            Serial.println(F("unknown — type 'help'"));
        }
        return false;
    }

    HldiWifiCfg &_cfg;
    String _line;
};

#endif // HLDI_WIFI_CFG_H
