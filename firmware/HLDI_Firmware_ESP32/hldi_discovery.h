/*
 * hldi_discovery.h  —  discovery / beacon protocol constants.
 *
 * Shared definitions for finding an HLDI controller automatically over WiFi
 * (UDP broadcast beacon) and Bluetooth Low Energy (advertised service).  The
 * PC discovery code uses the same constants.
 *
 * --- WiFi discovery ---
 * The PC broadcasts the probe string HLDI_PROBE to UDP port HLDI_DISC_PORT.
 * Every controller on the LAN replies (to the sender) with a one-line JSON
 * beacon describing itself, e.g.:
 *   {"hldi":1,"name":"HLDI-3F2A","ver":36,"ip":"192.168.1.50","port":3333,
 *    "mac":"AA:BB:CC:DD:EE:FF","transport":"wifi"}
 * The controller also emits this beacon unsolicited every few seconds so a
 * passive listener can find it too ("маяк при широковещательном запросе").
 *
 * --- command transport ---
 * Once found, the binary protocol frames (see hldi_proto.h) flow unchanged
 * over a TCP connection to HLDI_TCP_PORT (WiFi) or over the BLE UART
 * characteristic (Bluetooth).
 *
 * --- BLE discovery ---
 * The controller advertises HLDI_BLE_SERVICE_UUID and a device name
 * "HLDI-xxxx".  A Nordic-UART-style RX/TX characteristic pair carries the
 * same binary frames.
 */
#ifndef HLDI_DISCOVERY_H
#define HLDI_DISCOVERY_H

// ---- WiFi discovery ----
#define HLDI_DISC_PORT      50505           // UDP discovery port
#define HLDI_TCP_PORT       3333            // TCP command port
#define HLDI_PROBE          "HLDI?WHO"      // PC -> broadcast probe
#define HLDI_BEACON_KEY     "hldi"          // JSON marker in the reply
#define HLDI_BEACON_PERIOD  3000            // unsolicited beacon period (ms)

// ---- BLE discovery (Nordic UART–style) ----
// custom 128-bit UUIDs for the HLDI service and its RX/TX characteristics
#define HLDI_BLE_SERVICE_UUID  "6e40a001-b5a3-f393-e0a9-e50e24dcca9e"
#define HLDI_BLE_RX_UUID       "6e40a002-b5a3-f393-e0a9-e50e24dcca9e" // PC->dev
#define HLDI_BLE_TX_UUID       "6e40a003-b5a3-f393-e0a9-e50e24dcca9e" // dev->PC
#define HLDI_BLE_NAME_PREFIX   "HLDI-"

#endif // HLDI_DISCOVERY_H
