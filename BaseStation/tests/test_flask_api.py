"""
tests/test_flask_api.py — Integration tests for the Flask REST API.

Tests cover:
  - Unauthenticated requests return 401
  - Login flow (GET and POST)
  - GET /api/sensors returns node list
  - GET /api/sensors/<id>/history returns time-series
  - POST /api/command queues a command
  - GET /api/config returns config (credentials redacted)
  - POST /api/config saves new config
  - GET /api/lora/status when no hardware
  - POST /api/mqtt/test (mocked)
  - POST /api/alerts/test (mocked)
  - POST /api/alerts/test-email (mocked)
  - 404 on unknown JSON endpoint
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from lss_basestation import config as cfg
from lss_basestation.config_storage import ConfigStorage
from lss_basestation.sensor_store import SensorStore
from lss_basestation.remote_config import RemoteConfig
from lss_basestation.mqtt_manager import MQTTManager
from lss_basestation.alerts import AlertManager
from lss_basestation.packet_parser import MultiSensorPacket, SensorValue
from lss_basestation.web.app import create_app


@pytest.fixture
def app(tmp_path):
    db_path = str(tmp_path / "test.db")
    cfg_path = str(tmp_path / "config.json")

    store  = SensorStore(db_path=db_path)
    rc     = RemoteConfig()
    cs     = ConfigStorage(path=cfg_path)
    cs.set("web_password", "testpass")

    mqtt   = MagicMock(spec=MQTTManager)
    mqtt.test_connection.return_value = (True, "OK")
    alerts = MagicMock(spec=AlertManager)
    alerts.test_teams.return_value = (True, "OK")
    alerts.test_email.return_value = (True, "OK")

    flask_app = create_app(
        sensor_store=store,
        remote_config=rc,
        config_storage=cs,
        mqtt_manager=mqtt,
        alert_manager=alerts,
    )
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(client):
    """Client with an active session (logged in)."""
    client.post("/login", data={"password": "testpass"})
    return client


# ============================================================
# Auth
# ============================================================

def test_dashboard_requires_auth(client):
    resp = client.get("/")
    assert resp.status_code == 302  # redirect to /login


def test_api_requires_auth(client):
    resp = client.get("/api/sensors")
    assert resp.status_code == 401


def test_login_page(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Sign in" in resp.data


def test_login_wrong_password(client):
    resp = client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 200
    assert b"Invalid" in resp.data


def test_login_success(client):
    resp = client.post("/login", data={"password": "testpass"},
                       follow_redirects=True)
    assert resp.status_code == 200


def test_logout(auth_client):
    auth_client.get("/logout")
    resp = auth_client.get("/api/sensors")
    assert resp.status_code == 401

# ============================================================
# GET /api/sensors
# ============================================================

def test_api_sensors_empty(auth_client):
    resp = auth_client.get("/api/sensors")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data == []


def test_api_sensors_with_node(auth_client, app):
    store: SensorStore = app.config["STORE"]
    pkt = MultiSensorPacket(
        sync_word=cfg.SYNC_MULTI_SENSOR,
        network_id=1,
        packet_type=cfg.PACKET_MULTI_SENSOR,
        sensor_id=2,
        battery_percent=70,
        power_state=0,
        last_command_seq=0,
        ack_status=0,
        location="Hall",
        zone="Z1",
        values=[SensorValue(cfg.VALUE_TEMPERATURE, 21.5)],
        rssi=-80.0,
        snr=8.0,
    )
    store.ingest_multi_sensor(pkt)
    resp = auth_client.get("/api/sensors")
    data = json.loads(resp.data)
    assert len(data) == 1
    assert data[0]["node_id"] == 2
    assert data[0]["location"] == "Hall"

# ============================================================
# GET /api/sensors/<id>/history
# ============================================================

def test_history_empty(auth_client):
    resp = auth_client.get("/api/sensors/99/history")
    assert resp.status_code == 200
    assert json.loads(resp.data) == []


def test_history_with_data(auth_client, app):
    store: SensorStore = app.config["STORE"]
    pkt = MultiSensorPacket(
        sync_word=cfg.SYNC_MULTI_SENSOR,
        network_id=1,
        packet_type=cfg.PACKET_MULTI_SENSOR,
        sensor_id=3,
        battery_percent=60,
        power_state=0,
        last_command_seq=0,
        ack_status=0,
        location="",
        zone="",
        values=[SensorValue(cfg.VALUE_TEMPERATURE, 18.0)],
    )
    store.ingest_multi_sensor(pkt)
    resp = auth_client.get("/api/sensors/3/history?limit=10")
    data = json.loads(resp.data)
    assert len(data) == 1

# ============================================================
# POST /api/command
# ============================================================

def test_queue_command(auth_client):
    payload = {"node_id": 5, "command_type": cfg.CMD_PING}
    resp = auth_client.post("/api/command",
                            data=json.dumps(payload),
                            content_type="application/json")
    assert resp.status_code == 202
    data = json.loads(resp.data)
    assert data["queued"] is True
    assert "sequence_number" in data


def test_queue_command_missing_fields(auth_client):
    resp = auth_client.post("/api/command",
                            data=json.dumps({"node_id": 1}),
                            content_type="application/json")
    assert resp.status_code == 400


def test_queue_command_invalid_node(auth_client):
    payload = {"node_id": 0, "command_type": cfg.CMD_PING}
    resp = auth_client.post("/api/command",
                            data=json.dumps(payload),
                            content_type="application/json")
    assert resp.status_code == 400


def test_queue_command_bad_data_hex(auth_client):
    payload = {"node_id": 1, "command_type": cfg.CMD_PING, "data": "ZZZZ"}
    resp = auth_client.post("/api/command",
                            data=json.dumps(payload),
                            content_type="application/json")
    assert resp.status_code == 400


def test_ping_convenience(auth_client):
    resp = auth_client.post("/api/command/ping/3")
    assert resp.status_code == 202


def test_restart_convenience(auth_client):
    resp = auth_client.post("/api/command/restart/7")
    assert resp.status_code == 202

# ============================================================
# GET/POST /api/config
# ============================================================

def test_get_config(auth_client):
    resp = auth_client.get("/api/config")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "lora" in data
    assert "mqtt" in data


def test_get_config_redacts_password(auth_client, app):
    cs: ConfigStorage = app.config["CFG"]
    cs.update_section("mqtt", {"password": "supersecret", "enabled": True})
    resp = auth_client.get("/api/config")
    data = json.loads(resp.data)
    assert data["mqtt"]["password"] == "***"


def test_post_config(auth_client):
    new_cfg = {"network_id": 2, "lora": {"frequency": 915.0}}
    resp = auth_client.post("/api/config",
                            data=json.dumps(new_cfg),
                            content_type="application/json")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["saved"] is True

# ============================================================
# GET /api/lora/status
# ============================================================

def test_lora_status_no_hardware(auth_client):
    resp = auth_client.get("/api/lora/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["available"] is False

# ============================================================
# POST /api/mqtt/test
# ============================================================

def test_mqtt_test(auth_client):
    resp = auth_client.post("/api/mqtt/test")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True

# ============================================================
# POST /api/alerts/test and test-email
# ============================================================

def test_alerts_test(auth_client):
    resp = auth_client.post("/api/alerts/test")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True


def test_alerts_test_email(auth_client):
    resp = auth_client.post("/api/alerts/test-email",
                            data=json.dumps({}),
                            content_type="application/json")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True

# ============================================================
# 404
# ============================================================

def test_404_json(auth_client):
    resp = auth_client.get("/api/nonexistent")
    assert resp.status_code == 404
    data = json.loads(resp.data)
    assert "error" in data
