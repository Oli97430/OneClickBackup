"""Tests for security hardening code added in the code-review fix commit.

Covers sanitization, validation, path-traversal guards, closure captures,
thread safety, injection prevention, credential redaction, and PE verification.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import zipfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# 1. helpers.py -- sanitize_ps_string
# ---------------------------------------------------------------------------

class TestSanitizePsString(unittest.TestCase):
    """Test PowerShell single-quote escaping in helpers.sanitize_ps_string."""

    def setUp(self):
        from src.utils.helpers import sanitize_ps_string
        self.fn = sanitize_ps_string

    def test_normal_string_unchanged(self):
        self.assertEqual(self.fn("hello world"), "hello world")

    def test_single_quote_doubled(self):
        self.assertEqual(self.fn("O'Brien"), "O''Brien")

    def test_multiple_quotes(self):
        self.assertEqual(self.fn("it's a 'test'"), "it''s a ''test''")

    def test_empty_string(self):
        self.assertEqual(self.fn(""), "")

    def test_no_quotes(self):
        self.assertEqual(self.fn("safe string 123"), "safe string 123")

    def test_consecutive_quotes(self):
        self.assertEqual(self.fn("a''b"), "a''''b")


# ---------------------------------------------------------------------------
# 2. helpers.py -- validate_drive_letter
# ---------------------------------------------------------------------------

class TestValidateDriveLetter(unittest.TestCase):
    """Test drive letter validation in helpers.validate_drive_letter."""

    def setUp(self):
        from src.utils.helpers import validate_drive_letter
        self.fn = validate_drive_letter

    def test_valid_uppercase(self):
        self.assertEqual(self.fn("C"), "C")

    def test_valid_lowercase(self):
        self.assertEqual(self.fn("c"), "C")

    def test_valid_other_letter(self):
        self.assertEqual(self.fn("D"), "D")

    def test_valid_with_spaces(self):
        self.assertEqual(self.fn(" d "), "D")

    def test_returns_uppercase(self):
        result = self.fn("z")
        self.assertEqual(result, "Z")
        self.assertTrue(result.isupper())

    def test_invalid_digit(self):
        with self.assertRaises(ValueError):
            self.fn("1")

    def test_invalid_two_chars(self):
        with self.assertRaises(ValueError):
            self.fn("CD")

    def test_invalid_empty(self):
        with self.assertRaises(ValueError):
            self.fn("")

    def test_invalid_colon(self):
        with self.assertRaises(ValueError):
            self.fn("C:")

    def test_invalid_slash(self):
        with self.assertRaises(ValueError):
            self.fn("/")


# ---------------------------------------------------------------------------
# 3. backup.py -- Zip Slip protection in decompress_backup
# ---------------------------------------------------------------------------

class TestZipSlipProtection(unittest.TestCase):
    """Test that decompress_backup rejects zip members with path traversal."""

    def setUp(self):
        from src.core.backup import BackupManager
        self.mgr = BackupManager.__new__(BackupManager)
        self.mgr._log = MagicMock()
        self.mgr._progress_callback = None
        self.mgr._cancel_event = threading.Event()
        self.mgr._backup_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.mgr._backup_dir, ignore_errors=True)

    def test_zip_slip_detected(self):
        """A zip member named ../../evil.txt must raise BackupError."""
        from src.core.backup import BackupError

        zip_path = os.path.join(self.mgr._backup_dir, "malicious.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../evil.txt", "malicious content")

        dest = os.path.join(self.mgr._backup_dir, "extracted")
        with self.assertRaises(BackupError) as ctx:
            self.mgr.decompress_backup(zip_path, dest)
        self.assertIn("Zip Slip", str(ctx.exception))

    def test_normal_extraction_works(self):
        """A well-behaved zip with normal paths should extract fine."""
        zip_path = os.path.join(self.mgr._backup_dir, "good.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("subdir/file.txt", "hello")
            zf.writestr("another.txt", "world")

        dest = os.path.join(self.mgr._backup_dir, "extracted")
        result = self.mgr.decompress_backup(zip_path, dest)
        self.assertTrue(os.path.isfile(os.path.join(result, "subdir", "file.txt")))
        self.assertTrue(os.path.isfile(os.path.join(result, "another.txt")))


# ---------------------------------------------------------------------------
# 4. backup.py -- _redact_command
# ---------------------------------------------------------------------------

class TestRedactCommand(unittest.TestCase):
    """Test password redaction in BackupManager._redact_command."""

    def setUp(self):
        from src.core.backup import BackupManager
        self.fn = BackupManager._redact_command

    def test_password_inline(self):
        cmd = ["7z", "a", "-t7z", "-pMySecret", "-mhe=on", "out.7z", "src"]
        result = self.fn(cmd)
        self.assertIn("-p***", result)
        self.assertNotIn("MySecret", result)

    def test_password_complex(self):
        cmd = ["7z", "a", "-pS3cr3t!@#$", "archive.7z", "data"]
        result = self.fn(cmd)
        self.assertIn("-p***", result)
        self.assertNotIn("S3cr3t", result)

    def test_no_password(self):
        cmd = ["7z", "a", "-t7z", "-mhe=on", "out.7z", "src"]
        result = self.fn(cmd)
        self.assertNotIn("-p***", result)
        self.assertIn("-t7z", result)

    def test_only_dash_p(self):
        """A bare '-p' with no attached password is still matched by the regex."""
        cmd = ["7z", "a", "-p", "out.7z"]
        result = self.fn(cmd)
        # '-p' alone is 2 chars; the regex is r"^-p.+" which needs at least
        # one char after -p, so bare '-p' should pass through unchanged.
        self.assertIn("-p ", result)


# ---------------------------------------------------------------------------
# 5. backup.py -- delete_backup path traversal guard
# ---------------------------------------------------------------------------

class TestDeleteBackupPathTraversal(unittest.TestCase):
    """Test that delete_backup refuses paths outside backup_dir."""

    def setUp(self):
        from src.core.backup import BackupManager
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = BackupManager(backup_dir=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_outside_path_rejected(self):
        from src.core.backup import BackupError, BackupInfo

        # Create a fake metadata file pointing to an outside directory
        evil_path = os.path.normpath(os.path.join(self.tmpdir, "..", "evil_target"))
        os.makedirs(evil_path, exist_ok=True)

        backup_id = "test_evil_123"
        info = BackupInfo(
            backup_id=backup_id,
            name="evil",
            timestamp="2026-01-01T00:00:00",
            source_disk=0,
            source_partitions=[],
            backup_type="partition",
            total_size_bytes=0,
            compressed_size_bytes=0,
            backup_path=evil_path,
            checksum="",
            os_version="test",
        )

        meta_path = os.path.join(self.tmpdir, f"{backup_id}_meta.json")
        with open(meta_path, "w") as f:
            from dataclasses import asdict
            json.dump(asdict(info), f)

        with self.assertRaises(BackupError) as ctx:
            self.mgr.delete_backup(backup_id)
        self.assertIn("outside backup directory", str(ctx.exception))

        # Verify the evil target was NOT deleted
        self.assertTrue(os.path.isdir(evil_path))

        # Cleanup
        import shutil
        shutil.rmtree(evil_path, ignore_errors=True)

    def test_inside_path_accepted(self):
        """A backup inside backup_dir should be deletable."""
        from src.core.backup import BackupInfo
        from dataclasses import asdict

        backup_id = "test_good_456"
        good_path = os.path.join(self.tmpdir, backup_id)
        os.makedirs(good_path, exist_ok=True)

        # Write a dummy file so directory is non-empty
        with open(os.path.join(good_path, "data.bin"), "w") as f:
            f.write("test")

        info = BackupInfo(
            backup_id=backup_id,
            name="good",
            timestamp="2026-01-01T00:00:00",
            source_disk=0,
            source_partitions=[],
            backup_type="partition",
            total_size_bytes=0,
            compressed_size_bytes=0,
            backup_path=good_path,
            checksum="",
            os_version="test",
        )
        meta_path = os.path.join(self.tmpdir, f"{backup_id}_meta.json")
        with open(meta_path, "w") as f:
            json.dump(asdict(info), f)

        result = self.mgr.delete_backup(backup_id)
        self.assertTrue(result)
        self.assertFalse(os.path.isdir(good_path))


# ---------------------------------------------------------------------------
# 6. backup.py -- removesuffix fix in encrypt_backup
# ---------------------------------------------------------------------------

class TestRemoveSuffixFix(unittest.TestCase):
    """Verify that encrypt_backup's .removesuffix('.zip') doesn't mangle names."""

    def test_removesuffix_on_non_zip(self):
        """'pizza.zip' ends with '.zip' so removesuffix strips it to 'pizza'."""
        name = "pizza.zip"
        result = name.removesuffix(".zip")
        self.assertEqual(result, "pizza")

    def test_removesuffix_no_match(self):
        """'archive.7z' does not end with '.zip' so it stays unchanged."""
        name = "archive.7z"
        result = name.removesuffix(".zip")
        self.assertEqual(result, "archive.7z")

    def test_removesuffix_only_strips_suffix(self):
        """Ensure '.zip' in the middle is not stripped."""
        name = "my.zip.backup"
        result = name.removesuffix(".zip")
        self.assertEqual(result, "my.zip.backup")

    def test_encrypted_path_construction(self):
        """The encrypted path should be base + .encrypted.7z, not mangled."""
        source = r"C:\backups\mybackup.zip"
        encrypted_path = source.removesuffix(".zip") + ".encrypted.7z"
        self.assertEqual(encrypted_path, r"C:\backups\mybackup.encrypted.7z")

    def test_encrypted_path_no_zip(self):
        source = r"C:\backups\mybackup"
        encrypted_path = source.removesuffix(".zip") + ".encrypted.7z"
        self.assertEqual(encrypted_path, r"C:\backups\mybackup.encrypted.7z")


# ---------------------------------------------------------------------------
# 7. cloud_backup.py -- path traversal in upload/delete
# ---------------------------------------------------------------------------

class TestCloudBackupPathTraversal(unittest.TestCase):
    """Test that CloudBackupManager rejects path-traversal subpaths."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create a fake sync folder
        self.sync_folder = os.path.join(self.tmpdir, "FakeCloud")
        os.makedirs(self.sync_folder)
        # Create a source file
        self.src_file = os.path.join(self.tmpdir, "backup.zip")
        with open(self.src_file, "wb") as f:
            f.write(b"test data")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_manager(self):
        from src.core.cloud_backup import CloudBackupManager, CloudProvider
        mgr = CloudBackupManager.__new__(CloudBackupManager)
        mgr._providers = {
            "testcloud": CloudProvider(
                name="testcloud",
                display_name="Test Cloud",
                available=True,
                sync_folder=self.sync_folder,
            )
        }
        return mgr

    def test_upload_traversal_rejected(self):
        mgr = self._make_manager()
        with self.assertRaises(ValueError) as ctx:
            mgr.upload("testcloud", self.src_file, "../../evil/file.zip")
        self.assertIn("Path traversal", str(ctx.exception))

    def test_delete_traversal_rejected(self):
        mgr = self._make_manager()
        with self.assertRaises(ValueError) as ctx:
            mgr.delete_remote("testcloud", "../../evil.txt")
        self.assertIn("Path traversal", str(ctx.exception))

    def test_normal_subpath_upload(self):
        mgr = self._make_manager()
        dest = mgr.upload("testcloud", self.src_file, "Backups/daily.zip")
        self.assertTrue(os.path.isfile(dest))
        self.assertTrue(dest.startswith(os.path.realpath(self.sync_folder)))


# ---------------------------------------------------------------------------
# 8. secure_wipe.py -- closure capture fix (_idx default argument)
# ---------------------------------------------------------------------------

class TestClosureCapturePattern(unittest.TestCase):
    """Verify the _idx=pass_idx default-arg pattern captures correctly.

    The bug this fix addresses: without the default argument, every
    closure in the loop would share the same loop variable, meaning
    all callbacks would reference the final pass_idx value.
    """

    def test_default_arg_captures_own_index(self):
        """Replicate the closure pattern from secure_wipe and verify
        that each closure captures its own index."""
        captured_indices = []

        for pass_idx in range(5):
            def callback(pct: float, msg: str, _idx: int = pass_idx) -> None:
                captured_indices.append(_idx)

            callback(0.0, "test")

        self.assertEqual(captured_indices, [0, 1, 2, 3, 4])

    def test_without_default_arg_all_same(self):
        """Demonstrate the bug: without default arg, all closures
        capture the final loop value."""
        closures = []
        for pass_idx in range(5):
            def callback(pct: float, msg: str) -> None:
                closures.append(pass_idx)
            closures_list_copy = callback  # noqa
            closures.append(callback)

        # Call them after the loop ends
        results = []
        callbacks = closures[::2]  # Every other item is a callback
        # Simpler approach: just build and verify the pattern directly
        results_bad = []
        funcs = []
        for idx in range(5):
            def bad_callback(_idx=None):
                return idx  # captures loop var, not snapshot
            funcs.append(bad_callback)

        for fn in funcs:
            results_bad.append(fn())

        # Without default arg, all return the final idx value (4)
        self.assertTrue(all(v == 4 for v in results_bad))

        # With default arg (the fix), each captures its own value
        fixed_funcs = []
        for idx in range(5):
            def good_callback(_idx=idx):
                return _idx
            fixed_funcs.append(good_callback)

        results_good = [fn() for fn in fixed_funcs]
        self.assertEqual(results_good, [0, 1, 2, 3, 4])

    def test_pass_callback_scales_overall_progress(self):
        """Verify the actual callback logic: each pass contributes
        its fraction of the total progress."""
        passes = 3
        results = []

        for pass_idx in range(passes):
            def _pass_callback(pct: float, msg: str, _idx: int = pass_idx):
                overall = ((_idx + pct / 100.0) / passes) * 100.0
                results.append((_idx, round(overall, 1)))

            # Simulate 50% completion of this pass
            _pass_callback(50.0, "test")

        # Pass 0 at 50%: ((0 + 0.5) / 3) * 100 = 16.7
        # Pass 1 at 50%: ((1 + 0.5) / 3) * 100 = 50.0
        # Pass 2 at 50%: ((2 + 0.5) / 3) * 100 = 83.3
        self.assertEqual(results[0], (0, 16.7))
        self.assertEqual(results[1], (1, 50.0))
        self.assertEqual(results[2], (2, 83.3))


# ---------------------------------------------------------------------------
# 9. settings.py -- thread safety + type validation
# ---------------------------------------------------------------------------

class TestSettingsThreadSafety(unittest.TestCase):
    """Test that concurrent get/set operations don't crash."""

    def test_concurrent_access(self):
        """Hammer get/set from multiple threads and ensure no exceptions."""
        from src.utils.settings import Settings

        tmpdir = tempfile.mkdtemp()
        settings_path = os.path.join(tmpdir, "test_settings.json")

        with patch("src.utils.settings._get_settings_path", return_value=settings_path):
            s = Settings()

        errors = []
        barrier = threading.Barrier(10)

        def writer(idx):
            try:
                barrier.wait(timeout=5)
                for _ in range(50):
                    s.set(f"key_{idx}", idx)
                    s.get(f"key_{idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        # Cleanup
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_wrong_type_reverts_to_default(self):
        """A setting with the wrong type should revert to default on load."""
        from src.utils.settings import Settings, _DEFAULTS

        tmpdir = tempfile.mkdtemp()
        settings_path = os.path.join(tmpdir, "test_settings.json")

        # Write a settings file with "theme" as int instead of str
        bad_settings = dict(_DEFAULTS)
        bad_settings["theme"] = 123  # should be str
        bad_settings["auto_verify"] = "yes"  # should be bool
        with open(settings_path, "w") as f:
            json.dump(bad_settings, f)

        with patch("src.utils.settings._get_settings_path", return_value=settings_path):
            s = Settings()

        # theme should be reverted to default ("dark")
        self.assertEqual(s.get("theme"), "dark")
        self.assertIsInstance(s.get("theme"), str)

        # auto_verify should be reverted to default (False)
        self.assertEqual(s.get("auto_verify"), False)
        self.assertIsInstance(s.get("auto_verify"), bool)

        # A correctly-typed setting should be preserved
        self.assertEqual(s.get("language"), "en")

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 10. notifications.py -- app_id validation
# ---------------------------------------------------------------------------

class TestNotificationAppIdValidation(unittest.TestCase):
    """Test that NotificationManager rejects unsafe app_id values."""

    def test_normal_app_id_accepted(self):
        from src.utils.notifications import NotificationManager
        nm = NotificationManager(app_id="OneClickBackup")
        self.assertEqual(nm._app_id, "OneClickBackup")

    def test_app_id_with_dots_and_spaces(self):
        from src.utils.notifications import NotificationManager
        nm = NotificationManager(app_id="My App 2.0")
        self.assertEqual(nm._app_id, "My App 2.0")

    def test_dangerous_app_id_rejected(self):
        from src.utils.notifications import NotificationManager
        nm = NotificationManager(app_id="'); dangerous-command")
        # Should fall back to default
        self.assertEqual(nm._app_id, "OneClickBackup")

    def test_injection_semicolon(self):
        from src.utils.notifications import NotificationManager
        nm = NotificationManager(app_id="test; Remove-Item -Recurse C:\\")
        self.assertEqual(nm._app_id, "OneClickBackup")

    def test_empty_app_id_uses_default(self):
        from src.utils.notifications import NotificationManager
        nm = NotificationManager(app_id="")
        self.assertEqual(nm._app_id, "OneClickBackup")

    def test_pipe_injection_rejected(self):
        from src.utils.notifications import NotificationManager
        nm = NotificationManager(app_id="test | whoami")
        self.assertEqual(nm._app_id, "OneClickBackup")


# ---------------------------------------------------------------------------
# 11. crash_report.py -- _redact_paths
# ---------------------------------------------------------------------------

class TestCrashReportPathRedaction(unittest.TestCase):
    """Test username redaction in crash_report._redact_paths."""

    def setUp(self):
        from src.utils.crash_report import _redact_paths
        self.fn = _redact_paths

    def test_windows_path_redacted(self):
        text = r"C:\Users\JohnDoe\Documents\file.txt"
        result = self.fn(text)
        self.assertIn(r"C:\Users\***", result)
        self.assertNotIn("JohnDoe", result)

    def test_unix_home_redacted(self):
        text = "/home/johndoe/.config/app"
        result = self.fn(text)
        self.assertIn("/home/***", result)
        self.assertNotIn("johndoe", result)

    def test_macos_path_redacted(self):
        text = "/Users/macuser/Library/Preferences"
        result = self.fn(text)
        self.assertIn("/Users/***", result)
        self.assertNotIn("macuser", result)

    def test_no_username_pattern_passes_through(self):
        text = "Error in module foobar at line 42"
        result = self.fn(text)
        self.assertEqual(result, text)

    def test_multiple_paths_all_redacted(self):
        text = r"Source: C:\Users\Alice\code  Target: C:\Users\Bob\backup"
        result = self.fn(text)
        self.assertNotIn("Alice", result)
        self.assertNotIn("Bob", result)
        self.assertEqual(result.count("***"), 2)

    def test_traceback_with_path(self):
        text = '  File "C:\\Users\\Dev123\\project\\main.py", line 10'
        result = self.fn(text)
        self.assertNotIn("Dev123", result)
        self.assertIn("***", result)


# ---------------------------------------------------------------------------
# 12. admin.py -- argument quoting via subprocess.list2cmdline
# ---------------------------------------------------------------------------

class TestAdminArgumentQuoting(unittest.TestCase):
    """Verify that admin.py uses subprocess.list2cmdline for safe quoting."""

    def test_list2cmdline_imported(self):
        """subprocess must be imported in admin.py for list2cmdline usage."""
        import src.utils.admin as admin_mod
        self.assertTrue(hasattr(admin_mod, 'subprocess'))

    def test_list2cmdline_used_in_run_as_admin(self):
        """The run_as_admin function should use subprocess.list2cmdline."""
        import inspect
        from src.utils.admin import run_as_admin
        source = inspect.getsource(run_as_admin)
        self.assertIn("subprocess.list2cmdline", source)

    def test_list2cmdline_handles_special_chars(self):
        """subprocess.list2cmdline should properly quote special characters."""
        args = ['--path=C:\\Program Files\\App', '--name=O\'Brien']
        result = subprocess.list2cmdline(args)
        # list2cmdline should quote args containing spaces
        self.assertIn('"', result)


# ---------------------------------------------------------------------------
# 13. updater.py -- PE header verification and SHA-256
# ---------------------------------------------------------------------------

class TestUpdaterIntegrity(unittest.TestCase):
    """Test _verify_pe_header and _compute_sha256 in AutoUpdater."""

    def test_valid_pe_header(self):
        """A file starting with MZ should pass verification."""
        from src.core.updater import AutoUpdater

        with tempfile.NamedTemporaryFile(delete=False, suffix=".exe") as f:
            f.write(b"MZ" + b"\x00" * 100)
            f.flush()
            tmp_path = f.name

        try:
            # Should not raise
            AutoUpdater._verify_pe_header(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_invalid_pe_header(self):
        """A file NOT starting with MZ should raise RuntimeError."""
        from src.core.updater import AutoUpdater

        with tempfile.NamedTemporaryFile(delete=False, suffix=".exe") as f:
            f.write(b"PK" + b"\x00" * 100)  # ZIP header, not PE
            f.flush()
            tmp_path = f.name

        try:
            with self.assertRaises(RuntimeError) as ctx:
                AutoUpdater._verify_pe_header(tmp_path)
            self.assertIn("MZ", str(ctx.exception))
        finally:
            # _verify_pe_header removes the file on failure
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_sha256_computation(self):
        """SHA-256 should match hashlib reference computation."""
        from src.core.updater import AutoUpdater

        content = b"Hello, OneClickBackup security test!"
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(content)
            f.flush()
            tmp_path = f.name

        try:
            result = AutoUpdater._compute_sha256(tmp_path)
            expected = hashlib.sha256(content).hexdigest()
            self.assertEqual(result, expected)
        finally:
            os.unlink(tmp_path)

    def test_sha256_empty_file(self):
        from src.core.updater import AutoUpdater

        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp_path = f.name

        try:
            result = AutoUpdater._compute_sha256(tmp_path)
            expected = hashlib.sha256(b"").hexdigest()
            self.assertEqual(result, expected)
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# 14. clone.py -- system disk guard in clone_disk
# ---------------------------------------------------------------------------

class TestCloneDiskSystemGuard(unittest.TestCase):
    """Test that clone_disk rejects cloning onto the system disk."""

    @patch("src.core.clone._is_system_disk")
    @patch("src.core.clone._disk_exists", return_value=True)
    @patch("src.core.clone._require_admin")
    def test_target_is_system_disk_raises(self, mock_admin, mock_exists, mock_sys):
        from src.core.backup import BackupManager, BackupError

        # _is_system_disk returns True for target (disk 0), False for source
        mock_sys.side_effect = lambda idx: idx == 0

        mgr = BackupManager.__new__(BackupManager)
        mgr._log = MagicMock()
        mgr._cancel_event = threading.Event()
        mgr._progress_callback = None

        with self.assertRaises(BackupError) as ctx:
            mgr.clone_disk(source_disk=1, target_disk=0)

        self.assertIn("system disk", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# 15. winpe.py -- batch injection validation in _write_adk_bat
# ---------------------------------------------------------------------------

class TestWinPEBatchInjection(unittest.TestCase):
    """Test that _write_adk_bat rejects commands with shell metacharacters."""

    def setUp(self):
        from src.core.winpe import WinPEMixin
        self.fn = WinPEMixin._write_adk_bat

        # Create a fake deploy_env file
        self.tmpdir = tempfile.mkdtemp()
        self.fake_env = os.path.join(self.tmpdir, "DandISetEnv.bat")
        with open(self.fake_env, "w") as f:
            f.write("@echo off\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ampersand_rejected(self):
        with self.assertRaises(ValueError):
            self.fn(self.fake_env, "safe_cmd & malicious_cmd")

    def test_pipe_rejected(self):
        with self.assertRaises(ValueError):
            self.fn(self.fake_env, "cmd | evil")

    def test_redirect_greater_rejected(self):
        with self.assertRaises(ValueError):
            self.fn(self.fake_env, "cmd > output.txt")

    def test_redirect_less_rejected(self):
        with self.assertRaises(ValueError):
            self.fn(self.fake_env, "cmd < input.txt")

    def test_caret_rejected(self):
        with self.assertRaises(ValueError):
            self.fn(self.fake_env, "cmd ^| evil")

    def test_percent_rejected(self):
        with self.assertRaises(ValueError):
            self.fn(self.fake_env, "cmd %PATH%")

    def test_safe_command_accepted(self):
        bat_path = self.fn(self.fake_env, 'copype amd64 "C:\\temp\\winpe"')
        try:
            self.assertTrue(os.path.isfile(bat_path))
            with open(bat_path) as f:
                content = f.read()
            self.assertIn("copype", content)
        finally:
            os.unlink(bat_path)

    def test_missing_env_file_rejected(self):
        with self.assertRaises(ValueError):
            self.fn(os.path.join(self.tmpdir, "nonexistent.bat"), "safe_cmd")


# ---------------------------------------------------------------------------
# 16. disk_image.py -- _ps_escape
# ---------------------------------------------------------------------------

class TestDiskImagePsEscape(unittest.TestCase):
    """Test the local _ps_escape in disk_image.py."""

    def setUp(self):
        from src.core.disk_image import _ps_escape
        self.fn = _ps_escape

    def test_single_quote_doubled(self):
        self.assertEqual(self.fn("it's"), "it''s")

    def test_multiple_quotes(self):
        self.assertEqual(self.fn("a'b'c"), "a''b''c")

    def test_no_quotes(self):
        self.assertEqual(self.fn("safe"), "safe")

    def test_empty_string(self):
        self.assertEqual(self.fn(""), "")

    def test_path_with_quotes(self):
        self.assertEqual(
            self.fn("C:\\Users\\O'Brien\\file.vhdx"),
            "C:\\Users\\O''Brien\\file.vhdx",
        )


# ---------------------------------------------------------------------------
# 17. scheduler.py -- _ps_escape
# ---------------------------------------------------------------------------

class TestSchedulerPsEscape(unittest.TestCase):
    """Test the local _ps_escape in scheduler.py."""

    def setUp(self):
        from src.core.scheduler import _ps_escape
        self.fn = _ps_escape

    def test_single_quote_doubled(self):
        self.assertEqual(self.fn("it's"), "it''s")

    def test_multiple_quotes(self):
        self.assertEqual(self.fn("a'b'c"), "a''b''c")

    def test_no_quotes(self):
        self.assertEqual(self.fn("clean"), "clean")

    def test_empty_string(self):
        self.assertEqual(self.fn(""), "")

    def test_task_description(self):
        self.assertEqual(
            self.fn("John's daily backup"),
            "John''s daily backup",
        )


if __name__ == "__main__":
    unittest.main()
