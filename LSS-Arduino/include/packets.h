/**
 * @file packets.h
 * @brief LSS packet protocol definitions and codec.
 *
 * All structs are packed and use little-endian byte order to match
 * the Python base station's struct module format strings.
 *
 * SOURCE OF TRUTH: LSS.md § Packet Protocol
 * Any change here is a sync-required change — deploy to both sides.
 */

#pragma once
#include <Arduino.h>

// ============================================================
// Application-level sync words
// ============================================================

/** Legacy v1 telemetry packet sync word. */
#define SYNC_LEGACY        0x1234u
/** Multi-sensor telemetry packet sync word (v2.9+). */
#define SYNC_MULTI_SENSOR  0xABCDu
/** Command / ACK packet sync word. */
#define SYNC_COMMAND       0xCDEFu

// ============================================================
// Packet type codes
// ============================================================

typedef enum __attribute__((packed)) {
    PACKET_LEGACY       = 0,  ///< SensorData v1 — backward compat only
    PACKET_MULTI_SENSOR = 1,  ///< MultiSensorHeader (v2.9+)
    PACKET_CONFIG       = 2,  ///< Configuration data / command
    PACKET_ACK          = 3,  ///< Acknowledgment
} PacketType;

// ============================================================
// Command codes
// ============================================================

typedef enum __attribute__((packed)) : uint8_t {
    CMD_PING              = 0x00,
    CMD_GET_CONFIG        = 0x01,
    CMD_SET_INTERVAL      = 0x02,
    CMD_SET_LOCATION      = 0x03,
    CMD_SET_TEMP_THRESH   = 0x04,
    CMD_SET_BATTERY_THRESH = 0x05,
    CMD_SET_MESH_CONFIG   = 0x06,
    CMD_RESTART           = 0x07,
    CMD_FACTORY_RESET     = 0x08,
    CMD_SET_LORA_PARAMS   = 0x09,
    CMD_TIME_SYNC         = 0x0A,
    CMD_SENSOR_ANNOUNCE   = 0x0B,
    CMD_BASE_WELCOME      = 0x0C,
    CMD_ACK               = 0xA0,
    CMD_NACK              = 0xA1,
} CommandType;

// ============================================================
// Value types (SensorValuePacket.type)
// ============================================================

typedef enum __attribute__((packed)) : uint8_t {
    VALUE_TEMPERATURE            = 0,
    VALUE_HUMIDITY               = 1,
    VALUE_PRESSURE               = 2,
    VALUE_LIGHT                  = 3,
    VALUE_VOLTAGE                = 4,
    VALUE_CURRENT                = 5,
    VALUE_POWER                  = 6,
    VALUE_ENERGY                 = 7,
    VALUE_GAS_RESISTANCE         = 8,
    VALUE_BATTERY                = 9,
    VALUE_SIGNAL_STRENGTH        = 10,
    VALUE_MOISTURE               = 11,
    VALUE_GENERIC                = 12,
    VALUE_THERMISTOR_TEMPERATURE = 13,
} ValueType;

// ============================================================
// Packet structs
// ============================================================

/** Single typed measurement, repeated valueCount times after the header. */
typedef struct __attribute__((packed)) {
    uint8_t type;   ///< ValueType
    float   value;
} SensorValuePacket;

/**
 * Multi-sensor telemetry header (v2.9+).
 *
 * Followed by valueCount SensorValuePacket entries then a uint16 CRC.
 * Maximum 16 SensorValuePacket entries per transmission.
 */
typedef struct __attribute__((packed)) {
    uint16_t syncWord;        ///< SYNC_MULTI_SENSOR = 0xABCD
    uint16_t networkId;
    uint8_t  packetType;      ///< PACKET_MULTI_SENSOR = 1
    uint8_t  sensorId;        ///< Node ID (1–254)
    uint8_t  valueCount;      ///< Number of following SensorValuePacket entries
    uint8_t  batteryPercent;
    uint8_t  powerState;      ///< 0 = discharging, 1 = charging
    uint8_t  lastCommandSeq;  ///< Piggybacked ACK sequence number
    uint8_t  ackStatus;       ///< 0 = success, non-zero = error code
    uint8_t  _pad;            ///< Alignment pad (matches Python 'x')
    char     location[32];
    char     zone[16];
} MultiSensorHeader;

/** Maximum sensor values per multi-sensor packet. */
#define MAX_SENSOR_VALUES 16

/** Full multi-sensor packet assembled in memory before transmission. */
typedef struct {
    MultiSensorHeader header;
    SensorValuePacket values[MAX_SENSOR_VALUES];
    uint16_t          checksum;
} MultiSensorPacket;

/**
 * Command packet sent from base station to a client node.
 * Maximum total size: 200 bytes.
 */
typedef struct __attribute__((packed)) {
    uint16_t syncWord;        ///< SYNC_COMMAND = 0xCDEF
    uint8_t  commandType;     ///< CommandType
    uint8_t  targetSensorId;  ///< Destination node ID (255 = broadcast)
    uint8_t  sequenceNumber;  ///< Monotonic counter for ACK correlation
    uint8_t  dataLength;      ///< Number of valid bytes in data[]
    uint8_t  _pad;            ///< Alignment pad — must be zero
    uint8_t  data[192];       ///< Command-specific payload
    uint16_t checksum;        ///< CRC-16/CCITT-FALSE over all preceding bytes
} CommandPacket;

/** ACK / NACK packet sent from a client node to the base station. */
typedef struct __attribute__((packed)) {
    uint16_t syncWord;        ///< SYNC_COMMAND = 0xCDEF
    uint8_t  commandType;     ///< CMD_ACK or CMD_NACK
    uint8_t  sensorId;        ///< Responding node ID
    uint8_t  sequenceNumber;  ///< Matches originating CommandPacket.sequenceNumber
    uint8_t  statusCode;      ///< 0 = success; non-zero = implementation-defined error
    uint8_t  dataLength;      ///< Number of valid bytes in data[]
    uint8_t  _pad;            ///< Alignment pad — must be zero
    uint8_t  data[192];       ///< Optional response payload
    uint16_t checksum;        ///< CRC-16/CCITT-FALSE over all preceding bytes
} AckPacket;

/** Legacy v1 SensorData packet (backward compatibility only). */
typedef struct __attribute__((packed)) {
    uint16_t syncWord;        ///< SYNC_LEGACY = 0x1234
    uint8_t  sensorId;        ///< Node ID (1–254)
    uint16_t networkId;       ///< Network identifier
    float    temperature;     ///< °C
    float    humidity;        ///< %RH
    uint8_t  batteryPercent;  ///< 0–100 %
    int8_t   rssi;            ///< Last-hop RSSI in dBm (reported by node)
    float    snr;             ///< Last-hop SNR in dB (reported by node)
} SensorDataLegacy;

// ============================================================
// Codec functions
// ============================================================

/**
 * Compute CRC-16/CCITT-FALSE over len bytes of data.
 *
 * @param data   Pointer to input buffer.
 * @param len    Number of bytes to include.
 * @return       16-bit CRC.
 */
uint16_t lss_crc16(const uint8_t *data, size_t len);

/**
 * Serialise a MultiSensorPacket into buf.
 *
 * Fills the syncWord, packetType, nodeId, computes the checksum, and
 * copies header + values + checksum into buf.
 *
 * @param pkt    Packet to serialise (header and values must be filled).
 * @param buf    Output buffer (must be at least lss_multi_sensor_size(pkt) bytes).
 * @return       Number of bytes written, or 0 on error.
 */
size_t lss_serialize_multi_sensor(const MultiSensorPacket *pkt,
                                  uint8_t *buf, size_t buf_len);

/**
 * Deserialise a raw buffer into a MultiSensorPacket.
 *
 * @param buf    Raw received bytes.
 * @param len    Length of buf.
 * @param out    Output packet struct.
 * @return       true on success, false on length/CRC/sync failure.
 */
bool lss_deserialize_multi_sensor(const uint8_t *buf, size_t len,
                                  MultiSensorPacket *out);

/**
 * Serialise a CommandPacket into buf (for use by the base station stub).
 *
 * @return Number of bytes written, or 0 on error.
 */
size_t lss_serialize_command(const CommandPacket *pkt,
                             uint8_t *buf, size_t buf_len);

/**
 * Deserialise a raw buffer into a CommandPacket.
 *
 * @return true on success.
 */
bool lss_deserialize_command(const uint8_t *buf, size_t len,
                             CommandPacket *out);

/**
 * Serialise an AckPacket.
 *
 * @return Number of bytes written, or 0 on error.
 */
size_t lss_serialize_ack(const AckPacket *pkt,
                         uint8_t *buf, size_t buf_len);

/**
 * Build and serialise a CMD_ACK or CMD_NACK response into buf.
 *
 * @param ack_type      CMD_ACK or CMD_NACK.
 * @param sensor_id     This node's ID.
 * @param seq           Sequence number of the command being ACKed.
 * @param status_code   0 = success, non-zero = error.
 * @param buf           Output buffer.
 * @param buf_len       Size of output buffer.
 * @return              Number of bytes written.
 */
size_t lss_build_ack(CommandType ack_type, uint8_t sensor_id, uint8_t seq,
                     uint8_t status_code, uint8_t *buf, size_t buf_len);

/** Compute the serialised byte size of the given packet. */
size_t lss_multi_sensor_size(const MultiSensorPacket *pkt);

/** Return true if the first two bytes of buf match a known sync word. */
bool lss_detect_packet(const uint8_t *buf, size_t len, PacketType *type_out);
