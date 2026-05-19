"""Unit tests for src.utils.helpers."""

import sys
import os
import re
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.helpers import (
    format_bytes,
    parse_size_to_bytes,
    safe_int,
    get_drive_letter_from_path,
    generate_timestamp,
)


class TestFormatBytes(unittest.TestCase):
    """Tests for format_bytes()."""

    def test_zero_bytes(self):
        self.assertEqual(format_bytes(0), "0.00 B")

    def test_small_bytes(self):
        self.assertEqual(format_bytes(512), "512.00 B")

    def test_one_kilobyte(self):
        self.assertEqual(format_bytes(1024), "1.00 KB")

    def test_one_megabyte(self):
        self.assertEqual(format_bytes(1024 ** 2), "1.00 MB")

    def test_one_gigabyte(self):
        self.assertEqual(format_bytes(1024 ** 3), "1.00 GB")

    def test_one_terabyte(self):
        self.assertEqual(format_bytes(1024 ** 4), "1.00 TB")

    def test_one_petabyte(self):
        self.assertEqual(format_bytes(1024 ** 5), "1.00 PB")

    def test_exabyte_range(self):
        result = format_bytes(1024 ** 6)
        self.assertIn("EB", result)

    def test_fractional_gigabyte(self):
        # 1.5 GB
        self.assertEqual(format_bytes(int(1.5 * 1024 ** 3)), "1.50 GB")

    def test_negative_returns_zero(self):
        self.assertEqual(format_bytes(-1), "0 B")

    def test_negative_large_returns_zero(self):
        self.assertEqual(format_bytes(-999999), "0 B")


class TestParseSizeToBytes(unittest.TestCase):
    """Tests for parse_size_to_bytes()."""

    def test_simple_gb(self):
        self.assertEqual(parse_size_to_bytes("1 GB"), 1024 ** 3)

    def test_simple_mb(self):
        self.assertEqual(parse_size_to_bytes("256 MB"), 256 * 1024 ** 2)

    def test_no_space(self):
        self.assertEqual(parse_size_to_bytes("500GB"), 500 * 1024 ** 3)

    def test_case_insensitive(self):
        self.assertEqual(parse_size_to_bytes("1 gb"), 1024 ** 3)

    def test_bytes_unit(self):
        self.assertEqual(parse_size_to_bytes("100 B"), 100)

    def test_invalid_string_returns_zero(self):
        self.assertEqual(parse_size_to_bytes("not a size"), 0)

    def test_empty_string_returns_zero(self):
        self.assertEqual(parse_size_to_bytes(""), 0)

    def test_round_trip_with_format_bytes(self):
        """parse_size_to_bytes(format_bytes(x)) should approximate x."""
        original = 1024 ** 3  # 1 GB exactly
        formatted = format_bytes(original)
        recovered = parse_size_to_bytes(formatted)
        self.assertEqual(recovered, original)

    def test_round_trip_megabyte(self):
        original = 256 * 1024 ** 2
        formatted = format_bytes(original)
        recovered = parse_size_to_bytes(formatted)
        self.assertEqual(recovered, original)


class TestSafeInt(unittest.TestCase):
    """Tests for safe_int()."""

    def test_valid_int(self):
        self.assertEqual(safe_int(42), 42)

    def test_valid_string_int(self):
        self.assertEqual(safe_int("123"), 123)

    def test_float_truncates(self):
        self.assertEqual(safe_int(3.9), 3)

    def test_none_returns_default(self):
        self.assertEqual(safe_int(None), 0)

    def test_none_returns_custom_default(self):
        self.assertEqual(safe_int(None, -1), -1)

    def test_garbage_string_returns_default(self):
        self.assertEqual(safe_int("abc"), 0)

    def test_empty_string_returns_default(self):
        self.assertEqual(safe_int(""), 0)

    def test_negative_int(self):
        self.assertEqual(safe_int(-10), -10)

    def test_negative_string(self):
        self.assertEqual(safe_int("-5"), -5)

    def test_zero(self):
        self.assertEqual(safe_int(0), 0)


class TestGetDriveLetterFromPath(unittest.TestCase):
    """Tests for get_drive_letter_from_path()."""

    def test_backslash_path(self):
        self.assertEqual(get_drive_letter_from_path("C:\\Users"), "C")

    def test_forward_slash_path(self):
        self.assertEqual(get_drive_letter_from_path("D:/data"), "D")

    def test_lowercase_drive(self):
        result = get_drive_letter_from_path("c:\\folder")
        self.assertEqual(result, "C")

    def test_empty_string_returns_none(self):
        self.assertIsNone(get_drive_letter_from_path(""))

    def test_none_returns_none(self):
        self.assertIsNone(get_drive_letter_from_path(None))

    def test_no_drive_letter(self):
        result = get_drive_letter_from_path("relative/path")
        self.assertIsNone(result)

    def test_full_path(self):
        self.assertEqual(
            get_drive_letter_from_path("E:\\Backups\\2024\\data.zip"),
            "E",
        )


class TestGenerateTimestamp(unittest.TestCase):
    """Tests for generate_timestamp()."""

    def test_format_matches_pattern(self):
        ts = generate_timestamp()
        # Expected format: YYYYMMDD_HHMMSS
        pattern = r"^\d{8}_\d{6}$"
        self.assertRegex(ts, pattern)

    def test_length(self):
        ts = generate_timestamp()
        self.assertEqual(len(ts), 15)  # 8 + 1 + 6

    def test_starts_with_valid_year(self):
        ts = generate_timestamp()
        year = int(ts[:4])
        self.assertGreaterEqual(year, 2024)
        self.assertLessEqual(year, 2100)


if __name__ == "__main__":
    unittest.main()
