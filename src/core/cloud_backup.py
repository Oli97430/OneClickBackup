"""Cloud backup integration for OneDrive, Google Drive, and Dropbox.

Provides a unified interface for uploading backup archives to cloud
storage providers. Each provider is optional; if the required SDK
is not installed, the provider is simply unavailable.

Usage:
    mgr = CloudBackupManager()
    providers = mgr.list_providers()         # ["onedrive", "local_sync"]
    mgr.upload("onedrive", "/path/to/backup.zip", "Backups/daily.zip")
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass
class CloudProvider:
    """Describes a cloud storage provider."""

    name: str
    display_name: str
    available: bool
    sync_folder: str  # local sync folder path (if known)


class CloudBackupManager:
    """Manages cloud backup uploads via local sync folders.

    The approach uses local sync folders (OneDrive, Google Drive, Dropbox)
    which are automatically synced by the provider's desktop client.
    This avoids the need for OAuth tokens or API keys.
    """

    def __init__(self) -> None:
        self._providers: dict[str, CloudProvider] = {}
        self._detect_providers()

    # ------------------------------------------------------------------
    # Provider detection
    # ------------------------------------------------------------------

    def _detect_providers(self) -> None:
        """Detect available cloud storage sync folders."""
        home = os.path.expanduser("~")

        # OneDrive
        onedrive_paths = [
            os.path.join(home, "OneDrive"),
            os.path.join(home, "OneDrive - Personal"),
            os.environ.get("OneDrive", ""),
            os.environ.get("OneDriveConsumer", ""),
        ]
        for p in onedrive_paths:
            if p and os.path.isdir(p):
                self._providers["onedrive"] = CloudProvider(
                    name="onedrive",
                    display_name="Microsoft OneDrive",
                    available=True,
                    sync_folder=p,
                )
                break
        else:
            self._providers["onedrive"] = CloudProvider(
                name="onedrive", display_name="Microsoft OneDrive",
                available=False, sync_folder="",
            )

        # Google Drive
        gdrive_paths = [
            os.path.join(home, "Google Drive"),
            os.path.join(home, "My Drive"),
            os.path.join(home, "Google Drive", "My Drive"),
            os.path.join("G:", os.sep, "My Drive"),
        ]
        for p in gdrive_paths:
            if p and os.path.isdir(p):
                self._providers["googledrive"] = CloudProvider(
                    name="googledrive",
                    display_name="Google Drive",
                    available=True,
                    sync_folder=p,
                )
                break
        else:
            self._providers["googledrive"] = CloudProvider(
                name="googledrive", display_name="Google Drive",
                available=False, sync_folder="",
            )

        # Dropbox
        dropbox_paths = [
            os.path.join(home, "Dropbox"),
            os.path.join(home, "Dropbox (Personal)"),
        ]
        for p in dropbox_paths:
            if p and os.path.isdir(p):
                self._providers["dropbox"] = CloudProvider(
                    name="dropbox",
                    display_name="Dropbox",
                    available=True,
                    sync_folder=p,
                )
                break
        else:
            self._providers["dropbox"] = CloudProvider(
                name="dropbox", display_name="Dropbox",
                available=False, sync_folder="",
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_providers(self) -> list[CloudProvider]:
        """Return all known providers with their availability status."""
        return list(self._providers.values())

    def get_available_providers(self) -> list[CloudProvider]:
        """Return only providers that have a local sync folder."""
        return [p for p in self._providers.values() if p.available]

    def upload(
        self,
        provider_name: str,
        local_path: str,
        remote_subpath: str = "",
        progress_callback=None,
    ) -> str:
        """Copy a backup file into the provider's sync folder.

        Args:
            provider_name: One of "onedrive", "googledrive", "dropbox".
            local_path: Absolute path to the file to upload.
            remote_subpath: Sub-path within the sync folder (e.g. "Backups/daily.zip").
                           If empty, the file is placed in a "OneClickBackup" subfolder.
            progress_callback: Optional ``(bytes_copied, total_bytes)`` callback.

        Returns:
            The destination path in the sync folder.

        Raises:
            ValueError: If the provider is unknown or unavailable.
            FileNotFoundError: If the source file does not exist.
        """
        prov = self._providers.get(provider_name)
        if prov is None:
            raise ValueError(f"Unknown provider: {provider_name}")
        if not prov.available:
            raise ValueError(
                f"{prov.display_name} sync folder not found. "
                "Please install the desktop client and set up sync."
            )
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"Source file not found: {local_path}")

        if not remote_subpath:
            remote_subpath = os.path.join(
                "OneClickBackup", os.path.basename(local_path)
            )

        dest = os.path.join(prov.sync_folder, remote_subpath)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        total = os.path.getsize(local_path)
        _log.info(
            "Copying %s → %s (%s bytes)", local_path, dest, total,
        )

        # Copy with progress reporting
        copied = 0
        buf_size = 4 * 1024 * 1024  # 4 MB chunks
        with open(local_path, "rb") as src, open(dest, "wb") as dst:
            while True:
                chunk = src.read(buf_size)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                if progress_callback:
                    progress_callback(copied, total)

        _log.info("Cloud upload complete: %s", dest)
        return dest

    def delete_remote(self, provider_name: str, remote_subpath: str) -> bool:
        """Delete a file from the provider's sync folder."""
        prov = self._providers.get(provider_name)
        if prov is None or not prov.available:
            return False
        path = os.path.join(prov.sync_folder, remote_subpath)
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False

    def list_remote_backups(self, provider_name: str) -> list[str]:
        """List backup files in the provider's OneClickBackup folder."""
        prov = self._providers.get(provider_name)
        if prov is None or not prov.available:
            return []
        folder = os.path.join(prov.sync_folder, "OneClickBackup")
        if not os.path.isdir(folder):
            return []
        return sorted(os.listdir(folder))
