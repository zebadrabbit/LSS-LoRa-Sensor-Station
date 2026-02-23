"""
web/app.py — Flask REST API and Jinja2 web dashboard.

All mutable state is injected via the ``create_app`` factory so that
the application can be tested without a running radio.
"""

import logging
import time
from datetime import datetime
from functools import wraps
from typing import Any

from flask import (
    Flask, request, jsonify, render_template,
    session, redirect, url_for, abort,
)

from .. import config as cfg
from ..config_storage import ConfigStorage
from ..sensor_store import SensorStore
from ..remote_config import RemoteConfig
from ..mqtt_manager import MQTTManager
from ..alerts import AlertManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(
    sensor_store: SensorStore,
    remote_config: RemoteConfig,
    config_storage: ConfigStorage,
    mqtt_manager: MQTTManager,
    alert_manager: AlertManager,
    lora_manager=None,          # LoRaManager — optional (type hint avoids circular import)
) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder="templates")
    app.secret_key = cfg.SECRET_KEY

    # Stash dependencies in app config for access in view functions.
    app.config["STORE"] = sensor_store
    app.config["RC"] = remote_config
    app.config["CFG"] = config_storage
    app.config["MQTT"] = mqtt_manager
    app.config["ALERTS"] = alert_manager
    app.config["LORA"] = lora_manager

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("logged_in"):
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Unauthorized"}), 401
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    # ------------------------------------------------------------------
    # Auth routes
    # ------------------------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            pw = request.form.get("password", "")
            stored = app.config["CFG"].get("web_password", "admin")
            if pw == stored:
                session["logged_in"] = True
                return redirect(url_for("dashboard"))
            error = "Invalid password"
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    @app.route("/")
    @login_required
    def dashboard():
        store: SensorStore = app.config["STORE"]
        nodes = store.get_all_nodes()
        nodes_data = []
        for n in nodes:
            d = {
                "node_id": n.node_id,
                "location": n.location,
                "zone": n.zone,
                "battery_percent": n.battery_percent,
                "power_state": n.power_state,
                "rssi": n.rssi,
                "snr": n.snr,
                "online": n.online,
                "last_seen": (
                    datetime.fromtimestamp(n.last_seen).strftime("%Y-%m-%d %H:%M:%S")
                    if n.last_seen else "never"
                ),
                "values": {
                    cfg.VALUE_NAMES.get(k, str(k)): {
                        "value": round(v, 2),
                        "unit": cfg.VALUE_UNITS.get(k, ""),
                    }
                    for k, v in n.values.items()
                },
            }
            nodes_data.append(d)
        return render_template("dashboard.html", nodes=nodes_data)

    # ------------------------------------------------------------------
    # API — sensors
    # ------------------------------------------------------------------

    @app.route("/api/sensors")
    @login_required
    def api_sensors():
        store: SensorStore = app.config["STORE"]
        nodes = store.get_all_nodes()
        result = []
        for n in nodes:
            result.append({
                "node_id": n.node_id,
                "location": n.location,
                "zone": n.zone,
                "battery_percent": n.battery_percent,
                "power_state": n.power_state,
                "rssi": n.rssi,
                "snr": n.snr,
                "online": n.online,
                "last_seen": n.last_seen,
                "values": {
                    str(k): v for k, v in n.values.items()
                },
            })
        return jsonify(result)

    @app.route("/api/sensors/<int:node_id>/history")
    @login_required
    def api_sensor_history(node_id: int):
        store: SensorStore = app.config["STORE"]
        limit = min(int(request.args.get("limit", 100)), 1000)
        since = float(request.args.get("since", 0.0))
        rows = store.get_history(node_id, limit=limit, since=since)
        return jsonify(rows)

    # ------------------------------------------------------------------
    # API — commands
    # ------------------------------------------------------------------

    @app.route("/api/command", methods=["POST"])
    @login_required
    def api_command():
        rc: RemoteConfig = app.config["RC"]
        body = request.get_json(force=True) or {}
        node_id = body.get("node_id")
        command_type = body.get("command_type")
        data_hex = body.get("data", "")

        if node_id is None or command_type is None:
            return jsonify({"error": "node_id and command_type are required"}), 400
        if not (1 <= int(node_id) <= 254):
            return jsonify({"error": "node_id must be 1–254"}), 400

        try:
            data = bytes.fromhex(data_hex) if data_hex else b""
        except ValueError:
            return jsonify({"error": "data must be a hex string"}), 400

        seq = rc.enqueue(int(node_id), int(command_type), data)
        return jsonify({"queued": True, "sequence_number": seq}), 202

    # Convenience endpoints for common commands
    @app.route("/api/command/ping/<int:node_id>", methods=["POST"])
    @login_required
    def api_ping(node_id: int):
        rc: RemoteConfig = app.config["RC"]
        seq = rc.enqueue_ping(node_id)
        return jsonify({"queued": True, "sequence_number": seq}), 202

    @app.route("/api/command/restart/<int:node_id>", methods=["POST"])
    @login_required
    def api_restart(node_id: int):
        rc: RemoteConfig = app.config["RC"]
        seq = rc.enqueue_restart(node_id)
        return jsonify({"queued": True, "sequence_number": seq}), 202

    @app.route("/api/command/pending")
    @login_required
    def api_pending_commands():
        rc: RemoteConfig = app.config["RC"]
        return jsonify(rc.all_pending())

    # ------------------------------------------------------------------
    # API — configuration
    # ------------------------------------------------------------------

    @app.route("/api/config", methods=["GET"])
    @login_required
    def api_get_config():
        cs: ConfigStorage = app.config["CFG"]
        data = cs.all()
        # Redact sensitive credentials from GET response
        for section in ("mqtt", "alerts"):
            if section in data:
                for key in ("password", "smtp_password"):
                    if key in data[section] and data[section][key]:
                        data[section][key] = "***"
        return jsonify(data)

    @app.route("/api/config", methods=["POST"])
    @login_required
    def api_set_config():
        cs: ConfigStorage = app.config["CFG"]
        body = request.get_json(force=True) or {}
        cs.replace_all(body)
        return jsonify({"saved": True})

    # ------------------------------------------------------------------
    # API — LoRa status
    # ------------------------------------------------------------------

    @app.route("/api/lora/status")
    @login_required
    def api_lora_status():
        lora = app.config.get("LORA")
        if lora is None:
            return jsonify({"available": False, "mode": "stub"})
        return jsonify(lora.radio_status)

    @app.route("/api/lora/reboot-status", methods=["POST"])
    @login_required
    def api_lora_reboot_status():
        # Placeholder for coordinated LoRa param reboot handshake.
        return jsonify({"status": "ok"})

    # ------------------------------------------------------------------
    # API — MQTT
    # ------------------------------------------------------------------

    @app.route("/api/mqtt/test", methods=["POST"])
    @login_required
    def api_mqtt_test():
        mqtt: MQTTManager = app.config["MQTT"]
        ok, msg = mqtt.test_connection()
        return jsonify({"success": ok, "message": msg})

    # ------------------------------------------------------------------
    # API — alerts
    # ------------------------------------------------------------------

    @app.route("/api/alerts/test", methods=["POST"])
    @login_required
    def api_alerts_test():
        alerts: AlertManager = app.config["ALERTS"]
        ok, msg = alerts.test_teams()
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/alerts/test-email", methods=["POST"])
    @login_required
    def api_alerts_test_email():
        alerts: AlertManager = app.config["ALERTS"]
        body = request.get_json(force=True) or {}
        recipient = body.get("recipient")
        ok, msg = alerts.test_email(recipient=recipient)
        return jsonify({"success": ok, "message": msg})

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return render_template("500.html"), 500

    return app
