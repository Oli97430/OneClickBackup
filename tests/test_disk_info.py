"""Unit tests for src.core.disk_info data structures and helpers."""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.disk_info import (
    PartitionInfo,
    DiskInfo,
    _safe_int,
    _safe_str,
    _safe_bool,
    _normalize_media_type,
    _normalize_health,
    _check_4k_from_offsets,
)


class TestPartitionInfoDefaults(unittest.TestCase):
    """Tests for PartitionInfo dataclass defaults."""

    def test_default_index(self):
        p = PartitionInfo()
        self.assertEqual(p.index, 0)

    def test_default_letter_empty(self):
        p = PartitionInfo()
        self.assertEqual(p.letter, "")

    def test_default_label_empty(self):
        p = PartitionInfo()
        self.assertEqual(p.label, "")

    def test_default_file_system_empty(self):
        p = PartitionInfo()
        self.assertEqual(p.file_system, "")

    def test_default_sizes_zero(self):
        p = PartitionInfo()
        self.assertEqual(p.size_bytes, 0)
        self.assertEqual(p.used_bytes, 0)
        self.assertEqual(p.free_bytes, 0)

    def test_default_booleans_false(self):
        p = PartitionInfo()
        self.assertFalse(p.is_active)
        self.assertFalse(p.is_boot)
        self.assertFalse(p.is_system)

    def test_custom_values(self):
        p = PartitionInfo(
            index=1, letter="C", label="System",
            file_system="NTFS", size_bytes=100_000_000,
            is_boot=True,
        )
        self.assertEqual(p.index, 1)
        self.assertEqual(p.letter, "C")
        self.assertEqual(p.label, "System")
        self.assertEqual(p.file_system, "NTFS")
        self.assertEqual(p.size_bytes, 100_000_000)
        self.assertTrue(p.is_boot)


class TestDiskInfoDefaults(unittest.TestCase):
    """Tests for DiskInfo dataclass defaults."""

    def test_default_index(self):
        d = DiskInfo()
        self.assertEqual(d.index, 0)

    def test_default_model_empty(self):
        d = DiskInfo()
        self.assertEqual(d.model, "")

    def test_default_media_type(self):
        d = DiskInfo()
        self.assertEqual(d.media_type, "Unknown")

    def test_default_interface_type(self):
        d = DiskInfo()
        self.assertEqual(d.interface_type, "Unknown")

    def test_default_partition_style(self):
        d = DiskInfo()
        self.assertEqual(d.partition_style, "Unknown")

    def test_default_health_status(self):
        d = DiskInfo()
        self.assertEqual(d.health_status, "Unknown")

    def test_default_partitions_empty_list(self):
        d = DiskInfo()
        self.assertEqual(d.partitions, [])

    def test_default_system_disk_false(self):
        d = DiskInfo()
        self.assertFalse(d.is_system_disk)

    def test_default_4k_aligned_true(self):
        d = DiskInfo()
        self.assertTrue(d.is_4k_aligned)

    def test_partitions_list_independence(self):
        """Each DiskInfo instance should have its own partitions list."""
        d1 = DiskInfo()
        d2 = DiskInfo()
        d1.partitions.append(PartitionInfo(index=1))
        self.assertEqual(len(d2.partitions), 0)


class TestSafeInt(unittest.TestCase):
    """Tests for _safe_int() helper."""

    def test_valid_int(self):
        self.assertEqual(_safe_int(42), 42)

    def test_string_number(self):
        self.assertEqual(_safe_int("100"), 100)

    def test_none_returns_default(self):
        self.assertEqual(_safe_int(None), 0)

    def test_none_custom_default(self):
        self.assertEqual(_safe_int(None, -1), -1)

    def test_invalid_string_returns_default(self):
        self.assertEqual(_safe_int("not_a_number"), 0)

    def test_float_truncates(self):
        self.assertEqual(_safe_int(3.7), 3)

    def test_empty_string_returns_default(self):
        self.assertEqual(_safe_int(""), 0)


class TestSafeStr(unittest.TestCase):
    """Tests for _safe_str() helper."""

    def test_valid_string(self):
        self.assertEqual(_safe_str("hello"), "hello")

    def test_strips_whitespace(self):
        self.assertEqual(_safe_str("  padded  "), "padded")

    def test_none_returns_default(self):
        self.assertEqual(_safe_str(None), "")

    def test_none_custom_default(self):
        self.assertEqual(_safe_str(None, "fallback"), "fallback")

    def test_number_to_string(self):
        self.assertEqual(_safe_str(42), "42")

    def test_empty_string(self):
        self.assertEqual(_safe_str(""), "")


class TestSafeBool(unittest.TestCase):
    """Tests for _safe_bool() helper."""

    def test_true(self):
        self.assertTrue(_safe_bool(True))

    def test_false(self):
        self.assertFalse(_safe_bool(False))

    def test_none_returns_default(self):
        self.assertFalse(_safe_bool(None))

    def test_none_custom_default(self):
        self.assertTrue(_safe_bool(None, True))

    def test_truthy_int(self):
        self.assertTrue(_safe_bool(1))

    def test_falsy_int(self):
        self.assertFalse(_safe_bool(0))

    def test_nonempty_string_is_true(self):
        self.assertTrue(_safe_bool("yes"))


class TestNormalizeMediaType(unittest.TestCase):
    """Tests for _normalize_media_type()."""

    def test_ssd(self):
        self.assertEqual(_normalize_media_type("SSD"), "SSD")

    def test_solid_state(self):
        self.assertEqual(_normalize_media_type("Solid State Drive"), "SSD")

    def test_hdd(self):
        self.assertEqual(_normalize_media_type("HDD"), "HDD")

    def test_hard_disk(self):
        self.assertEqual(_normalize_media_type("Hard Disk Drive"), "HDD")

    def test_spinning(self):
        self.assertEqual(_normalize_media_type("Spinning"), "HDD")

    def test_unspecified_returns_unknown(self):
        self.assertEqual(_normalize_media_type("Unspecified"), "Unknown")

    def test_empty_returns_unknown(self):
        self.assertEqual(_normalize_media_type(""), "Unknown")

    def test_unknown_string_returns_unknown(self):
        self.assertEqual(_normalize_media_type("Unknown"), "Unknown")

    def test_other_type_returned_as_is(self):
        self.assertEqual(_normalize_media_type("SCM"), "SCM")


class TestNormalizeHealth(unittest.TestCase):
    """Tests for _normalize_health()."""

    def test_healthy_string(self):
        self.assertEqual(_normalize_health("Healthy"), "Healthy")

    def test_healthy_case_insensitive(self):
        self.assertEqual(_normalize_health("healthy"), "Healthy")

    def test_zero_means_healthy(self):
        self.assertEqual(_normalize_health("0"), "Healthy")

    def test_warning_returned_as_is(self):
        self.assertEqual(_normalize_health("Warning"), "Warning")

    def test_empty_returns_unknown(self):
        self.assertEqual(_normalize_health(""), "Unknown")

    def test_degraded_returned_as_is(self):
        self.assertEqual(_normalize_health("Degraded"), "Degraded")


class TestCheck4kFromOffsets(unittest.TestCase):
    """Tests for _check_4k_from_offsets()."""

    def test_all_aligned(self):
        offsets = [0, 4096, 8192, 1048576]  # all multiples of 4096
        self.assertTrue(_check_4k_from_offsets(offsets))

    def test_one_misaligned(self):
        offsets = [4096, 8192, 5000]  # 5000 is not a multiple of 4096
        self.assertFalse(_check_4k_from_offsets(offsets))

    def test_empty_list(self):
        """Empty list means all() returns True (vacuous truth)."""
        self.assertTrue(_check_4k_from_offsets([]))

    def test_single_aligned_offset(self):
        self.assertTrue(_check_4k_from_offsets([4096]))

    def test_single_misaligned_offset(self):
        self.assertFalse(_check_4k_from_offsets([512]))

    def test_large_aligned_offset(self):
        offset = 4096 * 1000000  # 4 GB offset, perfectly aligned
        self.assertTrue(_check_4k_from_offsets([offset]))

    def test_zero_offset_is_aligned(self):
        self.assertTrue(_check_4k_from_offsets([0]))


if __name__ == "__main__":
    unittest.main()
