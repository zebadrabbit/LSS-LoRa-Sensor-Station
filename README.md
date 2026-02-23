# LSS — LoRa Sensor Station

A mesh-networked sensor platform built on a Raspberry Pi base station and Heltec WiFi LoRa 32 v3 client nodes.
The base station coordinates the LoRa network, stores time-series sensor data, and exposes a web dashboard and REST API.
Client nodes read one or more sensors, transmit telemetry over LoRa, and accept remote configuration commands.

---

## Contents

- [Hardware](#hardware)
- [Repository structure](#repository-structure)
- [Base station setup](#base-station-setup)
- [Client firmware setup](#client-firmware-setup)
- [Configuration](#configuration)
- [Web dashboard & API](#web-dashboard--api)
- [Supported sensors](#supported-sensors)
- [Packet protocol](#packet-protocol)
- [Testing](#testing)
- [Development workflow](#development-workflow)
- [Security](#security)

---

## Hardware

### Base station

| Component | Part |
|-----------|------|
| Computer | Raspberry Pi 5 |
| LoRa radio | [Adafruit LoRa Radio Bonnet — RFM95W @ 915 MHz](https://www.adafruit.com/product/4074) |
| Display | 5" LCD with touchscreen |

**SPI wiring (BCM pin numbers)**

| Signal | GPIO (BCM) | Board pin |
|--------|-----------|-----------|
| CS     | 7 (SPI CE1) | 26 |
| IRQ (G0) | 24 | 18 |
| RST    | 25 | 22 |

### Client node

| Component | Part |
|-----------|------|
| MCU + radio | [Heltec WiFi LoRa 32 v3](https://heltec.org/project/wifi-lora-32/) (SX1262 integrated) |

**Notable GPIO**

| Signal | GPIO |
|--------|------|
| Built-in LED | 35 |
| User button | 0 |
| Battery ADC | 1 (enable via GPIO 37) |

Battery voltage divider ratio: **4.9×** (390 kΩ / 100 kΩ)

---

## Repository structure

```
LSS/
├── LSS.md                        ← Protocol specification (source of truth)
├── CHANGELOG.md
│
├── BaseStation/                  ← Python application (Raspberry Pi)
│   ├── main.py                   ← Entry point
│   ├── requirements.txt
│   ├── pyproject.toml            ← pytest config
│   ├── systemd/
│   │   └── lss-basestation.service
│   ├── lss_basestation/
│   │   ├── config.py             ← Radio params, constants, enums
│   │   ├── config_storage.py     ← Persistent JSON config
│   │   ├── packet_parser.py      ← Packet codec (all 4 types)
│   │   ├── sensor_store.py       ← In-memory state + SQLite history
│   │   ├── remote_config.py      ← Outbound command queue with retry
│   │   ├── lora_manager.py       ← RFM95W driver, RX/TX loops
│   │   ├── mqtt_manager.py       ← MQTT publish
│   │   ├── alerts.py             ← Teams webhook + email alerts
│   │   └── web/
│   │       ├── app.py            ← Flask REST API + dashboard
│   │       └── templates/
│   ├── data/                     ← Runtime: config.json, sensors.db, logs
│   └── tests/                    ← pytest suite (94 tests)
│
└── LSS-Arduino/                  ← PlatformIO firmware (Heltec client node)
    ├── platformio.ini
    ├── include/
    │   ├── packets.h             ← Packet structs and codec declarations
    │   ├── mesh.h                ← MeshRouter
    │   ├── sensor_base.h         ← Abstract SensorBase interface
    │   ├── sensors.h             ← Concrete sensor driver declarations
    │   └── node_config.h         ← NVS config store + command handler
    ├── src/
    │   ├── packets.cpp           ← CRC-16, serialise/deserialise
    │   ├── mesh.cpp              ← AODV routing, beacons, TTL enforcement
    │   ├── sensors.cpp           ← All sensor driver implementations
    │   ├── node_config.cpp       ← NVS read/write, factory reset
    │   ├── command_handler.cpp   ← Applies all 14 command types
    │   └── main.cpp              ← setup() + loop()
    └── test/
        ├── test_packets/         ← Unity: codec round-trips, CRC vectors
        ├── test_mesh/            ← Unity: routing, TTL, beacons
        └── test_support/         ← Arduino stubs for native host build
```

---

## Base station setup

### 1. Enable SPI on the Raspberry Pi

```bash
sudo raspi-config   # Interface Options → SPI → Enable
```

### 2. Clone and install

```bash
git clone git@github.com:zebadrabbit/LSS-LoRa-Sensor-Station.git
cd LSS-LoRa-Sensor-Station/BaseStation

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Enable the hardware LoRa driver

Uncomment the hardware lines in `requirements.txt`:

```
adafruit-circuitpython-rfm9x>=2.4
RPi.GPIO>=0.7
```

Then re-run `pip install -r requirements.txt`.

### 4. Run

```bash
python main.py
```

The web dashboard will be available at `http://<pi-ip>:5000`.
Default password: `admin` — **change this before exposing to a network** (see [Configuration](#configuration)).

### 5. Install as a systemd service (optional)

```bash
sudo cp systemd/lss-basestation.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lss-basestation
```

Adjust the `User=` and `WorkingDirectory=` lines in the service file if your paths differ.

---

## Client firmware setup

### Requirements

- [PlatformIO](https://platformio.org/) (VS Code extension or CLI)

### 1. Set the node ID

Each node requires a unique ID (1–254) stored in NVS.
Before first flash, edit `LSS-Arduino/src/main.cpp` and set the default node ID, or flash once and use `CMD_FACTORY_RESET` followed by setting IDs via the NVS provisioning approach of your choice.

> Node ID 0 is reserved for the base station. Node ID 255 is the broadcast address.

### 2. Add your sensors

In `LSS-Arduino/src/main.cpp`, uncomment and configure the sensors attached to your node:

```cpp
// Example: DHT22 on GPIO 4
sensors[sensor_count++] = new DHTSensor(4, 22);

// Example: DS18B20 on GPIO 5
sensors[sensor_count++] = new DS18B20Sensor(5);

// Example: BME680 at default I2C address
sensors[sensor_count++] = new BME680Sensor();
```

### 3. Build and flash

```bash
cd LSS-Arduino
pio run -e heltec_wifi_lora_32_V3 --target upload
```

On first boot the node broadcasts `CMD_SENSOR_ANNOUNCE`. The base station responds with `CMD_BASE_WELCOME` (time sync + network config) and the node begins sending telemetry.

---

## Configuration

Runtime configuration is stored in `BaseStation/data/config.json` and is created automatically on first run with sensible defaults.
It can be edited directly or updated via the web interface at `/api/config`.

### Key settings

| Key | Default | Description |
|-----|---------|-------------|
| `network_id` | `1` | Isolates networks at the radio level via sync word |
| `lora.frequency` | `915.0` | MHz |
| `lora.spreading_factor` | `10` | SF7–SF12 |
| `lora.tx_power` | `20` | dBm |
| `mqtt.enabled` | `false` | Enable MQTT publishing |
| `mqtt.broker` | `localhost` | MQTT broker hostname |
| `alerts.teams_webhook_url` | `""` | Microsoft Teams incoming webhook |
| `alerts.smtp_host` | `""` | SMTP server for email alerts |
| `web_password` | `admin` | Dashboard login password — **change this** |

> **Sync-required:** changes to `network_id` or any `lora.*` parameter must be deployed to the base station and all client nodes simultaneously. Tag these commits clearly.

---

## Web dashboard & API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Live dashboard (auto-refreshes every 30 s) |
| GET | `/api/sensors` | All node current state (JSON) |
| GET | `/api/sensors/<id>/history` | Time-series rows for a node |
| POST | `/api/command` | Queue a command to a client node |
| POST | `/api/command/ping/<id>` | Ping a node |
| POST | `/api/command/restart/<id>` | Reboot a node |
| GET | `/api/command/pending` | All queued / in-flight commands |
| GET | `/api/config` | Read base station config |
| POST | `/api/config` | Update base station config |
| GET | `/api/lora/status` | LoRa radio status |
| POST | `/api/mqtt/test` | Test MQTT connection |
| POST | `/api/alerts/test` | Send a test Teams alert |
| POST | `/api/alerts/test-email` | Send a test email |

All API endpoints require a valid session. Credentials in GET responses are redacted to `***`.

### Queuing a command (example)

```bash
curl -X POST http://localhost:5000/api/command \
  -H "Content-Type: application/json" \
  -b "session=<cookie>" \
  -d '{"node_id": 3, "command_type": 2, "data": "983a0000"}'
```

(`0x00003a98` = 15000 ms interval, little-endian)

---

## Supported sensors

| Sensor | Interface | Value types |
|--------|-----------|-------------|
| DHT22 / DHT11 | GPIO | Temperature (°C), Humidity (%RH) |
| DS18B20 | 1-Wire | Temperature (°C) |
| BME680 | I2C | Temperature, Humidity, Pressure (hPa), Gas resistance (Ω) |
| BH1750 | I2C | Illuminance (lx) |
| INA219 | I2C | Voltage (V), Current (mA), Power (mW) |
| SHT31 | I2C | Temperature (°C), Humidity (%RH) |
| BMP280 | I2C | Temperature (°C), Pressure (hPa) |
| NTC Thermistor | ADC | Temperature (°C, Steinhart–Hart) |
| Resistive soil moisture | ADC | Moisture (%) |

Multiple sensors can be attached to a single node. Up to **16 values** are packed into each telemetry transmission.

---

## Packet protocol

All packets use little-endian byte order and carry a **CRC-16/CCITT-FALSE** checksum.
`LSS.md` is the authoritative protocol specification — keep it in sync with the code.

### Sync words

| Sync word | Type |
|-----------|------|
| `0xABCD` | Multi-sensor telemetry (v2.9+, preferred) |
| `0xCDEF` | Command / ACK |
| `0x1234` | Legacy v1 telemetry (backward compat only) |

### LoRa radio defaults

| Parameter | Value |
|-----------|-------|
| Frequency | 915.0 MHz |
| Spreading factor | SF10 |
| Bandwidth | 125 kHz |
| Coding rate | 4/5 |
| TX power | 20 dBm |
| Sync word | `0x12 + (network_id % 244)` |

### Command set (summary)

| Code | Command | Direction |
|------|---------|-----------|
| `0x00` | `CMD_PING` | Base → Client |
| `0x02` | `CMD_SET_INTERVAL` | Base → Client |
| `0x03` | `CMD_SET_LOCATION` | Base → Client |
| `0x07` | `CMD_RESTART` | Base → Client |
| `0x09` | `CMD_SET_LORA_PARAMS` | Base → Client |
| `0x0A` | `CMD_TIME_SYNC` | Base → Client |
| `0x0B` | `CMD_SENSOR_ANNOUNCE` | Client → Base |
| `0x0C` | `CMD_BASE_WELCOME` | Base → Client |
| `0xA0` | `CMD_ACK` | Client → Base |
| `0xA1` | `CMD_NACK` | Client → Base |

Full command table in `LSS.md`.

---

## Testing

### Python (pytest)

```bash
cd BaseStation
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

94 tests covering packet codec, sensor store (SQLite round-trips, offline watchdog), command queue (retry, ACK, piggyback), alert rate-limiting, and the full Flask API.

### Arduino firmware (Unity — native host build)

```bash
cd LSS-Arduino
pio test -e native
```

Tests run on your host machine with no hardware required. Covers CRC vectors, packet encode/decode round-trips, mesh route CRUD, TTL enforcement, route eviction, and beacon timing.

---

## Development workflow

- **Commit often, push selectively.** Local commits are cheap; pushed commits should be coherent units.
- **Sync-required changes** (packet format, command codes, LoRa defaults) must be pushed and deployed to both base station and all client nodes together. Tag these commits with a version and note `[SYNC REQUIRED]` in the commit message.
- **Feature branches** for anything non-trivial. Merge to `main` only when both sides are consistent.
- **CHANGELOG.md** must be updated with every version bump using [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.
- **Every function, struct, and config constant must have a doc comment.** `LSS.md` is the source of truth — keep it in sync with the code.

---

## Security

The current implementation provides basic isolation and integrity checking but is **not hardened for production or public-network deployment**.

| Control | Status |
|---------|--------|
| RF-level network isolation via sync word | ✅ Implemented |
| CRC-16 packet integrity | ✅ Implemented |
| Session-based web auth | ✅ Implemented |
| LoRa payload encryption (AES-128) | ❌ Not yet implemented |
| Replay protection beyond sequence numbers | ❌ Not yet implemented |
| Key exchange for new nodes | ❌ Not yet implemented |

Before deploying in any environment where LoRa transmissions may be received by untrusted parties, AES-128 with a pre-shared key should be added at the application layer.

Change `SECRET_KEY` in `BaseStation/lss_basestation/config.py` and `web_password` in `config.json` before production use.
