# Changelog

All notable changes to OneClick Backup & Disk Manager are documented in this file.

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
