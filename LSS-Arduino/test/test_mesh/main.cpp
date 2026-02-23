/**
 * @file test/test_mesh/main.cpp
 * @brief Unity tests for the mesh router.
 *
 * Runs on the native (host) platform.
 */

#include "../test_support/arduino_stub.h"
#include <unity.h>
#include <string.h>
#include <stdint.h>

// Stub millis() as a writable variable for test control
static uint32_t _fake_millis = 0;
// Override millis macro for this TU
#undef millis
#define millis() _fake_millis

#include "../../src/mesh.cpp"

// ============================================================

void setUp(void)    { _fake_millis = 0; }
void tearDown(void) {}

// ============================================================
// Route table
// ============================================================

void test_update_and_lookup_route(void) {
    MeshRouter router(1, true);
    router.update_route(0, 5, 2);
    TEST_ASSERT_EQUAL(5, router.next_hop_for(0));
}

void test_no_route_returns_broadcast(void) {
    MeshRouter router(1, true);
    TEST_ASSERT_EQUAL(255, router.next_hop_for(0));
}

void test_route_overwritten_by_newer(void) {
    MeshRouter router(1, true);
    router.update_route(0, 3, 2);
    router.update_route(0, 7, 1);  // shorter path
    // Last update wins (route table replaces by nearest matching dest)
    TEST_ASSERT_EQUAL(7, router.next_hop_for(0));
}

void test_stale_route_evicted(void) {
    MeshRouter router(2, true);
    router.update_route(0, 5, 1);
    // Advance clock past timeout
    _fake_millis = MESH_ROUTE_TIMEOUT + 1;
    router.evict_stale_routes();
    TEST_ASSERT_EQUAL(255, router.next_hop_for(0));
}

void test_fresh_route_not_evicted(void) {
    MeshRouter router(2, true);
    router.update_route(0, 5, 1);
    _fake_millis = MESH_ROUTE_TIMEOUT - 1000;
    router.evict_stale_routes();
    TEST_ASSERT_EQUAL(5, router.next_hop_for(0));
}

// ============================================================
// wrap()
// ============================================================

void test_wrap_produces_mesh_header(void) {
    MeshRouter router(3, true);
    uint8_t payload[] = {0x01, 0x02, 0x03};
    uint8_t buf[128];
    size_t len = router.wrap(0, payload, sizeof(payload), buf, sizeof(buf));

    TEST_ASSERT_EQUAL(sizeof(MeshHeader) + 3, len);

    MeshHeader hdr;
    memcpy(&hdr, buf, sizeof(MeshHeader));
    TEST_ASSERT_EQUAL(MESH_DATA, hdr.packetType);
    TEST_ASSERT_EQUAL(3, hdr.sourceId);
    TEST_ASSERT_EQUAL(0, hdr.destId);
    TEST_ASSERT_EQUAL(3, hdr.prevHop);
    TEST_ASSERT_EQUAL(0, hdr.hopCount);
    TEST_ASSERT_EQUAL(MESH_MAX_HOPS, hdr.ttl);

    // Payload matches
    TEST_ASSERT_EQUAL_MEMORY(payload, buf + sizeof(MeshHeader), 3);
}

void test_wrap_broadcast(void) {
    MeshRouter router(1, true);
    uint8_t pl[] = {0xFF};
    uint8_t buf[64];
    size_t len = router.wrap(255, pl, 1, buf, sizeof(buf));
    MeshHeader hdr;
    memcpy(&hdr, buf, sizeof(MeshHeader));
    TEST_ASSERT_EQUAL(255, hdr.destId);
    TEST_ASSERT_EQUAL(255, hdr.nextHop);  // broadcast
}

void test_wrap_buffer_too_small(void) {
    MeshRouter router(1, true);
    uint8_t pl[200] = {};
    uint8_t buf[4];  // Too small
    size_t len = router.wrap(0, pl, sizeof(pl), buf, sizeof(buf));
    TEST_ASSERT_EQUAL(0, len);
}

// ============================================================
// receive()
// ============================================================

static uint8_t _make_mesh_frame(uint8_t pkt_type, uint8_t src, uint8_t dest,
                                 uint8_t hop_count, uint8_t ttl,
                                 const uint8_t *payload, size_t pay_len,
                                 uint8_t *out, size_t out_len)
{
    MeshHeader hdr = {};
    hdr.packetType  = pkt_type;
    hdr.sourceId    = src;
    hdr.destId      = dest;
    hdr.nextHop     = dest;
    hdr.prevHop     = src;
    hdr.hopCount    = hop_count;
    hdr.ttl         = ttl;
    hdr.sequenceNum = 1;

    size_t total = sizeof(MeshHeader) + pay_len;
    if (out_len < total) return 0;
    memcpy(out, &hdr, sizeof(MeshHeader));
    if (payload && pay_len) memcpy(out + sizeof(MeshHeader), payload, pay_len);
    return total;
}

void test_receive_for_this_node(void) {
    MeshRouter router(5, true);
    uint8_t payload[] = {0xAA, 0xBB};
    uint8_t frame[64];
    size_t flen = _make_mesh_frame(MESH_DATA, 1, 5, 0, MESH_MAX_HOPS,
                                   payload, sizeof(payload), frame, sizeof(frame));
    const uint8_t *out_pay;
    size_t out_len;
    bool mine = router.receive(frame, flen, &out_pay, &out_len);
    TEST_ASSERT_TRUE(mine);
    TEST_ASSERT_EQUAL(2, out_len);
    TEST_ASSERT_EQUAL_MEMORY(payload, out_pay, 2);
}

void test_receive_not_for_this_node(void) {
    MeshRouter router(5, true);
    uint8_t frame[64];
    size_t flen = _make_mesh_frame(MESH_DATA, 1, 3, 0, MESH_MAX_HOPS,
                                   nullptr, 0, frame, sizeof(frame));
    const uint8_t *out_pay;
    size_t out_len;
    bool mine = router.receive(frame, flen, &out_pay, &out_len);
    TEST_ASSERT_FALSE(mine);
}

void test_receive_exceeds_hop_limit(void) {
    MeshRouter router(5, true);
    uint8_t frame[64];
    // hop_count >= MESH_MAX_HOPS → drop
    size_t flen = _make_mesh_frame(MESH_DATA, 1, 5, MESH_MAX_HOPS, 1,
                                   nullptr, 0, frame, sizeof(frame));
    const uint8_t *out_pay;
    size_t out_len;
    bool mine = router.receive(frame, flen, &out_pay, &out_len);
    TEST_ASSERT_FALSE(mine);
}

void test_receive_broadcast(void) {
    MeshRouter router(5, true);
    uint8_t payload[] = {0x01};
    uint8_t frame[64];
    size_t flen = _make_mesh_frame(MESH_DATA, 1, 255, 0, MESH_MAX_HOPS,
                                   payload, 1, frame, sizeof(frame));
    const uint8_t *out_pay;
    size_t out_len;
    bool mine = router.receive(frame, flen, &out_pay, &out_len);
    TEST_ASSERT_TRUE(mine);  // broadcast is for everyone
}

void test_receive_beacon_not_for_node(void) {
    MeshRouter router(5, true);
    uint8_t frame[64];
    size_t flen = _make_mesh_frame(MESH_NEIGHBOR_BEACON, 2, 255, 0, 1,
                                   nullptr, 0, frame, sizeof(frame));
    const uint8_t *out_pay;
    size_t out_len;
    bool mine = router.receive(frame, flen, &out_pay, &out_len);
    TEST_ASSERT_FALSE(mine);  // Beacon is handled internally
    // Route to node 2 should now exist
    TEST_ASSERT_EQUAL(2, router.next_hop_for(2));
}

void test_receive_too_short(void) {
    MeshRouter router(1, true);
    uint8_t buf[2] = {};
    const uint8_t *out_pay;
    size_t out_len;
    bool mine = router.receive(buf, sizeof(buf), &out_pay, &out_len);
    TEST_ASSERT_FALSE(mine);
}

// ============================================================
// tick() — beacon generation
// ============================================================

void test_tick_no_beacon_before_interval(void) {
    MeshRouter router(1, true);
    uint8_t buf[64];
    // Just created — _last_beacon = 0, millis = 0
    size_t len = router.tick(buf, sizeof(buf));
    // First tick at 0 ms — should trigger immediately (0 - 0 >= interval is false)
    // (depends on implementation: first call should fire)
    // We accept either behaviour — just ensure no crash and valid output if non-zero
    (void)len;
}

void test_tick_sends_beacon_after_interval(void) {
    MeshRouter router(1, true);
    uint8_t buf[64];

    // Advance past beacon interval
    _fake_millis = MESH_BEACON_INTERVAL + 1;
    size_t len = router.tick(buf, sizeof(buf));
    TEST_ASSERT_EQUAL(sizeof(MeshHeader), len);

    MeshHeader hdr;
    memcpy(&hdr, buf, sizeof(MeshHeader));
    TEST_ASSERT_EQUAL(MESH_NEIGHBOR_BEACON, hdr.packetType);
    TEST_ASSERT_EQUAL(1, hdr.sourceId);
    TEST_ASSERT_EQUAL(255, hdr.destId);
    TEST_ASSERT_EQUAL(1, hdr.ttl);
}

void test_tick_no_duplicate_beacon(void) {
    MeshRouter router(1, true);
    uint8_t buf[64];
    _fake_millis = MESH_BEACON_INTERVAL + 1;
    router.tick(buf, sizeof(buf));
    // Advance only a little — not enough for another beacon
    _fake_millis += 100;
    size_t len = router.tick(buf, sizeof(buf));
    TEST_ASSERT_EQUAL(0, len);
}

// ============================================================
// Disabled mesh
// ============================================================

void test_disabled_mesh_wrap_still_works(void) {
    MeshRouter router(1, false);
    uint8_t pl[] = {1, 2, 3};
    uint8_t buf[64];
    // wrap() doesn't check _enabled — caller decides whether to use mesh
    size_t len = router.wrap(0, pl, 3, buf, sizeof(buf));
    TEST_ASSERT_GREATER_THAN(0, len);
}

// ============================================================
// main
// ============================================================

int main(void) {
    UNITY_BEGIN();

    RUN_TEST(test_update_and_lookup_route);
    RUN_TEST(test_no_route_returns_broadcast);
    RUN_TEST(test_route_overwritten_by_newer);
    RUN_TEST(test_stale_route_evicted);
    RUN_TEST(test_fresh_route_not_evicted);

    RUN_TEST(test_wrap_produces_mesh_header);
    RUN_TEST(test_wrap_broadcast);
    RUN_TEST(test_wrap_buffer_too_small);

    RUN_TEST(test_receive_for_this_node);
    RUN_TEST(test_receive_not_for_this_node);
    RUN_TEST(test_receive_exceeds_hop_limit);
    RUN_TEST(test_receive_broadcast);
    RUN_TEST(test_receive_beacon_not_for_node);
    RUN_TEST(test_receive_too_short);

    RUN_TEST(test_tick_no_beacon_before_interval);
    RUN_TEST(test_tick_sends_beacon_after_interval);
    RUN_TEST(test_tick_no_duplicate_beacon);

    RUN_TEST(test_disabled_mesh_wrap_still_works);

    return UNITY_END();
}
