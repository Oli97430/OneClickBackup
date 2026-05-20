<p align="center">
  <img src="assets/logo.png" alt="OneClick Backup" width="180">
</p>

<h1 align="center">OneClick Backup & Disk Manager</h1>

<p align="center">
  <strong>A modern, all-in-one Windows disk management and backup utility.</strong><br>
  Built with Python &amp; CustomTkinter — dark-themed, multi-language, admin-aware.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/platform-Windows-0078D6?style=flat-square&logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/version-1.3.0-6366f1?style=flat-square" alt="v1.3.0">
  <img src="https://img.shields.io/badge/tests-714%20passing-brightgreen?style=flat-square" alt="714 tests">
  <img src="https://img.shields.io/badge/pyright-0%20errors-blue?style=flat-square" alt="pyright 0 errors">
</p>

---

## Overview

**OneClick Backup** provides a professional desktop interface for managing disks, partitions, backups, and system cloning on Windows. It combines the power of Windows management tools (diskpart, PowerShell, WMI, wbadmin, robocopy, 7-Zip) behind an intuitive dark-themed GUI.

### Key Features

| Category | Features |
|:---------|:---------|
| **Dashboard** | Real-time disk visualization, partition bars, SMART health, multi-disk selection, drag & drop |
| **Backup** | Full / incremental / system / partition backups, ZIP compression, AES-256 encryption, cloud sync (OneDrive/GDrive/Dropbox) |
| **Scheduling** | Daily / weekly / monthly via Windows Task Scheduler, history tracking with export |
| **Disk Cloning** | Full disk clone, OS-only migration, network cloning via UNC paths |
| **Partitions** | Create, resize, merge, format, delete, change drive letters |
| **Disk Conversion** | MBR ↔ GPT, Basic ↔ Dynamic, NTFS ↔ FAT32 |
| **Recovery** | Quick & deep partition scan, filesystem signature detection |
| **Health** | S.M.A.R.T. monitoring, benchmarks (sequential/random IO), surface tests |
| **Disk Imaging** | VHD / VHDX / IMG creation, mount, convert |
| **Security** | Secure wipe (DoD 5220.22-M), System Restore Points |
| **Advanced** | WinPE bootable USB, defragmentation, HTML reports, auto-updater |
| **Accessibility** | Dark / light / high-contrast themes, Tab-focusable, WCAG compliance |
| **i18n** | 5 languages (EN, FR, ES, DE, AR with RTL), instant hot-switching |
| **Distribution** | Standalone EXE, Inno Setup installer, CLI mode, portable mode, system tray |

---

## Quick Start

### Option A: Standalone Executable

Download `OneClickBackup.exe` from the [Releases](../../releases/latest) page. No Python required.

### Option B: Run from Source

```bash
git clone https://github.com/Oli97430/OneClickBackup.git
cd OneClickBackup
pip install -r requirements.txt
python main.py
```

### Option C: Build the EXE

```bash
python build.py           # Release build (no console)
python build.py --debug   # Debug build (with console)
```

### Option D: CLI Mode

```bash
python main.py --cli backup --type system --dest E:\Backups
python main.py --cli list-disks
python main.py --cli benchmark --disk 0
python main.py --cli smart --disk 0
```

---

## Requirements

| Dependency       | Version  | Purpose                          |
|:-----------------|:---------|:---------------------------------|
| Python           | >= 3.10  | Runtime                          |
| customtkinter    | >= 5.2.0 | Modern dark-themed UI framework  |
| psutil           | >= 5.9.0 | Disk usage & system metrics      |
| wmi              | >= 1.5.1 | Windows Management Instrumentation |
| pywin32          | >= 306   | Windows COM/API bindings         |
| Pillow           | >= 10.0  | Logo/icon generation             |

```bash
pip install -r requirements.txt
```

---

## Project Structure

```
OneClickBackup/
├── main.py                   # Entry point
├── build.py                  # PyInstaller build script (v1.3.0)
├── installer.iss             # Inno Setup installer script
├── sign.ps1                  # Code signing scaffold
├── pyrightconfig.json        # Type checking config
│
├── src/
│   ├── core/
│   │   ├── backup.py         # Backup engine (create/restore/verify/delete/encrypt/compress)
│   │   ├── clone.py          # Disk clone & OS migration mixin
│   │   ├── winpe.py          # WinPE bootable media mixin
│   │   ├── disk_info.py      # WMI/PowerShell disk scanning with async support
│   │   ├── disk_health.py    # SMART, benchmarks, surface tests
│   │   ├── disk_image.py     # VHD/VHDX/IMG creation & conversion
│   │   ├── operations.py     # Queued disk operations (preview-before-apply)
│   │   ├── recovery.py       # Partition recovery via signature detection
│   │   ├── scheduler.py      # Windows Task Scheduler integration
│   │   ├── secure_wipe.py    # DoD 5220.22-M multi-pass wipe
│   │   ├── cloud_backup.py   # Cloud sync folder backup
│   │   ├── history.py        # Backup history persistence
│   │   └── updater.py        # Auto-update from GitHub Releases
│   │
│   ├── ui/
│   │   ├── app.py            # Main window, sidebar, theme toggle, system tray
│   │   ├── dashboard.py      # Disk overview, partition bars, multi-disk selection
│   │   ├── pages.py          # 9 feature pages (clone, backup, recovery, scheduler, etc.)
│   │   └── widgets.py        # Shared widgets, 3 color palettes, accessibility
│   │
│   └── utils/
│       ├── helpers.py        # PS/diskpart wrappers, sanitization, formatting
│       ├── admin.py          # UAC elevation & admin checks
│       ├── i18n.py           # Translation system (5 languages, auto-locale)
│       ├── settings.py       # Thread-safe JSON settings, portable mode
│       ├── cli.py            # CLI argument parsing & command dispatch
│       ├── crash_report.py   # Exception handler with path redaction
│       ├── notifications.py  # Windows toast notifications
│       └── report.py         # HTML report generation
│
├── tests/                    # 714 tests
│   ├── test_backup.py        # Backup engine tests
│   ├── test_security.py      # Security hardening tests (75)
│   ├── test_integration.py   # Cross-module integration tests (92)
│   ├── test_coverage_boost.py # Coverage-targeted tests (71)
│   ├── test_coverage_extra.py # Module-specific coverage (165)
│   ├── test_new_modules.py   # New module unit tests (105)
│   └── ...                   # Per-module unit tests
│
└── .github/workflows/ci.yml  # CI: lint, test (3 Python versions), build, release
```

---

## Security

The codebase has been hardened through two comprehensive code reviews:

- **PowerShell injection prevention** — All user-supplied strings are escaped via `sanitize_ps_string()` before interpolation into PS commands
- **Zip Slip protection** — Archive extraction validates all member paths stay within the destination
- **Path traversal guards** — `os.path.realpath()` boundary checks on backup deletion, cloud upload, and remote delete
- **Password redaction** — 7-Zip passwords are never logged; `_redact_command()` strips `-p*` args
- **Update integrity** — Downloaded EXEs are verified via SHA-256 hash or PE header check
- **Thread safety** — All background-to-UI data passes through `tkinter.after()`; Settings use `threading.Lock`
- **Input validation** — Drive letters, task names, schedule times, and file systems are regex-validated
- **Crash report privacy** — Usernames are redacted from tracebacks and log excerpts

---

## Architecture

- **Preview-before-apply** — Disk operations are queued, reviewed, then batch-executed
- **Lazy page loading** — Pages instantiated on first navigation
- **Batched WMI queries** — 3 calls for all disks instead of N*4 per disk
- **Cache with TTL** — Disk scan results cached for 5 seconds
- **Async scanning** — `asyncio.run_in_executor` for non-blocking disk enumeration
- **Mixin composition** — `BackupManager` = `BackupManager` + `CloneMixin` + `WinPEMixin`
- **Graceful degradation** — Admin features disabled (not hidden) when running unprivileged

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing

# Type checking
pyright src/
```

**714 tests** | **0 pyright errors** | Security, integration, and unit test suites

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Run tests (`pytest tests/`)
4. Commit your changes
5. Open a Pull Request

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built with Python, CustomTkinter, and a lot of work</sub>
</p>
