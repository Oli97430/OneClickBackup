"""Disk health monitoring: SMART data, temperature, surface tests, benchmarks.

Provides detailed S.M.A.R.T. attribute retrieval, real-time temperature
readings, sequential surface scanning, and IO throughput benchmarking for
both HDDs and SSDs.

All queries that touch low-level storage APIs require administrator
privileges.  The module delegates PowerShell execution to
:func:`src.utils.helpers.run_powershell`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from src.utils.helpers import run_powershell


logger = logging.getLogger("OneClickBackup.DiskHealth")


# ---------------------------------------------------------------------------
# SMART attribute IDs (ATA/ATAPI standard)
# ---------------------------------------------------------------------------

_ATTR_TEMPERATURE: int = 194
_ATTR_REALLOCATED_SECTORS: int = 5
_ATTR_PENDING_SECTORS: int = 197
_ATTR_UNCORRECTABLE_SECTORS: int = 198
_ATTR_POWER_ON_HOURS: int = 9
_ATTR_WEAR_LEVELING: int = 177
_ATTR_TOTAL_LBAS_WRITTEN: int = 241

# Sector size used for LBA-to-byte conversion
_SECTOR_BYTES: int = 512

# Benchmark defaults
_BENCHMARK_BLOCK_SIZE: int = 1024 * 1024          # 1 MiB per IO
_BENCHMARK_SEQ_SIZE: int = 128 * 1024 * 1024      # 128 MiB sequential test
_BENCHMARK_RANDOM_BLOCKS: int = 1000               # 1 000 random IO ops


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SMARTInfo:
    """Parsed S.M.A.R.T. health data for a single physical disk."""

    temperature_celsius: int | None = None
    power_on_hours: int | None = None
    reallocated_sectors: int | None = None
    pending_sectors: int | None = None
    uncorrectable_sectors: int | None = None
    wear_leveling_count: int | None = None          # SSD only
    total_bytes_written: int | None = None           # SSD only
    overall_health: str = "Unknown"                  # Healthy / Warning / Critical
    raw_attributes: dict = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    """Throughput measurements from a disk benchmark run."""

    sequential_read_mbps: float = 0.0
    sequential_write_mbps: float = 0.0
    random_read_iops: float = 0.0
    random_write_iops: float = 0.0
    test_duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DiskHealthError(Exception):
    """Raised when a disk health operation fails."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_int(value: object, default: int | None = None) -> int | None:
    """Convert *value* to int, returning *default* on failure."""
    if value is None:
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _run_ps_json(command: str) -> object | None:
    """Execute a PowerShell command that outputs JSON and parse the result.

    Returns the parsed object, or *None* on failure.
    """
    stdout, stderr, rc = run_powershell(command)
    if rc != 0 or not stdout.strip():
        if stderr:
            logger.debug("PowerShell stderr: %s", stderr)
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        logger.debug("Failed to parse PowerShell JSON: %.200s", stdout)
        return None


def _parse_smart_bytes(raw: str) -> list[int]:
    """Parse a whitespace-separated string of decimal byte values.

    The ``VendorSpecific`` property of ``MSStorageDriver_ATAPISmartData``
    is a byte array that PowerShell serialises as space-delimited decimals
    when piped through ``ConvertTo-Json``.

    Returns a list of int byte values, or an empty list on failure.
    """
    try:
        return [int(b) for b in raw.split()]
    except (ValueError, AttributeError):
        return []


def _extract_smart_attribute(vendor_bytes: list[int], attr_id: int) -> int | None:
    """Extract the raw value of a SMART attribute from the vendor byte array.

    Each attribute record is 12 bytes starting at offset 2 in the
    vendor-specific area:

        Byte 0:  attribute ID
        Byte 1:  flags (low)
        Byte 2:  flags (high)
        Byte 3:  normalised value
        Byte 4:  worst value
        Bytes 5-10: raw value (little-endian, 6 bytes)
        Byte 11: reserved

    The vendor area begins with a 2-byte header (revision), so the first
    attribute sits at index 2.
    """
    if len(vendor_bytes) < 14:
        return None

    offset = 2  # skip revision word
    while offset + 12 <= len(vendor_bytes):
        aid = vendor_bytes[offset]
        if aid == 0:
            # End of attribute table
            break
        if aid == attr_id:
            # Raw value: bytes 5..10 relative to the attribute start
            raw_bytes = vendor_bytes[offset + 5 : offset + 11]
            value = 0
            for i, b in enumerate(raw_bytes):
                value |= b << (8 * i)
            return value
        offset += 12

    return None


def _determine_health(
    reallocated: int | None,
    pending: int | None,
    uncorrectable: int | None,
    wear_leveling: int | None,
    ps_health: str,
) -> str:
    """Derive an overall health verdict from SMART counters and PS status.

    Returns ``"Healthy"``, ``"Warning"``, or ``"Critical"``.
    """
    # If PowerShell already reports unhealthy, trust it
    ps_lower = ps_health.lower() if ps_health else ""
    if "unhealthy" in ps_lower or "degraded" in ps_lower:
        return "Critical"

    # Critical: any uncorrectable or large reallocated count
    if uncorrectable is not None and uncorrectable > 0:
        return "Critical"
    if reallocated is not None and reallocated > 100:
        return "Critical"

    # Warning: moderate reallocated / pending sectors or low wear leveling
    if reallocated is not None and reallocated > 0:
        return "Warning"
    if pending is not None and pending > 0:
        return "Warning"
    if wear_leveling is not None and wear_leveling < 10:
        return "Warning"

    if ps_lower in ("healthy", "0"):
        return "Healthy"

    # Default when we have no negative indicators
    if reallocated == 0 and pending == 0 and uncorrectable == 0:
        return "Healthy"

    return "Healthy"


# ---------------------------------------------------------------------------
# DiskHealthManager
# ---------------------------------------------------------------------------


class DiskHealthManager:
    """Collects disk health telemetry via PowerShell and direct IO."""

    def __init__(self) -> None:
        self._log = logging.getLogger("OneClickBackup.DiskHealth")

    # ------------------------------------------------------------------
    # SMART info
    # ------------------------------------------------------------------

    def get_smart_info(self, disk_index: int) -> SMARTInfo:
        """Retrieve S.M.A.R.T. data for *disk_index*.

        Attempts two data sources in order:

        1.  ``Get-StorageReliabilityCounter`` (works on most modern disks).
        2.  WMI ``MSStorageDriver_ATAPISmartData`` for the raw vendor byte
            array (provides the deepest attribute coverage but requires
            admin and may not be available on NVMe).

        Returns a populated :class:`SMARTInfo`.  Fields that could not be
        determined are left as *None*.
        """
        info = SMARTInfo()

        # --- Source 1: StorageReliabilityCounter (high-level) ---
        self._fill_from_reliability_counter(disk_index, info)

        # --- Source 2: WMI raw SMART bytes (deeper attribute coverage) ---
        self._fill_from_wmi_smart(disk_index, info)

        # --- Health verdict ---
        ps_health = self._get_ps_health_status(disk_index)
        info.overall_health = _determine_health(
            info.reallocated_sectors,
            info.pending_sectors,
            info.uncorrectable_sectors,
            info.wear_leveling_count,
            ps_health,
        )

        return info

    def _fill_from_reliability_counter(
        self, disk_index: int, info: SMARTInfo
    ) -> None:
        """Populate *info* from ``Get-StorageReliabilityCounter``."""
        cmd = (
            f"Get-PhysicalDisk | Where-Object {{ $_.DeviceId -eq '{disk_index}' }} "
            "| Get-StorageReliabilityCounter "
            "| Select-Object Temperature, PowerOnHours, ReadErrorsTotal, "
            "ReadErrorsCorrected, ReadErrorsUncorrected, Wear, "
            "WriteErrorsTotal, WriteErrorsCorrected, WriteErrorsUncorrected "
            "| ConvertTo-Json -Compress"
        )
        data = _run_ps_json(cmd)
        if data is None:
            self._log.debug(
                "StorageReliabilityCounter unavailable for disk %d", disk_index
            )
            return

        if isinstance(data, list):
            data = data[0] if data else {}

        info.temperature_celsius = _safe_int(data.get("Temperature"))  # type: ignore[union-attr]
        info.power_on_hours = _safe_int(data.get("PowerOnHours"))  # type: ignore[union-attr]
        info.wear_leveling_count = _safe_int(data.get("Wear"))  # type: ignore[union-attr]

        # StorageReliabilityCounter doesn't expose reallocated/pending
        # directly, but read/write error counters are useful context.
        for key, val in (data if isinstance(data, dict) else {}).items():
            if val is not None:
                info.raw_attributes[key] = val

    def _fill_from_wmi_smart(self, disk_index: int, info: SMARTInfo) -> None:
        """Populate *info* from WMI ``MSStorageDriver_ATAPISmartData``.

        This provides the full ATA SMART attribute table as a raw byte
        array. Not available on all systems (requires admin, may not
        work with NVMe drives).
        """
        cmd = (
            "Get-WmiObject -Namespace root\\WMI "
            "-Class MSStorageDriver_ATAPISmartData "
            "| Select-Object InstanceName, VendorSpecific "
            "| ConvertTo-Json -Compress"
        )
        data = _run_ps_json(cmd)
        if data is None:
            self._log.debug("WMI SMART data unavailable")
            return

        if isinstance(data, dict):
            data = [data]

        # Match the correct disk instance.  InstanceName typically
        # contains the disk number, e.g. "SCSI\\Disk&Ven_..._0"
        target_entry: dict | None = None
        for entry in data:  # type: ignore[union-attr]
            instance = str(entry.get("InstanceName", ""))
            # Heuristic: look for the disk index at the end of the name
            if instance.rstrip("_").endswith(str(disk_index)):
                target_entry = entry
                break

        # If exact match failed, use entry at the disk_index position
        if target_entry is None and isinstance(data, list):
            if 0 <= disk_index < len(data):
                target_entry = data[disk_index]

        if target_entry is None:
            self._log.debug(
                "No WMI SMART entry matched disk %d", disk_index
            )
            return

        vendor_raw = target_entry.get("VendorSpecific", "")
        if isinstance(vendor_raw, list):
            vendor_bytes = [int(b) for b in vendor_raw]
        elif isinstance(vendor_raw, str):
            vendor_bytes = _parse_smart_bytes(vendor_raw)
        else:
            vendor_bytes = []

        if not vendor_bytes:
            return

        # Extract individual attributes (only overwrite if not yet set)
        temp = _extract_smart_attribute(vendor_bytes, _ATTR_TEMPERATURE)
        if temp is not None and info.temperature_celsius is None:
            # Temperature raw value sometimes encodes min/max in upper
            # bytes; the current temperature is in the low byte.
            info.temperature_celsius = temp & 0xFF

        poh = _extract_smart_attribute(vendor_bytes, _ATTR_POWER_ON_HOURS)
        if poh is not None and info.power_on_hours is None:
            info.power_on_hours = poh

        realloc = _extract_smart_attribute(vendor_bytes, _ATTR_REALLOCATED_SECTORS)
        if realloc is not None:
            info.reallocated_sectors = realloc

        pending = _extract_smart_attribute(vendor_bytes, _ATTR_PENDING_SECTORS)
        if pending is not None:
            info.pending_sectors = pending

        uncorr = _extract_smart_attribute(vendor_bytes, _ATTR_UNCORRECTABLE_SECTORS)
        if uncorr is not None:
            info.uncorrectable_sectors = uncorr

        wear = _extract_smart_attribute(vendor_bytes, _ATTR_WEAR_LEVELING)
        if wear is not None and info.wear_leveling_count is None:
            info.wear_leveling_count = wear

        lbas = _extract_smart_attribute(vendor_bytes, _ATTR_TOTAL_LBAS_WRITTEN)
        if lbas is not None:
            info.total_bytes_written = lbas * _SECTOR_BYTES

        # Stash every decoded attribute id -> raw value
        offset = 2
        while offset + 12 <= len(vendor_bytes):
            aid = vendor_bytes[offset]
            if aid == 0:
                break
            raw_val = 0
            for i, b in enumerate(vendor_bytes[offset + 5 : offset + 11]):
                raw_val |= b << (8 * i)
            info.raw_attributes[f"smart_{aid}"] = raw_val
            offset += 12

    def _get_ps_health_status(self, disk_index: int) -> str:
        """Return the HealthStatus string from ``Get-PhysicalDisk``."""
        cmd = (
            f"Get-PhysicalDisk | Where-Object {{ $_.DeviceId -eq '{disk_index}' }} "
            "| Select-Object -ExpandProperty HealthStatus"
        )
        stdout, _, rc = run_powershell(cmd)
        if rc == 0 and stdout.strip():
            return stdout.strip()
        return "Unknown"

    # ------------------------------------------------------------------
    # Temperature
    # ------------------------------------------------------------------

    def get_temperature(self, disk_index: int) -> int | None:
        """Return the current temperature in Celsius for *disk_index*.

        Uses ``Get-PhysicalDisk | Get-StorageReliabilityCounter``.
        Returns *None* when the disk or driver does not expose temperature.
        """
        cmd = (
            f"Get-PhysicalDisk | Where-Object {{ $_.DeviceId -eq '{disk_index}' }} "
            "| Get-StorageReliabilityCounter "
            "| Select-Object -ExpandProperty Temperature"
        )
        stdout, stderr, rc = run_powershell(cmd)
        if rc != 0 or not stdout.strip():
            self._log.debug(
                "Temperature unavailable for disk %d: %s", disk_index, stderr
            )
            return None

        return _safe_int(stdout.strip())

    # ------------------------------------------------------------------
    # Surface test
    # ------------------------------------------------------------------

    def run_surface_test(
        self,
        disk_index: int,
        callback: Callable[[str, float], None] | None = None,
    ) -> dict:
        """Perform a read-only surface scan on *disk_index*.

        Opens the physical drive (``\\\\.\\PhysicalDrive<N>``) via a
        PowerShell script that reads each sector sequentially and counts
        IO errors.

        Args:
            disk_index: Physical disk number.
            callback:   Optional ``callback(message, percent)`` for
                        progress reporting.

        Returns:
            A dict with keys ``"total_sectors"``, ``"bad_sectors"``,
            ``"error_details"`` (list of sector offsets), and
            ``"duration_seconds"``.

        Raises:
            DiskHealthError: If the disk cannot be opened.
        """
        self._log.info("Starting surface test on disk %d", disk_index)

        if callback:
            callback("Querying disk size...", 0.0)

        # Get total disk size
        size_cmd = f"(Get-Disk -Number {disk_index}).Size"
        stdout, stderr, rc = run_powershell(size_cmd)
        if rc != 0 or not stdout.strip().isdigit():
            raise DiskHealthError(
                f"Cannot determine size of disk {disk_index}: {stderr}"
            )
        disk_size = int(stdout.strip())
        total_sectors = disk_size // _SECTOR_BYTES
        if total_sectors == 0:
            raise DiskHealthError(f"Disk {disk_index} reports zero size.")

        # Build a PowerShell script that reads sectors in 1 MiB chunks
        # and reports bad offsets as JSON.
        chunk_size = 1024 * 1024  # 1 MiB
        sectors_per_chunk = chunk_size // _SECTOR_BYTES
        total_chunks = max(1, (disk_size + chunk_size - 1) // chunk_size)

        # The script prints one JSON object at the end with the result.
        ps_script = (
            f"$ErrorActionPreference = 'SilentlyContinue'\n"
            f"$path = '\\\\.\\PhysicalDrive{disk_index}'\n"
            f"$chunkSize = {chunk_size}\n"
            f"$totalChunks = {total_chunks}\n"
            f"$badSectors = @()\n"
            f"$buf = New-Object byte[] $chunkSize\n"
            f"try {{\n"
            f"  $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')\n"
            f"  for ($i = 0; $i -lt $totalChunks; $i++) {{\n"
            f"    try {{\n"
            f"      $null = $fs.Read($buf, 0, $chunkSize)\n"
            f"    }} catch {{\n"
            f"      $offset = [int64]$i * $chunkSize\n"
            f"      $badSectors += $offset\n"
            f"    }}\n"
            f"    if ($i % 1000 -eq 0) {{\n"
            f"      [Console]::Error.WriteLine(\"PROGRESS:$i/$totalChunks\")\n"
            f"    }}\n"
            f"  }}\n"
            f"  $fs.Close()\n"
            f"}} catch {{\n"
            f"  [Console]::Error.WriteLine(\"OPENERR:$($_.Exception.Message)\")\n"
            f"}}\n"
            f"$result = @{{ total_sectors={total_sectors}; "
            f"bad_sectors=$badSectors.Count; "
            f"error_details=$badSectors }} | ConvertTo-Json -Compress\n"
            f"Write-Output $result"
        )

        if callback:
            callback("Scanning disk surface (this may take a while)...", 1.0)

        start = time.monotonic()
        stdout, stderr, rc = run_powershell(ps_script)
        elapsed = time.monotonic() - start

        # Check for open errors reported via stderr
        if "OPENERR:" in stderr:
            err_msg = stderr.split("OPENERR:")[-1].strip()
            raise DiskHealthError(
                f"Cannot open PhysicalDrive{disk_index}: {err_msg}"
            )

        # Parse result
        result: dict = {
            "total_sectors": total_sectors,
            "bad_sectors": 0,
            "error_details": [],
            "duration_seconds": round(elapsed, 2),
        }

        if stdout.strip():
            try:
                parsed = json.loads(stdout)
                result["bad_sectors"] = int(parsed.get("bad_sectors", 0))
                details = parsed.get("error_details", [])
                if isinstance(details, list):
                    result["error_details"] = [int(d) for d in details]
                elif isinstance(details, (int, float)):
                    result["error_details"] = [int(details)]
            except (json.JSONDecodeError, ValueError) as exc:
                self._log.warning("Failed to parse surface test output: %s", exc)

        if callback:
            callback(
                f"Surface test complete: {result['bad_sectors']} bad sector(s) found.",
                100.0,
            )

        self._log.info(
            "Surface test disk %d: %d bad sectors, %.1f s",
            disk_index,
            result["bad_sectors"],
            elapsed,
        )
        return result

    # ------------------------------------------------------------------
    # Benchmark
    # ------------------------------------------------------------------

    def run_benchmark(
        self,
        drive_letter: str,
        callback: Callable[[str, float], None] | None = None,
    ) -> BenchmarkResult:
        """Measure sequential and random IO throughput on *drive_letter*.

        Creates a temporary file on the target volume and performs timed
        read/write passes using Python's low-level :func:`os.open` /
        :func:`os.read` / :func:`os.write` for minimal buffering overhead.

        Args:
            drive_letter: Single letter, e.g. ``"D"``.
            callback:     Optional progress reporter.

        Returns:
            A :class:`BenchmarkResult` with measured throughput figures.

        Raises:
            DiskHealthError: If the volume is not accessible.
        """
        letter = drive_letter.strip().upper().rstrip(":")
        if not letter or not letter.isalpha() or len(letter) != 1:
            raise DiskHealthError(f"Invalid drive letter: {drive_letter!r}")

        root = f"{letter}:\\"
        if not os.path.isdir(root):
            raise DiskHealthError(f"Volume {root} is not accessible.")

        self._log.info("Starting benchmark on %s:", letter)

        overall_start = time.perf_counter()
        result = BenchmarkResult()

        # --- Sequential write ---
        if callback:
            callback("Benchmarking sequential write...", 5.0)

        tmp_path = os.path.join(root, f"_ocb_bench_{os.getpid()}.tmp")
        try:
            result.sequential_write_mbps = self._bench_sequential_write(tmp_path)

            # --- Sequential read ---
            if callback:
                callback("Benchmarking sequential read...", 30.0)
            result.sequential_read_mbps = self._bench_sequential_read(tmp_path)

            # --- Random write ---
            if callback:
                callback("Benchmarking random write...", 55.0)
            result.random_write_iops = self._bench_random_write(tmp_path)

            # --- Random read ---
            if callback:
                callback("Benchmarking random read...", 80.0)
            result.random_read_iops = self._bench_random_read(tmp_path)

        finally:
            # Clean up
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        result.test_duration_seconds = round(
            time.perf_counter() - overall_start, 2
        )

        if callback:
            callback("Benchmark complete.", 100.0)

        self._log.info(
            "Benchmark %s: seq_r=%.1f MB/s, seq_w=%.1f MB/s, "
            "rnd_r=%.0f IOPS, rnd_w=%.0f IOPS (%.1f s)",
            letter,
            result.sequential_read_mbps,
            result.sequential_write_mbps,
            result.random_read_iops,
            result.random_write_iops,
            result.test_duration_seconds,
        )
        return result

    # -- Sequential helpers ------------------------------------------------

    def _bench_sequential_write(self, path: str) -> float:
        """Write ``_BENCHMARK_SEQ_SIZE`` bytes and return MB/s."""
        block = os.urandom(_BENCHMARK_BLOCK_SIZE)
        total = _BENCHMARK_SEQ_SIZE
        written = 0

        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_BINARY)
        try:
            start = time.perf_counter()
            while written < total:
                chunk = min(_BENCHMARK_BLOCK_SIZE, total - written)
                os.write(fd, block[:chunk])
                written += chunk
            os.fsync(fd)
            elapsed = time.perf_counter() - start
        finally:
            os.close(fd)

        if elapsed <= 0:
            return 0.0
        return round((written / (1024 * 1024)) / elapsed, 2)

    def _bench_sequential_read(self, path: str) -> float:
        """Read the benchmark file sequentially and return MB/s."""
        try:
            file_size = os.path.getsize(path)
        except OSError:
            return 0.0
        if file_size == 0:
            return 0.0

        fd = os.open(path, os.O_RDONLY | os.O_BINARY)
        try:
            total_read = 0
            start = time.perf_counter()
            while True:
                chunk = os.read(fd, _BENCHMARK_BLOCK_SIZE)
                if not chunk:
                    break
                total_read += len(chunk)
            elapsed = time.perf_counter() - start
        finally:
            os.close(fd)

        if elapsed <= 0:
            return 0.0
        return round((total_read / (1024 * 1024)) / elapsed, 2)

    # -- Random IO helpers -------------------------------------------------

    def _bench_random_write(self, path: str) -> float:
        """Perform random 4 KiB writes and return IOPS."""
        block_4k = os.urandom(4096)
        try:
            file_size = os.path.getsize(path)
        except OSError:
            return 0.0
        if file_size < 4096:
            return 0.0

        max_offset = (file_size // 4096) - 1
        if max_offset <= 0:
            return 0.0

        fd = os.open(path, os.O_RDWR | os.O_BINARY)
        try:
            # Pre-compute pseudo-random offsets (deterministic, fast)
            import random as _rng
            gen = _rng.Random(42)
            offsets = [gen.randint(0, max_offset) * 4096 for _ in range(_BENCHMARK_RANDOM_BLOCKS)]

            start = time.perf_counter()
            for off in offsets:
                os.lseek(fd, off, os.SEEK_SET)
                os.write(fd, block_4k)
            os.fsync(fd)
            elapsed = time.perf_counter() - start
        finally:
            os.close(fd)

        if elapsed <= 0:
            return 0.0
        return round(_BENCHMARK_RANDOM_BLOCKS / elapsed, 1)

    def _bench_random_read(self, path: str) -> float:
        """Perform random 4 KiB reads and return IOPS."""
        try:
            file_size = os.path.getsize(path)
        except OSError:
            return 0.0
        if file_size < 4096:
            return 0.0

        max_offset = (file_size // 4096) - 1
        if max_offset <= 0:
            return 0.0

        fd = os.open(path, os.O_RDONLY | os.O_BINARY)
        try:
            import random as _rng
            gen = _rng.Random(42)
            offsets = [gen.randint(0, max_offset) * 4096 for _ in range(_BENCHMARK_RANDOM_BLOCKS)]

            start = time.perf_counter()
            for off in offsets:
                os.lseek(fd, off, os.SEEK_SET)
                os.read(fd, 4096)
            elapsed = time.perf_counter() - start
        finally:
            os.close(fd)

        if elapsed <= 0:
            return 0.0
        return round(_BENCHMARK_RANDOM_BLOCKS / elapsed, 1)

    # ------------------------------------------------------------------
    # Batch: all disks
    # ------------------------------------------------------------------

    def get_all_disk_health(self) -> list[dict]:
        """Return a health summary for every physical disk in the system.

        Each entry is a dict with keys ``"disk_index"``,
        ``"temperature_celsius"``, ``"overall_health"``,
        ``"power_on_hours"``, ``"reallocated_sectors"``,
        ``"pending_sectors"``, ``"uncorrectable_sectors"``,
        ``"wear_leveling_count"``, and ``"total_bytes_written"``.
        """
        # Discover all disk indices
        cmd = (
            "Get-PhysicalDisk | Select-Object DeviceId | ConvertTo-Json -Compress"
        )
        data = _run_ps_json(cmd)
        if data is None:
            self._log.warning("No physical disks found")
            return []

        if isinstance(data, dict):
            data = [data]

        results: list[dict] = []
        for entry in data:  # type: ignore[union-attr]
            dev_id = entry.get("DeviceId")
            if dev_id is None:
                continue
            try:
                idx = int(dev_id)
            except (ValueError, TypeError):
                continue

            try:
                info = self.get_smart_info(idx)
            except Exception as exc:
                self._log.debug("SMART query failed for disk %d: %s", idx, exc)
                info = SMARTInfo()

            results.append({
                "disk_index": idx,
                "temperature_celsius": info.temperature_celsius,
                "overall_health": info.overall_health,
                "power_on_hours": info.power_on_hours,
                "reallocated_sectors": info.reallocated_sectors,
                "pending_sectors": info.pending_sectors,
                "uncorrectable_sectors": info.uncorrectable_sectors,
                "wear_leveling_count": info.wear_leveling_count,
                "total_bytes_written": info.total_bytes_written,
            })

        return results
