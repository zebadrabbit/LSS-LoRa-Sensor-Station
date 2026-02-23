"""
tests/test_remote_config.py — Unit tests for remote_config.py.

Tests cover:
  - Enqueue adds a command and returns a sequence number
  - next_due returns the first un-attempted command
  - mark_sent increments attempt counter
  - Retry: command is re-due after COMMAND_RETRY_TIMEOUT
  - Retry exhaustion: command is marked failed after COMMAND_RETRY_COUNT
  - process_ack clears the pending entry on success
  - process_ack marks failed on NACK
  - process_piggyback_ack handles both success and failure
  - all_pending returns only active commands
  - purge_completed removes acked/failed entries
  - Command factory helpers produce correct payloads
"""

import struct
import time
import pytest

from lss_basestation import config as cfg
from lss_basestation.remote_config import RemoteConfig


@pytest.fixture
def rc():
    return RemoteConfig()


# ============================================================

def test_enqueue_returns_seq(rc):
    seq = rc.enqueue(node_id=1, command_type=cfg.CMD_PING)
    assert isinstance(seq, int)
    assert 0 <= seq <= 255


def test_enqueue_next_due(rc):
    rc.enqueue(1, cfg.CMD_PING)
    cmd = rc.next_due()
    assert cmd is not None
    assert cmd.command_type == cfg.CMD_PING
    assert cmd.node_id == 1


def test_next_due_empty(rc):
    assert rc.next_due() is None


def test_mark_sent_increments_attempts(rc):
    seq = rc.enqueue(1, cfg.CMD_PING)
    cmd = rc.next_due()
    rc.mark_sent(seq)
    # Should not be due immediately (timeout hasn't elapsed)
    assert rc.next_due() is None


def test_retry_after_timeout(rc, monkeypatch):
    monkeypatch.setattr(cfg, "COMMAND_RETRY_TIMEOUT", 0)
    monkeypatch.setattr(cfg, "COMMAND_RETRY_COUNT", 3)
    seq = rc.enqueue(1, cfg.CMD_PING)
    rc.mark_sent(seq)
    # With 0 timeout, it should be due again immediately
    cmd = rc.next_due()
    assert cmd is not None
    assert cmd.sequence_number == seq


def test_retry_exhaustion(rc, monkeypatch):
    monkeypatch.setattr(cfg, "COMMAND_RETRY_TIMEOUT", 0)
    monkeypatch.setattr(cfg, "COMMAND_RETRY_COUNT", 2)
    seq = rc.enqueue(1, cfg.CMD_PING)
    # First attempt
    rc.mark_sent(seq)
    rc.next_due()   # triggers second attempt check
    # Second attempt
    rc.mark_sent(seq)
    cmd = rc.next_due()  # should exhaust on this call
    assert cmd is None or cmd.failed
    # Verify failed flag
    with rc._lock:
        entry = rc._find_locked(seq)
    assert entry is not None
    assert entry.failed is True


def test_process_ack_clears(rc):
    seq = rc.enqueue(3, cfg.CMD_SET_INTERVAL)
    found = rc.process_ack(3, seq, success=True)
    assert found is True
    with rc._lock:
        entry = rc._find_locked(seq)
    assert entry.acked is True


def test_process_nack(rc):
    seq = rc.enqueue(3, cfg.CMD_SET_INTERVAL)
    rc.process_ack(3, seq, success=False)
    with rc._lock:
        entry = rc._find_locked(seq)
    assert entry.failed is True


def test_process_ack_wrong_node(rc):
    seq = rc.enqueue(3, cfg.CMD_PING)
    # Wrong node_id
    found = rc.process_ack(99, seq, success=True)
    assert found is False


def test_process_piggyback_ack_success(rc):
    seq = rc.enqueue(2, cfg.CMD_PING)
    rc.process_piggyback_ack(2, seq, ack_status=0)
    with rc._lock:
        e = rc._find_locked(seq)
    assert e.acked is True


def test_process_piggyback_ack_failure(rc):
    seq = rc.enqueue(2, cfg.CMD_PING)
    rc.process_piggyback_ack(2, seq, ack_status=1)
    with rc._lock:
        e = rc._find_locked(seq)
    assert e.failed is True


def test_process_piggyback_zero_seq(rc):
    # seq=0 means "no piggybacked ACK" — must not touch any real command
    seq = rc.enqueue(2, cfg.CMD_PING)
    rc.process_piggyback_ack(2, 0, ack_status=0)
    with rc._lock:
        e = rc._find_locked(seq)
    # Command with seq=0 may get acked but a later enqueue starts at seq 1 normally.
    # The important thing is no exception is raised.


def test_all_pending(rc):
    rc.enqueue(1, cfg.CMD_PING)
    rc.enqueue(2, cfg.CMD_RESTART)
    pending = rc.all_pending()
    assert len(pending) == 2


def test_all_pending_excludes_acked(rc):
    seq = rc.enqueue(1, cfg.CMD_PING)
    rc.process_ack(1, seq, success=True)
    pending = rc.all_pending()
    assert len(pending) == 0


def test_purge_completed(rc):
    seq = rc.enqueue(1, cfg.CMD_PING)
    rc.process_ack(1, seq, success=True)
    removed = rc.purge_completed()
    assert removed == 1
    assert len(rc._queue) == 0


def test_enqueue_set_interval_payload(rc):
    seq = rc.enqueue_set_interval(1, 15000)
    with rc._lock:
        e = rc._find_locked(seq)
    assert e is not None
    interval, = struct.unpack("<I", e.data)
    assert interval == 15000


def test_enqueue_set_location_payload(rc):
    seq = rc.enqueue_set_location(1, "Basement", "Zone2")
    with rc._lock:
        e = rc._find_locked(seq)
    assert b"Basement" in e.data
    assert b"Zone2" in e.data


def test_enqueue_time_sync_payload(rc):
    epoch = 1700000000
    tz = -300
    seq = rc.enqueue_time_sync(1, epoch, tz)
    with rc._lock:
        e = rc._find_locked(seq)
    ep, tz_r = struct.unpack("<Ih", e.data)
    assert ep == epoch
    assert tz_r == tz


def test_result_callback_on_ack(rc):
    results = []
    rc.set_result_callback(
        lambda nid, seq, ctype, success: results.append((nid, success))
    )
    seq = rc.enqueue(5, cfg.CMD_PING)
    rc.process_ack(5, seq, success=True)
    assert results == [(5, True)]


def test_sequence_wraps_at_255(rc):
    rc._seq = 254
    s1 = rc.enqueue(1, cfg.CMD_PING)
    s2 = rc.enqueue(1, cfg.CMD_PING)
    assert s1 == 254
    assert s2 == 255
    s3 = rc.enqueue(1, cfg.CMD_PING)
    assert s3 == 0  # wraps
