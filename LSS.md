# LoRa Sensor Station (LSS)

## Overview

The LoRa Sensor Station (LSS) is a mesh-networked sensor platform consisting of a central base station and multiple client nodes. The base station coordinates the network, collects and stores sensor data, and exposes a web interface for monitoring and configuration. Client nodes are primarily sensors that report readings over LoRa radio and accept remote configuration commands from the base station.

Parity between the base station and clients must be maintained at all times. Any change to the packet protocol, command set, or LoRa parameters is a sync-required change and must be deployed to both sides simultaneously.

---

## Hardware

### Base Station
- **Computer:** Raspberry Pi 5
- **Radio:** Adafruit LoRa Radio Bonnet — RFM95W @ 915 MHz ([product](https://www.adafruit.com/product/4074), [setup guide](https://learn.adafruit.com/adafruit-radio-bonnets/rfm9x-raspberry-pi-setup))
- **Display:** 5" LCD with touchscreen
- **SPI wiring (BCM pin numbers):**
  - CS → GPIO 7 (SPI CE1, board pin 26)
  - IRQ (G0) → GPIO 24 (board pin 18)
  - RST → GPIO 25 (board pin 22)

### Client Node
- **MCU:** Heltec WiFi LoRa 32 v3 ([product](https://heltec.org/project/wifi-lora-32/))
- **Radio:** SX1262 integrated LoRa transceiver
- **Notable GPIO:**
  - LED: GPIO 35 (`LED_BUILTIN`)
  - User button: GPIO 0
  - Battery sense: GPIO 1 (ADC), enable via GPIO 37
  - Battery voltage divider ratio: 4.9× (390 kΩ / 100 kΩ)

---

## Repository Structure

```
BaseStation/                  ← Python base station (Raspberry Pi)
    main.py
    requirements.txt
    lss_basestation/
        config.py             ← Radio params, file paths, constants
        config_storage.py     ← Persistent JSON config read/write
        lora_manager.py       ← RFM95W driver + packet receive loop
        packet_parser.py      ← Deserialise all packet types
        sensor_store.py       ← In-memory state + SQLite history
        remote_config.py      ← Outbound command queue
        mqtt_manager.py       ← MQTT publish
        alerts.py             ← Teams / email notifications
        web/app.py            ← Flask application + REST API
    data/                     ← config.json, sensors.db, logs
    systemd/                  ← lss-basestation.service

LSS-Arduino/                  ← PlatformIO firmware (Heltec client + base)
    platformio.ini
    src/                      ← .cpp implementation files
    include/                  ← .h headers
    data/                     ← LittleFS web UI (HTML/JS/CSS)
    test/                     ← Unity unit tests (to be populated)
```

---

## LoRa Radio Parameters

Both sides **must** use identical radio parameters. These are the authoritative defaults.

| Parameter        | Value           |
|-----------------|-----------------|
| Frequency        | 915.0 MHz       |
| Spreading Factor | SF10            |
| Bandwidth        | 125 kHz         |
| Coding Rate      | 4/5             |
| TX Power         | 20 dBm          |
| Preamble Length  | 8 symbols       |
| Max Payload      | 255 bytes       |
| Sync Word        | `0x12 + (networkId % 244)` |

> The LoRa sync word is derived from the Network ID so that networks are isolated at the radio level. The default Network ID is `1`.

---

## Packet Protocol

### Application-Level Sync Words

| Type               | Sync Word  | Purpose                          |
|--------------------|-----------|----------------------------------|
| Legacy telemetry   | `0x1234`  | `SensorData` struct (v1 format)  |
| Multi-sensor       | `0xABCD`  | `MultiSensorHeader` (v2.9+)      |
| Command / ACK      | `0xCDEF`  | `CommandPacket` / `AckPacket`    |

### Packet Types

```
PACKET_LEGACY       = 0   // SensorData – backward compatibility only
PACKET_MULTI_SENSOR = 1   // Variable-length multi-value telemetry
PACKET_CONFIG       = 2   // Configuration data
PACKET_ACK          = 3   // Acknowledgment
```

### Multi-Sensor Telemetry Packet (v2.9+, preferred)

```
MultiSensorHeader  (packed)
    uint16  syncWord        // 0xABCD
    uint16  networkId
    uint8   packetType      // PACKET_MULTI_SENSOR = 1
    uint8   sensorId        // Node ID (1–254; 0 and 255 reserved)
    uint8   valueCount      // Number of SensorValuePacket entries following
    uint8   batteryPercent
    uint8   powerState      // 0 = discharging, 1 = charging
    uint8   lastCommandSeq  // Piggyback ACK: seq of last processed command
    uint8   ackStatus       // 0 = success, non-zero = error code
    char[32] location
    char[16] zone

SensorValuePacket  (packed, repeated valueCount times)
    uint8   type            // ValueType enum
    float   value

uint16  checksum            // CRC16 over header + all value entries
```

Maximum 16 `SensorValuePacket` entries per transmission.

### Command Packet

Sent from base station to a client node.

```
CommandPacket  (packed, max 200 bytes)
    uint16  syncWord        // 0xCDEF
    uint8   commandType     // CommandType enum
    uint8   targetSensorId
    uint8   sequenceNumber  // For ACK tracking
    uint8   dataLength
    uint8[192] data         // Command-specific payload
    uint16  checksum
```

Retry policy: up to 3 attempts, 12-second timeout per attempt.

### ACK / NACK Packet

```
AckPacket  (packed)
    uint16  syncWord        // 0xCDEF
    uint8   commandType     // CMD_ACK or CMD_NACK
    uint8   sensorId
    uint8   sequenceNumber  // Matches originating command
    uint8   statusCode
    uint8   dataLength
    uint8[192] data         // Optional response payload
    uint16  checksum
```

ACKs may also be piggybacked on the next telemetry packet via `lastCommandSeq` / `ackStatus` in the `MultiSensorHeader`, avoiding an extra transmission.

---

## Command Set

| Code   | Name                | Direction       | Description                                 |
|--------|---------------------|-----------------|---------------------------------------------|
| `0x00` | `CMD_PING`          | Base → Client   | Keepalive / round-trip latency check        |
| `0x01` | `CMD_GET_CONFIG`    | Base → Client   | Request current config from node            |
| `0x02` | `CMD_SET_INTERVAL`  | Base → Client   | Update telemetry transmission interval      |
| `0x03` | `CMD_SET_LOCATION`  | Base → Client   | Set node location string and zone           |
| `0x04` | `CMD_SET_TEMP_THRESH` | Base → Client | Temperature alert thresholds                |
| `0x05` | `CMD_SET_BATTERY_THRESH` | Base → Client | Battery alert thresholds                 |
| `0x06` | `CMD_SET_MESH_CONFIG` | Base → Client  | Enable / disable mesh forwarding            |
| `0x07` | `CMD_RESTART`       | Base → Client   | Reboot node                                 |
| `0x08` | `CMD_FACTORY_RESET` | Base → Client   | Wipe NVS and reboot                         |
| `0x09` | `CMD_SET_LORA_PARAMS` | Base → Client | Update frequency, SF, BW, TX power         |
| `0x0A` | `CMD_TIME_SYNC`     | Base → Client   | Send UTC epoch + timezone offset (minutes)  |
| `0x0B` | `CMD_SENSOR_ANNOUNCE` | Client → Base | Node boot announcement; triggers welcome   |
| `0x0C` | `CMD_BASE_WELCOME`  | Base → Client   | Response to announce: time + base config    |
| `0xA0` | `CMD_ACK`           | Client → Base   | Positive acknowledgment                     |
| `0xA1` | `CMD_NACK`          | Client → Base   | Negative acknowledgment                     |

---

## Client Enrollment Flow

New clients self-enroll without manual intervention:

1. Client boots and broadcasts `CMD_SENSOR_ANNOUNCE` over LoRa.
2. Base station receives the announce, registers the node ID, and queues `CMD_BASE_WELCOME` containing the current UTC time and timezone offset.
3. Client receives `CMD_BASE_WELCOME`, sets its RTC, and begins normal telemetry transmission.
4. Base station marks the node as enrolled on first successful telemetry receipt.

Node IDs (1–254) are pre-configured per device in NVS. ID 0 and 255 are reserved. If a node ID collision occurs, behavior is undefined — IDs must be unique within a network.

---

## Sensor Data Model

### Supported Sensor Hardware

| Enum                  | Hardware           | Interface | Typical Values              |
|-----------------------|--------------------|-----------|-----------------------------|
| `SENSOR_THERMISTOR`   | NTC thermistor     | ADC       | Temperature (°C)            |
| `SENSOR_DS18B20`      | DS18B20            | 1-Wire    | Temperature (°C)            |
| `SENSOR_DHT22`        | DHT22              | DHT       | Temperature (°C), Humidity (%RH) |
| `SENSOR_DHT11`        | DHT11              | DHT       | Temperature (°C), Humidity (%RH) |
| `SENSOR_BME680`       | BME680             | I2C       | Temp, Humidity, Pressure, Gas resistance |
| `SENSOR_BH1750`       | BH1750             | I2C       | Illuminance (lx)            |
| `SENSOR_INA219`       | INA219             | I2C       | Voltage (V), Current (mA), Power (mW) |
| `SENSOR_SHT31`        | SHT31              | I2C       | Temperature (°C), Humidity (%RH) |
| `SENSOR_BMP280`       | BMP280             | I2C       | Temperature (°C), Pressure  |
| `SENSOR_SOIL_MOISTURE`| Resistive probe    | ADC       | Moisture (%)                |

### Value Types (transmitted in `SensorValuePacket.type`)

| Code | Name                        | Unit  |
|------|-----------------------------|-------|
| 0    | `VALUE_TEMPERATURE`         | °C    |
| 1    | `VALUE_HUMIDITY`            | %RH   |
| 2    | `VALUE_PRESSURE`            | hPa   |
| 3    | `VALUE_LIGHT`               | lx    |
| 4    | `VALUE_VOLTAGE`             | V     |
| 5    | `VALUE_CURRENT`             | mA    |
| 6    | `VALUE_POWER`               | mW    |
| 7    | `VALUE_ENERGY`              | Wh    |
| 8    | `VALUE_GAS_RESISTANCE`      | Ω     |
| 9    | `VALUE_BATTERY`             | %     |
| 10   | `VALUE_SIGNAL_STRENGTH`     | dBm   |
| 11   | `VALUE_MOISTURE`            | %     |
| 12   | `VALUE_GENERIC`             | —     |
| 13   | `VALUE_THERMISTOR_TEMPERATURE` | °C |

---

## Mesh Network Architecture

The mesh uses an **AODV-inspired, coordinator-centric** topology:

- The base station is the permanent coordinator (node ID 0 by convention).
- All client-to-base paths are the primary use case; client-to-client is supported for relay only.
- Route discovery uses controlled flooding (RREQ / RREP packets).
- Routing tables are maintained per-node with a 10-minute route timeout.
- Maximum hop count: **5**. Packets exceeding this are dropped.
- Neighbor beacons are broadcast every **30 seconds** for topology maintenance.
- Sequence numbers prevent routing loops and duplicate processing.

### Mesh Packet Types

| Value | Name                    | Description                           |
|-------|-------------------------|---------------------------------------|
| 0     | `MESH_DATA`             | User data payload                     |
| 1     | `MESH_ROUTE_REQUEST`    | RREQ — flood to discover a route      |
| 2     | `MESH_ROUTE_REPLY`      | RREP — unicast reply on found route   |
| 3     | `MESH_ROUTE_ERROR`      | Notify upstream of broken link        |
| 4     | `MESH_NEIGHBOR_BEACON`  | Periodic neighbor discovery broadcast |

### Mesh Header (prepended to all mesh frames)

```
MeshHeader (packed)
    uint8   packetType
    uint8   sourceId
    uint8   destId        // 255 = broadcast
    uint8   nextHop
    uint8   prevHop
    uint8   hopCount
    uint8   ttl
    uint16  sequenceNum
```

---

## Base Station Software Architecture

The base station runs as a Python application under systemd (`lss-basestation.service`).

### Modules

| Module               | Responsibility                                              |
|----------------------|-------------------------------------------------------------|
| `lora_manager.py`    | SPI driver for RFM95W; receive loop; dispatches parsed packets |
| `packet_parser.py`   | Deserialises legacy, multi-sensor, and command packet types |
| `sensor_store.py`    | In-memory sensor state; SQLite history (time-series per node) |
| `remote_config.py`   | Outbound command queue with retry logic                     |
| `config_storage.py`  | Persistent JSON config (LoRa params, network ID, MQTT, etc.) |
| `mqtt_manager.py`    | Publishes sensor readings to an MQTT broker                 |
| `alerts.py`          | Microsoft Teams webhook and SMTP email notifications        |
| `web/app.py`         | Flask REST API + Jinja2 web interface                       |

### Data Storage

- **In-memory:** Last known state for each node (up to 10 nodes, 120 history points each).
- **SQLite (`sensors.db`):** Time-series rows per node — timestamp, battery, RSSI, SNR, and all sensor values. Used for historical charts and export.
- **JSON (`config.json`):** All persistent configuration (LoRa params, MQTT, alert thresholds, enrolled nodes).

A node is considered **offline** after 300 seconds without a packet.

### REST API Surface (Flask)

Key endpoints exposed by `web/app.py`:

| Method | Path                        | Description                          |
|--------|-----------------------------|--------------------------------------|
| GET    | `/`                         | Dashboard                            |
| GET    | `/api/sensors`              | All sensor current state (JSON)      |
| GET    | `/api/sensors/<id>/history` | Time-series history for a node       |
| POST   | `/api/command`              | Queue a command to a client node     |
| GET    | `/api/config`               | Read base station config             |
| POST   | `/api/config`               | Update base station config           |
| GET    | `/api/lora/status`          | LoRa radio status                    |
| POST   | `/api/lora/reboot-status`   | Coordinated LoRa param reboot status |
| POST   | `/api/mqtt/test`            | Test MQTT broker connection          |
| POST   | `/api/alerts/test`          | Send test Teams alert                |
| POST   | `/api/alerts/test-email`    | Send test email                      |

---

## Security

### Current State
- **Network isolation:** The LoRa sync word is derived from the Network ID (`0x12 + (networkId % 244)`), providing basic RF-level separation between networks.
- **Packet validation:** All packets carry a CRC16 checksum and a fixed sync word. Malformed packets are silently dropped.
- **Web interface:** Session-based authentication via Flask secret key (`SECRET_KEY` in `config.py` — must be changed in production).

### Known Gaps (must be addressed before production deployment)
- **No payload encryption.** LoRa transmissions are plaintext and can be received by any radio tuned to the same frequency and SF. AES-128 with a pre-shared key should be added at the application layer.
- **No key exchange mechanism.** There is currently no protocol for securely distributing the encryption key to new nodes.
- **No replay protection beyond sequence numbers.** Sequence numbers wrap and a determined attacker could replay captured packets.

---

## Testing Requirements

Full automated test suites are required for both sides.

### Python Base Station (`pytest`)
- Packet parser: deserialisation of all packet types, checksum validation, malformed input rejection.
- Sensor store: state transitions, timeout logic, history eviction, SQLite round-trip.
- Remote config: command queuing, retry expiry, ACK/NACK processing.
- Alert manager: rate limiting, Teams/email dispatch (mocked HTTP).
- Flask API: all endpoints, error cases, authentication.

### Arduino Firmware (PlatformIO)
- Packet codec: serialise → deserialise round-trip for all packet types.
- Checksum: known-good and known-bad vectors.
- Mesh routing: RREQ/RREP cycle, TTL expiry, route table eviction.
- Command queue: retry counter, timeout, ACK clearing.
- Sensor interface: mock sensor returning fixed values; `read()` / `getValue()` contract.


---

## Development Workflow

- **Commit often, push selectively.** Local commits are cheap; pushed commits should be coherent units of work.
- **Sync-required changes** (packet format, command codes, LoRa defaults) must be pushed and deployed to both base station and all clients together. Tag these commits clearly.
- **Feature branches** for anything non-trivial. Merge to `main` only when both sides are consistent.
- **CHANGELOG.md** must be updated with every version bump using [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.
- **Every function, struct, and config constant must have a doc comment.** The packet protocol and command table in this document are the source of truth — keep them in sync with the code.

---

## Base Station Responsibilities

- Coordinate the mesh network (permanent coordinator, node ID 0)
- Receive and parse telemetry packets from all client nodes
- Store sensor data in SQLite with timestamps (time-series)
- Queue and deliver configuration commands to client nodes with retry
- Synchronise time to all nodes on startup and every 3 hours
- Provide a Flask web interface for monitoring and configuration
- Publish sensor data to an MQTT broker (optional)
- Send alert notifications (Teams webhook, SMTP email) on threshold violations
- Monitor node health (online/offline status, battery, RSSI/SNR trends)

---

## Client Responsibilities

- Announce presence on boot via `CMD_SENSOR_ANNOUNCE`
- Transmit multi-sensor telemetry packets at a configurable interval
- Receive and ACK commands from the base station
- Apply remote configuration (interval, location, LoRa params, thresholds)
- Maintain local RTC synchronised from base station time sync commands
- Participate in mesh routing (forward packets for other nodes when enabled)
- Report battery voltage and charge state in every telemetry packet
- Operate correctly on battery power; minimise unnecessary transmissions
