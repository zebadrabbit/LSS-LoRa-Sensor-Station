"""
tests/test_packet_parser.py — Unit tests for packet_parser.py.

Tests cover:
  - CRC-16 known vectors
  - Multi-sensor packet: encode → decode round-trip
  - Multi-sensor packet: checksum rejection
  - Multi-sensor packet: truncated input
  - Command packet: encode → decode round-trip
  - ACK packet: build_ack → parse round-trip
  - Legacy packet: decode
  - detect_packet_type: all sync words
  - detect_packet_type: garbage input
"""

import struct
import pytest

from lss_basestation import config as cfg
from lss_basestation.packet_parser import (
    _crc16,
    build_command,
    parse_multi_sensor,
    parse_command,
    parse_ack,
    parse_legacy,
    detect_packet_type,
    MultiSensorPacket,
    SensorValue,
)

# ============================================================
# CRC-16 helpers
# ============================================================

def test_crc16_empty():
    assert _crc16(b"") == 0xFFFF

def test_crc16_known_vector():
    # CRC-16/CCITT-FALSE of b"123456789" = 0x29B1
    assert _crc16(b"123456789") == 0x29B1

def test_crc16_single_byte():
    # Regression: ensure single-byte input doesn't raise
    result = _crc16(b"\x00")
    assert isinstance(result, int)

# ============================================================
# Multi-sensor packet round-trip
# ============================================================

def _make_multi_raw(values=None, sensor_id=3, network_id=1, corrupt_crc=False):
    """Build a valid (or intentionally corrupt) multi-sensor raw payload."""
    if values is None:
        values = [(cfg.VALUE_TEMPERATURE, 22.5), (cfg.VALUE_HUMIDITY, 55.0)]

    value_count = len(values)
    # Header fmt: <HHBBBBBBBx32s16s
    header = struct.pack(
        "<HHBBBBBBBx32s16s",
        cfg.SYNC_MULTI_SENSOR,
        network_id,
        cfg.PACKET_MULTI_SENSOR,
        sensor_id,
        value_count,
        75,             # battery_percent
        0,              # power_state
        0,              # last_command_seq
        0,              # ack_status
        b"Garage\x00".ljust(32, b"\x00"),
        b"Zone1\x00".ljust(16, b"\x00"),
    )
    vals_bytes = b"".join(struct.pack("<Bf", t, v) for t, v in values)
    payload = header + vals_bytes
    crc = _crc16(payload) if not corrupt_crc else 0xDEAD
    return payload + struct.pack("<H", crc)


def test_multi_sensor_round_trip():
    raw = _make_multi_raw()
    pkt = parse_multi_sensor(raw, rssi=-80.0, snr=7.5)
    assert pkt is not None
    assert pkt.sensor_id == 3
    assert pkt.battery_percent == 75
    assert pkt.location == "Garage"
    assert pkt.zone == "Zone1"
    assert len(pkt.values) == 2
    assert abs(pkt.values[0].value - 22.5) < 0.001
    assert pkt.values[0].type == cfg.VALUE_TEMPERATURE
    assert pkt.rssi == -80.0
    assert pkt.snr == 7.5


def test_multi_sensor_bad_crc():
    raw = _make_multi_raw(corrupt_crc=True)
    assert parse_multi_sensor(raw) is None


def test_multi_sensor_too_short():
    raw = _make_multi_raw()
    assert parse_multi_sensor(raw[:10]) is None


def test_multi_sensor_wrong_sync():
    raw = bytearray(_make_multi_raw())
    # Overwrite sync word
    struct.pack_into("<H", raw, 0, 0x1234)
    assert parse_multi_sensor(bytes(raw)) is None


def test_multi_sensor_max_values():
    """16 values should serialise and deserialise correctly."""
    values = [(i, float(i) * 1.1) for i in range(16)]
    raw = _make_multi_raw(values=values)
    pkt = parse_multi_sensor(raw)
    assert pkt is not None
    assert len(pkt.values) == 16


def test_multi_sensor_no_rssi():
    raw = _make_multi_raw()
    pkt = parse_multi_sensor(raw)
    assert pkt is not None
    assert pkt.rssi is None
    assert pkt.snr is None

# ============================================================
# Command packet round-trip
# ============================================================

def test_command_round_trip():
    data = struct.pack("<I", 15000)  # interval = 15 s
    raw = build_command(cfg.CMD_SET_INTERVAL, target_id=5, seq=42, data=data)
    pkt = parse_command(raw)
    assert pkt is not None
    assert pkt.command_type == cfg.CMD_SET_INTERVAL
    assert pkt.target_sensor_id == 5
    assert pkt.sequence_number == 42
    assert pkt.data == data


def test_command_empty_data():
    raw = build_command(cfg.CMD_PING, target_id=1, seq=0)
    pkt = parse_command(raw)
    assert pkt is not None
    assert pkt.data == b""


def test_command_bad_crc():
    raw = bytearray(build_command(cfg.CMD_PING, 1, 0))
    raw[-1] ^= 0xFF  # Flip last byte of CRC
    assert parse_command(bytes(raw)) is None


def test_command_wrong_sync():
    raw = bytearray(build_command(cfg.CMD_PING, 1, 0))
    struct.pack_into("<H", raw, 0, 0x1234)
    assert parse_command(bytes(raw)) is None


def test_command_data_too_long():
    with pytest.raises(ValueError):
        build_command(cfg.CMD_PING, 1, 0, data=b"\x00" * 193)

# ============================================================
# ACK packet
# ============================================================

def test_ack_round_trip():
    from lss_basestation.packet_parser import AckPacket
    # Build a fake ACK raw packet manually (same struct as CommandPacket)
    # Use build_command with CMD_ACK type as the ACK packet has the SAME layout
    raw = build_command(cfg.CMD_ACK, target_id=3, seq=7)
    pkt = parse_ack(raw)
    assert pkt is not None
    assert pkt.command_type == cfg.CMD_ACK
    assert pkt.sequence_number == 7


def test_nack_round_trip():
    raw = build_command(cfg.CMD_NACK, target_id=2, seq=9)
    pkt = parse_ack(raw)
    assert pkt is not None
    assert pkt.command_type == cfg.CMD_NACK

# ============================================================
# Legacy packet
# ============================================================

def _make_legacy_raw():
    return struct.pack(
        "<HBHffBbf",
        cfg.SYNC_LEGACY,
        7,       # sensor_id
        1,       # network_id
        23.4,    # temperature
        61.2,    # humidity
        85,      # battery_percent
        -70,     # rssi
        8.0,     # snr
    )


def test_legacy_round_trip():
    raw = _make_legacy_raw()
    pkt = parse_legacy(raw)
    assert pkt is not None
    assert pkt.sensor_id == 7
    assert abs(pkt.temperature - 23.4) < 0.001
    assert abs(pkt.humidity - 61.2) < 0.001
    assert pkt.battery_percent == 85


def test_legacy_wrong_sync():
    raw = bytearray(_make_legacy_raw())
    struct.pack_into("<H", raw, 0, 0xDEAD)
    assert parse_legacy(bytes(raw)) is None


def test_legacy_too_short():
    assert parse_legacy(b"\x34\x12") is None

# ============================================================
# detect_packet_type
# ============================================================

def test_detect_multi_sensor():
    raw = _make_multi_raw()
    assert detect_packet_type(raw) == cfg.PACKET_MULTI_SENSOR


def test_detect_legacy():
    raw = _make_legacy_raw()
    assert detect_packet_type(raw) == cfg.PACKET_LEGACY


def test_detect_command():
    raw = build_command(cfg.CMD_PING, 1, 0)
    assert detect_packet_type(raw) == cfg.PACKET_CONFIG


def test_detect_ack():
    raw = build_command(cfg.CMD_ACK, 1, 0)
    assert detect_packet_type(raw) == cfg.PACKET_ACK


def test_detect_garbage():
    assert detect_packet_type(b"\x00\x01\x02\x03") is None


def test_detect_too_short():
    assert detect_packet_type(b"\xCD") is None
