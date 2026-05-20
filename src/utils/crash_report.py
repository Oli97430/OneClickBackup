"""Unhandled-exception capture and crash-report logging.

Installs a custom ``sys.excepthook`` that writes a detailed crash
report to ``~/.oneclickbackup_logs/crash_YYYYMMDD_HHMMSS.txt`` and
shows a messagebox to the user so they know where to find it.

Usage:
    from src.utils.crash_report import install_crash_handler
    install_crash_handler()   # call once at startup
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import traceback
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)

_LOG_DIR = os.path.join(os.path.expanduser("~"), ".oneclickbackup_logs")
_APP_LOG_FILE = os.path.join(_LOG_DIR, "app.log")
_TAIL_LINES = 50  # Number of recent log lines to include


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install_crash_handler() -> None:
    """Replace ``sys.excepthook`` with a handler that writes crash reports.

    Safe to call multiple times -- subsequent calls are no-ops.
    """
    if getattr(install_crash_handler, "_installed", False):
        return

    sys.excepthook = _crash_hook
    install_crash_handler._installed = True  # type: ignore[attr-defined]
    _log.debug("Crash handler installed.")


def get_crash_reports() -> list[dict[str, str]]:
    """List all existing crash reports with timestamp and summary.

    Returns:
        A list of dicts, each containing:

        - ``"path"``: Absolute path to the crash file.
        - ``"timestamp"``: ISO-formatted creation time.
        - ``"summary"``: The first non-blank content line of the file
          (usually the exception message).
    """
    reports: list[dict[str, str]] = []

    if not os.path.isdir(_LOG_DIR):
        return reports

    for fname in sorted(os.listdir(_LOG_DIR)):
        if not fname.startswith("crash_") or not fname.endswith(".txt"):
            continue

        fpath = os.path.join(_LOG_DIR, fname)
        timestamp = _parse_timestamp_from_filename(fname)
        summary = _extract_summary(fpath)

        reports.append({
            "path": fpath,
            "timestamp": timestamp,
            "summary": summary,
        })

    # Most-recent first
    reports.sort(key=lambda r: r["timestamp"], reverse=True)
    return reports


# ---------------------------------------------------------------------------
# The hook
# ---------------------------------------------------------------------------

def _crash_hook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: Any,
) -> None:
    """Custom except-hook that writes a crash report and shows a dialog."""
    # Always log the traceback through the normal logging system first.
    _log.critical(
        "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
    )

    try:
        report_path = _write_crash_report(exc_type, exc_value, exc_tb)
        _show_crash_dialog(report_path)
    except Exception as inner:
        # Absolute last resort: print to stderr.
        print(
            f"[CRASH] Failed to write crash report: {inner}",
            file=sys.stderr,
        )
        traceback.print_exception(exc_type, exc_value, exc_tb)


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_crash_report(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: Any,
) -> str:
    """Write a crash report file and return its path."""
    os.makedirs(_LOG_DIR, exist_ok=True)

    now = datetime.now()
    fname = f"crash_{now.strftime('%Y%m%d_%H%M%S')}.txt"
    report_path = os.path.join(_LOG_DIR, fname)

    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    sys_info = _gather_system_info()
    log_tail = _read_log_tail(_TAIL_LINES)

    lines: list[str] = [
        "=" * 70,
        "  OneClick Backup & Disk Manager -- CRASH REPORT",
        "=" * 70,
        "",
        f"Timestamp : {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Exception : {exc_type.__name__}: {exc_value}",
        "",
        "-" * 70,
        "  System Information",
        "-" * 70,
        sys_info,
        "",
        "-" * 70,
        "  Traceback",
        "-" * 70,
        tb_text,
        "-" * 70,
        f"  Last {_TAIL_LINES} log lines ({_APP_LOG_FILE})",
        "-" * 70,
        log_tail if log_tail else "(no log file found)",
        "",
        "=" * 70,
        "  End of crash report",
        "=" * 70,
        "",
    ]

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    _log.info("Crash report written to %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

def _show_crash_dialog(report_path: str) -> None:
    """Show a messagebox informing the user about the crash report.

    Silently does nothing if tkinter is unavailable (e.g. headless CI).
    """
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        messagebox.showerror(
            "OneClick Backup -- Unexpected Error",
            f"The application encountered an unexpected error and must close.\n\n"
            f"A crash report has been saved to:\n{report_path}\n\n"
            f"Please include this file if you report the issue.",
            parent=root,
        )
        root.destroy()
    except Exception:
        # Headless or tkinter missing -- fall through silently.
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gather_system_info() -> str:
    """Return a multi-line string of system / runtime details."""
    info_lines = [
        f"  OS           : {platform.system()} {platform.version()}",
        f"  OS Release   : {platform.release()}",
        f"  Architecture : {platform.machine()}",
        f"  Python       : {platform.python_version()} ({sys.executable})",
        f"  Platform     : {platform.platform()}",
    ]

    try:
        from src import __version__
        info_lines.append(f"  App Version  : {__version__}")
    except Exception:
        info_lines.append("  App Version  : (unknown)")

    return "\n".join(info_lines)


def _read_log_tail(n: int) -> str:
    """Return the last *n* lines of the application log file."""
    if not os.path.isfile(_APP_LOG_FILE):
        return ""

    try:
        with open(_APP_LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        tail = all_lines[-n:] if len(all_lines) > n else all_lines
        return "".join(tail).rstrip()
    except OSError:
        return ""


def _parse_timestamp_from_filename(fname: str) -> str:
    """Extract an ISO timestamp from a filename like ``crash_20260520_143025.txt``."""
    # Strip prefix and suffix: "crash_20260520_143025.txt" -> "20260520_143025"
    stem = fname.removeprefix("crash_").removesuffix(".txt")
    try:
        dt = datetime.strptime(stem, "%Y%m%d_%H%M%S")
        return dt.isoformat()
    except ValueError:
        return stem


def _extract_summary(path: str) -> str:
    """Read the first meaningful line from a crash report file.

    Skips header separators and blank lines, returning the ``Exception :``
    line when found.
    """
    try:
        fallback: str | None = None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("=") or stripped.startswith("-"):
                    continue
                if stripped.startswith("Exception"):
                    return stripped
                # Remember first substantive line as fallback, keep looking
                # for an "Exception" line.
                if fallback is None:
                    fallback = stripped
        if fallback is not None:
            return fallback
    except OSError:
        pass
    return "(unreadable)"
