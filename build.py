"""Build standalone OneClick Backup executable using PyInstaller.

Usage:
    python build.py          # Build release EXE
    python build.py --debug  # Build with console window for debugging

Output:
    dist/OneClickBackup.exe  - Single-file Windows executable
"""

import os
import subprocess
import shutil
import sys
import io

# Fix Windows console encoding for box-drawing characters
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(PROJECT_ROOT, "assets", "icon.ico")
MAIN_SCRIPT = os.path.join(PROJECT_ROOT, "main.py")
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")

APP_NAME = "OneClickBackup"
APP_VERSION = "1.0.0"


def check_pyinstaller():
    """Ensure PyInstaller is installed."""
    try:
        import PyInstaller
        print(f"  [OK] PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("  [~] Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "--quiet"])
        print("  [OK] PyInstaller installed.")


def check_icon():
    """Verify the icon file exists, generate it if missing."""
    if os.path.isfile(ICON_PATH):
        print(f"  [OK] Icon found: {ICON_PATH}")
        return True

    print("  [~] Icon not found, generating...")
    logo_script = os.path.join(PROJECT_ROOT, "generate_logo.py")
    if os.path.isfile(logo_script):
        subprocess.check_call([sys.executable, logo_script])
        if os.path.isfile(ICON_PATH):
            print(f"  [OK] Icon generated: {ICON_PATH}")
            return True

    print("  [!] Could not generate icon, building without it.")
    return False


def clean():
    """Remove previous build artifacts."""
    for d in (DIST_DIR, BUILD_DIR):
        if os.path.isdir(d):
            shutil.rmtree(d)
            print(f"  [OK] Cleaned {d}")

    spec_file = os.path.join(PROJECT_ROOT, f"{APP_NAME}.spec")
    if os.path.isfile(spec_file):
        os.remove(spec_file)


def build(debug=False):
    """Run PyInstaller to create the executable."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        f"--name={APP_NAME}",
        "--noconsole" if not debug else "--console",
        "--clean",

        # Include all source packages
        "--add-data", f"src{os.pathsep}src",
        "--add-data", f"assets{os.pathsep}assets",

        # Hidden imports that PyInstaller may miss
        "--hidden-import", "customtkinter",
        "--hidden-import", "psutil",
        "--hidden-import", "wmi",
        "--hidden-import", "win32com",
        "--hidden-import", "win32api",
        "--hidden-import", "pythoncom",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "PIL.ImageDraw",
        "--hidden-import", "PIL.ImageFont",

        # Exclude unnecessary modules to reduce size
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--exclude-module", "pytest",
        "--exclude-module", "unittest",
    ]

    if os.path.isfile(ICON_PATH):
        cmd.extend(["--icon", ICON_PATH])

    cmd.append(MAIN_SCRIPT)

    print(f"\n  Running PyInstaller...")
    print(f"  Command: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)

    if result.returncode != 0:
        print(f"\n  [ERREUR] Build failed with code {result.returncode}")
        sys.exit(1)

    exe_path = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
    if os.path.isfile(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\n  ╔══════════════════════════════════════════════╗")
        print(f"  ║  Build successful!                            ║")
        print(f"  ╠══════════════════════════════════════════════╣")
        print(f"  ║  Output: dist/{APP_NAME}.exe")
        print(f"  ║  Size:   {size_mb:.1f} MB")
        print(f"  ╚══════════════════════════════════════════════╝")
    else:
        print(f"\n  [ERREUR] EXE not found at {exe_path}")
        sys.exit(1)


def main():
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print(f"  ║  {APP_NAME} Builder v{APP_VERSION}              ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()

    debug = "--debug" in sys.argv

    print("  Checking prerequisites...")
    check_pyinstaller()
    has_icon = check_icon()

    print("\n  Cleaning previous builds...")
    clean()

    print(f"\n  Building {'debug' if debug else 'release'} executable...")
    build(debug=debug)


if __name__ == "__main__":
    main()
