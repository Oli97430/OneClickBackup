"""Administrator privilege utilities for Windows."""

import ctypes
import sys
import functools
from typing import Callable, Any


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
    """
    if is_admin():
        return

    script = sys.argv[0]
    params = " ".join(f'"{arg}"' for arg in sys.argv[1:])

    try:
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            f'"{script}" {params}',
            None,
            1,  # SW_SHOWNORMAL
        )
    except OSError as e:
        print(f"Failed to elevate privileges: {e}")
        sys.exit(1)

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
            print("Administrator privileges required. Requesting elevation...")
            run_as_admin()
            # run_as_admin calls sys.exit, so this line is only reached
            # if already admin or elevation was skipped
        return func(*args, **kwargs)

    return wrapper
