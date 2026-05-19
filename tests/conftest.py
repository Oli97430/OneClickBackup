"""Shared test fixtures for OneClick Backup test suite."""

from __future__ import annotations

import os
import sys

import pytest

# Ensure the project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def sample_disk_info():
    """Return a minimal DiskInfo dataclass for testing."""
    from src.core.disk_info import DiskInfo, PartitionInfo

    part = PartitionInfo(
        index=1,
        letter="C",
        label="System",
        file_system="NTFS",
        total_size=500_000_000_000,
        used_space=250_000_000_000,
        free_space=250_000_000_000,
    )
    return DiskInfo(
        index=0,
        model="Samsung SSD 970 EVO 500GB",
        serial_number="S4EVNF0M123456",
        size=500_000_000_000,
        media_type="SSD",
        interface_type="NVMe",
        partition_style="GPT",
        health_status="Healthy",
        partitions=[part],
        is_system_disk=True,
    )


@pytest.fixture
def sample_partition_info():
    """Return a minimal PartitionInfo dataclass for testing."""
    from src.core.disk_info import PartitionInfo

    return PartitionInfo(
        index=1,
        letter="D",
        label="Data",
        file_system="NTFS",
        total_size=1_000_000_000_000,
        used_space=400_000_000_000,
        free_space=600_000_000_000,
    )


@pytest.fixture
def operation_manager():
    """Return a fresh OperationManager instance."""
    from src.core.operations import OperationManager

    return OperationManager()


@pytest.fixture
def tmp_backup_dir(tmp_path):
    """Return a temporary directory suitable for backup tests."""
    backup_dir = tmp_path / "test_backups"
    backup_dir.mkdir()
    return str(backup_dir)
