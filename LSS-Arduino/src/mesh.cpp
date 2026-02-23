/**
 * @file mesh.cpp
 * @brief AODV-inspired mesh router implementation.
 */

#include "mesh.h"
#include <string.h>

// ============================================================
// Constructor
// ============================================================

MeshRouter::MeshRouter(uint8_t node_id, bool enabled)
    : _node_id(node_id), _enabled(enabled), _seq(0), _last_beacon(0)
{
    memset(_routes, 0, sizeof(_routes));
}

// ============================================================
// receive
// ============================================================

bool MeshRouter::receive(const uint8_t *raw, size_t len,
                         const uint8_t **out_payload, size_t *out_len)
{
    if (len < sizeof(MeshHeader)) return false;

    MeshHeader hdr;
    memcpy(&hdr, raw, sizeof(MeshHeader));

    // Drop packets that have exceeded the hop limit
    if (hdr.hopCount >= MESH_MAX_HOPS) return false;

    // Record the neighbor that sent this packet
    if (hdr.prevHop != 0 && hdr.prevHop != 255) {
        update_route(hdr.sourceId, hdr.prevHop, hdr.hopCount);
    }

    // Handle RREQ / RREP / RERR / Beacon internally
    if (hdr.packetType == MESH_NEIGHBOR_BEACON) {
        update_route(hdr.sourceId, hdr.sourceId, 1);
        return false;
    }

    if (hdr.packetType == MESH_ROUTE_REQUEST) {
        // If this is for us, do nothing further — upper layer handles it
        if (hdr.destId == _node_id) {
            *out_payload = raw + sizeof(MeshHeader);
            *out_len     = len - sizeof(MeshHeader);
            return true;
        }
        // Otherwise flood (caller re-transmits with updated hop count)
        return false;
    }

    // MESH_DATA — check if we are the destination
    if (hdr.destId == _node_id || hdr.destId == 255) {
        *out_payload = raw + sizeof(MeshHeader);
        *out_len     = len - sizeof(MeshHeader);
        return true;
    }

    // Not for us and mesh forwarding is enabled — caller should re-transmit
    return false;
}

// ============================================================
// wrap
// ============================================================

size_t MeshRouter::wrap(uint8_t dest_id, const uint8_t *payload, size_t pay_len,
                        uint8_t *out_buf, size_t buf_len)
{
    size_t total = sizeof(MeshHeader) + pay_len;
    if (buf_len < total) return 0;

    MeshHeader hdr;
    memset(&hdr, 0, sizeof(MeshHeader));
    hdr.packetType = MESH_DATA;
    hdr.sourceId   = _node_id;
    hdr.destId     = dest_id;
    hdr.prevHop    = _node_id;
    hdr.nextHop    = (dest_id == 255) ? 255 : next_hop_for(dest_id);
    hdr.hopCount   = 0;
    hdr.ttl        = MESH_MAX_HOPS;
    hdr.sequenceNum = _next_seq();

    memcpy(out_buf, &hdr, sizeof(MeshHeader));
    memcpy(out_buf + sizeof(MeshHeader), payload, pay_len);
    return total;
}

// ============================================================
// tick — periodic beacon
// ============================================================

size_t MeshRouter::tick(uint8_t *out_buf, size_t buf_len)
{
    evict_stale_routes();

    uint32_t now = millis();
    if (now - _last_beacon < MESH_BEACON_INTERVAL) return 0;
    _last_beacon = now;

    if (buf_len < sizeof(MeshHeader)) return 0;

    MeshHeader hdr;
    memset(&hdr, 0, sizeof(MeshHeader));
    hdr.packetType  = MESH_NEIGHBOR_BEACON;
    hdr.sourceId    = _node_id;
    hdr.destId      = 255;   // broadcast
    hdr.prevHop     = _node_id;
    hdr.nextHop     = 255;
    hdr.hopCount    = 0;
    hdr.ttl         = 1;     // beacons are single-hop
    hdr.sequenceNum = _next_seq();

    memcpy(out_buf, &hdr, sizeof(MeshHeader));
    return sizeof(MeshHeader);
}

// ============================================================
// Route table management
// ============================================================

void MeshRouter::update_route(uint8_t dest_id, uint8_t next_hop,
                              uint8_t hop_count)
{
    int idx = _find_route(dest_id);
    if (idx < 0) idx = _alloc_slot();

    _routes[idx].destId      = dest_id;
    _routes[idx].nextHop     = next_hop;
    _routes[idx].hopCount    = hop_count;
    _routes[idx].lastUpdated = millis();
    _routes[idx].valid       = true;
}

uint8_t MeshRouter::next_hop_for(uint8_t dest_id) const
{
    int idx = _find_route(dest_id);
    if (idx >= 0 && _routes[idx].valid) return _routes[idx].nextHop;
    return 255;  // no route — broadcast as fallback
}

void MeshRouter::evict_stale_routes()
{
    uint32_t now = millis();
    for (int i = 0; i < MESH_MAX_ROUTES; i++) {
        if (_routes[i].valid &&
            (now - _routes[i].lastUpdated) > MESH_ROUTE_TIMEOUT) {
            _routes[i].valid = false;
        }
    }
}

// ============================================================
// Private helpers
// ============================================================

int MeshRouter::_find_route(uint8_t dest_id) const
{
    for (int i = 0; i < MESH_MAX_ROUTES; i++) {
        if (_routes[i].valid && _routes[i].destId == dest_id) return i;
    }
    return -1;
}

int MeshRouter::_alloc_slot()
{
    // Prefer an invalid slot
    for (int i = 0; i < MESH_MAX_ROUTES; i++) {
        if (!_routes[i].valid) return i;
    }
    // Evict oldest
    int oldest = 0;
    uint32_t oldest_ts = _routes[0].lastUpdated;
    for (int i = 1; i < MESH_MAX_ROUTES; i++) {
        if (_routes[i].lastUpdated < oldest_ts) {
            oldest_ts = _routes[i].lastUpdated;
            oldest = i;
        }
    }
    return oldest;
}

uint16_t MeshRouter::_next_seq()
{
    return _seq++;
}
