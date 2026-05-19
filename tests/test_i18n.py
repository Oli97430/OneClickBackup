"""Unit tests for src.utils.i18n."""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.i18n import (
    t,
    set_language,
    get_language,
    get_languages,
    validate_translations,
    LANGUAGES,
    _T,
)


class TestTranslationLookup(unittest.TestCase):
    """Tests for t() translation function."""

    def setUp(self):
        """Ensure English is active before each test."""
        set_language("en")

    def test_known_key_returns_english(self):
        result = t("sidebar.dashboard")
        self.assertEqual(result, "Dashboard")

    def test_app_title_english(self):
        result = t("app.title")
        self.assertEqual(result, "OneClick Backup & Disk Manager")

    def test_unknown_key_returns_key_itself(self):
        result = t("this.key.does.not.exist")
        self.assertEqual(result, "this.key.does.not.exist")

    def test_empty_key_returns_empty(self):
        result = t("")
        self.assertEqual(result, "")


class TestPlaceholderSubstitution(unittest.TestCase):
    """Tests for t() placeholder substitution."""

    def setUp(self):
        set_language("en")

    def test_single_placeholder(self):
        result = t("status.pending_n", n=5)
        self.assertEqual(result, "5 pending")

    def test_multiple_placeholders(self):
        result = t("clone.confirm_msg", src="Disk 0", tgt="Disk 1")
        self.assertIn("Disk 0", result)
        self.assertIn("Disk 1", result)

    def test_missing_placeholder_no_error(self):
        # t() should not raise even if kwargs don't match
        result = t("status.pending_n")  # no n= provided
        # The raw template string should be returned with {n} intact
        self.assertIn("{n}", result)

    def test_extra_kwargs_ignored(self):
        result = t("sidebar.dashboard", extra="unused")
        self.assertEqual(result, "Dashboard")


class TestSetLanguage(unittest.TestCase):
    """Tests for set_language() and get_language()."""

    def tearDown(self):
        """Reset to English after each test."""
        set_language("en")

    def test_set_and_get_french(self):
        set_language("fr")
        self.assertEqual(get_language(), "fr")

    def test_set_and_get_spanish(self):
        set_language("es")
        self.assertEqual(get_language(), "es")

    def test_round_trip(self):
        for lang_code in LANGUAGES:
            set_language(lang_code)
            self.assertEqual(get_language(), lang_code)

    def test_invalid_language_ignored(self):
        set_language("en")
        set_language("xx_INVALID")
        # Should still be English since "xx_INVALID" is not in LANGUAGES
        self.assertEqual(get_language(), "en")

    def test_french_translation(self):
        set_language("fr")
        result = t("sidebar.dashboard")
        self.assertEqual(result, "Tableau de bord")

    def test_fallback_to_english_for_missing_key(self):
        """Non-English languages should fall back to English for missing keys."""
        set_language("es")
        # Spanish has fewer keys than English; pick one that exists in en but not es
        en_keys = set(_T["en"].keys())
        es_keys = set(_T["es"].keys())
        missing_in_es = en_keys - es_keys
        if missing_in_es:
            key = sorted(missing_in_es)[0]
            result = t(key)
            # Should get the English value, not the key itself
            self.assertEqual(result, _T["en"][key])


class TestGetLanguages(unittest.TestCase):
    """Tests for get_languages()."""

    def test_returns_all_six_languages(self):
        langs = get_languages()
        self.assertEqual(len(langs), 6)

    def test_contains_expected_codes(self):
        langs = get_languages()
        expected = {"en", "fr", "es", "de", "ar", "zh"}
        self.assertEqual(set(langs.keys()), expected)

    def test_english_display_name(self):
        langs = get_languages()
        self.assertEqual(langs["en"], "English")

    def test_french_display_name(self):
        langs = get_languages()
        self.assertEqual(langs["fr"], "Français")

    def test_returns_new_dict(self):
        """get_languages() should return a copy, not the original."""
        langs1 = get_languages()
        langs2 = get_languages()
        self.assertEqual(langs1, langs2)
        self.assertIsNot(langs1, langs2)


class TestValidateTranslations(unittest.TestCase):
    """Tests for validate_translations()."""

    def test_returns_dict(self):
        result = validate_translations()
        self.assertIsInstance(result, dict)

    def test_english_not_in_result(self):
        """English is the reference; it should not appear as missing."""
        result = validate_translations(reference_lang="en")
        self.assertNotIn("en", result)

    def test_incomplete_languages_have_missing_keys(self):
        """At least some non-English languages should have missing keys."""
        result = validate_translations()
        # Spanish, German, Arabic, Chinese are intentionally subset translations
        has_missing = any(len(v) > 0 for v in result.values())
        self.assertTrue(has_missing, "Expected at least one language with missing keys")

    def test_missing_keys_are_strings(self):
        result = validate_translations()
        for lang_code, keys in result.items():
            self.assertIsInstance(keys, list)
            for key in keys:
                self.assertIsInstance(key, str)

    def test_french_completeness(self):
        """French should be relatively complete (same key count as English)."""
        en_count = len(_T["en"])
        fr_count = len(_T["fr"])
        # French is the most complete translation, it should match English
        self.assertEqual(fr_count, en_count)


if __name__ == "__main__":
    unittest.main()
