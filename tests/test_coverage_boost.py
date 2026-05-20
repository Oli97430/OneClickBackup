"""Targeted tests to improve coverage for key modules.

Focuses on: updater, cli, crash_report, notifications, helpers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from io import StringIO
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ======================================================================
# Shared fakes
# ======================================================================

@dataclass
class _FakePartition:
    index: int = 0
    letter: str = ""
    label: str = ""
    file_system: str = ""
    size_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    partition_type: str = ""
    is_active: bool = False
    is_boot: bool = False
    is_system: bool = False


@dataclass
class _FakeDisk:
    index: int = 0
    model: str = "Test Disk"
    size_bytes: int = 500_000_000_000
    media_type: str = "SSD"
    interface_type: str = "NVMe"
    partition_style: str = "GPT"
    health_status: str = "Healthy"
    is_system_disk: bool = True
    is_4k_aligned: bool = True
    unallocated_bytes: int = 0
    partitions: list = field(default_factory=list)


@dataclass
class _FakeBackupInfo:
    backup_id: str = "bk-001"
    name: str = "Test Backup"
    backup_type: str = "system"
    compressed_size_bytes: int = 1_000_000
    timestamp: str = "20260520_120000"


# ======================================================================
# 1. Updater: download_update + apply_update coverage
# ======================================================================

class TestUpdaterDownload(unittest.TestCase):
    """Test download_update with mocked HTTP."""

    @patch("src.core.updater.urlopen")
    def test_download_update_success(self, mock_urlopen):
        from urllib.error import URLError
        from src.core.updater import AutoUpdater, UpdateInfo

        # Create a mock response that returns file data with MZ header
        # so PE verification passes when no SHA-256 hash is available.
        exe_data = b"MZ" + b"fakebinarydata" * 100
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [exe_data, b""]
        mock_resp.headers = {"Content-Length": str(len(exe_data))}
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            mock_resp,                       # download
            URLError("not found"),           # .sha256 sidecar
            URLError("not found"),           # .sha256sum sidecar
        ]

        info = UpdateInfo(
            current_version="1.0.0",
            latest_version="2.0.0",
            is_update_available=True,
            release_url="https://github.com/test",
            download_url="https://github.com/test/OneClickBackup.exe",
            release_notes="New",
            published_at="2026-01-01",
            file_size=len(exe_data),
        )

        updater = AutoUpdater(current_version="1.0.0")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = updater.download_update(info, dest_dir=tmpdir)
            self.assertTrue(os.path.isfile(result))
            self.assertIn("OneClickBackup.exe", result)

    @patch("src.core.updater.urlopen")
    def test_download_update_with_callback(self, mock_urlopen):
        from urllib.error import URLError
        from src.core.updater import AutoUpdater, UpdateInfo

        exe_data = b"MZ" + b"data" * 100
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [exe_data, b""]
        mock_resp.headers = {"Content-Length": str(len(exe_data))}
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            mock_resp,                       # download
            URLError("not found"),           # .sha256 sidecar
            URLError("not found"),           # .sha256sum sidecar
        ]

        info = UpdateInfo(
            current_version="1.0.0",
            latest_version="2.0.0",
            is_update_available=True,
            release_url="",
            download_url="https://example.com/app.exe",
            release_notes="",
            published_at="",
            file_size=len(exe_data),
        )

        updater = AutoUpdater(current_version="1.0.0")
        progress = []
        with tempfile.TemporaryDirectory() as tmpdir:
            updater.download_update(
                info, dest_dir=tmpdir,
                callback=lambda pct, msg: progress.append((pct, msg)),
            )
        self.assertTrue(len(progress) >= 2)  # at least start + end

    def test_download_update_no_url_raises(self):
        from src.core.updater import AutoUpdater, UpdateInfo

        info = UpdateInfo(
            current_version="1.0.0",
            latest_version="2.0.0",
            is_update_available=True,
            release_url="",
            download_url="",
            release_notes="",
            published_at="",
            file_size=0,
        )

        updater = AutoUpdater(current_version="1.0.0")
        with self.assertRaises(RuntimeError):
            updater.download_update(info)

    @patch("src.core.updater.urlopen")
    def test_download_update_default_dest(self, mock_urlopen):
        """download_update uses temp dir when no dest_dir given."""
        from urllib.error import URLError
        from src.core.updater import AutoUpdater, UpdateInfo

        # The download response returns a minimal MZ header so PE
        # verification passes when no SHA-256 hash is available.
        exe_bytes = b"MZ" + b"\x00" * 10
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [exe_bytes, b""]
        mock_resp.headers = {"Content-Length": str(len(exe_bytes))}
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        # urlopen is called once for the download, then again for each
        # .sha256 sidecar lookup (which should fail with URLError).
        mock_urlopen.side_effect = [
            mock_resp,                       # download
            URLError("not found"),           # .sha256 sidecar
            URLError("not found"),           # .sha256sum sidecar
        ]

        info = UpdateInfo(
            current_version="1.0.0",
            latest_version="2.0.0",
            is_update_available=True,
            release_url="",
            download_url="https://example.com/app.exe",
            release_notes="",
            published_at="",
            file_size=len(exe_bytes),
        )

        updater = AutoUpdater(current_version="1.0.0")
        result = updater.download_update(info)
        self.assertTrue(os.path.isfile(result))
        os.remove(result)  # cleanup


class TestUpdaterApply(unittest.TestCase):
    """Test apply_update logic."""

    def test_apply_update_not_exe_returns(self):
        """apply_update returns early if not running as .exe."""
        from src.core.updater import AutoUpdater

        updater = AutoUpdater(current_version="1.0.0")
        # sys.executable is python.exe -- mock it to be non-.exe
        with patch("sys.executable", "/usr/bin/python3"):
            # Should return without calling sys.exit since it's not an .exe
            updater.apply_update("/fake/path.exe")
            # If we get here, it means apply_update returned gracefully


# ======================================================================
# 2. CLI: command dispatch coverage
# ======================================================================

class TestCliListBackups(unittest.TestCase):
    """Test --list-backups CLI command."""

    @patch("src.core.backup.BackupManager")
    def test_list_backups_empty(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_mgr = MagicMock()
        mock_mgr.list_backups.return_value = []
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--list-backups"])
        self.assertEqual(rc, 0)
        self.assertIn("No backups found", captured.getvalue())

    @patch("src.core.backup.BackupManager")
    def test_list_backups_with_entries(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_mgr = MagicMock()
        mock_mgr.list_backups.return_value = [_FakeBackupInfo()]
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--list-backups"])
        self.assertEqual(rc, 0)
        self.assertIn("bk-001", captured.getvalue())


class TestCliHealth(unittest.TestCase):
    """Test --health CLI command in various configurations."""

    @patch("src.core.disk_info.get_all_disks")
    @patch("src.core.disk_info.get_disk_health", return_value="Healthy")
    def test_health_with_multiple_disks(self, mock_health, mock_disks):
        from src.utils.cli import run_cli

        disks = [
            _FakeDisk(index=0, model="SSD 1"),
            _FakeDisk(index=1, model="HDD 2"),
        ]
        mock_disks.return_value = disks

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--health"])
        self.assertEqual(rc, 0)
        output = captured.getvalue()
        self.assertIn("SSD 1", output)
        self.assertIn("HDD 2", output)

    @patch("src.core.disk_info.get_all_disks", return_value=[])
    def test_health_no_disks(self, mock_disks):
        from src.utils.cli import run_cli

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--health"])
        self.assertEqual(rc, 0)
        self.assertIn("No disks found", captured.getvalue())


class TestCliListDisksWithData(unittest.TestCase):
    """Test --list-disks with actual disk data."""

    @patch("src.core.disk_info.get_all_disks")
    def test_list_disks_with_data(self, mock_disks):
        from src.utils.cli import run_cli

        disk = _FakeDisk(
            index=0, model="Test SSD",
            partitions=[_FakePartition(index=1)],
        )
        mock_disks.return_value = [disk]

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--list-disks"])
        self.assertEqual(rc, 0)
        output = captured.getvalue()
        self.assertIn("Test SSD", output)
        self.assertIn("#", output)  # header


class TestCliErrorHandling(unittest.TestCase):
    """Test CLI error handling paths."""

    @patch("src.core.disk_info.get_all_disks", side_effect=Exception("disk error"))
    def test_exception_returns_error(self, mock_disks):
        from src.utils.cli import run_cli
        rc = run_cli(["--list-disks"])
        self.assertEqual(rc, 1)

    @patch("src.core.disk_info.get_all_disks", side_effect=KeyboardInterrupt)
    def test_keyboard_interrupt(self, mock_disks):
        from src.utils.cli import run_cli
        rc = run_cli(["--list-disks"])
        self.assertEqual(rc, 130)

    def test_print_err(self):
        from src.utils.cli import _print_err
        captured = StringIO()
        with patch("sys.stderr", captured):
            _print_err("test error msg")
        self.assertIn("test error msg", captured.getvalue())


class TestCliVerifyBackup(unittest.TestCase):
    """Test --verify-backup CLI command."""

    @patch("src.core.backup.BackupManager")
    def test_verify_backup_pass(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_mgr = MagicMock()
        mock_mgr.verify_backup.return_value = True
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--verify-backup", "bk-001"])
        self.assertEqual(rc, 0)
        self.assertIn("PASSED", captured.getvalue())

    @patch("src.core.backup.BackupManager")
    def test_verify_backup_fail(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_mgr = MagicMock()
        mock_mgr.verify_backup.return_value = False
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--verify-backup", "bk-001"])
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", captured.getvalue())


class TestCliClone(unittest.TestCase):
    """Test --clone CLI command."""

    @patch("src.core.backup.BackupManager")
    def test_clone_same_disk_error(self, mock_mgr_cls):
        from src.utils.cli import run_cli
        rc = run_cli(["--clone", "0", "0"])
        self.assertEqual(rc, 1)

    @patch("src.core.backup.BackupManager")
    def test_clone_success(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_mgr = MagicMock()
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--clone", "0", "1"])
        self.assertEqual(rc, 0)
        self.assertIn("Clone complete", captured.getvalue())


class TestCliBackup(unittest.TestCase):
    """Test --backup CLI command."""

    @patch("src.core.backup.BackupManager")
    def test_backup_system(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_info = _FakeBackupInfo()
        mock_info.backup_path = "C:\\Backups\\test"
        mock_mgr = MagicMock()
        mock_mgr.create_system_backup.return_value = mock_info
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--backup", "system"])
        self.assertEqual(rc, 0)
        self.assertIn("Backup complete", captured.getvalue())

    @patch("src.core.backup.BackupManager")
    def test_backup_full_disk(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_info = _FakeBackupInfo()
        mock_info.backup_path = "C:\\Backups\\full"
        mock_mgr = MagicMock()
        mock_mgr.create_full_disk_backup.return_value = mock_info
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--backup", "full_disk"])
        self.assertEqual(rc, 0)

    @patch("src.core.backup.BackupManager")
    def test_backup_partition(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_info = _FakeBackupInfo()
        mock_info.backup_path = "C:\\Backups\\part"
        mock_mgr = MagicMock()
        mock_mgr.create_partition_backup.return_value = mock_info
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--backup", "partition"])
        self.assertEqual(rc, 0)


class TestCliScheduledBackup(unittest.TestCase):
    """Test --scheduled-backup CLI command."""

    @patch("src.core.backup.BackupManager")
    def test_scheduled_backup_no_config(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        mock_mgr = MagicMock()
        mock_mgr.backup_dir = tempfile.gettempdir()
        mock_mgr_cls.return_value = mock_mgr

        rc = run_cli(["--scheduled-backup", "nonexistent_schedule"])
        self.assertEqual(rc, 1)

    @patch("src.core.backup.BackupManager")
    def test_scheduled_backup_with_config(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_mgr = MagicMock()
            mock_mgr.backup_dir = tmpdir
            mock_info = _FakeBackupInfo()
            mock_info.backup_path = os.path.join(tmpdir, "backup")
            mock_mgr.create_system_backup.return_value = mock_info
            mock_mgr_cls.return_value = mock_mgr

            # Write a schedule config
            config_path = os.path.join(tmpdir, "schedule_test_sched.json")
            with open(config_path, "w") as f:
                json.dump({"backup_type": "system", "dest": tmpdir, "name": "test"}, f)

            captured = StringIO()
            with patch("sys.stdout", captured):
                rc = run_cli(["--scheduled-backup", "test_sched"])
            self.assertEqual(rc, 0)

    @patch("src.core.backup.BackupManager")
    def test_scheduled_backup_corrupt_config(self, mock_mgr_cls):
        from src.utils.cli import run_cli

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_mgr = MagicMock()
            mock_mgr.backup_dir = tmpdir
            mock_mgr_cls.return_value = mock_mgr

            config_path = os.path.join(tmpdir, "schedule_bad.json")
            with open(config_path, "w") as f:
                f.write("not valid json {{{{")

            rc = run_cli(["--scheduled-backup", "bad"])
            self.assertEqual(rc, 1)


class TestCliBenchmark(unittest.TestCase):
    """Test --benchmark CLI command."""

    @patch("os.path.isdir", return_value=False)
    def test_benchmark_invalid_drive(self, mock_isdir):
        from src.utils.cli import run_cli
        rc = run_cli(["--benchmark", "Z"])
        self.assertEqual(rc, 1)

    def test_benchmark_valid_drive(self):
        """Benchmark with a valid drive and a file to read."""
        from src.utils.cli import run_cli

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file >= 16 MB for benchmarking
            test_file = os.path.join(tmpdir, "large_file.bin")
            with open(test_file, "wb") as f:
                f.write(b"\x00" * (17 * 1024 * 1024))

            # _cmd_benchmark does `import os` locally, so we must patch
            # functions on the real os module, not on src.utils.cli.os.
            drive_letter = os.path.splitdrive(tmpdir)[0][0]  # e.g. "F"

            orig_isdir = os.path.isdir

            def fake_isdir(p):
                if p == f"{drive_letter}:\\":
                    return True
                return orig_isdir(p)

            with patch("os.path.isdir", side_effect=fake_isdir), \
                 patch("os.walk", return_value=iter([(tmpdir, [], ["large_file.bin"])])), \
                 patch("os.path.getsize", return_value=17 * 1024 * 1024):

                captured = StringIO()
                with patch("sys.stdout", captured):
                    rc = run_cli(["--benchmark", drive_letter])
                self.assertEqual(rc, 0)
                self.assertIn("MB/s", captured.getvalue())

    def test_benchmark_no_large_file(self):
        """Benchmark when no file >= 16 MB is found."""
        from src.utils.cli import run_cli

        with patch("os.path.isdir", return_value=True), \
             patch("os.walk", return_value=iter([])):

            rc = run_cli(["--benchmark", "X"])
            self.assertEqual(rc, 1)

    def test_benchmark_read_oserror(self):
        """Benchmark handles OSError when reading the file."""
        from src.utils.cli import run_cli

        with patch("os.path.isdir", return_value=True), \
             patch("os.walk", return_value=iter([("X:\\", [], ["big.dat"])])), \
             patch("os.path.getsize", return_value=20 * 1024 * 1024), \
             patch("os.path.join", return_value="X:\\big.dat"), \
             patch("builtins.open", side_effect=OSError("read failed")):

            rc = run_cli(["--benchmark", "X"])
            self.assertEqual(rc, 1)


class TestCliVersionFallback(unittest.TestCase):
    """Test --version when __version__ import fails."""

    def test_version_import_error(self):
        from src.utils.cli import _cmd_version
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            # Block 'from src import __version__' by detecting the fromlist
            if name == "src":
                fromlist = None
                if len(args) >= 3:
                    fromlist = args[2]
                elif "fromlist" in kwargs:
                    fromlist = kwargs["fromlist"]
                if fromlist and "__version__" in fromlist:
                    raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        captured = StringIO()
        with patch("sys.stdout", captured), \
             patch("builtins.__import__", side_effect=mock_import):
            rc = _cmd_version()

        self.assertEqual(rc, 0)
        output = captured.getvalue()
        self.assertIn("version unknown", output)


class TestCliProgressCallbackCoverage(unittest.TestCase):
    """Test that progress callbacks in CLI commands get called."""

    @patch("src.core.backup.BackupManager")
    def test_backup_progress_callback_invoked(self, mock_mgr_cls):
        """The _progress callback inside _cmd_backup prints correctly."""
        from src.utils.cli import run_cli

        mock_info = _FakeBackupInfo()
        mock_info.backup_path = "C:\\Backups\\test"
        mock_mgr = MagicMock()

        # Capture the progress callback and invoke it
        def capture_set_progress(cb):
            cb("Starting backup...", 50.0)

        mock_mgr.set_progress_callback.side_effect = capture_set_progress
        mock_mgr.create_system_backup.return_value = mock_info
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--backup", "system"])
        self.assertEqual(rc, 0)
        self.assertIn("50.0%", captured.getvalue())

    @patch("src.core.backup.BackupManager")
    def test_clone_progress_callback_invoked(self, mock_mgr_cls):
        """The _progress callback inside _cmd_clone prints correctly."""
        from src.utils.cli import run_cli

        mock_mgr = MagicMock()

        def capture_set_progress(cb):
            cb("Cloning...", 25.0)

        mock_mgr.set_progress_callback.side_effect = capture_set_progress
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--clone", "0", "1"])
        self.assertEqual(rc, 0)
        self.assertIn("25.0%", captured.getvalue())

    @patch("src.core.backup.BackupManager")
    def test_verify_progress_callback_invoked(self, mock_mgr_cls):
        """The _progress callback inside _cmd_verify_backup prints correctly."""
        from src.utils.cli import run_cli

        mock_mgr = MagicMock()

        def capture_set_progress(cb):
            cb("Verifying...", 75.0)

        mock_mgr.set_progress_callback.side_effect = capture_set_progress
        mock_mgr.verify_backup.return_value = True
        mock_mgr_cls.return_value = mock_mgr

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--verify-backup", "bk-001"])
        self.assertEqual(rc, 0)
        self.assertIn("75.0%", captured.getvalue())


# ======================================================================
# 3. Crash report: improve to 80%+
# ======================================================================

class TestCrashHookDirectly(unittest.TestCase):
    """Test _crash_hook directly to cover the hook body."""

    def test_crash_hook_writes_report(self):
        import src.utils.crash_report as crash_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(crash_mod, "_LOG_DIR", tmpdir), \
                 patch.object(crash_mod, "_APP_LOG_FILE", os.path.join(tmpdir, "app.log")), \
                 patch.object(crash_mod, "_show_crash_dialog"):

                try:
                    raise RuntimeError("test hook coverage")
                except RuntimeError:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    crash_mod._crash_hook(exc_type, exc_value, exc_tb)

                files = [f for f in os.listdir(tmpdir) if f.startswith("crash_")]
                self.assertGreaterEqual(len(files), 1)

    def test_crash_hook_inner_error(self):
        """If _write_crash_report fails, _crash_hook prints to stderr."""
        import src.utils.crash_report as crash_mod

        with patch.object(crash_mod, "_write_crash_report", side_effect=Exception("write fail")), \
             patch.object(crash_mod, "_show_crash_dialog"):

            captured = StringIO()
            with patch("sys.stderr", captured):
                try:
                    raise ValueError("outer")
                except ValueError:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    crash_mod._crash_hook(exc_type, exc_value, exc_tb)

            self.assertIn("CRASH", captured.getvalue())


class TestCrashWriteReport(unittest.TestCase):
    """Test _write_crash_report directly."""

    def test_write_crash_report_contents(self):
        import src.utils.crash_report as crash_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(crash_mod, "_LOG_DIR", tmpdir), \
                 patch.object(crash_mod, "_APP_LOG_FILE", os.path.join(tmpdir, "app.log")):

                try:
                    raise TypeError("coverage test error")
                except TypeError:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    path = crash_mod._write_crash_report(exc_type, exc_value, exc_tb)

                self.assertTrue(os.path.isfile(path))
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("TypeError", content)
                self.assertIn("coverage test error", content)
                self.assertIn("System Information", content)
                self.assertIn("Traceback", content)


class TestCrashGatherSystemInfo(unittest.TestCase):
    """Test _gather_system_info."""

    def test_gather_system_info(self):
        import src.utils.crash_report as crash_mod
        info = crash_mod._gather_system_info()
        self.assertIn("OS", info)
        self.assertIn("Python", info)

    def test_gather_system_info_with_version(self):
        import src.utils.crash_report as crash_mod
        info = crash_mod._gather_system_info()
        # Should include app version
        self.assertIn("App Version", info)


class TestCrashReadLogTail(unittest.TestCase):
    """Test _read_log_tail."""

    def test_read_log_tail_no_file(self):
        import src.utils.crash_report as crash_mod
        with patch.object(crash_mod, "_APP_LOG_FILE", "Z:\\nonexistent.log"):
            result = crash_mod._read_log_tail(10)
        self.assertEqual(result, "")

    def test_read_log_tail_with_file(self):
        import src.utils.crash_report as crash_mod

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            for i in range(100):
                f.write(f"Line {i}\n")
            fname = f.name

        try:
            with patch.object(crash_mod, "_APP_LOG_FILE", fname):
                result = crash_mod._read_log_tail(5)
            self.assertIn("Line 99", result)
            self.assertIn("Line 95", result)
        finally:
            os.remove(fname)


class TestCrashExtractSummaryEdgeCases(unittest.TestCase):
    """Cover edge cases in _extract_summary."""

    def test_extract_summary_no_exception_line(self):
        """File with no Exception line returns first substantive line."""
        import src.utils.crash_report as crash_mod

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("=" * 70 + "\n")
            f.write("\n")
            f.write("Some other content\n")
            fname = f.name
        try:
            result = crash_mod._extract_summary(fname)
            self.assertEqual(result, "Some other content")
        finally:
            os.remove(fname)

    def test_extract_summary_only_separators(self):
        """File with only separators returns fallback."""
        import src.utils.crash_report as crash_mod

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("=" * 70 + "\n")
            f.write("-" * 70 + "\n")
            f.write("\n")
            fname = f.name
        try:
            result = crash_mod._extract_summary(fname)
            self.assertEqual(result, "(unreadable)")
        finally:
            os.remove(fname)


# ======================================================================
# 4. Notifications: cover _send_toast and _fallback_notify
# ======================================================================

class TestNotificationsSendToast(unittest.TestCase):
    """Test _send_toast method."""

    @patch("src.utils.notifications.run_powershell", return_value=("", "", 0))
    def test_send_toast_success(self, mock_ps):
        from src.utils.notifications import NotificationManager

        nm = NotificationManager()
        result = nm._send_toast("Title", "Body")
        self.assertTrue(result)
        mock_ps.assert_called_once()
        # Verify the PS command contains the escaped title/body
        cmd = mock_ps.call_args[0][0]
        self.assertIn("Title", cmd)
        self.assertIn("Body", cmd)

    @patch("src.utils.notifications.run_powershell", return_value=("", "error", 1))
    def test_send_toast_failure(self, mock_ps):
        from src.utils.notifications import NotificationManager

        nm = NotificationManager()
        result = nm._send_toast("Title", "Body")
        self.assertFalse(result)

    @patch("src.utils.notifications.run_powershell", return_value=("", "", 0))
    def test_send_toast_escapes_special_chars(self, mock_ps):
        from src.utils.notifications import NotificationManager

        nm = NotificationManager()
        nm._send_toast("A & B <script>", "It's \"fine\"")
        cmd = mock_ps.call_args[0][0]
        self.assertIn("&amp;", cmd)
        self.assertIn("&lt;", cmd)


class TestNotificationsFallback(unittest.TestCase):
    """Test _fallback_notify."""

    @patch("threading.Thread")
    def test_fallback_notify_starts_thread(self, mock_thread_cls):
        from src.utils.notifications import NotificationManager

        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        NotificationManager._fallback_notify("Title", "Body", "info")
        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()


class TestNotificationsFullFlow(unittest.TestCase):
    """Test full notification flow: toast success, toast fail + fallback."""

    @patch("src.utils.notifications.run_powershell", return_value=("", "", 0))
    def test_notify_toast_success_no_fallback(self, mock_ps):
        from src.utils.notifications import NotificationManager

        nm = NotificationManager()
        with patch.object(nm, "_fallback_notify") as mock_fb:
            nm.notify("Title", "Body")
            mock_fb.assert_not_called()

    @patch("src.utils.notifications.run_powershell", return_value=("", "err", 1))
    def test_notify_toast_fail_calls_fallback(self, mock_ps):
        from src.utils.notifications import NotificationManager

        nm = NotificationManager()
        with patch.object(nm, "_fallback_notify") as mock_fb:
            nm.notify("Title", "Body", icon="warning")
            mock_fb.assert_called_once_with("Title", "Body", "warning")


class TestNotificationsFallbackShowInner(unittest.TestCase):
    """Exercise the _show() inner function inside _fallback_notify.

    Covers lines 147-164 of notifications.py by intercepting the
    thread target and running it synchronously with tkinter mocked
    via builtins.__import__.
    """

    def _capture_and_run_show(self, icon: str):
        """Capture the _show closure, run it with mocked tkinter."""
        from src.utils.notifications import NotificationManager
        import builtins

        # Step 1: capture the _show function from the Thread call
        captured_target = {}

        def fake_thread(target, daemon=True):
            captured_target["fn"] = target
            return MagicMock()

        with patch("src.utils.notifications.threading.Thread", side_effect=fake_thread):
            NotificationManager._fallback_notify("Title", "Body", icon)

        # Step 2: run _show with tkinter imports intercepted
        mock_root = MagicMock()
        mock_messagebox = MagicMock()
        mock_tk_module = MagicMock()
        mock_tk_module.Tk.return_value = mock_root

        original_import = builtins.__import__

        def patched_import(name, *args, **kwargs):
            if name == "tkinter":
                return mock_tk_module
            # "from tkinter import messagebox" calls __import__("tkinter.messagebox", ...)
            # but the actual "messagebox" is fetched as getattr on the tkinter module.
            # However, Python may also call __import__("tkinter.messagebox").
            if name == "tkinter.messagebox":
                return mock_tk_module
            return original_import(name, *args, **kwargs)

        # Temporarily remove tkinter from sys.modules so _show re-imports
        saved_tk = sys.modules.pop("tkinter", None)
        saved_mb = sys.modules.pop("tkinter.messagebox", None)
        try:
            # Put our mock in sys.modules so `from tkinter import messagebox`
            # resolves to our mock
            sys.modules["tkinter"] = mock_tk_module
            mock_tk_module.messagebox = mock_messagebox
            sys.modules["tkinter.messagebox"] = mock_messagebox

            captured_target["fn"]()
        finally:
            # Restore original modules
            sys.modules.pop("tkinter", None)
            sys.modules.pop("tkinter.messagebox", None)
            if saved_tk is not None:
                sys.modules["tkinter"] = saved_tk
            if saved_mb is not None:
                sys.modules["tkinter.messagebox"] = saved_mb

        return mock_root, mock_messagebox

    def test_show_info_icon(self):
        """_show with icon='info' calls showinfo."""
        mock_root, mock_mb = self._capture_and_run_show("info")
        mock_mb.showinfo.assert_called_once_with("Title", "Body", parent=mock_root)
        mock_root.destroy.assert_called_once()

    def test_show_error_icon(self):
        """_show with icon='error' calls showerror."""
        mock_root, mock_mb = self._capture_and_run_show("error")
        mock_mb.showerror.assert_called_once_with("Title", "Body", parent=mock_root)
        mock_root.destroy.assert_called_once()

    def test_show_warning_icon(self):
        """_show with icon='warning' calls showwarning."""
        mock_root, mock_mb = self._capture_and_run_show("warning")
        mock_mb.showwarning.assert_called_once_with("Title", "Body", parent=mock_root)
        mock_root.destroy.assert_called_once()

    def test_show_tkinter_exception(self):
        """_show handles tkinter ImportError gracefully."""
        from src.utils.notifications import NotificationManager

        captured_target = {}

        def fake_thread(target, daemon=True):
            captured_target["fn"] = target
            return MagicMock()

        with patch("src.utils.notifications.threading.Thread", side_effect=fake_thread):
            NotificationManager._fallback_notify("T", "M", "info")

        # Remove tkinter from sys.modules and put a broken one
        saved_tk = sys.modules.pop("tkinter", None)
        saved_mb = sys.modules.pop("tkinter.messagebox", None)
        try:
            # Make tkinter import raise inside _show
            import builtins
            original_import = builtins.__import__

            def bad_import(name, *args, **kwargs):
                if name == "tkinter" or name.startswith("tkinter."):
                    raise ImportError("no tkinter")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=bad_import):
                # Should not raise -- the inner try/except catches it
                captured_target["fn"]()
        finally:
            if saved_tk is not None:
                sys.modules["tkinter"] = saved_tk
            if saved_mb is not None:
                sys.modules["tkinter.messagebox"] = saved_mb


class TestNotificationsConvenienceMethods(unittest.TestCase):
    """Cover notify_backup_complete and notify_error fully."""

    @patch("src.utils.notifications.run_powershell", return_value=("", "", 0))
    def test_notify_backup_complete_message(self, mock_ps):
        from src.utils.notifications import NotificationManager

        nm = NotificationManager()
        nm.notify_backup_complete("My Backup", 1024 * 1024 * 500)
        cmd = mock_ps.call_args[0][0]
        self.assertIn("Backup Complete", cmd)
        self.assertIn("My Backup", cmd)

    @patch("src.utils.notifications.run_powershell", return_value=("", "", 0))
    def test_notify_error_message(self, mock_ps):
        from src.utils.notifications import NotificationManager

        nm = NotificationManager()
        nm.notify_error("Clone", "Target is read-only")
        cmd = mock_ps.call_args[0][0]
        self.assertIn("Clone Failed", cmd)
        self.assertIn("Target is read-only", cmd)

    @patch("src.utils.notifications.run_powershell", return_value=("", "err", 1))
    def test_notify_exception_in_toast_and_fallback(self, mock_ps):
        """If notify itself raises, it logs and doesn't propagate."""
        from src.utils.notifications import NotificationManager

        nm = NotificationManager()
        # Make _fallback_notify raise to trigger the outer except
        with patch.object(nm, "_fallback_notify", side_effect=Exception("boom")):
            # Should not raise
            nm.notify("Title", "Body")


class TestNotificationsCustomAppId(unittest.TestCase):
    """Test custom app_id in toast command."""

    @patch("src.utils.notifications.run_powershell", return_value=("", "", 0))
    def test_custom_app_id_in_toast(self, mock_ps):
        from src.utils.notifications import NotificationManager

        nm = NotificationManager(app_id="MyCustomApp")
        nm._send_toast("T", "M")
        cmd = mock_ps.call_args[0][0]
        self.assertIn("MyCustomApp", cmd)


# ======================================================================
# 5. Helpers: run_powershell and run_diskpart coverage
# ======================================================================

class TestRunPowershell(unittest.TestCase):
    """Test run_powershell with mocked subprocess."""

    @patch("src.utils.helpers.subprocess.run")
    def test_run_powershell_success(self, mock_run):
        from src.utils.helpers import run_powershell

        mock_run.return_value = MagicMock(
            stdout="output text", stderr="", returncode=0
        )
        stdout, stderr, rc = run_powershell("Get-Date")
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "output text")

    @patch("src.utils.helpers.subprocess.run")
    def test_run_powershell_timeout(self, mock_run):
        from src.utils.helpers import run_powershell

        mock_run.side_effect = subprocess.TimeoutExpired("powershell", 120)
        stdout, stderr, rc = run_powershell("slow-cmd")
        self.assertEqual(rc, 1)
        self.assertIn("timed out", stderr)

    @patch("src.utils.helpers.subprocess.run")
    def test_run_powershell_not_found(self, mock_run):
        from src.utils.helpers import run_powershell

        mock_run.side_effect = FileNotFoundError()
        stdout, stderr, rc = run_powershell("cmd")
        self.assertEqual(rc, 1)
        self.assertIn("not found", stderr)

    @patch("src.utils.helpers.subprocess.run")
    def test_run_powershell_os_error(self, mock_run):
        from src.utils.helpers import run_powershell

        mock_run.side_effect = OSError("permission denied")
        stdout, stderr, rc = run_powershell("cmd")
        self.assertEqual(rc, 1)
        self.assertIn("OS error", stderr)


class TestRunDiskpart(unittest.TestCase):
    """Test run_diskpart with mocked subprocess."""

    @patch("src.utils.helpers.subprocess.run")
    def test_run_diskpart_success(self, mock_run):
        from src.utils.helpers import run_diskpart

        mock_run.return_value = MagicMock(
            stdout="DiskPart succeeded", stderr="", returncode=0
        )
        stdout, stderr, rc = run_diskpart(["select disk 0", "clean"])
        self.assertEqual(rc, 0)
        self.assertIn("DiskPart succeeded", stdout)

    @patch("src.utils.helpers.subprocess.run")
    def test_run_diskpart_timeout(self, mock_run):
        from src.utils.helpers import run_diskpart

        mock_run.side_effect = subprocess.TimeoutExpired("diskpart", 120)
        stdout, stderr, rc = run_diskpart(["select disk 0"])
        self.assertEqual(rc, 1)
        self.assertIn("timed out", stderr)

    @patch("src.utils.helpers.subprocess.run")
    def test_run_diskpart_not_found(self, mock_run):
        from src.utils.helpers import run_diskpart

        mock_run.side_effect = FileNotFoundError()
        stdout, stderr, rc = run_diskpart(["list disk"])
        self.assertEqual(rc, 1)
        self.assertIn("not found", stderr)

    @patch("src.utils.helpers.subprocess.run")
    def test_run_diskpart_os_error(self, mock_run):
        from src.utils.helpers import run_diskpart

        mock_run.side_effect = OSError("denied")
        stdout, stderr, rc = run_diskpart(["list disk"])
        self.assertEqual(rc, 1)
        self.assertIn("OS error", stderr)


# ======================================================================
# 6. Cloud backup: cover remaining branches
# ======================================================================

class TestCloudBackupGoogleDrive(unittest.TestCase):
    """Test Google Drive detection."""

    def test_googledrive_detection(self):
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            gdrive_dir = os.path.join(tmpdir, "Google Drive")
            os.makedirs(gdrive_dir)

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            available = mgr.get_available_providers()
            gd = [p for p in available if p.name == "googledrive"]
            self.assertEqual(len(gd), 1)


class TestCloudBackupDropbox(unittest.TestCase):
    """Test Dropbox detection."""

    def test_dropbox_detection(self):
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            dropbox_dir = os.path.join(tmpdir, "Dropbox")
            os.makedirs(dropbox_dir)

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            available = mgr.get_available_providers()
            db = [p for p in available if p.name == "dropbox"]
            self.assertEqual(len(db), 1)


class TestCloudBackupListRemoteEmpty(unittest.TestCase):
    """Test list_remote_backups with no folder."""

    def test_list_remote_unknown_provider(self):
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            self.assertEqual(mgr.list_remote_backups("unknown"), [])

    def test_delete_remote_unknown_provider(self):
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            self.assertFalse(mgr.delete_remote("unknown", "file.zip"))

    def test_delete_remote_nonexistent_file(self):
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            sync_dir = os.path.join(tmpdir, "OneDrive")
            os.makedirs(sync_dir)

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            self.assertFalse(mgr.delete_remote("onedrive", "nonexistent.zip"))


# ======================================================================
# 7. Settings: cover remaining branches
# ======================================================================

class TestSettingsProperties(unittest.TestCase):
    """Test settings properties and edge cases."""

    def test_settings_path_property(self):
        from src.utils.settings import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            with patch("src.utils.settings._get_settings_path", return_value=settings_file):
                s = Settings()
                self.assertEqual(s.settings_path, settings_file)

    def test_settings_is_portable_property(self):
        from src.utils.settings import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            with patch("src.utils.settings._get_settings_path", return_value=settings_file):
                s = Settings()
                # Just verify it returns a boolean
                self.assertIsInstance(s.is_portable, bool)

    def test_settings_get_unknown_key(self):
        from src.utils.settings import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            with patch("src.utils.settings._get_settings_path", return_value=settings_file):
                s = Settings()
                self.assertIsNone(s.get("totally_unknown_key"))

    def test_settings_get_with_default(self):
        from src.utils.settings import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            with patch("src.utils.settings._get_settings_path", return_value=settings_file):
                s = Settings()
                result = s.get("nonexistent", "my_default")
                self.assertEqual(result, "my_default")


if __name__ == "__main__":
    unittest.main()
