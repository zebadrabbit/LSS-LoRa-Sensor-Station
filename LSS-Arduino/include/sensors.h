/**
 * @file sensors.h
 * @brief Concrete sensor driver declarations.
 *
 * Each driver corresponds to one row in the supported hardware table in LSS.md.
 */

#pragma once
#include "sensor_base.h"

// ============================================================
// DHT22 / DHT11 — temperature + humidity
// ============================================================

class DHTSensor : public SensorBase {
public:
    /**
     * @param pin        Data GPIO pin.
     * @param dht_type   DHT22 or DHT11 constant from the DHT library.
     */
    DHTSensor(uint8_t pin, uint8_t dht_type);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _pin;
    uint8_t _type;
    float   _temp = 0.0f;
    float   _hum  = 0.0f;
    void   *_dht  = nullptr;  ///< opaque pointer to DHT instance
};

// ============================================================
// DS18B20 — 1-Wire temperature
// ============================================================

class DS18B20Sensor : public SensorBase {
public:
    /** @param pin  1-Wire data GPIO pin (requires 4.7 kΩ pull-up to 3.3 V). */
    explicit DS18B20Sensor(uint8_t pin);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _pin;
    float   _temp = 0.0f;
    void   *_ow   = nullptr;  ///< opaque OneWire*
    void   *_dt   = nullptr;  ///< opaque DallasTemperature*
};

// ============================================================
// BME680 — temperature, humidity, pressure, gas resistance (I2C)
// ============================================================

class BME680Sensor : public SensorBase {
public:
    /** @param i2c_addr  I2C address (0x76 when SDO low, 0x77 when SDO high). */
    explicit BME680Sensor(uint8_t i2c_addr = 0x76);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _addr;
    float   _temp = 0.0f, _hum = 0.0f, _press = 0.0f, _gas = 0.0f;
    void   *_bme  = nullptr;
};

// ============================================================
// BH1750 — illuminance (I2C)
// ============================================================

class BH1750Sensor : public SensorBase {
public:
    /** @param i2c_addr  I2C address (0x23 when ADDR low, 0x5C when ADDR high). */
    explicit BH1750Sensor(uint8_t i2c_addr = 0x23);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _addr;
    float   _lux  = 0.0f;
    void   *_bh   = nullptr;
};

// ============================================================
// INA219 — voltage, current, power (I2C)
// ============================================================

class INA219Sensor : public SensorBase {
public:
    /** @param i2c_addr  I2C address (0x40–0x4F, set via A0/A1 pins). */
    explicit INA219Sensor(uint8_t i2c_addr = 0x40);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _addr;
    float   _v = 0.0f, _i = 0.0f, _p = 0.0f;
    void   *_ina = nullptr;
};

// ============================================================
// SHT31 — temperature, humidity (I2C)
// ============================================================

class SHT31Sensor : public SensorBase {
public:
    /** @param i2c_addr  I2C address (0x44 when ADDR low, 0x45 when ADDR high). */
    explicit SHT31Sensor(uint8_t i2c_addr = 0x44);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _addr;
    float   _temp = 0.0f, _hum = 0.0f;
    void   *_sht  = nullptr;
};

// ============================================================
// BMP280 — temperature, pressure (I2C)
// ============================================================

class BMP280Sensor : public SensorBase {
public:
    /** @param i2c_addr  I2C address (0x76 when SDO low, 0x77 when SDO high). */
    explicit BMP280Sensor(uint8_t i2c_addr = 0x76);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _addr;
    float   _temp = 0.0f, _press = 0.0f;
    void   *_bmp  = nullptr;
};

// ============================================================
// NTC Thermistor — temperature (ADC)
// ============================================================

class ThermistorSensor : public SensorBase {
public:
    /**
     * @param adc_pin       Analog input pin.
     * @param r_fixed       Fixed resistor value (Ω).
     * @param r_nominal     Thermistor resistance at nominal temp (Ω).
     * @param t_nominal     Nominal temperature (°C).
     * @param b_coeff       Steinhart–Hart B coefficient.
     */
    ThermistorSensor(uint8_t adc_pin, float r_fixed = 10000.0f,
                     float r_nominal = 10000.0f, float t_nominal = 25.0f,
                     float b_coeff = 3950.0f);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _pin;
    float   _r_fixed, _r_nom, _t_nom, _b;
    float   _temp = 0.0f;
};

// ============================================================
// Soil Moisture — capacitive/resistive (ADC)
// ============================================================

class SoilMoistureSensor : public SensorBase {
public:
    /**
     * @param adc_pin   Analog input pin.
     * @param dry_raw   ADC reading in completely dry soil.
     * @param wet_raw   ADC reading in saturated soil.
     */
    SoilMoistureSensor(uint8_t adc_pin, int dry_raw = 3500, int wet_raw = 1500);
    bool        begin()                                        override;
    bool        read()                                         override;
    uint8_t     values(SensorValuePacket *out, uint8_t max_len) const override;
    const char *name() const                                   override;

private:
    uint8_t _pin;
    int     _dry, _wet;
    float   _moisture = 0.0f;
};
