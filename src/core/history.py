"""Persistent operation history for OneClickBackup.

Stores every executed operation with timestamp, result, and details
in a JSON-Lines file for auditing and review.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime

_log = logging.getLogger(__name__)

_HISTORY_DIR = os.path.join(os.path.expanduser("~"), ".oneclickbackup_logs")
_HISTORY_FILE = os.path.join(_HISTORY_DIR, "operation_history.jsonl")
_LOCK = threading.Lock()


@dataclass
class HistoryEntry:
    """A single recorded operation."""

    timestamp: str
    operation: str
    description: str
    success: bool
    message: str
    disk_index: int | None = None
    partition_index: int | None = None
    duration_seconds: float = 0.0
    details: dict | None = None


class OperationHistory:
    """Thread-safe, file-backed operation history."""

    def __init__(self, path: str = "") -> None:
        self._path = path or _HISTORY_FILE
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        operation: str,
        description: str,
        success: bool,
        message: str = "",
        disk_index: int | None = None,
        partition_index: int | None = None,
        duration_seconds: float = 0.0,
        details: dict | None = None,
    ) -> HistoryEntry:
        """Append an entry to the history file."""
        entry = HistoryEntry(
            timestamp=datetime.now().isoformat(),
            operation=operation,
            description=description,
            success=success,
            message=message,
            disk_index=disk_index,
            partition_index=partition_index,
            duration_seconds=duration_seconds,
            details=details,
        )
        with _LOCK:
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
            except OSError as exc:
                _log.warning("Failed to write history entry: %s", exc)
        return entry

    def get_all(self, limit: int = 500) -> list[HistoryEntry]:
        """Return the most recent *limit* entries (newest first)."""
        entries: list[HistoryEntry] = []
        with _LOCK:
            if not os.path.isfile(self._path):
                return entries
            try:
                with open(self._path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            entries.append(HistoryEntry(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue
            except OSError as exc:
                _log.warning("Failed to read history: %s", exc)
        entries.reverse()
        return entries[:limit]

    def get_by_operation(self, operation: str, limit: int = 100) -> list[HistoryEntry]:
        """Return entries filtered by operation type."""
        return [e for e in self.get_all(limit * 5) if e.operation == operation][:limit]

    def get_failures(self, limit: int = 100) -> list[HistoryEntry]:
        """Return only failed operations."""
        return [e for e in self.get_all(limit * 5) if not e.success][:limit]

    def clear(self) -> None:
        """Remove all history entries."""
        with _LOCK:
            try:
                if os.path.isfile(self._path):
                    os.remove(self._path)
                    _log.info("Operation history cleared.")
            except OSError as exc:
                _log.warning("Failed to clear history: %s", exc)

    def export_json(self, output_path: str) -> str:
        """Export full history as a formatted JSON file."""
        entries = self.get_all(limit=10000)
        data = [asdict(e) for e in entries]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _log.info("Exported %d history entries to %s", len(data), output_path)
        return output_path

    @property
    def count(self) -> int:
        """Return the total number of entries (line count, no parsing)."""
        with _LOCK:
            if not os.path.isfile(self._path):
                return 0
            try:
                with open(self._path, encoding="utf-8") as f:
                    return sum(1 for line in f if line.strip())
            except OSError:
                return 0
