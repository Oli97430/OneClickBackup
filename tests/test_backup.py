"""Tests for the backup module data classes and exceptions."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.backup import BackupInfo, BackupError, AdminRequiredError, CancelledError


class TestBackupInfo(unittest.TestCase):
    """Test the BackupInfo dataclass."""

    def _make_info(self, **overrides):
        defaults = dict(
            backup_id="test-001",
            name="Test Backup",
            timestamp="20260519_120000",
            source_disk=0,
            source_partitions=[1, 2],
            backup_type="full_disk",
            total_size_bytes=500_000_000_000,
            compressed_size_bytes=250_000_000_000,
            backup_path="C:\\Backups\\test-001",
            checksum="abc123def456",
            os_version="Windows 11 Home 10.0.26100",
        )
        defaults.update(overrides)
        return BackupInfo(**defaults)

    def test_creation(self):
        info = self._make_info()
        self.assertEqual(info.backup_id, "test-001")
        self.assertEqual(info.name, "Test Backup")

    def test_backup_type(self):
        info = self._make_info(backup_type="partition")
        self.assertEqual(info.backup_type, "partition")

    def test_source_partitions_is_list(self):
        info = self._make_info()
        self.assertIsInstance(info.source_partitions, list)
        self.assertEqual(info.source_partitions, [1, 2])

    def test_size_fields(self):
        info = self._make_info()
        self.assertEqual(info.total_size_bytes, 500_000_000_000)
        self.assertLess(info.compressed_size_bytes, info.total_size_bytes)

    def test_all_fields_present(self):
        info = self._make_info()
        expected_fields = {
            "backup_id", "name", "timestamp", "source_disk",
            "source_partitions", "backup_type", "total_size_bytes",
            "compressed_size_bytes", "backup_path", "checksum", "os_version",
            # New fields added in v1.2.0
            "is_compressed", "compression_format", "is_encrypted",
            "parent_backup_id", "backup_mode", "file_count", "manifest_path",
        }
        self.assertEqual(set(vars(info).keys()), expected_fields)


class TestExceptionHierarchy(unittest.TestCase):
    """Test the exception classes."""

    def test_backup_error_is_exception(self):
        self.assertTrue(issubclass(BackupError, Exception))

    def test_admin_required_is_backup_error(self):
        self.assertTrue(issubclass(AdminRequiredError, BackupError))

    def test_cancelled_is_backup_error(self):
        self.assertTrue(issubclass(CancelledError, BackupError))

    def test_admin_required_catchable_as_backup_error(self):
        with self.assertRaises(BackupError):
            raise AdminRequiredError("need admin")

    def test_cancelled_catchable_as_backup_error(self):
        with self.assertRaises(BackupError):
            raise CancelledError("user cancelled")

    def test_error_message_preserved(self):
        err = BackupError("disk full")
        self.assertEqual(str(err), "disk full")

    def test_admin_error_message(self):
        err = AdminRequiredError("requires elevation")
        self.assertIn("elevation", str(err))


class TestBackupManagerInit(unittest.TestCase):
    """Test BackupManager instantiation without actual disk access."""

    def test_default_backup_dir(self):
        from src.core.backup import BackupManager
        mgr = BackupManager()
        self.assertIn("OneClickBackups", mgr.backup_dir)

    def test_custom_backup_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from src.core.backup import BackupManager
            mgr = BackupManager(backup_dir=tmp)
            self.assertEqual(mgr.backup_dir, tmp)

    def test_cancel_event_starts_unset(self):
        from src.core.backup import BackupManager
        mgr = BackupManager()
        self.assertFalse(mgr._cancel_event.is_set())

    def test_cancel_sets_event(self):
        from src.core.backup import BackupManager
        mgr = BackupManager()
        mgr.cancel()
        self.assertTrue(mgr._cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
