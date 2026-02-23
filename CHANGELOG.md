# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

## [0.1.0] — 2026-02-22

### Added

#### Base Station (Python)
- `config.py` — Authoritative LoRa radio parameters, packet protocol constants,
  command codes, value types, and operational constants.
- `config_storage.py` — Thread-safe persistent JSON configuration with
  atomic save and named-section helpers.
- `packet_parser.py` — Struct-based codec for all four packet types
  (legacy v1, multi-sensor v2.9+, command, ACK/NACK) with CRC-16/CCITT-FALSE
  validation and `build_command` serialiser.
- `sensor_store.py` — In-memory node state (up to 10 nodes, 120 history points
  per node) backed by SQLite WAL for time-series persistence; background watchdog
  for offline detection.
- `remote_config.py` — Outbound command queue with up to 3 retry attempts and
  12-second per-attempt timeout; piggybacked-ACK processing; factory helpers
  for all outbound command types.
- `lora_manager.py` — RFM95W SPI driver wrapper (adafruit-circuitpython-rfm9x);
  background RX and TX threads; auto-enrollment (CMD_SENSOR_ANNOUNCE →
  CMD_BASE_WELCOME); periodic time-sync every 3 hours; threshold alert checks.
- `mqtt_manager.py` — Non-blocking paho MQTT client with auto-reconnect;
  per-node topic tree `<prefix>/<id>/<value>`; online/offline LWT-style
  publish; `test_connection` helper.
- `alerts.py` — Microsoft Teams (MessageCard webhook) and SMTP email
  notifications; per-key rate limiting; async dispatch.
- `web/app.py` — Flask application factory; session-based auth; full REST API
  surface (sensors, history, commands, config, LoRa status, MQTT test,
  alerts test); Jinja2 dashboard with auto-refresh; credential redaction on GET.
- `systemd/lss-basestation.service` — systemd unit for automatic startup on
  Raspberry Pi.

#### Client Firmware (C++/PlatformIO)
- `packets.h / packets.cpp` — Packed C structs matching the Python codec;
  CRC-16/CCITT-FALSE; `lss_serialize_*` / `lss_deserialize_*` / `lss_build_ack`;
  `lss_detect_packet` sync-word probe.
- `mesh.h / mesh.cpp` — `MeshRouter`: AODV-inspired route table (20 entries,
  10-minute timeout); `wrap()` / `receive()` with hop-count enforcement;
  periodic neighbor beacon every 30 seconds; route eviction.
- `sensor_base.h` — Abstract `SensorBase` interface (`begin`, `read`, `values`).
- `sensors.h / sensors.cpp` — Concrete drivers: DHT22/DHT11, DS18B20 (1-Wire),
  BME680, BH1750, INA219, SHT31, BMP280, NTC thermistor (Steinhart–Hart),
  resistive soil moisture; all gated behind `#ifndef TEST_BUILD` for host tests.
- `node_config.h / node_config.cpp` — `NodeConfigStore` with ESP32 NVS
  Preferences read/write; `factory_reset()` wipes the namespace.
- `command_handler.cpp` — Stateless `handle_command()` applies all 14 command
  types and returns a serialised CMD_ACK or CMD_NACK.
- `main.cpp` — Full firmware loop: NVS load → radio init → sensor init →
  CMD_SENSOR_ANNOUNCE → RX dispatch → telemetry TX schedule → mesh beacon.

#### Tests
- 94 pytest tests: packet codec, sensor store (SQLite round-trip, offline
  watchdog, MAX_NODES cap), command queue (retry, ACK, piggyback), alerts
  (rate-limiting, mocked HTTP/SMTP), Flask API (auth, all endpoints, 404).
- Unity test suites (native build): packet codec (CRC vectors, round-trips,
  bad-CRC rejection, size computation) and mesh router (route CRUD, TTL
  enforcement, staleness eviction, beacon timing).

### Sync-Required
- Initial packet protocol definition — both base station and all client nodes
  must run this version simultaneously. Tag: `v0.1.0`.

[Unreleased]: https://github.com/zebadrabbit/LSS-LoRa-Sensor-Station/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/zebadrabbit/LSS-LoRa-Sensor-Station/releases/tag/v0.1.0
