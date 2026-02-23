/**
 * @file mesh.h
 * @brief AODV-inspired, coordinator-centric mesh networking.
 *
 * SOURCE OF TRUTH: LSS.md § Mesh Network Architecture
 */

#pragma once
#include <Arduino.h>
#include "packets.h"

// ============================================================
// Mesh packet types
// ============================================================

typedef enum __attribute__((packed)) : uint8_t {
    MESH_DATA            = 0,  ///< User data payload
    MESH_ROUTE_REQUEST   = 1,  ///< RREQ — flood to discover route
    MESH_ROUTE_REPLY     = 2,  ///< RREP — unicast reply on found route
    MESH_ROUTE_ERROR     = 3,  ///< Notify upstream of broken link
    MESH_NEIGHBOR_BEACON = 4,  ///< Periodic neighbor discovery broadcast
} MeshPacketType;

/** Mesh header prepended to every mesh frame. */
typedef struct __attribute__((packed)) {
    uint8_t  packetType;   ///< MeshPacketType
    uint8_t  sourceId;
    uint8_t  destId;       ///< 255 = broadcast
    uint8_t  nextHop;
    uint8_t  prevHop;
    uint8_t  hopCount;
    uint8_t  ttl;          ///< Maximum hops remaining
    uint16_t sequenceNum;
} MeshHeader;

// ============================================================
// Routing table
// ============================================================

#define MESH_MAX_ROUTES     20
#define MESH_MAX_HOPS        5
#define MESH_ROUTE_TIMEOUT  600000UL  ///< ms (10 minutes)
#define MESH_BEACON_INTERVAL 30000UL  ///< ms (30 seconds)

typedef struct {
    uint8_t  destId;
    uint8_t  nextHop;
    uint8_t  hopCount;
    uint32_t lastUpdated;   ///< millis() timestamp
    bool     valid;
} RouteEntry;

// ============================================================
// MeshRouter class
// ============================================================

class MeshRouter {
public:
    /**
     * @param node_id   This node's ID.
     * @param enabled   Whether mesh forwarding is active.
     */
    explicit MeshRouter(uint8_t node_id, bool enabled = true);

    /**
     * Process an incoming raw mesh frame.
     *
     * Call this from the LoRa receive callback with every inbound packet.
     * Returns true if the frame is intended for this node (caller should
     * process the payload); false if it was forwarded or dropped.
     *
     * @param raw       Raw bytes received.
     * @param len       Length of raw.
     * @param out_payload  Set to point inside raw at the payload start.
     * @param out_len      Length of the payload.
     */
    bool receive(const uint8_t *raw, size_t len,
                 const uint8_t **out_payload, size_t *out_len);

    /**
     * Wrap a payload in a mesh frame and write it to out_buf.
     *
     * @param dest_id   Destination node ID (255 = broadcast).
     * @param payload   Payload bytes to wrap.
     * @param pay_len   Payload length.
     * @param out_buf   Output buffer.
     * @param buf_len   Output buffer capacity.
     * @return          Number of bytes written, or 0 on error.
     */
    size_t wrap(uint8_t dest_id, const uint8_t *payload, size_t pay_len,
                uint8_t *out_buf, size_t buf_len);

    /**
     * Handle the periodic beacon — call from loop() every MESH_BEACON_INTERVAL ms.
     *
     * @param out_buf  Output buffer for the beacon frame.
     * @param buf_len  Buffer capacity.
     * @return         Number of bytes written (0 if not due yet).
     */
    size_t tick(uint8_t *out_buf, size_t buf_len);

    /**
     * Add or refresh a route table entry.
     *
     * @param dest_id    Destination node ID.
     * @param next_hop   Next-hop node ID toward dest.
     * @param hop_count  Total hops to destination.
     */
    void update_route(uint8_t dest_id, uint8_t next_hop, uint8_t hop_count);

    /**
     * Look up the next hop toward dest_id.
     *
     * @return Next-hop node ID, or 255 if no route.
     */
    uint8_t next_hop_for(uint8_t dest_id) const;

    /** Evict stale route entries older than MESH_ROUTE_TIMEOUT. */
    void evict_stale_routes();

    void set_enabled(bool enabled) { _enabled = enabled; }
    bool is_enabled() const        { return _enabled; }

private:
    uint8_t    _node_id;
    bool       _enabled;
    RouteEntry _routes[MESH_MAX_ROUTES];
    uint16_t   _seq;
    uint32_t   _last_beacon;

    /** Return the index of dest_id in the route table, or -1. */
    int _find_route(uint8_t dest_id) const;

    /** Return an empty slot index, or the oldest entry's index if full. */
    int _alloc_slot();

    uint16_t _next_seq();
};
