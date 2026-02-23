/**
 * @file node_config.cpp
 * @brief NodeConfigStore implementation using ESP32 NVS.
 */

#include "node_config.h"

#ifndef TEST_BUILD
#include <Preferences.h>
#endif

// Default values — must match the base station defaults in config.py
static void _fill_defaults(NodeConfig &cfg)
{
    cfg.nodeId                = 1;
    cfg.networkId             = 1;
    cfg.telemetryIntervalMs   = 30000;   // 30 seconds
    strncpy(cfg.location, "Unknown", LOCATION_MAXLEN - 1);
    cfg.location[LOCATION_MAXLEN - 1] = '\0';
    strncpy(cfg.zone, "default", ZONE_MAXLEN - 1);
    cfg.zone[ZONE_MAXLEN - 1] = '\0';
    cfg.tempThreshHigh        = 50.0f;
    cfg.tempThreshLow         = -20.0f;
    cfg.batteryThreshLow      = 20.0f;
    cfg.batteryThreshCritical = 10.0f;
    cfg.loraFrequency         = 915.0f;
    cfg.loraSpreadingFactor   = 10;
    cfg.loraTxPower           = 20;
    cfg.meshEnabled           = true;
    cfg.tzOffsetMinutes       = 0;
    cfg.lastTimeSync          = 0;
}

bool NodeConfigStore::load()
{
#ifndef TEST_BUILD
    Preferences prefs;
    if (!prefs.begin(NVS_NAMESPACE, true)) {
        // Namespace doesn't exist yet — write defaults
        _apply_defaults();
        return save();
    }

    _cfg.nodeId                = prefs.getUChar("node_id", 1);
    _cfg.networkId             = prefs.getUShort("network_id", 1);
    _cfg.telemetryIntervalMs   = prefs.getULong("tx_interval", 30000);
    prefs.getString("location", _cfg.location, sizeof(_cfg.location));
    prefs.getString("zone",     _cfg.zone,     sizeof(_cfg.zone));
    _cfg.tempThreshHigh        = prefs.getFloat("temp_hi",  50.0f);
    _cfg.tempThreshLow         = prefs.getFloat("temp_lo", -20.0f);
    _cfg.batteryThreshLow      = prefs.getFloat("batt_lo",  20.0f);
    _cfg.batteryThreshCritical = prefs.getFloat("batt_crit",10.0f);
    _cfg.loraFrequency         = prefs.getFloat("lora_freq",915.0f);
    _cfg.loraSpreadingFactor   = prefs.getUChar("lora_sf",   10);
    _cfg.loraTxPower           = prefs.getUChar("lora_txpwr",20);
    _cfg.meshEnabled           = prefs.getBool("mesh_en",  true);
    _cfg.tzOffsetMinutes       = prefs.getLong("tz_offset",   0);
    _cfg.lastTimeSync          = prefs.getULong("time_sync",  0);
    prefs.end();
#else
    _apply_defaults();
#endif
    return true;
}

bool NodeConfigStore::save()
{
#ifndef TEST_BUILD
    Preferences prefs;
    if (!prefs.begin(NVS_NAMESPACE, false)) return false;

    prefs.putUChar("node_id",    _cfg.nodeId);
    prefs.putUShort("network_id",_cfg.networkId);
    prefs.putULong("tx_interval",_cfg.telemetryIntervalMs);
    prefs.putString("location",  _cfg.location);
    prefs.putString("zone",      _cfg.zone);
    prefs.putFloat("temp_hi",    _cfg.tempThreshHigh);
    prefs.putFloat("temp_lo",    _cfg.tempThreshLow);
    prefs.putFloat("batt_lo",    _cfg.batteryThreshLow);
    prefs.putFloat("batt_crit",  _cfg.batteryThreshCritical);
    prefs.putFloat("lora_freq",  _cfg.loraFrequency);
    prefs.putUChar("lora_sf",    _cfg.loraSpreadingFactor);
    prefs.putUChar("lora_txpwr", _cfg.loraTxPower);
    prefs.putBool("mesh_en",     _cfg.meshEnabled);
    prefs.putLong("tz_offset",   _cfg.tzOffsetMinutes);
    prefs.putULong("time_sync",  _cfg.lastTimeSync);
    prefs.end();
#endif
    return true;
}

void NodeConfigStore::factory_reset()
{
#ifndef TEST_BUILD
    Preferences prefs;
    prefs.begin(NVS_NAMESPACE, false);
    prefs.clear();
    prefs.end();
#endif
    _apply_defaults();
    save();
}

void NodeConfigStore::_apply_defaults()
{
    _fill_defaults(_cfg);
}
