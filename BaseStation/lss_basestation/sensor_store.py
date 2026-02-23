"""
sensor_store.py — In-memory sensor state and SQLite time-series history.

Each node's last known state is cached in memory.  Every incoming telemetry
packet is also written to the SQLite database for historical queries and
dashboard charts.

A background thread marks nodes offline after NODE_OFFLINE_TIMEOUT seconds.
"""

import logging
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config as cfg
from .packet_parser import MultiSensorPacket, LegacyPacket, SensorValue

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class NodeState:
    """Last-known state for a single sensor node."""
    node_id: int
    location: str = ""
    zone: str = ""
    battery_percent: int = 0
    power_state: int = 0            # 0 = discharging, 1 = charging
    rssi: Optional[float] = None
    snr: Optional[float] = None
    last_seen: float = 0.0          # Unix timestamp
    online: bool = False
    values: dict[int, float] = field(default_factory=dict)  # type → value
    # Ring buffer of recent readings for sparkline charts
    history: deque = field(default_factory=lambda: deque(maxlen=cfg.MAX_HISTORY_POINTS))


@dataclass
class HistoryPoint:
    """One time-series sample stored in memory and SQLite."""
    timestamp: float
    battery_percent: int
    rssi: Optional[float]
    snr: Optional[float]
    values: dict[int, float]        # type → value


# ---------------------------------------------------------------------------
# SensorStore
# ---------------------------------------------------------------------------

class SensorStore:
    """
    Thread-safe store for node state and historical sensor data.

    Nodes are created automatically on first telemetry receipt.  Up to
    MAX_NODES nodes are tracked; additional nodes are logged and ignored.
    """

    def __init__(self, db_path: str = cfg.DB_PATH) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._nodes: dict[int, NodeState] = {}
        self._db: Optional[sqlite3.Connection] = None
        self._init_db()
        self._start_watchdog()

    # ------------------------------------------------------------------
    # Public interface — ingestion
    # ------------------------------------------------------------------

    def ingest_multi_sensor(self, packet: MultiSensorPacket) -> None:
        """Record a multi-sensor telemetry packet."""
        nid = packet.sensor_id
        if nid == cfg.BASE_STATION_ID or nid == cfg.NODE_ID_BROADCAST:
            logger.debug("dropping packet from reserved node ID %d", nid)
            return
        with self._lock:
            node = self._get_or_create_locked(nid)
            if node is None:
                return
            node.location = packet.location or node.location
            node.zone = packet.zone or node.zone
            node.battery_percent = packet.battery_percent
            node.power_state = packet.power_state
            node.rssi = packet.rssi
            node.snr = packet.snr
            node.last_seen = time.time()
            node.online = True
            for sv in packet.values:
                node.values[sv.type] = sv.value
            point = HistoryPoint(
                timestamp=node.last_seen,
                battery_percent=node.battery_percent,
                rssi=node.rssi,
                snr=node.snr,
                values=dict(node.values),
            )
            node.history.append(point)
        self._write_history(nid, point)

    def ingest_legacy(self, packet: LegacyPacket,
                      rssi: Optional[float] = None,
                      snr: Optional[float] = None) -> None:
        """Record a legacy v1 SensorData packet."""
        nid = packet.sensor_id
        with self._lock:
            node = self._get_or_create_locked(nid)
            if node is None:
                return
            node.battery_percent = packet.battery_percent
            node.rssi = rssi if rssi is not None else float(packet.rssi)
            node.snr = snr if snr is not None else packet.snr
            node.last_seen = time.time()
            node.online = True
            node.values[cfg.VALUE_TEMPERATURE] = packet.temperature
            node.values[cfg.VALUE_HUMIDITY] = packet.humidity
            point = HistoryPoint(
                timestamp=node.last_seen,
                battery_percent=node.battery_percent,
                rssi=node.rssi,
                snr=node.snr,
                values=dict(node.values),
            )
            node.history.append(point)
        self._write_history(nid, point)

    # ------------------------------------------------------------------
    # Public interface — queries
    # ------------------------------------------------------------------

    def get_node(self, node_id: int) -> Optional[NodeState]:
        """Return a snapshot of a node's current state, or None."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return None
            # Return a shallow copy to avoid external mutation
            snap = NodeState(
                node_id=node.node_id,
                location=node.location,
                zone=node.zone,
                battery_percent=node.battery_percent,
                power_state=node.power_state,
                rssi=node.rssi,
                snr=node.snr,
                last_seen=node.last_seen,
                online=node.online,
                values=dict(node.values),
            )
            return snap

    def get_all_nodes(self) -> list[NodeState]:
        """Return snapshots of all tracked nodes."""
        with self._lock:
            return [
                NodeState(
                    node_id=n.node_id,
                    location=n.location,
                    zone=n.zone,
                    battery_percent=n.battery_percent,
                    power_state=n.power_state,
                    rssi=n.rssi,
                    snr=n.snr,
                    last_seen=n.last_seen,
                    online=n.online,
                    values=dict(n.values),
                )
                for n in self._nodes.values()
            ]

    def get_history(self, node_id: int, limit: int = 100,
                    since: float = 0.0) -> list[dict[str, Any]]:
        """
        Return time-series rows for *node_id* from SQLite.

        Rows are ordered oldest-first.  *since* is a Unix timestamp;
        *limit* caps the number of returned rows.
        """
        if self._db is None:
            return []
        try:
            cur = self._db.execute(
                "SELECT timestamp, battery_percent, rssi, snr, values_json "
                "FROM sensor_history "
                "WHERE node_id = ? AND timestamp >= ? "
                "ORDER BY timestamp ASC LIMIT ?",
                (node_id, since, limit),
            )
            rows = []
            for ts, batt, rssi, snr, values_json in cur.fetchall():
                import json
                rows.append({
                    "timestamp": ts,
                    "battery_percent": batt,
                    "rssi": rssi,
                    "snr": snr,
                    "values": json.loads(values_json) if values_json else {},
                })
            return rows
        except sqlite3.Error as exc:
            logger.error("history query failed: %s", exc)
            return []

    def node_count(self) -> int:
        """Return the number of currently-tracked nodes."""
        with self._lock:
            return len(self._nodes)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_locked(self, node_id: int) -> Optional[NodeState]:
        """Return or create a NodeState.  Must be called with _lock held."""
        if node_id in self._nodes:
            return self._nodes[node_id]
        if len(self._nodes) >= cfg.MAX_NODES:
            logger.warning("MAX_NODES (%d) reached; ignoring node %d",
                           cfg.MAX_NODES, node_id)
            return None
        node = NodeState(node_id=node_id)
        self._nodes[node_id] = node
        logger.info("Registered new node %d", node_id)
        return node

    def _init_db(self) -> None:
        """Open (or create) the SQLite database and create the schema."""
        import os
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        try:
            self._db = sqlite3.connect(self._db_path, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS sensor_history (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id       INTEGER NOT NULL,
                    timestamp     REAL    NOT NULL,
                    battery_percent INTEGER,
                    rssi          REAL,
                    snr           REAL,
                    values_json   TEXT
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_ts "
                "ON sensor_history (node_id, timestamp)"
            )
            self._db.commit()
            logger.info("SQLite database opened at %s", self._db_path)
        except sqlite3.Error as exc:
            logger.error("Failed to open SQLite database: %s", exc)
            self._db = None

    def _write_history(self, node_id: int, point: HistoryPoint) -> None:
        """Persist a HistoryPoint to SQLite (best-effort)."""
        if self._db is None:
            return
        import json
        try:
            self._db.execute(
                "INSERT INTO sensor_history "
                "(node_id, timestamp, battery_percent, rssi, snr, values_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    node_id,
                    point.timestamp,
                    point.battery_percent,
                    point.rssi,
                    point.snr,
                    json.dumps({str(k): v for k, v in point.values.items()}),
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            logger.error("Failed to write history for node %d: %s", node_id, exc)

    def _start_watchdog(self) -> None:
        """Start a daemon thread that marks nodes offline after idle timeout."""
        t = threading.Thread(target=self._watchdog_loop, daemon=True,
                             name="sensor-watchdog")
        t.start()

    def _watchdog_loop(self) -> None:
        """Periodically scan nodes and transition them to offline."""
        while True:
            time.sleep(30)
            now = time.time()
            with self._lock:
                for node in self._nodes.values():
                    if node.online and (now - node.last_seen) > cfg.NODE_OFFLINE_TIMEOUT:
                        node.online = False
                        logger.info("Node %d marked offline (last seen %.0f s ago)",
                                    node.node_id, now - node.last_seen)
