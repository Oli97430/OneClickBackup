# Changelog

All notable changes to OneClick Backup & Disk Manager are documented in this file.

## [1.3.0] — 2026-05-20

### Security Hardening (29 findings fixed)
- **Zip Slip protection** in decompress_backup — validates all archive member paths
- **PowerShell injection prevention** — `sanitize_ps_string()` applied across 6 modules (20+ sites)
- **Path traversal guards** on backup deletion, cloud upload/delete with `os.path.realpath()` checks
- **Password redaction** — 7-Zip passwords never logged; `_redact_command()` strips `-p*` args
- **Update integrity** — SHA-256 hash verification or PE header check on downloaded EXEs
- **Thread-safety fixes** — all background-to-UI data routed through `tkinter.after()`
- **System disk guard** — prevents cloning onto the running OS disk
- **Batch injection prevention** in WinPE — shell metacharacter validation
- **Closure capture fix** in secure_wipe — `pass_idx` bound by value in callbacks
- **Settings** made thread-safe with `threading.Lock` + JSON type validation on load
- **Crash reports** redact usernames from paths
- **Notifications** validate `app_id` against safe character pattern
- **Admin** uses `subprocess.list2cmdline` for proper argument quoting

### New Features (v1.2.0 + v1.3.0)
- **Scheduler** — daily/weekly/monthly backup scheduling via Windows Task Scheduler
- **History** — backup history tracking with search, export, and clear
- **Cloud backup** — sync-folder detection for OneDrive, Google Drive, Dropbox
- **SMART monitoring** — full S.M.A.R.T. attribute parsing via WMI
- **Disk benchmarks** — sequential/random IO throughput measurement
- **Surface tests** — sector-by-sector read verification
- **Disk imaging** — VHD/VHDX/IMG creation, mounting, and conversion
- **Partition recovery** — quick & deep scan with filesystem signature detection
- **Secure wipe** — DoD 5220.22-M multi-pass erasure
- **Incremental backup** — robocopy `/MAXAGE` differential mode
- **Compression** — ZIP (deflate) with progress tracking
- **Encryption** — AES-256 via 7-Zip command-line
- **Network cloning** — UNC path support for LAN-based backups
- **System Restore Points** — create via `Checkpoint-Computer`
- **HTML reports** — disk health reports with inline CSS
- **Auto-updater** — check GitHub Releases API with SHA-256 verification
- **CLI mode** — `--cli` flag for headless operations (backup, list-disks, benchmark, SMART)
- **System tray** — minimize to tray with pystray (optional)
- **Toast notifications** — Windows toast via PowerShell
- **Portable mode** — `.portable` file detection for USB-based settings
- **Multi-disk selection** — checkbox-based selection in dashboard
- **Drag & drop** — tkdnd support with tkinter fallback
- **Async disk scanning** — `asyncio.run_in_executor` for non-blocking enumeration
- **Dark / light / high-contrast themes** — three palettes with WCAG compliance
- **Backup selection** — clickable rows in backup list with visual highlighting

### Testing
- **714 tests** (was 206) across 9 test files
- 75 security hardening tests covering every defense mechanism
- 92 integration tests for cross-module workflows
- 165 coverage boost tests for parsers, validators, and helpers
- **0 pyright errors** (was unchecked), 42 acceptable warnings

### Infrastructure
- Pyright type-checking config (`pyrightconfig.json`)
- Inno Setup installer script (`installer.iss`)
- Code signing scaffold (`sign.ps1`)
- CI updated: lint + pyright + test (3 Python versions) + coverage + build + release
- `TYPE_CHECKING` stubs for mixin classes (CloneMixin, WinPEMixin)

### Bug Fixes
- `_get_os_version` called as method instead of module function
- `rstrip(".zip")` stripped wrong characters — now `removesuffix(".zip")`
- `_VALID_FILE_SYSTEMS` mixed case prevented exFAT/ReFS validation
- `proc` possibly unbound in robocopy progress tracking
- `_draw` override signature incompatible with CTkFrame
- `ScheduledTask` constructor called with wrong parameter names
- `deep_scan`/`quick_scan` callback parameter name mismatch
- Crash report summary preferred title line over Exception line
- Full `C:\` tree walk replaced with `shutil.disk_usage().used`
- Brace escaping in disk_health surface test PS script

## [1.1.0] — 2026-05-19

### Architecture
- Split `backup.py` (1759 lines) into modular files using mixin pattern: `clone.py` (CloneMixin), `winpe.py` (WinPEMixin)
- Removed ~1400 lines of duplicated widget code from `dashboard.py` — now imports from `widgets.py` as single source of truth
- Added `_get()` helper for transparent dict/dataclass field access
- Added `_health_color()` to widgets for consistent health status coloring

### Thread Safety
- Added `threading.Lock` to i18n module (`_lang_lock`) — protects `_current_lang` reads/writes in `t()`, `set_language()`, `get_language()`
- Added `threading.Lock` to disk_info module (`_cache_lock`) — protects `_disk_cache` and `_cache_timestamp` in `get_all_disks()` and `refresh_disk_info()`
- Cache reads now return `list(_disk_cache)` copy to prevent caller mutations

### Internationalization
- Auto-detect system locale on first launch (was hardcoded to French)
- Language priority: saved preference → system locale → English fallback
- Added `_detect_system_locale()` using `locale.getdefaultlocale()`

### Logging
- Added structured logging with `RotatingFileHandler` (5 MB, 3 backups) to `~/.oneclickbackup_logs/app.log`
- Console handler at INFO level, file handler at DEBUG level
- Replaced all `print()` calls in admin.py and main.py with proper logger calls

### Type Hints
- Added `from __future__ import annotations` across all modules
- Modernized types: `Optional[str]` → `str | None`, `Tuple[...]` → `tuple[...]`
- Added return type annotations to all 20+ OperationManager methods
- Moved `Callable` imports from `typing` to `collections.abc`

### UI & Accessibility
- Added `Tooltip` widget class for hover tooltips on any widget
- Added global keyboard shortcuts: `Ctrl+1`–`Ctrl+7` for page navigation, `F5` for refresh, `Ctrl+Q` to quit
- Status bar shows warning when running without administrator privileges

### Testing
- Added 206+ unit tests across 7 test files
- Tests cover: helpers, i18n, disk_info, operations, widgets, backup, conftest fixtures
- Added `pyproject.toml` with pytest configuration
- Added `conftest.py` with shared fixtures

### Project Configuration
- Created `pyproject.toml` with full project metadata, dev dependencies, pytest/ruff/pyright config
- Added proper docstrings to all `__init__.py` files
- Updated `.gitignore` for pytest cache, coverage, and log directories
- Improved `install.bat` to match professional quality of `launch.bat`

## [1.0.0] — 2026-05-18

### Initial Release
- Dashboard with real-time disk visualization (gradient partition bars, health status)
- Disk cloning: full disk clone and OS-only migration
- Partition management: create, resize, merge, format, delete, change drive letters
- Backup & restore: full disk images, partition backups, system state backups with checksums
- Disk conversion: MBR ↔ GPT, Basic ↔ Dynamic, NTFS ↔ FAT32
- Partition recovery: scan and recover lost/deleted partitions
- Advanced tools: WinPE bootable USB, 4K alignment check, disk health reports
- 6 languages: English, French, Spanish, German, Arabic (RTL), Chinese Simplified
- UAC-aware: works in limited mode with optional admin elevation
- Midnight Operations dark theme with indigo/teal accents
- PyInstaller standalone EXE build
- Professional logo and multi-size icon
