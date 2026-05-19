"""OneClick Backup & Disk Manager - Entry Point.

Launch the application, checking for dependencies and optionally
requesting administrator privileges.
"""

import sys
import os

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
    from src.utils.admin import is_admin

    if not is_admin():
        print("Note: Running without administrator privileges. Some features will be limited.")
        print("Right-click and 'Run as administrator' for full functionality.\n")

    missing = _check_dependencies()
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        print("Run:  pip install -r requirements.txt")
        input("Press Enter to exit...")
        sys.exit(1)

    # Launch the GUI
    from src.ui.app import OneClickBackupApp

    app = OneClickBackupApp()
    app.mainloop()


if __name__ == "__main__":
    main()
