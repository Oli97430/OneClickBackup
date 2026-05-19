"""Unit tests for widget utility functions in src.ui.widgets.

Tests only the pure utility functions (_lighten, _darken, format_bytes,
get_fs_color) which do not require a running Tk event loop.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui.widgets import (
    _lighten,
    _darken,
    format_bytes,
    get_fs_color,
    COLORS,
    PARTITION_COLORS,
)


class TestLighten(unittest.TestCase):
    """Tests for _lighten() color helper."""

    def _parse_hex(self, hex_color):
        """Parse #RRGGBB into (r, g, b) tuple."""
        return (
            int(hex_color[1:3], 16),
            int(hex_color[3:5], 16),
            int(hex_color[5:7], 16),
        )

    def test_produces_lighter_color(self):
        original = "#336699"
        result = _lighten(original, 0.25)
        orig_r, orig_g, orig_b = self._parse_hex(original)
        res_r, res_g, res_b = self._parse_hex(result)
        # Each channel should be >= original
        self.assertGreaterEqual(res_r, orig_r)
        self.assertGreaterEqual(res_g, orig_g)
        self.assertGreaterEqual(res_b, orig_b)

    def test_white_stays_white(self):
        result = _lighten("#ffffff", 0.5)
        self.assertEqual(result, "#ffffff")

    def test_black_becomes_lighter(self):
        result = _lighten("#000000", 0.5)
        r, g, b = self._parse_hex(result)
        self.assertGreater(r, 0)
        self.assertGreater(g, 0)
        self.assertGreater(b, 0)

    def test_returns_valid_hex(self):
        result = _lighten("#6366f1", 0.3)
        self.assertTrue(result.startswith("#"))
        self.assertEqual(len(result), 7)

    def test_zero_factor_no_change(self):
        original = "#aabbcc"
        result = _lighten(original, 0.0)
        self.assertEqual(result, original)

    def test_full_factor_becomes_white(self):
        result = _lighten("#336699", 1.0)
        self.assertEqual(result, "#ffffff")


class TestDarken(unittest.TestCase):
    """Tests for _darken() color helper."""

    def _parse_hex(self, hex_color):
        return (
            int(hex_color[1:3], 16),
            int(hex_color[3:5], 16),
            int(hex_color[5:7], 16),
        )

    def test_produces_darker_color(self):
        original = "#336699"
        result = _darken(original, 0.30)
        orig_r, orig_g, orig_b = self._parse_hex(original)
        res_r, res_g, res_b = self._parse_hex(result)
        # Each channel should be <= original
        self.assertLessEqual(res_r, orig_r)
        self.assertLessEqual(res_g, orig_g)
        self.assertLessEqual(res_b, orig_b)

    def test_black_stays_black(self):
        result = _darken("#000000", 0.5)
        self.assertEqual(result, "#000000")

    def test_white_becomes_darker(self):
        result = _darken("#ffffff", 0.5)
        r, g, b = self._parse_hex(result)
        self.assertLess(r, 255)
        self.assertLess(g, 255)
        self.assertLess(b, 255)

    def test_returns_valid_hex(self):
        result = _darken("#6366f1", 0.3)
        self.assertTrue(result.startswith("#"))
        self.assertEqual(len(result), 7)

    def test_zero_factor_no_change(self):
        original = "#aabbcc"
        result = _darken(original, 0.0)
        self.assertEqual(result, original)

    def test_full_factor_becomes_black(self):
        result = _darken("#336699", 1.0)
        self.assertEqual(result, "#000000")


class TestFormatBytesWidget(unittest.TestCase):
    """Tests for widgets.format_bytes() (1-decimal variant)."""

    def test_zero_bytes(self):
        self.assertEqual(format_bytes(0), "0.0 B")

    def test_kilobyte(self):
        self.assertEqual(format_bytes(1024), "1.0 KB")

    def test_megabyte(self):
        self.assertEqual(format_bytes(1024 ** 2), "1.0 MB")

    def test_gigabyte(self):
        self.assertEqual(format_bytes(1024 ** 3), "1.0 GB")

    def test_terabyte(self):
        self.assertEqual(format_bytes(1024 ** 4), "1.0 TB")

    def test_petabyte_range(self):
        result = format_bytes(1024 ** 5)
        self.assertIn("PB", result)

    def test_negative_treated_as_zero(self):
        result = format_bytes(-500)
        # Negative is clamped to 0 per the implementation
        self.assertEqual(result, "0.0 B")

    def test_consistency_across_calls(self):
        """Calling with the same value should always return the same result."""
        val = 1024 ** 3 + 512 * 1024 ** 2  # 1.5 GB
        result1 = format_bytes(val)
        result2 = format_bytes(val)
        self.assertEqual(result1, result2)


class TestGetFsColor(unittest.TestCase):
    """Tests for get_fs_color()."""

    def test_ntfs_color(self):
        color = get_fs_color("NTFS")
        self.assertEqual(color, COLORS["ntfs_color"])

    def test_fat32_color(self):
        color = get_fs_color("FAT32")
        self.assertEqual(color, COLORS["fat32_color"])

    def test_exfat_color(self):
        color = get_fs_color("exFAT")
        self.assertEqual(color, COLORS["exfat_color"])

    def test_ntfs_case_insensitive(self):
        color = get_fs_color("ntfs")
        self.assertEqual(color, COLORS["ntfs_color"])

    def test_unknown_fs_returns_unknown_color(self):
        color = get_fs_color("BTRFS")
        self.assertEqual(color, COLORS["unknown_color"])

    def test_empty_string_returns_unknown_color(self):
        color = get_fs_color("")
        self.assertEqual(color, COLORS["unknown_color"])

    def test_efi_color(self):
        color = get_fs_color("EFI")
        self.assertEqual(color, COLORS["efi_color"])

    def test_recovery_color(self):
        color = get_fs_color("Recovery")
        self.assertEqual(color, COLORS["recovery_color"])

    def test_returns_valid_hex_color(self):
        """All returned colors should be valid hex strings."""
        for fs in ("NTFS", "FAT32", "exFAT", "XYZ", ""):
            color = get_fs_color(fs)
            self.assertTrue(color.startswith("#"), f"Color for {fs!r} should start with #")
            self.assertEqual(len(color), 7, f"Color for {fs!r} should be #RRGGBB")


if __name__ == "__main__":
    unittest.main()
