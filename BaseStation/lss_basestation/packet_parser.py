"""
packet_parser.py — Deserialise all LSS packet types from raw bytes.

Packet formats are defined in LSS.md (Packet Protocol section).
All structs are little-endian and packed (no padding).
"""

import struct
import logging
from dataclasses import dataclass, field
from typing import Optional

from . import config as cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Struct format strings (little-endian, packed)
# ---------------------------------------------------------------------------

# MultiSensorHeader: uint16 syncWord, uint16 networkId, uint8 packetType,
#   uint8 sensorId, uint8 valueCount, uint8 batteryPercent,
#   uint8 powerState, uint8 lastCommandSeq, uint8 ackStatus,
#   char[32] location, char[16] zone
_MULTI_HEADER_FMT = "<HHBBBBBBBx32s16s"
_MULTI_HEADER_SIZE = struct.calcsize(_MULTI_HEADER_FMT)  # should be 58

# SensorValuePacket: uint8 type, float value
_VALUE_FMT = "<Bf"
_VALUE_SIZE = struct.calcsize(_VALUE_FMT)  # 5 bytes

# CommandPacket: uint16 syncWord, uint8 commandType, uint8 targetSensorId,
#   uint8 sequenceNumber, uint8 dataLength, uint8[192] data, uint16 checksum
_CMD_FMT = "<HBBBBx192sH"
_CMD_SIZE = struct.calcsize(_CMD_FMT)  # 200 bytes total

# AckPacket: same layout as CommandPacket but commandType is CMD_ACK/NACK
_ACK_FMT = "<HBBBBx192sH"
_ACK_SIZE = struct.calcsize(_ACK_FMT)

# Legacy SensorData (v1): kept for backward-compatibility parsing only.
# uint16 syncWord, uint8 sensorId, uint16 networkId, float temperature,
#   float humidity, uint8 batteryPercent, int8 rssi, float snr
_LEGACY_FMT = "<HBHffBbf"
_LEGACY_SIZE = struct.calcsize(_LEGACY_FMT)


# ---------------------------------------------------------------------------
# Parsed data classes
# ---------------------------------------------------------------------------

@dataclass
class SensorValue:
    """A single typed measurement from a multi-sensor telemetry packet."""
    type: int
    value: float

    @property
    def unit(self) -> str:
        return cfg.VALUE_UNITS.get(self.type, "")

    @property
    def name(self) -> str:
        return cfg.VALUE_NAMES.get(self.type, f"type_{self.type}")


@dataclass
class MultiSensorPacket:
    """Parsed representation of a PACKET_MULTI_SENSOR frame."""
    sync_word: int
    network_id: int
    packet_type: int
    sensor_id: int
    battery_percent: int
    power_state: int            # 0 = discharging, 1 = charging
    last_command_seq: int       # Piggybacked ACK sequence number
    ack_status: int             # 0 = success, non-zero = error code
    location: str
    zone: str
    values: list[SensorValue] = field(default_factory=list)
    rssi: Optional[float] = None
    snr: Optional[float] = None


@dataclass
class CommandPacket:
    """Parsed representation of a PACKET_CONFIG / command frame."""
    sync_word: int
    command_type: int
    target_sensor_id: int
    sequence_number: int
    data_length: int
    data: bytes


@dataclass
class AckPacket:
    """Parsed representation of a CMD_ACK / CMD_NACK response."""
    sync_word: int
    command_type: int
    sensor_id: int
    sequence_number: int
    status_code: int
    data_length: int
    data: bytes


@dataclass
class LegacyPacket:
    """Parsed representation of a v1 SensorData packet."""
    sync_word: int
    sensor_id: int
    network_id: int
    temperature: float
    humidity: float
    battery_percent: int
    rssi: int
    snr: float


# ---------------------------------------------------------------------------
# CRC-16 (CCITT-FALSE / poly 0x1021, init 0xFFFF, no reflection)
# ---------------------------------------------------------------------------

def _crc16(data: bytes) -> int:
    """Compute CRC-16/CCITT-FALSE over *data*."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
        crc &= 0xFFFF
    return crc


# ---------------------------------------------------------------------------
# Public parsing functions
# ---------------------------------------------------------------------------

def detect_packet_type(raw: bytes) -> Optional[int]:
    """
    Inspect the first two bytes of *raw* to determine the packet type.

    Returns one of the PACKET_* constants or None if unrecognised.
    """
    if len(raw) < 2:
        return None
    sync = struct.unpack_from("<H", raw, 0)[0]
    if sync == cfg.SYNC_LEGACY and len(raw) >= _LEGACY_SIZE:
        return cfg.PACKET_LEGACY
    if sync == cfg.SYNC_MULTI_SENSOR:
        return cfg.PACKET_MULTI_SENSOR
    if sync == cfg.SYNC_COMMAND:
        # Differentiate command vs ACK by commandType byte
        if len(raw) >= 3:
            cmd_type = raw[2]
            if cmd_type in (cfg.CMD_ACK, cfg.CMD_NACK):
                return cfg.PACKET_ACK
        return cfg.PACKET_CONFIG
    return None


def parse_multi_sensor(raw: bytes, rssi: Optional[float] = None,
                       snr: Optional[float] = None) -> Optional[MultiSensorPacket]:
    """
    Deserialise a PACKET_MULTI_SENSOR frame from *raw*.

    Returns None if the data is too short, the checksum fails, or the
    sync word is wrong.
    """
    if len(raw) < _MULTI_HEADER_SIZE + 2:
        logger.debug("multi-sensor packet too short (%d bytes)", len(raw))
        return None

    try:
        fields = struct.unpack_from(_MULTI_HEADER_FMT, raw, 0)
    except struct.error as exc:
        logger.warning("multi-sensor header unpack failed: %s", exc)
        return None

    (sync, network_id, pkt_type, sensor_id, value_count, batt_pct,
     power_state, last_cmd_seq, ack_status, location_b, zone_b) = fields

    if sync != cfg.SYNC_MULTI_SENSOR:
        logger.debug("unexpected sync word 0x%04X in multi-sensor packet", sync)
        return None

    if value_count > 16:
        logger.warning("value_count %d exceeds maximum 16; clamping", value_count)
        value_count = 16

    expected_len = _MULTI_HEADER_SIZE + value_count * _VALUE_SIZE + 2
    if len(raw) < expected_len:
        logger.debug("packet too short for %d values (have %d, need %d)",
                     value_count, len(raw), expected_len)
        return None

    # Verify checksum (covers header + all value entries)
    payload_end = _MULTI_HEADER_SIZE + value_count * _VALUE_SIZE
    received_crc = struct.unpack_from("<H", raw, payload_end)[0]
    computed_crc = _crc16(raw[:payload_end])
    if received_crc != computed_crc:
        logger.warning("CRC mismatch on multi-sensor packet from node %d "
                       "(got 0x%04X, want 0x%04X)", sensor_id,
                       received_crc, computed_crc)
        return None

    values: list[SensorValue] = []
    offset = _MULTI_HEADER_SIZE
    for _ in range(value_count):
        vtype, vfloat = struct.unpack_from(_VALUE_FMT, raw, offset)
        values.append(SensorValue(type=vtype, value=vfloat))
        offset += _VALUE_SIZE

    return MultiSensorPacket(
        sync_word=sync,
        network_id=network_id,
        packet_type=pkt_type,
        sensor_id=sensor_id,
        battery_percent=batt_pct,
        power_state=power_state,
        last_command_seq=last_cmd_seq,
        ack_status=ack_status,
        location=location_b.rstrip(b"\x00").decode("utf-8", errors="replace"),
        zone=zone_b.rstrip(b"\x00").decode("utf-8", errors="replace"),
        values=values,
        rssi=rssi,
        snr=snr,
    )


def parse_command(raw: bytes) -> Optional[CommandPacket]:
    """
    Deserialise a CommandPacket from *raw*.

    Returns None on length, sync-word, or checksum failure.
    """
    if len(raw) < _CMD_SIZE:
        return None
    try:
        sync, cmd_type, target_id, seq, data_len, data_bytes, crc = \
            struct.unpack_from(_CMD_FMT, raw, 0)
    except struct.error as exc:
        logger.warning("command packet unpack failed: %s", exc)
        return None

    if sync != cfg.SYNC_COMMAND:
        return None

    payload_end = _CMD_SIZE - 2
    if _crc16(raw[:payload_end]) != crc:
        logger.warning("CRC mismatch on command packet (type 0x%02X)", cmd_type)
        return None

    return CommandPacket(
        sync_word=sync,
        command_type=cmd_type,
        target_sensor_id=target_id,
        sequence_number=seq,
        data_length=data_len,
        data=data_bytes[:data_len],
    )


def parse_ack(raw: bytes) -> Optional[AckPacket]:
    """
    Deserialise an AckPacket (CMD_ACK or CMD_NACK) from *raw*.

    Returns None on failure.
    """
    if len(raw) < _ACK_SIZE:
        return None
    try:
        sync, cmd_type, sensor_id, seq, status, data_bytes, crc = \
            struct.unpack_from(_ACK_FMT, raw, 0)
    except struct.error as exc:
        logger.warning("ack packet unpack failed: %s", exc)
        return None

    if sync != cfg.SYNC_COMMAND:
        return None

    payload_end = _ACK_SIZE - 2
    if _crc16(raw[:payload_end]) != crc:
        logger.warning("CRC mismatch on ACK packet from node %d", sensor_id)
        return None

    return AckPacket(
        sync_word=sync,
        command_type=cmd_type,
        sensor_id=sensor_id,
        sequence_number=seq,
        status_code=status,
        data_length=len(data_bytes),
        data=data_bytes,
    )


def parse_legacy(raw: bytes, rssi: Optional[float] = None) -> Optional[LegacyPacket]:
    """
    Deserialise a legacy v1 SensorData packet from *raw*.

    Legacy packets carry no checksum; the sync word is the only guard.
    """
    if len(raw) < _LEGACY_SIZE:
        return None
    try:
        sync, sensor_id, network_id, temp, hum, batt, rssi_i, snr = \
            struct.unpack_from(_LEGACY_FMT, raw, 0)
    except struct.error:
        return None

    if sync != cfg.SYNC_LEGACY:
        return None

    return LegacyPacket(
        sync_word=sync,
        sensor_id=sensor_id,
        network_id=network_id,
        temperature=temp,
        humidity=hum,
        battery_percent=batt,
        rssi=rssi_i,
        snr=snr,
    )


# ---------------------------------------------------------------------------
# Command serialisation helpers
# ---------------------------------------------------------------------------

def build_command(command_type: int, target_id: int, seq: int,
                  data: bytes = b"") -> bytes:
    """
    Serialise a CommandPacket ready for transmission.

    *data* must be at most 192 bytes.  Pads to exactly 192 bytes.
    """
    if len(data) > 192:
        raise ValueError(f"command data too long ({len(data)} > 192 bytes)")
    padded = data.ljust(192, b"\x00")
    payload = struct.pack("<HBBBBx192s",
                         cfg.SYNC_COMMAND, command_type, target_id, seq,
                         len(data), padded)
    crc = _crc16(payload)
    return payload + struct.pack("<H", crc)
