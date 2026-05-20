"""Auto-update checker for OneClickBackup.

Queries the GitHub Releases API to check for newer versions and
optionally downloads the updated EXE.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from urllib.request import urlopen, Request
from urllib.error import URLError

_log = logging.getLogger(__name__)

_GITHUB_REPO = "Oli97430/OneClickBackup"
_API_URL = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
_CURRENT_VERSION = "1.1.0"


@dataclass
class UpdateInfo:
    """Information about an available update."""

    current_version: str
    latest_version: str
    is_update_available: bool
    release_url: str
    download_url: str
    release_notes: str
    published_at: str
    file_size: int


class AutoUpdater:
    """Checks for and applies application updates from GitHub releases."""

    def __init__(self, current_version: str = "") -> None:
        self._current_version = current_version or _CURRENT_VERSION
        self._log = logging.getLogger("OneClickBackup.Updater")

    # ------------------------------------------------------------------
    # Version comparison
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_version(version_str: str) -> tuple[int, ...]:
        """Parse a version string like '1.2.3' into a comparable tuple."""
        cleaned = re.sub(r"^v", "", version_str.strip())
        parts = []
        for p in cleaned.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    def _is_newer(self, remote_version: str) -> bool:
        """Return True if *remote_version* is newer than the current."""
        return self._parse_version(remote_version) > self._parse_version(
            self._current_version
        )

    # ------------------------------------------------------------------
    # GitHub API
    # ------------------------------------------------------------------

    def check_for_update(self) -> UpdateInfo:
        """Query the GitHub Releases API for the latest release.

        Returns an :class:`UpdateInfo` with details. Network errors
        result in ``is_update_available = False``.
        """
        self._log.info("Checking for updates from %s...", _API_URL)

        try:
            req = Request(_API_URL, headers={"Accept": "application/json"})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (URLError, OSError, json.JSONDecodeError) as exc:
            self._log.warning("Update check failed: %s", exc)
            return UpdateInfo(
                current_version=self._current_version,
                latest_version=self._current_version,
                is_update_available=False,
                release_url="",
                download_url="",
                release_notes="",
                published_at="",
                file_size=0,
            )

        tag = data.get("tag_name", "")
        body = data.get("body", "") or ""
        html_url = data.get("html_url", "")
        published = data.get("published_at", "")

        # Find the .exe asset
        download_url = ""
        file_size = 0
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".exe"):
                download_url = asset.get("browser_download_url", "")
                file_size = asset.get("size", 0)
                break

        is_newer = self._is_newer(tag)
        self._log.info(
            "Current: %s, Latest: %s, Update available: %s",
            self._current_version, tag, is_newer,
        )

        return UpdateInfo(
            current_version=self._current_version,
            latest_version=tag,
            is_update_available=is_newer,
            release_url=html_url,
            download_url=download_url,
            release_notes=body[:2000],
            published_at=published,
            file_size=file_size,
        )

    def check_async(self, callback) -> None:
        """Check for updates in a background thread.

        Calls *callback(update_info)* on the calling thread's event loop
        when done.
        """
        def _bg():
            info = self.check_for_update()
            callback(info)
        threading.Thread(target=_bg, daemon=True).start()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_update(
        self,
        update_info: UpdateInfo,
        dest_dir: str = "",
        callback=None,
    ) -> str:
        """Download the updated EXE to *dest_dir*.

        Returns the path to the downloaded file.
        Calls *callback(percent, message)* for progress.
        """
        if not update_info.download_url:
            raise RuntimeError("No download URL available.")

        if not dest_dir:
            dest_dir = tempfile.gettempdir()
        os.makedirs(dest_dir, exist_ok=True)

        filename = os.path.basename(update_info.download_url)
        dest_path = os.path.join(dest_dir, filename)

        self._log.info("Downloading update to %s...", dest_path)
        if callback:
            callback(0.0, f"Downloading {filename}...")

        req = Request(update_info.download_url)
        with urlopen(req, timeout=120) as resp:
            total = update_info.file_size or int(
                resp.headers.get("Content-Length", 0)
            )
            downloaded = 0
            chunk_size = 65536
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if callback and total > 0:
                        pct = (downloaded / total) * 100
                        callback(pct, f"Downloading... {downloaded // 1024} KB")

        if callback:
            callback(100.0, "Download complete.")
        self._log.info("Update downloaded: %s (%d bytes)", dest_path, downloaded)
        return dest_path

    def apply_update(self, downloaded_path: str) -> None:
        """Replace the current EXE with the downloaded one and restart.

        Creates a small batch script that waits for the current process
        to exit, replaces the EXE, and relaunches.
        """
        import sys
        current_exe = sys.executable
        if not current_exe.lower().endswith(".exe"):
            self._log.warning("Not running as EXE; cannot auto-apply update.")
            return

        # Write a temporary batch script
        bat_content = (
            f'@echo off\n'
            f'timeout /t 2 /nobreak >nul\n'
            f'copy /y "{downloaded_path}" "{current_exe}"\n'
            f'start "" "{current_exe}"\n'
            f'del "%~f0"\n'
        )
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="ocb_update_")
        with os.fdopen(fd, "w") as f:
            f.write(bat_content)

        self._log.info("Launching update script: %s", bat_path)
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        sys.exit(0)
