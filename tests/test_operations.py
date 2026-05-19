"""Unit tests for src.core.operations."""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.operations import (
    _sanitize_label,
    PendingOperation,
    OperationManager,
)


class TestSanitizeLabelValid(unittest.TestCase):
    """Tests for _sanitize_label() with valid inputs."""

    def test_simple_label(self):
        self.assertEqual(_sanitize_label("MyDisk"), "MyDisk")

    def test_label_with_space(self):
        self.assertEqual(_sanitize_label("Backup 2024"), "Backup 2024")

    def test_label_with_hyphen(self):
        self.assertEqual(_sanitize_label("My-Disk"), "My-Disk")

    def test_label_with_underscore(self):
        self.assertEqual(_sanitize_label("My_Disk"), "My_Disk")

    def test_label_with_digits(self):
        self.assertEqual(_sanitize_label("Disk01"), "Disk01")

    def test_empty_label(self):
        self.assertEqual(_sanitize_label(""), "")

    def test_whitespace_only_label(self):
        self.assertEqual(_sanitize_label("   "), "")

    def test_label_stripped(self):
        self.assertEqual(_sanitize_label("  MyDisk  "), "MyDisk")

    def test_max_length_32(self):
        label = "A" * 32
        self.assertEqual(_sanitize_label(label), label)


class TestSanitizeLabelInvalid(unittest.TestCase):
    """Tests for _sanitize_label() with invalid inputs."""

    def test_semicolon_rejected(self):
        with self.assertRaises(ValueError):
            _sanitize_label("rm -rf;")

    def test_shell_injection_rejected(self):
        with self.assertRaises(ValueError):
            _sanitize_label("$(whoami)")

    def test_pipe_rejected(self):
        with self.assertRaises(ValueError):
            _sanitize_label("label|bad")

    def test_ampersand_rejected(self):
        with self.assertRaises(ValueError):
            _sanitize_label("label&command")

    def test_too_long_rejected(self):
        with self.assertRaises(ValueError):
            _sanitize_label("A" * 50)

    def test_special_chars_rejected(self):
        with self.assertRaises(ValueError):
            _sanitize_label("label<>")

    def test_quotes_rejected(self):
        with self.assertRaises(ValueError):
            _sanitize_label('label"quote')

    def test_backtick_rejected(self):
        with self.assertRaises(ValueError):
            _sanitize_label("label`cmd`")


class TestPendingOperation(unittest.TestCase):
    """Tests for PendingOperation dataclass."""

    def test_creation(self):
        op = PendingOperation(
            op_type="resize",
            description="Resize partition 1 on disk 0",
            disk_index=0,
            params={"partition_index": 1, "new_size_bytes": 1024},
            risk_level="high",
            reversible=True,
        )
        self.assertEqual(op.op_type, "resize")
        self.assertEqual(op.description, "Resize partition 1 on disk 0")
        self.assertEqual(op.disk_index, 0)
        self.assertEqual(op.params["partition_index"], 1)
        self.assertEqual(op.risk_level, "high")
        self.assertTrue(op.reversible)

    def test_critical_non_reversible(self):
        op = PendingOperation(
            op_type="delete",
            description="Delete partition 2",
            disk_index=1,
            params={"partition_index": 2},
            risk_level="critical",
            reversible=False,
        )
        self.assertEqual(op.risk_level, "critical")
        self.assertFalse(op.reversible)

    def test_params_is_dict(self):
        op = PendingOperation(
            op_type="format",
            description="Format",
            disk_index=0,
            params={"file_system": "NTFS", "quick": True},
            risk_level="critical",
            reversible=False,
        )
        self.assertIsInstance(op.params, dict)
        self.assertEqual(op.params["file_system"], "NTFS")


class TestOperationManagerQueue(unittest.TestCase):
    """Tests for OperationManager queue operations."""

    def setUp(self):
        self.mgr = OperationManager()

    def test_initial_pending_empty(self):
        self.assertEqual(len(self.mgr.get_pending()), 0)

    def test_add_operation(self):
        op = PendingOperation(
            op_type="resize",
            description="Test resize",
            disk_index=0,
            params={},
            risk_level="low",
            reversible=True,
        )
        self.mgr.add_operation(op)
        self.assertEqual(len(self.mgr.get_pending()), 1)

    def test_add_multiple_operations(self):
        for i in range(3):
            op = PendingOperation(
                op_type="resize",
                description=f"Op {i}",
                disk_index=0,
                params={},
                risk_level="low",
                reversible=True,
            )
            self.mgr.add_operation(op)
        self.assertEqual(len(self.mgr.get_pending()), 3)

    def test_remove_operation(self):
        op = PendingOperation(
            op_type="delete",
            description="Delete test",
            disk_index=0,
            params={},
            risk_level="critical",
            reversible=False,
        )
        self.mgr.add_operation(op)
        self.mgr.remove_operation(0)
        self.assertEqual(len(self.mgr.get_pending()), 0)

    def test_remove_invalid_index_raises(self):
        with self.assertRaises(IndexError):
            self.mgr.remove_operation(0)

    def test_clear_pending(self):
        for i in range(5):
            op = PendingOperation(
                op_type="format",
                description=f"Format {i}",
                disk_index=0,
                params={},
                risk_level="high",
                reversible=False,
            )
            self.mgr.add_operation(op)
        self.assertEqual(len(self.mgr.get_pending()), 5)
        self.mgr.clear_pending()
        self.assertEqual(len(self.mgr.get_pending()), 0)

    def test_get_pending_returns_copy(self):
        op = PendingOperation(
            op_type="resize",
            description="Test",
            disk_index=0,
            params={},
            risk_level="low",
            reversible=True,
        )
        self.mgr.add_operation(op)
        pending = self.mgr.get_pending()
        pending.clear()
        # Original queue should not be affected
        self.assertEqual(len(self.mgr.get_pending()), 1)

    def test_queue_resize_partition(self):
        self.mgr.queue_resize_partition(0, 1, 1024 * 1024 * 100)
        ops = self.mgr.get_pending()
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].op_type, "resize")

    def test_queue_create_partition(self):
        self.mgr.queue_create_partition(0, 1024 * 1024 * 500, "NTFS", "Data", "E")
        ops = self.mgr.get_pending()
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].op_type, "create")

    def test_queue_delete_partition(self):
        self.mgr.queue_delete_partition(0, 2)
        ops = self.mgr.get_pending()
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].op_type, "delete")
        self.assertEqual(ops[0].risk_level, "critical")

    def test_apply_all_empty_queue(self):
        results = self.mgr.apply_all()
        self.assertEqual(results, [])

    def test_get_history_initially_empty(self):
        self.assertEqual(len(self.mgr.get_history()), 0)


class TestOperationManagerValidation(unittest.TestCase):
    """Tests for OperationManager input validation."""

    def setUp(self):
        self.mgr = OperationManager()

    def test_invalid_disk_index_negative(self):
        with self.assertRaises(ValueError):
            self.mgr.queue_resize_partition(-1, 1, 1024)

    def test_invalid_partition_index_zero(self):
        with self.assertRaises(ValueError):
            self.mgr.queue_resize_partition(0, 0, 1024)

    def test_invalid_file_system(self):
        with self.assertRaises(ValueError):
            self.mgr.queue_create_partition(0, 1024 * 1024, "BTRFS")

    def test_invalid_drive_letter(self):
        with self.assertRaises(ValueError):
            self.mgr.queue_change_letter(0, 1, "1")

    def test_invalid_size_zero(self):
        with self.assertRaises(ValueError):
            self.mgr.queue_create_partition(0, 0, "NTFS")

    def test_merge_same_partition_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.queue_merge_partitions(0, 1, 1)


if __name__ == "__main__":
    unittest.main()
