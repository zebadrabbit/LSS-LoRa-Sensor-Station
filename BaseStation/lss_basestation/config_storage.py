"""
config_storage.py — Persistent JSON configuration read/write.

The config file lives at DATA_DIR/config.json and is loaded once at startup.
Call save() after every mutation to keep it in sync with the in-memory state.
"""

import json
import logging
import os
import threading
from typing import Any

from . import config as cfg

logger = logging.getLogger(__name__)

# Default configuration written on first run.
_DEFAULTS: dict[str, Any] = {
    "network_id": cfg.LORA_NETWORK_ID,
    "lora": {
        "frequency": cfg.LORA_FREQUENCY,
        "spreading_factor": cfg.LORA_SPREADING_FACTOR,
        "bandwidth": cfg.LORA_BANDWIDTH,
        "coding_rate": cfg.LORA_CODING_RATE,
        "tx_power": cfg.LORA_TX_POWER,
        "preamble_length": cfg.LORA_PREAMBLE_LENGTH,
    },
    "mqtt": {
        "enabled": False,
        "broker": "localhost",
        "port": 1883,
        "username": "",
        "password": "",
        "topic_prefix": "lss",
    },
    "alerts": {
        "teams_webhook_url": "",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_from": "",
        "smtp_to": [],
        "rate_limit_seconds": 300,
    },
    "nodes": {},   # keyed by str(node_id)
}


class ConfigStorage:
    """Thread-safe persistent configuration backed by a JSON file."""

    def __init__(self, path: str = cfg.CONFIG_PATH) -> None:
        """Initialise and load config from *path*, creating it if absent."""
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return top-level config value for *key*, or *default*."""
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a top-level *key* and persist immediately."""
        with self._lock:
            self._data[key] = value
            self._save_locked()

    def get_section(self, section: str) -> dict[str, Any]:
        """Return a shallow copy of a named subsection dict."""
        with self._lock:
            return dict(self._data.get(section, {}))

    def update_section(self, section: str, updates: dict[str, Any]) -> None:
        """Merge *updates* into a named subsection and persist."""
        with self._lock:
            self._data.setdefault(section, {}).update(updates)
            self._save_locked()

    def get_node(self, node_id: int) -> dict[str, Any]:
        """Return persisted metadata for *node_id*, or an empty dict."""
        with self._lock:
            return dict(self._data.get("nodes", {}).get(str(node_id), {}))

    def set_node(self, node_id: int, data: dict[str, Any]) -> None:
        """Persist metadata for *node_id* and save."""
        with self._lock:
            self._data.setdefault("nodes", {})[str(node_id)] = data
            self._save_locked()

    def all(self) -> dict[str, Any]:
        """Return a deep-copy snapshot of the entire config."""
        with self._lock:
            return json.loads(json.dumps(self._data))

    def replace_all(self, new_data: dict[str, Any]) -> None:
        """Overwrite the entire config with *new_data* and persist."""
        with self._lock:
            self._data = dict(new_data)
            self._save_locked()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load config from disk; writes defaults if the file is absent."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
                logger.info("Config loaded from %s", self._path)
                return
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read config (%s); using defaults", exc)
        self._data = json.loads(json.dumps(_DEFAULTS))
        self._save_locked()
        logger.info("Default config written to %s", self._path)

    def _save_locked(self) -> None:
        """Write current config to disk (must be called with _lock held)."""
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except OSError as exc:
            logger.error("Failed to save config: %s", exc)
