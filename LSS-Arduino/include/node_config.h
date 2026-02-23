/**
 * @file node_config.h
 * @brief Persistent node configuration stored in NVS (Non-Volatile Storage).
 *
 * Configuration is loaded from NVS at boot and written back whenever
 * a CMD_SET_* command is applied.  Factory reset wipes the NVS namespace.
 */

#pragma once
#include <Arduino.h>

/** Maximum length of a location string (including null terminator). */
#define LOCATION_MAXLEN 32
/** Maximum length of a zone string (including null terminator). */
#define ZONE_MAXLEN     16

/** All runtime-configurable parameters for a client node. */
typedef struct {
    uint8_t  nodeId;                   ///< 1–254; must be unique in the network
    uint16_t networkId;                ///< Must match base station
    uint32_t telemetryIntervalMs;      ///< Telemetry transmission period in ms
    char     location[LOCATION_MAXLEN];
    char     zone[ZONE_MAXLEN];
    float    tempThreshHigh;           ///< °C above which an alert fires
    float    tempThreshLow;            ///< °C below which an alert fires
    float    batteryThreshLow;         ///< % below which a low-battery alert fires
    float    batteryThreshCritical;    ///< % below which a critical alert fires
    float    loraFrequency;            ///< MHz
    uint8_t  loraSpreadingFactor;
    uint8_t  loraTxPower;              ///< dBm
    bool     meshEnabled;              ///< Whether to participate in mesh routing
    int32_t  tzOffsetMinutes;          ///< UTC timezone offset in minutes
    uint32_t lastTimeSync;             ///< Unix epoch of last time sync (UTC)
} NodeConfig;

/** NVS namespace used by the node config (max 15 chars for ESP-IDF). */
#define NVS_NAMESPACE "lss_node"

class NodeConfigStore {
public:
    NodeConfigStore() = default;

    /**
     * Load config from NVS.  If no saved config exists, writes defaults.
     *
     * @return true on success.
     */
    bool load();

    /**
     * Persist the current in-memory config to NVS.
     *
     * @return true on success.
     */
    bool save();

    /**
     * Erase all LSS NVS keys and reload defaults.
     *
     * Called when CMD_FACTORY_RESET is received.
     */
    void factory_reset();

    /** Return a reference to the in-memory config (modify then call save()). */
    NodeConfig &config() { return _cfg; }

    /** Return a const reference for read-only access. */
    const NodeConfig &config() const { return _cfg; }

private:
    NodeConfig _cfg{};

    /** Write default values into _cfg. */
    void _apply_defaults();
};

/**
 * @file command_handler.h
 * @brief Process incoming commands from the base station.
 */

#pragma once
#include "packets.h"
#include "node_config.h"
#include "mesh.h"

/**
 * Decode and apply a received CommandPacket.
 *
 * On success, writes a CMD_ACK into ack_buf.
 * On failure, writes a CMD_NACK into ack_buf.
 *
 * @param pkt        Parsed command (sync word and CRC already verified).
 * @param cfg_store  Mutable node config store.
 * @param mesh       Mesh router (for CMD_SET_MESH_CONFIG).
 * @param ack_buf    Output buffer for the ACK/NACK response.
 * @param ack_len    Capacity of ack_buf.
 * @return           Bytes written to ack_buf.
 */
size_t handle_command(const CommandPacket *pkt, NodeConfigStore &cfg_store,
                      MeshRouter &mesh, uint8_t *ack_buf, size_t ack_len);
