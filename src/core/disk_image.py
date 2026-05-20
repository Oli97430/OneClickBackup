"""Disk image creation, mounting, and format conversion for Windows.

Supports VHD, VHDX (via Hyper-V PowerShell cmdlets) and raw ``.img``
images (via direct sector-level I/O against ``\\\\.\\PhysicalDriveN``).

All operations that touch physical disks or mount/dismount virtual disks
require administrator privileges.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from src.utils.admin import is_admin
from src.utils.helpers import run_powershell


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECTOR_SIZE: int = 512
_READ_CHUNK: int = 1024 * 1024  # 1 MiB per read during raw copy

_SUPPORTED_FORMATS: frozenset[str] = frozenset({"vhd", "vhdx", "img"})

_log = logging.getLogger("OneClickBackup.DiskImage")


def _ps_escape(s: str) -> str:
    """Escape single quotes for safe interpolation into PowerShell single-quoted strings."""
    return s.replace("'", "''")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DiskImageError(Exception):
    """Raised when a disk-image operation fails."""


class AdminRequiredError(DiskImageError):
    """Raised when the current process lacks administrator privileges."""


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


def _normalise_format(fmt: str) -> str:
    """Return the lower-cased format string, stripping a leading dot.

    Raises *DiskImageError* for unsupported formats.
    """
    fmt = fmt.lower().lstrip(".")
    if fmt not in _SUPPORTED_FORMATS:
        raise DiskImageError(
            f"Unsupported format '{fmt}'. "
            f"Supported formats: {', '.join(sorted(_SUPPORTED_FORMATS))}"
        )
    return fmt


def _get_disk_size(disk_index: int) -> int:
    """Return total size in bytes for *disk_index*, or 0 on failure."""
    cmd = f"(Get-Disk -Number {disk_index}).Size"
    stdout, _, rc = run_powershell(cmd)
    if rc == 0 and stdout.strip().isdigit():
        return int(stdout.strip())
    return 0


def _disk_exists(disk_index: int) -> bool:
    """Return *True* if the physical disk number is present."""
    cmd = (
        f"Get-Disk -Number {disk_index} -ErrorAction SilentlyContinue "
        "| Measure-Object | Select-Object -ExpandProperty Count"
    )
    stdout, _, rc = run_powershell(cmd)
    return rc == 0 and stdout.strip() == "1"


# ---------------------------------------------------------------------------
# DiskImageManager
# ---------------------------------------------------------------------------

class DiskImageManager:
    """Create, mount, convert, and inspect VHD / VHDX / IMG disk images."""

    # ======================================================================
    # VHD / VHDX creation
    # ======================================================================

    @staticmethod
    def create_vhd(
        path: str,
        size_bytes: int,
        vhd_type: str = "dynamic",
    ) -> bool:
        """Create a new VHD or VHDX file using PowerShell ``New-VHD``.

        Args:
            path: Destination file path (must end in ``.vhd`` or ``.vhdx``).
            size_bytes: Virtual disk size in bytes.
            vhd_type: ``"dynamic"`` (default) or ``"fixed"``.

        Returns:
            *True* on success.

        Raises:
            AdminRequiredError: If not running elevated.
            DiskImageError: On invalid arguments or PowerShell failure.
        """
        _require_admin("VHD creation")

        ext = Path(path).suffix.lower()
        if ext not in (".vhd", ".vhdx"):
            raise DiskImageError(
                f"Path must end in .vhd or .vhdx, got '{ext}'."
            )

        if size_bytes <= 0:
            raise DiskImageError("size_bytes must be a positive integer.")

        vhd_type_lower = vhd_type.lower()
        if vhd_type_lower not in ("dynamic", "fixed"):
            raise DiskImageError(
                f"vhd_type must be 'dynamic' or 'fixed', got '{vhd_type}'."
            )

        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        type_flag = "-Dynamic" if vhd_type_lower == "dynamic" else "-Fixed"
        cmd = (
            f"New-VHD -Path '{_ps_escape(path)}' -SizeBytes {size_bytes} "
            f"{type_flag} -ErrorAction Stop"
        )

        _log.info("Creating %s VHD: %s (%d bytes)", vhd_type_lower, path, size_bytes)
        stdout, stderr, rc = run_powershell(cmd)
        if rc != 0:
            _log.error("New-VHD failed: %s", stderr)
            raise DiskImageError(f"Failed to create VHD: {stderr}")

        _log.info("VHD created: %s", path)
        return True

    # ======================================================================
    # Disk image from physical disk
    # ======================================================================

    @staticmethod
    def create_disk_image(
        disk_index: int,
        output_path: str,
        format: str = "vhdx",
        callback: Callable[[str, float], None] | None = None,
    ) -> bool:
        """Create a disk image of an entire physical disk.

        For VHD / VHDX the method uses ``wbadmin`` as the primary strategy
        and falls back to a raw sector copy converted via ``Convert-VHD``
        when ``wbadmin`` is unavailable.

        For raw ``.img`` images the method copies every sector directly
        from ``\\\\.\\PhysicalDriveN`` to the output file.

        Args:
            disk_index: Physical disk number (0, 1, ...).
            output_path: Destination file path.
            format: ``"vhdx"`` (default), ``"vhd"``, or ``"img"``.
            callback: Optional ``callback(message, percent)`` for progress.

        Returns:
            *True* on success.

        Raises:
            AdminRequiredError: If not running elevated.
            DiskImageError: On any operational failure.
        """
        _require_admin("Disk image creation")
        fmt = _normalise_format(format)

        if not _disk_exists(disk_index):
            raise DiskImageError(f"Disk {disk_index} does not exist.")

        parent_dir = os.path.dirname(output_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        if fmt == "img":
            return DiskImageManager._create_raw_image(
                disk_index, output_path, callback,
            )

        # VHD / VHDX path
        return DiskImageManager._create_vhd_image(
            disk_index, output_path, fmt, callback,
        )

    # ------------------------------------------------------------------
    # Private: raw .img creation
    # ------------------------------------------------------------------

    @staticmethod
    def _create_raw_image(
        disk_index: int,
        output_path: str,
        callback: Callable[[str, float], None] | None,
    ) -> bool:
        """Sector-by-sector copy of ``\\\\.\\PhysicalDriveN`` to a file."""
        disk_size = _get_disk_size(disk_index)
        if disk_size <= 0:
            raise DiskImageError(
                f"Cannot determine size for disk {disk_index}."
            )

        device_path = rf"\\.\PhysicalDrive{disk_index}"
        _log.info(
            "Creating raw image of %s (%d bytes) -> %s",
            device_path, disk_size, output_path,
        )

        if callback:
            callback("Opening physical disk...", 0.0)

        try:
            with open(device_path, "rb") as src, \
                 open(output_path, "wb") as dst:
                bytes_copied = 0
                while bytes_copied < disk_size:
                    remaining = disk_size - bytes_copied
                    chunk_size = min(_READ_CHUNK, remaining)
                    data = src.read(chunk_size)
                    if not data:
                        break
                    dst.write(data)
                    bytes_copied += len(data)

                    if callback:
                        pct = (bytes_copied / disk_size) * 100.0
                        callback(
                            f"Copying sectors... {bytes_copied}/{disk_size}",
                            min(pct, 100.0),
                        )
        except PermissionError as exc:
            raise DiskImageError(
                f"Permission denied opening {device_path}. "
                "Ensure the application is running as administrator."
            ) from exc
        except OSError as exc:
            raise DiskImageError(
                f"I/O error creating raw image: {exc}"
            ) from exc

        if callback:
            callback("Raw disk image complete.", 100.0)

        _log.info("Raw image created: %s (%d bytes)", output_path, bytes_copied)
        return True

    # ------------------------------------------------------------------
    # Private: VHD / VHDX creation from physical disk
    # ------------------------------------------------------------------

    @staticmethod
    def _create_vhd_image(
        disk_index: int,
        output_path: str,
        fmt: str,
        callback: Callable[[str, float], None] | None,
    ) -> bool:
        """Create a VHD/VHDX image using ``wbadmin`` with a ``Convert-VHD``
        fallback for format conversion.
        """
        if callback:
            callback("Resolving disk partitions...", 0.0)

        # Build the volume list (e.g. "C:,D:") for the volumes on this disk.
        cmd = (
            f"Get-Partition -DiskNumber {disk_index} "
            "| Where-Object { $_.DriveLetter } "
            "| Select-Object -ExpandProperty DriveLetter"
        )
        stdout, _, rc = run_powershell(cmd)
        letters: list[str] = []
        if rc == 0 and stdout.strip():
            for line in stdout.strip().splitlines():
                ch = line.strip()
                if ch and ch.isalpha():
                    letters.append(ch.upper())

        if not letters:
            raise DiskImageError(
                f"No accessible partitions found on disk {disk_index}."
            )

        # If the output extension doesn't match *fmt*, adjust and convert later.
        desired_ext = f".{fmt}"
        out_ext = Path(output_path).suffix.lower()
        needs_convert = out_ext != desired_ext

        # wbadmin writes a WindowsImageBackup folder.  We work in a
        # temporary directory next to the target file and convert afterwards.
        work_dir = output_path + ".wbadmin_tmp"
        os.makedirs(work_dir, exist_ok=True)

        include_spec = ",".join(f"{l}:" for l in letters)

        if callback:
            callback("Running wbadmin backup...", 5.0)

        wb_cmd = (
            f"wbadmin start backup -backupTarget:\"{work_dir}\" "
            f"-include:{include_spec} -quiet"
        )
        _log.info("wbadmin command: %s", wb_cmd)
        wb_stdout, wb_stderr, wb_rc = run_powershell(wb_cmd)

        if wb_rc != 0:
            _log.warning("wbadmin failed (rc=%d): %s", wb_rc, wb_stderr)
            # Clean up temp dir and fall back to raw + convert
            _safe_rmtree(work_dir)
            return DiskImageManager._fallback_raw_to_vhd(
                disk_index, output_path, fmt, callback,
            )

        # Locate the .vhdx file that wbadmin created
        if callback:
            callback("Locating backup image...", 70.0)

        found_vhd = _find_first_file(work_dir, (".vhd", ".vhdx"))
        if not found_vhd:
            _log.warning("No VHD(X) found in wbadmin output; falling back.")
            _safe_rmtree(work_dir)
            return DiskImageManager._fallback_raw_to_vhd(
                disk_index, output_path, fmt, callback,
            )

        # Move / convert the image to the requested path and format
        if callback:
            callback("Finalising disk image...", 80.0)

        src_ext = Path(found_vhd).suffix.lower()
        if src_ext == desired_ext or not needs_convert:
            # Simple move
            try:
                os.replace(found_vhd, output_path)
            except OSError as exc:
                raise DiskImageError(
                    f"Failed to move image to {output_path}: {exc}"
                ) from exc
        else:
            # Convert between VHD <-> VHDX
            conv_cmd = (
                f"Convert-VHD -Path '{_ps_escape(found_vhd)}' "
                f"-DestinationPath '{_ps_escape(output_path)}' -ErrorAction Stop"
            )
            _, conv_err, conv_rc = run_powershell(conv_cmd)
            if conv_rc != 0:
                raise DiskImageError(
                    f"Convert-VHD failed: {conv_err}"
                )

        _safe_rmtree(work_dir)

        if callback:
            callback("Disk image created.", 100.0)

        _log.info("Disk image created: %s", output_path)
        return True

    @staticmethod
    def _fallback_raw_to_vhd(
        disk_index: int,
        output_path: str,
        fmt: str,
        callback: Callable[[str, float], None] | None,
    ) -> bool:
        """Create a raw image then convert it to VHD/VHDX."""
        raw_path = output_path + ".raw.img"

        if callback:
            callback("Falling back to raw image + conversion...", 10.0)

        try:
            DiskImageManager._create_raw_image(disk_index, raw_path, callback)

            if callback:
                callback("Converting raw image to VHD(X)...", 80.0)

            conv_cmd = (
                f"Convert-VHD -Path '{_ps_escape(raw_path)}' "
                f"-DestinationPath '{_ps_escape(output_path)}' -ErrorAction Stop"
            )
            _, conv_err, conv_rc = run_powershell(conv_cmd)
            if conv_rc != 0:
                raise DiskImageError(
                    f"Convert-VHD from raw failed: {conv_err}"
                )
        finally:
            # Always clean up the intermediate raw file
            try:
                os.remove(raw_path)
            except OSError:
                pass

        if callback:
            callback("Disk image created (via raw fallback).", 100.0)

        _log.info("Disk image created via raw fallback: %s", output_path)
        return True

    # ======================================================================
    # Mount / Dismount
    # ======================================================================

    @staticmethod
    def mount_vhd(path: str) -> str | None:
        """Mount a VHD / VHDX and return the assigned drive letter.

        Uses PowerShell ``Mount-VHD``.  Returns *None* if mounting
        succeeded but no drive letter could be resolved, or if an
        error occurred.

        Raises:
            AdminRequiredError: If not running elevated.
            DiskImageError: On PowerShell failure.
        """
        _require_admin("VHD mount")

        if not os.path.isfile(path):
            raise DiskImageError(f"File not found: {path}")

        _log.info("Mounting VHD: %s", path)
        cmd = f"Mount-VHD -Path '{_ps_escape(path)}' -ErrorAction Stop"
        _, stderr, rc = run_powershell(cmd)
        if rc != 0:
            raise DiskImageError(f"Mount-VHD failed: {stderr}")

        # Retrieve the drive letter that Windows assigned
        letter_cmd = (
            f"(Get-DiskImage -ImagePath '{_ps_escape(path)}' | Get-Disk | "
            "Get-Partition | Where-Object { $_.DriveLetter } | "
            "Select-Object -First 1).DriveLetter"
        )
        stdout, _, rc = run_powershell(letter_cmd)
        if rc == 0 and stdout.strip():
            letter = stdout.strip().rstrip(":")
            if letter and letter.isalpha():
                _log.info("VHD mounted as %s:", letter.upper())
                return letter.upper()

        _log.warning("VHD mounted but no drive letter resolved.")
        return None

    @staticmethod
    def dismount_vhd(path: str) -> bool:
        """Dismount a previously mounted VHD / VHDX.

        Args:
            path: The image file path that was passed to :meth:`mount_vhd`.

        Returns:
            *True* on success.

        Raises:
            AdminRequiredError: If not running elevated.
            DiskImageError: On PowerShell failure.
        """
        _require_admin("VHD dismount")

        _log.info("Dismounting VHD: %s", path)
        cmd = f"Dismount-VHD -Path '{_ps_escape(path)}' -ErrorAction Stop"
        _, stderr, rc = run_powershell(cmd)
        if rc != 0:
            raise DiskImageError(f"Dismount-VHD failed: {stderr}")

        _log.info("VHD dismounted: %s", path)
        return True

    # ======================================================================
    # List mounted VHDs
    # ======================================================================

    @staticmethod
    def list_mounted_vhds() -> list[dict]:
        """Return information about every currently mounted VHD / VHDX.

        Each dict contains:
            - ``Path``: image file path.
            - ``Attached``: whether the image is attached (bool).
            - ``DiskNumber``: disk number if attached.
            - ``Size``: virtual size in bytes.

        Returns:
            A (possibly empty) list of dicts.
        """
        cmd = (
            "Get-VHD -Path (Get-Disk | Where-Object { $_.Location } "
            "| Select-Object -ExpandProperty Location) -ErrorAction SilentlyContinue "
            "| Select-Object Path, Attached, DiskNumber, Size "
            "| ConvertTo-Json -Compress"
        )
        stdout, _, rc = run_powershell(cmd)
        if rc != 0 or not stdout.strip():
            return []

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            _log.debug("Failed to parse Get-VHD JSON: %s", stdout[:200])
            return []

        if isinstance(data, dict):
            data = [data]

        results: list[dict] = []
        for entry in data:
            results.append({
                "Path": str(entry.get("Path", "")),
                "Attached": bool(entry.get("Attached", False)),
                "DiskNumber": int(entry.get("DiskNumber", -1)),
                "Size": int(entry.get("Size", 0)),
            })
        return results

    # ======================================================================
    # Image conversion
    # ======================================================================

    @staticmethod
    def convert_image(
        source: str,
        target: str,
        target_format: str,
    ) -> bool:
        """Convert a disk image between VHD, VHDX, and raw IMG formats.

        Conversion paths:
            - VHD <-> VHDX: uses ``Convert-VHD``.
            - VHD/VHDX -> IMG: mounts the image and performs raw sector copy.
            - IMG -> VHD/VHDX: uses ``Convert-VHD`` (PowerShell can accept
              a raw image as input).

        Args:
            source: Path to the existing disk image.
            target: Destination path for the converted image.
            target_format: One of ``"vhd"``, ``"vhdx"``, ``"img"``.

        Returns:
            *True* on success.

        Raises:
            AdminRequiredError: If not running elevated.
            DiskImageError: On failure.
        """
        _require_admin("Image conversion")
        fmt = _normalise_format(target_format)

        if not os.path.isfile(source):
            raise DiskImageError(f"Source file not found: {source}")

        src_ext = Path(source).suffix.lower().lstrip(".")

        parent_dir = os.path.dirname(target)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        _log.info("Converting %s -> %s (format=%s)", source, target, fmt)

        # -- VHD <-> VHDX (both handled by Convert-VHD) -------------------
        if src_ext in ("vhd", "vhdx") and fmt in ("vhd", "vhdx"):
            cmd = (
                f"Convert-VHD -Path '{_ps_escape(source)}' "
                f"-DestinationPath '{_ps_escape(target)}' -ErrorAction Stop"
            )
            _, stderr, rc = run_powershell(cmd)
            if rc != 0:
                raise DiskImageError(f"Convert-VHD failed: {stderr}")
            _log.info("Conversion complete: %s", target)
            return True

        # -- VHD/VHDX -> IMG (mount + raw read) ---------------------------
        if src_ext in ("vhd", "vhdx") and fmt == "img":
            return DiskImageManager._vhd_to_raw(source, target)

        # -- IMG -> VHD/VHDX (Convert-VHD from raw) -----------------------
        if src_ext == "img" and fmt in ("vhd", "vhdx"):
            cmd = (
                f"Convert-VHD -Path '{_ps_escape(source)}' "
                f"-DestinationPath '{_ps_escape(target)}' -ErrorAction Stop"
            )
            _, stderr, rc = run_powershell(cmd)
            if rc != 0:
                raise DiskImageError(f"Convert-VHD from raw failed: {stderr}")
            _log.info("Conversion complete: %s", target)
            return True

        raise DiskImageError(
            f"Unsupported conversion: {src_ext} -> {fmt}."
        )

    @staticmethod
    def _vhd_to_raw(source: str, target: str) -> bool:
        """Mount a VHD/VHDX, find its disk number, and raw-copy to *target*."""
        # Mount the image (read-only)
        mount_cmd = f"Mount-VHD -Path '{_ps_escape(source)}' -ReadOnly -ErrorAction Stop"
        _, stderr, rc = run_powershell(mount_cmd)
        if rc != 0:
            raise DiskImageError(f"Mount-VHD failed: {stderr}")

        try:
            # Find the disk number of the mounted image
            num_cmd = (
                f"(Get-DiskImage -ImagePath '{_ps_escape(source)}' | Get-Disk).Number"
            )
            stdout, _, rc = run_powershell(num_cmd)
            if rc != 0 or not stdout.strip().isdigit():
                raise DiskImageError(
                    "Could not determine disk number for mounted VHD."
                )
            disk_number = int(stdout.strip())

            disk_size = _get_disk_size(disk_number)
            if disk_size <= 0:
                raise DiskImageError(
                    "Could not determine size of mounted VHD."
                )

            # Sector-by-sector copy
            device_path = rf"\\.\PhysicalDrive{disk_number}"
            with open(device_path, "rb") as src, \
                 open(target, "wb") as dst:
                bytes_copied = 0
                while bytes_copied < disk_size:
                    remaining = disk_size - bytes_copied
                    chunk_size = min(_READ_CHUNK, remaining)
                    data = src.read(chunk_size)
                    if not data:
                        break
                    dst.write(data)
                    bytes_copied += len(data)

        finally:
            # Always dismount
            dismount_cmd = (
                f"Dismount-VHD -Path '{_ps_escape(source)}' -ErrorAction SilentlyContinue"
            )
            run_powershell(dismount_cmd)

        _log.info("VHD -> raw conversion complete: %s", target)
        return True

    # ======================================================================
    # Image info
    # ======================================================================

    @staticmethod
    def get_image_info(path: str) -> dict:
        """Return metadata about a disk image file.

        For VHD / VHDX files the metadata is retrieved via ``Get-VHD``.
        For raw ``.img`` files the returned info is derived from the
        file size on disk.

        The returned dict contains at least:
            - ``Path``: absolute path to the image.
            - ``Format``: ``"vhd"``, ``"vhdx"``, or ``"img"``.
            - ``FileSize``: actual file size on disk (bytes).
            - ``VirtualSize``: virtual / logical size (bytes).
            - ``VhdType``: ``"Dynamic"``, ``"Fixed"``, or ``"Raw"``.

        Raises:
            DiskImageError: If the file does not exist or cannot be read.
        """
        if not os.path.isfile(path):
            raise DiskImageError(f"File not found: {path}")

        ext = Path(path).suffix.lower().lstrip(".")
        file_size = os.path.getsize(path)

        # -- Raw image -----------------------------------------------------
        if ext == "img":
            return {
                "Path": os.path.abspath(path),
                "Format": "img",
                "FileSize": file_size,
                "VirtualSize": file_size,
                "VhdType": "Raw",
            }

        # -- VHD / VHDX ----------------------------------------------------
        cmd = (
            f"Get-VHD -Path '{_ps_escape(path)}' -ErrorAction Stop "
            "| Select-Object Path, VhdFormat, VhdType, FileSize, Size "
            "| ConvertTo-Json -Compress"
        )
        stdout, stderr, rc = run_powershell(cmd)
        if rc != 0:
            _log.warning("Get-VHD failed for %s: %s", path, stderr)
            # Return a best-effort dict from the file system alone
            return {
                "Path": os.path.abspath(path),
                "Format": ext,
                "FileSize": file_size,
                "VirtualSize": file_size,
                "VhdType": "Unknown",
            }

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "Path": os.path.abspath(path),
                "Format": ext,
                "FileSize": file_size,
                "VirtualSize": file_size,
                "VhdType": "Unknown",
            }

        return {
            "Path": str(data.get("Path", os.path.abspath(path))),
            "Format": str(data.get("VhdFormat", ext)).lower(),
            "FileSize": int(data.get("FileSize", file_size)),
            "VirtualSize": int(data.get("Size", file_size)),
            "VhdType": str(data.get("VhdType", "Unknown")),
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _find_first_file(
    directory: str,
    extensions: tuple[str, ...],
) -> str | None:
    """Walk *directory* and return the first file matching *extensions*."""
    for dirpath, _dirs, files in os.walk(directory):
        for fname in files:
            if fname.lower().endswith(extensions):
                return os.path.join(dirpath, fname)
    return None


def _safe_rmtree(path: str) -> None:
    """Remove a directory tree, ignoring errors."""
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
