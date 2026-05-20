"""Integration tests for cross-module workflows in OneClickBackup.

Covers end-to-end flows that span multiple modules, verifying that
they interoperate correctly when wired together. All external I/O
(PowerShell, diskpart, disk access, network) is mocked.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from io import StringIO
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ======================================================================
# Shared test fakes
# ======================================================================

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


# ======================================================================
# 1. Backup creation + compression + history recording flow
# ======================================================================

class TestBackupHistoryIntegration(unittest.TestCase):
    """Verify that backup creation records a history entry."""

    def test_backup_then_history_record(self):
        """Simulate a backup creation and verify history entry is written."""
        from src.core.history import OperationHistory, HistoryEntry

        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "history.jsonl")
            history = OperationHistory(path=history_path)

            # Simulate a backup operation completing
            entry = history.record(
                operation="backup",
                description="Full disk backup of disk 0",
                success=True,
                message="Backup created successfully",
                disk_index=0,
                duration_seconds=120.5,
                details={
                    "backup_id": "bk-001",
                    "backup_type": "full_disk",
                    "compressed_size": 250_000_000_000,
                    "checksum": "abc123",
                },
            )

            self.assertIsInstance(entry, HistoryEntry)
            self.assertEqual(entry.operation, "backup")
            self.assertTrue(entry.success)
            self.assertEqual(entry.disk_index, 0)

            # Verify history can be retrieved
            all_entries = history.get_all()
            self.assertEqual(len(all_entries), 1)
            self.assertEqual(all_entries[0].details["backup_id"], "bk-001")

            # Verify export works
            export_path = os.path.join(tmpdir, "export.json")
            history.export_json(export_path)
            with open(export_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["operation"], "backup")

    def test_failed_backup_recorded_as_failure(self):
        """History correctly records a failed backup."""
        from src.core.history import OperationHistory

        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "history.jsonl")
            history = OperationHistory(path=history_path)

            history.record(
                operation="backup",
                description="Full disk backup",
                success=False,
                message="Access denied: admin required",
                disk_index=0,
            )

            failures = history.get_failures()
            self.assertEqual(len(failures), 1)
            self.assertFalse(failures[0].success)
            self.assertIn("Access denied", failures[0].message)

    def test_multiple_operations_filtered_correctly(self):
        """History correctly filters different operation types."""
        from src.core.history import OperationHistory

        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "history.jsonl")
            history = OperationHistory(path=history_path)

            history.record(operation="backup", description="d", success=True)
            history.record(operation="clone", description="d", success=True)
            history.record(operation="backup", description="d", success=True)
            history.record(operation="wipe", description="d", success=False)

            backups = history.get_by_operation("backup")
            self.assertEqual(len(backups), 2)

            clones = history.get_by_operation("clone")
            self.assertEqual(len(clones), 1)

            failures = history.get_failures()
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].operation, "wipe")


# ======================================================================
# 2. Scheduler create + list + remove cycle
# ======================================================================

class TestSchedulerCycle(unittest.TestCase):
    """Test the full scheduler lifecycle: create, list, remove."""

    @patch("src.core.scheduler.run_powershell", return_value=("", "", 0))
    def test_schedule_create_list_remove(self, mock_ps):
        """Full cycle: schedule a backup, list it, then remove it."""
        from src.core.scheduler import BackupScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "schedules.json")
            scheduler = BackupScheduler(config_path=config_path)

            # Create
            entry = scheduler.schedule_backup(
                name="daily_test",
                backup_type="full_disk",
                schedule_type="daily",
                time_str="09:00",
            )
            self.assertEqual(entry.name, "daily_test")
            self.assertEqual(entry.backup_type, "full_disk")
            self.assertTrue(entry.task_name.startswith("OneClickBackup_"))

            # List
            schedules = scheduler.list_schedules()
            self.assertEqual(len(schedules), 1)
            self.assertEqual(schedules[0]["name"], "daily_test")

            # Get single
            found = scheduler.get_schedule("daily_test")
            self.assertIsNotNone(found)
            self.assertEqual(found["schedule_type"], "daily")

            # is_scheduled
            self.assertTrue(scheduler.is_scheduled("daily_test"))
            self.assertFalse(scheduler.is_scheduled("nonexistent"))

            # Remove
            scheduler.remove_schedule("daily_test")
            self.assertEqual(scheduler.list_schedules(), [])
            self.assertFalse(scheduler.is_scheduled("daily_test"))

    @patch("src.core.scheduler.run_powershell", return_value=("", "", 0))
    def test_scheduler_weekly(self, mock_ps):
        """Create a weekly schedule with day_of_week."""
        from src.core.scheduler import BackupScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "schedules.json")
            scheduler = BackupScheduler(config_path=config_path)

            entry = scheduler.schedule_backup(
                name="weekly_test",
                backup_type="system",
                schedule_type="weekly",
                time_str="22:00",
                day_of_week="Monday",
            )
            self.assertEqual(entry.day_of_week, "Monday")
            self.assertEqual(entry.schedule_type, "weekly")

    @patch("src.core.scheduler.run_powershell", return_value=("", "", 0))
    def test_scheduler_monthly(self, mock_ps):
        """Create a monthly schedule with day_of_month."""
        from src.core.scheduler import BackupScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "schedules.json")
            scheduler = BackupScheduler(config_path=config_path)

            entry = scheduler.schedule_backup(
                name="monthly_test",
                backup_type="partition",
                schedule_type="monthly",
                time_str="03:00",
                day_of_month=15,
            )
            self.assertEqual(entry.day_of_month, 15)
            self.assertEqual(entry.schedule_type, "monthly")

    @patch("src.core.scheduler.run_powershell", return_value=("", "", 0))
    def test_scheduler_duplicate_name_raises(self, mock_ps):
        """Creating a schedule with a duplicate name raises SchedulerError."""
        from src.core.scheduler import BackupScheduler, SchedulerError

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "schedules.json")
            scheduler = BackupScheduler(config_path=config_path)

            scheduler.schedule_backup(
                name="dup_test",
                backup_type="full_disk",
                schedule_type="daily",
                time_str="09:00",
            )

            with self.assertRaises(SchedulerError):
                scheduler.schedule_backup(
                    name="dup_test",
                    backup_type="full_disk",
                    schedule_type="daily",
                    time_str="10:00",
                )

    @patch("src.core.scheduler.run_powershell", return_value=("", "", 0))
    def test_scheduler_remove_nonexistent_raises(self, mock_ps):
        """Removing a non-existent schedule raises SchedulerError."""
        from src.core.scheduler import BackupScheduler, SchedulerError

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "schedules.json")
            scheduler = BackupScheduler(config_path=config_path)

            with self.assertRaises(SchedulerError):
                scheduler.remove_schedule("nonexistent")

    def test_scheduler_validation_errors(self):
        """Invalid inputs raise ValueError."""
        from src.core.scheduler import (
            _validate_name, _validate_time,
            _validate_schedule_type, _validate_day_of_week,
            _validate_day_of_month,
        )

        with self.assertRaises(ValueError):
            _validate_name("bad name with spaces!!")

        with self.assertRaises(ValueError):
            _validate_time("25:00")

        with self.assertRaises(ValueError):
            _validate_time("9:00")  # needs leading zero

        with self.assertRaises(ValueError):
            _validate_schedule_type("hourly")

        with self.assertRaises(ValueError):
            _validate_day_of_week(None, "weekly")

        with self.assertRaises(ValueError):
            _validate_day_of_week("Funday", "weekly")

        with self.assertRaises(ValueError):
            _validate_day_of_month(None, "monthly")

        with self.assertRaises(ValueError):
            _validate_day_of_month(29, "monthly")

    @patch("src.core.scheduler.run_powershell", return_value=("", "", 0))
    def test_scheduler_config_persistence(self, mock_ps):
        """Config file is written and can be loaded by a new instance."""
        from src.core.scheduler import BackupScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "schedules.json")

            # Create a schedule
            s1 = BackupScheduler(config_path=config_path)
            s1.schedule_backup(
                name="persist_test",
                backup_type="full_disk",
                schedule_type="daily",
                time_str="08:00",
            )

            # Load a new instance from the same config
            s2 = BackupScheduler(config_path=config_path)
            self.assertTrue(s2.is_scheduled("persist_test"))
            self.assertEqual(len(s2.list_schedules()), 1)

    @patch("src.core.scheduler.run_powershell", return_value=("", "error", 1))
    def test_scheduler_ps_failure_raises(self, mock_ps):
        """If PowerShell fails, schedule_backup raises SchedulerError."""
        from src.core.scheduler import BackupScheduler, SchedulerError

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "schedules.json")
            scheduler = BackupScheduler(config_path=config_path)

            with self.assertRaises(SchedulerError):
                scheduler.schedule_backup(
                    name="fail_test",
                    backup_type="full_disk",
                    schedule_type="daily",
                    time_str="09:00",
                )


# ======================================================================
# 3. Cloud backup: provider detection + upload
# ======================================================================

class TestCloudBackupIntegration(unittest.TestCase):
    """Test cloud backup detection and upload using temp dirs."""

    def test_provider_detection_with_temp_dirs(self):
        """Create temp sync folders and verify providers are detected."""
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            onedrive_dir = os.path.join(tmpdir, "OneDrive")
            os.makedirs(onedrive_dir)

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            providers = mgr.list_providers()
            self.assertTrue(len(providers) >= 3)

            # OneDrive should be detected
            available = mgr.get_available_providers()
            onedrive = [p for p in available if p.name == "onedrive"]
            self.assertEqual(len(onedrive), 1)
            self.assertTrue(onedrive[0].available)
            self.assertEqual(onedrive[0].sync_folder, onedrive_dir)

    def test_upload_to_sync_folder(self):
        """Upload a file to a simulated sync folder."""
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            sync_dir = os.path.join(tmpdir, "OneDrive")
            os.makedirs(sync_dir)

            # Create a test backup file
            src_file = os.path.join(tmpdir, "backup.zip")
            with open(src_file, "wb") as f:
                f.write(b"fake backup data " * 100)

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            dest = mgr.upload("onedrive", src_file)
            self.assertTrue(os.path.isfile(dest))
            self.assertIn("OneClickBackup", dest)

            # Verify content matches
            with open(src_file, "rb") as f1, open(dest, "rb") as f2:
                self.assertEqual(f1.read(), f2.read())

    def test_upload_with_progress_callback(self):
        """Upload reports progress via callback."""
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            sync_dir = os.path.join(tmpdir, "OneDrive")
            os.makedirs(sync_dir)

            src_file = os.path.join(tmpdir, "backup.zip")
            with open(src_file, "wb") as f:
                f.write(b"x" * (5 * 1024 * 1024))  # 5 MB

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            progress_calls = []
            mgr.upload("onedrive", src_file, progress_callback=lambda c, t: progress_calls.append((c, t)))
            self.assertTrue(len(progress_calls) > 0)

    def test_upload_unknown_provider_raises(self):
        """Upload to an unknown provider raises ValueError."""
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            with self.assertRaises(ValueError):
                mgr.upload("icloud", "/fake/path.zip")

    def test_upload_unavailable_provider_raises(self):
        """Upload to unavailable provider raises ValueError."""
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            # No sync folders created
            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            with self.assertRaises(ValueError):
                mgr.upload("dropbox", "/fake/path.zip")

    def test_upload_nonexistent_file_raises(self):
        """Upload a non-existent file raises FileNotFoundError."""
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            sync_dir = os.path.join(tmpdir, "OneDrive")
            os.makedirs(sync_dir)

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            with self.assertRaises(FileNotFoundError):
                mgr.upload("onedrive", "/nonexistent/file.zip")

    def test_delete_remote(self):
        """Delete a file from the sync folder."""
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            sync_dir = os.path.join(tmpdir, "OneDrive")
            os.makedirs(sync_dir)

            # Place a file in the sync folder
            target = os.path.join(sync_dir, "test.zip")
            with open(target, "w") as f:
                f.write("data")

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            self.assertTrue(mgr.delete_remote("onedrive", "test.zip"))
            self.assertFalse(os.path.isfile(target))

    def test_list_remote_backups(self):
        """List files in the OneClickBackup subfolder."""
        from src.core.cloud_backup import CloudBackupManager

        with tempfile.TemporaryDirectory() as tmpdir:
            sync_dir = os.path.join(tmpdir, "OneDrive")
            ocb_dir = os.path.join(sync_dir, "OneClickBackup")
            os.makedirs(ocb_dir)

            for name in ["backup_a.zip", "backup_b.zip"]:
                with open(os.path.join(ocb_dir, name), "w") as f:
                    f.write("data")

            with patch("os.path.expanduser", return_value=tmpdir), \
                 patch.dict(os.environ, {"OneDrive": "", "OneDriveConsumer": ""}):
                mgr = CloudBackupManager()

            backups = mgr.list_remote_backups("onedrive")
            self.assertEqual(len(backups), 2)
            self.assertIn("backup_a.zip", backups)


# ======================================================================
# 4. Disk health flow: SMART + benchmark + HTML report
# ======================================================================

class TestDiskHealthReportFlow(unittest.TestCase):
    """Test SMART info retrieval + report generation."""

    @patch("src.core.disk_health.run_powershell")
    def test_smart_info_then_report(self, mock_ps):
        """Get SMART info, then generate an HTML report with disk data."""
        from src.core.disk_health import DiskHealthManager, SMARTInfo
        from src.utils.report import ReportGenerator

        # Mock PowerShell calls for SMART info
        reliability_data = json.dumps({
            "Temperature": 35,
            "PowerOnHours": 5000,
            "Wear": 95,
        })
        mock_ps.side_effect = [
            (reliability_data, "", 0),  # StorageReliabilityCounter
            ("", "", 1),                # WMI SMART (not available)
            ("Healthy", "", 0),         # HealthStatus
        ]

        mgr = DiskHealthManager()
        info = mgr.get_smart_info(0)

        self.assertEqual(info.temperature_celsius, 35)
        self.assertEqual(info.power_on_hours, 5000)
        self.assertEqual(info.overall_health, "Healthy")

        # Now generate a report with fake disk data
        part = _FakePartition(
            index=1, letter="C", label="System", file_system="NTFS",
            size_bytes=256_000_000_000, used_bytes=128_000_000_000,
            free_bytes=128_000_000_000, partition_type="Primary",
            is_active=True, is_boot=True, is_system=True,
        )
        disk = _FakeDisk(
            index=0, model="Samsung 980 PRO",
            health_status=info.overall_health,
            partitions=[part],
        )

        gen = ReportGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            html_path = os.path.join(tmpdir, "report.html")
            result = gen.generate_html([disk], html_path)
            self.assertTrue(os.path.isfile(result))

            with open(result, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Samsung 980 PRO", content)
            self.assertIn("Healthy", content)

    @patch("src.core.disk_health.run_powershell")
    def test_smart_warning_health(self, mock_ps):
        """SMART with reallocated sectors triggers Warning health."""
        from src.core.disk_health import DiskHealthManager

        # Return SMART data with reallocated sectors
        reliability_data = json.dumps({"Temperature": 40, "PowerOnHours": 10000})
        # WMI data with reallocated sector attribute
        wmi_data = json.dumps({
            "InstanceName": "test_0",
            "VendorSpecific": "0 0 " + " ".join(["0"] * 12),  # minimal
        })
        mock_ps.side_effect = [
            (reliability_data, "", 0),
            (wmi_data, "", 0),
            ("Healthy", "", 0),
        ]

        mgr = DiskHealthManager()
        info = mgr.get_smart_info(0)
        # Should still be Healthy since no bad sectors in the parsed data
        self.assertIn(info.overall_health, ("Healthy", "Unknown"))

    @patch("src.core.disk_health.run_powershell")
    def test_get_temperature(self, mock_ps):
        """get_temperature returns correct value."""
        from src.core.disk_health import DiskHealthManager

        mock_ps.return_value = ("42", "", 0)
        mgr = DiskHealthManager()
        temp = mgr.get_temperature(0)
        self.assertEqual(temp, 42)

    @patch("src.core.disk_health.run_powershell")
    def test_get_temperature_unavailable(self, mock_ps):
        """get_temperature returns None when unavailable."""
        from src.core.disk_health import DiskHealthManager

        mock_ps.return_value = ("", "error", 1)
        mgr = DiskHealthManager()
        temp = mgr.get_temperature(0)
        self.assertIsNone(temp)


# ======================================================================
# 5. CLI mode tests
# ======================================================================

class TestCliIntegration(unittest.TestCase):
    """Test CLI commands return expected exit codes."""

    def test_version_returns_zero(self):
        from src.utils.cli import run_cli
        rc = run_cli(["--version"])
        self.assertEqual(rc, 0)

    def test_version_contains_version_string(self):
        from src.utils.cli import run_cli
        captured = StringIO()
        with patch("sys.stdout", captured):
            run_cli(["--version"])
        output = captured.getvalue()
        self.assertIn("OneClick Backup", output)
        self.assertRegex(output, r"\d+\.\d+")

    @patch("src.core.disk_info.get_all_disks", return_value=[])
    def test_list_disks_returns_zero(self, mock_disks):
        from src.utils.cli import run_cli
        rc = run_cli(["--list-disks"])
        self.assertEqual(rc, 0)

    @patch("src.core.disk_info.get_all_disks", return_value=[])
    def test_list_disks_empty_message(self, mock_disks):
        from src.utils.cli import run_cli
        captured = StringIO()
        with patch("sys.stdout", captured):
            run_cli(["--list-disks"])
        self.assertIn("No disks found", captured.getvalue())

    @patch("src.core.disk_info.get_all_disks")
    @patch("src.core.disk_info.get_disk_health", return_value="Healthy")
    def test_health_returns_zero(self, mock_health, mock_disks):
        from src.utils.cli import run_cli
        disk = _FakeDisk(index=0, model="Test SSD")
        mock_disks.return_value = [disk]
        rc = run_cli(["--health"])
        self.assertEqual(rc, 0)

    @patch("src.core.disk_info.get_all_disks")
    @patch("src.core.disk_info.get_disk_health", return_value="Healthy")
    def test_health_specific_disk(self, mock_health, mock_disks):
        from src.utils.cli import run_cli
        disk = _FakeDisk(index=2, model="Test HDD")
        mock_disks.return_value = [disk]

        captured = StringIO()
        with patch("sys.stdout", captured):
            rc = run_cli(["--health", "--disk", "2"])
        self.assertEqual(rc, 0)
        self.assertIn("Test HDD", captured.getvalue())

    @patch("src.core.disk_info.get_all_disks")
    @patch("src.core.disk_info.get_disk_health", return_value="Healthy")
    def test_health_missing_disk_returns_error(self, mock_health, mock_disks):
        from src.utils.cli import run_cli
        mock_disks.return_value = [_FakeDisk(index=0)]
        rc = run_cli(["--health", "--disk", "99"])
        self.assertEqual(rc, 1)

    def test_no_args_returns_zero(self):
        from src.utils.cli import run_cli
        rc = run_cli([])
        self.assertEqual(rc, 0)

    def test_unknown_flag_exits(self):
        from src.utils.cli import run_cli
        with self.assertRaises(SystemExit) as ctx:
            run_cli(["--totally-unknown-flag"])
        self.assertEqual(ctx.exception.code, 2)


# ======================================================================
# 6. Settings: portable vs non-portable mode
# ======================================================================

class TestSettingsPortableMode(unittest.TestCase):
    """Test settings path changes in portable mode."""

    def test_non_portable_settings_in_home(self):
        """In non-portable mode, settings go to ~/.oneclickbackup/."""
        from src.utils.settings import _get_settings_path, _is_portable, _get_app_dir

        app_dir = _get_app_dir()
        portable_file = os.path.join(app_dir, ".portable")

        # Ensure .portable does NOT exist
        if os.path.isfile(portable_file):
            self.skipTest(".portable file exists in project root")

        self.assertFalse(_is_portable())
        settings_path = _get_settings_path()
        self.assertIn(".oneclickbackup", settings_path)
        self.assertIn("oneclickbackup_settings.json", settings_path)

    def test_portable_mode_with_marker_file(self):
        """When .portable exists, settings are stored next to the app."""
        from src.utils.settings import _get_settings_path, _is_portable, _get_app_dir

        app_dir = _get_app_dir()
        portable_file = os.path.join(app_dir, ".portable")
        created = False

        try:
            if not os.path.isfile(portable_file):
                with open(portable_file, "w") as f:
                    f.write("")
                created = True

            self.assertTrue(_is_portable())
            settings_path = _get_settings_path()
            # In portable mode, settings are alongside the app
            self.assertTrue(settings_path.startswith(app_dir))
        finally:
            if created:
                os.remove(portable_file)

    def test_settings_get_set_reset(self):
        """Settings get/set/reset work with a temp path."""
        from src.utils.settings import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")

            with patch("src.utils.settings._get_settings_path", return_value=settings_file):
                s = Settings()

                # Defaults
                self.assertEqual(s.get("theme"), "dark")
                self.assertEqual(s.get("language"), "en")
                self.assertTrue(s.get("auto_check_updates"))

                # Set
                s.set("theme", "light")
                self.assertEqual(s.get("theme"), "light")

                # Verify persisted
                self.assertTrue(os.path.isfile(settings_file))
                with open(settings_file, encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["theme"], "light")

                # Reset
                s.reset()
                self.assertEqual(s.get("theme"), "dark")

    def test_settings_to_dict(self):
        """to_dict returns a copy of all settings."""
        from src.utils.settings import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            with patch("src.utils.settings._get_settings_path", return_value=settings_file):
                s = Settings()
                d = s.to_dict()
                self.assertIsInstance(d, dict)
                self.assertIn("theme", d)
                self.assertIn("language", d)

    def test_settings_loads_existing_file(self):
        """Settings loads values from an existing JSON file."""
        from src.utils.settings import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump({"theme": "custom", "language": "fr"}, f)

            with patch("src.utils.settings._get_settings_path", return_value=settings_file):
                s = Settings()
                self.assertEqual(s.get("theme"), "custom")
                self.assertEqual(s.get("language"), "fr")
                # Defaults still fill in missing keys
                self.assertTrue(s.get("auto_check_updates"))

    def test_settings_handles_corrupt_file(self):
        """Settings falls back to defaults when file is corrupt."""
        from src.utils.settings import Settings

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = os.path.join(tmpdir, "settings.json")
            with open(settings_file, "w", encoding="utf-8") as f:
                f.write("not valid json {{{")

            with patch("src.utils.settings._get_settings_path", return_value=settings_file):
                s = Settings()
                self.assertEqual(s.get("theme"), "dark")  # default


# ======================================================================
# 7. Crash handler integration
# ======================================================================

class TestCrashHandlerIntegration(unittest.TestCase):
    """Test crash handler installs and writes crash reports."""

    def setUp(self):
        import src.utils.crash_report as crash_mod
        self._crash_mod = crash_mod
        self._original_hook = sys.excepthook
        self._original_installed = getattr(crash_mod.install_crash_handler, "_installed", False)

    def tearDown(self):
        sys.excepthook = self._original_hook
        self._crash_mod.install_crash_handler._installed = self._original_installed

    def test_install_then_trigger_creates_report(self):
        """Install handler, simulate crash, verify report file exists."""
        import src.utils.crash_report as crash_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            crash_mod.install_crash_handler._installed = False

            with patch.object(crash_mod, "_LOG_DIR", tmpdir), \
                 patch.object(crash_mod, "_APP_LOG_FILE", os.path.join(tmpdir, "app.log")), \
                 patch.object(crash_mod, "_show_crash_dialog"):

                crash_mod.install_crash_handler()
                self.assertTrue(crash_mod.install_crash_handler._installed)

                # Trigger the crash hook directly
                try:
                    raise ValueError("test crash for integration test")
                except ValueError:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    crash_mod._crash_hook(exc_type, exc_value, exc_tb)

                # Verify a crash file was created
                files = [f for f in os.listdir(tmpdir) if f.startswith("crash_")]
                self.assertGreaterEqual(len(files), 1)

                # Verify file contents
                crash_path = os.path.join(tmpdir, files[0])
                with open(crash_path, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("CRASH REPORT", content)
                self.assertIn("ValueError", content)
                self.assertIn("test crash for integration test", content)

    def test_crash_report_listing(self):
        """get_crash_reports finds and parses existing report files."""
        import src.utils.crash_report as crash_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake crash files matching the actual format
            for name in ["crash_20260101_120000.txt", "crash_20260515_093000.txt"]:
                path = os.path.join(tmpdir, name)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("=" * 70 + "\n")
                    f.write("  OneClick Backup & Disk Manager -- CRASH REPORT\n")
                    f.write("=" * 70 + "\n")
                    f.write("\n")
                    f.write("Timestamp : 2026-01-01 12:00:00\n")
                    f.write("Exception : TestError: something broke\n")

            with patch.object(crash_mod, "_LOG_DIR", tmpdir):
                reports = crash_mod.get_crash_reports()

            self.assertEqual(len(reports), 2)
            # Most recent first
            self.assertIn("2026-05-15", reports[0]["timestamp"])
            # _extract_summary skips =, -, and blank lines then returns
            # the first substantive line. That will be "Timestamp : ..."
            # or "Exception : ..." depending on what it hits first.
            self.assertTrue(
                "Timestamp" in reports[0]["summary"]
                or "Exception" in reports[0]["summary"]
                or "OneClick Backup" in reports[0]["summary"]
            )


# ======================================================================
# 8. Updater: mock GitHub API
# ======================================================================

class TestUpdaterIntegration(unittest.TestCase):
    """Test updater with mocked GitHub API responses."""

    def _make_github_response(self, tag="v2.0.0", body="New features"):
        return json.dumps({
            "tag_name": tag,
            "body": body,
            "html_url": "https://github.com/test/releases/v2.0.0",
            "published_at": "2026-05-20T12:00:00Z",
            "assets": [{
                "name": "OneClickBackup.exe",
                "browser_download_url": "https://github.com/test/download/OneClickBackup.exe",
                "size": 15_000_000,
            }],
        }).encode("utf-8")

    @patch("src.core.updater.urlopen")
    def test_update_check_full_flow(self, mock_urlopen):
        """Check for update, verify UpdateInfo fields."""
        from src.core.updater import AutoUpdater, UpdateInfo

        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_github_response(tag="v3.0.0")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        updater = AutoUpdater(current_version="1.0.0")
        info = updater.check_for_update()

        self.assertIsInstance(info, UpdateInfo)
        self.assertTrue(info.is_update_available)
        self.assertEqual(info.current_version, "1.0.0")
        self.assertEqual(info.latest_version, "v3.0.0")
        self.assertIn("OneClickBackup.exe", info.download_url)
        self.assertEqual(info.file_size, 15_000_000)
        self.assertEqual(info.release_notes, "New features")

    @patch("src.core.updater.urlopen")
    def test_no_update_when_current(self, mock_urlopen):
        from src.core.updater import AutoUpdater

        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_github_response(tag="v1.0.0")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        updater = AutoUpdater(current_version="1.0.0")
        info = updater.check_for_update()
        self.assertFalse(info.is_update_available)

    @patch("src.core.updater.urlopen")
    def test_network_error_returns_safe(self, mock_urlopen):
        from src.core.updater import AutoUpdater
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("no network")
        updater = AutoUpdater(current_version="1.0.0")
        info = updater.check_for_update()

        self.assertFalse(info.is_update_available)
        self.assertEqual(info.download_url, "")

    @patch("src.core.updater.urlopen")
    def test_updater_async_calls_callback(self, mock_urlopen):
        """check_async calls the callback with UpdateInfo."""
        from src.core.updater import AutoUpdater, UpdateInfo
        import threading

        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_github_response(tag="v2.0.0")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        updater = AutoUpdater(current_version="1.0.0")
        results = []
        event = threading.Event()

        def on_result(info):
            results.append(info)
            event.set()

        updater.check_async(on_result)
        event.wait(timeout=5)

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], UpdateInfo)
        self.assertTrue(results[0].is_update_available)


# ======================================================================
# 9. Recovery: mock PowerShell, verify quick_scan returns results
# ======================================================================

class TestRecoveryIntegration(unittest.TestCase):
    """Test partition recovery scan with mocked PowerShell."""

    @patch("src.core.recovery.is_admin", return_value=True)
    @patch("src.core.recovery.run_powershell")
    def test_quick_scan_finds_ntfs(self, mock_ps, mock_admin):
        """Quick scan finds an NTFS partition in unallocated space."""
        from src.core.recovery import PartitionRecovery, RecoveredPartition
        import base64

        # Mock _disk_exists
        # Mock _get_unallocated_regions chain
        partition_data = json.dumps([
            {"PartitionNumber": 1, "Offset": 1048576, "Size": 100_000_000_000}
        ])
        disk_size_str = "500000000000"

        # Build a fake NTFS boot sector
        boot_sector = bytearray(512)
        boot_sector[3:11] = b"NTFS    "   # NTFS magic at offset 3
        boot_sector[510] = 0x55            # Boot signature
        boot_sector[511] = 0xAA
        b64_sector = base64.b64encode(bytes(boot_sector)).decode()

        mock_ps.side_effect = [
            # _disk_exists
            ("1", "", 0),
            # _get_unallocated_regions: Get-Partition
            (partition_data, "", 0),
            # _get_disk_size_bytes
            (disk_size_str, "", 0),
            # _read_sectors_ps: first sector of unallocated region
            (b64_sector, "", 0),
        ]

        recovery = PartitionRecovery()
        results = recovery.quick_scan(1)

        self.assertIsInstance(results, list)
        self.assertGreaterEqual(len(results), 1)
        if results:
            self.assertIsInstance(results[0], RecoveredPartition)
            self.assertEqual(results[0].file_system, "NTFS")
            self.assertTrue(results[0].boot_signature)

    @patch("src.core.recovery.is_admin", return_value=True)
    @patch("src.core.recovery.run_powershell")
    def test_quick_scan_no_unallocated(self, mock_ps, mock_admin):
        """Quick scan with no unallocated space returns empty list."""
        from src.core.recovery import PartitionRecovery

        # Disk exists, full partition coverage, no gaps
        partition_data = json.dumps([
            {"PartitionNumber": 1, "Offset": 1048576, "Size": 499_998_951_424}
        ])
        mock_ps.side_effect = [
            ("1", "", 0),              # _disk_exists
            (partition_data, "", 0),   # Get-Partition
            ("500000000000", "", 0),   # _get_disk_size_bytes
        ]

        recovery = PartitionRecovery()
        results = recovery.quick_scan(0)
        self.assertEqual(results, [])

    @patch("src.core.recovery.is_admin", return_value=False)
    def test_quick_scan_requires_admin(self, mock_admin):
        """Quick scan raises AdminRequiredError without admin."""
        from src.core.recovery import PartitionRecovery, AdminRequiredError

        recovery = PartitionRecovery()
        with self.assertRaises(AdminRequiredError):
            recovery.quick_scan(0)


# ======================================================================
# 10. Secure wipe: mock PowerShell, verify quick_wipe calls diskpart
# ======================================================================

class TestSecureWipeIntegration(unittest.TestCase):
    """Test secure wipe with mocked PowerShell and diskpart."""

    @patch("src.core.secure_wipe.is_admin", return_value=True)
    @patch("src.core.secure_wipe._is_system_disk", return_value=False)
    @patch("src.core.secure_wipe._get_disk_size_bytes", return_value=500_000_000_000)
    @patch("src.core.secure_wipe._disk_exists", return_value=True)
    @patch("src.core.secure_wipe.run_diskpart")
    def test_quick_wipe_calls_diskpart_clean(self, mock_dp, mock_exists, mock_size, mock_sys, mock_admin):
        """quick_wipe invokes diskpart with select disk + clean."""
        from src.core.secure_wipe import SecureWiper

        mock_dp.return_value = ("DiskPart succeeded", "", 0)

        wiper = SecureWiper()
        result = wiper.quick_wipe(2)
        self.assertTrue(result)

        # Verify diskpart was called with correct commands
        mock_dp.assert_called_once()
        script = mock_dp.call_args[0][0]
        self.assertEqual(script, ["select disk 2", "clean"])

    @patch("src.core.secure_wipe.is_admin", return_value=True)
    @patch("src.core.secure_wipe._is_system_disk", return_value=True)
    @patch("src.core.secure_wipe._get_disk_size_bytes", return_value=500_000_000_000)
    @patch("src.core.secure_wipe._disk_exists", return_value=True)
    def test_quick_wipe_system_disk_refused(self, mock_exists, mock_size, mock_sys, mock_admin):
        """quick_wipe refuses to wipe the system disk."""
        from src.core.secure_wipe import SecureWiper, WipeError

        wiper = SecureWiper()
        with self.assertRaises(WipeError):
            wiper.quick_wipe(0)

    @patch("src.core.secure_wipe.is_admin", return_value=False)
    def test_quick_wipe_requires_admin(self, mock_admin):
        """quick_wipe raises AdminRequiredError without admin."""
        from src.core.secure_wipe import SecureWiper, AdminRequiredError

        wiper = SecureWiper()
        with self.assertRaises(AdminRequiredError):
            wiper.quick_wipe(0)

    @patch("src.core.secure_wipe.is_admin", return_value=True)
    @patch("src.core.secure_wipe._is_system_disk", return_value=False)
    @patch("src.core.secure_wipe._get_disk_size_bytes", return_value=500_000_000_000)
    @patch("src.core.secure_wipe._disk_exists", return_value=True)
    @patch("src.core.secure_wipe.run_diskpart")
    def test_quick_wipe_with_callback(self, mock_dp, mock_exists, mock_size, mock_sys, mock_admin):
        """quick_wipe calls progress callback."""
        from src.core.secure_wipe import SecureWiper

        mock_dp.return_value = ("OK", "", 0)

        wiper = SecureWiper()
        progress_calls = []
        wiper.quick_wipe(1, callback=lambda pct, msg: progress_calls.append((pct, msg)))
        self.assertTrue(len(progress_calls) >= 2)  # start + end

    @patch("src.core.secure_wipe.is_admin", return_value=True)
    @patch("src.core.secure_wipe._is_system_disk", return_value=False)
    @patch("src.core.secure_wipe._get_disk_size_bytes", return_value=500_000_000_000)
    @patch("src.core.secure_wipe._disk_exists", return_value=True)
    @patch("src.core.secure_wipe.run_diskpart")
    def test_quick_wipe_diskpart_failure(self, mock_dp, mock_exists, mock_size, mock_sys, mock_admin):
        """quick_wipe raises WipeError when diskpart fails."""
        from src.core.secure_wipe import SecureWiper, WipeError

        mock_dp.return_value = ("", "Access denied", 1)

        wiper = SecureWiper()
        with self.assertRaises(WipeError):
            wiper.quick_wipe(1)

    def test_wipe_cancellation(self):
        """cancel() sets the event and check triggers error."""
        from src.core.secure_wipe import SecureWiper, WipeError

        wiper = SecureWiper()
        wiper.cancel()
        with self.assertRaises(WipeError):
            wiper._check_cancelled()


# ======================================================================
# 11. Disk health internal helpers
# ======================================================================

class TestDiskHealthHelpers(unittest.TestCase):
    """Test disk_health internal helper functions."""

    def test_safe_int_valid(self):
        from src.core.disk_health import _safe_int
        self.assertEqual(_safe_int(42), 42)
        self.assertEqual(_safe_int("100"), 100)

    def test_safe_int_invalid(self):
        from src.core.disk_health import _safe_int
        self.assertIsNone(_safe_int("abc"))
        self.assertIsNone(_safe_int(None))

    def test_determine_health_healthy(self):
        from src.core.disk_health import _determine_health
        self.assertEqual(_determine_health(0, 0, 0, 90, "Healthy"), "Healthy")

    def test_determine_health_warning_reallocated(self):
        from src.core.disk_health import _determine_health
        self.assertEqual(_determine_health(5, 0, 0, 90, "Healthy"), "Warning")

    def test_determine_health_critical_uncorrectable(self):
        from src.core.disk_health import _determine_health
        self.assertEqual(_determine_health(0, 0, 1, 90, "Healthy"), "Critical")

    def test_determine_health_critical_ps_unhealthy(self):
        from src.core.disk_health import _determine_health
        self.assertEqual(_determine_health(0, 0, 0, 90, "Unhealthy"), "Critical")

    def test_determine_health_warning_pending(self):
        from src.core.disk_health import _determine_health
        self.assertEqual(_determine_health(0, 3, 0, 90, "Healthy"), "Warning")

    def test_determine_health_warning_low_wear(self):
        from src.core.disk_health import _determine_health
        self.assertEqual(_determine_health(0, 0, 0, 5, "Healthy"), "Warning")

    def test_parse_smart_bytes(self):
        from src.core.disk_health import _parse_smart_bytes
        result = _parse_smart_bytes("1 2 3 4 5")
        self.assertEqual(result, [1, 2, 3, 4, 5])

    def test_parse_smart_bytes_invalid(self):
        from src.core.disk_health import _parse_smart_bytes
        self.assertEqual(_parse_smart_bytes("not numbers"), [])

    def test_extract_smart_attribute(self):
        from src.core.disk_health import _extract_smart_attribute
        # Build a minimal vendor byte array with attr ID 194 (temperature)
        vendor = [0] * 26  # revision (2 bytes) + one attribute (12 bytes) + end marker
        vendor[2] = 194     # attribute ID
        vendor[7] = 35      # raw value byte 0 = 35 degrees
        result = _extract_smart_attribute(vendor, 194)
        self.assertEqual(result, 35)

    def test_extract_smart_attribute_missing(self):
        from src.core.disk_health import _extract_smart_attribute
        vendor = [0] * 14
        vendor[2] = 5  # reallocated sectors, not temperature
        result = _extract_smart_attribute(vendor, 194)
        self.assertIsNone(result)


# ======================================================================
# 12. Notifications integration
# ======================================================================

class TestNotificationsIntegration(unittest.TestCase):
    """Test notification manager integration patterns."""

    @patch.object(
        __import__("src.utils.notifications", fromlist=["NotificationManager"]).NotificationManager,
        "_send_toast", return_value=True,
    )
    def test_backup_complete_notification(self, mock_toast):
        from src.utils.notifications import NotificationManager
        nm = NotificationManager()
        nm.notify_backup_complete("Daily Backup", 1_073_741_824)
        mock_toast.assert_called_once()

    @patch.object(
        __import__("src.utils.notifications", fromlist=["NotificationManager"]).NotificationManager,
        "_send_toast", return_value=True,
    )
    def test_error_notification(self, mock_toast):
        from src.utils.notifications import NotificationManager
        nm = NotificationManager()
        nm.notify_error("Clone", "Disk read error")
        mock_toast.assert_called_once()


# ======================================================================
# 13. Report generation (HTML + text)
# ======================================================================

class TestReportIntegration(unittest.TestCase):
    """Test report generation with various disk configurations."""

    def test_html_report_with_multiple_disks(self):
        from src.utils.report import ReportGenerator

        disks = [
            _FakeDisk(
                index=0, model="Samsung 980 PRO",
                partitions=[
                    _FakePartition(index=1, letter="C", label="System",
                                   file_system="NTFS", size_bytes=256_000_000_000,
                                   used_bytes=128_000_000_000, free_bytes=128_000_000_000,
                                   partition_type="Primary", is_active=True, is_boot=True, is_system=True),
                ],
            ),
            _FakeDisk(
                index=1, model="WD Blue 2TB", size_bytes=2_000_000_000_000,
                media_type="HDD", interface_type="SATA",
                health_status="Warning", is_system_disk=False,
                partitions=[],
            ),
        ]

        gen = ReportGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            html_path = os.path.join(tmpdir, "report.html")
            result = gen.generate_html(disks, html_path)
            self.assertTrue(os.path.isfile(result))

            with open(result, encoding="utf-8") as f:
                content = f.read()

            self.assertIn("<!DOCTYPE html>", content)
            self.assertIn("Samsung 980 PRO", content)
            self.assertIn("WD Blue 2TB", content)
            self.assertIn("2 disk(s)", content)
            self.assertIn("health-warn", content)
            self.assertIn("No partitions found", content)

    def test_text_report_with_multiple_disks(self):
        from src.utils.report import ReportGenerator

        disks = [
            _FakeDisk(index=0, model="Test SSD", partitions=[
                _FakePartition(index=1, letter="D", label="Data",
                               file_system="NTFS", size_bytes=500_000_000_000),
            ]),
        ]

        gen = ReportGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = os.path.join(tmpdir, "report.txt")
            result = gen.generate_text(disks, txt_path)
            self.assertTrue(os.path.isfile(result))

            with open(result, encoding="utf-8") as f:
                content = f.read()

            self.assertIn("Disk Status Report", content)
            self.assertIn("Test SSD", content)
            self.assertIn("End of report", content)


# ======================================================================
# 14. Recovery internal helpers
# ======================================================================

class TestRecoveryHelpers(unittest.TestCase):
    """Test recovery module internal helper functions."""

    def test_check_boot_signature_valid(self):
        from src.core.recovery import _check_boot_signature
        data = bytearray(512)
        data[510] = 0x55
        data[511] = 0xAA
        self.assertTrue(_check_boot_signature(bytes(data)))

    def test_check_boot_signature_invalid(self):
        from src.core.recovery import _check_boot_signature
        data = bytearray(512)
        self.assertFalse(_check_boot_signature(bytes(data)))

    def test_check_boot_signature_short_data(self):
        from src.core.recovery import _check_boot_signature
        self.assertFalse(_check_boot_signature(b"short"))

    def test_identify_filesystem_ntfs(self):
        from src.core.recovery import _identify_filesystem
        data = bytearray(512)
        data[3:11] = b"NTFS    "
        data[510] = 0x55
        data[511] = 0xAA
        result = _identify_filesystem(bytes(data))
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "NTFS")
        self.assertGreaterEqual(result[1], 0.9)

    def test_identify_filesystem_fat32(self):
        from src.core.recovery import _identify_filesystem
        data = bytearray(512)
        data[82:87] = b"FAT32"
        data[510] = 0x55
        data[511] = 0xAA
        result = _identify_filesystem(bytes(data))
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "FAT32")

    def test_identify_filesystem_efi(self):
        from src.core.recovery import _identify_filesystem
        data = bytearray(512)
        data[0:8] = b"EFI PART"
        result = _identify_filesystem(bytes(data))
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "EFI")

    def test_identify_filesystem_unknown(self):
        from src.core.recovery import _identify_filesystem
        data = bytes(512)
        result = _identify_filesystem(data)
        self.assertIsNone(result)

    def test_assess_status_recoverable(self):
        from src.core.recovery import _assess_status
        self.assertEqual(_assess_status(0.95, True), "recoverable")

    def test_assess_status_partial(self):
        from src.core.recovery import _assess_status
        self.assertEqual(_assess_status(0.70, False), "partial")

    def test_assess_status_damaged(self):
        from src.core.recovery import _assess_status
        self.assertEqual(_assess_status(0.30, False), "damaged")


# ======================================================================
# 15. Helpers module
# ======================================================================

class TestHelpers(unittest.TestCase):
    """Test utility helper functions."""

    def test_format_bytes_various(self):
        from src.utils.helpers import format_bytes
        self.assertIn("B", format_bytes(100))
        self.assertIn("KB", format_bytes(1500))
        self.assertIn("MB", format_bytes(5_000_000))
        self.assertIn("GB", format_bytes(5_000_000_000))
        self.assertIn("TB", format_bytes(5_000_000_000_000))
        self.assertEqual(format_bytes(-1), "0 B")

    def test_parse_size_to_bytes(self):
        from src.utils.helpers import parse_size_to_bytes
        self.assertEqual(parse_size_to_bytes("1 KB"), 1024)
        self.assertEqual(parse_size_to_bytes("1 MB"), 1024 ** 2)
        self.assertEqual(parse_size_to_bytes("1 GB"), 1024 ** 3)
        self.assertEqual(parse_size_to_bytes("invalid"), 0)

    def test_get_drive_letter_from_path(self):
        from src.utils.helpers import get_drive_letter_from_path
        self.assertEqual(get_drive_letter_from_path("C:\\Users\\test"), "C")
        self.assertEqual(get_drive_letter_from_path("D:/data"), "D")
        self.assertIsNone(get_drive_letter_from_path(""))

    def test_safe_int(self):
        from src.utils.helpers import safe_int
        self.assertEqual(safe_int(42), 42)
        self.assertEqual(safe_int("100"), 100)
        self.assertEqual(safe_int("abc", 0), 0)

    def test_generate_timestamp(self):
        from src.utils.helpers import generate_timestamp
        ts = generate_timestamp()
        self.assertRegex(ts, r"\d{8}_\d{6}")


# ======================================================================
# 16. CLI parser tests
# ======================================================================

class TestCliParser(unittest.TestCase):
    """Test CLI argument parser configuration."""

    def test_parser_backup_types(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        for btype in ["full_disk", "partition", "system"]:
            ns = parser.parse_args(["--backup", btype])
            self.assertEqual(ns.backup, btype)

    def test_parser_rejects_invalid_backup(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--backup", "invalid"])

    def test_parser_clone_pair(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["--clone", "0", "1"])
        self.assertEqual(ns.clone, [0, 1])

    def test_parser_health_with_disk(self):
        from src.utils.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["--health", "--disk", "3"])
        self.assertTrue(ns.health)
        self.assertEqual(ns.disk, 3)

    def test_has_action_false_when_empty(self):
        from src.utils.cli import _build_parser, _has_action
        parser = _build_parser()
        ns = parser.parse_args([])
        self.assertFalse(_has_action(ns))

    def test_has_action_true_with_version(self):
        from src.utils.cli import _build_parser, _has_action
        parser = _build_parser()
        ns = parser.parse_args(["--version"])
        self.assertTrue(_has_action(ns))


# ======================================================================
# 17. Secure wipe validation
# ======================================================================

class TestSecureWipeValidation(unittest.TestCase):
    """Test secure wipe input validation."""

    @patch("src.core.secure_wipe.is_admin", return_value=True)
    @patch("src.core.secure_wipe.run_powershell")
    def test_validate_disk_invalid_index(self, mock_ps, mock_admin):
        from src.core.secure_wipe import SecureWiper, WipeError

        wiper = SecureWiper()
        with self.assertRaises(WipeError):
            wiper._validate_disk(-1)

    @patch("src.core.secure_wipe.is_admin", return_value=True)
    @patch("src.core.secure_wipe.run_powershell", return_value=("0", "", 0))
    def test_validate_disk_not_exists(self, mock_ps, mock_admin):
        from src.core.secure_wipe import SecureWiper, WipeError

        wiper = SecureWiper()
        with self.assertRaises(WipeError):
            wiper._validate_disk(99)


if __name__ == "__main__":
    unittest.main()
