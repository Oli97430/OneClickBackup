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
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.utils.admin import is_admin
from src.utils.helpers import run_powershell


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

# Avoid circular imports: the mixins import data classes and helpers from
# this module, so we import them late (after the module-level symbols they
# need are defined).
from src.core.clone import CloneMixin
from src.core.winpe import WinPEMixin


class BackupManager(CloneMixin, WinPEMixin):
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
    # Private helpers
    # ======================================================================

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
