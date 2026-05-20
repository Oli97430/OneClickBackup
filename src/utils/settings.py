"""Persistent settings for OneClickBackup.

Stores settings in a JSON file. In portable mode (.portable file next
to the executable), settings are stored alongside the app. Otherwise,
they go into the user's home directory.
"""

from __future__ import annotations

import json
import logging
import os
import threading

_log = logging.getLogger(__name__)

_SETTINGS_FILE = "oneclickbackup_settings.json"

# Defaults
_DEFAULTS: dict = {
    "theme": "dark",
    "language": "en",
    "backup_dir": "",
    "auto_verify": False,
    "auto_compress": False,
    "auto_check_updates": True,
    "show_notifications": True,
    "usb_monitor_enabled": True,
    "minimize_to_tray": False,
    "max_backups": 10,
    "default_wipe_method": "quick",
    "scheduler_enabled": True,
}


def _get_app_dir() -> str:
    """Return the directory containing the main script or EXE."""
    # When frozen (PyInstaller), sys._MEIPASS gives temp dir;
    # we want the directory of the actual .exe
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_portable() -> bool:
    """Check if running in portable mode."""
    app_dir = _get_app_dir()
    return os.path.isfile(os.path.join(app_dir, ".portable"))


def _get_settings_path() -> str:
    """Return the path to the settings JSON file."""
    if _is_portable():
        return os.path.join(_get_app_dir(), _SETTINGS_FILE)
    else:
        settings_dir = os.path.join(os.path.expanduser("~"), ".oneclickbackup")
        os.makedirs(settings_dir, exist_ok=True)
        return os.path.join(settings_dir, _SETTINGS_FILE)


class Settings:
    """Thread-safe, JSON-backed application settings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path = _get_settings_path()
        self._data: dict = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        """Load settings from file, falling back to defaults."""
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                self._data.update(stored)
                # Validate loaded values against default types; discard
                # any value whose type does not match the default.
                for key, default_val in _DEFAULTS.items():
                    if key in self._data and not isinstance(
                        self._data[key], type(default_val)
                    ):
                        _log.warning(
                            "Setting %r has wrong type %s (expected %s), "
                            "reverting to default",
                            key,
                            type(self._data[key]).__name__,
                            type(default_val).__name__,
                        )
                        self._data[key] = default_val
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning("Failed to load settings from %s: %s", self._path, exc)

    def save(self) -> None:
        """Persist current settings to disk."""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            _log.warning("Failed to save settings to %s: %s", self._path, exc)

    def get(self, key: str, default=None):
        """Get a setting value."""
        with self._lock:
            return self._data.get(key, default if default is not None else _DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        """Set a setting value and save."""
        with self._lock:
            self._data[key] = value
            self.save()

    def reset(self) -> None:
        """Reset all settings to defaults."""
        self._data = dict(_DEFAULTS)
        self.save()

    @property
    def is_portable(self) -> bool:
        """Whether running in portable mode."""
        return _is_portable()

    @property
    def settings_path(self) -> str:
        """Path to the settings file."""
        return self._path

    def to_dict(self) -> dict:
        """Return a copy of all settings."""
        return dict(self._data)
