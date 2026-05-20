"""OneClick Backup & Disk Manager - Entry Point.

Launch the application, checking for dependencies and optionally
requesting administrator privileges.
"""

from __future__ import annotations

import logging
import sys
import os


def _setup_logging() -> None:
    """Configure application-wide logging."""
    log_format = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_format, date_format))

    # Root logger
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)

    # File handler (optional, in user home)
    try:
        log_dir = os.path.join(os.path.expanduser("~"), ".oneclickbackup_logs")
        os.makedirs(log_dir, exist_ok=True)
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=5_000_000,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(log_format, date_format))
        root.addHandler(file_handler)
    except Exception:
        pass


_setup_logging()

# Ensure the project root is on the import path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _check_dependencies() -> list[str]:
    """Return a list of missing third-party packages."""
    missing: list[str] = []
    for pkg in ("customtkinter", "psutil"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def main() -> None:
    _log = logging.getLogger(__name__)

    # Install crash handler early
    try:
        from src.utils.crash_report import install_crash_handler
        install_crash_handler()
    except Exception:
        pass

    # CLI mode: if command-line arguments are provided, run in CLI mode
    if len(sys.argv) > 1:
        try:
            from src.utils.cli import run_cli
            exit_code = run_cli(sys.argv[1:])
            sys.exit(exit_code)
        except ImportError:
            _log.warning("CLI module not available.")
        except SystemExit:
            raise
        except Exception as exc:
            _log.error("CLI error: %s", exc)
            _sys.exit(1)

    from src.utils.admin import is_admin

    if not is_admin():
        _log.warning(
            "Running without administrator privileges. Some features will be limited."
        )
        _log.info("Right-click and 'Run as administrator' for full functionality.")

    missing = _check_dependencies()
    if missing:
        _log.error("Missing dependencies: %s", ", ".join(missing))
        _log.error("Run:  pip install -r requirements.txt")
        # In --noconsole PyInstaller builds, input() blocks forever
        # because there is no console. Use a messagebox if tkinter is
        # available, otherwise just exit.
        try:
            import tkinter as _tk
            from tkinter import messagebox as _mb
            _root = _tk.Tk()
            _root.withdraw()
            _mb.showerror(
                "Missing Dependencies",
                f"Required packages not found:\n{', '.join(missing)}\n\n"
                "Run:  pip install -r requirements.txt",
            )
            _root.destroy()
        except Exception:
            pass
        sys.exit(1)

    # Launch the GUI
    from src.ui.app import OneClickBackupApp

    app = OneClickBackupApp()
    app.mainloop()


if __name__ == "__main__":
    main()
