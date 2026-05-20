"""Command-line interface for OneClick Backup & Disk Manager.

Provides ``run_cli(args)`` which parses arguments and dispatches to the
appropriate core module.  Designed for headless / scripted use and CI
pipelines where the GUI is not desired.

Usage:
    python -m src.utils.cli --list-disks
    python -m src.utils.cli --backup partition --dest D:\\Backups --name nightly
    python -m src.utils.cli --health --disk 0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from src.utils.helpers import format_bytes

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_cli(args: list[str] | None = None) -> int:
    """Parse command-line arguments and execute the requested operation.

    Args:
        args: Argument list to parse.  Defaults to ``sys.argv[1:]``.

    Returns:
        Exit code: 0 on success, non-zero on error.
    """
    parser = _build_parser()
    ns = parser.parse_args(args)

    # No command given -- print help and exit.
    if not _has_action(ns):
        parser.print_help()
        return 0

    try:
        if ns.version:
            return _cmd_version()
        if ns.list_disks:
            return _cmd_list_disks()
        if ns.list_backups:
            return _cmd_list_backups()
        if ns.backup:
            return _cmd_backup(ns.backup, ns.dest, ns.name)
        if ns.clone:
            return _cmd_clone(ns.clone)
        if ns.verify_backup:
            return _cmd_verify_backup(ns.verify_backup)
        if ns.health:
            return _cmd_health(ns.disk)
        if ns.benchmark:
            return _cmd_benchmark(ns.benchmark)
        if ns.scheduled_backup:
            return _cmd_scheduled_backup(ns.scheduled_backup)
    except KeyboardInterrupt:
        _print_err("Interrupted.")
        return 130
    except Exception as exc:
        _print_err(f"Error: {exc}")
        _log.exception("CLI command failed")
        return 1

    parser.print_help()
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="oneclickbackup",
        description="OneClick Backup & Disk Manager -- command-line interface.",
    )

    # Backup
    parser.add_argument(
        "--backup",
        metavar="TYPE",
        choices=["full_disk", "partition", "system"],
        default=None,
        help="Create a backup of the given type (full_disk, partition, system).",
    )
    parser.add_argument(
        "--dest",
        metavar="PATH",
        default="",
        help="Destination folder for backup (used with --backup).",
    )
    parser.add_argument(
        "--name",
        metavar="NAME",
        default="",
        help="Human-readable backup name (used with --backup).",
    )

    # Clone
    parser.add_argument(
        "--clone",
        nargs=2,
        metavar=("SRC_DISK", "TGT_DISK"),
        type=int,
        default=None,
        help="Clone SRC_DISK to TGT_DISK.",
    )

    # Queries
    parser.add_argument(
        "--list-disks",
        action="store_true",
        default=False,
        help="List all physical disks and exit.",
    )
    parser.add_argument(
        "--list-backups",
        action="store_true",
        default=False,
        help="List all available backups and exit.",
    )
    parser.add_argument(
        "--verify-backup",
        metavar="ID",
        default=None,
        help="Verify the integrity of a backup by its ID.",
    )

    # Health / benchmark
    parser.add_argument(
        "--health",
        action="store_true",
        default=False,
        help="Show disk health information.",
    )
    parser.add_argument(
        "--disk",
        metavar="N",
        type=int,
        default=None,
        help="Disk number to target (used with --health).",
    )
    parser.add_argument(
        "--benchmark",
        metavar="DRIVE",
        default=None,
        help="Run a simple sequential-read benchmark on a drive letter (e.g. C).",
    )

    # Scheduled
    parser.add_argument(
        "--scheduled-backup",
        metavar="SCHEDULE_ID",
        default=None,
        help="Execute a previously configured scheduled backup by its ID.",
    )

    # Version
    parser.add_argument(
        "--version",
        action="store_true",
        default=False,
        help="Print the application version and exit.",
    )

    return parser


def _has_action(ns: argparse.Namespace) -> bool:
    """Return *True* if the user specified at least one actionable flag."""
    return any([
        ns.version,
        ns.list_disks,
        ns.list_backups,
        ns.backup,
        ns.clone,
        ns.verify_backup,
        ns.health,
        ns.benchmark,
        ns.scheduled_backup,
    ])


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _cmd_version() -> int:
    """Print version string."""
    try:
        from src import __version__
        print(f"OneClick Backup & Disk Manager v{__version__}")
    except ImportError:
        print("OneClick Backup & Disk Manager (version unknown)")
    return 0


def _cmd_list_disks() -> int:
    """Print a table of all physical disks."""
    from src.core.disk_info import get_all_disks

    disks = get_all_disks()
    if not disks:
        print("No disks found. (Try running as Administrator.)")
        return 0

    # Header
    print(
        f"{'#':<4} {'Model':<30} {'Size':<12} {'Type':<6} "
        f"{'Interface':<10} {'Style':<6} {'Health':<10} {'Parts':<6}"
    )
    print("-" * 84)

    for d in disks:
        print(
            f"{d.index:<4} {d.model[:29]:<30} "
            f"{format_bytes(d.size_bytes):<12} {d.media_type:<6} "
            f"{d.interface_type:<10} {d.partition_style:<6} "
            f"{d.health_status:<10} {len(d.partitions):<6}"
        )

    return 0


def _cmd_list_backups() -> int:
    """Print a table of all known backups."""
    from src.core.backup import BackupManager

    mgr = BackupManager()
    backups = mgr.list_backups()

    if not backups:
        print("No backups found.")
        return 0

    print(
        f"{'ID':<24} {'Name':<25} {'Type':<12} "
        f"{'Size':<12} {'Timestamp':<20}"
    )
    print("-" * 93)

    for b in backups:
        print(
            f"{b.backup_id:<24} {b.name[:24]:<25} "
            f"{b.backup_type:<12} "
            f"{format_bytes(b.compressed_size_bytes):<12} "
            f"{b.timestamp[:19]:<20}"
        )

    return 0


def _cmd_backup(backup_type: str, dest: str, name: str) -> int:
    """Create a backup of the specified type."""
    from src.core.backup import BackupManager

    mgr = BackupManager(backup_dir=dest if dest else "")

    def _progress(msg: str, pct: float) -> None:
        print(f"  [{pct:5.1f}%] {msg}")

    mgr.set_progress_callback(_progress)

    if backup_type == "system":
        info = mgr.create_system_backup(
            destination_path=dest, name=name
        )
    elif backup_type == "full_disk":
        info = mgr.create_full_disk_backup(disk_index=0, name=name)
    elif backup_type == "partition":
        info = mgr.create_partition_backup(
            disk_index=0, partition_index=1, name=name
        )
    else:
        _print_err(f"Unknown backup type: {backup_type}")
        return 1

    print(f"\nBackup complete: {info.backup_id}")
    print(f"  Path: {info.backup_path}")
    print(f"  Size: {format_bytes(info.compressed_size_bytes)}")
    return 0


def _cmd_clone(disk_pair: list[int]) -> int:
    """Clone source disk to target disk."""
    from src.core.backup import BackupManager

    src_disk, tgt_disk = disk_pair

    if src_disk == tgt_disk:
        _print_err("Source and target disk cannot be the same.")
        return 1

    mgr = BackupManager()

    def _progress(msg: str, pct: float) -> None:
        print(f"  [{pct:5.1f}%] {msg}")

    mgr.set_progress_callback(_progress)
    mgr.clone_disk(src_disk, tgt_disk)

    print(f"\nClone complete: disk {src_disk} -> disk {tgt_disk}")
    return 0


def _cmd_verify_backup(backup_id: str) -> int:
    """Verify backup integrity."""
    from src.core.backup import BackupManager

    mgr = BackupManager()

    def _progress(msg: str, pct: float) -> None:
        print(f"  [{pct:5.1f}%] {msg}")

    mgr.set_progress_callback(_progress)
    ok = mgr.verify_backup(backup_id)

    if ok:
        print(f"Backup {backup_id}: PASSED")
        return 0
    else:
        print(f"Backup {backup_id}: FAILED")
        return 1


def _cmd_health(disk_index: int | None) -> int:
    """Print disk health information."""
    from src.core.disk_info import get_all_disks, get_disk_health

    disks = get_all_disks()
    if not disks:
        print("No disks found. (Try running as Administrator.)")
        return 0

    targets = disks
    if disk_index is not None:
        targets = [d for d in disks if d.index == disk_index]
        if not targets:
            _print_err(f"Disk {disk_index} not found.")
            return 1

    for d in targets:
        # Refresh health via the dedicated helper for accuracy.
        health = get_disk_health(d.index)
        aligned = "Yes" if d.is_4k_aligned else "No"
        print(f"Disk {d.index}: {d.model}")
        print(f"  Health     : {health}")
        print(f"  Media      : {d.media_type}")
        print(f"  Size       : {format_bytes(d.size_bytes)}")
        print(f"  4K Aligned : {aligned}")
        print()

    return 0


def _cmd_benchmark(drive_letter: str) -> int:
    """Run a simple sequential-read throughput benchmark.

    Reads the first 128 MB of the drive (or available free space) and
    reports the MB/s rate.  This is a coarse indicator only.
    """
    import os
    import time

    drive_letter = drive_letter.strip().rstrip(":").upper()
    test_path = f"{drive_letter}:\\"

    if not os.path.isdir(test_path):
        _print_err(f"Drive {drive_letter}: is not accessible.")
        return 1

    # Find a readable file to use as a benchmark source.  We read the
    # first large file we encounter under the root directory.
    target_file: str | None = None
    min_size = 16 * 1024 * 1024  # 16 MB minimum

    for dirpath, _dirs, files in os.walk(test_path):
        for fname in files:
            fp = os.path.join(dirpath, fname)
            try:
                if os.path.getsize(fp) >= min_size:
                    target_file = fp
                    break
            except OSError:
                continue
        if target_file:
            break

    if target_file is None:
        _print_err(
            f"No file >= 16 MB found on {drive_letter}: for benchmarking."
        )
        return 1

    read_bytes = 128 * 1024 * 1024  # 128 MB
    chunk_size = 1024 * 1024  # 1 MB

    print(f"Benchmarking sequential read on {drive_letter}: ...")
    print(f"  File: {target_file}")

    total_read = 0
    start = time.perf_counter()
    try:
        with open(target_file, "rb") as fh:
            while total_read < read_bytes:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                total_read += len(chunk)
    except OSError as exc:
        _print_err(f"Read error: {exc}")
        return 1

    elapsed = time.perf_counter() - start
    if elapsed <= 0:
        elapsed = 0.001

    mb_read = total_read / (1024 * 1024)
    throughput = mb_read / elapsed

    print(f"  Read {mb_read:.1f} MB in {elapsed:.2f} s")
    print(f"  Throughput: {throughput:.1f} MB/s")
    return 0


def _cmd_scheduled_backup(schedule_id: str) -> int:
    """Execute a previously configured scheduled backup.

    Scheduled-backup configuration is stored as JSON files under the
    backup directory.  This command loads the configuration and runs the
    matching backup type.
    """
    import os

    from src.core.backup import BackupManager

    mgr = BackupManager()
    config_path = os.path.join(mgr.backup_dir, f"schedule_{schedule_id}.json")

    if not os.path.isfile(config_path):
        _print_err(f"Schedule configuration not found: {config_path}")
        return 1

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        _print_err(f"Failed to read schedule config: {exc}")
        return 1

    backup_type = config.get("backup_type", "system")
    dest = config.get("dest", "")
    name = config.get("name", f"scheduled_{schedule_id}")

    print(f"Running scheduled backup '{schedule_id}' (type={backup_type})")
    return _cmd_backup(backup_type, dest, name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_err(msg: str) -> None:
    """Print an error message to stderr."""
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Allow ``python -m src.utils.cli``
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(run_cli())
