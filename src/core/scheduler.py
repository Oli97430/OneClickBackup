"""Backup scheduling via Windows Task Scheduler.

Manages scheduled backup tasks using PowerShell cmdlets
(``Register-ScheduledTask``, ``Unregister-ScheduledTask``, etc.) and
persists configuration to a JSON file in the user's home directory.

Each schedule registers a Windows Task Scheduler entry that invokes
``pythonw.exe main.py --scheduled-backup <backup_id>`` at the
requested cadence.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from src.utils.helpers import run_powershell


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TASK_PREFIX = "OneClickBackup_"
"""Prefix applied to every Task Scheduler entry so our tasks are easy
to identify and will not collide with unrelated tasks."""

_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), ".oneclickbackup_schedules.json"
)

_VALID_SCHEDULE_TYPES = ("daily", "weekly", "monthly")

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
"""Matches HH:MM in 24-hour format (00:00 .. 23:59)."""

_VALID_DAYS_OF_WEEK = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
"""Allowed characters for a schedule name (used in the task path)."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ScheduleEntry:
    """Persisted configuration for a single scheduled backup."""

    name: str
    backup_type: str          # "full_disk", "partition", "system"
    schedule_type: str        # "daily", "weekly", "monthly"
    time_str: str             # "HH:MM" (24-hour)
    day_of_week: str | None   # e.g. "Monday"; required for weekly
    day_of_month: int | None  # 1..28; required for monthly
    task_name: str            # full Task Scheduler name (_TASK_PREFIX + name)
    created_at: str           # ISO-8601 timestamp
    enabled: bool = True


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SchedulerError(Exception):
    """Raised when a scheduling operation fails."""


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

def _validate_name(name: str) -> None:
    """Raise *ValueError* if *name* is not a safe task-name fragment."""
    if not _SAFE_NAME_RE.match(name):
        raise ValueError(
            f"Schedule name {name!r} is invalid. "
            "Use 1-64 characters: letters, digits, hyphens, underscores."
        )


def _validate_time(time_str: str) -> None:
    """Raise *ValueError* unless *time_str* matches ``HH:MM``."""
    if not _TIME_RE.match(time_str):
        raise ValueError(
            f"Time {time_str!r} is invalid. Expected HH:MM in 24-hour format."
        )


def _validate_schedule_type(schedule_type: str) -> None:
    if schedule_type not in _VALID_SCHEDULE_TYPES:
        raise ValueError(
            f"Unsupported schedule type {schedule_type!r}. "
            f"Must be one of {_VALID_SCHEDULE_TYPES!r}."
        )


def _validate_day_of_week(day: str | None, schedule_type: str) -> None:
    if schedule_type != "weekly":
        return
    if day is None:
        raise ValueError(
            "day_of_week is required for weekly schedules."
        )
    if day not in _VALID_DAYS_OF_WEEK:
        raise ValueError(
            f"Invalid day_of_week {day!r}. "
            f"Must be one of {_VALID_DAYS_OF_WEEK!r}."
        )


def _validate_day_of_month(day: int | None, schedule_type: str) -> None:
    if schedule_type != "monthly":
        return
    if day is None:
        raise ValueError(
            "day_of_month is required for monthly schedules."
        )
    if not isinstance(day, int) or not 1 <= day <= 28:
        raise ValueError(
            f"day_of_month must be an integer between 1 and 28, got {day!r}. "
            "Values above 28 are disallowed to avoid months without that day."
        )


# ---------------------------------------------------------------------------
# BackupScheduler
# ---------------------------------------------------------------------------

class BackupScheduler:
    """Create, remove, and query scheduled backup tasks.

    Schedules are registered in Windows Task Scheduler **and** mirrored
    to a JSON config file so they can be listed without querying the OS
    every time.
    """

    def __init__(self, config_path: str = "") -> None:
        self._config_path = config_path or _CONFIG_PATH
        self._log = logging.getLogger("OneClickBackup.Scheduler")
        self._schedules: dict[str, ScheduleEntry] = {}
        self._load_config()

    # -- Public API ---------------------------------------------------------

    def schedule_backup(
        self,
        name: str,
        backup_type: str,
        schedule_type: str,
        time_str: str,
        day_of_week: str | None = None,
        day_of_month: int | None = None,
    ) -> ScheduleEntry:
        """Register a new scheduled backup.

        Args:
            name:          Unique label (letters, digits, hyphens, underscores).
            backup_type:   One of ``"full_disk"``, ``"partition"``, ``"system"``.
            schedule_type: One of ``"daily"``, ``"weekly"``, ``"monthly"``.
            time_str:      Execution time as ``"HH:MM"`` (24-hour).
            day_of_week:   Required for weekly schedules (e.g. ``"Monday"``).
            day_of_month:  Required for monthly schedules (1..28).

        Returns:
            The created :class:`ScheduleEntry`.

        Raises:
            ValueError: On invalid arguments.
            SchedulerError: If the Task Scheduler command fails.
        """
        # --- validation ---
        _validate_name(name)
        _validate_time(time_str)
        _validate_schedule_type(schedule_type)
        _validate_day_of_week(day_of_week, schedule_type)
        _validate_day_of_month(day_of_month, schedule_type)

        if name in self._schedules:
            raise SchedulerError(
                f"A schedule named {name!r} already exists. "
                "Remove it first or choose a different name."
            )

        task_name = f"{_TASK_PREFIX}{name}"
        entry = ScheduleEntry(
            name=name,
            backup_type=backup_type,
            schedule_type=schedule_type,
            time_str=time_str,
            day_of_week=day_of_week,
            day_of_month=day_of_month,
            task_name=task_name,
            created_at=datetime.now().isoformat(),
        )

        # Register in Windows Task Scheduler
        self._register_task(entry)

        # Persist
        self._schedules[name] = entry
        self._save_config()
        self._log.info(
            "Scheduled backup %r (%s, %s at %s).",
            name, backup_type, schedule_type, time_str,
        )
        return entry

    def remove_schedule(self, name: str) -> None:
        """Remove a scheduled backup by name.

        Unregisters the Windows task and deletes the entry from the
        config file.

        Raises:
            SchedulerError: If *name* is not found or the task cannot
                be unregistered.
        """
        if name not in self._schedules:
            raise SchedulerError(f"Schedule {name!r} not found.")

        entry = self._schedules[name]
        self._unregister_task(entry.task_name)

        del self._schedules[name]
        self._save_config()
        self._log.info("Removed schedule %r.", name)

    def list_schedules(self) -> list[dict]:
        """Return all schedule entries as plain dicts (JSON-friendly)."""
        return [asdict(e) for e in self._schedules.values()]

    def get_schedule(self, name: str) -> dict | None:
        """Return a single schedule as a dict, or *None* if not found."""
        entry = self._schedules.get(name)
        if entry is None:
            return None
        return asdict(entry)

    def is_scheduled(self, name: str) -> bool:
        """Return whether a schedule with the given name exists."""
        return name in self._schedules

    # -- Config persistence -------------------------------------------------

    def _load_config(self) -> None:
        """Load schedule entries from the JSON config file."""
        if not os.path.isfile(self._config_path):
            self._log.debug("No config file at %s; starting empty.", self._config_path)
            return

        try:
            with open(self._config_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            self._log.warning(
                "Failed to read schedule config %s: %s", self._config_path, exc
            )
            return

        if not isinstance(data, dict):
            self._log.warning("Unexpected config format in %s; ignoring.", self._config_path)
            return

        for name, raw in data.items():
            try:
                self._schedules[name] = ScheduleEntry(**raw)
            except (TypeError, KeyError) as exc:
                self._log.warning(
                    "Skipping malformed schedule entry %r: %s", name, exc
                )

        self._log.debug(
            "Loaded %d schedule(s) from %s.", len(self._schedules), self._config_path
        )

    def _save_config(self) -> None:
        """Write all schedule entries to the JSON config file."""
        data = {name: asdict(entry) for name, entry in self._schedules.items()}
        try:
            with open(self._config_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            self._log.debug("Saved %d schedule(s) to %s.", len(data), self._config_path)
        except OSError as exc:
            self._log.error(
                "Failed to write schedule config %s: %s", self._config_path, exc
            )
            raise SchedulerError(
                f"Could not save schedule config to {self._config_path}: {exc}"
            ) from exc

    # -- Windows Task Scheduler interaction ---------------------------------

    def _get_executable_path(self) -> str:
        """Resolve the path to ``pythonw.exe`` alongside the running interpreter."""
        python_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(python_dir, "pythonw.exe")
        if os.path.isfile(pythonw):
            return pythonw
        # Fall back to the current interpreter (may be python.exe)
        return sys.executable

    def _get_main_script(self) -> str:
        """Return the absolute path to ``main.py``."""
        # Project root is two levels up from src/core/scheduler.py
        project_root = str(Path(__file__).resolve().parents[2])
        return os.path.join(project_root, "main.py")

    def _build_trigger_ps(self, entry: ScheduleEntry) -> str:
        """Build the ``New-ScheduledTaskTrigger`` fragment for *entry*."""
        if entry.schedule_type == "daily":
            return f"New-ScheduledTaskTrigger -Daily -At '{entry.time_str}'"

        if entry.schedule_type == "weekly":
            return (
                f"New-ScheduledTaskTrigger -Weekly "
                f"-DaysOfWeek {entry.day_of_week} -At '{entry.time_str}'"
            )

        # monthly — Task Scheduler CIM trigger via manual construction
        # New-ScheduledTaskTrigger does not support -Monthly natively in
        # all PS versions, so we create a CIM instance directly.
        return (
            "$trigger = New-CimInstance -CimClass "
            "(Get-CimClass -Namespace 'Root/Microsoft/Windows/TaskScheduler' "
            "-ClassName 'MSFT_TaskMonthlyTrigger') -ClientOnly; "
            f"$trigger.DaysOfMonth = {entry.day_of_month}; "
            "$trigger.MonthsOfYear = 4095; "  # all 12 months
            f"$trigger.StartBoundary = (Get-Date -Hour {entry.time_str.split(':')[0]} "
            f"-Minute {entry.time_str.split(':')[1]} -Second 0).ToString('s'); "
            "$trigger"
        )

    def _register_task(self, entry: ScheduleEntry) -> None:
        """Register a task in Windows Task Scheduler via PowerShell.

        Raises:
            SchedulerError: If the PowerShell command fails.
        """
        executable = self._get_executable_path()
        main_script = self._get_main_script()
        arguments = f'"{main_script}" --scheduled-backup {entry.name}'

        if entry.schedule_type == "monthly":
            # Monthly triggers need the CIM-instance approach
            trigger_fragment = self._build_trigger_ps(entry)
            ps_cmd = (
                f"{trigger_fragment}; "
                f"$action = New-ScheduledTaskAction "
                f"-Execute '{executable}' -Argument '{arguments}'; "
                f"$settings = New-ScheduledTaskSettingsSet "
                f"-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                f"-StartWhenAvailable; "
                f"Register-ScheduledTask "
                f"-TaskName '{entry.task_name}' "
                f"-Trigger $trigger -Action $action -Settings $settings "
                f"-Description 'OneClickBackup scheduled backup: {entry.name}' "
                f"-Force"
            )
        else:
            trigger_fragment = self._build_trigger_ps(entry)
            ps_cmd = (
                f"$trigger = {trigger_fragment}; "
                f"$action = New-ScheduledTaskAction "
                f"-Execute '{executable}' -Argument '{arguments}'; "
                f"$settings = New-ScheduledTaskSettingsSet "
                f"-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                f"-StartWhenAvailable; "
                f"Register-ScheduledTask "
                f"-TaskName '{entry.task_name}' "
                f"-Trigger $trigger -Action $action -Settings $settings "
                f"-Description 'OneClickBackup scheduled backup: {entry.name}' "
                f"-Force"
            )

        self._log.debug("Registering task: %s", ps_cmd)
        stdout, stderr, rc = run_powershell(ps_cmd)

        if rc != 0:
            self._log.error(
                "Failed to register scheduled task %r (rc=%d): %s",
                entry.task_name, rc, stderr,
            )
            raise SchedulerError(
                f"Could not register task {entry.task_name!r} in "
                f"Windows Task Scheduler: {stderr}"
            )

        self._log.debug("Task registered: %s — %s", entry.task_name, stdout)

    def _unregister_task(self, task_name: str) -> None:
        """Remove a task from Windows Task Scheduler.

        Raises:
            SchedulerError: If the PowerShell command fails.
        """
        ps_cmd = (
            f"Unregister-ScheduledTask -TaskName '{task_name}' "
            f"-Confirm:$false -ErrorAction Stop"
        )
        self._log.debug("Unregistering task: %s", ps_cmd)
        stdout, stderr, rc = run_powershell(ps_cmd)

        if rc != 0:
            self._log.error(
                "Failed to unregister task %r (rc=%d): %s",
                task_name, rc, stderr,
            )
            raise SchedulerError(
                f"Could not unregister task {task_name!r}: {stderr}"
            )

        self._log.debug("Task unregistered: %s", task_name)
