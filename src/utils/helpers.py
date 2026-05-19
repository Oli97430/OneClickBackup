"""General-purpose utility functions for OneClickBackup."""

import os
import re
import subprocess
import tempfile
from datetime import datetime
from typing import Optional, Tuple


def format_bytes(size_bytes: int) -> str:
    """Convert a byte count to a human-readable string.

    Args:
        size_bytes: Number of bytes.

    Returns:
        Formatted string such as "1.50 GB" or "256.00 MB".
    """
    if size_bytes < 0:
        return "0 B"

    units = ("B", "KB", "MB", "GB", "TB", "PB")
    factor = 1024.0

    for unit in units:
        if abs(size_bytes) < factor:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= factor  # type: ignore[assignment]

    return f"{size_bytes:.2f} EB"


def parse_size_to_bytes(size_str: str) -> int:
    """Convert a human-readable size string back to bytes.

    Accepts formats like "500 GB", "1.5TB", "256 MB", "1024KB".

    Args:
        size_str: The size string to parse.

    Returns:
        The size in bytes, or 0 if parsing fails.
    """
    size_str = size_str.strip()
    match = re.match(r"^\s*([\d.]+)\s*(B|KB|MB|GB|TB|PB|EB)\s*$", size_str, re.IGNORECASE)
    if not match:
        return 0

    value = float(match.group(1))
    unit = match.group(2).upper()

    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
        "PB": 1024 ** 5,
        "EB": 1024 ** 6,
    }

    return int(value * multipliers.get(unit, 1))


def get_drive_letter_from_path(path: str) -> Optional[str]:
    """Extract the drive letter from a Windows file path.

    Args:
        path: A Windows file path such as "C:\\Users\\..." or "D:/data".

    Returns:
        The uppercase drive letter (e.g. "C"), or None if not found.
    """
    if not path:
        return None

    drive = os.path.splitdrive(path)[0]
    if drive:
        return drive.rstrip(":").upper()

    return None


def safe_int(value: object, default: int = 0) -> int:
    """Safely convert a value to an integer.

    Args:
        value: The value to convert.
        default: Fallback if conversion fails.

    Returns:
        The integer value, or *default* on failure.
    """
    try:
        return int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def run_powershell(command: str) -> Tuple[str, str, int]:
    """Execute a PowerShell command and capture its output.

    Args:
        command: The PowerShell command string to run.

    Returns:
        A tuple of (stdout, stderr, return_code).
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out after 120 seconds", 1
    except FileNotFoundError:
        return "", "PowerShell executable not found", 1
    except OSError as e:
        return "", f"OS error running PowerShell: {e}", 1


def run_diskpart(script_lines: list[str]) -> Tuple[str, str, int]:
    """Run a diskpart script and return its output.

    Creates a temporary script file, invokes diskpart, then cleans up.

    Args:
        script_lines: List of diskpart commands, one per line.

    Returns:
        A tuple of (stdout, stderr, return_code).
    """
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="diskpart_"
        ) as tmp:
            tmp.write("\n".join(script_lines) + "\n")
            tmp_path = tmp.name

        result = subprocess.run(
            ["diskpart", "/s", tmp_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "Diskpart command timed out after 120 seconds", 1
    except FileNotFoundError:
        return "", "diskpart executable not found", 1
    except OSError as e:
        return "", f"OS error running diskpart: {e}", 1
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def generate_timestamp() -> str:
    """Generate a formatted timestamp suitable for backup names.

    Returns:
        A string like "20260518_143025" (YYYYMMDD_HHMMSS).
    """
    return datetime.now().strftime("%Y%m%d_%H%M%S")
