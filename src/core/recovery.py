"""Partition recovery scanner for lost or deleted partitions.

Scans physical disks for filesystem signatures (NTFS, FAT32, EFI/GPT)
in unallocated regions and optionally recreates discovered partitions
via diskpart.

All scan operations require administrator privileges because they open
raw disk handles (``\\\\.\\PhysicalDriveN``).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from src.utils.admin import is_admin
from src.utils.helpers import run_diskpart, run_powershell


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECTOR_SIZE: int = 512

# Filesystem magic signatures
_NTFS_MAGIC: bytes = b"NTFS    "       # offset 3 in the boot sector
_NTFS_MAGIC_OFFSET: int = 3

_FAT32_MAGIC: bytes = b"FAT32"         # offset 82 in the boot sector
_FAT32_MAGIC_OFFSET: int = 82

_EFI_SIGNATURE: bytes = b"EFI PART"    # GPT header at LBA 1, offset 0
_EFI_SIGNATURE_OFFSET: int = 0

# Chunk size for deep-scan disk reads (1 MiB)
_DEEP_SCAN_CHUNK_SECTORS: int = 2048


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RecoveryError(Exception):
    """Raised when a recovery operation fails."""


class AdminRequiredError(RecoveryError):
    """Raised when the current process lacks administrator privileges."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RecoveredPartition:
    """A partition discovered during a scan.

    Attributes:
        start_sector:   First sector of the detected filesystem header.
        end_sector:     Estimated last sector (based on FS metadata or region size).
        size_bytes:     Estimated partition size in bytes.
        file_system:    Detected filesystem type (``"NTFS"``, ``"FAT32"``, ``"EFI"``).
        status:         Recovery assessment (``"recoverable"``, ``"partial"``, ``"damaged"``).
        confidence:     Confidence score from 0.0 (low) to 1.0 (high).
        boot_signature: Whether a valid 0x55AA boot signature was found.
    """

    start_sector: int
    end_sector: int
    size_bytes: int
    file_system: str
    status: str
    confidence: float
    boot_signature: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_admin(operation: str = "This operation") -> None:
    """Raise :exc:`AdminRequiredError` unless the process is elevated."""
    if not is_admin():
        raise AdminRequiredError(
            f"{operation} requires administrator privileges. "
            "Please re-run the application as administrator."
        )


def _disk_exists(disk_index: int) -> bool:
    """Return *True* if the given physical disk number is present."""
    cmd = (
        f"Get-Disk -Number {disk_index} -ErrorAction SilentlyContinue | "
        "Measure-Object | Select-Object -ExpandProperty Count"
    )
    stdout, _, rc = run_powershell(cmd)
    return rc == 0 and stdout.strip() == "1"


def _get_disk_size_bytes(disk_index: int) -> int:
    """Return total disk size in bytes (0 on failure)."""
    cmd = f"(Get-Disk -Number {disk_index}).Size"
    stdout, _, rc = run_powershell(cmd)
    if rc == 0 and stdout.strip().isdigit():
        return int(stdout.strip())
    return 0


def _get_unallocated_regions(disk_index: int) -> list[dict]:
    """Return a list of unallocated regions on the disk.

    Each dict contains ``start_bytes`` and ``size_bytes``.

    The approach: query all partitions (with offsets and sizes), then compute
    the gaps between them.
    """
    cmd = (
        f"Get-Partition -DiskNumber {disk_index} -ErrorAction SilentlyContinue | "
        "Select-Object PartitionNumber, Offset, Size | "
        "Sort-Object Offset | ConvertTo-Json -Compress"
    )
    stdout, _, rc = run_powershell(cmd)
    disk_size = _get_disk_size_bytes(disk_index)
    if disk_size == 0:
        return []

    partitions: list[dict] = []
    if rc == 0 and stdout.strip():
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                data = [data]
            for p in data:
                partitions.append({
                    "offset": int(p.get("Offset", 0)),
                    "size": int(p.get("Size", 0)),
                })
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Sort partitions by offset
    partitions.sort(key=lambda p: p["offset"])

    regions: list[dict] = []
    cursor = 0

    # Skip the first 1 MiB — reserved for MBR/GPT headers
    min_start = 1024 * 1024

    for part in partitions:
        gap_start = max(cursor, min_start)
        gap_end = part["offset"]
        if gap_end > gap_start:
            regions.append({
                "start_bytes": gap_start,
                "size_bytes": gap_end - gap_start,
            })
        cursor = part["offset"] + part["size"]

    # Trailing unallocated space after the last partition
    if cursor < disk_size:
        gap_start = max(cursor, min_start)
        if disk_size > gap_start:
            regions.append({
                "start_bytes": gap_start,
                "size_bytes": disk_size - gap_start,
            })

    return regions


def _read_sectors_ps(
    disk_index: int,
    offset_bytes: int,
    count: int = 1,
) -> bytes | None:
    """Read *count* sectors from a physical disk starting at *offset_bytes*.

    Uses a PowerShell script that opens ``\\\\.\\PhysicalDriveN`` and reads
    raw bytes, returning them as a Base64-encoded string.

    Returns *None* on any failure.
    """
    byte_count = count * _SECTOR_SIZE
    ps_script = (
        f"$path = '\\\\.\\PhysicalDrive{disk_index}';"
        "$fs = [System.IO.File]::Open($path, "
        "[System.IO.FileMode]::Open, "
        "[System.IO.FileAccess]::Read, "
        "[System.IO.FileShare]::ReadWrite);"
        f"$fs.Seek({offset_bytes}, [System.IO.SeekOrigin]::Begin) | Out-Null;"
        f"$buf = New-Object byte[] {byte_count};"
        "$read = $fs.Read($buf, 0, $buf.Length);"
        "$fs.Close();"
        "if ($read -gt 0) { [Convert]::ToBase64String($buf[0..($read-1)]) } "
        "else { '' }"
    )
    stdout, stderr, rc = run_powershell(ps_script)
    if rc != 0 or not stdout.strip():
        return None

    import base64
    try:
        return base64.b64decode(stdout.strip())
    except Exception:
        return None


def _check_boot_signature(sector_data: bytes) -> bool:
    """Return *True* if the last two bytes of the sector are 0x55AA."""
    if len(sector_data) < _SECTOR_SIZE:
        return False
    return sector_data[510] == 0x55 and sector_data[511] == 0xAA


def _identify_filesystem(sector_data: bytes) -> tuple[str, float] | None:
    """Attempt to identify a filesystem from boot sector data.

    Returns a ``(filesystem_type, confidence)`` tuple or *None*.
    """
    if len(sector_data) < _SECTOR_SIZE:
        return None

    has_boot_sig = _check_boot_signature(sector_data)

    # NTFS: "NTFS    " at offset 3
    if (
        len(sector_data) > _NTFS_MAGIC_OFFSET + len(_NTFS_MAGIC)
        and sector_data[_NTFS_MAGIC_OFFSET : _NTFS_MAGIC_OFFSET + len(_NTFS_MAGIC)]
        == _NTFS_MAGIC
    ):
        confidence = 0.95 if has_boot_sig else 0.70
        return ("NTFS", confidence)

    # FAT32: "FAT32" at offset 82
    if (
        len(sector_data) > _FAT32_MAGIC_OFFSET + len(_FAT32_MAGIC)
        and sector_data[_FAT32_MAGIC_OFFSET : _FAT32_MAGIC_OFFSET + len(_FAT32_MAGIC)]
        == _FAT32_MAGIC
    ):
        confidence = 0.90 if has_boot_sig else 0.65
        return ("FAT32", confidence)

    # EFI/GPT: "EFI PART" at the beginning of LBA 1
    if (
        len(sector_data) > _EFI_SIGNATURE_OFFSET + len(_EFI_SIGNATURE)
        and sector_data[_EFI_SIGNATURE_OFFSET : _EFI_SIGNATURE_OFFSET + len(_EFI_SIGNATURE)]
        == _EFI_SIGNATURE
    ):
        return ("EFI", 0.98)

    return None


def _estimate_partition_size(
    sector_data: bytes,
    file_system: str,
    region_size_bytes: int,
) -> int:
    """Try to extract the partition size from filesystem metadata.

    Falls back to *region_size_bytes* when the metadata is unreadable.
    """
    try:
        if file_system == "NTFS" and len(sector_data) >= 80:
            # NTFS BPB: bytes-per-sector at offset 11 (2 bytes, LE)
            # sectors-per-cluster at offset 13 (1 byte)
            # total-sectors at offset 40 (8 bytes, LE)
            bps = int.from_bytes(sector_data[11:13], "little")
            total_sectors = int.from_bytes(sector_data[40:48], "little")
            if bps > 0 and total_sectors > 0:
                return bps * total_sectors

        if file_system == "FAT32" and len(sector_data) >= 40:
            # FAT32 BPB: total-sectors-32 at offset 32 (4 bytes, LE)
            bps = int.from_bytes(sector_data[11:13], "little")
            total_sectors = int.from_bytes(sector_data[32:36], "little")
            if bps > 0 and total_sectors > 0:
                return bps * total_sectors
    except Exception:
        pass

    return region_size_bytes


def _assess_status(confidence: float, boot_sig: bool) -> str:
    """Map a confidence score and boot-signature presence to a status label."""
    if confidence >= 0.85 and boot_sig:
        return "recoverable"
    if confidence >= 0.60:
        return "partial"
    return "damaged"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class PartitionRecovery:
    """Scanner for lost / deleted partitions on a physical disk.

    Provides quick and deep scanning modes plus a recovery method that
    recreates a discovered partition via diskpart.

    Example::

        recovery = PartitionRecovery()
        found = recovery.quick_scan(1, callback=lambda pct, msg: print(f"{pct:.0f}% {msg}"))
        for p in found:
            print(p)
    """

    def __init__(self) -> None:
        self._log = logging.getLogger("OneClickBackup.Recovery")
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Request cancellation of the running scan."""
        self._cancel_event.set()

    def _check_cancelled(self) -> None:
        """Raise :exc:`RecoveryError` if cancellation has been requested."""
        if self._cancel_event.is_set():
            raise RecoveryError("Operation cancelled by user.")

    # ------------------------------------------------------------------
    # Quick scan
    # ------------------------------------------------------------------

    def quick_scan(
        self,
        disk_index: int,
        callback: Callable[[float, str], None] | None = None,
    ) -> list[RecoveredPartition]:
        """Scan unallocated regions for filesystem signatures.

        Reads the first sector(s) of each unallocated region and checks
        for known filesystem magic bytes.

        Args:
            disk_index: Physical disk number (``\\\\.\\PhysicalDriveN``).
            callback:   Optional ``callback(percent, message)`` for progress.

        Returns:
            List of :class:`RecoveredPartition` objects found.
        """
        _require_admin("Partition recovery scan")
        self._cancel_event.clear()

        if not isinstance(disk_index, int) or disk_index < 0:
            raise RecoveryError(f"Invalid disk index: {disk_index!r}")
        if not _disk_exists(disk_index):
            raise RecoveryError(f"Disk {disk_index} does not exist.")

        self._log.info("Starting quick scan on disk %d", disk_index)
        if callback:
            callback(0.0, f"Scanning disk {disk_index} for unallocated regions...")

        regions = _get_unallocated_regions(disk_index)
        if not regions:
            self._log.info("No unallocated regions found on disk %d", disk_index)
            if callback:
                callback(100.0, "No unallocated regions found.")
            return []

        self._log.info(
            "Found %d unallocated region(s) on disk %d", len(regions), disk_index
        )

        results: list[RecoveredPartition] = []
        total = len(regions)

        for idx, region in enumerate(regions):
            self._check_cancelled()
            pct = ((idx + 1) / total) * 100.0
            start = region["start_bytes"]
            size = region["size_bytes"]

            if callback:
                callback(pct * 0.9, f"Checking region at offset {start}...")

            # Read the first sector of the region
            sector_data = _read_sectors_ps(disk_index, start, count=1)
            if sector_data is None:
                self._log.debug("Could not read sector at offset %d", start)
                continue

            fs_result = _identify_filesystem(sector_data)
            if fs_result is None:
                # Also check the second sector (LBA 1 relative to region)
                # for a GPT header
                sector_data_lba1 = _read_sectors_ps(
                    disk_index, start + _SECTOR_SIZE, count=1
                )
                if sector_data_lba1 is not None:
                    fs_result = _identify_filesystem(sector_data_lba1)

            if fs_result is None:
                continue

            fs_type, confidence = fs_result
            boot_sig = _check_boot_signature(sector_data)
            est_size = _estimate_partition_size(sector_data, fs_type, size)
            start_sector = start // _SECTOR_SIZE
            end_sector = start_sector + (est_size // _SECTOR_SIZE) - 1

            rp = RecoveredPartition(
                start_sector=start_sector,
                end_sector=end_sector,
                size_bytes=est_size,
                file_system=fs_type,
                status=_assess_status(confidence, boot_sig),
                confidence=confidence,
                boot_signature=boot_sig,
            )
            results.append(rp)
            self._log.info(
                "Quick scan found %s partition at sector %d (%.2f confidence)",
                fs_type, start_sector, confidence,
            )

        if callback:
            callback(100.0, f"Quick scan complete. Found {len(results)} partition(s).")

        self._log.info(
            "Quick scan on disk %d complete: %d partition(s) found",
            disk_index, len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Deep scan
    # ------------------------------------------------------------------

    def deep_scan(
        self,
        disk_index: int,
        callback: Callable[[float, str], None] | None = None,
    ) -> list[RecoveredPartition]:
        """Scan the entire disk surface for filesystem headers.

        Reads the disk in chunks and checks every sector boundary for
        known filesystem signatures.  This is much slower than
        :meth:`quick_scan` but can find partitions even when the
        partition table is completely destroyed.

        Args:
            disk_index: Physical disk number (``\\\\.\\PhysicalDriveN``).
            callback:   Optional ``callback(percent, message)`` for progress.

        Returns:
            List of :class:`RecoveredPartition` objects found.
        """
        _require_admin("Deep partition scan")
        self._cancel_event.clear()

        if not isinstance(disk_index, int) or disk_index < 0:
            raise RecoveryError(f"Invalid disk index: {disk_index!r}")
        if not _disk_exists(disk_index):
            raise RecoveryError(f"Disk {disk_index} does not exist.")

        disk_size = _get_disk_size_bytes(disk_index)
        if disk_size == 0:
            raise RecoveryError(f"Could not determine size of disk {disk_index}.")

        self._log.info(
            "Starting deep scan on disk %d (%d bytes)", disk_index, disk_size
        )
        if callback:
            callback(0.0, f"Deep scanning disk {disk_index}...")

        results: list[RecoveredPartition] = []
        chunk_bytes = _DEEP_SCAN_CHUNK_SECTORS * _SECTOR_SIZE
        total_chunks = max(1, disk_size // chunk_bytes)
        seen_offsets: set[int] = set()
        offset = 0

        chunk_index = 0
        while offset < disk_size:
            self._check_cancelled()

            pct = min((chunk_index / total_chunks) * 100.0, 99.9)
            if callback and chunk_index % 50 == 0:
                mb_scanned = offset / (1024 * 1024)
                mb_total = disk_size / (1024 * 1024)
                callback(
                    pct,
                    f"Scanning... {mb_scanned:,.0f} / {mb_total:,.0f} MiB",
                )

            data = _read_sectors_ps(disk_index, offset, count=_DEEP_SCAN_CHUNK_SECTORS)
            if data is None:
                offset += chunk_bytes
                chunk_index += 1
                continue

            # Walk through each sector boundary in the chunk
            for sector_offset in range(0, len(data) - _SECTOR_SIZE + 1, _SECTOR_SIZE):
                sector_data = data[sector_offset : sector_offset + _SECTOR_SIZE]
                abs_offset = offset + sector_offset

                if abs_offset in seen_offsets:
                    continue

                fs_result = _identify_filesystem(sector_data)
                if fs_result is None:
                    continue

                fs_type, confidence = fs_result
                boot_sig = _check_boot_signature(sector_data)

                # Estimate the remaining region from this point to end of disk
                remaining = disk_size - abs_offset
                est_size = _estimate_partition_size(sector_data, fs_type, remaining)
                start_sector = abs_offset // _SECTOR_SIZE
                end_sector = start_sector + (est_size // _SECTOR_SIZE) - 1

                rp = RecoveredPartition(
                    start_sector=start_sector,
                    end_sector=end_sector,
                    size_bytes=est_size,
                    file_system=fs_type,
                    status=_assess_status(confidence, boot_sig),
                    confidence=confidence,
                    boot_signature=boot_sig,
                )
                results.append(rp)
                seen_offsets.add(abs_offset)
                self._log.info(
                    "Deep scan found %s at sector %d (%.2f confidence)",
                    fs_type, start_sector, confidence,
                )

            offset += chunk_bytes
            chunk_index += 1

        if callback:
            callback(100.0, f"Deep scan complete. Found {len(results)} partition(s).")

        self._log.info(
            "Deep scan on disk %d complete: %d partition(s) found",
            disk_index, len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def recover_partition(
        self,
        disk_index: int,
        partition: RecoveredPartition,
    ) -> bool:
        """Recreate a partition at the discovered offset and size.

        Uses diskpart to create a primary partition at the exact offset
        where the filesystem header was found.

        .. warning::

            This is a destructive operation on the partition table.
            Only use on unallocated space.

        Args:
            disk_index: Physical disk number.
            partition:  A :class:`RecoveredPartition` returned by a scan.

        Returns:
            *True* on success.
        """
        _require_admin("Partition recovery")

        if not isinstance(disk_index, int) or disk_index < 0:
            raise RecoveryError(f"Invalid disk index: {disk_index!r}")
        if not _disk_exists(disk_index):
            raise RecoveryError(f"Disk {disk_index} does not exist.")

        if partition.status == "damaged":
            self._log.warning(
                "Attempting to recover a partition marked as 'damaged' "
                "(confidence=%.2f). Results may be unreliable.",
                partition.confidence,
            )

        size_mb = max(1, partition.size_bytes // (1024 * 1024))
        offset_kb = (partition.start_sector * _SECTOR_SIZE) // 1024

        self._log.info(
            "Recovering %s partition on disk %d: offset=%d KB, size=%d MB",
            partition.file_system, disk_index, offset_kb, size_mb,
        )

        script_lines = [
            f"select disk {disk_index}",
            f"create partition primary size={size_mb} offset={offset_kb}",
        ]

        # Assign a drive letter so the recovered partition is accessible
        script_lines.append("assign")

        stdout, stderr, rc = run_diskpart(script_lines)

        if rc != 0:
            err = stderr if stderr else stdout
            self._log.error("diskpart failed during recovery: %s", err)
            raise RecoveryError(f"Failed to recreate partition: {err}")

        lower_out = stdout.lower()
        if "virtual disk service error" in lower_out:
            self._log.error("diskpart error in output: %s", stdout)
            raise RecoveryError(f"diskpart error: {stdout}")

        self._log.info(
            "Successfully recovered %s partition on disk %d at sector %d",
            partition.file_system, disk_index, partition.start_sector,
        )
        return True
