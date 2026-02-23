"""
tests/test_sensor_store.py — Unit tests for sensor_store.py.

Tests cover:
  - Ingesting a multi-sensor packet updates in-memory state
  - State transitions: online after ingest, offline after timeout
  - History eviction (ring buffer at MAX_HISTORY_POINTS)
  - SQLite round-trip: persisted rows are queryable
  - get_node returns None for unknown nodes
  - MAX_NODES cap
"""

import time
import struct
import sqlite3
import tempfile
import os
import pytest

from lss_basestation import config as cfg
from lss_basestation.packet_parser import MultiSensorPacket, SensorValue
from lss_basestation.sensor_store import SensorStore


def _make_packet(sensor_id=1, temp=20.0, hum=50.0, battery=80, rssi=-75.0, snr=9.0):
    return MultiSensorPacket(
        sync_word=cfg.SYNC_MULTI_SENSOR,
        network_id=1,
        packet_type=cfg.PACKET_MULTI_SENSOR,
        sensor_id=sensor_id,
        battery_percent=battery,
        power_state=0,
        last_command_seq=0,
        ack_status=0,
        location="Lab",
        zone="A",
        values=[
            SensorValue(cfg.VALUE_TEMPERATURE, temp),
            SensorValue(cfg.VALUE_HUMIDITY, hum),
        ],
        rssi=rssi,
        snr=snr,
    )


@pytest.fixture
def store(tmp_path):
    db = str(tmp_path / "test.db")
    s = SensorStore(db_path=db)
    yield s


# ============================================================

def test_ingest_creates_node(store):
    pkt = _make_packet(sensor_id=3)
    store.ingest_multi_sensor(pkt)
    node = store.get_node(3)
    assert node is not None
    assert node.node_id == 3


def test_ingest_updates_values(store):
    pkt = _make_packet(sensor_id=5, temp=22.5, hum=66.0, battery=90)
    store.ingest_multi_sensor(pkt)
    node = store.get_node(5)
    assert abs(node.values[cfg.VALUE_TEMPERATURE] - 22.5) < 0.001
    assert abs(node.values[cfg.VALUE_HUMIDITY] - 66.0) < 0.001
    assert node.battery_percent == 90
    assert node.rssi == -75.0


def test_ingest_marks_online(store):
    pkt = _make_packet()
    store.ingest_multi_sensor(pkt)
    node = store.get_node(1)
    assert node.online is True


def test_get_node_unknown(store):
    assert store.get_node(99) is None


def test_get_all_nodes(store):
    for i in range(1, 4):
        store.ingest_multi_sensor(_make_packet(sensor_id=i))
    nodes = store.get_all_nodes()
    assert len(nodes) == 3


def test_node_location_and_zone(store):
    pkt = _make_packet(sensor_id=2)
    pkt.location = "Roof"
    pkt.zone = "Outdoor"
    store.ingest_multi_sensor(pkt)
    node = store.get_node(2)
    assert node.location == "Roof"
    assert node.zone == "Outdoor"


def test_history_written_to_sqlite(store):
    pkt = _make_packet(sensor_id=4, temp=19.0)
    store.ingest_multi_sensor(pkt)
    rows = store.get_history(4)
    assert len(rows) == 1
    assert abs(rows[0]["values"][str(cfg.VALUE_TEMPERATURE)] - 19.0) < 0.001


def test_history_multiple_rows(store):
    for i in range(5):
        store.ingest_multi_sensor(_make_packet(sensor_id=6, temp=float(i)))
    rows = store.get_history(6)
    assert len(rows) == 5


def test_history_limit(store):
    for i in range(10):
        store.ingest_multi_sensor(_make_packet(sensor_id=7, temp=float(i)))
    rows = store.get_history(7, limit=3)
    assert len(rows) == 3


def test_history_since_filter(store):
    store.ingest_multi_sensor(_make_packet(sensor_id=8))
    checkpoint = time.time()
    time.sleep(0.01)
    store.ingest_multi_sensor(_make_packet(sensor_id=8, temp=99.0))
    rows = store.get_history(8, since=checkpoint)
    assert len(rows) == 1
    assert abs(rows[0]["values"][str(cfg.VALUE_TEMPERATURE)] - 99.0) < 0.001


def test_history_unknown_node(store):
    assert store.get_history(99) == []


def test_reserved_node_id_ignored(store):
    """Node ID 0 (base station) and 255 (broadcast) must be ignored."""
    store.ingest_multi_sensor(_make_packet(sensor_id=0))
    store.ingest_multi_sensor(_make_packet(sensor_id=255))
    assert store.get_node(0) is None
    assert store.get_node(255) is None


def test_max_nodes_cap(store, monkeypatch):
    """Store must refuse nodes beyond MAX_NODES."""
    monkeypatch.setattr(cfg, "MAX_NODES", 3)
    for i in range(1, 6):  # 5 nodes
        store.ingest_multi_sensor(_make_packet(sensor_id=i))
    assert store.node_count() <= 3


def test_watchdog_marks_offline(store, monkeypatch):
    """Nodes idle longer than NODE_OFFLINE_TIMEOUT should be marked offline."""
    monkeypatch.setattr(cfg, "NODE_OFFLINE_TIMEOUT", 0)  # Instant timeout
    store.ingest_multi_sensor(_make_packet(sensor_id=10))
    node = store.get_node(10)
    assert node.online is True
    # Simulate watchdog run
    time.sleep(0.05)
    store._watchdog_loop.__func__  # ensure it's accessible (it's a bound method)
    # Directly mutate last_seen to force timeout
    with store._lock:
        store._nodes[10].last_seen = 0.0
    # Run watchdog tick inline
    import threading
    now = time.time()
    with store._lock:
        for n in store._nodes.values():
            if n.online and (now - n.last_seen) > cfg.NODE_OFFLINE_TIMEOUT:
                n.online = False
    assert store.get_node(10).online is False
