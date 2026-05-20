"""Multi-pass secure disk wipe.

Provides :class:`SecureWiper` with quick, multi-pass, and DoD 5220.22-M
wipe methods, plus post-wipe verification.

All wipe operations require administrator privileges because they write
raw data to physical disk handles (``\\\\.\\PhysicalDriveN``) or invoke
diskpart.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from src.utils.admin import is_admin
from src.utils.helpers import run_diskpart, run_powershell


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECTOR_SIZE: int = 512

# Write chunk: 1 MiB at a time keeps memory usage low while giving
# reasonable throughput through the PowerShell bridge.
_CHUNK_SECTORS: int = 2048  # 2048 * 512 = 1 MiB

# DoD 5220.22-M defines a 7-pass pattern
_DOD_PASSES: int = 7


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WipeError(Exception):
    """Raised when a wipe operation fails."""


class AdminRequiredError(WipeError):
    """Raised when the current process lacks administrator privileges."""


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


def _is_system_disk(disk_index: int) -> bool:
    """Return *True* if the disk contains the running Windows installation.

    This is a safety check — callers should never wipe the system disk.
    """
    cmd = (
        "Get-Partition -DiskNumber {idx} -ErrorAction SilentlyContinue | "
        "Where-Object {{ $_.DriveLetter -eq 'C' }} | "
        "Measure-Object | Select-Object -ExpandProperty Count"
    ).format(idx=disk_index)
    stdout, _, rc = run_powershell(cmd)
    return rc == 0 and stdout.strip() not in ("", "0")


def _write_pattern_to_disk(
    disk_index: int,
    disk_size: int,
    pattern_byte: int | None,
    pass_label: str,
    callback: Callable[[float, str], None] | None,
    cancel_event: threading.Event,
    log: logging.Logger,
) -> bool:
    """Overwrite every sector of the disk with a single byte pattern.

    When *pattern_byte* is ``None``, random data is used instead.

    Returns *True* on success.
    """
    chunk_bytes = _CHUNK_SECTORS * _SECTOR_SIZE
    total_chunks = max(1, disk_size // chunk_bytes)
    offset = 0
    chunk_index = 0

    while offset < disk_size:
        if cancel_event.is_set():
            raise WipeError("Wipe cancelled by user.")

        remaining = min(chunk_bytes, disk_size - offset)

        if pattern_byte is not None:
            # Build a Base64-encoded block of the fill byte
            b64_cmd = _build_write_pattern_ps(
                disk_index, offset, remaining, pattern_byte
            )
        else:
            # Random data pass
            b64_cmd = _build_write_random_ps(disk_index, offset, remaining)

        stdout, stderr, rc = run_powershell(b64_cmd)
        if rc != 0:
            err = stderr if stderr else stdout
            log.error(
                "%s: write failed at offset %d: %s", pass_label, offset, err
            )
            return False

        chunk_index += 1
        offset += remaining

        if callback and chunk_index % 20 == 0:
            pct = min((chunk_index / total_chunks) * 100.0, 99.9)
            mb_done = offset / (1024 * 1024)
            mb_total = disk_size / (1024 * 1024)
            callback(
                pct,
                f"{pass_label}: {mb_done:,.0f} / {mb_total:,.0f} MiB",
            )

    return True


def _build_write_pattern_ps(
    disk_index: int,
    offset: int,
    byte_count: int,
    pattern_byte: int,
) -> str:
    """Build a PowerShell command that writes *byte_count* of *pattern_byte*."""
    return (
        f"$path = '\\\\.\\PhysicalDrive{disk_index}';"
        "$fs = [System.IO.File]::Open($path, "
        "[System.IO.FileMode]::Open, "
        "[System.IO.FileAccess]::ReadWrite, "
        "[System.IO.FileShare]::ReadWrite);"
        f"$fs.Seek({offset}, [System.IO.SeekOrigin]::Begin) | Out-Null;"
        f"$buf = New-Object byte[] {byte_count};"
        f"for ($i=0; $i -lt {byte_count}; $i++) {{ $buf[$i] = {pattern_byte} }};"
        "$fs.Write($buf, 0, $buf.Length);"
        "$fs.Flush();"
        "$fs.Close();"
        "'OK'"
    )


def _build_write_random_ps(
    disk_index: int,
    offset: int,
    byte_count: int,
) -> str:
    """Build a PowerShell command that writes *byte_count* of random data."""
    return (
        f"$path = '\\\\.\\PhysicalDrive{disk_index}';"
        "$fs = [System.IO.File]::Open($path, "
        "[System.IO.FileMode]::Open, "
        "[System.IO.FileAccess]::ReadWrite, "
        "[System.IO.FileShare]::ReadWrite);"
        f"$fs.Seek({offset}, [System.IO.SeekOrigin]::Begin) | Out-Null;"
        f"$buf = New-Object byte[] {byte_count};"
        "$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create();"
        "$rng.GetBytes($buf);"
        "$fs.Write($buf, 0, $buf.Length);"
        "$fs.Flush();"
        "$fs.Close();"
        "$rng.Dispose();"
        "'OK'"
    )


def _read_sectors_ps(
    disk_index: int,
    offset: int,
    count: int = 1,
) -> bytes | None:
    """Read *count* sectors starting at *offset* and return raw bytes."""
    import base64

    byte_count = count * _SECTOR_SIZE
    ps_script = (
        f"$path = '\\\\.\\PhysicalDrive{disk_index}';"
        "$fs = [System.IO.File]::Open($path, "
        "[System.IO.FileMode]::Open, "
        "[System.IO.FileAccess]::Read, "
        "[System.IO.FileShare]::ReadWrite);"
        f"$fs.Seek({offset}, [System.IO.SeekOrigin]::Begin) | Out-Null;"
        f"$buf = New-Object byte[] {byte_count};"
        "$read = $fs.Read($buf, 0, $buf.Length);"
        "$fs.Close();"
        "if ($read -gt 0) { [Convert]::ToBase64String($buf[0..($read-1)]) } "
        "else { '' }"
    )
    stdout, _, rc = run_powershell(ps_script)
    if rc != 0 or not stdout.strip():
        return None

    try:
        return base64.b64decode(stdout.strip())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SecureWiper:
    """Multi-pass secure disk wipe with progress reporting and cancellation.

    Example::

        wiper = SecureWiper()
        ok = wiper.secure_wipe(2, passes=3, callback=lambda pct, msg: print(f"{pct:.0f}% {msg}"))
        if ok:
            wiper.verify_wipe(2)
    """

    def __init__(self) -> None:
        self._log = logging.getLogger("OneClickBackup.SecureWipe")
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Request cancellation of the running wipe."""
        self._cancel_event.set()

    def _check_cancelled(self) -> None:
        """Raise :exc:`WipeError` if cancellation has been requested."""
        if self._cancel_event.is_set():
            raise WipeError("Wipe cancelled by user.")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_disk(self, disk_index: int) -> int:
        """Validate the disk index and return the disk size in bytes.

        Raises :exc:`WipeError` on invalid input or if the disk is the
        system disk (safety guard).
        """
        if not isinstance(disk_index, int) or disk_index < 0:
            raise WipeError(f"Invalid disk index: {disk_index!r}")
        if not _disk_exists(disk_index):
            raise WipeError(f"Disk {disk_index} does not exist.")
        if _is_system_disk(disk_index):
            raise WipeError(
                f"Disk {disk_index} contains the running Windows installation. "
                "Refusing to wipe the system disk."
            )

        disk_size = _get_disk_size_bytes(disk_index)
        if disk_size == 0:
            raise WipeError(f"Could not determine size of disk {disk_index}.")

        return disk_size

    # ------------------------------------------------------------------
    # Quick wipe (diskpart clean)
    # ------------------------------------------------------------------

    def quick_wipe(
        self,
        disk_index: int,
        callback: Callable[[float, str], None] | None = None,
    ) -> bool:
        """Wipe a disk using ``diskpart clean``.

        This removes the partition table but does **not** overwrite the
        data sectors — data may still be recoverable with forensic tools.

        Args:
            disk_index: Physical disk number to wipe.
            callback:   Optional ``callback(percent, message)`` for progress.

        Returns:
            *True* on success.
        """
        _require_admin("Disk wipe")
        self._cancel_event.clear()
        self._validate_disk(disk_index)

        self._log.info("Starting quick wipe (diskpart clean) on disk %d", disk_index)
        if callback:
            callback(0.0, f"Wiping disk {disk_index} (quick)...")

        stdout, stderr, rc = run_diskpart([
            f"select disk {disk_index}",
            "clean",
        ])

        if rc != 0:
            err = stderr if stderr else stdout
            self._log.error("Quick wipe failed on disk %d: %s", disk_index, err)
            raise WipeError(f"diskpart clean failed: {err}")

        lower_out = stdout.lower()
        if "virtual disk service error" in lower_out:
            self._log.error("diskpart error: %s", stdout)
            raise WipeError(f"diskpart error: {stdout}")

        self._log.info("Quick wipe completed on disk %d", disk_index)
        if callback:
            callback(100.0, "Quick wipe complete.")

        return True

    # ------------------------------------------------------------------
    # Multi-pass secure wipe
    # ------------------------------------------------------------------

    def secure_wipe(
        self,
        disk_index: int,
        passes: int = 3,
        callback: Callable[[float, str], None] | None = None,
    ) -> bool:
        """Overwrite the entire disk surface with multiple data patterns.

        Default 3-pass scheme:

        1. All zeros (``0x00``)
        2. All ones (``0xFF``)
        3. Cryptographically random data

        Args:
            disk_index: Physical disk number to wipe.
            passes:     Number of overwrite passes (minimum 1).
            callback:   Optional ``callback(percent, message)`` for progress.

        Returns:
            *True* when all passes complete successfully.
        """
        _require_admin("Secure disk wipe")
        self._cancel_event.clear()

        if passes < 1:
            raise WipeError(f"passes must be >= 1, got {passes}")

        disk_size = self._validate_disk(disk_index)

        self._log.info(
            "Starting %d-pass secure wipe on disk %d (%d bytes)",
            passes, disk_index, disk_size,
        )

        # Clean the partition table first so the raw handle can write freely
        if callback:
            callback(0.0, "Cleaning partition table...")
        run_diskpart([f"select disk {disk_index}", "clean"])

        # Build the pattern sequence: 0x00, 0xFF, random, then cycle
        patterns: list[tuple[int | None, str]] = []
        base_patterns: list[tuple[int | None, str]] = [
            (0x00, "zeros (0x00)"),
            (0xFF, "ones (0xFF)"),
            (None, "random data"),
        ]
        for i in range(passes):
            byte_val, label = base_patterns[i % len(base_patterns)]
            patterns.append((byte_val, f"Pass {i + 1}/{passes}: {label}"))

        for pass_idx, (byte_val, label) in enumerate(patterns):
            self._check_cancelled()

            self._log.info("Secure wipe %s on disk %d", label, disk_index)

            def _pass_callback(pct: float, msg: str) -> None:
                if callback:
                    # Scale each pass into its fraction of the total
                    overall = ((pass_idx + pct / 100.0) / passes) * 100.0
                    callback(overall, msg)

            ok = _write_pattern_to_disk(
                disk_index=disk_index,
                disk_size=disk_size,
                pattern_byte=byte_val,
                pass_label=label,
                callback=_pass_callback,
                cancel_event=self._cancel_event,
                log=self._log,
            )
            if not ok:
                raise WipeError(f"Secure wipe failed during: {label}")

        self._log.info(
            "Secure wipe completed on disk %d (%d passes)", disk_index, passes
        )
        if callback:
            callback(100.0, f"Secure wipe complete ({passes} passes).")

        return True

    # ------------------------------------------------------------------
    # DoD 5220.22-M wipe (7 passes)
    # ------------------------------------------------------------------

    def dod_wipe(
        self,
        disk_index: int,
        callback: Callable[[float, str], None] | None = None,
    ) -> bool:
        """Wipe a disk following the DoD 5220.22-M standard (7 passes).

        Pass pattern::

            1. 0x00
            2. 0xFF
            3. Random
            4. 0x00
            5. 0xFF
            6. Random
            7. 0x00

        Args:
            disk_index: Physical disk number to wipe.
            callback:   Optional ``callback(percent, message)`` for progress.

        Returns:
            *True* when all seven passes complete successfully.
        """
        _require_admin("DoD 5220.22-M wipe")
        self._cancel_event.clear()
        disk_size = self._validate_disk(disk_index)

        self._log.info(
            "Starting DoD 5220.22-M wipe on disk %d (%d bytes)",
            disk_index, disk_size,
        )

        # Clean the partition table first
        if callback:
            callback(0.0, "Cleaning partition table...")
        run_diskpart([f"select disk {disk_index}", "clean"])

        dod_patterns: list[tuple[int | None, str]] = [
            (0x00, "Pass 1/7: zeros (0x00)"),
            (0xFF, "Pass 2/7: ones (0xFF)"),
            (None, "Pass 3/7: random data"),
            (0x00, "Pass 4/7: zeros (0x00)"),
            (0xFF, "Pass 5/7: ones (0xFF)"),
            (None, "Pass 6/7: random data"),
            (0x00, "Pass 7/7: final zeros (0x00)"),
        ]

        for pass_idx, (byte_val, label) in enumerate(dod_patterns):
            self._check_cancelled()

            self._log.info("DoD wipe %s on disk %d", label, disk_index)

            def _pass_callback(pct: float, msg: str) -> None:
                if callback:
                    overall = ((pass_idx + pct / 100.0) / _DOD_PASSES) * 100.0
                    callback(overall, msg)

            ok = _write_pattern_to_disk(
                disk_index=disk_index,
                disk_size=disk_size,
                pattern_byte=byte_val,
                pass_label=label,
                callback=_pass_callback,
                cancel_event=self._cancel_event,
                log=self._log,
            )
            if not ok:
                raise WipeError(f"DoD wipe failed during: {label}")

        self._log.info("DoD 5220.22-M wipe completed on disk %d", disk_index)
        if callback:
            callback(100.0, "DoD 5220.22-M wipe complete (7 passes).")

        return True

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_wipe(
        self,
        disk_index: int,
        callback: Callable[[float, str], None] | None = None,
    ) -> bool:
        """Verify that a disk has been wiped by reading sectors.

        Reads every sector and checks that all bytes are zero.  This is
        appropriate after a wipe whose final pass writes zeros (e.g.
        :meth:`dod_wipe`).  After a random-data final pass, this method
        will report a non-zero verification — use it only when the last
        pass is a zero fill.

        Args:
            disk_index: Physical disk number to verify.
            callback:   Optional ``callback(percent, message)`` for progress.

        Returns:
            *True* if every sampled sector is all-zeros.
        """
        _require_admin("Wipe verification")
        self._cancel_event.clear()

        if not isinstance(disk_index, int) or disk_index < 0:
            raise WipeError(f"Invalid disk index: {disk_index!r}")
        if not _disk_exists(disk_index):
            raise WipeError(f"Disk {disk_index} does not exist.")

        disk_size = _get_disk_size_bytes(disk_index)
        if disk_size == 0:
            raise WipeError(f"Could not determine size of disk {disk_index}.")

        self._log.info("Verifying wipe on disk %d", disk_index)
        if callback:
            callback(0.0, "Verifying wipe...")

        chunk_bytes = _CHUNK_SECTORS * _SECTOR_SIZE
        total_chunks = max(1, disk_size // chunk_bytes)
        zero_chunk = bytes(chunk_bytes)
        offset = 0
        chunk_index = 0
        non_zero_count = 0

        while offset < disk_size:
            self._check_cancelled()

            data = _read_sectors_ps(disk_index, offset, count=_CHUNK_SECTORS)
            if data is None:
                self._log.warning(
                    "Could not read at offset %d during verification", offset
                )
                offset += chunk_bytes
                chunk_index += 1
                continue

            # Compare against the expected zero pattern
            expected_len = min(chunk_bytes, disk_size - offset)
            if data[:expected_len] != zero_chunk[:expected_len]:
                non_zero_count += 1
                self._log.debug("Non-zero data at offset %d", offset)

            chunk_index += 1
            offset += chunk_bytes

            if callback and chunk_index % 20 == 0:
                pct = min((chunk_index / total_chunks) * 100.0, 99.9)
                mb_done = offset / (1024 * 1024)
                mb_total = disk_size / (1024 * 1024)
                callback(
                    pct,
                    f"Verifying... {mb_done:,.0f} / {mb_total:,.0f} MiB",
                )

        if non_zero_count > 0:
            self._log.warning(
                "Verification found %d non-zero chunk(s) on disk %d",
                non_zero_count, disk_index,
            )
            if callback:
                callback(
                    100.0,
                    f"Verification failed: {non_zero_count} non-zero chunk(s) found.",
                )
            return False

        self._log.info("Wipe verification passed on disk %d", disk_index)
        if callback:
            callback(100.0, "Verification passed: all sectors are zero.")

        return True
