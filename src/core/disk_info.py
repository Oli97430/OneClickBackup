"""
Comprehensive disk and partition information gathering for Windows.

Uses WMI, psutil, and PowerShell commands to collect detailed information
about physical disks, partitions, volumes, and their health status.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import psutil

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_disk_cache: list[DiskInfo] | None = None
_cache_timestamp: float = 0.0
_CACHE_TTL_SECONDS: float = 5.0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PartitionInfo:
    """Represents a single partition on a physical disk."""

    index: int = 0                  # Partition number
    letter: str = ""                # Drive letter (e.g. "C") or ""
    label: str = ""                 # Volume label
    file_system: str = ""           # NTFS, FAT32, exFAT, etc.
    size_bytes: int = 0             # Total size in bytes
    used_bytes: int = 0             # Used space in bytes
    free_bytes: int = 0             # Free space in bytes
    partition_type: str = ""        # Primary, Logical, EFI, Recovery, etc.
    is_active: bool = False
    is_boot: bool = False
    is_system: bool = False
    offset_bytes: int = 0           # Byte offset on the physical disk


@dataclass
class DiskInfo:
    """Represents a physical disk and all its partitions."""

    index: int = 0                  # Disk number (0, 1, 2 ...)
    model: str = ""                 # Disk model name
    serial: str = ""                # Serial number
    size_bytes: int = 0             # Total disk size in bytes
    media_type: str = "Unknown"     # SSD, HDD, Unknown
    interface_type: str = "Unknown" # SATA, NVMe, USB, etc.
    partition_style: str = "Unknown"  # MBR, GPT, RAW
    is_system_disk: bool = False    # True if disk contains Windows
    health_status: str = "Unknown"  # Healthy, Warning, etc.
    partitions: list[PartitionInfo] = field(default_factory=list)
    unallocated_bytes: int = 0      # Unallocated space on the disk
    is_4k_aligned: bool = True      # Whether all partitions are 4K-aligned


# ---------------------------------------------------------------------------
# PowerShell helper
# ---------------------------------------------------------------------------


def _run_powershell(command: str, timeout: int = 30) -> str | None:
    """Run a PowerShell command and return its stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        if result.stderr.strip():
            logger.debug("PowerShell stderr: %s", result.stderr.strip())
        return None
    except subprocess.TimeoutExpired:
        logger.warning("PowerShell command timed out: %s", command[:120])
        return None
    except FileNotFoundError:
        logger.warning("PowerShell executable not found")
        return None
    except Exception as exc:
        logger.debug("PowerShell error: %s", exc)
        return None


def _run_powershell_json(command: str, timeout: int = 30) -> Any:
    """Run a PowerShell command that outputs JSON and parse the result."""
    raw = _run_powershell(command, timeout=timeout)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Failed to parse PowerShell JSON output: %s", raw[:200])
        return None


# ---------------------------------------------------------------------------
# WMI helpers
# ---------------------------------------------------------------------------


def _get_wmi_connection() -> Any:
    """Return a WMI COM connection, or None if unavailable."""
    try:
        import wmi  # type: ignore[import-untyped]
        return wmi.WMI()
    except ImportError:
        logger.debug("wmi package not installed; falling back to PowerShell")
        return None
    except Exception as exc:
        logger.debug("Failed to connect to WMI: %s", exc)
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    """Convert a value to int, returning *default* on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    """Convert a value to a stripped string, returning *default* on failure."""
    if value is None:
        return default
    try:
        return str(value).strip()
    except (ValueError, TypeError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    """Convert a value to bool."""
    if value is None:
        return default
    try:
        return bool(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Media type / health / alignment helpers
# ---------------------------------------------------------------------------


def detect_media_type(disk_index: int) -> str:
    """Detect whether a disk is SSD or HDD using PowerShell Get-PhysicalDisk.

    Returns one of "SSD", "HDD", or "Unknown".
    """
    cmd = (
        f"Get-PhysicalDisk | Where-Object {{ $_.DeviceId -eq '{disk_index}' }} "
        f"| Select-Object -Property MediaType | ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return "Unknown"

    # PowerShell may return a single object or a list
    if isinstance(data, list):
        data = data[0] if data else {}

    media = _safe_str(data.get("MediaType", ""), "Unknown")
    if not media or media == "Unspecified":
        return "Unknown"
    # Normalise common values
    media_lower = media.lower()
    if "ssd" in media_lower or "solid" in media_lower:
        return "SSD"
    if "hdd" in media_lower or "hard" in media_lower or "spinning" in media_lower:
        return "HDD"
    return media  # Return as-is if it's something else (e.g. SCM)


def check_4k_alignment(disk_index: int) -> bool:
    """Check whether all partitions on *disk_index* are 4K-aligned.

    A partition is 4K-aligned when its byte offset is a multiple of 4096.
    Returns True if all partitions are aligned (or the disk has none).
    """
    cmd = (
        f"Get-Partition -DiskNumber {disk_index} "
        f"| Select-Object -Property PartitionNumber, Offset "
        f"| ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        # Fallback: assume aligned if we cannot determine
        return True

    if isinstance(data, dict):
        data = [data]

    for part in data:
        offset = _safe_int(part.get("Offset", 0))
        if offset % 4096 != 0:
            return False
    return True


def get_disk_health(disk_index: int) -> str:
    """Retrieve the S.M.A.R.T. health status for *disk_index* via WMI.

    Falls back to PowerShell Get-PhysicalDisk if WMI is unavailable.
    Returns "Healthy", "Warning", "Unhealthy", or "Unknown".
    """
    # Attempt WMI first
    try:
        import wmi  # type: ignore[import-untyped]
        c = wmi.WMI()
        for item in c.Win32_DiskDrive():
            if _safe_int(item.Index) == disk_index:
                status = _safe_str(item.Status, "Unknown")
                if status.lower() == "ok":
                    return "Healthy"
                return status
    except Exception as exc:
        logger.debug("WMI health query failed for disk %d: %s", disk_index, exc)

    # Fallback: PowerShell Get-PhysicalDisk
    cmd = (
        f"Get-PhysicalDisk | Where-Object {{ $_.DeviceId -eq '{disk_index}' }} "
        f"| Select-Object -Property HealthStatus | ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return "Unknown"
    if isinstance(data, list):
        data = data[0] if data else {}
    return _safe_str(data.get("HealthStatus", "Unknown"), "Unknown")


# ---------------------------------------------------------------------------
# Internal: build partition list from WMI
# ---------------------------------------------------------------------------


def _build_partitions_wmi(
    wmi_conn: Any,
    disk_device_id: str,
) -> list[PartitionInfo]:
    """Build a list of PartitionInfo for a disk using WMI associations."""
    partitions: list[PartitionInfo] = []

    try:
        # Map: WMI partition DeviceID -> PartitionInfo (partially filled)
        part_map: dict[str, PartitionInfo] = {}

        # 1. Walk Win32_DiskDriveToDiskPartition to find partitions on this disk
        for assoc in wmi_conn.Win32_DiskDriveToDiskPartition():
            if _safe_str(assoc.Antecedent.DeviceID) != disk_device_id:
                continue
            dep = assoc.Dependent
            pinfo = PartitionInfo(
                index=_safe_int(dep.Index),
                size_bytes=_safe_int(dep.Size),
                partition_type=_safe_str(dep.Type, "Unknown"),
                is_boot=_safe_bool(dep.BootPartition),
                offset_bytes=_safe_int(dep.StartingOffset),
            )
            part_map[_safe_str(dep.DeviceID)] = pinfo

        # 2. Walk Win32_LogicalDiskToPartition to attach drive letters / volumes
        for assoc in wmi_conn.Win32_LogicalDiskToPartition():
            part_id = _safe_str(assoc.Antecedent.DeviceID)
            # Antecedent is the partition, Dependent is the logical disk
            # — but the association naming is reversed in WMI:
            #   Antecedent = Win32_DiskPartition
            #   Dependent  = Win32_LogicalDisk
            # Actually in Win32_LogicalDiskToPartition:
            #   Antecedent = Win32_DiskPartition
            #   Dependent  = Win32_LogicalDisk
            wmi_part_id = _safe_str(assoc.Antecedent.DeviceID)
            logical = assoc.Dependent
            if wmi_part_id in part_map:
                pinfo = part_map[wmi_part_id]
                pinfo.letter = _safe_str(logical.DeviceID).rstrip(":")
                pinfo.label = _safe_str(logical.VolumeName)
                pinfo.file_system = _safe_str(logical.FileSystem)
                pinfo.free_bytes = _safe_int(logical.FreeSpace)
                total = _safe_int(logical.Size)
                if total > 0:
                    pinfo.size_bytes = total
                    pinfo.used_bytes = total - pinfo.free_bytes

        # 3. Enrich with psutil usage stats where available
        _enrich_partitions_with_psutil(part_map)

        partitions = sorted(part_map.values(), key=lambda p: p.index)
    except Exception as exc:
        logger.debug("Error building partitions via WMI: %s", exc)

    return partitions


def _enrich_partitions_with_psutil(
    part_map: dict[str, PartitionInfo],
) -> None:
    """Fill in usage data from psutil for partitions that have a drive letter."""
    try:
        psutil_parts = psutil.disk_partitions(all=False)
        for ps_part in psutil_parts:
            mount = ps_part.mountpoint.rstrip("\\").rstrip(":")
            # Find matching PartitionInfo by letter
            for pinfo in part_map.values():
                if pinfo.letter and pinfo.letter.upper() == mount.upper():
                    try:
                        usage = psutil.disk_usage(ps_part.mountpoint)
                        pinfo.size_bytes = pinfo.size_bytes or usage.total
                        pinfo.used_bytes = usage.used
                        pinfo.free_bytes = usage.free
                        if not pinfo.file_system:
                            pinfo.file_system = ps_part.fstype
                    except OSError:
                        pass
                    break
    except Exception as exc:
        logger.debug("psutil enrichment failed: %s", exc)


# ---------------------------------------------------------------------------
# Internal: build partition list from PowerShell (fallback)
# ---------------------------------------------------------------------------


def _build_partitions_powershell(disk_index: int) -> list[PartitionInfo]:
    """Build partition list using PowerShell Get-Partition / Get-Volume."""
    partitions: list[PartitionInfo] = []

    cmd = (
        f"Get-Partition -DiskNumber {disk_index} "
        f"| Select-Object PartitionNumber, DriveLetter, Size, Offset, "
        f"Type, IsActive, IsBoot, IsSystem "
        f"| ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return partitions

    if isinstance(data, dict):
        data = [data]

    # Build a map of drive letter -> volume info
    vol_map = _get_volume_map()

    for item in data:
        letter_raw = _safe_str(item.get("DriveLetter", ""))
        # PowerShell may return 0 / null for no drive letter
        letter = letter_raw if letter_raw and letter_raw != "0" else ""

        pinfo = PartitionInfo(
            index=_safe_int(item.get("PartitionNumber")),
            letter=letter,
            size_bytes=_safe_int(item.get("Size")),
            offset_bytes=_safe_int(item.get("Offset")),
            partition_type=_safe_str(item.get("Type", ""), "Unknown"),
            is_active=_safe_bool(item.get("IsActive")),
            is_boot=_safe_bool(item.get("IsBoot")),
            is_system=_safe_bool(item.get("IsSystem")),
        )

        # Fill volume details from the volume map
        if letter and letter.upper() in vol_map:
            vol = vol_map[letter.upper()]
            pinfo.label = vol.get("label", "")
            pinfo.file_system = vol.get("file_system", "")
            pinfo.free_bytes = vol.get("free_bytes", 0)
            if pinfo.size_bytes > 0:
                pinfo.used_bytes = pinfo.size_bytes - pinfo.free_bytes

        # Supplement with psutil
        if letter:
            try:
                usage = psutil.disk_usage(f"{letter}:\\")
                pinfo.size_bytes = pinfo.size_bytes or usage.total
                pinfo.used_bytes = usage.used
                pinfo.free_bytes = usage.free
            except OSError:
                pass

        partitions.append(pinfo)

    return sorted(partitions, key=lambda p: p.index)


def _get_volume_map() -> dict[str, dict[str, Any]]:
    """Return a dict mapping uppercase drive letter -> volume metadata."""
    vol_map: dict[str, dict[str, Any]] = {}
    cmd = (
        "Get-Volume | Where-Object { $_.DriveLetter -ne $null } "
        "| Select-Object DriveLetter, FileSystemLabel, FileSystem, "
        "SizeRemaining | ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return vol_map
    if isinstance(data, dict):
        data = [data]
    for item in data:
        dl = _safe_str(item.get("DriveLetter", ""))
        if dl:
            vol_map[dl.upper()] = {
                "label": _safe_str(item.get("FileSystemLabel", "")),
                "file_system": _safe_str(item.get("FileSystem", "")),
                "free_bytes": _safe_int(item.get("SizeRemaining", 0)),
            }
    return vol_map


# ---------------------------------------------------------------------------
# Internal: detect partition style
# ---------------------------------------------------------------------------


def _detect_partition_style(disk_index: int) -> str:
    """Return 'GPT', 'MBR', 'RAW', or 'Unknown' for the given disk."""
    cmd = (
        f"Get-Disk -Number {disk_index} "
        f"| Select-Object -Property PartitionStyle | ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return "Unknown"
    if isinstance(data, list):
        data = data[0] if data else {}
    style = _safe_str(data.get("PartitionStyle", ""), "Unknown")
    return style if style else "Unknown"


# ---------------------------------------------------------------------------
# Internal: detect interface type
# ---------------------------------------------------------------------------


def _detect_interface_type_ps(disk_index: int) -> str:
    """Detect interface type (NVMe, SATA, USB, ...) via PowerShell."""
    cmd = (
        f"Get-PhysicalDisk | Where-Object {{ $_.DeviceId -eq '{disk_index}' }} "
        f"| Select-Object -Property BusType | ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return "Unknown"
    if isinstance(data, list):
        data = data[0] if data else {}
    bus = _safe_str(data.get("BusType", ""), "Unknown")
    return bus if bus else "Unknown"


# ---------------------------------------------------------------------------
# Batched PowerShell query (replaces N+4 per-disk calls)
# ---------------------------------------------------------------------------


def _get_all_physical_disk_props() -> dict[int, dict]:
    """Fetch MediaType, BusType, HealthStatus for ALL physical disks in one call.

    Returns a dict mapping DeviceId (int) to a property dict.
    """
    cmd = (
        "Get-PhysicalDisk | Select-Object DeviceId, MediaType, BusType, "
        "HealthStatus | ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return {}
    if isinstance(data, dict):
        data = [data]
    result: dict[int, dict] = {}
    for item in data:
        dev_id = _safe_int(item.get("DeviceId", -1), -1)
        if dev_id >= 0:
            result[dev_id] = {
                "MediaType": _safe_str(item.get("MediaType", ""), "Unknown"),
                "BusType": _safe_str(item.get("BusType", ""), "Unknown"),
                "HealthStatus": _safe_str(item.get("HealthStatus", ""), "Unknown"),
            }
    return result


def _get_all_disk_partition_styles() -> dict[int, str]:
    """Fetch PartitionStyle for ALL disks in one call."""
    cmd = (
        "Get-Disk | Select-Object Number, PartitionStyle | ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return {}
    if isinstance(data, dict):
        data = [data]
    result: dict[int, str] = {}
    for item in data:
        num = _safe_int(item.get("Number", -1), -1)
        style = _safe_str(item.get("PartitionStyle", ""), "Unknown")
        if num >= 0:
            result[num] = style if style else "Unknown"
    return result


def _get_all_partition_offsets() -> dict[int, list[int]]:
    """Fetch partition offsets for ALL disks in one call (for 4K alignment check)."""
    cmd = (
        "Get-Partition | Select-Object DiskNumber, PartitionNumber, Offset "
        "| ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        return {}
    if isinstance(data, dict):
        data = [data]
    result: dict[int, list[int]] = {}
    for item in data:
        disk_num = _safe_int(item.get("DiskNumber", -1), -1)
        offset = _safe_int(item.get("Offset", 0))
        if disk_num >= 0:
            result.setdefault(disk_num, []).append(offset)
    return result


def _check_4k_from_offsets(offsets: list[int]) -> bool:
    """Return True if all offsets are 4K-aligned."""
    return all(o % 4096 == 0 for o in offsets)


def _normalize_media_type(raw: str) -> str:
    """Normalise a MediaType string from Get-PhysicalDisk."""
    if not raw or raw in ("Unspecified", "Unknown"):
        return "Unknown"
    lower = raw.lower()
    if "ssd" in lower or "solid" in lower:
        return "SSD"
    if "hdd" in lower or "hard" in lower or "spinning" in lower:
        return "HDD"
    return raw


def _normalize_health(raw: str) -> str:
    """Normalise a HealthStatus string."""
    if raw == "0" or raw.lower() == "healthy":
        return "Healthy"
    return raw if raw else "Unknown"


# ---------------------------------------------------------------------------
# Core: gather all disks
# ---------------------------------------------------------------------------


def get_all_disks() -> list[DiskInfo]:
    """Gather information on every physical disk in the system.

    Results are cached for ``_CACHE_TTL_SECONDS`` to avoid excessive WMI /
    PowerShell overhead.  Call ``refresh_disk_info()`` to force a fresh scan.
    """
    global _disk_cache, _cache_timestamp

    now = time.monotonic()
    if _disk_cache is not None and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        return _disk_cache

    disks = _gather_disks()
    _disk_cache = disks
    _cache_timestamp = time.monotonic()
    return disks


def refresh_disk_info() -> list[DiskInfo]:
    """Force a fresh disk scan, ignoring the cache."""
    global _disk_cache, _cache_timestamp
    _disk_cache = None
    _cache_timestamp = 0.0
    return get_all_disks()


def get_disk_by_index(index: int) -> DiskInfo | None:
    """Return the DiskInfo for a given disk number, or None if not found."""
    for disk in get_all_disks():
        if disk.index == index:
            return disk
    return None


# ---------------------------------------------------------------------------
# Internal: full disk scan
# ---------------------------------------------------------------------------


def _gather_disks() -> list[DiskInfo]:
    """Perform a complete scan of physical disks.

    Tries WMI first; falls back to PowerShell if the ``wmi`` package is not
    available or the query fails.
    """
    wmi_conn = _get_wmi_connection()
    if wmi_conn is not None:
        try:
            return _gather_disks_wmi(wmi_conn)
        except Exception as exc:
            logger.debug("WMI disk scan failed, falling back to PS: %s", exc)

    return _gather_disks_powershell()


def _gather_disks_wmi(wmi_conn: Any) -> list[DiskInfo]:
    """Scan disks via WMI Win32_DiskDrive and related classes."""
    disks: list[DiskInfo] = []

    # Batch-fetch all per-disk PowerShell data in 3 calls (instead of N*4)
    phys_props = _get_all_physical_disk_props()
    part_styles = _get_all_disk_partition_styles()
    all_offsets = _get_all_partition_offsets()

    for drive in wmi_conn.Win32_DiskDrive():
        idx = _safe_int(drive.Index)

        # Partition list
        device_id = _safe_str(drive.DeviceID)
        parts = _build_partitions_wmi(wmi_conn, device_id)

        # Use batched data for this disk
        disk_props = phys_props.get(idx, {})

        # Interface type from WMI (may be generic), prefer batched PS data
        iface = _safe_str(drive.InterfaceType, "Unknown")
        ps_iface = disk_props.get("BusType", "Unknown")
        if ps_iface and ps_iface != "Unknown":
            iface = ps_iface

        # Media type from batched data
        media = _normalize_media_type(disk_props.get("MediaType", "Unknown"))

        # Partition style from batched data
        style = part_styles.get(idx, "Unknown")

        # Health from batched data, fall back to WMI status
        health = _normalize_health(disk_props.get("HealthStatus", "Unknown"))
        if health == "Unknown":
            wmi_status = _safe_str(drive.Status, "Unknown")
            if wmi_status.lower() == "ok":
                health = "Healthy"

        # 4K alignment from batched offset data
        offsets = all_offsets.get(idx, [])
        aligned = _check_4k_from_offsets(offsets) if offsets else True

        # System disk: any partition has the boot flag or letter == C
        is_sys = any(
            p.is_boot or p.is_system or p.letter.upper() == "C"
            for p in parts
        )

        total_size = _safe_int(drive.Size)
        allocated = sum(p.size_bytes for p in parts)
        unallocated = max(total_size - allocated, 0)

        disk = DiskInfo(
            index=idx,
            model=_safe_str(drive.Model),
            serial=_safe_str(drive.SerialNumber),
            size_bytes=total_size,
            media_type=media,
            interface_type=iface,
            partition_style=style,
            is_system_disk=is_sys,
            health_status=health,
            partitions=parts,
            unallocated_bytes=unallocated,
            is_4k_aligned=aligned,
        )
        disks.append(disk)

    return sorted(disks, key=lambda d: d.index)


def _gather_disks_powershell() -> list[DiskInfo]:
    """Scan disks using PowerShell Get-Disk (fallback when WMI unavailable)."""
    disks: list[DiskInfo] = []

    cmd = (
        "Get-Disk | Select-Object Number, FriendlyName, SerialNumber, Size, "
        "PartitionStyle, HealthStatus | ConvertTo-Json"
    )
    data = _run_powershell_json(cmd)
    if data is None:
        logger.warning("PowerShell Get-Disk returned no data")
        return disks

    if isinstance(data, dict):
        data = [data]

    # Batch-fetch physical disk properties and offsets
    phys_props = _get_all_physical_disk_props()
    all_offsets = _get_all_partition_offsets()

    for item in data:
        idx = _safe_int(item.get("Number"))
        total_size = _safe_int(item.get("Size"))

        parts = _build_partitions_powershell(idx)

        # Use batched physical disk data
        disk_props = phys_props.get(idx, {})
        media = _normalize_media_type(disk_props.get("MediaType", "Unknown"))
        iface = disk_props.get("BusType", "Unknown") or "Unknown"

        offsets = all_offsets.get(idx, [])
        aligned = _check_4k_from_offsets(offsets) if offsets else True

        is_sys = any(
            p.is_boot or p.is_system or p.letter.upper() == "C"
            for p in parts
        )

        allocated = sum(p.size_bytes for p in parts)
        unallocated = max(total_size - allocated, 0)

        health_raw = _normalize_health(
            _safe_str(item.get("HealthStatus", ""), "Unknown")
        )

        disk = DiskInfo(
            index=idx,
            model=_safe_str(item.get("FriendlyName", "")),
            serial=_safe_str(item.get("SerialNumber", "")),
            size_bytes=total_size,
            media_type=media,
            interface_type=iface,
            partition_style=_safe_str(item.get("PartitionStyle", ""), "Unknown"),
            is_system_disk=is_sys,
            health_status=health_raw,
            partitions=parts,
            unallocated_bytes=unallocated,
            is_4k_aligned=aligned,
        )
        disks.append(disk)

    return sorted(disks, key=lambda d: d.index)
