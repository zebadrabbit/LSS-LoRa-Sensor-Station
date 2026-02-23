/**
 * @file command_handler.cpp
 * @brief Process and apply incoming commands from the base station.
 */

#include "node_config.h"  // also includes command_handler.h declaration
#include "packets.h"
#include "mesh.h"
#include <string.h>

#ifndef TEST_BUILD
#include <Arduino.h>
#include <nvs_flash.h>
#endif

// Helper: write a CMD_ACK or CMD_NACK into ack_buf
static inline size_t _ack(uint8_t sensor_id, uint8_t seq, bool success,
                           uint8_t *buf, size_t len)
{
    return lss_build_ack(
        success ? CMD_ACK : CMD_NACK,
        sensor_id, seq,
        success ? 0x00 : 0x01,
        buf, len
    );
}

size_t handle_command(const CommandPacket *pkt, NodeConfigStore &cfg_store,
                      MeshRouter &mesh, uint8_t *ack_buf, size_t ack_len)
{
    NodeConfig &cfg = cfg_store.config();
    uint8_t    seq  = pkt->sequenceNumber;
    uint8_t    nid  = cfg.nodeId;
    bool       ok   = true;

    switch (static_cast<CommandType>(pkt->commandType)) {

    // ----------------------------------------------------------------
    case CMD_PING:
        // Nothing to do — just ACK
        break;

    // ----------------------------------------------------------------
    case CMD_GET_CONFIG:
        // ACK with no payload; future enhancement could return config bytes
        break;

    // ----------------------------------------------------------------
    case CMD_SET_INTERVAL:
        if (pkt->dataLength >= 4) {
            uint32_t interval;
            memcpy(&interval, pkt->data, sizeof(uint32_t));
            if (interval >= 1000 && interval <= 3600000UL) {
                cfg.telemetryIntervalMs = interval;
                cfg_store.save();
            } else {
                ok = false;
            }
        } else {
            ok = false;
        }
        break;

    // ----------------------------------------------------------------
    case CMD_SET_LOCATION: {
        // data = null-terminated location (max 32) + null-terminated zone (max 16)
        size_t loc_len = strnlen((const char *)pkt->data, LOCATION_MAXLEN);
        strncpy(cfg.location, (const char *)pkt->data, LOCATION_MAXLEN - 1);
        cfg.location[LOCATION_MAXLEN - 1] = '\0';

        const char *zone_ptr = (const char *)pkt->data + loc_len + 1;
        size_t remaining = pkt->dataLength - (loc_len + 1);
        if (remaining > 0 && remaining <= ZONE_MAXLEN) {
            strncpy(cfg.zone, zone_ptr, ZONE_MAXLEN - 1);
            cfg.zone[ZONE_MAXLEN - 1] = '\0';
        }
        cfg_store.save();
        break;
    }

    // ----------------------------------------------------------------
    case CMD_SET_TEMP_THRESH:
        if (pkt->dataLength >= 8) {
            float lo, hi;
            memcpy(&lo, pkt->data,     sizeof(float));
            memcpy(&hi, pkt->data + 4, sizeof(float));
            cfg.tempThreshLow  = lo;
            cfg.tempThreshHigh = hi;
            cfg_store.save();
        } else {
            ok = false;
        }
        break;

    // ----------------------------------------------------------------
    case CMD_SET_BATTERY_THRESH:
        if (pkt->dataLength >= 8) {
            float lo, crit;
            memcpy(&lo,   pkt->data,     sizeof(float));
            memcpy(&crit, pkt->data + 4, sizeof(float));
            cfg.batteryThreshLow      = lo;
            cfg.batteryThreshCritical = crit;
            cfg_store.save();
        } else {
            ok = false;
        }
        break;

    // ----------------------------------------------------------------
    case CMD_SET_MESH_CONFIG:
        if (pkt->dataLength >= 1) {
            bool enabled = pkt->data[0] != 0;
            cfg.meshEnabled = enabled;
            mesh.set_enabled(enabled);
            cfg_store.save();
        } else {
            ok = false;
        }
        break;

    // ----------------------------------------------------------------
    case CMD_RESTART:
#ifndef TEST_BUILD
        // ACK before restart so the base station knows we got it
        lss_build_ack(CMD_ACK, nid, seq, 0, ack_buf, ack_len);
        delay(200);
        ESP.restart();
        return sizeof(AckPacket);  // unreachable but satisfies compiler
#endif
        break;

    // ----------------------------------------------------------------
    case CMD_FACTORY_RESET:
#ifndef TEST_BUILD
        lss_build_ack(CMD_ACK, nid, seq, 0, ack_buf, ack_len);
        delay(200);
        cfg_store.factory_reset();
        ESP.restart();
        return sizeof(AckPacket);
#endif
        cfg_store.factory_reset();
        break;

    // ----------------------------------------------------------------
    case CMD_SET_LORA_PARAMS:
        if (pkt->dataLength >= 7) {
            float freq;
            uint8_t sf, tx_power;
            memcpy(&freq,     pkt->data,     sizeof(float));
            memcpy(&sf,       pkt->data + 4, sizeof(uint8_t));
            memcpy(&tx_power, pkt->data + 6, sizeof(uint8_t));
            cfg.loraFrequency       = freq;
            cfg.loraSpreadingFactor = sf;
            cfg.loraTxPower         = tx_power;
            cfg_store.save();
            // LoRa params take effect on next boot
        } else {
            ok = false;
        }
        break;

    // ----------------------------------------------------------------
    case CMD_TIME_SYNC:
    case CMD_BASE_WELCOME:
        if (pkt->dataLength >= 6) {
            uint32_t epoch;
            int16_t  tz;
            memcpy(&epoch, pkt->data,     sizeof(uint32_t));
            memcpy(&tz,    pkt->data + 4, sizeof(int16_t));
            cfg.lastTimeSync    = epoch;
            cfg.tzOffsetMinutes = tz;
            cfg_store.save();
            // RTC update would go here in a production build
        } else {
            ok = false;
        }
        break;

    // ----------------------------------------------------------------
    default:
        ok = false;
        break;
    }

    return _ack(nid, seq, ok, ack_buf, ack_len);
}
