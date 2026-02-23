"""
main.py — Entry point for the LSS base station.

Wires together all sub-systems, starts the LoRa receive loop, and
launches the Flask web server.
"""

import logging
import os
import sys
import time

from lss_basestation import config as cfg
from lss_basestation.config_storage import ConfigStorage
from lss_basestation.sensor_store import SensorStore
from lss_basestation.remote_config import RemoteConfig
from lss_basestation.lora_manager import LoRaManager
from lss_basestation.mqtt_manager import MQTTManager
from lss_basestation.alerts import AlertManager
from lss_basestation.web.app import create_app


def _configure_logging() -> None:
    """Set up root logger to write to stdout and to a rotating file."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    os.makedirs(os.path.dirname(cfg.LOG_PATH), exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler
        handlers.append(
            RotatingFileHandler(
                cfg.LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
            )
        )
    except OSError as exc:
        print(f"Warning: could not open log file: {exc}", file=sys.stderr)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def main() -> None:
    _configure_logging()
    logger = logging.getLogger("lss.main")
    logger.info("LoRa Sensor Station starting up")

    # ------------------------------------------------------------------
    # Persistent configuration
    # ------------------------------------------------------------------
    config_storage = ConfigStorage()

    # ------------------------------------------------------------------
    # Core sub-systems
    # ------------------------------------------------------------------
    sensor_store = SensorStore(db_path=cfg.DB_PATH)
    remote_config = RemoteConfig()

    # ------------------------------------------------------------------
    # Optional integrations (MQTT, alerts)
    # ------------------------------------------------------------------
    mqtt_cfg = config_storage.get_section("mqtt")
    mqtt_manager = MQTTManager(
        broker=mqtt_cfg.get("broker", "localhost"),
        port=int(mqtt_cfg.get("port", 1883)),
        username=mqtt_cfg.get("username", ""),
        password=mqtt_cfg.get("password", ""),
        topic_prefix=mqtt_cfg.get("topic_prefix", "lss"),
        enabled=bool(mqtt_cfg.get("enabled", False)),
    )

    alert_cfg = config_storage.get_section("alerts")
    alert_manager = AlertManager(
        teams_webhook_url=alert_cfg.get("teams_webhook_url", ""),
        smtp_host=alert_cfg.get("smtp_host", ""),
        smtp_port=int(alert_cfg.get("smtp_port", 587)),
        smtp_username=alert_cfg.get("smtp_username", ""),
        smtp_password=alert_cfg.get("smtp_password", ""),
        smtp_from=alert_cfg.get("smtp_from", ""),
        smtp_to=alert_cfg.get("smtp_to", []),
        rate_limit_seconds=int(alert_cfg.get("rate_limit_seconds", 300)),
    )

    # ------------------------------------------------------------------
    # LoRa radio manager
    # ------------------------------------------------------------------
    lora_manager = LoRaManager(
        sensor_store=sensor_store,
        remote_config=remote_config,
        config_storage=config_storage,
        mqtt_manager=mqtt_manager,
        alert_manager=alert_manager,
    )
    lora_manager.start()

    # ------------------------------------------------------------------
    # Flask web server (blocking)
    # ------------------------------------------------------------------
    flask_app = create_app(
        sensor_store=sensor_store,
        remote_config=remote_config,
        config_storage=config_storage,
        mqtt_manager=mqtt_manager,
        alert_manager=alert_manager,
        lora_manager=lora_manager,
    )

    logger.info("Starting web server on %s:%d", cfg.FLASK_HOST, cfg.FLASK_PORT)
    flask_app.run(
        host=cfg.FLASK_HOST,
        port=cfg.FLASK_PORT,
        debug=cfg.FLASK_DEBUG,
        use_reloader=False,     # Reloader conflicts with background threads
    )


if __name__ == "__main__":
    main()
