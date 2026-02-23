/**
 * @file test/test_packets/main.cpp
 * @brief Unity tests for the packet codec (CRC, serialise, deserialise).
 *
 * Runs on the native (host) platform — no hardware required.
 */

#include "../test_support/arduino_stub.h"
#include <unity.h>
#include <string.h>

// Pull in the implementation directly
#include "../../src/packets.cpp"

// ============================================================
// setUp / tearDown
// ============================================================

void setUp(void)    {}
void tearDown(void) {}

// ============================================================
// CRC-16 tests
// ============================================================

void test_crc16_empty(void) {
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, lss_crc16(nullptr, 0));
}

void test_crc16_known_vector(void) {
    // CRC-16/CCITT-FALSE of "123456789" = 0x29B1
    const uint8_t data[] = "123456789";
    TEST_ASSERT_EQUAL_HEX16(0x29B1, lss_crc16(data, 9));
}

void test_crc16_single_zero(void) {
    const uint8_t b = 0x00;
    uint16_t crc = lss_crc16(&b, 1);
    TEST_ASSERT_NOT_EQUAL(0, crc);  // Just check it runs without crash
}

// ============================================================
// MultiSensorPacket round-trip
// ============================================================

void test_multi_sensor_round_trip(void) {
    MultiSensorPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.header.syncWord       = SYNC_MULTI_SENSOR;
    pkt.header.networkId      = 1;
    pkt.header.packetType     = PACKET_MULTI_SENSOR;
    pkt.header.sensorId       = 5;
    pkt.header.valueCount     = 2;
    pkt.header.batteryPercent = 85;
    strncpy(pkt.header.location, "Shed", 31);
    strncpy(pkt.header.zone, "Outdoor", 15);
    pkt.values[0] = {VALUE_TEMPERATURE, 19.5f};
    pkt.values[1] = {VALUE_HUMIDITY, 62.0f};

    uint8_t buf[255];
    size_t len = lss_serialize_multi_sensor(&pkt, buf, sizeof(buf));
    TEST_ASSERT_GREATER_THAN(0, len);

    MultiSensorPacket out;
    memset(&out, 0, sizeof(out));
    TEST_ASSERT_TRUE(lss_deserialize_multi_sensor(buf, len, &out));

    TEST_ASSERT_EQUAL(5, out.header.sensorId);
    TEST_ASSERT_EQUAL(2, out.header.valueCount);
    TEST_ASSERT_EQUAL(85, out.header.batteryPercent);
    TEST_ASSERT_EQUAL_STRING("Shed", out.header.location);
    TEST_ASSERT_EQUAL(VALUE_TEMPERATURE, out.values[0].type);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 19.5f, out.values[0].value);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 62.0f, out.values[1].value);
}

void test_multi_sensor_bad_crc(void) {
    MultiSensorPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.header.syncWord   = SYNC_MULTI_SENSOR;
    pkt.header.valueCount = 0;

    uint8_t buf[255];
    size_t len = lss_serialize_multi_sensor(&pkt, buf, sizeof(buf));

    // Corrupt the CRC
    buf[len - 1] ^= 0xFF;
    MultiSensorPacket out;
    TEST_ASSERT_FALSE(lss_deserialize_multi_sensor(buf, len, &out));
}

void test_multi_sensor_too_short(void) {
    uint8_t buf[4] = {0xCD, 0xAB, 0x01, 0x00};
    MultiSensorPacket out;
    TEST_ASSERT_FALSE(lss_deserialize_multi_sensor(buf, sizeof(buf), &out));
}

void test_multi_sensor_wrong_sync(void) {
    MultiSensorPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.header.syncWord = 0x1234;  // wrong
    pkt.header.valueCount = 0;
    uint8_t buf[255];
    size_t len = lss_serialize_multi_sensor(&pkt, buf, sizeof(buf));
    // Fix: override sync word in buffer to wrong value
    buf[0] = 0x34; buf[1] = 0x12;
    MultiSensorPacket out;
    TEST_ASSERT_FALSE(lss_deserialize_multi_sensor(buf, len, &out));
}

void test_multi_sensor_max_values(void) {
    MultiSensorPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.header.syncWord   = SYNC_MULTI_SENSOR;
    pkt.header.valueCount = MAX_SENSOR_VALUES;
    for (int i = 0; i < MAX_SENSOR_VALUES; i++) {
        pkt.values[i] = {(uint8_t)(i % 14), (float)i * 1.5f};
    }
    uint8_t buf[255];
    size_t len = lss_serialize_multi_sensor(&pkt, buf, sizeof(buf));
    TEST_ASSERT_GREATER_THAN(0, len);

    MultiSensorPacket out;
    TEST_ASSERT_TRUE(lss_deserialize_multi_sensor(buf, len, &out));
    TEST_ASSERT_EQUAL(MAX_SENSOR_VALUES, out.header.valueCount);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, (float)(MAX_SENSOR_VALUES - 1) * 1.5f,
                             out.values[MAX_SENSOR_VALUES - 1].value);
}

// ============================================================
// CommandPacket round-trip
// ============================================================

void test_command_round_trip(void) {
    CommandPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.syncWord       = SYNC_COMMAND;
    pkt.commandType    = CMD_SET_INTERVAL;
    pkt.targetSensorId = 7;
    pkt.sequenceNumber = 42;
    uint32_t interval  = 15000;
    memcpy(pkt.data, &interval, sizeof(interval));
    pkt.dataLength     = sizeof(interval);

    uint8_t buf[sizeof(CommandPacket)];
    size_t len = lss_serialize_command(&pkt, buf, sizeof(buf));
    TEST_ASSERT_EQUAL(sizeof(CommandPacket), len);

    CommandPacket out;
    TEST_ASSERT_TRUE(lss_deserialize_command(buf, len, &out));
    TEST_ASSERT_EQUAL(CMD_SET_INTERVAL, out.commandType);
    TEST_ASSERT_EQUAL(7, out.targetSensorId);
    TEST_ASSERT_EQUAL(42, out.sequenceNumber);
    uint32_t parsed_interval;
    memcpy(&parsed_interval, out.data, sizeof(uint32_t));
    TEST_ASSERT_EQUAL(15000, parsed_interval);
}

void test_command_bad_crc(void) {
    CommandPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.syncWord    = SYNC_COMMAND;
    pkt.commandType = CMD_PING;

    uint8_t buf[sizeof(CommandPacket)];
    lss_serialize_command(&pkt, buf, sizeof(buf));
    buf[sizeof(CommandPacket) - 1] ^= 0xFF;

    CommandPacket out;
    TEST_ASSERT_FALSE(lss_deserialize_command(buf, sizeof(buf), &out));
}

// ============================================================
// ACK build
// ============================================================

void test_build_ack(void) {
    uint8_t buf[sizeof(AckPacket)];
    size_t len = lss_build_ack(CMD_ACK, 3, 7, 0, buf, sizeof(buf));
    TEST_ASSERT_EQUAL(sizeof(AckPacket), len);

    // Verify sync word
    uint16_t sync;
    memcpy(&sync, buf, 2);
    TEST_ASSERT_EQUAL_HEX16(SYNC_COMMAND, sync);
    TEST_ASSERT_EQUAL_HEX8(CMD_ACK, buf[2]);
    TEST_ASSERT_EQUAL(3, buf[3]);  // sensorId
    TEST_ASSERT_EQUAL(7, buf[4]);  // seq
}

void test_build_nack(void) {
    uint8_t buf[sizeof(AckPacket)];
    lss_build_ack(CMD_NACK, 2, 9, 1, buf, sizeof(buf));
    TEST_ASSERT_EQUAL_HEX8(CMD_NACK, buf[2]);
}

// ============================================================
// detect_packet
// ============================================================

void test_detect_multi_sensor(void) {
    // Start with SYNC_MULTI_SENSOR
    uint8_t buf[64] = {};
    buf[0] = 0xCD; buf[1] = 0xAB;  // SYNC_MULTI_SENSOR LE
    PacketType t;
    TEST_ASSERT_TRUE(lss_detect_packet(buf, sizeof(buf), &t));
    TEST_ASSERT_EQUAL(PACKET_MULTI_SENSOR, t);
}

void test_detect_command(void) {
    uint8_t buf[64] = {};
    buf[0] = 0xEF; buf[1] = 0xCD;  // SYNC_COMMAND LE
    buf[2] = CMD_PING;
    PacketType t;
    TEST_ASSERT_TRUE(lss_detect_packet(buf, sizeof(buf), &t));
    TEST_ASSERT_EQUAL(PACKET_CONFIG, t);
}

void test_detect_ack_from_sync(void) {
    uint8_t buf[64] = {};
    buf[0] = 0xEF; buf[1] = 0xCD;
    buf[2] = CMD_ACK;
    PacketType t;
    TEST_ASSERT_TRUE(lss_detect_packet(buf, sizeof(buf), &t));
    TEST_ASSERT_EQUAL(PACKET_ACK, t);
}

void test_detect_garbage(void) {
    uint8_t buf[4] = {0xDE, 0xAD, 0xBE, 0xEF};
    PacketType t;
    TEST_ASSERT_FALSE(lss_detect_packet(buf, sizeof(buf), &t));
}

void test_detect_too_short(void) {
    PacketType t;
    TEST_ASSERT_FALSE(lss_detect_packet(nullptr, 0, &t));
}

// ============================================================
// lss_multi_sensor_size
// ============================================================

void test_size_no_values(void) {
    MultiSensorPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.header.valueCount = 0;
    size_t expected = sizeof(MultiSensorHeader) + sizeof(uint16_t);
    TEST_ASSERT_EQUAL(expected, lss_multi_sensor_size(&pkt));
}

void test_size_with_values(void) {
    MultiSensorPacket pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.header.valueCount = 3;
    size_t expected = sizeof(MultiSensorHeader)
                    + 3 * sizeof(SensorValuePacket)
                    + sizeof(uint16_t);
    TEST_ASSERT_EQUAL(expected, lss_multi_sensor_size(&pkt));
}

// ============================================================
// main
// ============================================================

int main(void) {
    UNITY_BEGIN();

    RUN_TEST(test_crc16_empty);
    RUN_TEST(test_crc16_known_vector);
    RUN_TEST(test_crc16_single_zero);

    RUN_TEST(test_multi_sensor_round_trip);
    RUN_TEST(test_multi_sensor_bad_crc);
    RUN_TEST(test_multi_sensor_too_short);
    RUN_TEST(test_multi_sensor_wrong_sync);
    RUN_TEST(test_multi_sensor_max_values);

    RUN_TEST(test_command_round_trip);
    RUN_TEST(test_command_bad_crc);

    RUN_TEST(test_build_ack);
    RUN_TEST(test_build_nack);

    RUN_TEST(test_detect_multi_sensor);
    RUN_TEST(test_detect_command);
    RUN_TEST(test_detect_ack_from_sync);
    RUN_TEST(test_detect_garbage);
    RUN_TEST(test_detect_too_short);

    RUN_TEST(test_size_no_values);
    RUN_TEST(test_size_with_values);

    return UNITY_END();
}
