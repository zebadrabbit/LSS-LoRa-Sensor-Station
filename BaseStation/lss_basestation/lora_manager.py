"""
lora_manager.py — RFM95W SPI driver wrapper and packet receive/transmit loop.

Responsibilities:
  - Initialise the RFM95W via adafruit-circuitpython-rfm9x
  - Run a blocking receive loop on a background thread
  - Dispatch each received packet to packet_parser and then to sensor_store
  - Process piggybacked ACKs and standalone ACK packets
  - Drain the remote_config command queue and transmit due commands
  - Handle node announce → send CMD_BASE_WELCOME
  - Emit periodic time-sync broadcasts every TIME_SYNC_INTERVAL seconds
"""

import logging
import threading
import time
import struct
from typing import Optional, TYPE_CHECKING

from . import config as cfg
from .packet_parser import (
    detect_packet_type,
    parse_multi_sensor,
    parse_legacy,
    parse_command,
    parse_ack,
)

if TYPE_CHECKING:
    from .sensor_store import SensorStore
    from .remote_config import RemoteConfig
    from .mqtt_manager import MQTTManager
    from .alerts import AlertManager
    from .config_storage import ConfigStorage

logger = logging.getLogger(__name__)

# Guard: only import hardware libraries when running on the actual Pi.
try:
    import board  # type: ignore
    import busio  # type: ignore
    import digitalio  # type: ignore
    import adafruit_rfm9x  # type: ignore
    _HARDWARE_AVAILABLE = True
except (ImportError, NotImplementedError):
    _HARDWARE_AVAILABLE = False
    logger.warning("Hardware libraries not available; LoRa radio in stub mode")


class LoRaManager:
    """
    Manages the RFM95W radio and the packet receive / transmit loop.

    If hardware is not available (non-Pi environment), the class starts in
    stub mode: no radio is initialised, but all other logic runs normally.
    This makes unit-testing possible without physical hardware.
    """

    def __init__(
        self,
        sensor_store: "SensorStore",
        remote_config: "RemoteConfig",
        config_storage: "ConfigStorage",
        mqtt_manager: Optional["MQTTManager"] = None,
        alert_manager: Optional["AlertManager"] = None,
    ) -> None:
        self._store = sensor_store
        self._rc = remote_config
        self._cfg = config_storage
        self._mqtt = mqtt_manager
        self._alerts = alert_manager
        self._radio = None
        self._running = False
        self._last_time_sync = time.time()  # avoid spurious sync on first loop tick
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialise the radio (if available) and start background threads."""
        if _HARDWARE_AVAILABLE:
            self._init_radio()
        self._running = True
        self._rx_thread = threading.Thread(
            target=self._rx_loop, daemon=True, name="lora-rx"
        )
        self._tx_thread = threading.Thread(
            target=self._tx_loop, daemon=True, name="lora-tx"
        )
        self._rx_thread.start()
        self._tx_thread.start()
        logger.info("LoRa manager started (hardware=%s)", _HARDWARE_AVAILABLE)

    def stop(self) -> None:
        """Signal background threads to exit."""
        self._running = False

    @property
    def is_hardware_available(self) -> bool:
        return _HARDWARE_AVAILABLE

    @property
    def radio_status(self) -> dict:
        """Return current radio status for the API."""
        if self._radio is None:
            return {"available": False, "mode": "stub"}
        lora_cfg = self._cfg.get_section("lora")
        return {
            "available": True,
            "mode": "hardware",
            "frequency": lora_cfg.get("frequency", cfg.LORA_FREQUENCY),
            "spreading_factor": lora_cfg.get("spreading_factor", cfg.LORA_SPREADING_FACTOR),
            "bandwidth": lora_cfg.get("bandwidth", cfg.LORA_BANDWIDTH),
            "tx_power": lora_cfg.get("tx_power", cfg.LORA_TX_POWER),
        }

    # ------------------------------------------------------------------
    # Internal — radio initialisation
    # ------------------------------------------------------------------

    def _init_radio(self) -> None:
        """Initialise the RFM95W via CircuitPython SPI."""
        try:
            spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
            cs = digitalio.DigitalInOut(getattr(board, f"D{cfg.LORA_SPI_CS}"))
            reset = digitalio.DigitalInOut(getattr(board, f"D{cfg.LORA_RST}"))
            lora_cfg = self._cfg.get_section("lora")
            self._radio = adafruit_rfm9x.RFM9x(
                spi, cs, reset,
                lora_cfg.get("frequency", cfg.LORA_FREQUENCY),
            )
            self._radio.spreading_factor = lora_cfg.get(
                "spreading_factor", cfg.LORA_SPREADING_FACTOR
            )
            self._radio.signal_bandwidth = lora_cfg.get(
                "bandwidth", cfg.LORA_BANDWIDTH
            )
            self._radio.coding_rate = lora_cfg.get(
                "coding_rate", cfg.LORA_CODING_RATE
            )
            self._radio.tx_power = lora_cfg.get(
                "tx_power", cfg.LORA_TX_POWER
            )
            self._radio.preamble_length = lora_cfg.get(
                "preamble_length", cfg.LORA_PREAMBLE_LENGTH
            )
            network_id = self._cfg.get("network_id", cfg.LORA_NETWORK_ID)
            self._radio.node = cfg.BASE_STATION_ID
            self._radio.destination = cfg.NODE_ID_BROADCAST
            # Sync word: 0x12 + (network_id % 244)
            self._radio.sync_word = 0x12 + (network_id % 244)
            # Promiscuous mode: accept packets regardless of RadioHead dest byte.
            # RadioLib (Arduino) sends raw packets with no RadioHead header, so
            # packet[0] will be our LSS sync byte, not a RadioHead address.
            self._radio.promiscuous = True
            logger.info("RFM95W initialised on %.1f MHz", self._radio.frequency_mhz)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Radio init failed: %s", exc)
            self._radio = None

    # ------------------------------------------------------------------
    # Internal — receive loop
    # ------------------------------------------------------------------

    def _rx_loop(self) -> None:
        """Blocking receive loop — runs on the lora-rx thread."""
        while self._running:
            if self._radio is None:
                time.sleep(0.1)
                continue
            try:
                # with_header=True returns the full buffer including any
                # RadioHead header bytes that adafruit_rfm9x prepends/strips.
                # Because Arduino (RadioLib) sends raw packets with no header,
                # the library in non-header mode would silently strip 4 bytes
                # we didn't add; requesting the header gives us the real bytes.
                raw = self._radio.receive(timeout=0.5, with_header=True)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Radio receive error: %s", exc)
                time.sleep(1)
                continue

            if raw is None:
                continue

            rssi = getattr(self._radio, "last_rssi", None)
            snr = getattr(self._radio, "last_snr", None)
            raw_bytes = bytes(raw)
            logger.info(
                "RX %d bytes  RSSI=%s SNR=%s  hex=%s",
                len(raw_bytes), rssi, snr,
                raw_bytes[:16].hex(),
            )
            self._dispatch(raw_bytes, rssi=rssi, snr=snr)

    def _dispatch(self, raw: bytes, rssi: Optional[float] = None,
                  snr: Optional[float] = None) -> None:
        """Route a raw packet to the correct parser and handler."""
        ptype = detect_packet_type(raw)
        if ptype is None:
            logger.info("Unrecognised packet (%d bytes) — first 8: %s",
                        len(raw), raw[:8].hex())
            return

        if ptype == cfg.PACKET_MULTI_SENSOR:
            pkt = parse_multi_sensor(raw, rssi=rssi, snr=snr)
            if pkt:
                self._handle_multi_sensor(pkt)

        elif ptype == cfg.PACKET_LEGACY:
            pkt = parse_legacy(raw, rssi=rssi)
            if pkt:
                self._store.ingest_legacy(pkt, rssi=rssi, snr=snr)
                if self._mqtt:
                    # Wrap legacy into a minimal multi-sensor for MQTT publish
                    from .packet_parser import MultiSensorPacket, SensorValue
                    mp = MultiSensorPacket(
                        sync_word=pkt.sync_word,
                        network_id=pkt.network_id,
                        packet_type=cfg.PACKET_LEGACY,
                        sensor_id=pkt.sensor_id,
                        battery_percent=pkt.battery_percent,
                        power_state=0,
                        last_command_seq=0,
                        ack_status=0,
                        location="",
                        zone="",
                        values=[
                            SensorValue(cfg.VALUE_TEMPERATURE, pkt.temperature),
                            SensorValue(cfg.VALUE_HUMIDITY, pkt.humidity),
                        ],
                        rssi=rssi,
                        snr=snr,
                    )
                    self._mqtt.publish_packet(mp)

        elif ptype == cfg.PACKET_ACK:
            pkt = parse_ack(raw)
            if pkt:
                success = pkt.command_type == cfg.CMD_ACK
                self._rc.process_ack(pkt.sensor_id, pkt.sequence_number, success)

        elif ptype == cfg.PACKET_CONFIG:
            pkt = parse_command(raw)
            if pkt and pkt.command_type == cfg.CMD_SENSOR_ANNOUNCE:
                self._handle_announce(pkt.target_sensor_id)

    def _handle_multi_sensor(self, pkt) -> None:
        """Process an incoming multi-sensor telemetry packet."""
        self._store.ingest_multi_sensor(pkt)
        # Process piggybacked ACK
        self._rc.process_piggyback_ack(
            pkt.sensor_id, pkt.last_command_seq, pkt.ack_status
        )
        if self._mqtt:
            self._mqtt.publish_packet(pkt)
        # Alert checks
        if self._alerts:
            self._check_alerts(pkt)

    def _handle_announce(self, node_id: int) -> None:
        """Respond to CMD_SENSOR_ANNOUNCE with CMD_BASE_WELCOME."""
        logger.info("Node %d announced — queuing CMD_BASE_WELCOME", node_id)
        now_epoch = int(time.time())
        # Send UTC + zero tz offset (clients can be updated later via CMD_TIME_SYNC)
        self._rc.enqueue_base_welcome(node_id, now_epoch, 0)

    def _check_alerts(self, pkt) -> None:
        """Evaluate threshold conditions and fire alerts if breached."""
        node_cfg = self._cfg_node(pkt.sensor_id)
        for sv in pkt.values:
            if sv.type == cfg.VALUE_TEMPERATURE:
                high = node_cfg.get("temp_thresh_high", 50.0)
                low = node_cfg.get("temp_thresh_low", -20.0)
                if sv.value > high:
                    self._alerts.send(
                        f"Node {pkt.sensor_id}: High Temperature",
                        f"Temperature {sv.value:.1f}°C exceeds threshold {high}°C",
                        key=f"node_{pkt.sensor_id}_temp_high",
                    )
                elif sv.value < low:
                    self._alerts.send(
                        f"Node {pkt.sensor_id}: Low Temperature",
                        f"Temperature {sv.value:.1f}°C below threshold {low}°C",
                        key=f"node_{pkt.sensor_id}_temp_low",
                    )
        if pkt.battery_percent <= node_cfg.get("battery_thresh_critical", 10):
            self._alerts.send(
                f"Node {pkt.sensor_id}: Critical Battery",
                f"Battery at {pkt.battery_percent}%",
                key=f"node_{pkt.sensor_id}_batt_critical",
            )
        elif pkt.battery_percent <= node_cfg.get("battery_thresh_low", 20):
            self._alerts.send(
                f"Node {pkt.sensor_id}: Low Battery",
                f"Battery at {pkt.battery_percent}%",
                key=f"node_{pkt.sensor_id}_batt_low",
            )

    def _cfg_node(self, node_id: int) -> dict:
        """Return persisted node config; fall back to empty dict."""
        return self._cfg.get_node(node_id)

    # ------------------------------------------------------------------
    # Internal — transmit loop
    # ------------------------------------------------------------------

    def _tx_loop(self) -> None:
        """Drain the command queue and send periodic time-syncs."""
        while self._running:
            self._maybe_send_time_sync()
            cmd = self._rc.next_due()
            if cmd and self._radio is not None:
                try:
                    # Set the RadioHead destination byte to the target node ID.
                    # adafruit_rfm9x prepends [dest, node, id, flags] on TX;
                    # the Arduino offset-4 fallback strips these before parsing.
                    self._radio.send(
                        cmd.raw_packet,
                        destination=cmd.node_id,
                        node=cfg.BASE_STATION_ID,
                    )
                    self._rc.mark_sent(cmd.sequence_number)
                    logger.debug(
                        "Sent %s → node %d (seq %d, attempt %d)",
                        cfg.CMD_NAMES.get(cmd.command_type, f"0x{cmd.command_type:02X}"),
                        cmd.node_id, cmd.sequence_number, cmd.attempts + 1,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.error("Radio send failed: %s", exc)
            # Periodically purge completed entries
            self._rc.purge_completed()
            time.sleep(0.05)

    def _maybe_send_time_sync(self) -> None:
        """Broadcast CMD_TIME_SYNC to all nodes every TIME_SYNC_INTERVAL seconds."""
        now = time.time()
        if now - self._last_time_sync < cfg.TIME_SYNC_INTERVAL:
            return
        self._last_time_sync = now
        for node in self._store.get_all_nodes():
            if node.online:
                self._rc.enqueue_time_sync(
                    node.node_id, int(now), 0
                )
        logger.info("Time sync queued for all online nodes")
