"""Extra tests to boost coverage on non-UI, non-admin code paths.

Targets six modules:
    - disk_health.py   (helpers, SMART parsing, health verdict)
    - recovery.py      (signature detection, status assessment, dataclasses)
    - i18n.py          (thread safety, round-trip, format edge cases)
    - operations.py    (queue lifecycle, validation, dataclass, apply flow)
    - disk_info.py     (safe_* helpers, normalize helpers, dataclasses, cache)
    - admin.py         (is_admin mock, require_admin decorator)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ======================================================================
# 1. disk_health.py
# ======================================================================


class TestSafeInt(unittest.TestCase):
    """Tests for disk_health._safe_int."""

    def _f(self, value, default=None):
        from src.core.disk_health import _safe_int
        return _safe_int(value, default)

    def test_none_returns_default(self):
        self.assertIsNone(self._f(None))

    def test_none_with_explicit_default(self):
        self.assertEqual(self._f(None, 42), 42)

    def test_valid_int(self):
        self.assertEqual(self._f(7), 7)

    def test_string_int(self):
        self.assertEqual(self._f("123"), 123)

    def test_invalid_string(self):
        self.assertIsNone(self._f("abc"))

    def test_invalid_string_with_default(self):
        self.assertEqual(self._f("xyz", -1), -1)

    def test_float_truncates(self):
        self.assertEqual(self._f(3.9), 3)

    def test_zero(self):
        self.assertEqual(self._f(0), 0)

    def test_negative(self):
        self.assertEqual(self._f(-5), -5)

    def test_bool_true(self):
        # bool is a subclass of int in Python
        self.assertEqual(self._f(True), 1)


class TestRunPsJson(unittest.TestCase):
    """Tests for disk_health._run_ps_json."""

    @patch("src.core.disk_health.run_powershell")
    def test_success_parses_json(self, mock_rp):
        from src.core.disk_health import _run_ps_json
        mock_rp.return_value = ('{"Temperature": 42}', "", 0)
        result = _run_ps_json("some-cmd")
        self.assertEqual(result, {"Temperature": 42})

    @patch("src.core.disk_health.run_powershell")
    def test_nonzero_rc_returns_none(self, mock_rp):
        from src.core.disk_health import _run_ps_json
        mock_rp.return_value = ("", "error", 1)
        self.assertIsNone(_run_ps_json("fail-cmd"))

    @patch("src.core.disk_health.run_powershell")
    def test_empty_stdout_returns_none(self, mock_rp):
        from src.core.disk_health import _run_ps_json
        mock_rp.return_value = ("   ", "", 0)
        self.assertIsNone(_run_ps_json("empty-cmd"))

    @patch("src.core.disk_health.run_powershell")
    def test_invalid_json_returns_none(self, mock_rp):
        from src.core.disk_health import _run_ps_json
        mock_rp.return_value = ("NOT JSON {{{", "", 0)
        self.assertIsNone(_run_ps_json("bad-json"))

    @patch("src.core.disk_health.run_powershell")
    def test_stderr_logged_on_failure(self, mock_rp):
        from src.core.disk_health import _run_ps_json
        mock_rp.return_value = ("", "some error info", 1)
        self.assertIsNone(_run_ps_json("err-cmd"))


class TestParseSmartBytes(unittest.TestCase):
    """Tests for disk_health._parse_smart_bytes."""

    def _f(self, raw):
        from src.core.disk_health import _parse_smart_bytes
        return _parse_smart_bytes(raw)

    def test_valid_space_separated(self):
        self.assertEqual(self._f("10 20 30"), [10, 20, 30])

    def test_single_value(self):
        self.assertEqual(self._f("255"), [255])

    def test_empty_string(self):
        self.assertEqual(self._f(""), [])

    def test_invalid_input_returns_empty(self):
        self.assertEqual(self._f("abc def"), [])

    def test_none_input_returns_empty(self):
        self.assertEqual(self._f(None), [])

    def test_mixed_valid_invalid_returns_empty(self):
        # int() fails on "abc", so entire conversion fails
        self.assertEqual(self._f("1 2 abc 4"), [])


class TestExtractSmartAttribute(unittest.TestCase):
    """Tests for disk_health._extract_smart_attribute."""

    def _f(self, vendor_bytes, attr_id):
        from src.core.disk_health import _extract_smart_attribute
        return _extract_smart_attribute(vendor_bytes, attr_id)

    def _build_vendor_bytes(self, attrs):
        """Build a vendor byte array with a 2-byte header and attribute records.

        attrs: list of (attr_id, raw_value) tuples.
        Each attribute record is 12 bytes.
        """
        data = [0, 0]  # 2-byte revision header
        for aid, raw_val in attrs:
            record = [0] * 12
            record[0] = aid           # attribute ID
            record[1] = 0             # flags low
            record[2] = 0             # flags high
            record[3] = 100           # normalised value
            record[4] = 100           # worst value
            # raw value in bytes 5..10 (little-endian, 6 bytes)
            for i in range(6):
                record[5 + i] = (raw_val >> (8 * i)) & 0xFF
            record[11] = 0            # reserved
            data.extend(record)
        # Terminate with a zero-ID record
        data.extend([0] * 12)
        return data

    def test_too_short_returns_none(self):
        self.assertIsNone(self._f([0] * 10, 5))

    def test_not_found_returns_none(self):
        vendor = self._build_vendor_bytes([(194, 42)])
        self.assertIsNone(self._f(vendor, 5))

    def test_extract_temperature(self):
        vendor = self._build_vendor_bytes([(194, 35)])
        self.assertEqual(self._f(vendor, 194), 35)

    def test_extract_reallocated_sectors(self):
        vendor = self._build_vendor_bytes([(5, 3)])
        self.assertEqual(self._f(vendor, 5), 3)

    def test_extract_large_value(self):
        # Test a 6-byte value (e.g., total LBAs written)
        big_val = 0x0000AABBCCDDEE
        vendor = self._build_vendor_bytes([(241, big_val)])
        self.assertEqual(self._f(vendor, 241), big_val)

    def test_multiple_attributes_find_correct_one(self):
        vendor = self._build_vendor_bytes([
            (5, 10),    # reallocated
            (9, 5000),  # power-on hours
            (194, 40),  # temperature
        ])
        self.assertEqual(self._f(vendor, 9), 5000)
        self.assertEqual(self._f(vendor, 194), 40)

    def test_end_of_table_marker(self):
        # Build with one attr, then the zero-ID terminator should stop search
        vendor = self._build_vendor_bytes([(194, 30)])
        # Searching for a non-existent attr should return None
        self.assertIsNone(self._f(vendor, 197))


class TestDetermineHealth(unittest.TestCase):
    """Tests for disk_health._determine_health."""

    def _f(self, reallocated, pending, uncorrectable, wear, ps_health):
        from src.core.disk_health import _determine_health
        return _determine_health(reallocated, pending, uncorrectable, wear, ps_health)

    # --- Critical ---

    def test_uncorrectable_gt_0_critical(self):
        self.assertEqual(self._f(0, 0, 1, 100, "Healthy"), "Critical")

    def test_reallocated_gt_100_critical(self):
        self.assertEqual(self._f(101, 0, 0, 100, "Healthy"), "Critical")

    def test_ps_unhealthy_critical(self):
        self.assertEqual(self._f(None, None, None, None, "Unhealthy"), "Critical")

    def test_ps_degraded_critical(self):
        self.assertEqual(self._f(None, None, None, None, "Degraded"), "Critical")

    # --- Warning ---

    def test_reallocated_gt_0_warning(self):
        self.assertEqual(self._f(5, 0, 0, 100, "Healthy"), "Warning")

    def test_pending_gt_0_warning(self):
        self.assertEqual(self._f(0, 3, 0, 100, "Healthy"), "Warning")

    def test_wear_lt_10_warning(self):
        self.assertEqual(self._f(0, 0, 0, 5, "Healthy"), "Warning")

    # --- Healthy ---

    def test_all_zero_healthy(self):
        self.assertEqual(self._f(0, 0, 0, 100, "Healthy"), "Healthy")

    def test_ps_healthy_string(self):
        self.assertEqual(self._f(None, None, None, None, "Healthy"), "Healthy")

    def test_ps_status_zero(self):
        self.assertEqual(self._f(None, None, None, None, "0"), "Healthy")

    def test_all_zero_no_ps(self):
        self.assertEqual(self._f(0, 0, 0, None, ""), "Healthy")

    def test_all_none_unknown_ps(self):
        # No data at all but no negative indicators
        self.assertEqual(self._f(None, None, None, None, ""), "Healthy")


class TestSMARTInfoDataclass(unittest.TestCase):
    """Tests for disk_health.SMARTInfo dataclass construction."""

    def test_default_values(self):
        from src.core.disk_health import SMARTInfo
        info = SMARTInfo()
        self.assertIsNone(info.temperature_celsius)
        self.assertIsNone(info.power_on_hours)
        self.assertIsNone(info.reallocated_sectors)
        self.assertEqual(info.overall_health, "Unknown")
        self.assertEqual(info.raw_attributes, {})

    def test_custom_values(self):
        from src.core.disk_health import SMARTInfo
        info = SMARTInfo(temperature_celsius=42, overall_health="Healthy")
        self.assertEqual(info.temperature_celsius, 42)
        self.assertEqual(info.overall_health, "Healthy")


class TestBenchmarkResultDataclass(unittest.TestCase):
    """Tests for disk_health.BenchmarkResult dataclass."""

    def test_defaults_are_zero(self):
        from src.core.disk_health import BenchmarkResult
        br = BenchmarkResult()
        self.assertEqual(br.sequential_read_mbps, 0.0)
        self.assertEqual(br.random_write_iops, 0.0)


class TestDiskHealthManagerGetTemperature(unittest.TestCase):
    """Tests for DiskHealthManager.get_temperature with mocked PS."""

    @patch("src.core.disk_health.run_powershell")
    def test_returns_temperature(self, mock_rp):
        from src.core.disk_health import DiskHealthManager
        mock_rp.return_value = ("38\n", "", 0)
        mgr = DiskHealthManager()
        self.assertEqual(mgr.get_temperature(0), 38)

    @patch("src.core.disk_health.run_powershell")
    def test_returns_none_on_failure(self, mock_rp):
        from src.core.disk_health import DiskHealthManager
        mock_rp.return_value = ("", "not available", 1)
        mgr = DiskHealthManager()
        self.assertIsNone(mgr.get_temperature(0))

    @patch("src.core.disk_health.run_powershell")
    def test_returns_none_on_empty(self, mock_rp):
        from src.core.disk_health import DiskHealthManager
        mock_rp.return_value = ("", "", 0)
        mgr = DiskHealthManager()
        self.assertIsNone(mgr.get_temperature(0))


class TestDiskHealthManagerGetSmartInfo(unittest.TestCase):
    """Tests for DiskHealthManager.get_smart_info with mocked PS."""

    @patch("src.core.disk_health.run_powershell")
    def test_basic_smart_info(self, mock_rp):
        from src.core.disk_health import DiskHealthManager
        reliability_json = json.dumps({
            "Temperature": 35,
            "PowerOnHours": 1200,
            "Wear": 95,
        })
        # _fill_from_reliability_counter calls _run_ps_json -> run_powershell
        # _fill_from_wmi_smart calls _run_ps_json -> run_powershell
        # _get_ps_health_status calls run_powershell directly
        mock_rp.side_effect = [
            (reliability_json, "", 0),  # reliability counter
            ("", "", 1),                # WMI SMART (unavailable)
            ("Healthy", "", 0),         # health status
        ]
        mgr = DiskHealthManager()
        info = mgr.get_smart_info(0)
        self.assertEqual(info.temperature_celsius, 35)
        self.assertEqual(info.power_on_hours, 1200)
        self.assertEqual(info.wear_leveling_count, 95)
        self.assertEqual(info.overall_health, "Healthy")


# ======================================================================
# 2. recovery.py
# ======================================================================


class TestCheckBootSignature(unittest.TestCase):
    """Tests for recovery._check_boot_signature."""

    def _f(self, data):
        from src.core.recovery import _check_boot_signature
        return _check_boot_signature(data)

    def test_valid_boot_signature(self):
        sector = bytearray(512)
        sector[510] = 0x55
        sector[511] = 0xAA
        self.assertTrue(self._f(bytes(sector)))

    def test_invalid_boot_signature(self):
        sector = bytearray(512)
        sector[510] = 0x00
        sector[511] = 0x00
        self.assertFalse(self._f(bytes(sector)))

    def test_too_short(self):
        self.assertFalse(self._f(b"\x00" * 100))

    def test_partial_signature(self):
        sector = bytearray(512)
        sector[510] = 0x55
        sector[511] = 0x00
        self.assertFalse(self._f(bytes(sector)))


class TestIdentifyFilesystem(unittest.TestCase):
    """Tests for recovery._identify_filesystem."""

    def _f(self, data):
        from src.core.recovery import _identify_filesystem
        return _identify_filesystem(data)

    def _make_sector(self, magic_bytes=None, magic_offset=0, boot_sig=True):
        sector = bytearray(512)
        if magic_bytes and magic_offset is not None:
            sector[magic_offset:magic_offset + len(magic_bytes)] = magic_bytes
        if boot_sig:
            sector[510] = 0x55
            sector[511] = 0xAA
        return bytes(sector)

    def test_ntfs_with_boot_sig(self):
        sector = self._make_sector(b"NTFS    ", 3, True)
        result = self._f(sector)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "NTFS")
        self.assertEqual(result[1], 0.95)

    def test_ntfs_without_boot_sig(self):
        sector = self._make_sector(b"NTFS    ", 3, False)
        result = self._f(sector)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "NTFS")
        self.assertEqual(result[1], 0.70)

    def test_fat32_with_boot_sig(self):
        sector = self._make_sector(b"FAT32", 82, True)
        result = self._f(sector)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "FAT32")
        self.assertEqual(result[1], 0.90)

    def test_fat32_without_boot_sig(self):
        sector = self._make_sector(b"FAT32", 82, False)
        result = self._f(sector)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "FAT32")
        self.assertEqual(result[1], 0.65)

    def test_efi_signature(self):
        sector = self._make_sector(b"EFI PART", 0, False)
        result = self._f(sector)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "EFI")
        self.assertEqual(result[1], 0.98)

    def test_unknown_returns_none(self):
        sector = self._make_sector(None, 0, False)
        self.assertIsNone(self._f(sector))

    def test_too_short_returns_none(self):
        self.assertIsNone(self._f(b"\x00" * 100))


class TestAssessStatus(unittest.TestCase):
    """Tests for recovery._assess_status."""

    def _f(self, confidence, boot_sig):
        from src.core.recovery import _assess_status
        return _assess_status(confidence, boot_sig)

    def test_recoverable(self):
        self.assertEqual(self._f(0.95, True), "recoverable")

    def test_recoverable_threshold(self):
        self.assertEqual(self._f(0.85, True), "recoverable")

    def test_partial_high_confidence_no_boot(self):
        self.assertEqual(self._f(0.95, False), "partial")

    def test_partial_medium_confidence(self):
        self.assertEqual(self._f(0.65, True), "partial")

    def test_partial_at_threshold(self):
        self.assertEqual(self._f(0.60, False), "partial")

    def test_damaged(self):
        self.assertEqual(self._f(0.50, False), "damaged")

    def test_damaged_low_confidence_with_boot(self):
        self.assertEqual(self._f(0.40, True), "damaged")


class TestEstimatePartitionSize(unittest.TestCase):
    """Tests for recovery._estimate_partition_size."""

    def _f(self, sector_data, file_system, region_size):
        from src.core.recovery import _estimate_partition_size
        return _estimate_partition_size(sector_data, file_system, region_size)

    def test_ntfs_valid_metadata(self):
        sector = bytearray(512)
        # bytes-per-sector at offset 11 (2 bytes LE) = 512
        sector[11] = 0x00
        sector[12] = 0x02  # 512
        # total-sectors at offset 40 (8 bytes LE) = 1000
        sector[40] = 0xE8
        sector[41] = 0x03
        sector[42:48] = b"\x00" * 6
        result = self._f(bytes(sector), "NTFS", 999999)
        self.assertEqual(result, 512 * 1000)

    def test_fat32_valid_metadata(self):
        sector = bytearray(512)
        # bytes-per-sector at offset 11 = 512
        sector[11] = 0x00
        sector[12] = 0x02
        # total-sectors-32 at offset 32 (4 bytes LE) = 2000
        sector[32] = 0xD0
        sector[33] = 0x07
        sector[34:36] = b"\x00" * 2
        result = self._f(bytes(sector), "FAT32", 999999)
        self.assertEqual(result, 512 * 2000)

    def test_fallback_to_region_size(self):
        sector = bytearray(512)
        result = self._f(bytes(sector), "EFI", 5000000)
        self.assertEqual(result, 5000000)

    def test_ntfs_too_short_fallback(self):
        sector = bytearray(50)
        result = self._f(bytes(sector), "NTFS", 1234)
        self.assertEqual(result, 1234)


class TestRecoveredPartitionDataclass(unittest.TestCase):
    """Tests for recovery.RecoveredPartition dataclass."""

    def test_construction(self):
        from src.core.recovery import RecoveredPartition
        rp = RecoveredPartition(
            start_sector=2048,
            end_sector=10240,
            size_bytes=4194304,
            file_system="NTFS",
            status="recoverable",
            confidence=0.95,
            boot_signature=True,
        )
        self.assertEqual(rp.start_sector, 2048)
        self.assertEqual(rp.file_system, "NTFS")
        self.assertTrue(rp.boot_signature)


class TestPartitionRecoveryCancellation(unittest.TestCase):
    """Tests for PartitionRecovery cancel/check logic."""

    def test_cancel_sets_event(self):
        from src.core.recovery import PartitionRecovery, RecoveryError
        pr = PartitionRecovery()
        pr.cancel()
        with self.assertRaises(RecoveryError):
            pr._check_cancelled()

    def test_not_cancelled_does_not_raise(self):
        from src.core.recovery import PartitionRecovery
        pr = PartitionRecovery()
        # Should not raise
        pr._check_cancelled()


# ======================================================================
# 3. i18n.py
# ======================================================================


class TestI18nThreadSafety(unittest.TestCase):
    """Test thread safety of language switching."""

    def test_concurrent_set_get_language(self):
        from src.utils.i18n import set_language, get_language, LANGUAGES
        errors = []
        valid_langs = list(LANGUAGES.keys())

        def worker(lang):
            try:
                set_language(lang)
                result = get_language()
                # The result must be one of the valid languages
                if result not in valid_langs:
                    errors.append(f"Invalid language: {result}")
            except Exception as e:
                errors.append(str(e))

        threads = []
        for _ in range(20):
            for lang in valid_langs:
                t = threading.Thread(target=worker, args=(lang,))
                threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # Restore
        set_language("en")


class TestI18nSetGetRoundTrip(unittest.TestCase):
    """Test set_language / get_language round-trip."""

    def setUp(self):
        from src.utils.i18n import set_language
        set_language("en")

    def tearDown(self):
        from src.utils.i18n import set_language
        set_language("en")

    def test_roundtrip_french(self):
        from src.utils.i18n import set_language, get_language
        set_language("fr")
        self.assertEqual(get_language(), "fr")

    def test_roundtrip_german(self):
        from src.utils.i18n import set_language, get_language
        set_language("de")
        self.assertEqual(get_language(), "de")

    def test_invalid_language_ignored(self):
        from src.utils.i18n import set_language, get_language
        set_language("en")
        set_language("xx")  # not in LANGUAGES
        self.assertEqual(get_language(), "en")


class TestI18nGetLanguages(unittest.TestCase):
    """Test get_languages."""

    def test_returns_dict(self):
        from src.utils.i18n import get_languages
        langs = get_languages()
        self.assertIsInstance(langs, dict)

    def test_contains_expected_keys(self):
        from src.utils.i18n import get_languages
        langs = get_languages()
        for code in ("en", "fr", "es", "de", "ar", "zh"):
            self.assertIn(code, langs)

    def test_returns_copy(self):
        from src.utils.i18n import get_languages, LANGUAGES
        langs = get_languages()
        langs["test"] = "TestLang"
        self.assertNotIn("test", LANGUAGES)


class TestI18nTranslationFallback(unittest.TestCase):
    """Test t() fallback logic across languages."""

    def setUp(self):
        from src.utils.i18n import set_language
        set_language("en")

    def tearDown(self):
        from src.utils.i18n import set_language
        set_language("en")

    def test_french_key_present(self):
        from src.utils.i18n import t, set_language
        set_language("fr")
        result = t("sidebar.dashboard")
        self.assertEqual(result, "Tableau de bord")

    def test_french_missing_key_falls_back_to_english(self):
        from src.utils.i18n import t, set_language, _T
        set_language("fr")
        # Find a key in English not in French
        en_keys = set(_T["en"].keys())
        fr_keys = set(_T["fr"].keys())
        missing = en_keys - fr_keys
        if missing:
            key = sorted(missing)[0]
            result = t(key)
            self.assertEqual(result, _T["en"][key])

    def test_format_missing_kwarg_returns_unformatted(self):
        from src.utils.i18n import t
        # status.pending_n has {n} placeholder
        result = t("status.pending_n")
        # Should return string with literal {n} since no kwargs
        self.assertIn("{n}", result)

    def test_format_extra_kwargs_ignored(self):
        from src.utils.i18n import t
        result = t("status.ready", unused="value")
        self.assertEqual(result, "Ready")


class TestI18nValidateTranslations(unittest.TestCase):
    """Test validate_translations returns missing keys."""

    def test_returns_dict(self):
        from src.utils.i18n import validate_translations
        result = validate_translations()
        self.assertIsInstance(result, dict)

    def test_english_not_in_missing(self):
        from src.utils.i18n import validate_translations
        result = validate_translations(reference_lang="en")
        self.assertNotIn("en", result)


# ======================================================================
# 4. operations.py
# ======================================================================


class TestPendingOperationDataclass(unittest.TestCase):
    """Tests for PendingOperation dataclass construction."""

    def test_basic_construction(self):
        from src.core.operations import PendingOperation
        op = PendingOperation(
            op_type="resize",
            description="Resize partition 1 on disk 0",
            disk_index=0,
            params={"partition_index": 1, "new_size_bytes": 1024},
            risk_level="high",
            reversible=True,
        )
        self.assertEqual(op.op_type, "resize")
        self.assertEqual(op.disk_index, 0)
        self.assertTrue(op.reversible)

    def test_critical_operation(self):
        from src.core.operations import PendingOperation
        op = PendingOperation(
            op_type="delete",
            description="Delete partition",
            disk_index=1,
            params={},
            risk_level="critical",
            reversible=False,
        )
        self.assertEqual(op.risk_level, "critical")
        self.assertFalse(op.reversible)


class TestOperationManagerValidation(unittest.TestCase):
    """Tests for OperationManager static validators."""

    def test_validate_disk_index_valid(self):
        from src.core.operations import OperationManager
        # Should not raise
        OperationManager._validate_disk_index(0)
        OperationManager._validate_disk_index(5)

    def test_validate_disk_index_negative(self):
        from src.core.operations import OperationManager
        with self.assertRaises(ValueError):
            OperationManager._validate_disk_index(-1)

    def test_validate_disk_index_not_int(self):
        from src.core.operations import OperationManager
        with self.assertRaises(ValueError):
            OperationManager._validate_disk_index("abc")

    def test_validate_partition_index_valid(self):
        from src.core.operations import OperationManager
        OperationManager._validate_partition_index(1)
        OperationManager._validate_partition_index(10)

    def test_validate_partition_index_zero(self):
        from src.core.operations import OperationManager
        with self.assertRaises(ValueError):
            OperationManager._validate_partition_index(0)

    def test_validate_file_system_valid(self):
        from src.core.operations import OperationManager
        # The validator upper-cases input, so only values whose .upper()
        # is in _VALID_FILE_SYSTEMS work. "NTFS" and "FAT32" are already
        # fully uppercase in the set.
        for fs in ("NTFS", "FAT32", "ntfs", "fat32"):
            OperationManager._validate_file_system(fs)

    def test_validate_file_system_case_insensitive(self):
        from src.core.operations import OperationManager
        OperationManager._validate_file_system("ntfs")

    def test_validate_file_system_invalid(self):
        from src.core.operations import OperationManager
        with self.assertRaises(ValueError):
            OperationManager._validate_file_system("ext4")

    def test_validate_drive_letter_valid(self):
        from src.core.operations import OperationManager
        OperationManager._validate_drive_letter("D")
        OperationManager._validate_drive_letter("z")

    def test_validate_drive_letter_invalid(self):
        from src.core.operations import OperationManager
        with self.assertRaises(ValueError):
            OperationManager._validate_drive_letter("1")
        with self.assertRaises(ValueError):
            OperationManager._validate_drive_letter("AB")

    def test_validate_size_bytes_valid(self):
        from src.core.operations import OperationManager
        OperationManager._validate_size_bytes(1024)

    def test_validate_size_bytes_zero(self):
        from src.core.operations import OperationManager
        with self.assertRaises(ValueError):
            OperationManager._validate_size_bytes(0)

    def test_validate_size_bytes_negative(self):
        from src.core.operations import OperationManager
        with self.assertRaises(ValueError):
            OperationManager._validate_size_bytes(-100)


class TestOperationManagerQueue(unittest.TestCase):
    """Tests for OperationManager queue lifecycle."""

    def setUp(self):
        from src.core.operations import OperationManager
        self.mgr = OperationManager()

    def test_initially_empty(self):
        self.assertEqual(len(self.mgr.get_pending()), 0)

    def test_queue_resize(self):
        self.mgr.queue_resize_partition(0, 1, 1024 * 1024 * 100)
        pending = self.mgr.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].op_type, "resize")

    def test_queue_create(self):
        self.mgr.queue_create_partition(0, 1024 * 1024 * 500, "NTFS", "Data", "E")
        pending = self.mgr.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].op_type, "create")
        self.assertIn("E", pending[0].description)

    def test_queue_delete(self):
        self.mgr.queue_delete_partition(0, 2)
        pending = self.mgr.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].op_type, "delete")
        self.assertEqual(pending[0].risk_level, "critical")

    def test_queue_format(self):
        self.mgr.queue_format_partition(1, 1, "FAT32", "USB", quick=True)
        pending = self.mgr.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].params["file_system"], "FAT32")

    def test_queue_merge_same_partition_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.queue_merge_partitions(0, 1, 1)

    def test_queue_merge_valid(self):
        self.mgr.queue_merge_partitions(0, 1, 2)
        pending = self.mgr.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].op_type, "merge")

    def test_queue_convert_mbr_gpt(self):
        self.mgr.queue_convert_mbr_to_gpt(0)
        self.assertEqual(self.mgr.get_pending()[0].op_type, "convert_mbr_gpt")

    def test_queue_convert_gpt_mbr(self):
        self.mgr.queue_convert_gpt_to_mbr(1)
        self.assertEqual(self.mgr.get_pending()[0].op_type, "convert_gpt_mbr")

    def test_queue_set_active(self):
        self.mgr.queue_set_active(0, 1)
        self.assertEqual(self.mgr.get_pending()[0].op_type, "set_active")

    def test_queue_change_letter(self):
        self.mgr.queue_change_letter(0, 1, "F")
        pending = self.mgr.get_pending()
        self.assertEqual(pending[0].params["new_letter"], "F")

    def test_queue_4k_align(self):
        self.mgr.queue_4k_align(0, 1)
        self.assertEqual(self.mgr.get_pending()[0].op_type, "4k_align")

    def test_remove_operation(self):
        self.mgr.queue_resize_partition(0, 1, 1024)
        self.mgr.queue_delete_partition(0, 2)
        self.mgr.remove_operation(0)
        pending = self.mgr.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].op_type, "delete")

    def test_remove_out_of_range(self):
        with self.assertRaises(IndexError):
            self.mgr.remove_operation(0)

    def test_clear_pending(self):
        self.mgr.queue_resize_partition(0, 1, 1024)
        self.mgr.queue_delete_partition(0, 2)
        self.mgr.clear_pending()
        self.assertEqual(len(self.mgr.get_pending()), 0)

    def test_get_pending_returns_copy(self):
        self.mgr.queue_resize_partition(0, 1, 1024)
        pending = self.mgr.get_pending()
        pending.clear()
        self.assertEqual(len(self.mgr.get_pending()), 1)

    def test_get_history_initially_empty(self):
        self.assertEqual(len(self.mgr.get_history()), 0)


class TestOperationManagerApply(unittest.TestCase):
    """Tests for OperationManager.apply_all."""

    def setUp(self):
        from src.core.operations import OperationManager
        self.mgr = OperationManager()

    def test_apply_empty_returns_empty(self):
        results = self.mgr.apply_all()
        self.assertEqual(results, [])

    def test_apply_unknown_op_type_fails(self):
        from src.core.operations import PendingOperation
        op = PendingOperation(
            op_type="nonexistent",
            description="Unknown op",
            disk_index=0,
            params={},
            risk_level="low",
            reversible=False,
        )
        self.mgr.add_operation(op)
        results = self.mgr.apply_all()
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        self.assertIn("Unknown operation type", results[0]["message"])

    def test_progress_callback_called(self):
        from src.core.operations import PendingOperation
        messages = []
        self.mgr.set_progress_callback(lambda msg, pct: messages.append(msg))
        op = PendingOperation(
            op_type="nonexistent",
            description="Test op",
            disk_index=0,
            params={},
            risk_level="low",
            reversible=False,
        )
        self.mgr.add_operation(op)
        self.mgr.apply_all()
        self.assertTrue(len(messages) > 0)


class TestOperationManagerEnsureInt(unittest.TestCase):
    """Tests for OperationManager._ensure_int."""

    def test_valid_int(self):
        from src.core.operations import OperationManager
        self.assertEqual(OperationManager._ensure_int(5, "test"), 5)

    def test_string_int(self):
        from src.core.operations import OperationManager
        self.assertEqual(OperationManager._ensure_int("3", "test"), 3)

    def test_string_with_spaces_raises(self):
        from src.core.operations import OperationManager
        with self.assertRaises(ValueError):
            OperationManager._ensure_int(" 3 ; drop table", "test")


class TestSanitizeLabel(unittest.TestCase):
    """Extra tests for _sanitize_label edge cases."""

    def test_unicode_rejected(self):
        from src.core.operations import _sanitize_label
        with self.assertRaises(ValueError):
            _sanitize_label("diské")  # accented character

    def test_newline_rejected(self):
        from src.core.operations import _sanitize_label
        with self.assertRaises(ValueError):
            _sanitize_label("label\ninjection")


# ======================================================================
# 5. disk_info.py
# ======================================================================


class TestDiskInfoSafeInt(unittest.TestCase):
    """Tests for disk_info._safe_int."""

    def _f(self, value, default=0):
        from src.core.disk_info import _safe_int
        return _safe_int(value, default)

    def test_none_returns_default(self):
        self.assertEqual(self._f(None), 0)

    def test_valid_int(self):
        self.assertEqual(self._f(42), 42)

    def test_string_int(self):
        self.assertEqual(self._f("100"), 100)

    def test_invalid_returns_default(self):
        self.assertEqual(self._f("abc", 99), 99)


class TestDiskInfoSafeStr(unittest.TestCase):
    """Tests for disk_info._safe_str."""

    def _f(self, value, default=""):
        from src.core.disk_info import _safe_str
        return _safe_str(value, default)

    def test_none_returns_default(self):
        self.assertEqual(self._f(None), "")

    def test_strips_whitespace(self):
        self.assertEqual(self._f("  hello  "), "hello")

    def test_int_to_str(self):
        self.assertEqual(self._f(42), "42")

    def test_custom_default(self):
        self.assertEqual(self._f(None, "N/A"), "N/A")


class TestDiskInfoSafeBool(unittest.TestCase):
    """Tests for disk_info._safe_bool."""

    def _f(self, value, default=False):
        from src.core.disk_info import _safe_bool
        return _safe_bool(value, default)

    def test_none_returns_default(self):
        self.assertFalse(self._f(None))

    def test_true(self):
        self.assertTrue(self._f(True))

    def test_truthy_int(self):
        self.assertTrue(self._f(1))

    def test_falsy_zero(self):
        self.assertFalse(self._f(0))


class TestNormalizeMediaType(unittest.TestCase):
    """Tests for disk_info._normalize_media_type."""

    def _f(self, raw):
        from src.core.disk_info import _normalize_media_type
        return _normalize_media_type(raw)

    def test_ssd(self):
        self.assertEqual(self._f("SSD"), "SSD")

    def test_solid_state(self):
        self.assertEqual(self._f("Solid State"), "SSD")

    def test_hdd(self):
        self.assertEqual(self._f("HDD"), "HDD")

    def test_hard_disk(self):
        self.assertEqual(self._f("Hard Disk Drive"), "HDD")

    def test_spinning(self):
        self.assertEqual(self._f("Spinning"), "HDD")

    def test_unspecified(self):
        self.assertEqual(self._f("Unspecified"), "Unknown")

    def test_empty(self):
        self.assertEqual(self._f(""), "Unknown")

    def test_other(self):
        self.assertEqual(self._f("SCM"), "SCM")


class TestNormalizeHealth(unittest.TestCase):
    """Tests for disk_info._normalize_health."""

    def _f(self, raw):
        from src.core.disk_info import _normalize_health
        return _normalize_health(raw)

    def test_healthy(self):
        self.assertEqual(self._f("Healthy"), "Healthy")

    def test_zero_string(self):
        self.assertEqual(self._f("0"), "Healthy")

    def test_warning(self):
        self.assertEqual(self._f("Warning"), "Warning")

    def test_empty(self):
        self.assertEqual(self._f(""), "Unknown")


class TestCheck4kFromOffsets(unittest.TestCase):
    """Tests for disk_info._check_4k_from_offsets."""

    def _f(self, offsets):
        from src.core.disk_info import _check_4k_from_offsets
        return _check_4k_from_offsets(offsets)

    def test_all_aligned(self):
        self.assertTrue(self._f([0, 4096, 8192, 1048576]))

    def test_misaligned(self):
        self.assertFalse(self._f([0, 4096, 5000]))

    def test_empty_list(self):
        self.assertTrue(self._f([]))


class TestDiskInfoDataclasses(unittest.TestCase):
    """Tests for DiskInfo and PartitionInfo construction."""

    def test_partition_info_defaults(self):
        from src.core.disk_info import PartitionInfo
        p = PartitionInfo()
        self.assertEqual(p.index, 0)
        self.assertEqual(p.letter, "")
        self.assertEqual(p.file_system, "")
        self.assertEqual(p.size_bytes, 0)
        self.assertFalse(p.is_active)
        self.assertFalse(p.is_bitlocker)

    def test_disk_info_defaults(self):
        from src.core.disk_info import DiskInfo
        d = DiskInfo()
        self.assertEqual(d.index, 0)
        self.assertEqual(d.model, "")
        self.assertEqual(d.media_type, "Unknown")
        self.assertEqual(d.partitions, [])
        self.assertTrue(d.is_4k_aligned)
        self.assertIsNone(d.temperature_celsius)

    def test_disk_info_with_partitions(self):
        from src.core.disk_info import DiskInfo, PartitionInfo
        p1 = PartitionInfo(index=1, letter="C", size_bytes=100_000_000)
        p2 = PartitionInfo(index=2, letter="D", size_bytes=200_000_000)
        d = DiskInfo(index=0, model="Samsung 980 Pro", partitions=[p1, p2])
        self.assertEqual(len(d.partitions), 2)
        self.assertEqual(d.partitions[0].letter, "C")


class TestDiskInfoCacheBehavior(unittest.TestCase):
    """Tests for the caching layer in disk_info."""

    def test_cache_invalidation(self):
        import src.core.disk_info as di
        # Manually set the cache to a known value
        with di._cache_lock:
            di._disk_cache = []
            di._cache_timestamp = time.monotonic()
        # get_all_disks should return cached empty list
        with patch.object(di, '_gather_disks') as mock_gather:
            mock_gather.return_value = [di.DiskInfo(index=99)]
            result = di.get_all_disks()
            # Cache was fresh, so _gather_disks should NOT be called
            mock_gather.assert_not_called()
            self.assertEqual(result, [])

    def test_cache_expired_triggers_refresh(self):
        import src.core.disk_info as di
        # Set cache to an expired timestamp
        with di._cache_lock:
            di._disk_cache = [di.DiskInfo(index=0)]
            di._cache_timestamp = time.monotonic() - 100  # long expired
        with patch.object(di, '_gather_disks') as mock_gather:
            fresh_disks = [di.DiskInfo(index=1, model="Fresh")]
            mock_gather.return_value = fresh_disks
            result = di.get_all_disks()
            mock_gather.assert_called_once()
            self.assertEqual(result[0].model, "Fresh")

    def test_refresh_disk_info_clears_cache(self):
        import src.core.disk_info as di
        with di._cache_lock:
            di._disk_cache = [di.DiskInfo(index=0)]
            di._cache_timestamp = time.monotonic()
        with patch.object(di, '_gather_disks') as mock_gather:
            mock_gather.return_value = [di.DiskInfo(index=2)]
            result = di.refresh_disk_info()
            mock_gather.assert_called_once()
            self.assertEqual(result[0].index, 2)


# ======================================================================
# 6. admin.py
# ======================================================================


class TestIsAdmin(unittest.TestCase):
    """Tests for admin.is_admin with mocked ctypes."""

    @patch("src.utils.admin.ctypes")
    def test_admin_returns_true(self, mock_ctypes):
        from src.utils.admin import is_admin
        mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 1
        self.assertTrue(is_admin())

    @patch("src.utils.admin.ctypes")
    def test_non_admin_returns_false(self, mock_ctypes):
        from src.utils.admin import is_admin
        mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 0
        self.assertFalse(is_admin())

    @patch("src.utils.admin.ctypes")
    def test_attribute_error_returns_false(self, mock_ctypes):
        from src.utils.admin import is_admin
        mock_ctypes.windll.shell32.IsUserAnAdmin.side_effect = AttributeError
        self.assertFalse(is_admin())

    @patch("src.utils.admin.ctypes")
    def test_os_error_returns_false(self, mock_ctypes):
        from src.utils.admin import is_admin
        mock_ctypes.windll.shell32.IsUserAnAdmin.side_effect = OSError
        self.assertFalse(is_admin())


class TestRequireAdminDecorator(unittest.TestCase):
    """Tests for admin.require_admin decorator."""

    @patch("src.utils.admin.is_admin", return_value=True)
    def test_runs_when_admin(self, mock_is_admin):
        from src.utils.admin import require_admin

        @require_admin
        def my_func(x, y):
            return x + y

        result = my_func(3, 4)
        self.assertEqual(result, 7)

    @patch("src.utils.admin.is_admin", return_value=True)
    def test_preserves_function_name(self, mock_is_admin):
        from src.utils.admin import require_admin

        @require_admin
        def important_function():
            """Docstring."""
            pass

        self.assertEqual(important_function.__name__, "important_function")
        self.assertEqual(important_function.__doc__, "Docstring.")

    @patch("src.utils.admin.run_as_admin")
    @patch("src.utils.admin.is_admin", return_value=False)
    def test_calls_run_as_admin_when_not_admin(self, mock_is_admin, mock_run_as_admin):
        from src.utils.admin import require_admin

        mock_run_as_admin.side_effect = SystemExit(0)

        @require_admin
        def my_func():
            return "should not reach"

        with self.assertRaises(SystemExit):
            my_func()

        mock_run_as_admin.assert_called_once()


# ======================================================================
# Entry point
# ======================================================================


if __name__ == "__main__":
    unittest.main()
