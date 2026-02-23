/**
 * @file sensors.cpp
 * @brief Concrete sensor driver implementations.
 *
 * All hardware library headers are included here only, not in sensors.h,
 * to keep the public API clean and avoid pulling in heavy dependencies
 * for code that merely uses sensor values.
 */

#include "sensors.h"

#ifndef TEST_BUILD
#include <DHT.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Adafruit_BME680.h>
#include <BH1750.h>
#include <Adafruit_INA219.h>
#include <Adafruit_SHT31.h>
#include <Adafruit_BMP280.h>
#include <Arduino.h>
#include <math.h>
#endif

// ============================================================
// DHTSensor
// ============================================================

DHTSensor::DHTSensor(uint8_t pin, uint8_t dht_type)
    : _pin(pin), _type(dht_type) {}

bool DHTSensor::begin()
{
#ifndef TEST_BUILD
    _dht = new DHT(_pin, _type);
    static_cast<DHT *>(_dht)->begin();
#endif
    _ready = true;
    return true;
}

bool DHTSensor::read()
{
    if (!_ready) return false;
#ifndef TEST_BUILD
    DHT *dht = static_cast<DHT *>(_dht);
    float t = dht->readTemperature();
    float h = dht->readHumidity();
    if (isnan(t) || isnan(h)) return false;
    _temp = t;
    _hum  = h;
#endif
    return true;
}

uint8_t DHTSensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_TEMPERATURE, _temp);
    _write_value(out, max_len, &idx, VALUE_HUMIDITY, _hum);
    return idx;
}

const char *DHTSensor::name() const
{
    return (_type == 22) ? "DHT22" : "DHT11";
}

// ============================================================
// DS18B20Sensor
// ============================================================

DS18B20Sensor::DS18B20Sensor(uint8_t pin) : _pin(pin) {}

bool DS18B20Sensor::begin()
{
#ifndef TEST_BUILD
    _ow = new OneWire(_pin);
    _dt = new DallasTemperature(static_cast<OneWire *>(_ow));
    static_cast<DallasTemperature *>(_dt)->begin();
#endif
    _ready = true;
    return true;
}

bool DS18B20Sensor::read()
{
    if (!_ready) return false;
#ifndef TEST_BUILD
    DallasTemperature *dt = static_cast<DallasTemperature *>(_dt);
    dt->requestTemperatures();
    float t = dt->getTempCByIndex(0);
    if (t == DEVICE_DISCONNECTED_C) return false;
    _temp = t;
#endif
    return true;
}

uint8_t DS18B20Sensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_TEMPERATURE, _temp);
    return idx;
}

const char *DS18B20Sensor::name() const { return "DS18B20"; }

// ============================================================
// BME680Sensor
// ============================================================

BME680Sensor::BME680Sensor(uint8_t i2c_addr) : _addr(i2c_addr) {}

bool BME680Sensor::begin()
{
#ifndef TEST_BUILD
    Adafruit_BME680 *bme = new Adafruit_BME680();
    _bme = bme;
    if (!bme->begin(_addr)) return false;
    bme->setTemperatureOversampling(BME680_OS_8X);
    bme->setHumidityOversampling(BME680_OS_2X);
    bme->setPressureOversampling(BME680_OS_4X);
    bme->setIIRFilterSize(BME680_FILTER_SIZE_3);
    bme->setGasHeater(320, 150);
#endif
    _ready = true;
    return true;
}

bool BME680Sensor::read()
{
    if (!_ready) return false;
#ifndef TEST_BUILD
    Adafruit_BME680 *bme = static_cast<Adafruit_BME680 *>(_bme);
    if (!bme->performReading()) return false;
    _temp  = bme->temperature;
    _hum   = bme->humidity;
    _press = bme->pressure / 100.0f;  // Pa → hPa
    _gas   = bme->gas_resistance;
#endif
    return true;
}

uint8_t BME680Sensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_TEMPERATURE, _temp);
    _write_value(out, max_len, &idx, VALUE_HUMIDITY, _hum);
    _write_value(out, max_len, &idx, VALUE_PRESSURE, _press);
    _write_value(out, max_len, &idx, VALUE_GAS_RESISTANCE, _gas);
    return idx;
}

const char *BME680Sensor::name() const { return "BME680"; }

// ============================================================
// BH1750Sensor
// ============================================================

BH1750Sensor::BH1750Sensor(uint8_t i2c_addr) : _addr(i2c_addr) {}

bool BH1750Sensor::begin()
{
#ifndef TEST_BUILD
    BH1750 *bh = new BH1750(_addr);
    _bh = bh;
    if (!bh->begin()) return false;
#endif
    _ready = true;
    return true;
}

bool BH1750Sensor::read()
{
    if (!_ready) return false;
#ifndef TEST_BUILD
    float lux = static_cast<BH1750 *>(_bh)->readLightLevel();
    if (lux < 0) return false;
    _lux = lux;
#endif
    return true;
}

uint8_t BH1750Sensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_LIGHT, _lux);
    return idx;
}

const char *BH1750Sensor::name() const { return "BH1750"; }

// ============================================================
// INA219Sensor
// ============================================================

INA219Sensor::INA219Sensor(uint8_t i2c_addr) : _addr(i2c_addr) {}

bool INA219Sensor::begin()
{
#ifndef TEST_BUILD
    Adafruit_INA219 *ina = new Adafruit_INA219(_addr);
    _ina = ina;
    if (!ina->begin()) return false;
#endif
    _ready = true;
    return true;
}

bool INA219Sensor::read()
{
    if (!_ready) return false;
#ifndef TEST_BUILD
    Adafruit_INA219 *ina = static_cast<Adafruit_INA219 *>(_ina);
    _v = ina->getBusVoltage_V();
    _i = ina->getCurrent_mA();
    _p = ina->getPower_mW();
#endif
    return true;
}

uint8_t INA219Sensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_VOLTAGE, _v);
    _write_value(out, max_len, &idx, VALUE_CURRENT, _i);
    _write_value(out, max_len, &idx, VALUE_POWER, _p);
    return idx;
}

const char *INA219Sensor::name() const { return "INA219"; }

// ============================================================
// SHT31Sensor
// ============================================================

SHT31Sensor::SHT31Sensor(uint8_t i2c_addr) : _addr(i2c_addr) {}

bool SHT31Sensor::begin()
{
#ifndef TEST_BUILD
    Adafruit_SHT31 *sht = new Adafruit_SHT31();
    _sht = sht;
    if (!sht->begin(_addr)) return false;
#endif
    _ready = true;
    return true;
}

bool SHT31Sensor::read()
{
    if (!_ready) return false;
#ifndef TEST_BUILD
    Adafruit_SHT31 *sht = static_cast<Adafruit_SHT31 *>(_sht);
    _temp = sht->readTemperature();
    _hum  = sht->readHumidity();
    if (isnan(_temp) || isnan(_hum)) return false;
#endif
    return true;
}

uint8_t SHT31Sensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_TEMPERATURE, _temp);
    _write_value(out, max_len, &idx, VALUE_HUMIDITY, _hum);
    return idx;
}

const char *SHT31Sensor::name() const { return "SHT31"; }

// ============================================================
// BMP280Sensor
// ============================================================

BMP280Sensor::BMP280Sensor(uint8_t i2c_addr) : _addr(i2c_addr) {}

bool BMP280Sensor::begin()
{
#ifndef TEST_BUILD
    Adafruit_BMP280 *bmp = new Adafruit_BMP280();
    _bmp = bmp;
    if (!bmp->begin(_addr)) return false;
#endif
    _ready = true;
    return true;
}

bool BMP280Sensor::read()
{
    if (!_ready) return false;
#ifndef TEST_BUILD
    Adafruit_BMP280 *bmp = static_cast<Adafruit_BMP280 *>(_bmp);
    _temp  = bmp->readTemperature();
    _press = bmp->readPressure() / 100.0f;  // Pa → hPa
#endif
    return true;
}

uint8_t BMP280Sensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_TEMPERATURE, _temp);
    _write_value(out, max_len, &idx, VALUE_PRESSURE, _press);
    return idx;
}

const char *BMP280Sensor::name() const { return "BMP280"; }

// ============================================================
// ThermistorSensor
// ============================================================

ThermistorSensor::ThermistorSensor(uint8_t adc_pin, float r_fixed,
                                   float r_nominal, float t_nominal,
                                   float b_coeff)
    : _pin(adc_pin), _r_fixed(r_fixed), _r_nom(r_nominal),
      _t_nom(t_nominal), _b(b_coeff) {}

bool ThermistorSensor::begin() { _ready = true; return true; }

bool ThermistorSensor::read()
{
#ifndef TEST_BUILD
    int raw = analogRead(_pin);
    if (raw <= 0) return false;
    float r_therm = _r_fixed * ((4095.0f / (float)raw) - 1.0f);
    float steinhart = log(r_therm / _r_nom) / _b;
    steinhart += 1.0f / (_t_nom + 273.15f);
    _temp = (1.0f / steinhart) - 273.15f;
#endif
    return true;
}

uint8_t ThermistorSensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_THERMISTOR_TEMPERATURE, _temp);
    return idx;
}

const char *ThermistorSensor::name() const { return "Thermistor"; }

// ============================================================
// SoilMoistureSensor
// ============================================================

SoilMoistureSensor::SoilMoistureSensor(uint8_t adc_pin, int dry_raw, int wet_raw)
    : _pin(adc_pin), _dry(dry_raw), _wet(wet_raw) {}

bool SoilMoistureSensor::begin() { _ready = true; return true; }

bool SoilMoistureSensor::read()
{
#ifndef TEST_BUILD
    int raw = analogRead(_pin);
    float pct = 100.0f * (float)(_dry - raw) / (float)(_dry - _wet);
    if (pct < 0.0f) pct = 0.0f;
    if (pct > 100.0f) pct = 100.0f;
    _moisture = pct;
#endif
    return true;
}

uint8_t SoilMoistureSensor::values(SensorValuePacket *out, uint8_t max_len) const
{
    uint8_t idx = 0;
    _write_value(out, max_len, &idx, VALUE_MOISTURE, _moisture);
    return idx;
}

const char *SoilMoistureSensor::name() const { return "SoilMoisture"; }
