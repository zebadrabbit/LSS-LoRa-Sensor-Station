/**
 * @file sensor_base.h
 * @brief Abstract base class for all sensor drivers.
 *
 * All sensor implementations inherit from SensorBase and override
 * begin(), read(), and getValue().
 */

#pragma once
#include <Arduino.h>
#include "packets.h"

/**
 * Abstract sensor driver interface.
 *
 * A sensor may expose multiple values (e.g. DHT22 → temperature + humidity).
 * The values() method returns all current readings as SensorValuePacket
 * entries ready to be packed into a MultiSensorHeader.
 */
class SensorBase {
public:
    virtual ~SensorBase() = default;

    /**
     * Initialise the sensor hardware.
     *
     * Called once from setup().  Returns false if the sensor is not
     * present or fails self-test.
     */
    virtual bool begin() = 0;

    /**
     * Trigger a new measurement and cache the result internally.
     *
     * Returns false if the read fails (hardware error, timeout, etc.).
     * The cached values remain unchanged on failure.
     */
    virtual bool read() = 0;

    /**
     * Fill *out* with the most recently cached values.
     *
     * @param out      Caller-supplied array to fill.
     * @param max_len  Capacity of out.
     * @return         Number of entries written.
     */
    virtual uint8_t values(SensorValuePacket *out, uint8_t max_len) const = 0;

    /**
     * Human-readable name for this sensor (e.g. "DHT22").
     */
    virtual const char *name() const = 0;

    /** Returns true if begin() succeeded and the sensor is operational. */
    bool is_ready() const { return _ready; }

protected:
    bool _ready = false;

    /** Helper: write a single value to *out* at *idx* if space remains. */
    static uint8_t _write_value(SensorValuePacket *out, uint8_t max_len,
                                uint8_t *idx, ValueType type, float value) {
        if (*idx >= max_len) return 0;
        out[*idx].type  = static_cast<uint8_t>(type);
        out[*idx].value = value;
        (*idx)++;
        return 1;
    }
};
