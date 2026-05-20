"""Administrator privilege utilities for Windows."""

from __future__ import annotations

import ctypes
import functools
import logging
import subprocess
import sys
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


def is_admin() -> bool:
    """Check if the current process is running with administrator privileges.

    Returns:
        True if running as administrator, False otherwise.
    """
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except (AttributeError, OSError):
        return False


def run_as_admin() -> None:
    """Re-launch the current script with administrator privileges.

    Uses ShellExecuteW to trigger a UAC elevation prompt.
    The current process will exit after launching the elevated one.

    Handles both frozen EXE (PyInstaller) and source-run modes:
    - Frozen: ``ShellExecuteW("runas", "OneClickBackup.exe", "")``
    - Source: ``ShellExecuteW("runas", "python.exe", '"main.py" ...')``

    Raises:
        OSError: If the UAC prompt is cancelled or elevation fails.
    """
    if is_admin():
        return

    if getattr(sys, "frozen", False):
        # Frozen EXE: sys.executable IS the app — no script argument needed
        exe = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        # Source run: python.exe "main.py" [args...]
        exe = sys.executable
        params = subprocess.list2cmdline(sys.argv)

    _log.info("Requesting elevation: %s %s", exe, params)

    # ShellExecuteW returns an HINSTANCE value:
    #   > 32 = success, <= 32 = error
    ret = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        exe,
        params,
        None,
        1,  # SW_SHOWNORMAL
    )

    if ret <= 32:
        _log.error("ShellExecuteW failed with code %d", ret)
        raise OSError(f"Failed to elevate privileges (error code {ret})")

    _log.info("Elevated process launched, shutting down current instance.")
    sys.exit(0)


def require_admin(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that ensures a function runs only with admin privileges.

    If not running as admin, triggers UAC elevation and exits the
    current process. The elevated process will re-run from the start.

    Args:
        func: The function that requires administrator privileges.

    Returns:
        Wrapped function that checks for admin before executing.
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not is_admin():
            _log.warning("Administrator privileges required. Requesting elevation...")
            run_as_admin()
            # run_as_admin calls sys.exit, so this line is only reached
            # if already admin or elevation was skipped
        return func(*args, **kwargs)

    return wrapper
