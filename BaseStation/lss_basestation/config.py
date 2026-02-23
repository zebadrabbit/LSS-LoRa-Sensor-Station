"""
config.py — Compile-time constants and default LoRa radio parameters.

All values here are the authoritative defaults. Runtime-adjustable settings
live in config_storage.py (persisted to data/config.json).
"""

import os

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "sensors.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "lss.log")

# ---------------------------------------------------------------------------
# Flask / web
# ---------------------------------------------------------------------------

# Must be changed to a random secret before production deployment.
SECRET_KEY = "changeme-lss-secret"
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
FLASK_DEBUG = False

# ---------------------------------------------------------------------------
# LoRa radio (RFM95W / SX1262)
# ---------------------------------------------------------------------------

LORA_FREQUENCY = 915.0          # MHz
LORA_SPREADING_FACTOR = 10      # SF10
LORA_BANDWIDTH = 125000         # Hz (125 kHz)
LORA_CODING_RATE = 5            # 4/5 → denominator only
LORA_TX_POWER = 20              # dBm
LORA_PREAMBLE_LENGTH = 8        # symbols
LORA_MAX_PAYLOAD = 255          # bytes

# Default network ID.  Sync word = 0x12 + (network_id % 244).
LORA_NETWORK_ID = 1

# SPI / GPIO pin numbers (BCM)
LORA_SPI_CS = 7      # GPIO 7 — SPI CE1, board pin 26
LORA_IRQ = 24        # GPIO 24 — board pin 18 (G0 interrupt)
LORA_RST = 25        # GPIO 25 — board pin 22

# ---------------------------------------------------------------------------
# Packet protocol — application-level sync words
# ---------------------------------------------------------------------------

SYNC_LEGACY = 0x1234            # SensorData struct (v1)
SYNC_MULTI_SENSOR = 0xABCD      # MultiSensorHeader (v2.9+)
SYNC_COMMAND = 0xCDEF           # CommandPacket / AckPacket

# Packet type codes (carried in PacketType field)
PACKET_LEGACY = 0
PACKET_MULTI_SENSOR = 1
PACKET_CONFIG = 2
PACKET_ACK = 3

# ---------------------------------------------------------------------------
# Command codes
# ---------------------------------------------------------------------------

CMD_PING = 0x00
CMD_GET_CONFIG = 0x01
CMD_SET_INTERVAL = 0x02
CMD_SET_LOCATION = 0x03
CMD_SET_TEMP_THRESH = 0x04
CMD_SET_BATTERY_THRESH = 0x05
CMD_SET_MESH_CONFIG = 0x06
CMD_RESTART = 0x07
CMD_FACTORY_RESET = 0x08
CMD_SET_LORA_PARAMS = 0x09
CMD_TIME_SYNC = 0x0A
CMD_SENSOR_ANNOUNCE = 0x0B
CMD_BASE_WELCOME = 0x0C
CMD_ACK = 0xA0
CMD_NACK = 0xA1

CMD_NAMES = {
    CMD_PING: "CMD_PING",
    CMD_GET_CONFIG: "CMD_GET_CONFIG",
    CMD_SET_INTERVAL: "CMD_SET_INTERVAL",
    CMD_SET_LOCATION: "CMD_SET_LOCATION",
    CMD_SET_TEMP_THRESH: "CMD_SET_TEMP_THRESH",
    CMD_SET_BATTERY_THRESH: "CMD_SET_BATTERY_THRESH",
    CMD_SET_MESH_CONFIG: "CMD_SET_MESH_CONFIG",
    CMD_RESTART: "CMD_RESTART",
    CMD_FACTORY_RESET: "CMD_FACTORY_RESET",
    CMD_SET_LORA_PARAMS: "CMD_SET_LORA_PARAMS",
    CMD_TIME_SYNC: "CMD_TIME_SYNC",
    CMD_SENSOR_ANNOUNCE: "CMD_SENSOR_ANNOUNCE",
    CMD_BASE_WELCOME: "CMD_BASE_WELCOME",
    CMD_ACK: "CMD_ACK",
    CMD_NACK: "CMD_NACK",
}

# ---------------------------------------------------------------------------
# Value types (SensorValuePacket.type)
# ---------------------------------------------------------------------------

VALUE_TEMPERATURE = 0
VALUE_HUMIDITY = 1
VALUE_PRESSURE = 2
VALUE_LIGHT = 3
VALUE_VOLTAGE = 4
VALUE_CURRENT = 5
VALUE_POWER = 6
VALUE_ENERGY = 7
VALUE_GAS_RESISTANCE = 8
VALUE_BATTERY = 9
VALUE_SIGNAL_STRENGTH = 10
VALUE_MOISTURE = 11
VALUE_GENERIC = 12
VALUE_THERMISTOR_TEMPERATURE = 13

VALUE_UNITS = {
    VALUE_TEMPERATURE: "°C",
    VALUE_HUMIDITY: "%RH",
    VALUE_PRESSURE: "hPa",
    VALUE_LIGHT: "lx",
    VALUE_VOLTAGE: "V",
    VALUE_CURRENT: "mA",
    VALUE_POWER: "mW",
    VALUE_ENERGY: "Wh",
    VALUE_GAS_RESISTANCE: "Ω",
    VALUE_BATTERY: "%",
    VALUE_SIGNAL_STRENGTH: "dBm",
    VALUE_MOISTURE: "%",
    VALUE_GENERIC: "",
    VALUE_THERMISTOR_TEMPERATURE: "°C",
}

VALUE_NAMES = {
    VALUE_TEMPERATURE: "temperature",
    VALUE_HUMIDITY: "humidity",
    VALUE_PRESSURE: "pressure",
    VALUE_LIGHT: "light",
    VALUE_VOLTAGE: "voltage",
    VALUE_CURRENT: "current",
    VALUE_POWER: "power",
    VALUE_ENERGY: "energy",
    VALUE_GAS_RESISTANCE: "gas_resistance",
    VALUE_BATTERY: "battery",
    VALUE_SIGNAL_STRENGTH: "signal_strength",
    VALUE_MOISTURE: "moisture",
    VALUE_GENERIC: "generic",
    VALUE_THERMISTOR_TEMPERATURE: "thermistor_temperature",
}

# ---------------------------------------------------------------------------
# Mesh packet types
# ---------------------------------------------------------------------------

MESH_DATA = 0
MESH_ROUTE_REQUEST = 1
MESH_ROUTE_REPLY = 2
MESH_ROUTE_ERROR = 3
MESH_NEIGHBOR_BEACON = 4

# ---------------------------------------------------------------------------
# Operational constants
# ---------------------------------------------------------------------------

# Node IDs 0 and 255 are reserved; valid client range is 1–254.
BASE_STATION_ID = 0
NODE_ID_BROADCAST = 255
MAX_NODES = 10
MAX_HISTORY_POINTS = 120        # Per-node in-memory history ring

NODE_OFFLINE_TIMEOUT = 300      # Seconds without a packet → offline
TIME_SYNC_INTERVAL = 10800      # Seconds between base→node time syncs (3 h)
NEIGHBOR_BEACON_INTERVAL = 30   # Seconds between mesh neighbor beacons
MESH_ROUTE_TIMEOUT = 600        # Seconds before a route table entry expires
MESH_MAX_HOPS = 5               # Packets with hop_count > this are dropped

COMMAND_RETRY_COUNT = 3         # Maximum delivery attempts per command
COMMAND_RETRY_TIMEOUT = 12      # Seconds before retrying a command
