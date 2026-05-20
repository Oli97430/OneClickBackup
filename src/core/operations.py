"""
operations.py - Safe wrappers around Windows disk management operations.

Provides a preview-before-apply pattern for partition management using
diskpart and PowerShell commands. Each operation is validated, queued,
and only executed after explicit confirmation.
"""

from __future__ import annotations

import subprocess
import tempfile
import os
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Input sanitisation
# ---------------------------------------------------------------------------

_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9 _\-]{0,32}$")


def _sanitize_label(label: str) -> str:
    """Validate and return a safe volume label for diskpart.

    Raises *ValueError* if the label contains dangerous characters.
    """
    label = label.strip()
    if not label:
        return ""
    if not _SAFE_LABEL_RE.match(label):
        raise ValueError(
            f"Volume label {label!r} contains invalid characters. "
            "Only letters, digits, spaces, hyphens, and underscores are allowed (max 32 chars)."
        )
    return label


@dataclass
class PendingOperation:
    """Represents a queued operation that hasn't been applied yet."""

    op_type: str  # "resize", "create", "delete", "format", "merge", "convert_mbr_gpt", etc.
    description: str  # Human-readable description
    disk_index: int
    params: dict  # Operation-specific parameters
    risk_level: str  # "low", "medium", "high", "critical"
    reversible: bool


class OperationManager:
    """Manages a queue of pending operations with preview-before-apply pattern.

    Operations are first queued via queue_* methods, allowing callers to review
    the full set of changes before committing. The apply_all() method then
    executes them in order, stopping on the first failure.
    """

    # Valid file systems for format operations
    _VALID_FILE_SYSTEMS = {"NTFS", "FAT32", "EXFAT", "REFS"}

    # Valid drive letters
    _VALID_DRIVE_LETTERS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    def __init__(self):
        self._pending: list[PendingOperation] = []
        self._history: list[dict] = []
        self._progress_callback: Callable | None = None
        self._log = logging.getLogger("OneClickBackup.Operations")

    # ------------------------------------------------------------------
    # Progress / queue management
    # ------------------------------------------------------------------

    def set_progress_callback(self, callback: Callable[[str, float], None]) -> None:
        """Set callback for progress updates: callback(message, percent_0_to_100)."""
        self._progress_callback = callback

    def _report_progress(self, message: str, percent: float) -> None:
        """Send a progress update if a callback is registered."""
        if self._progress_callback is not None:
            self._progress_callback(message, percent)

    def add_operation(self, op: PendingOperation) -> None:
        """Add operation to pending queue."""
        self._log.info("Queued operation: %s", op.description)
        self._pending.append(op)

    def remove_operation(self, index: int) -> None:
        """Remove operation from pending queue by index."""
        if index < 0 or index >= len(self._pending):
            raise IndexError(
                f"Operation index {index} out of range (0-{len(self._pending) - 1})"
            )
        removed = self._pending.pop(index)
        self._log.info("Removed queued operation: %s", removed.description)

    def clear_pending(self) -> None:
        """Clear all pending operations."""
        count = len(self._pending)
        self._pending.clear()
        self._log.info("Cleared %d pending operations", count)

    def get_pending(self) -> list[PendingOperation]:
        """Get list of pending operations."""
        return list(self._pending)

    def get_history(self) -> list[dict]:
        """Get the history of executed operations."""
        return list(self._history)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply_all(self) -> list[dict]:
        """Apply all pending operations in order.

        Returns a list of result dicts, each containing:
            - "op": the PendingOperation
            - "success": bool
            - "message": str with details or error info

        Stops on first failure — remaining operations stay in the queue.
        """
        results: list[dict] = []
        total = len(self._pending)

        if total == 0:
            self._log.info("No pending operations to apply")
            return results

        self._log.info("Applying %d pending operations", total)

        applied_indices: list[int] = []

        for idx, op in enumerate(self._pending):
            percent = (idx / total) * 100
            self._report_progress(f"Executing ({idx + 1}/{total}): {op.description}", percent)

            result = self._execute_operation(op)
            results.append(result)
            self._history.append(result)

            if not result["success"]:
                self._log.error(
                    "Operation failed (%d/%d): %s — %s",
                    idx + 1,
                    total,
                    op.description,
                    result["message"],
                )
                # Remove only the operations that were attempted (including the failed one)
                self._pending = self._pending[idx + 1 :]
                self._report_progress(f"Failed at operation {idx + 1}/{total}", percent)
                return results

            applied_indices.append(idx)
            self._log.info(
                "Operation succeeded (%d/%d): %s", idx + 1, total, op.description
            )

        self._pending.clear()
        self._report_progress("All operations completed", 100.0)
        return results

    # ------------------------------------------------------------------
    # Queue helpers — input validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_disk_index(disk_index: int) -> None:
        if not isinstance(disk_index, int) or disk_index < 0:
            raise ValueError(f"disk_index must be a non-negative integer, got {disk_index!r}")

    @staticmethod
    def _validate_partition_index(partition_index: int) -> None:
        if not isinstance(partition_index, int) or partition_index < 1:
            raise ValueError(
                f"partition_index must be a positive integer (1-based), got {partition_index!r}"
            )

    @classmethod
    def _validate_file_system(cls, file_system: str) -> None:
        fs_upper = file_system.upper()
        if fs_upper not in cls._VALID_FILE_SYSTEMS:
            raise ValueError(
                f"Unsupported file system {file_system!r}. "
                f"Must be one of {sorted(cls._VALID_FILE_SYSTEMS)}"
            )

    @classmethod
    def _validate_drive_letter(cls, letter: str) -> None:
        if len(letter) != 1 or letter.upper() not in cls._VALID_DRIVE_LETTERS:
            raise ValueError(f"Invalid drive letter {letter!r}. Must be A-Z.")

    @staticmethod
    def _validate_size_bytes(size_bytes: int, label: str = "size_bytes") -> None:
        if not isinstance(size_bytes, int) or size_bytes <= 0:
            raise ValueError(f"{label} must be a positive integer, got {size_bytes!r}")

    # ------------------------------------------------------------------
    # Queue methods — each builds a PendingOperation
    # ------------------------------------------------------------------

    def queue_resize_partition(
        self, disk_index: int, partition_index: int, new_size_bytes: int
    ) -> None:
        """Queue a partition resize operation.

        Uses PowerShell: Resize-Partition -DiskNumber X -PartitionNumber Y -Size Z
        """
        self._validate_disk_index(disk_index)
        self._validate_partition_index(partition_index)
        self._validate_size_bytes(new_size_bytes, "new_size_bytes")

        size_mb = new_size_bytes / (1024 * 1024)
        op = PendingOperation(
            op_type="resize",
            description=(
                f"Resize partition {partition_index} on disk {disk_index} "
                f"to {size_mb:,.1f} MB"
            ),
            disk_index=disk_index,
            params={
                "partition_index": partition_index,
                "new_size_bytes": new_size_bytes,
            },
            risk_level="high",
            reversible=True,
        )
        self.add_operation(op)

    def queue_create_partition(
        self,
        disk_index: int,
        size_bytes: int,
        file_system: str = "NTFS",
        label: str = "",
        drive_letter: str = "",
    ) -> None:
        """Queue partition creation.

        Uses diskpart: create partition primary size=X, then format.
        """
        self._validate_disk_index(disk_index)
        self._validate_size_bytes(size_bytes)
        self._validate_file_system(file_system)
        if drive_letter:
            self._validate_drive_letter(drive_letter)

        size_mb = size_bytes / (1024 * 1024)
        desc_parts = [
            f"Create {file_system} partition on disk {disk_index} ({size_mb:,.1f} MB)"
        ]
        if label:
            desc_parts.append(f"label={label!r}")
        if drive_letter:
            desc_parts.append(f"letter={drive_letter.upper()}")

        op = PendingOperation(
            op_type="create",
            description=", ".join(desc_parts),
            disk_index=disk_index,
            params={
                "size_bytes": size_bytes,
                "file_system": file_system.upper(),
                "label": label,
                "drive_letter": drive_letter.upper() if drive_letter else "",
            },
            risk_level="medium",
            reversible=True,
        )
        self.add_operation(op)

    def queue_delete_partition(self, disk_index: int, partition_index: int) -> None:
        """Queue partition deletion.

        Uses diskpart: delete partition override.
        """
        self._validate_disk_index(disk_index)
        self._validate_partition_index(partition_index)

        op = PendingOperation(
            op_type="delete",
            description=(
                f"Delete partition {partition_index} on disk {disk_index}"
            ),
            disk_index=disk_index,
            params={"partition_index": partition_index},
            risk_level="critical",
            reversible=False,
        )
        self.add_operation(op)

    def queue_format_partition(
        self,
        disk_index: int,
        partition_index: int,
        file_system: str = "NTFS",
        label: str = "",
        quick: bool = True,
    ) -> None:
        """Queue format operation.

        Uses diskpart or PowerShell Format-Volume.
        """
        self._validate_disk_index(disk_index)
        self._validate_partition_index(partition_index)
        self._validate_file_system(file_system)

        fmt_type = "quick" if quick else "full"
        desc = (
            f"Format partition {partition_index} on disk {disk_index} "
            f"as {file_system.upper()} ({fmt_type})"
        )
        if label:
            desc += f" label={label!r}"

        op = PendingOperation(
            op_type="format",
            description=desc,
            disk_index=disk_index,
            params={
                "partition_index": partition_index,
                "file_system": file_system.upper(),
                "label": label,
                "quick": quick,
            },
            risk_level="critical",
            reversible=False,
        )
        self.add_operation(op)

    def queue_merge_partitions(
        self, disk_index: int, partition_index_1: int, partition_index_2: int
    ) -> None:
        """Queue merge of two adjacent partitions (delete second, extend first).

        The second partition is deleted and the first is extended to fill
        the freed space.
        """
        self._validate_disk_index(disk_index)
        self._validate_partition_index(partition_index_1)
        self._validate_partition_index(partition_index_2)

        if partition_index_1 == partition_index_2:
            raise ValueError("Cannot merge a partition with itself")

        op = PendingOperation(
            op_type="merge",
            description=(
                f"Merge partitions {partition_index_1} and {partition_index_2} "
                f"on disk {disk_index} (delete {partition_index_2}, extend {partition_index_1})"
            ),
            disk_index=disk_index,
            params={
                "partition_index_1": partition_index_1,
                "partition_index_2": partition_index_2,
            },
            risk_level="critical",
            reversible=False,
        )
        self.add_operation(op)

    def queue_convert_mbr_to_gpt(self, disk_index: int) -> None:
        """Queue MBR to GPT conversion.

        Uses mbr2gpt.exe /convert /disk:X /allowFullOS for non-destructive conversion.
        """
        self._validate_disk_index(disk_index)

        op = PendingOperation(
            op_type="convert_mbr_gpt",
            description=f"Convert disk {disk_index} from MBR to GPT",
            disk_index=disk_index,
            params={},
            risk_level="high",
            reversible=False,
        )
        self.add_operation(op)

    def queue_convert_gpt_to_mbr(self, disk_index: int) -> None:
        """Queue GPT to MBR conversion.

        WARNING: This is a destructive operation — disk must be empty.
        Uses diskpart: convert mbr.
        """
        self._validate_disk_index(disk_index)

        op = PendingOperation(
            op_type="convert_gpt_mbr",
            description=f"Convert disk {disk_index} from GPT to MBR",
            disk_index=disk_index,
            params={},
            risk_level="critical",
            reversible=False,
        )
        self.add_operation(op)

    def queue_set_active(self, disk_index: int, partition_index: int) -> None:
        """Queue set partition as active (MBR disks only)."""
        self._validate_disk_index(disk_index)
        self._validate_partition_index(partition_index)

        op = PendingOperation(
            op_type="set_active",
            description=(
                f"Set partition {partition_index} on disk {disk_index} as active"
            ),
            disk_index=disk_index,
            params={"partition_index": partition_index},
            risk_level="high",
            reversible=True,
        )
        self.add_operation(op)

    def queue_change_letter(
        self, disk_index: int, partition_index: int, new_letter: str
    ) -> None:
        """Queue drive letter change."""
        self._validate_disk_index(disk_index)
        self._validate_partition_index(partition_index)
        self._validate_drive_letter(new_letter)

        op = PendingOperation(
            op_type="change_letter",
            description=(
                f"Change drive letter of partition {partition_index} "
                f"on disk {disk_index} to {new_letter.upper()}:"
            ),
            disk_index=disk_index,
            params={
                "partition_index": partition_index,
                "new_letter": new_letter.upper(),
            },
            risk_level="low",
            reversible=True,
        )
        self.add_operation(op)

    def queue_4k_align(self, disk_index: int, partition_index: int) -> None:
        """Queue 4K alignment check / operation.

        Verifies whether the partition offset is 4K-aligned. If not, logs a
        warning. Actual realignment requires a recreate, which is queued
        separately if needed.
        """
        self._validate_disk_index(disk_index)
        self._validate_partition_index(partition_index)

        op = PendingOperation(
            op_type="4k_align",
            description=(
                f"Check/fix 4K alignment for partition {partition_index} "
                f"on disk {disk_index}"
            ),
            disk_index=disk_index,
            params={"partition_index": partition_index},
            risk_level="medium",
            reversible=False,
        )
        self.add_operation(op)

    # ------------------------------------------------------------------
    # Execution helpers (private)
    # ------------------------------------------------------------------

    def _execute_diskpart(self, script_lines: list[str]) -> tuple[bool, str]:
        """Run a diskpart script.

        Creates a temporary script file, executes ``diskpart /s <file>``,
        and returns (success, output).
        """
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, prefix="ocb_diskpart_"
            ) as tmp:
                tmp_path = tmp.name
                for line in script_lines:
                    tmp.write(line + "\n")

            self._log.debug("diskpart script (%s):\n%s", tmp_path, "\n".join(script_lines))

            result = subprocess.run(
                ["diskpart", "/s", tmp_path],
                capture_output=True,
                text=True,
                timeout=300,
            )

            output = result.stdout.strip()
            if result.returncode != 0:
                err = result.stderr.strip() if result.stderr else output
                self._log.error("diskpart failed (rc=%d): %s", result.returncode, err)
                return False, f"diskpart error (rc={result.returncode}): {err}"

            # diskpart may report errors inside stdout even with rc 0
            lower_output = output.lower()
            if "virtual disk service error" in lower_output or "the arguments" in lower_output:
                self._log.error("diskpart reported error in output: %s", output)
                return False, f"diskpart error: {output}"

            self._log.debug("diskpart output: %s", output)
            return True, output

        except subprocess.TimeoutExpired:
            self._log.error("diskpart timed out after 300 s")
            return False, "diskpart timed out after 300 seconds"
        except FileNotFoundError:
            self._log.error("diskpart executable not found")
            return False, "diskpart not found — are you running on Windows?"
        except OSError as exc:
            self._log.error("OS error running diskpart: %s", exc)
            return False, f"OS error: {exc}"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _execute_powershell(self, command: str) -> tuple[bool, str]:
        """Run a PowerShell command and return (success, output)."""
        self._log.debug("PowerShell command: %s", command)

        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )

            output = result.stdout.strip()
            if result.returncode != 0:
                err = result.stderr.strip() if result.stderr else output
                self._log.error(
                    "PowerShell failed (rc=%d): %s", result.returncode, err
                )
                return False, f"PowerShell error (rc={result.returncode}): {err}"

            self._log.debug("PowerShell output: %s", output)
            return True, output

        except subprocess.TimeoutExpired:
            self._log.error("PowerShell timed out after 300 s")
            return False, "PowerShell timed out after 300 seconds"
        except FileNotFoundError:
            self._log.error("powershell executable not found")
            return False, "powershell not found — are you running on Windows?"
        except OSError as exc:
            self._log.error("OS error running PowerShell: %s", exc)
            return False, f"OS error: {exc}"

    # ------------------------------------------------------------------
    # Operation dispatcher
    # ------------------------------------------------------------------

    def _execute_operation(self, op: PendingOperation) -> dict:
        """Execute a single operation. Routes to the appropriate handler.

        Returns a dict with keys: "op", "success", "message".
        """
        self._log.info("Executing: %s", op.description)

        handler_map = {
            "resize": self._exec_resize,
            "create": self._exec_create,
            "delete": self._exec_delete,
            "format": self._exec_format,
            "merge": self._exec_merge,
            "convert_mbr_gpt": self._exec_convert_mbr_gpt,
            "convert_gpt_mbr": self._exec_convert_gpt_mbr,
            "set_active": self._exec_set_active,
            "change_letter": self._exec_change_letter,
            "4k_align": self._exec_4k_align,
        }

        handler = handler_map.get(op.op_type)
        if handler is None:
            return {
                "op": op,
                "success": False,
                "message": f"Unknown operation type: {op.op_type!r}",
            }

        try:
            success, message = handler(op)
        except Exception as exc:
            self._log.exception("Unhandled error executing %s", op.op_type)
            success, message = False, f"Unexpected error: {exc}"

        return {"op": op, "success": success, "message": message}

    # ------------------------------------------------------------------
    # Individual operation executors
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_int(value: object, name: str) -> int:
        """Coerce *value* to int, raising if it cannot be safely converted.

        This is a defence-in-depth measure: all queue_* methods already
        validate inputs, but the executors re-check before interpolating
        values into shell commands.
        """
        v = int(value)  # type: ignore[arg-type]
        if str(v) != str(value).strip():
            raise ValueError(f"{name} is not a plain integer: {value!r}")
        return v

    def _exec_resize(self, op: PendingOperation) -> tuple[bool, str]:
        """Resize a partition via PowerShell Resize-Partition."""
        p = op.params
        disk = self._ensure_int(op.disk_index, "disk_index")
        part = self._ensure_int(p["partition_index"], "partition_index")
        size = self._ensure_int(p["new_size_bytes"], "new_size_bytes")
        cmd = (
            f"Resize-Partition -DiskNumber {disk} "
            f"-PartitionNumber {part} "
            f"-Size {size}"
        )
        return self._execute_powershell(cmd)

    def _exec_create(self, op: PendingOperation) -> tuple[bool, str]:
        """Create a partition via diskpart, then format it."""
        p = op.params
        disk = self._ensure_int(op.disk_index, "disk_index")
        # Round to nearest MB (avoids losing up to 1 MB via truncation)
        size_mb = max(1, round(int(p["size_bytes"]) / (1024 * 1024)))

        lines = [
            f"select disk {disk}",
            f"create partition primary size={size_mb}",
        ]

        # Assign drive letter if specified
        if p["drive_letter"]:
            lines.append(f"assign letter={p['drive_letter']}")

        # Format
        fmt_cmd = f"format fs={p['file_system']} quick"
        if p["label"]:
            safe_label = _sanitize_label(p["label"])
            if safe_label:
                fmt_cmd += f' label="{safe_label}"'
        lines.append(fmt_cmd)

        return self._execute_diskpart(lines)

    def _exec_delete(self, op: PendingOperation) -> tuple[bool, str]:
        """Delete a partition via diskpart."""
        p = op.params
        disk = self._ensure_int(op.disk_index, "disk_index")
        part = self._ensure_int(p["partition_index"], "partition_index")
        lines = [
            f"select disk {disk}",
            f"select partition {part}",
            "delete partition override",
        ]
        return self._execute_diskpart(lines)

    def _exec_format(self, op: PendingOperation) -> tuple[bool, str]:
        """Format a partition via diskpart."""
        p = op.params
        disk = self._ensure_int(op.disk_index, "disk_index")
        part = self._ensure_int(p["partition_index"], "partition_index")
        fmt_cmd = f"format fs={p['file_system']}"
        if p["quick"]:
            fmt_cmd += " quick"
        if p["label"]:
            safe_label = _sanitize_label(p["label"])
            if safe_label:
                fmt_cmd += f' label="{safe_label}"'

        lines = [
            f"select disk {disk}",
            f"select partition {part}",
            fmt_cmd,
        ]
        return self._execute_diskpart(lines)

    def _exec_merge(self, op: PendingOperation) -> tuple[bool, str]:
        """Merge two partitions: delete the second, then extend the first.

        This is a two-phase operation. If the delete succeeds but the extend
        fails, data on partition_index_2 is already lost.
        """
        p = op.params
        part1 = p["partition_index_1"]
        part2 = p["partition_index_2"]

        disk = self._ensure_int(op.disk_index, "disk_index")
        part1 = self._ensure_int(part1, "partition_index_1")
        part2 = self._ensure_int(part2, "partition_index_2")

        # Phase 1: Delete the second partition
        self._log.info("Merge phase 1: deleting partition %d on disk %d", part2, disk)
        delete_lines = [
            f"select disk {disk}",
            f"select partition {part2}",
            "delete partition override",
        ]
        ok, msg = self._execute_diskpart(delete_lines)
        if not ok:
            return False, f"Merge failed at delete phase: {msg}"

        # Phase 2: Extend the first partition into the freed space
        self._log.info("Merge phase 2: extending partition %d on disk %d", part1, disk)
        extend_lines = [
            f"select disk {disk}",
            f"select partition {part1}",
            "extend",
        ]
        ok, msg = self._execute_diskpart(extend_lines)
        if not ok:
            return False, (
                f"Merge partially completed — partition {part2} was deleted but "
                f"extending partition {part1} failed: {msg}"
            )

        return True, (
            f"Successfully merged: partition {part2} deleted, "
            f"partition {part1} extended"
        )

    def _exec_convert_mbr_gpt(self, op: PendingOperation) -> tuple[bool, str]:
        """Convert MBR to GPT using mbr2gpt.exe (non-destructive)."""
        disk = self._ensure_int(op.disk_index, "disk_index")
        cmd = f"& mbr2gpt.exe /convert /disk:{disk} /allowFullOS"
        return self._execute_powershell(cmd)

    def _exec_convert_gpt_mbr(self, op: PendingOperation) -> tuple[bool, str]:
        """Convert GPT to MBR using diskpart. Disk must be empty."""
        disk = self._ensure_int(op.disk_index, "disk_index")
        lines = [
            f"select disk {disk}",
            "convert mbr",
        ]
        return self._execute_diskpart(lines)

    def _exec_set_active(self, op: PendingOperation) -> tuple[bool, str]:
        """Set a partition as active (MBR only) using diskpart."""
        p = op.params
        disk = self._ensure_int(op.disk_index, "disk_index")
        part = self._ensure_int(p["partition_index"], "partition_index")
        lines = [
            f"select disk {disk}",
            f"select partition {part}",
            "active",
        ]
        return self._execute_diskpart(lines)

    def _exec_change_letter(self, op: PendingOperation) -> tuple[bool, str]:
        """Change a partition's drive letter using diskpart.

        Removes any existing letter assignment, then assigns the new one.
        """
        p = op.params
        disk = self._ensure_int(op.disk_index, "disk_index")
        part = self._ensure_int(p["partition_index"], "partition_index")
        letter = p["new_letter"]
        if len(letter) != 1 or not letter.isalpha():
            return False, f"Invalid drive letter: {letter!r}"
        lines = [
            f"select disk {disk}",
            f"select partition {part}",
            "remove",
            f"assign letter={letter.upper()}",
        ]
        return self._execute_diskpart(lines)

    def _exec_4k_align(self, op: PendingOperation) -> tuple[bool, str]:
        """Check 4K alignment of a partition.

        Reads the partition offset via PowerShell and checks whether it is
        a multiple of 4096 bytes.
        """
        p = op.params
        disk = self._ensure_int(op.disk_index, "disk_index")
        part = self._ensure_int(p["partition_index"], "partition_index")
        cmd = (
            f"Get-Partition -DiskNumber {disk} "
            f"-PartitionNumber {part} | "
            f"Select-Object -ExpandProperty Offset"
        )
        ok, output = self._execute_powershell(cmd)
        if not ok:
            return False, f"Could not read partition offset: {output}"

        try:
            offset = int(output.strip())
        except ValueError:
            return False, f"Unexpected offset value: {output!r}"

        aligned = (offset % 4096) == 0
        if aligned:
            msg = (
                f"Partition {p['partition_index']} on disk {op.disk_index} "
                f"is 4K-aligned (offset={offset})"
            )
            self._log.info(msg)
            return True, msg

        msg = (
            f"Partition {p['partition_index']} on disk {op.disk_index} "
            f"is NOT 4K-aligned (offset={offset}, remainder={offset % 4096}). "
            f"Realignment requires recreating the partition."
        )
        self._log.warning(msg)
        return False, msg
