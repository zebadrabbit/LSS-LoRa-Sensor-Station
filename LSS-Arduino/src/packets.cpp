/**
 * @file packets.cpp
 * @brief LSS packet codec implementation.
 */

#include "packets.h"
#include <string.h>

// ============================================================
// CRC-16/CCITT-FALSE
// ============================================================

uint16_t lss_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++) {
            if (crc & 0x8000u)
                crc = (crc << 1) ^ 0x1021u;
            else
                crc <<= 1;
        }
    }
    return crc;
}

// ============================================================
// Multi-sensor packet
// ============================================================

size_t lss_multi_sensor_size(const MultiSensorPacket *pkt)
{
    return sizeof(MultiSensorHeader)
         + pkt->header.valueCount * sizeof(SensorValuePacket)
         + sizeof(uint16_t);  // checksum
}

size_t lss_serialize_multi_sensor(const MultiSensorPacket *pkt,
                                  uint8_t *buf, size_t buf_len)
{
    size_t needed = lss_multi_sensor_size(pkt);
    if (buf_len < needed) return 0;

    size_t offset = 0;

    // Copy header
    memcpy(buf + offset, &pkt->header, sizeof(MultiSensorHeader));
    offset += sizeof(MultiSensorHeader);

    // Copy value entries
    size_t vals_sz = pkt->header.valueCount * sizeof(SensorValuePacket);
    memcpy(buf + offset, pkt->values, vals_sz);
    offset += vals_sz;

    // Compute CRC over header + values
    uint16_t crc = lss_crc16(buf, offset);
    memcpy(buf + offset, &crc, sizeof(uint16_t));
    offset += sizeof(uint16_t);

    return offset;
}

bool lss_deserialize_multi_sensor(const uint8_t *buf, size_t len,
                                  MultiSensorPacket *out)
{
    if (len < sizeof(MultiSensorHeader) + sizeof(uint16_t)) return false;

    memcpy(&out->header, buf, sizeof(MultiSensorHeader));

    if (out->header.syncWord != SYNC_MULTI_SENSOR) return false;
    if (out->header.valueCount > MAX_SENSOR_VALUES) return false;

    size_t payload_end = sizeof(MultiSensorHeader)
                       + out->header.valueCount * sizeof(SensorValuePacket);
    if (len < payload_end + sizeof(uint16_t)) return false;

    // Verify CRC
    uint16_t received_crc;
    memcpy(&received_crc, buf + payload_end, sizeof(uint16_t));
    if (lss_crc16(buf, payload_end) != received_crc) return false;

    // Copy value entries
    memcpy(out->values,
           buf + sizeof(MultiSensorHeader),
           out->header.valueCount * sizeof(SensorValuePacket));
    out->checksum = received_crc;
    return true;
}

// ============================================================
// Command packet
// ============================================================

size_t lss_serialize_command(const CommandPacket *pkt,
                             uint8_t *buf, size_t buf_len)
{
    if (buf_len < sizeof(CommandPacket)) return 0;
    memcpy(buf, pkt, sizeof(CommandPacket));

    // Recompute CRC over everything except the trailing uint16_t
    size_t payload_end = sizeof(CommandPacket) - sizeof(uint16_t);
    uint16_t crc = lss_crc16(buf, payload_end);
    memcpy(buf + payload_end, &crc, sizeof(uint16_t));
    return sizeof(CommandPacket);
}

bool lss_deserialize_command(const uint8_t *buf, size_t len,
                             CommandPacket *out)
{
    if (len < sizeof(CommandPacket)) return false;
    memcpy(out, buf, sizeof(CommandPacket));
    if (out->syncWord != SYNC_COMMAND) return false;

    size_t payload_end = sizeof(CommandPacket) - sizeof(uint16_t);
    uint16_t expected = lss_crc16(buf, payload_end);
    if (out->checksum != expected) return false;
    return true;
}

// ============================================================
// ACK packet
// ============================================================

size_t lss_serialize_ack(const AckPacket *pkt,
                         uint8_t *buf, size_t buf_len)
{
    if (buf_len < sizeof(AckPacket)) return 0;
    memcpy(buf, pkt, sizeof(AckPacket));

    size_t payload_end = sizeof(AckPacket) - sizeof(uint16_t);
    uint16_t crc = lss_crc16(buf, payload_end);
    memcpy(buf + payload_end, &crc, sizeof(uint16_t));
    return sizeof(AckPacket);
}

size_t lss_build_ack(CommandType ack_type, uint8_t sensor_id, uint8_t seq,
                     uint8_t status_code, uint8_t *buf, size_t buf_len)
{
    AckPacket pkt;
    memset(&pkt, 0, sizeof(AckPacket));
    pkt.syncWord       = SYNC_COMMAND;
    pkt.commandType    = static_cast<uint8_t>(ack_type);
    pkt.sensorId       = sensor_id;
    pkt.sequenceNumber = seq;
    pkt.statusCode     = status_code;
    pkt.dataLength     = 0;
    return lss_serialize_ack(&pkt, buf, buf_len);
}

// ============================================================
// Packet type detection
// ============================================================

bool lss_detect_packet(const uint8_t *buf, size_t len, PacketType *type_out)
{
    if (len < 2) return false;
    uint16_t sync;
    memcpy(&sync, buf, sizeof(uint16_t));

    if (sync == SYNC_LEGACY && len >= sizeof(SensorDataLegacy)) {
        *type_out = PACKET_LEGACY;
        return true;
    }
    if (sync == SYNC_MULTI_SENSOR) {
        *type_out = PACKET_MULTI_SENSOR;
        return true;
    }
    if (sync == SYNC_COMMAND) {
        if (len >= 3 && (buf[2] == CMD_ACK || buf[2] == CMD_NACK))
            *type_out = PACKET_ACK;
        else
            *type_out = PACKET_CONFIG;
        return true;
    }
    return false;
}
