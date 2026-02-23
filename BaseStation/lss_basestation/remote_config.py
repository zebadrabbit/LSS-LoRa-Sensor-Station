"""
remote_config.py — Outbound command queue with retry logic.

Commands are enqueued by the Flask API and drained by the LoRa manager's
transmit loop.  Each command is retried up to COMMAND_RETRY_COUNT times
before being marked as failed.  ACKs received from nodes (either as
standalone AckPackets or piggybacked in MultiSensorHeader) clear the
pending entry.
"""

import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import config as cfg
from .packet_parser import build_command

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PendingCommand:
    """A command awaiting delivery to a client node."""
    node_id: int
    command_type: int
    sequence_number: int
    data: bytes
    enqueued_at: float = field(default_factory=time.time)
    attempts: int = 0
    last_attempt_at: float = 0.0
    acked: bool = False
    failed: bool = False

    @property
    def raw_packet(self) -> bytes:
        """Serialise to a ready-to-transmit CommandPacket."""
        return build_command(
            self.command_type,
            self.node_id,
            self.sequence_number,
            self.data,
        )


# ---------------------------------------------------------------------------
# RemoteConfig
# ---------------------------------------------------------------------------

class RemoteConfig:
    """
    Thread-safe command queue with retry and ACK tracking.

    Usage::

        rc = RemoteConfig()
        rc.enqueue(node_id=3, command_type=CMD_PING)
        # ... lora_manager calls rc.next_due() in its TX loop
        cmd = rc.next_due()
        if cmd:
            radio.send(cmd.raw_packet)
            rc.mark_sent(cmd.sequence_number)
        # ... when ACK arrives:
        rc.process_ack(node_id=3, seq=cmd.sequence_number, success=True)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: list[PendingCommand] = []
        self._seq: int = 1  # Start at 1; 0 is the sentinel for "no piggybacked ACK"
        # Optional callback invoked on command success/failure:
        # callback(node_id, seq, command_type, success)
        self._on_result: Optional[Callable[[int, int, int, bool], None]] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_result_callback(
        self, cb: Callable[[int, int, int, bool], None]
    ) -> None:
        """Set a callback invoked when a command succeeds or permanently fails."""
        self._on_result = cb

    def enqueue(self, node_id: int, command_type: int,
                data: bytes = b"") -> int:
        """
        Add a command to the queue.

        Returns the assigned sequence number for ACK correlation.
        """
        with self._lock:
            seq = self._next_seq()
            cmd = PendingCommand(
                node_id=node_id,
                command_type=command_type,
                sequence_number=seq,
                data=data,
            )
            self._queue.append(cmd)
            logger.debug("Enqueued %s → node %d (seq %d)",
                         cfg.CMD_NAMES.get(command_type, f"0x{command_type:02X}"),
                         node_id, seq)
        return seq

    def next_due(self) -> Optional[PendingCommand]:
        """
        Return the next command that is due for (re)transmission, or None.

        A command is due if:
        - It has never been attempted, OR
        - It has been attempted fewer than COMMAND_RETRY_COUNT times AND
          at least COMMAND_RETRY_TIMEOUT seconds have elapsed since the
          last attempt.
        """
        now = time.time()
        with self._lock:
            for cmd in self._queue:
                if cmd.acked or cmd.failed:
                    continue
                if cmd.attempts == 0:
                    return cmd
                elapsed = now - cmd.last_attempt_at
                if elapsed >= cfg.COMMAND_RETRY_TIMEOUT:
                    if cmd.attempts >= cfg.COMMAND_RETRY_COUNT:
                        cmd.failed = True
                        logger.warning(
                            "Command seq %d (type 0x%02X) to node %d "
                            "exhausted all %d retries",
                            cmd.sequence_number, cmd.command_type,
                            cmd.node_id, cfg.COMMAND_RETRY_COUNT,
                        )
                        self._fire_result(cmd, success=False)
                    else:
                        return cmd
        return None

    def mark_sent(self, sequence_number: int) -> None:
        """Record that a transmission attempt was made."""
        now = time.time()
        with self._lock:
            cmd = self._find_locked(sequence_number)
            if cmd:
                cmd.attempts += 1
                cmd.last_attempt_at = now
                logger.debug("cmd seq %d attempt %d/%d",
                             sequence_number, cmd.attempts,
                             cfg.COMMAND_RETRY_COUNT)

    def process_ack(self, node_id: int, sequence_number: int,
                    success: bool) -> bool:
        """
        Mark a pending command as ACKed or NACKed.

        Returns True if a matching entry was found, False otherwise.
        """
        with self._lock:
            cmd = self._find_locked(sequence_number)
            if cmd is None or cmd.node_id != node_id:
                return False
            if success:
                cmd.acked = True
                logger.info("Node %d ACKed command seq %d (type 0x%02X)",
                            node_id, sequence_number, cmd.command_type)
            else:
                cmd.failed = True
                logger.warning("Node %d NACKed command seq %d (type 0x%02X)",
                               node_id, sequence_number, cmd.command_type)
            self._fire_result(cmd, success=success)
            return True

    def process_piggyback_ack(self, node_id: int, last_cmd_seq: int,
                              ack_status: int) -> None:
        """Handle ACK information piggybacked in a MultiSensorHeader."""
        if last_cmd_seq == 0:
            return
        if ack_status == 0:
            self.process_ack(node_id, last_cmd_seq, success=True)
        else:
            self.process_ack(node_id, last_cmd_seq, success=False)

    def pending_for_node(self, node_id: int) -> list[PendingCommand]:
        """Return all active (non-acked, non-failed) commands for *node_id*."""
        with self._lock:
            return [
                c for c in self._queue
                if c.node_id == node_id and not c.acked and not c.failed
            ]

    def all_pending(self) -> list[dict]:
        """Return a summary list of all active commands (for the API)."""
        with self._lock:
            return [
                {
                    "node_id": c.node_id,
                    "command_type": c.command_type,
                    "command_name": cfg.CMD_NAMES.get(
                        c.command_type, f"0x{c.command_type:02X}"
                    ),
                    "sequence_number": c.sequence_number,
                    "attempts": c.attempts,
                    "acked": c.acked,
                    "failed": c.failed,
                    "enqueued_at": c.enqueued_at,
                }
                for c in self._queue
                if not c.acked and not c.failed
            ]

    def purge_completed(self) -> int:
        """Remove all acked/failed entries from the queue.  Returns count removed."""
        with self._lock:
            before = len(self._queue)
            self._queue = [c for c in self._queue if not c.acked and not c.failed]
            return before - len(self._queue)

    # ------------------------------------------------------------------
    # Command factory helpers
    # ------------------------------------------------------------------

    def enqueue_ping(self, node_id: int) -> int:
        """Queue a CMD_PING to *node_id*."""
        return self.enqueue(node_id, cfg.CMD_PING)

    def enqueue_set_interval(self, node_id: int, interval_ms: int) -> int:
        """Queue CMD_SET_INTERVAL with a 4-byte little-endian interval."""
        data = struct.pack("<I", interval_ms)
        return self.enqueue(node_id, cfg.CMD_SET_INTERVAL, data)

    def enqueue_set_location(self, node_id: int, location: str,
                             zone: str) -> int:
        """Queue CMD_SET_LOCATION with null-terminated location and zone."""
        loc_b = location.encode("utf-8")[:31] + b"\x00"
        zone_b = zone.encode("utf-8")[:15] + b"\x00"
        return self.enqueue(node_id, cfg.CMD_SET_LOCATION, loc_b + zone_b)

    def enqueue_set_temp_thresh(self, node_id: int, low: float,
                                high: float) -> int:
        """Queue CMD_SET_TEMP_THRESH with two 4-byte floats (low, high)."""
        data = struct.pack("<ff", low, high)
        return self.enqueue(node_id, cfg.CMD_SET_TEMP_THRESH, data)

    def enqueue_set_battery_thresh(self, node_id: int, low: float,
                                   critical: float) -> int:
        """Queue CMD_SET_BATTERY_THRESH with two 4-byte floats (low, critical)."""
        data = struct.pack("<ff", low, critical)
        return self.enqueue(node_id, cfg.CMD_SET_BATTERY_THRESH, data)

    def enqueue_time_sync(self, node_id: int, utc_epoch: int,
                          tz_offset_min: int) -> int:
        """Queue CMD_TIME_SYNC with epoch (uint32) and tz offset (int16)."""
        data = struct.pack("<Ih", utc_epoch, tz_offset_min)
        return self.enqueue(node_id, cfg.CMD_TIME_SYNC, data)

    def enqueue_restart(self, node_id: int) -> int:
        """Queue CMD_RESTART."""
        return self.enqueue(node_id, cfg.CMD_RESTART)

    def enqueue_factory_reset(self, node_id: int) -> int:
        """Queue CMD_FACTORY_RESET."""
        return self.enqueue(node_id, cfg.CMD_FACTORY_RESET)

    def enqueue_base_welcome(self, node_id: int, utc_epoch: int,
                             tz_offset_min: int) -> int:
        """Queue CMD_BASE_WELCOME (time sync + base config) for a new node."""
        data = struct.pack("<Ih", utc_epoch, tz_offset_min)
        return self.enqueue(node_id, cfg.CMD_BASE_WELCOME, data)

    def enqueue_set_lora_params(self, node_id: int, frequency: float,
                                sf: int, bw: int, tx_power: int) -> int:
        """Queue CMD_SET_LORA_PARAMS."""
        data = struct.pack("<fBBB", frequency, sf, 0, tx_power)
        return self.enqueue(node_id, cfg.CMD_SET_LORA_PARAMS, data)

    def enqueue_set_mesh_config(self, node_id: int, enabled: bool) -> int:
        """Queue CMD_SET_MESH_CONFIG with a single byte flag."""
        data = struct.pack("<B", 1 if enabled else 0)
        return self.enqueue(node_id, cfg.CMD_SET_MESH_CONFIG, data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        """Return the next sequence number, wrapping at 255."""
        seq = self._seq
        self._seq = (self._seq + 1) % 256
        return seq

    def _find_locked(self, seq: int) -> Optional[PendingCommand]:
        """Locate a command by sequence number (must hold _lock)."""
        for cmd in self._queue:
            if cmd.sequence_number == seq:
                return cmd
        return None

    def _fire_result(self, cmd: PendingCommand, success: bool) -> None:
        """Invoke the result callback if registered (must hold _lock)."""
        if self._on_result:
            try:
                self._on_result(cmd.node_id, cmd.sequence_number,
                                cmd.command_type, success)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Command result callback raised: %s", exc)
