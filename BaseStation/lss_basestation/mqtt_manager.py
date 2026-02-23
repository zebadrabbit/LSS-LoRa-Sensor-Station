"""
mqtt_manager.py — Publish sensor readings to an MQTT broker.

Topic structure:
    <prefix>/<node_id>/<value_name>   e.g. lss/3/temperature
    <prefix>/<node_id>/battery
    <prefix>/<node_id>/rssi
    <prefix>/<node_id>/online          (payload "1" or "0")

All publishes are fire-and-forget (QoS 0).  The manager reconnects
automatically if the broker becomes unavailable.
"""

import logging
import threading
from typing import Optional

from . import config as cfg
from .packet_parser import MultiSensorPacket

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt  # type: ignore
    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False
    logger.warning("paho-mqtt not installed; MQTT publishing disabled")


class MQTTManager:
    """Thin wrapper around a paho MQTT client with auto-reconnect."""

    def __init__(self, broker: str, port: int, username: str, password: str,
                 topic_prefix: str, enabled: bool = True) -> None:
        self._broker = broker
        self._port = port
        self._username = username
        self._password = password
        self._prefix = topic_prefix.rstrip("/")
        self._enabled = enabled and _PAHO_AVAILABLE
        self._client: Optional["mqtt.Client"] = None
        self._lock = threading.Lock()
        self._connected = False

        if self._enabled:
            self._init_client()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def publish_packet(self, packet: MultiSensorPacket) -> None:
        """Publish all values from *packet* to MQTT."""
        if not self._enabled:
            return
        nid = str(packet.sensor_id)
        self._publish(f"{self._prefix}/{nid}/battery",
                      str(packet.battery_percent))
        self._publish(f"{self._prefix}/{nid}/power_state",
                      str(packet.power_state))
        if packet.rssi is not None:
            self._publish(f"{self._prefix}/{nid}/rssi", f"{packet.rssi:.1f}")
        if packet.snr is not None:
            self._publish(f"{self._prefix}/{nid}/snr", f"{packet.snr:.2f}")
        for sv in packet.values:
            name = cfg.VALUE_NAMES.get(sv.type, f"value_{sv.type}")
            self._publish(f"{self._prefix}/{nid}/{name}", f"{sv.value:.4f}")

    def publish_online_status(self, node_id: int, online: bool) -> None:
        """Publish online/offline status for *node_id*."""
        if not self._enabled:
            return
        self._publish(f"{self._prefix}/{node_id}/online",
                      "1" if online else "0")

    def test_connection(self) -> tuple[bool, str]:
        """
        Attempt a blocking connect/disconnect to validate broker settings.

        Returns (success, message).
        """
        if not _PAHO_AVAILABLE:
            return False, "paho-mqtt not installed"
        try:
            test_client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id="lss-test",
            )
            if self._username:
                test_client.username_pw_set(self._username, self._password)
            test_client.connect(self._broker, self._port, keepalive=5)
            test_client.disconnect()
            return True, "OK"
        except Exception as exc:  # pylint: disable=broad-except
            return False, str(exc)

    def disconnect(self) -> None:
        """Gracefully disconnect the MQTT client."""
        with self._lock:
            if self._client and self._connected:
                try:
                    self._client.disconnect()
                except Exception:  # pylint: disable=broad-except
                    pass
                self._connected = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_client(self) -> None:
        """Create and connect the persistent paho client."""
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="lss-basestation",
        )
        if self._username:
            self._client.username_pw_set(self._username, self._password)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        # Non-blocking connect; loop_start() handles reconnects.
        try:
            self._client.connect_async(self._broker, self._port, keepalive=60)
            self._client.loop_start()
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("MQTT initial connect failed: %s", exc)

    def _on_connect(self, _client, _userdata, _flags, rc, _properties=None) -> None:
        if rc == 0:
            self._connected = True
            logger.info("MQTT connected to %s:%d", self._broker, self._port)
        else:
            self._connected = False
            logger.warning("MQTT connect returned rc=%d", rc)

    def _on_disconnect(self, _client, _userdata, _flags, rc, _properties=None) -> None:
        self._connected = False
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly (rc=%d); "
                           "paho will retry", rc)

    def _publish(self, topic: str, payload: str) -> None:
        """Publish a single message; silently skips if not connected."""
        with self._lock:
            if not self._client or not self._connected:
                return
            try:
                self._client.publish(topic, payload, qos=0, retain=False)
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("MQTT publish failed (%s): %s", topic, exc)
