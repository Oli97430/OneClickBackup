"""Windows toast notifications for OneClick Backup & Disk Manager.

Provides a lightweight notification system that uses PowerShell
``[Windows.UI.Notifications.ToastNotificationManager]`` as the primary
backend and falls back to a tkinter messagebox when toast delivery is
unavailable (e.g. older Windows builds, missing App ID, or headless
environments).

Usage:
    from src.utils.notifications import NotificationManager
    nm = NotificationManager()
    nm.notify("Backup complete", "Your disk was backed up successfully.")
    nm.notify_error("Clone", "Target disk is read-only.")
"""

from __future__ import annotations

import logging
import threading

import re as _re

from src.utils.helpers import format_bytes, run_powershell

_log = logging.getLogger(__name__)


class NotificationManager:
    """Send Windows toast notifications with automatic fallback.

    All public methods are safe to call from any thread and will never
    raise on delivery failure -- errors are logged and silently ignored.
    """

    # PowerShell template for a toast notification.
    # {title}, {message}, and {icon_uri} are interpolated at send time.
    _TOAST_PS_TEMPLATE = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "$xml = [Windows.Data.Xml.Dom.XmlDocument]::new(); "
        "$xml.LoadXml('"
        '<toast>'
        '<visual>'
        '<binding template="ToastGeneric">'
        '<text>{title}</text>'
        '<text>{message}</text>'
        '</binding>'
        '</visual>'
        '</toast>'
        "'); "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
        "[Windows.UI.Notifications.ToastNotificationManager]"
        "::CreateToastNotifier('{app_id}').Show($toast)"
    )

    _DEFAULT_APP_ID = "OneClickBackup"

    # Only allow alphanumeric characters, dots, and spaces in app IDs.
    _SAFE_APP_ID_RE = _re.compile(r"^[A-Za-z0-9. ]+$")

    def __init__(self, app_id: str = "") -> None:
        raw_id = app_id or self._DEFAULT_APP_ID
        if not self._SAFE_APP_ID_RE.match(raw_id):
            _log.warning(
                "Unsafe characters in app_id %r, falling back to default", raw_id
            )
            raw_id = self._DEFAULT_APP_ID
        self._app_id = raw_id
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify(self, title: str, message: str, icon: str = "info") -> None:
        """Display a toast notification.

        Args:
            title: Bold heading shown in the notification.
            message: Body text underneath the title.
            icon: One of ``"info"``, ``"warning"``, or ``"error"``.
                  Currently used only by the fallback path.
        """
        try:
            if not self._send_toast(title, message):
                self._fallback_notify(title, message, icon)
        except Exception as exc:
            _log.debug("Notification delivery failed: %s", exc)

    def notify_backup_complete(self, name: str, size_bytes: int) -> None:
        """Convenience: notify that a backup finished successfully.

        Args:
            name: Human-readable backup name.
            size_bytes: Total size of the backup in bytes.
        """
        self.notify(
            "Backup Complete",
            f"{name} finished successfully ({format_bytes(size_bytes)}).",
            icon="info",
        )

    def notify_error(self, operation: str, error: str) -> None:
        """Convenience: notify the user about an operation failure.

        Args:
            operation: Short label for the failed operation (e.g. "Clone").
            error: Description of what went wrong.
        """
        self.notify(
            f"{operation} Failed",
            str(error),
            icon="error",
        )

    # ------------------------------------------------------------------
    # Internal: toast via PowerShell
    # ------------------------------------------------------------------

    def _send_toast(self, title: str, message: str) -> bool:
        """Attempt to show a WinRT toast notification.

        Returns *True* if the PowerShell command exited successfully.
        """
        # Escape single quotes for PowerShell string embedding and strip
        # XML-unsafe characters so the inline XML stays well-formed.
        safe_title = self._escape_for_ps_xml(title)
        safe_message = self._escape_for_ps_xml(message)

        cmd = self._TOAST_PS_TEMPLATE.format(
            title=safe_title,
            message=safe_message,
            app_id=self._app_id,
        )

        stdout, stderr, rc = run_powershell(cmd)
        if rc != 0:
            _log.debug(
                "Toast PowerShell failed (rc=%d): %s", rc, stderr or stdout
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Internal: fallback via tkinter
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_notify(title: str, message: str, icon: str) -> None:
        """Show a simple tkinter messagebox as a last-resort notification.

        Runs in a daemon thread so it never blocks the caller.
        """

        def _show() -> None:
            try:
                import tkinter as tk
                from tkinter import messagebox

                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)

                if icon == "error":
                    messagebox.showerror(title, message, parent=root)
                elif icon == "warning":
                    messagebox.showwarning(title, message, parent=root)
                else:
                    messagebox.showinfo(title, message, parent=root)

                root.destroy()
            except Exception as exc:
                _log.debug("Tkinter fallback notification failed: %s", exc)

        t = threading.Thread(target=_show, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _escape_for_ps_xml(text: str) -> str:
        """Escape a string for safe embedding in PowerShell inline XML.

        Replaces XML-special characters *and* single quotes (which would
        break the outer PowerShell string literal).
        """
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        text = text.replace('"', "&quot;")
        text = text.replace("'", "&apos;")
        return text
