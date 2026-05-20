"""Tests for the newer OneClickBackup modules.

Covers:
    - src.core.history      (OperationHistory)
    - src.core.updater       (AutoUpdater)
    - src.utils.crash_report (install_crash_handler, get_crash_reports)
    - src.utils.notifications (NotificationManager)
    - src.utils.report       (ReportGenerator)
    - src.utils.cli          (run_cli)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ======================================================================
# 1. OperationHistory
# ======================================================================

from src.core.history import OperationHistory, HistoryEntry


class TestHistoryEntry(unittest.TestCase):
    """Test the HistoryEntry dataclass."""

    def test_creation_with_defaults(self):
        entry = HistoryEntry(
            timestamp="2026-01-01T00:00:00",
            operation="backup",
            description="Full disk backup",
            success=True,
            message="OK",
        )
        self.assertEqual(entry.operation, "backup")
        self.assertTrue(entry.success)
        self.assertIsNone(entry.disk_index)
        self.assertIsNone(entry.partition_index)
        self.assertEqual(entry.duration_seconds, 0.0)
        self.assertIsNone(entry.details)

    def test_creation_with_all_fields(self):
        entry = HistoryEntry(
            timestamp="2026-01-01T00:00:00",
            operation="clone",
            description="Clone disk 0 to disk 1",
            success=False,
            message="Target disk full",
            disk_index=0,
            partition_index=1,
            duration_seconds=42.5,
            details={"src": 0, "tgt": 1},
        )
        self.assertEqual(entry.disk_index, 0)
        self.assertEqual(entry.partition_index, 1)
        self.assertAlmostEqual(entry.duration_seconds, 42.5)
        self.assertEqual(entry.details, {"src": 0, "tgt": 1})


class TestOperationHistoryRecord(unittest.TestCase):
    """Test OperationHistory.record()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "history.jsonl")
        self.history = OperationHistory(path=self._path)

    def tearDown(self):
        if os.path.isfile(self._path):
            os.remove(self._path)
        os.rmdir(self._tmpdir)

    def test_record_returns_entry(self):
        entry = self.history.record(
            operation="backup",
            description="Test backup",
            success=True,
            message="done",
        )
        self.assertIsInstance(entry, HistoryEntry)
        self.assertEqual(entry.operation, "backup")
        self.assertTrue(entry.success)

    def test_record_creates_file(self):
        self.history.record(
            operation="backup", description="d", success=True,
        )
        self.assertTrue(os.path.isfile(self._path))

    def test_record_writes_valid_jsonl(self):
        self.history.record(
            operation="backup", description="d", success=True,
        )
        with open(self._path, encoding="utf-8") as f:
            line = f.readline().strip()
        data = json.loads(line)
        self.assertEqual(data["operation"], "backup")

    def test_record_appends_multiple(self):
        self.history.record(operation="a", description="d", success=True)
        self.history.record(operation="b", description="d", success=False)
        with open(self._path, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        self.assertEqual(len(lines), 2)

    def test_record_with_disk_and_partition(self):
        entry = self.history.record(
            operation="format",
            description="Format partition",
            success=True,
            disk_index=1,
            partition_index=3,
            duration_seconds=10.5,
        )
        self.assertEqual(entry.disk_index, 1)
        self.assertEqual(entry.partition_index, 3)
        self.assertAlmostEqual(entry.duration_seconds, 10.5)

    def test_record_with_details_dict(self):
        details = {"speed_mbps": 120, "checksum": "abc"}
        entry = self.history.record(
            operation="backup",
            description="d",
            success=True,
            details=details,
        )
        self.assertEqual(entry.details, details)

    def test_record_timestamp_is_populated(self):
        entry = self.history.record(
            operation="backup", description="d", success=True,
        )
        self.assertIn("T", entry.timestamp)  # ISO format contains 'T'


class TestOperationHistoryGetAll(unittest.TestCase):
    """Test OperationHistory.get_all()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "history.jsonl")
        self.history = OperationHistory(path=self._path)

    def tearDown(self):
        if os.path.isfile(self._path):
            os.remove(self._path)
        os.rmdir(self._tmpdir)

    def test_get_all_empty(self):
        entries = self.history.get_all()
        self.assertEqual(entries, [])

    def test_get_all_returns_entries_in_reverse_order(self):
        self.history.record(operation="first", description="d", success=True)
        self.history.record(operation="second", description="d", success=True)
        self.history.record(operation="third", description="d", success=True)
        entries = self.history.get_all()
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].operation, "third")
        self.assertEqual(entries[1].operation, "second")
        self.assertEqual(entries[2].operation, "first")

    def test_get_all_respects_limit(self):
        for i in range(10):
            self.history.record(
                operation=f"op_{i}", description="d", success=True,
            )
        entries = self.history.get_all(limit=3)
        self.assertEqual(len(entries), 3)
        # Newest first
        self.assertEqual(entries[0].operation, "op_9")

    def test_get_all_skips_malformed_lines(self):
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("not valid json\n")
            good = json.dumps({
                "timestamp": "2026-01-01T00:00:00",
                "operation": "valid",
                "description": "d",
                "success": True,
                "message": "",
            })
            f.write(good + "\n")
        entries = self.history.get_all()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].operation, "valid")


class TestOperationHistoryGetByOperation(unittest.TestCase):
    """Test OperationHistory.get_by_operation()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "history.jsonl")
        self.history = OperationHistory(path=self._path)

    def tearDown(self):
        if os.path.isfile(self._path):
            os.remove(self._path)
        os.rmdir(self._tmpdir)

    def test_filters_by_operation(self):
        self.history.record(operation="backup", description="d", success=True)
        self.history.record(operation="clone", description="d", success=True)
        self.history.record(operation="backup", description="d", success=False)
        entries = self.history.get_by_operation("backup")
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertEqual(e.operation, "backup")

    def test_returns_empty_for_unknown_operation(self):
        self.history.record(operation="backup", description="d", success=True)
        entries = self.history.get_by_operation("nonexistent")
        self.assertEqual(entries, [])

    def test_respects_limit(self):
        for _ in range(20):
            self.history.record(
                operation="backup", description="d", success=True,
            )
        entries = self.history.get_by_operation("backup", limit=5)
        self.assertLessEqual(len(entries), 5)


class TestOperationHistoryGetFailures(unittest.TestCase):
    """Test OperationHistory.get_failures()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "history.jsonl")
        self.history = OperationHistory(path=self._path)

    def tearDown(self):
        if os.path.isfile(self._path):
            os.remove(self._path)
        os.rmdir(self._tmpdir)

    def test_returns_only_failures(self):
        self.history.record(operation="a", description="d", success=True)
        self.history.record(operation="b", description="d", success=False)
        self.history.record(operation="c", description="d", success=True)
        self.history.record(
            operation="d", description="d", success=False, message="err",
        )
        failures = self.history.get_failures()
        self.assertEqual(len(failures), 2)
        for f in failures:
            self.assertFalse(f.success)

    def test_returns_empty_when_all_succeed(self):
        self.history.record(operation="a", description="d", success=True)
        self.assertEqual(self.history.get_failures(), [])


class TestOperationHistoryClear(unittest.TestCase):
    """Test OperationHistory.clear()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "history.jsonl")
        self.history = OperationHistory(path=self._path)

    def tearDown(self):
        if os.path.isfile(self._path):
            os.remove(self._path)
        if os.path.isdir(self._tmpdir):
            os.rmdir(self._tmpdir)

    def test_clear_removes_file(self):
        self.history.record(operation="a", description="d", success=True)
        self.assertTrue(os.path.isfile(self._path))
        self.history.clear()
        self.assertFalse(os.path.isfile(self._path))

    def test_clear_then_get_all_returns_empty(self):
        self.history.record(operation="a", description="d", success=True)
        self.history.clear()
        self.assertEqual(self.history.get_all(), [])

    def test_clear_on_empty_does_not_raise(self):
        self.history.clear()  # Should not raise


class TestOperationHistoryExportJson(unittest.TestCase):
    """Test OperationHistory.export_json()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "history.jsonl")
        self._export_path = os.path.join(self._tmpdir, "export.json")
        self.history = OperationHistory(path=self._path)

    def tearDown(self):
        for p in (self._path, self._export_path):
            if os.path.isfile(p):
                os.remove(p)
        os.rmdir(self._tmpdir)

    def test_export_creates_file(self):
        self.history.record(operation="backup", description="d", success=True)
        result = self.history.export_json(self._export_path)
        self.assertEqual(result, self._export_path)
        self.assertTrue(os.path.isfile(self._export_path))

    def test_export_produces_valid_json(self):
        self.history.record(operation="a", description="d", success=True)
        self.history.record(operation="b", description="d", success=False)
        self.history.export_json(self._export_path)
        with open(self._export_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)

    def test_export_entries_have_expected_keys(self):
        self.history.record(
            operation="backup",
            description="Full backup",
            success=True,
            message="OK",
            disk_index=0,
        )
        self.history.export_json(self._export_path)
        with open(self._export_path, encoding="utf-8") as f:
            data = json.load(f)
        entry = data[0]
        self.assertIn("operation", entry)
        self.assertIn("timestamp", entry)
        self.assertIn("success", entry)
        self.assertIn("disk_index", entry)

    def test_export_empty_history(self):
        self.history.export_json(self._export_path)
        with open(self._export_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data, [])


class TestOperationHistoryCount(unittest.TestCase):
    """Test OperationHistory.count property."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "history.jsonl")
        self.history = OperationHistory(path=self._path)

    def tearDown(self):
        if os.path.isfile(self._path):
            os.remove(self._path)
        os.rmdir(self._tmpdir)

    def test_count_zero_when_empty(self):
        self.assertEqual(self.history.count, 0)

    def test_count_matches_records(self):
        self.history.record(operation="a", description="d", success=True)
        self.history.record(operation="b", description="d", success=True)
        self.history.record(operation="c", description="d", success=False)
        self.assertEqual(self.history.count, 3)

    def test_count_after_clear(self):
        self.history.record(operation="a", description="d", success=True)
        self.history.clear()
        self.assertEqual(self.history.count, 0)


# ======================================================================
# 2. AutoUpdater
# ======================================================================

from src.core.updater import AutoUpdater, UpdateInfo


class TestAutoUpdaterVersionParsing(unittest.TestCase):
    """Test version string parsing and comparison."""

    def test_parse_simple_version(self):
        result = AutoUpdater._parse_version("1.2.3")
        self.assertEqual(result, (1, 2, 3))

    def test_parse_version_with_v_prefix(self):
        result = AutoUpdater._parse_version("v2.0.1")
        self.assertEqual(result, (2, 0, 1))

    def test_parse_version_with_whitespace(self):
        result = AutoUpdater._parse_version("  v1.0.0  ")
        self.assertEqual(result, (1, 0, 0))

    def test_parse_version_two_parts(self):
        result = AutoUpdater._parse_version("1.0")
        self.assertEqual(result, (1, 0))

    def test_parse_version_with_non_numeric_part(self):
        result = AutoUpdater._parse_version("1.2.beta")
        self.assertEqual(result, (1, 2, 0))

    def test_is_newer_true(self):
        updater = AutoUpdater(current_version="1.0.0")
        self.assertTrue(updater._is_newer("1.1.0"))

    def test_is_newer_false_same(self):
        updater = AutoUpdater(current_version="1.1.0")
        self.assertFalse(updater._is_newer("1.1.0"))

    def test_is_newer_false_older(self):
        updater = AutoUpdater(current_version="2.0.0")
        self.assertFalse(updater._is_newer("1.9.9"))

    def test_is_newer_handles_v_prefix(self):
        updater = AutoUpdater(current_version="1.0.0")
        self.assertTrue(updater._is_newer("v1.0.1"))

    def test_is_newer_major_bump(self):
        updater = AutoUpdater(current_version="1.9.9")
        self.assertTrue(updater._is_newer("2.0.0"))


class TestAutoUpdaterCheckForUpdate(unittest.TestCase):
    """Test check_for_update() with mocked HTTP responses."""

    def _make_github_response(
        self, tag="v2.0.0", body="Release notes here", html_url="https://github.com/test",
        published_at="2026-05-20T12:00:00Z", exe_name="OneClickBackup.exe",
        exe_url="https://github.com/test/download/OneClickBackup.exe", exe_size=15_000_000,
    ):
        """Build a fake GitHub release API JSON payload."""
        return json.dumps({
            "tag_name": tag,
            "body": body,
            "html_url": html_url,
            "published_at": published_at,
            "assets": [
                {
                    "name": exe_name,
                    "browser_download_url": exe_url,
                    "size": exe_size,
                },
            ],
        }).encode("utf-8")

    @patch("src.core.updater.urlopen")
    def test_update_available(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_github_response(tag="v2.0.0")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        updater = AutoUpdater(current_version="1.0.0")
        info = updater.check_for_update()

        self.assertIsInstance(info, UpdateInfo)
        self.assertTrue(info.is_update_available)
        self.assertEqual(info.current_version, "1.0.0")
        self.assertEqual(info.latest_version, "v2.0.0")
        self.assertIn("OneClickBackup.exe", info.download_url)
        self.assertEqual(info.file_size, 15_000_000)

    @patch("src.core.updater.urlopen")
    def test_no_update_when_current(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_github_response(tag="v1.1.0")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        updater = AutoUpdater(current_version="1.1.0")
        info = updater.check_for_update()

        self.assertFalse(info.is_update_available)
        self.assertEqual(info.current_version, "1.1.0")

    @patch("src.core.updater.urlopen")
    def test_no_update_when_ahead(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_github_response(tag="v1.0.0")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        updater = AutoUpdater(current_version="2.0.0")
        info = updater.check_for_update()

        self.assertFalse(info.is_update_available)

    @patch("src.core.updater.urlopen")
    def test_network_error_returns_safe_default(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("no network")

        updater = AutoUpdater(current_version="1.0.0")
        info = updater.check_for_update()

        self.assertFalse(info.is_update_available)
        self.assertEqual(info.current_version, "1.0.0")
        self.assertEqual(info.latest_version, "1.0.0")
        self.assertEqual(info.download_url, "")

    @patch("src.core.updater.urlopen")
    def test_release_notes_truncated(self, mock_urlopen):
        long_notes = "x" * 5000
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_github_response(
            tag="v9.0.0", body=long_notes,
        )
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        updater = AutoUpdater(current_version="1.0.0")
        info = updater.check_for_update()

        self.assertLessEqual(len(info.release_notes), 2000)

    @patch("src.core.updater.urlopen")
    def test_no_exe_asset(self, mock_urlopen):
        data = json.dumps({
            "tag_name": "v2.0.0",
            "body": "notes",
            "html_url": "https://github.com/test",
            "published_at": "2026-01-01T00:00:00Z",
            "assets": [
                {"name": "source.zip", "browser_download_url": "http://x", "size": 100},
            ],
        }).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        updater = AutoUpdater(current_version="1.0.0")
        info = updater.check_for_update()

        self.assertTrue(info.is_update_available)
        self.assertEqual(info.download_url, "")
        self.assertEqual(info.file_size, 0)


class TestAutoUpdaterDefaultVersion(unittest.TestCase):
    """Test that the default version matches the package."""

    def test_default_version_used(self):
        updater = AutoUpdater()
        self.assertEqual(updater._current_version, "1.1.0")

    def test_custom_version_overrides(self):
        updater = AutoUpdater(current_version="9.9.9")
        self.assertEqual(updater._current_version, "9.9.9")


class TestUpdateInfoDataclass(unittest.TestCase):
    """Test the UpdateInfo dataclass."""

    def test_all_fields(self):
        info = UpdateInfo(
            current_version="1.0.0",
            latest_version="2.0.0",
            is_update_available=True,
            release_url="https://example.com",
            download_url="https://example.com/dl",
            release_notes="Fixed bugs",
            published_at="2026-01-01T00:00:00Z",
            file_size=1000,
        )
        self.assertTrue(info.is_update_available)
        self.assertEqual(info.file_size, 1000)


# ======================================================================
# 3. Crash report
# ======================================================================

import src.utils.crash_report as crash_module
from src.utils.crash_report import install_crash_handler, get_crash_reports


class TestInstallCrashHandler(unittest.TestCase):
    """Test install_crash_handler()."""

    def setUp(self):
        # Save original hook and reset the _installed flag so we can test
        self._original_hook = sys.excepthook
        if hasattr(install_crash_handler, "_installed"):
            self._original_installed = install_crash_handler._installed
        else:
            self._original_installed = False
        install_crash_handler._installed = False

    def tearDown(self):
        sys.excepthook = self._original_hook
        install_crash_handler._installed = self._original_installed

    def test_sets_excepthook(self):
        install_crash_handler()
        self.assertNotEqual(sys.excepthook, self._original_hook)
        self.assertEqual(sys.excepthook, crash_module._crash_hook)

    def test_idempotent(self):
        install_crash_handler()
        hook_after_first = sys.excepthook
        install_crash_handler()  # second call is a no-op
        self.assertIs(sys.excepthook, hook_after_first)

    def test_sets_installed_flag(self):
        install_crash_handler()
        self.assertTrue(install_crash_handler._installed)


class TestGetCrashReports(unittest.TestCase):
    """Test get_crash_reports()."""

    def test_returns_list(self):
        reports = get_crash_reports()
        self.assertIsInstance(reports, list)

    @patch("src.utils.crash_report._LOG_DIR")
    def test_returns_empty_for_nonexistent_dir(self, mock_dir):
        # Point at a directory that cannot exist
        with patch.object(crash_module, "_LOG_DIR", "Z:\\nonexistent_dir_xyz"):
            reports = get_crash_reports()
            self.assertEqual(reports, [])

    def test_crash_report_entries_have_required_keys(self):
        """If any crash reports exist, they must have path/timestamp/summary."""
        reports = get_crash_reports()
        for r in reports:
            self.assertIn("path", r)
            self.assertIn("timestamp", r)
            self.assertIn("summary", r)


class TestCrashReportHelpers(unittest.TestCase):
    """Test internal helpers used by the crash report module."""

    def test_parse_timestamp_from_filename(self):
        result = crash_module._parse_timestamp_from_filename(
            "crash_20260520_143025.txt"
        )
        self.assertEqual(result, "2026-05-20T14:30:25")

    def test_parse_timestamp_from_bad_filename(self):
        result = crash_module._parse_timestamp_from_filename("crash_garbage.txt")
        # Falls back to the raw stem
        self.assertEqual(result, "garbage")

    def test_extract_summary_missing_file(self):
        result = crash_module._extract_summary("Z:\\no_such_file.txt")
        self.assertEqual(result, "(unreadable)")

    def test_extract_summary_from_real_report(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8",
        ) as f:
            f.write("=" * 70 + "\n")
            f.write("  OneClick Backup -- CRASH REPORT\n")
            f.write("=" * 70 + "\n")
            f.write("\n")
            f.write("Timestamp : 2026-05-20 14:30:25\n")
            f.write("Exception : ValueError: bad value\n")
            fname = f.name
        try:
            result = crash_module._extract_summary(fname)
            self.assertIn("OneClick Backup", result)
        finally:
            os.remove(fname)


# ======================================================================
# 4. NotificationManager
# ======================================================================

from src.utils.notifications import NotificationManager


class TestNotificationManagerNotify(unittest.TestCase):
    """Test NotificationManager.notify()."""

    @patch.object(NotificationManager, "_send_toast", return_value=False)
    @patch.object(NotificationManager, "_fallback_notify")
    def test_notify_does_not_raise(self, mock_fallback, mock_toast):
        nm = NotificationManager()
        nm.notify("Title", "Body")  # must not raise

    @patch.object(NotificationManager, "_send_toast", return_value=True)
    def test_notify_calls_send_toast(self, mock_toast):
        nm = NotificationManager()
        nm.notify("Title", "Body")
        mock_toast.assert_called_once_with("Title", "Body")

    @patch.object(NotificationManager, "_send_toast", return_value=False)
    @patch.object(NotificationManager, "_fallback_notify")
    def test_notify_falls_back_on_toast_failure(self, mock_fallback, mock_toast):
        nm = NotificationManager()
        nm.notify("Title", "Body", icon="error")
        mock_fallback.assert_called_once_with("Title", "Body", "error")

    @patch.object(NotificationManager, "_send_toast", side_effect=Exception("boom"))
    def test_notify_swallows_exceptions(self, mock_toast):
        nm = NotificationManager()
        nm.notify("Title", "Body")  # must not raise


class TestNotificationManagerBackupComplete(unittest.TestCase):
    """Test NotificationManager.notify_backup_complete()."""

    @patch.object(NotificationManager, "notify")
    def test_calls_notify(self, mock_notify):
        nm = NotificationManager()
        nm.notify_backup_complete("My Backup", 1_073_741_824)
        mock_notify.assert_called_once()
        args = mock_notify.call_args
        self.assertIn("Backup Complete", args[0][0])
        self.assertIn("My Backup", args[0][1])

    @patch.object(NotificationManager, "notify")
    def test_formats_size(self, mock_notify):
        nm = NotificationManager()
        nm.notify_backup_complete("B", 500_000_000)
        body = mock_notify.call_args[0][1]
        # Should contain a human-readable size
        self.assertTrue(
            "MB" in body or "GB" in body or "KB" in body,
            f"Expected formatted size in body: {body}",
        )


class TestNotificationManagerNotifyError(unittest.TestCase):
    """Test NotificationManager.notify_error()."""

    @patch.object(NotificationManager, "notify")
    def test_calls_notify_with_error_icon(self, mock_notify):
        nm = NotificationManager()
        nm.notify_error("Clone", "Disk read error")
        mock_notify.assert_called_once()
        title, body = mock_notify.call_args[0][0], mock_notify.call_args[0][1]
        self.assertIn("Failed", title)
        self.assertIn("Disk read error", body)


class TestNotificationManagerEscaping(unittest.TestCase):
    """Test the XML/PS escaping helper."""

    def test_escapes_ampersand(self):
        result = NotificationManager._escape_for_ps_xml("A & B")
        self.assertIn("&amp;", result)

    def test_escapes_angle_brackets(self):
        result = NotificationManager._escape_for_ps_xml("<script>")
        self.assertIn("&lt;", result)
        self.assertIn("&gt;", result)

    def test_escapes_quotes(self):
        result = NotificationManager._escape_for_ps_xml("It's \"fine\"")
        self.assertIn("&apos;", result)
        self.assertIn("&quot;", result)

    def test_plain_text_unchanged(self):
        result = NotificationManager._escape_for_ps_xml("Hello World")
        self.assertEqual(result, "Hello World")


class TestNotificationManagerInit(unittest.TestCase):
    """Test NotificationManager initialization."""

    def test_default_app_id(self):
        nm = NotificationManager()
        self.assertEqual(nm._app_id, "OneClickBackup")

    def test_custom_app_id(self):
        nm = NotificationManager(app_id="TestApp")
        self.assertEqual(nm._app_id, "TestApp")


# ======================================================================
# 5. ReportGenerator
# ======================================================================

from src.utils.report import ReportGenerator


@dataclass
class _FakePartition:
    """Minimal stand-in for PartitionInfo."""
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
    """Minimal stand-in for DiskInfo."""
    index: int = 0
    model: str = "Fake Disk"
    size_bytes: int = 500_000_000_000
    media_type: str = "SSD"
    interface_type: str = "NVMe"
    partition_style: str = "GPT"
    health_status: str = "Healthy"
    is_system_disk: bool = True
    is_4k_aligned: bool = True
    unallocated_bytes: int = 0
    partitions: list = field(default_factory=list)


def _make_fake_disks():
    """Create a list of two fake disks for report tests."""
    part1 = _FakePartition(
        index=1, letter="C", label="System", file_system="NTFS",
        size_bytes=256_000_000_000, used_bytes=128_000_000_000,
        free_bytes=128_000_000_000, partition_type="Primary",
        is_active=True, is_boot=True, is_system=True,
    )
    part2 = _FakePartition(
        index=2, letter="D", label="Data", file_system="NTFS",
        size_bytes=244_000_000_000, used_bytes=100_000_000_000,
        free_bytes=144_000_000_000, partition_type="Primary",
    )
    disk0 = _FakeDisk(
        index=0, model="Samsung 980 PRO", partitions=[part1, part2],
    )
    disk1 = _FakeDisk(
        index=1, model="WD Blue 2TB", size_bytes=2_000_000_000_000,
        media_type="HDD", interface_type="SATA",
        health_status="Healthy", is_system_disk=False,
        partitions=[],
    )
    return [disk0, disk1]


class TestReportGeneratorHTML(unittest.TestCase):
    """Test ReportGenerator.generate_html()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._html_path = os.path.join(self._tmpdir, "report.html")
        self.gen = ReportGenerator()
        self.disks = _make_fake_disks()

    def tearDown(self):
        if os.path.isfile(self._html_path):
            os.remove(self._html_path)
        os.rmdir(self._tmpdir)

    def test_creates_html_file(self):
        result = self.gen.generate_html(self.disks, self._html_path)
        self.assertTrue(os.path.isfile(result))

    def test_returns_absolute_path(self):
        result = self.gen.generate_html(self.disks, self._html_path)
        self.assertTrue(os.path.isabs(result))

    def test_html_contains_doctype(self):
        self.gen.generate_html(self.disks, self._html_path)
        with open(self._html_path, encoding="utf-8") as f:
            content = f.read()
        self.assertTrue(content.startswith("<!DOCTYPE html>"))

    def test_html_contains_disk_model(self):
        self.gen.generate_html(self.disks, self._html_path)
        with open(self._html_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Samsung 980 PRO", content)
        self.assertIn("WD Blue 2TB", content)

    def test_html_contains_partition_info(self):
        self.gen.generate_html(self.disks, self._html_path)
        with open(self._html_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("System", content)  # partition label
        self.assertIn("NTFS", content)

    def test_html_contains_no_partitions_text(self):
        self.gen.generate_html(self.disks, self._html_path)
        with open(self._html_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("No partitions found", content)  # disk1 has none

    def test_html_with_empty_disk_list(self):
        self.gen.generate_html([], self._html_path)
        with open(self._html_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("0 disk(s)", content)


class TestReportGeneratorText(unittest.TestCase):
    """Test ReportGenerator.generate_text()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._txt_path = os.path.join(self._tmpdir, "report.txt")
        self.gen = ReportGenerator()
        self.disks = _make_fake_disks()

    def tearDown(self):
        if os.path.isfile(self._txt_path):
            os.remove(self._txt_path)
        os.rmdir(self._tmpdir)

    def test_creates_text_file(self):
        result = self.gen.generate_text(self.disks, self._txt_path)
        self.assertTrue(os.path.isfile(result))

    def test_returns_absolute_path(self):
        result = self.gen.generate_text(self.disks, self._txt_path)
        self.assertTrue(os.path.isabs(result))

    def test_text_contains_header(self):
        self.gen.generate_text(self.disks, self._txt_path)
        with open(self._txt_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Disk Status Report", content)

    def test_text_contains_disk_info(self):
        self.gen.generate_text(self.disks, self._txt_path)
        with open(self._txt_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Samsung 980 PRO", content)
        self.assertIn("SSD", content)
        self.assertIn("NVMe", content)

    def test_text_contains_partition_table(self):
        self.gen.generate_text(self.disks, self._txt_path)
        with open(self._txt_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("C:", content)
        self.assertIn("NTFS", content)

    def test_text_contains_no_partitions(self):
        self.gen.generate_text(self.disks, self._txt_path)
        with open(self._txt_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("No partitions found", content)

    def test_text_with_empty_disk_list(self):
        self.gen.generate_text([], self._txt_path)
        with open(self._txt_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Disks     : 0", content)

    def test_text_ends_with_footer(self):
        self.gen.generate_text(self.disks, self._txt_path)
        with open(self._txt_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("End of report", content)


class TestReportGeneratorHealthCssClass(unittest.TestCase):
    """Test the _health_css_class static method."""

    def test_healthy(self):
        self.assertEqual(ReportGenerator._health_css_class("Healthy"), "health-ok")

    def test_warning(self):
        self.assertEqual(ReportGenerator._health_css_class("Warning"), "health-warn")

    def test_degraded(self):
        self.assertEqual(ReportGenerator._health_css_class("Degraded"), "health-warn")

    def test_unhealthy(self):
        self.assertEqual(ReportGenerator._health_css_class("Unhealthy"), "health-bad")

    def test_error(self):
        self.assertEqual(ReportGenerator._health_css_class("Error"), "health-bad")

    def test_failed(self):
        self.assertEqual(ReportGenerator._health_css_class("Failed"), "health-bad")

    def test_unknown(self):
        self.assertEqual(
            ReportGenerator._health_css_class("SomethingElse"), "health-unknown",
        )


# ======================================================================
# 6. CLI (run_cli)
# ======================================================================

from src.utils.cli import run_cli


class TestCliVersion(unittest.TestCase):
    """Test run_cli --version."""

    def test_version_returns_zero(self):
        rc = run_cli(["--version"])
        self.assertEqual(rc, 0)

    def test_version_prints_output(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            run_cli(["--version"])
        output = captured.getvalue()
        self.assertIn("OneClick Backup", output)

    def test_version_contains_version_number(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            run_cli(["--version"])
        output = captured.getvalue()
        # Should contain a version-like pattern (digits and dots)
        self.assertRegex(output, r"\d+\.\d+")


class TestCliListDisks(unittest.TestCase):
    """Test run_cli --list-disks (mocked to avoid admin/disk access)."""

    @patch("src.utils.cli.get_all_disks", create=True)
    def test_list_disks_returns_zero(self, mock_get_disks):
        # We must mock at the point of import inside the function
        with patch("src.core.disk_info.get_all_disks", return_value=[]):
            rc = run_cli(["--list-disks"])
        self.assertEqual(rc, 0)

    @patch("src.core.disk_info.get_all_disks", return_value=[])
    def test_list_disks_no_disks_message(self, mock_get_disks):
        captured = StringIO()
        with patch("sys.stdout", captured):
            run_cli(["--list-disks"])
        output = captured.getvalue()
        self.assertIn("No disks found", output)


class TestCliNoArgs(unittest.TestCase):
    """Test run_cli with no arguments."""

    def test_no_args_returns_zero(self):
        rc = run_cli([])
        self.assertEqual(rc, 0)

    def test_no_args_prints_help(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            run_cli([])
        output = captured.getvalue()
        self.assertIn("oneclickbackup", output.lower())


class TestCliUnknownArgs(unittest.TestCase):
    """Test run_cli with unknown arguments."""

    def test_unknown_arg_exits(self):
        # argparse calls sys.exit(2) for unrecognized arguments
        with self.assertRaises(SystemExit) as ctx:
            run_cli(["--unknown-flag-xyz"])
        self.assertEqual(ctx.exception.code, 2)


class TestCliBuildParser(unittest.TestCase):
    """Test that the parser has expected arguments."""

    def test_accepts_backup_types(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["--backup", "full_disk"])
        self.assertEqual(ns.backup, "full_disk")

    def test_rejects_invalid_backup_type(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--backup", "invalid_type"])

    def test_accepts_health_flag(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["--health"])
        self.assertTrue(ns.health)

    def test_accepts_disk_number(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["--health", "--disk", "2"])
        self.assertEqual(ns.disk, 2)

    def test_accepts_clone_pair(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["--clone", "0", "1"])
        self.assertEqual(ns.clone, [0, 1])


if __name__ == "__main__":
    unittest.main()
