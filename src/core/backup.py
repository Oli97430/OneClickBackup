"""Backup, restore, and clone operations for Windows.

Handles full disk/partition image backup, system backup, disk/partition
cloning, OS migration, backup restoration, and WinPE bootable disk creation.

All destructive operations require administrator privileges.  The module
relies on standard Windows tools (robocopy, wbadmin, diskpart, bcdboot,
PowerShell) and delegates to the helpers in ``src.utils``.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.utils.admin import is_admin
from src.utils.helpers import (
    format_bytes,
    run_diskpart,
    run_powershell,
    generate_timestamp,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BackupInfo:
    """Metadata about a backup."""

    backup_id: str
    name: str
    timestamp: str
    source_disk: int
    source_partitions: list[int]
    backup_type: str  # "full_disk", "partition", "system", "clone"
    total_size_bytes: int
    compressed_size_bytes: int
    backup_path: str
    checksum: str
    os_version: str


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BackupError(Exception):
    """Raised when a backup operation fails."""


class AdminRequiredError(BackupError):
    """Raised when the current process lacks administrator privileges."""


class CancelledError(BackupError):
    """Raised when the user cancels an in-progress operation."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_admin(operation: str = "This operation") -> None:
    """Raise *AdminRequiredError* unless the process is elevated."""
    if not is_admin():
        raise AdminRequiredError(
            f"{operation} requires administrator privileges. "
            "Please re-run the application as administrator."
        )


def _get_os_version() -> str:
    """Return the Windows product name and build, e.g. 'Windows 11 Home 10.0.26100'."""
    try:
        stdout, _, rc = run_powershell(
            "(Get-CimInstance Win32_OperatingSystem).Caption + ' ' + "
            "(Get-CimInstance Win32_OperatingSystem).Version"
        )
        if rc == 0 and stdout:
            return stdout.strip()
    except Exception:
        pass
    return "Unknown"


def _dir_size(path: str) -> int:
    """Recursively compute the total size (in bytes) of *path*."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fname in filenames:
            fp = os.path.join(dirpath, fname)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _get_partition_drive_letter(disk_index: int, partition_index: int) -> Optional[str]:
    """Return the drive letter (e.g. ``"C"``) for a given disk/partition pair.

    Uses PowerShell ``Get-Partition`` to resolve the mapping.  Returns
    *None* when no drive letter is assigned.
    """
    cmd = (
        f"Get-Partition -DiskNumber {disk_index} -PartitionNumber {partition_index} "
        "| Select-Object -ExpandProperty DriveLetter"
    )
    stdout, _, rc = run_powershell(cmd)
    if rc == 0 and stdout.strip():
        letter = stdout.strip().rstrip(":")
        if letter and letter.isalpha():
            return letter.upper()
    return None


def _get_disk_partitions(disk_index: int) -> list[dict]:
    """Return a list of partition dicts for *disk_index*.

    Each dict has keys ``PartitionNumber``, ``DriveLetter``, ``Size``,
    ``Type``, and ``GptType``.
    """
    cmd = (
        f"Get-Partition -DiskNumber {disk_index} | "
        "Select-Object PartitionNumber, DriveLetter, Size, Type, GptType | "
        "ConvertTo-Json -Compress"
    )
    stdout, _, rc = run_powershell(cmd)
    if rc != 0 or not stdout.strip():
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    # PowerShell returns a single object (not list) when there is only one partition.
    if isinstance(data, dict):
        data = [data]

    partitions: list[dict] = []
    for entry in data:
        partitions.append({
            "PartitionNumber": int(entry.get("PartitionNumber", 0)),
            "DriveLetter": (entry.get("DriveLetter") or "").strip().rstrip(":"),
            "Size": int(entry.get("Size", 0)),
            "Type": str(entry.get("Type", "")),
            "GptType": str(entry.get("GptType", "")),
        })
    return partitions


def _get_disk_style(disk_index: int) -> str:
    """Return ``"GPT"`` or ``"MBR"`` for the given disk, or ``"Unknown"``."""
    cmd = (
        f"(Get-Disk -Number {disk_index}).PartitionStyle"
    )
    stdout, _, rc = run_powershell(cmd)
    if rc == 0:
        style = stdout.strip().upper()
        if style in ("GPT", "MBR"):
            return style
    return "Unknown"


def _get_disk_size(disk_index: int) -> int:
    """Return total disk size in bytes."""
    cmd = f"(Get-Disk -Number {disk_index}).Size"
    stdout, _, rc = run_powershell(cmd)
    if rc == 0 and stdout.strip().isdigit():
        return int(stdout.strip())
    return 0


def _disk_exists(disk_index: int) -> bool:
    """Return True if the disk number is valid and present."""
    cmd = f"Get-Disk -Number {disk_index} -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count"
    stdout, _, rc = run_powershell(cmd)
    return rc == 0 and stdout.strip() == "1"


def _is_system_disk(disk_index: int) -> bool:
    """Return True if *disk_index* contains the current Windows installation."""
    cmd = (
        f"Get-Partition -DiskNumber {disk_index} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DriveLetter -eq 'C' } | Measure-Object | "
        "Select-Object -ExpandProperty Count"
    )
    stdout, _, rc = run_powershell(cmd)
    return rc == 0 and stdout.strip() != "0"


def _clean_and_initialize_disk(disk_index: int, style: str = "GPT") -> bool:
    """Clean the target disk and initialise it with *style* (GPT or MBR).

    Returns *True* on success.
    """
    style_upper = style.upper()
    if style_upper not in ("GPT", "MBR"):
        style_upper = "GPT"

    cmd = (
        f"Clear-Disk -Number {disk_index} -RemoveData -RemoveOEM -Confirm:$false -ErrorAction Stop; "
        f"Initialize-Disk -Number {disk_index} -PartitionStyle {style_upper} -ErrorAction Stop"
    )
    _, stderr, rc = run_powershell(cmd)
    if rc != 0:
        raise BackupError(
            f"Failed to clean/initialise disk {disk_index}: {stderr}"
        )
    return True


def _create_partition(
    disk_index: int,
    size_bytes: int = 0,
    fs: str = "NTFS",
    drive_letter: str = "",
    is_efi: bool = False,
    is_msr: bool = False,
    use_maximum: bool = False,
) -> Optional[str]:
    """Create a single partition on *disk_index* and optionally format it.

    Returns the assigned drive letter or *None*.
    """
    # Build the New-Partition command
    parts: list[str] = [f"New-Partition -DiskNumber {disk_index}"]
    if use_maximum:
        parts.append("-UseMaximumSize")
    elif size_bytes > 0:
        parts.append(f"-Size {size_bytes}")

    if is_efi:
        parts.append("-GptType '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'")
    elif is_msr:
        parts.append("-GptType '{e3c9e316-0b5c-4db8-817d-f92df00215ae}'")

    if drive_letter:
        parts.append(f"-DriveLetter {drive_letter}")
    elif not is_msr:
        parts.append("-AssignDriveLetter")

    partition_cmd = " ".join(parts)

    # MSR partitions cannot be formatted
    if is_msr:
        _, stderr, rc = run_powershell(partition_cmd)
        if rc != 0:
            raise BackupError(f"Failed to create MSR partition: {stderr}")
        return None

    # Create and format in one pipeline
    fmt_label = "EFI" if is_efi else "Data"
    fs_type = "FAT32" if is_efi else fs
    full_cmd = (
        f"{partition_cmd} | Format-Volume -FileSystem {fs_type} "
        f"-NewFileSystemLabel '{fmt_label}' -Confirm:$false -ErrorAction Stop"
    )
    stdout, stderr, rc = run_powershell(full_cmd)
    if rc != 0:
        raise BackupError(f"Failed to create/format partition: {stderr}")

    # Retrieve the drive letter that was assigned
    if drive_letter:
        return drive_letter.upper()

    # Query back the newest partition's letter
    query = (
        f"Get-Partition -DiskNumber {disk_index} | "
        "Sort-Object PartitionNumber | Select-Object -Last 1 | "
        "Select-Object -ExpandProperty DriveLetter"
    )
    out, _, _ = run_powershell(query)
    letter = out.strip().rstrip(":")
    if letter and letter.isalpha():
        return letter.upper()
    return None


# ---------------------------------------------------------------------------
# BackupManager
# ---------------------------------------------------------------------------

class BackupManager:
    """Manages backup, restore, and clone operations."""

    def __init__(self, backup_dir: str = ""):
        self._backup_dir = backup_dir or os.path.join(
            os.path.expanduser("~"), "OneClickBackups"
        )
        self._log = logging.getLogger("OneClickBackup.Backup")
        self._progress_callback: Optional[Callable[[str, float], None]] = None
        self._cancel_event = threading.Event()
        os.makedirs(self._backup_dir, exist_ok=True)

    # -- Properties ---------------------------------------------------------

    @property
    def backup_dir(self) -> str:
        return self._backup_dir

    @backup_dir.setter
    def backup_dir(self, path: str) -> None:
        self._backup_dir = path
        os.makedirs(path, exist_ok=True)

    # -- Progress / cancellation --------------------------------------------

    def set_progress_callback(
        self, callback: Callable[[str, float], None]
    ) -> None:
        """Register *callback(message, percent)* for progress updates."""
        self._progress_callback = callback

    def cancel(self) -> None:
        """Signal the current operation to stop."""
        self._cancel_event.set()

    def _report_progress(self, message: str, percent: float) -> None:
        self._log.info("%s (%.1f%%)", message, percent)
        if self._progress_callback:
            self._progress_callback(message, percent)

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise CancelledError("Operation cancelled by user.")

    def _run_cancellable(
        self,
        cmd: list[str],
        *,
        timeout: int = 7200,
        poll_interval: float = 2.0,
    ) -> subprocess.CompletedProcess:
        """Run *cmd* in a subprocess that can be cancelled via ``_cancel_event``.

        Polls the process every *poll_interval* seconds and sends
        ``terminate()`` if the user cancels.  Falls back to ``kill()``
        if the process does not exit within 10 s of the terminate signal.
        """
        self._log.debug("Running (cancellable): %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    proc.wait(timeout=poll_interval)
                    break  # process finished
                except subprocess.TimeoutExpired:
                    pass
                if self._cancel_event.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise CancelledError("Operation cancelled by user.")
                if time.monotonic() > deadline:
                    proc.kill()
                    raise BackupError(
                        f"Command timed out after {timeout} s: {' '.join(cmd)}"
                    )
        except Exception:
            # Ensure the process is always reaped
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            raise

        stdout = proc.stdout.read() if proc.stdout else ""
        stderr = proc.stderr.read() if proc.stderr else ""
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    # ======================================================================
    # Backup Operations
    # ======================================================================

    def create_partition_backup(
        self,
        disk_index: int,
        partition_index: int,
        name: str = "",
    ) -> BackupInfo:
        """Create a file-level backup of a single partition using *robocopy*.

        The backup is stored as a directory tree under :pyattr:`backup_dir`.
        A JSON metadata sidecar is written alongside it.

        Args:
            disk_index: Physical disk number (0, 1, ...).
            partition_index: Partition number on the disk.
            name: Human-readable label.  Defaults to an auto-generated name.

        Returns:
            A :class:`BackupInfo` describing the completed backup.

        Raises:
            AdminRequiredError: If not running elevated.
            BackupError: On any operational failure.
        """
        _require_admin("Partition backup")
        self._cancel_event.clear()

        # 1. Resolve the drive letter
        self._report_progress("Resolving partition drive letter...", 0.0)
        drive_letter = _get_partition_drive_letter(disk_index, partition_index)
        if not drive_letter:
            raise BackupError(
                f"Partition {partition_index} on disk {disk_index} has no "
                "drive letter assigned.  Assign a letter first."
            )

        source_root = f"{drive_letter}:\\"
        if not os.path.isdir(source_root):
            raise BackupError(f"Source path {source_root} is not accessible.")

        # 2. Create backup directory
        backup_id = self._generate_backup_id()
        if not name:
            name = f"Partition_{drive_letter}_{backup_id}"
        backup_subdir = os.path.join(self._backup_dir, backup_id)
        os.makedirs(backup_subdir, exist_ok=True)

        # 3. Measure source size
        self._report_progress("Calculating source size...", 5.0)
        self._check_cancelled()
        total_size = _dir_size(source_root)

        # 4. Robocopy
        self._report_progress("Copying partition data...", 10.0)
        self._check_cancelled()
        ok = self._run_robocopy(source_root, backup_subdir)
        if not ok:
            raise BackupError(
                f"Robocopy failed while backing up {source_root}."
            )

        # 5. Compute checksum of a manifest file
        self._report_progress("Generating checksum...", 85.0)
        self._check_cancelled()
        manifest_path = self._write_file_manifest(backup_subdir)
        checksum = self._calculate_checksum(manifest_path)

        compressed_size = _dir_size(backup_subdir)

        # 6. Save metadata
        self._report_progress("Saving metadata...", 95.0)
        info = BackupInfo(
            backup_id=backup_id,
            name=name,
            timestamp=datetime.now().isoformat(),
            source_disk=disk_index,
            source_partitions=[partition_index],
            backup_type="partition",
            total_size_bytes=total_size,
            compressed_size_bytes=compressed_size,
            backup_path=backup_subdir,
            checksum=checksum,
            os_version=_get_os_version(),
        )
        self._save_metadata(info)

        self._report_progress("Partition backup complete.", 100.0)
        self._log.info("Backup %s finished: %s", backup_id, backup_subdir)
        return info

    # ------------------------------------------------------------------

    def create_system_backup(
        self, destination_path: str = "", name: str = ""
    ) -> BackupInfo:
        """Backup the Windows system volume (all critical volumes).

        Uses ``wbadmin start backup`` with ``-allCritical`` to capture the
        boot partition, EFI system partition, and the Windows volume.

        Args:
            destination_path: Target folder.  Defaults to :pyattr:`backup_dir`.
            name: Human-readable label.

        Returns:
            A :class:`BackupInfo` for the completed system backup.
        """
        _require_admin("System backup")
        self._cancel_event.clear()

        backup_id = self._generate_backup_id()
        if not name:
            name = f"System_{backup_id}"

        dest = destination_path or self._backup_dir
        os.makedirs(dest, exist_ok=True)

        self._report_progress("Starting system backup with wbadmin...", 5.0)

        # wbadmin requires a target specified as a path. It creates a
        # WindowsImageBackup folder inside it.
        cmd = [
            "wbadmin", "start", "backup",
            f"-backupTarget:{dest}",
            "-include:C:", "-allCritical", "-quiet",
        ]
        result = self._run_cancellable(cmd, timeout=7200)

        if result.returncode != 0:
            self._log.error("wbadmin stderr: %s", result.stderr)
            # Fall back to a robocopy-based system backup of C:\
            self._report_progress(
                "wbadmin failed; falling back to file-level copy of C:\\...", 10.0
            )
            return self._fallback_system_backup(backup_id, name, dest)

        self._report_progress("wbadmin backup completed.", 80.0)

        # Locate the WindowsImageBackup folder wbadmin created
        wib_path = os.path.join(dest, "WindowsImageBackup")
        backup_size = _dir_size(wib_path) if os.path.isdir(wib_path) else 0

        # Calculate C: total size as the "original" measure
        c_root = "C:\\"
        total_size = _dir_size(c_root)

        # Checksum: hash the catalog or first backup file found
        checksum = ""
        if os.path.isdir(wib_path):
            for dirpath, _dirs, files in os.walk(wib_path):
                for f in files:
                    fp = os.path.join(dirpath, f)
                    checksum = self._calculate_checksum(fp)
                    break
                if checksum:
                    break

        info = BackupInfo(
            backup_id=backup_id,
            name=name,
            timestamp=datetime.now().isoformat(),
            source_disk=0,
            source_partitions=[],
            backup_type="system",
            total_size_bytes=total_size,
            compressed_size_bytes=backup_size,
            backup_path=wib_path if os.path.isdir(wib_path) else dest,
            checksum=checksum,
            os_version=_get_os_version(),
        )
        self._save_metadata(info)
        self._report_progress("System backup complete.", 100.0)
        return info

    def _fallback_system_backup(
        self, backup_id: str, name: str, dest: str
    ) -> BackupInfo:
        """File-level backup of C:\\ when wbadmin is unavailable."""
        source = "C:\\"
        backup_subdir = os.path.join(dest, backup_id)
        os.makedirs(backup_subdir, exist_ok=True)

        self._report_progress("Copying system files...", 15.0)
        ok = self._run_robocopy(
            source,
            backup_subdir,
            options=[
                "/E", "/COPYALL", "/R:1", "/W:1",
                "/XD", "System Volume Information", "$Recycle.Bin",
                "/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
                "/NFL", "/NDL", "/NP",
            ],
        )

        total_size = _dir_size(source)
        backup_size = _dir_size(backup_subdir)

        manifest = self._write_file_manifest(backup_subdir)
        checksum = self._calculate_checksum(manifest)

        info = BackupInfo(
            backup_id=backup_id,
            name=name,
            timestamp=datetime.now().isoformat(),
            source_disk=0,
            source_partitions=[],
            backup_type="system",
            total_size_bytes=total_size,
            compressed_size_bytes=backup_size,
            backup_path=backup_subdir,
            checksum=checksum,
            os_version=_get_os_version(),
        )
        self._save_metadata(info)
        self._report_progress("Fallback system backup complete.", 100.0)
        return info

    # ------------------------------------------------------------------

    def create_full_disk_backup(
        self, disk_index: int, name: str = ""
    ) -> BackupInfo:
        """Backup every partition with an assigned drive letter on *disk_index*.

        Iterates through all partitions, copies each one via robocopy into
        a sub-folder named after its drive letter, and produces a unified
        :class:`BackupInfo` for the whole disk.
        """
        _require_admin("Full disk backup")
        self._cancel_event.clear()

        if not _disk_exists(disk_index):
            raise BackupError(f"Disk {disk_index} does not exist.")

        partitions = _get_disk_partitions(disk_index)
        if not partitions:
            raise BackupError(
                f"No partitions found on disk {disk_index}."
            )

        backup_id = self._generate_backup_id()
        if not name:
            name = f"Disk{disk_index}_{backup_id}"
        backup_root = os.path.join(self._backup_dir, backup_id)
        os.makedirs(backup_root, exist_ok=True)

        total_size = 0
        partition_numbers: list[int] = []
        count = len(partitions)

        for idx, part in enumerate(partitions):
            self._check_cancelled()
            letter = part["DriveLetter"]
            pnum = part["PartitionNumber"]

            if not letter:
                self._log.info(
                    "Skipping partition %d (no drive letter)", pnum
                )
                continue

            source = f"{letter}:\\"
            if not os.path.isdir(source):
                self._log.warning("Source %s not accessible, skipping.", source)
                continue

            partition_numbers.append(pnum)
            part_dir = os.path.join(backup_root, f"part_{letter}")
            os.makedirs(part_dir, exist_ok=True)

            pct_start = 5.0 + (85.0 * idx / count)
            self._report_progress(
                f"Copying partition {letter}: ({idx + 1}/{count})...",
                pct_start,
            )

            self._run_robocopy(source, part_dir)
            total_size += _dir_size(source)

        compressed_size = _dir_size(backup_root)
        manifest = self._write_file_manifest(backup_root)
        checksum = self._calculate_checksum(manifest)

        info = BackupInfo(
            backup_id=backup_id,
            name=name,
            timestamp=datetime.now().isoformat(),
            source_disk=disk_index,
            source_partitions=partition_numbers,
            backup_type="full_disk",
            total_size_bytes=total_size,
            compressed_size_bytes=compressed_size,
            backup_path=backup_root,
            checksum=checksum,
            os_version=_get_os_version(),
        )
        self._save_metadata(info)
        self._report_progress("Full disk backup complete.", 100.0)
        return info

    # ------------------------------------------------------------------

    def list_backups(self) -> list[BackupInfo]:
        """Return all backups found in :pyattr:`backup_dir`."""
        results: list[BackupInfo] = []
        if not os.path.isdir(self._backup_dir):
            return results

        for fname in os.listdir(self._backup_dir):
            if fname.endswith("_meta.json"):
                meta_path = os.path.join(self._backup_dir, fname)
                info = self._load_metadata(meta_path)
                if info is not None:
                    results.append(info)

        # Sort newest-first
        results.sort(key=lambda b: b.timestamp, reverse=True)
        return results

    # ------------------------------------------------------------------

    def delete_backup(self, backup_id: str) -> bool:
        """Delete the backup directory and its metadata file.

        Returns *True* if the backup was found and removed.
        """
        meta_path = os.path.join(
            self._backup_dir, f"{backup_id}_meta.json"
        )
        info = self._load_metadata(meta_path)
        if info is None:
            self._log.warning("Backup %s not found.", backup_id)
            return False

        # Remove the backup data directory
        if os.path.isdir(info.backup_path):
            shutil.rmtree(info.backup_path, ignore_errors=True)
            self._log.info("Removed backup data: %s", info.backup_path)

        # Remove the metadata file
        try:
            os.remove(meta_path)
        except OSError:
            pass

        # Remove manifest if present
        manifest_path = os.path.join(
            self._backup_dir, f"{backup_id}_manifest.txt"
        )
        if os.path.isfile(manifest_path):
            try:
                os.remove(manifest_path)
            except OSError:
                pass

        self._log.info("Deleted backup %s.", backup_id)
        return True

    # ------------------------------------------------------------------

    def restore_backup(
        self,
        backup_id: str,
        target_disk: int,
        target_partition: int,
    ) -> bool:
        """Restore a previously created backup to a target partition.

        For *partition* and *full_disk* backups the restore is file-level
        (robocopy mirror).  For *system* backups created with ``wbadmin``,
        the method attempts ``wbadmin start recovery``.

        Args:
            backup_id: The identifier of the backup to restore.
            target_disk: Destination physical disk number.
            target_partition: Destination partition number.

        Returns:
            *True* when the restore completes successfully.
        """
        _require_admin("Backup restore")
        self._cancel_event.clear()

        meta_path = os.path.join(
            self._backup_dir, f"{backup_id}_meta.json"
        )
        info = self._load_metadata(meta_path)
        if info is None:
            raise BackupError(f"Backup {backup_id} not found.")

        if not os.path.isdir(info.backup_path):
            raise BackupError(
                f"Backup data directory missing: {info.backup_path}"
            )

        target_letter = _get_partition_drive_letter(target_disk, target_partition)
        if not target_letter:
            raise BackupError(
                f"Target partition {target_partition} on disk {target_disk} "
                "has no drive letter."
            )

        target_root = f"{target_letter}:\\"

        # --- Restore based on backup type ---

        if info.backup_type == "system" and os.path.basename(info.backup_path) == "WindowsImageBackup":
            return self._restore_system_wbadmin(info, target_letter)

        if info.backup_type == "partition":
            self._report_progress("Restoring partition backup...", 5.0)
            self._check_cancelled()
            ok = self._run_robocopy(
                info.backup_path,
                target_root,
                options=["/MIR", "/COPYALL", "/R:1", "/W:1", "/NFL", "/NDL", "/NP"],
            )
            self._report_progress("Partition restore complete.", 100.0)
            return ok

        if info.backup_type == "full_disk":
            # Restore only the sub-folder that matches the target letter
            part_dir = os.path.join(info.backup_path, f"part_{target_letter}")
            if not os.path.isdir(part_dir):
                # Try restoring the first available sub-folder
                for entry in os.listdir(info.backup_path):
                    candidate = os.path.join(info.backup_path, entry)
                    if os.path.isdir(candidate) and entry.startswith("part_"):
                        part_dir = candidate
                        break
                else:
                    raise BackupError(
                        "No matching partition folder found in full disk backup."
                    )

            self._report_progress("Restoring from full-disk backup...", 5.0)
            self._check_cancelled()
            ok = self._run_robocopy(
                part_dir,
                target_root,
                options=["/MIR", "/COPYALL", "/R:1", "/W:1", "/NFL", "/NDL", "/NP"],
            )
            self._report_progress("Full-disk restore complete.", 100.0)
            return ok

        # Generic fallback: robocopy mirror
        self._report_progress("Restoring backup...", 5.0)
        ok = self._run_robocopy(
            info.backup_path,
            target_root,
            options=["/MIR", "/COPYALL", "/R:1", "/W:1", "/NFL", "/NDL", "/NP"],
        )
        self._report_progress("Restore complete.", 100.0)
        return ok

    def _restore_system_wbadmin(
        self, info: BackupInfo, target_letter: str
    ) -> bool:
        """Attempt a wbadmin-based system recovery."""
        self._report_progress("Restoring system with wbadmin...", 5.0)

        # wbadmin needs the backup location (parent of WindowsImageBackup)
        backup_location = str(Path(info.backup_path).parent)
        cmd = [
            "wbadmin", "start", "recovery",
            f"-version:{info.timestamp[:10].replace('-', '/')}",
            f"-backupTarget:{backup_location}",
            "-itemType:Volume",
            "-items:C:",
            f"-recoveryTarget:{target_letter}:",
            "-quiet",
        ]

        result = self._run_cancellable(cmd, timeout=7200)

        if result.returncode != 0:
            self._log.error("wbadmin recovery failed: %s", result.stderr)
            # Fallback to file-level robocopy restore
            self._report_progress(
                "wbadmin recovery failed; falling back to file-level restore...", 30.0
            )
            wib_subdir = info.backup_path
            target_root = f"{target_letter}:\\"
            ok = self._run_robocopy(
                wib_subdir,
                target_root,
                options=["/MIR", "/COPYALL", "/R:1", "/W:1", "/NFL", "/NDL", "/NP"],
            )
            self._report_progress("File-level restore complete.", 100.0)
            return ok

        self._report_progress("System restore complete.", 100.0)
        return True

    # ------------------------------------------------------------------

    def verify_backup(self, backup_id: str) -> bool:
        """Verify backup integrity by recomputing the file-manifest checksum.

        Returns *True* if the current checksum matches the stored one.
        """
        meta_path = os.path.join(
            self._backup_dir, f"{backup_id}_meta.json"
        )
        info = self._load_metadata(meta_path)
        if info is None:
            raise BackupError(f"Backup {backup_id} not found.")

        if not os.path.isdir(info.backup_path):
            raise BackupError(
                f"Backup data directory missing: {info.backup_path}"
            )

        self._report_progress("Verifying backup integrity...", 10.0)

        manifest_path = self._write_file_manifest(info.backup_path)
        current_checksum = self._calculate_checksum(manifest_path)

        matches = current_checksum == info.checksum
        if matches:
            self._report_progress("Verification passed.", 100.0)
            self._log.info("Backup %s integrity verified.", backup_id)
        else:
            self._report_progress("Verification FAILED.", 100.0)
            self._log.warning(
                "Backup %s checksum mismatch: expected %s, got %s",
                backup_id,
                info.checksum,
                current_checksum,
            )

        return matches

    # ======================================================================
    # Clone Operations
    # ======================================================================

    def clone_disk(
        self,
        source_disk: int,
        target_disk: int,
        resize_to_fit: bool = True,
    ) -> bool:
        """Clone every partition from *source_disk* to *target_disk*.

        This is a destructive operation: the target disk is wiped and
        re-initialised to match the source partition style.

        Steps:
            1. Validate both disks exist and target is large enough.
            2. Clean and initialise target.
            3. Re-create partitions on target.
            4. Copy data for each mounted partition.
            5. If the source is the system disk, fix the BCD on target.

        Args:
            source_disk: Physical disk number to clone from.
            target_disk: Physical disk number to clone to.
            resize_to_fit: When *True* and target is larger, expand the
                last data partition to fill remaining space.

        Returns:
            *True* on success.
        """
        _require_admin("Disk clone")
        self._cancel_event.clear()

        if source_disk == target_disk:
            raise BackupError("Source and target disk must be different.")

        if not _disk_exists(source_disk):
            raise BackupError(f"Source disk {source_disk} does not exist.")
        if not _disk_exists(target_disk):
            raise BackupError(f"Target disk {target_disk} does not exist.")

        source_size = _get_disk_size(source_disk)
        target_size = _get_disk_size(target_disk)

        src_partitions = _get_disk_partitions(source_disk)
        if not src_partitions:
            raise BackupError(f"No partitions on source disk {source_disk}.")

        # Total required space (sum of partition sizes)
        required = sum(p["Size"] for p in src_partitions)
        if target_size < required and not resize_to_fit:
            raise BackupError(
                f"Target disk ({format_bytes(target_size)}) is too small for "
                f"source data ({format_bytes(required)})."
            )

        style = _get_disk_style(source_disk)
        is_system = _is_system_disk(source_disk)

        # 1. Clean target
        self._report_progress("Preparing target disk...", 5.0)
        self._check_cancelled()
        _clean_and_initialize_disk(target_disk, style)

        # 2. Recreate partitions and copy data
        total_parts = len(src_partitions)
        windows_letter_on_target: Optional[str] = None
        efi_letter_on_target: Optional[str] = None

        for idx, part in enumerate(src_partitions):
            self._check_cancelled()
            pnum = part["PartitionNumber"]
            ptype = part["Type"].lower()
            gpt_type = part["GptType"].lower() if part["GptType"] else ""
            letter = part["DriveLetter"]
            size = part["Size"]

            pct = 10.0 + (80.0 * idx / total_parts)
            self._report_progress(
                f"Cloning partition {pnum} ({ptype})...", pct
            )

            # Determine partition characteristics
            is_efi = "efi" in ptype or "c12a7328" in gpt_type
            is_msr = "msr" in ptype or "reserved" in ptype or "e3c9e316" in gpt_type
            is_recovery = "recovery" in ptype

            # Last partition: use remaining space if resize_to_fit
            use_max = resize_to_fit and (idx == total_parts - 1) and not is_efi and not is_msr

            fs = "FAT32" if is_efi else "NTFS"

            try:
                assigned = _create_partition(
                    disk_index=target_disk,
                    size_bytes=0 if use_max else size,
                    fs=fs,
                    is_efi=is_efi,
                    is_msr=is_msr,
                    use_maximum=use_max,
                )
            except BackupError as exc:
                self._log.error(
                    "Failed to create partition %d on target: %s", pnum, exc
                )
                continue

            # Copy data if source has a drive letter and target got one
            if letter and assigned:
                src_root = f"{letter}:\\"
                dst_root = f"{assigned}:\\"
                if os.path.isdir(src_root):
                    self._run_robocopy(
                        src_root,
                        dst_root,
                        options=[
                            "/E", "/COPYALL", "/R:1", "/W:1",
                            "/XD", "System Volume Information", "$Recycle.Bin",
                            "/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
                            "/NFL", "/NDL", "/NP",
                        ],
                    )

                # Track system and EFI letters for boot fixup
                if letter.upper() == "C":
                    windows_letter_on_target = assigned
                if is_efi:
                    efi_letter_on_target = assigned

        # 3. Fix boot config if cloning the system disk
        if is_system and windows_letter_on_target:
            self._report_progress("Fixing boot configuration...", 92.0)
            self._fix_boot_config(
                windows_letter_on_target, efi_letter_on_target
            )

        self._report_progress("Disk clone complete.", 100.0)
        self._log.info(
            "Cloned disk %d -> %d successfully.", source_disk, target_disk
        )
        return True

    # ------------------------------------------------------------------

    def clone_partition(
        self,
        source_disk: int,
        source_partition: int,
        target_disk: int,
        target_partition: int,
    ) -> bool:
        """Clone a single partition's files to another existing partition.

        Both partitions must have assigned drive letters.

        Returns *True* on success.
        """
        _require_admin("Partition clone")
        self._cancel_event.clear()

        src_letter = _get_partition_drive_letter(source_disk, source_partition)
        dst_letter = _get_partition_drive_letter(target_disk, target_partition)

        if not src_letter:
            raise BackupError(
                f"Source partition {source_partition} on disk {source_disk} "
                "has no drive letter."
            )
        if not dst_letter:
            raise BackupError(
                f"Target partition {target_partition} on disk {target_disk} "
                "has no drive letter."
            )

        src_root = f"{src_letter}:\\"
        dst_root = f"{dst_letter}:\\"

        self._report_progress(
            f"Cloning {src_root} -> {dst_root}...", 5.0
        )
        self._check_cancelled()

        ok = self._run_robocopy(
            src_root,
            dst_root,
            options=[
                "/MIR", "/COPYALL", "/R:1", "/W:1",
                "/XD", "System Volume Information", "$Recycle.Bin",
                "/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
                "/NFL", "/NDL", "/NP",
            ],
        )

        self._report_progress("Partition clone complete.", 100.0)
        return ok

    # ------------------------------------------------------------------

    def migrate_os(self, target_disk: int) -> bool:
        """Migrate the running Windows installation to *target_disk*.

        Creates the standard UEFI partition layout (EFI + MSR + Windows)
        on the target, copies the C:\\ volume, and fixes the BCD.

        For MBR/legacy BIOS systems the layout is simplified to a single
        active NTFS boot partition.

        Args:
            target_disk: Physical disk number that will receive the OS.

        Returns:
            *True* on success.
        """
        _require_admin("OS migration")
        self._cancel_event.clear()

        if not _disk_exists(target_disk):
            raise BackupError(f"Target disk {target_disk} does not exist.")

        # Find the current system disk (the one containing C:)
        system_disk = self._find_system_disk()
        if system_disk is None:
            raise BackupError("Could not determine the current system disk.")

        if system_disk == target_disk:
            raise BackupError(
                "Target disk is the current system disk.  "
                "Choose a different disk for migration."
            )

        style = _get_disk_style(system_disk)
        is_uefi = style == "GPT"

        # 1. Clean and initialise target
        self._report_progress("Preparing target disk...", 5.0)
        self._check_cancelled()
        _clean_and_initialize_disk(target_disk, style)

        efi_letter: Optional[str] = None
        windows_letter: Optional[str] = None

        if is_uefi:
            # 2a. UEFI layout: EFI (100 MB) + MSR (16 MB) + Windows (rest)
            self._report_progress("Creating EFI partition...", 10.0)
            efi_letter = _create_partition(
                target_disk,
                size_bytes=100 * 1024 * 1024,
                is_efi=True,
            )

            self._report_progress("Creating MSR partition...", 15.0)
            _create_partition(
                target_disk,
                size_bytes=16 * 1024 * 1024,
                is_msr=True,
            )

            self._report_progress("Creating Windows partition...", 20.0)
            windows_letter = _create_partition(
                target_disk,
                fs="NTFS",
                use_maximum=True,
            )
        else:
            # 2b. MBR layout: single active NTFS partition
            self._report_progress("Creating boot partition...", 10.0)
            windows_letter = _create_partition(
                target_disk,
                fs="NTFS",
                use_maximum=True,
            )
            # Mark as active via diskpart
            if windows_letter:
                run_diskpart([
                    f"select disk {target_disk}",
                    "select partition 1",
                    "active",
                ])

        if not windows_letter:
            raise BackupError(
                "Failed to create Windows partition on target disk."
            )

        # 3. Copy Windows volume
        self._report_progress("Copying Windows files...", 25.0)
        self._check_cancelled()

        src = "C:\\"
        dst = f"{windows_letter}:\\"
        ok = self._run_robocopy(
            src,
            dst,
            options=[
                "/E", "/COPYALL", "/DCOPY:DAT", "/R:1", "/W:1",
                "/XD", "System Volume Information", "$Recycle.Bin",
                "/XF", "pagefile.sys", "hiberfil.sys", "swapfile.sys",
                "/NFL", "/NDL", "/NP",
            ],
        )
        if not ok:
            self._log.warning("robocopy reported errors during OS copy.")

        # 4. Copy EFI files if UEFI
        if is_uefi and efi_letter:
            self._report_progress("Copying EFI boot files...", 85.0)
            self._copy_efi_partition(efi_letter)

        # 5. Fix BCD
        self._report_progress("Fixing boot configuration...", 90.0)
        self._fix_boot_config(windows_letter, efi_letter)

        self._report_progress("OS migration complete.", 100.0)
        self._log.info("OS migrated to disk %d.", target_disk)
        return True

    # ======================================================================
    # WinPE Operations
    # ======================================================================

    def create_winpe_disk(self, target_drive_letter: str) -> bool:
        """Create a WinPE bootable USB disk.

        Attempts to use the Windows ADK ``copype`` + ``MakeWinPEMedia``
        pipeline.  If ADK is not installed, falls back to a basic
        bootable USB created with diskpart + ``bootsect``.

        Args:
            target_drive_letter: Drive letter of the USB drive (e.g. ``"E"``).

        Returns:
            *True* on success.
        """
        _require_admin("WinPE disk creation")
        self._cancel_event.clear()

        target_drive_letter = target_drive_letter.upper().rstrip(":")
        if not target_drive_letter.isalpha() or len(target_drive_letter) != 1:
            raise BackupError(f"Invalid drive letter: {target_drive_letter}")

        prereqs = self.check_winpe_prerequisites()

        if prereqs["adk_installed"] and prereqs["winpe_installed"]:
            return self._create_winpe_adk(target_drive_letter, prereqs["adk_path"])

        self._log.info(
            "ADK/WinPE addon not found.  Using basic bootable USB method."
        )
        return self._create_winpe_basic(target_drive_letter)

    def check_winpe_prerequisites(self) -> dict:
        """Check whether the Windows ADK and WinPE add-on are installed.

        Returns:
            A dict with keys ``adk_installed`` (bool), ``winpe_installed``
            (bool), and ``adk_path`` (str or empty).
        """
        result: dict = {
            "adk_installed": False,
            "winpe_installed": False,
            "adk_path": "",
        }

        # Check registry for ADK install path
        cmd = (
            "Get-ItemProperty -Path "
            "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows Kits\\Installed Roots' "
            "-Name KitsRoot10 -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty KitsRoot10"
        )
        stdout, _, rc = run_powershell(cmd)
        if rc == 0 and stdout.strip():
            adk_root = stdout.strip()
            result["adk_path"] = adk_root
            # ADK is installed if the Assessment and Deployment Kit folder exists
            if os.path.isdir(os.path.join(adk_root, "Assessment and Deployment Kit")):
                result["adk_installed"] = True
            # WinPE addon lives under Windows Preinstallation Environment
            winpe_dir = os.path.join(
                adk_root,
                "Assessment and Deployment Kit",
                "Windows Preinstallation Environment",
            )
            if os.path.isdir(winpe_dir):
                result["winpe_installed"] = True

        return result

    def _create_winpe_adk(
        self, target_letter: str, adk_path: str
    ) -> bool:
        """Build WinPE using the full ADK toolchain."""
        import tempfile

        self._report_progress("Building WinPE image from ADK...", 5.0)

        work_dir = os.path.join(
            tempfile.gettempdir(), "OneClickBackup_WinPE"
        )
        if os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

        # Find the Deployment Tools environment batch
        deploy_env = os.path.join(
            adk_path,
            "Assessment and Deployment Kit",
            "Deployment Tools",
            "DandISetEnv.bat",
        )

        # copype creates a working WinPE directory — it requires the ADK
        # environment so we write a temp .bat that sources it first.
        arch = "amd64"
        self._report_progress("Running copype...", 15.0)
        copype_bat = self._write_adk_bat(deploy_env, f'copype {arch} "{work_dir}"')
        try:
            result = subprocess.run(
                ["cmd", "/c", copype_bat],
                capture_output=True, text=True, timeout=300,
            )
        finally:
            self._safe_unlink(copype_bat)
        if result.returncode != 0:
            raise BackupError(f"copype failed: {result.stderr}")

        # Optionally mount boot.wim and inject custom scripts
        wim_path = os.path.join(work_dir, "media", "sources", "boot.wim")
        mount_dir = os.path.join(work_dir, "mount")

        if os.path.isfile(wim_path):
            self._report_progress("Mounting boot.wim...", 35.0)
            subprocess.run(
                ["dism", "/Mount-Wim", f"/WimFile:{wim_path}",
                 "/Index:1", f"/MountDir:{mount_dir}"],
                capture_output=True, text=True, timeout=300,
            )

            # Copy our tool into the WinPE image
            tool_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            tool_dst = os.path.join(mount_dir, "OneClickBackup")
            if os.path.isdir(tool_src):
                shutil.copytree(tool_src, tool_dst, dirs_exist_ok=True)

            # Create autorun startnet.cmd addition
            startnet = os.path.join(
                mount_dir, "Windows", "System32", "startnet.cmd"
            )
            if os.path.isfile(startnet):
                with open(startnet, "a") as f:
                    f.write("\r\necho OneClickBackup WinPE Environment Ready\r\n")

            self._report_progress("Unmounting boot.wim...", 55.0)
            subprocess.run(
                ["dism", "/Unmount-Wim", f"/MountDir:{mount_dir}", "/Commit"],
                capture_output=True, text=True, timeout=300,
            )

        # Write to USB using MakeWinPEMedia (requires ADK environment)
        self._report_progress("Writing to USB...", 70.0)
        media_bat = self._write_adk_bat(
            deploy_env, f'MakeWinPEMedia /UFD "{work_dir}" {target_letter}:',
        )
        try:
            result = subprocess.run(
                ["cmd", "/c", media_bat],
                capture_output=True, text=True, timeout=600,
            )
        finally:
            self._safe_unlink(media_bat)
        if result.returncode != 0:
            raise BackupError(f"MakeWinPEMedia failed: {result.stderr}")

        # Cleanup
        shutil.rmtree(work_dir, ignore_errors=True)
        self._report_progress("WinPE USB created successfully.", 100.0)
        return True

    def _create_winpe_basic(self, target_letter: str) -> bool:
        """Create a minimal bootable USB without ADK.

        Formats the drive and copies the Windows Recovery Environment
        (WinRE) if available.
        """
        self._report_progress("Formatting USB drive...", 10.0)

        # Find the target disk number via PowerShell
        cmd = (
            f"(Get-Partition -DriveLetter {target_letter}).DiskNumber"
        )
        stdout, stderr, rc = run_powershell(cmd)
        if rc != 0 or not stdout.strip().isdigit():
            raise BackupError(
                f"Cannot determine disk number for drive {target_letter}: "
                f"{stderr}"
            )
        usb_disk = int(stdout.strip())

        # Format with diskpart
        dp_script = [
            f"select disk {usb_disk}",
            "clean",
            "create partition primary",
            "select partition 1",
            "active",
            "format fs=fat32 quick label=WINPE",
            f"assign letter={target_letter}",
        ]
        dp_out, dp_err, dp_rc = run_diskpart(dp_script)
        if dp_rc != 0:
            raise BackupError(f"diskpart failed: {dp_err}")

        self._report_progress("Copying boot files...", 40.0)

        # Try to use bootsect to write MBR/PBR
        bootsect_paths = [
            r"C:\Windows\System32\bootsect.exe",
            r"C:\Windows\Boot\bootsect.exe",
        ]
        for bp in bootsect_paths:
            if os.path.isfile(bp):
                subprocess.run(
                    [bp, "/nt60", f"{target_letter}:", "/mbr"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                break

        # Copy WinRE.wim if it exists
        winre_source = r"C:\Windows\System32\Recovery\Winre.wim"
        target_root = f"{target_letter}:\\"

        if os.path.isfile(winre_source):
            self._report_progress("Copying WinRE image...", 55.0)
            sources_dir = os.path.join(target_root, "sources")
            os.makedirs(sources_dir, exist_ok=True)
            boot_wim = os.path.join(sources_dir, "boot.wim")
            shutil.copy2(winre_source, boot_wim)

        # Copy essential boot files from the Windows installation
        self._report_progress("Copying boot manager...", 75.0)
        boot_files = [
            (r"C:\Windows\Boot\EFI\bootmgfw.efi", os.path.join(target_root, "EFI", "Boot", "bootx64.efi")),
            (r"C:\Windows\Boot\PCAT\bootmgr", os.path.join(target_root, "bootmgr")),
        ]
        for src_file, dst_file in boot_files:
            if os.path.isfile(src_file):
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)

        # Create a minimal BCD store with bcdboot (best effort)
        subprocess.run(
            ["bcdboot", r"C:\Windows", "/s", f"{target_letter}:", "/f", "ALL"],
            capture_output=True, text=True, timeout=60,
        )

        self._report_progress("Basic bootable USB created.", 100.0)
        self._log.info(
            "Basic bootable USB written to %s:.", target_letter
        )
        return True

    # ======================================================================
    # Private helpers
    # ======================================================================

    @staticmethod
    def _write_adk_bat(deploy_env: str, command: str) -> str:
        """Write a temp .bat that sources the ADK environment then runs *command*.

        Returns the path to the temp file (caller must delete after use).
        """
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="ocb_adk_")
        with os.fdopen(fd, "w") as f:
            f.write(f'@call "{deploy_env}"\r\n')
            f.write(f"{command}\r\n")
        return bat_path

    @staticmethod
    def _safe_unlink(path: str) -> None:
        """Delete *path* ignoring errors (cleanup helper)."""
        try:
            os.unlink(path)
        except OSError:
            pass

    def _generate_backup_id(self) -> str:
        """Generate a unique backup identifier (timestamp + random suffix)."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        return f"{ts}_{suffix}"

    def _save_metadata(self, info: BackupInfo) -> None:
        """Persist *info* as a JSON sidecar in :pyattr:`backup_dir`."""
        meta_path = os.path.join(
            self._backup_dir, f"{info.backup_id}_meta.json"
        )
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(asdict(info), f, indent=2)
        self._log.debug("Metadata saved: %s", meta_path)

    def _load_metadata(self, meta_path: str) -> Optional[BackupInfo]:
        """Load a :class:`BackupInfo` from a JSON file, or return *None*."""
        try:
            with open(meta_path, encoding="utf-8") as f:
                data = json.load(f)
            return BackupInfo(**data)
        except (OSError, json.JSONDecodeError, TypeError, KeyError) as exc:
            self._log.debug("Failed to load metadata %s: %s", meta_path, exc)
            return None

    def _calculate_checksum(self, file_path: str) -> str:
        """Return the SHA-256 hex digest of *file_path*."""
        sha = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    sha.update(chunk)
        except OSError:
            return ""
        return sha.hexdigest()

    def _write_file_manifest(self, directory: str) -> str:
        """Write a sorted list of (relative_path, size) to a manifest file.

        The manifest is stored alongside the backup metadata and is used
        for integrity verification.

        Returns the absolute path of the manifest file.
        """
        entries: list[str] = []
        for dirpath, _dirs, files in os.walk(directory):
            for fname in files:
                fp = os.path.join(dirpath, fname)
                rel = os.path.relpath(fp, directory)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    size = 0
                entries.append(f"{rel}|{size}")

        entries.sort()

        # Derive the backup_id from the directory name
        dir_name = os.path.basename(directory)
        manifest_path = os.path.join(
            self._backup_dir, f"{dir_name}_manifest.txt"
        )
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("\n".join(entries))

        return manifest_path

    def _run_robocopy(
        self, source: str, target: str, options: Optional[list[str]] = None
    ) -> bool:
        """Run robocopy with configurable flags.

        Default flags perform a full copy with minimal retries.
        Robocopy exit codes 0-7 indicate success; 8+ indicate errors.

        Returns *True* on success.
        """
        cmd = ["robocopy", source, target]
        if options:
            cmd.extend(options)
        else:
            cmd.extend([
                "/E", "/COPYALL", "/R:1", "/W:1",
                "/NFL", "/NDL", "/NP",
            ])

        self._log.debug("Running: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=14400,  # 4-hour timeout for very large copies
            )
        except subprocess.TimeoutExpired:
            self._log.error("robocopy timed out.")
            return False
        except FileNotFoundError:
            self._log.error("robocopy executable not found.")
            return False

        # Exit codes 0-7 are success (each bit means "files copied",
        # "extra files", "mismatched", etc.)
        success = result.returncode < 8
        if not success:
            self._log.error(
                "robocopy failed (exit %d): %s",
                result.returncode,
                result.stderr or result.stdout,
            )
        return success

    def _find_system_disk(self) -> Optional[int]:
        """Return the disk number that holds the C:\\ partition."""
        cmd = (
            "Get-Partition -DriveLetter C -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty DiskNumber"
        )
        stdout, _, rc = run_powershell(cmd)
        if rc == 0 and stdout.strip().isdigit():
            return int(stdout.strip())
        return None

    def _fix_boot_config(
        self,
        windows_letter: str,
        efi_letter: Optional[str] = None,
    ) -> None:
        """Run ``bcdboot`` to install boot files on the target disk.

        For UEFI systems with an EFI partition, ``/f UEFI`` is used.
        For MBR systems or when no EFI letter is provided, ``/f BIOS``
        or ``/f ALL`` is used.

        Args:
            windows_letter: Drive letter of the copied Windows partition.
            efi_letter: Drive letter of the EFI system partition (if any).
        """
        windows_dir = f"{windows_letter}:\\Windows"
        if not os.path.isdir(windows_dir):
            self._log.warning(
                "Windows directory not found at %s; skipping BCD fix.",
                windows_dir,
            )
            return

        if efi_letter:
            cmd = ["bcdboot", windows_dir, "/s", f"{efi_letter}:", "/f", "UEFI"]
        else:
            cmd = ["bcdboot", windows_dir, "/f", "ALL"]

        self._log.info("Fixing boot config: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            self._log.error(
                "bcdboot failed (exit %d): %s",
                result.returncode,
                result.stderr,
            )
            raise BackupError(f"bcdboot failed: {result.stderr}")

        self._log.info("Boot configuration updated successfully.")

    def _copy_efi_partition(self, target_efi_letter: str) -> None:
        """Copy the current system EFI partition contents to the target.

        Temporarily mounts the source EFI partition to a free letter,
        copies via robocopy, then removes the letter.
        """
        # Find the source EFI partition
        cmd = (
            "Get-Partition | Where-Object { $_.GptType -eq "
            "'{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -and $_.DriveLetter -eq '' } | "
            "Select-Object DiskNumber, PartitionNumber | "
            "ConvertTo-Json -Compress"
        )
        stdout, _, rc = run_powershell(cmd)
        if rc != 0 or not stdout.strip():
            self._log.info("No unmounted EFI partition found; using bcdboot only.")
            return

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return

        if isinstance(data, list):
            data = data[0]  # Use the first EFI partition

        src_disk = data.get("DiskNumber")
        src_part = data.get("PartitionNumber")
        if src_disk is None or src_part is None:
            return

        # Pick a temporary letter
        temp_letter = self._find_free_drive_letter()
        if not temp_letter:
            self._log.warning("No free drive letter for EFI mount.")
            return

        # Assign letter
        assign_cmd = (
            f"Get-Partition -DiskNumber {src_disk} -PartitionNumber {src_part} | "
            f"Set-Partition -NewDriveLetter {temp_letter}"
        )
        run_powershell(assign_cmd)

        try:
            src_root = f"{temp_letter}:\\"
            dst_root = f"{target_efi_letter}:\\"
            if os.path.isdir(src_root):
                self._run_robocopy(
                    src_root,
                    dst_root,
                    options=["/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NP"],
                )
        finally:
            # Remove the temporary letter
            remove_cmd = (
                f"Get-Partition -DiskNumber {src_disk} -PartitionNumber {src_part} | "
                f"Remove-PartitionAccessPath -AccessPath '{temp_letter}:\\'"
            )
            run_powershell(remove_cmd)

    def _find_free_drive_letter(self) -> Optional[str]:
        """Return an unused drive letter (Z down to D)."""
        cmd = "Get-PSDrive -PSProvider FileSystem | Select-Object -ExpandProperty Name"
        stdout, _, rc = run_powershell(cmd)
        used = set()
        if rc == 0:
            for line in stdout.splitlines():
                ch = line.strip().upper()
                if len(ch) == 1 and ch.isalpha():
                    used.add(ch)

        for code in range(ord("Z"), ord("D") - 1, -1):
            letter = chr(code)
            if letter not in used:
                return letter

        return None
