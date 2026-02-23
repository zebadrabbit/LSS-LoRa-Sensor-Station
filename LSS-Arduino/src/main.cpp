/**
 * @file main.cpp
 * @brief LSS client node main firmware for Heltec WiFi LoRa 32 v3.
 *
 * Startup sequence:
 *  1. Load NodeConfig from NVS.
 *  2. Initialise LoRa radio with saved parameters.
 *  3. Initialise attached sensors.
 *  4. Broadcast CMD_SENSOR_ANNOUNCE to enrol with the base station.
 *  5. Enter the main loop: read sensors, transmit telemetry, receive commands.
 */

#include <Arduino.h>
#include "packets.h"
#include "mesh.h"
#include "node_config.h"
#include "sensors.h"
#include "sensor_base.h"

// Heltec LoRa 32 v3 uses the RadioLib SX126x driver bundled with the board package.
#include <RadioLib.h>

// ============================================================
// Hardware pin definitions (Heltec WiFi LoRa 32 v3)
// ============================================================

#define PIN_LED         35   ///< Built-in LED (GPIO 35)
#define PIN_BUTTON       0   ///< User button (GPIO 0, active-low)
#define PIN_BATT_ADC     1   ///< Battery sense ADC (enabled via GPIO 37)
#define PIN_BATT_EN     37   ///< Battery ADC enable pin

// SX1262 wiring (Heltec v3 board-internal)
#define LORA_CS         8
#define LORA_IRQ       14
#define LORA_RST       12
#define LORA_BUSY      13

// Voltage divider ratio for battery sense (390 kΩ / 100 kΩ → ×4.9)
#define BATT_DIVIDER_RATIO 4.9f
#define BATT_ADC_REF_MV  3300.0f
#define BATT_ADC_BITS    4095.0f

// Battery voltage to % LUT (3.0 V = 0%, 4.2 V = 100%)
#define BATT_FULL_MV   4200.0f
#define BATT_EMPTY_MV  3000.0f

// ============================================================
// Module globals
// ============================================================

static SX1262 radio = new Module(LORA_CS, LORA_IRQ, LORA_RST, LORA_BUSY);
static NodeConfigStore  cfg_store;
static MeshRouter      *mesh_router  = nullptr;

// Sensor array — populate in setup() for your hardware configuration.
// Example: one DHT22 on GPIO 4.
static SensorBase *sensors[16] = {};
static uint8_t     sensor_count = 0;

static uint32_t  last_tx_ms    = 0;
static uint8_t   tx_buf[255];
static uint8_t   rx_buf[255];

// Flags set from RadioLib ISR
static volatile bool rx_done_flag = false;
static volatile bool tx_done_flag = false;

// ============================================================
// Battery helpers
// ============================================================

/**
 * Read battery voltage and return percentage (0–100).
 *
 * Requires GPIO 37 pulled high to enable the ADC divider.
 */
static uint8_t read_battery_percent()
{
    digitalWrite(PIN_BATT_EN, HIGH);
    delayMicroseconds(100);
    int raw = analogRead(PIN_BATT_ADC);
    digitalWrite(PIN_BATT_EN, LOW);

    float mv = ((float)raw / BATT_ADC_BITS) * BATT_ADC_REF_MV * BATT_DIVIDER_RATIO;
    float pct = 100.0f * (mv - BATT_EMPTY_MV) / (BATT_FULL_MV - BATT_EMPTY_MV);
    if (pct < 0.0f) pct = 0.0f;
    if (pct > 100.0f) pct = 100.0f;
    return (uint8_t)pct;
}

// ============================================================
// RadioLib interrupt callbacks
// ============================================================

static void IRAM_ATTR on_rx_done()  { rx_done_flag = true; }
static void IRAM_ATTR on_tx_done()  { tx_done_flag = true; }

// ============================================================
// Transmission helpers
// ============================================================

/**
 * Build a MultiSensorPacket from all attached sensors and transmit it.
 */
static void transmit_telemetry(uint8_t last_cmd_seq, uint8_t ack_status)
{
    MultiSensorPacket pkt;
    memset(&pkt, 0, sizeof(pkt));

    const NodeConfig &cfg = cfg_store.config();

    pkt.header.syncWord       = SYNC_MULTI_SENSOR;
    pkt.header.networkId      = cfg.networkId;
    pkt.header.packetType     = PACKET_MULTI_SENSOR;
    pkt.header.sensorId       = cfg.nodeId;
    pkt.header.batteryPercent = read_battery_percent();
    pkt.header.powerState     = 0;   // charging detection not implemented here
    pkt.header.lastCommandSeq = last_cmd_seq;
    pkt.header.ackStatus      = ack_status;
    strncpy(pkt.header.location, cfg.location, 31);
    strncpy(pkt.header.zone,     cfg.zone,     15);

    uint8_t vcount = 0;
    for (uint8_t i = 0; i < sensor_count && vcount < MAX_SENSOR_VALUES; i++) {
        if (!sensors[i] || !sensors[i]->is_ready()) continue;
        sensors[i]->read();
        SensorValuePacket tmp[4];
        uint8_t n = sensors[i]->values(tmp, 4);
        for (uint8_t j = 0; j < n && vcount < MAX_SENSOR_VALUES; j++) {
            pkt.values[vcount++] = tmp[j];
        }
    }
    pkt.header.valueCount = vcount;

    // Optionally wrap in a mesh frame
    uint8_t dest = 0;  // Base station = node 0
    size_t raw_len;

    if (cfg.meshEnabled && mesh_router) {
        uint8_t payload[255];
        size_t pay_len = lss_serialize_multi_sensor(&pkt, payload, sizeof(payload));
        if (pay_len == 0) return;
        raw_len = mesh_router->wrap(dest, payload, pay_len, tx_buf, sizeof(tx_buf));
    } else {
        raw_len = lss_serialize_multi_sensor(&pkt, tx_buf, sizeof(tx_buf));
    }

    if (raw_len > 0) {
        radio.startTransmit(tx_buf, (size_t)raw_len);
    }
}

/**
 * Broadcast CMD_SENSOR_ANNOUNCE to enrol with the base station.
 */
static void send_announce()
{
    CommandPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.syncWord       = SYNC_COMMAND;
    pkt.commandType    = CMD_SENSOR_ANNOUNCE;
    pkt.targetSensorId = cfg_store.config().nodeId;
    pkt.sequenceNumber = 0;
    pkt.dataLength     = 0;

    size_t len = lss_serialize_command(&pkt, tx_buf, sizeof(tx_buf));
    if (len > 0) {
        radio.transmit(tx_buf, len);
    }
}

// ============================================================
// setup()
// ============================================================

void setup()
{
    Serial.begin(115200);
    Serial.println("LSS node starting");

    // Load persistent configuration from NVS
    cfg_store.load();
    const NodeConfig &cfg = cfg_store.config();

    // GPIO init
    pinMode(PIN_LED,    OUTPUT);
    pinMode(PIN_BUTTON, INPUT_PULLUP);
    pinMode(PIN_BATT_EN, OUTPUT);
    digitalWrite(PIN_BATT_EN, LOW);

    // Initialise LoRa radio
    int16_t state = radio.begin(
        cfg.loraFrequency,
        125.0f,                          // bandwidth kHz
        cfg.loraSpreadingFactor,
        5,                               // coding rate 4/5
        0x12 + (cfg.networkId % 244),    // sync word
        cfg.loraTxPower,
        8                                // preamble length
    );
    if (state != RADIOLIB_ERR_NONE) {
        Serial.printf("Radio init failed: %d\n", state);
    }
    radio.setDio1Action(on_rx_done);
    radio.startReceive();

    // Initialise mesh router
    mesh_router = new MeshRouter(cfg.nodeId, cfg.meshEnabled);

    // ------------------------------------------------------------------
    // Add sensors here for your hardware configuration.
    // Example: DHT22 on GPIO 4.
    // ------------------------------------------------------------------
    // sensors[sensor_count++] = new DHTSensor(4, 22);
    // sensors[sensor_count++] = new DS18B20Sensor(5);
    // sensors[sensor_count++] = new BME680Sensor();

    for (uint8_t i = 0; i < sensor_count; i++) {
        if (!sensors[i]->begin()) {
            Serial.printf("Sensor %d (%s) failed to initialise\n",
                          i, sensors[i]->name());
        }
    }

    // Announce presence to base station
    delay(500);
    send_announce();
    Serial.printf("Node %d announced on network %d\n",
                  cfg.nodeId, cfg.networkId);
}

// ============================================================
// loop()
// ============================================================

void loop()
{
    const NodeConfig &cfg = cfg_store.config();

    // ------------------------------------------------------------------
    // Receive path
    // ------------------------------------------------------------------
    if (rx_done_flag) {
        rx_done_flag = false;
        size_t len = 0;
        int16_t state = radio.readData(rx_buf, sizeof(rx_buf) - 1);
        if (state == RADIOLIB_ERR_NONE) {
            len = radio.getPacketLength();
            PacketType ptype;

            // Try to detect the packet at offset 0 (raw LSS frame, node-to-node).
            // If that fails, retry at offset 4: adafruit_rfm9x on the base station
            // prepends a 4-byte RadioHead header [dest, node, id, flags] that the
            // Arduino did not send and we must skip before parsing.
            const uint8_t *parse_buf = rx_buf;
            size_t parse_len = len;
            if (!lss_detect_packet(rx_buf, len, &ptype) && len > 4) {
                parse_buf = rx_buf + 4;
                parse_len = len - 4;
            }

            if (lss_detect_packet(parse_buf, parse_len, &ptype)) {
                if (ptype == PACKET_CONFIG) {
                    CommandPacket cmd;
                    if (lss_deserialize_command(parse_buf, parse_len, &cmd)) {
                        if (cmd.targetSensorId == cfg.nodeId ||
                            cmd.targetSensorId == 255) {
                            // Process command and send ACK
                            size_t ack_len = handle_command(
                                &cmd, cfg_store, *mesh_router,
                                tx_buf, sizeof(tx_buf)
                            );
                            if (ack_len > 0) {
                                // Send ACK after a brief backoff
                                delay(50);
                                radio.transmit(tx_buf, ack_len);
                                radio.startReceive();
                            }
                        }
                    }
                }
            }
        }
        radio.startReceive();
    }

    // ------------------------------------------------------------------
    // Transmit path: send telemetry on schedule
    // ------------------------------------------------------------------
    if (tx_done_flag) {
        tx_done_flag = false;
        radio.startReceive();
    }

    uint32_t now = millis();
    if (now - last_tx_ms >= cfg.telemetryIntervalMs) {
        last_tx_ms = now;
        transmit_telemetry(0, 0);
    }

    // ------------------------------------------------------------------
    // Mesh beacon
    // ------------------------------------------------------------------
    if (mesh_router && cfg.meshEnabled) {
        uint8_t beacon[sizeof(MeshHeader)];
        size_t blen = mesh_router->tick(beacon, sizeof(beacon));
        if (blen > 0) {
            radio.transmit(beacon, blen);
            radio.startReceive();
        }
    }

    // ------------------------------------------------------------------
    // LED heartbeat — on while actively transmitting, off otherwise
    // ------------------------------------------------------------------
    digitalWrite(PIN_LED, (now % 2000) < 50 ? HIGH : LOW);
}
