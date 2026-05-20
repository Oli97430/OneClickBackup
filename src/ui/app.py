"""Main application window for OneClick Backup & Disk Manager.

Provides the top-level window with a sidebar navigation, a swappable
content area (one page per feature), and a bottom status bar.
"""

from __future__ import annotations

import logging
import os
import threading
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from src.ui.widgets import COLORS, SidebarButton, StatusBar
from src.utils.i18n import t, set_language, get_language, get_languages

# ---------------------------------------------------------------------------
# Lazy imports for pages (avoids circular / heavy upfront loads)
# ---------------------------------------------------------------------------


def _import_dashboard():
    from src.ui.dashboard import DashboardPage
    return DashboardPage


def _import_pages():
    from src.ui.pages import (
        ClonePage,
        PartitionPage,
        ConversionPage,
        BackupPage,
        RecoveryPage,
        AdvancedPage,
        SchedulerPage,
        HistoryPage,
    )
    return {
        "clone": ClonePage,
        "partitions": PartitionPage,
        "convert": ConversionPage,
        "backup": BackupPage,
        "recovery": RecoveryPage,
        "advanced": AdvancedPage,
        "scheduler": SchedulerPage,
        "history": HistoryPage,
    }


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class OneClickBackupApp(ctk.CTk):
    """Root application window."""

    APP_NAME = "OneClick Backup & Disk Manager"
    APP_VERSION = "1.2.0"
    WINDOW_SIZE = "1280x800"
    MIN_SIZE = (1024, 600)
    _dark_mode: bool = True
    _current_theme: str = "dark"

    def __init__(self) -> None:
        super().__init__()

        # Window setup
        self.title(t("app.title"))
        self.geometry(self.WINDOW_SIZE)
        self.minsize(*self.MIN_SIZE)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.configure(fg_color=COLORS["bg_dark"])

        # Managers
        self._operation_manager = self._create_op_manager()
        self._backup_manager = self._create_backup_manager()

        # Page tracking
        self._current_page: str = "dashboard"
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._sidebar_buttons: dict[str, SidebarButton] = {}

        self._build_ui()

        # Warn user if managers failed to load
        if self._operation_manager is None:
            self.after(500, lambda: self._status_bar.set_status(
                "⚠ Some features unavailable (run as Administrator)"))

        self._show_page("dashboard")
        self._bind_global_shortcuts()
        self._start_usb_monitor()
        self._check_for_updates_async()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Manager creation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _create_op_manager():
        try:
            from src.core.operations import OperationManager
            return OperationManager()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Could not create OperationManager: %s", exc,
            )
            return None

    @staticmethod
    def _create_backup_manager():
        try:
            from src.core.backup import BackupManager
            return BackupManager()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Could not create BackupManager: %s", exc,
            )
            return None

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- Sidebar (left) ----
        self._sidebar = ctk.CTkFrame(
            self, fg_color=COLORS["bg_medium"], width=230, corner_radius=0,
        )
        self._sidebar.grid(row=0, column=0, sticky="ns", rowspan=2)
        self._sidebar.grid_propagate(False)
        self._sidebar.grid_columnconfigure(0, weight=1)

        # Sidebar right-edge accent line
        self._sidebar_accent = ctk.CTkFrame(
            self, width=1, fg_color=COLORS["border"], corner_radius=0,
        )
        self._sidebar_accent.grid(row=0, column=0, sticky="nse", rowspan=2)

        # Logo block — geometric display font
        self._logo_frame = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._logo_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(22, 28))
        ctk.CTkLabel(
            self._logo_frame, text="💾",
            font=ctk.CTkFont(size=28), text_color=COLORS["text_muted"],
        ).pack(pady=(0, 6))
        self._logo_label = ctk.CTkLabel(
            self._logo_frame, text=t("app.logo"),
            font=("Bahnschrift SemiBold", 16),
            text_color=COLORS["accent_blue"],
        )
        self._logo_label.pack()
        self._logo_sub_label = ctk.CTkLabel(
            self._logo_frame, text=t("app.logo_sub"),
            font=("Bahnschrift Light", 11),
            text_color=COLORS["text_muted"],
        )
        self._logo_sub_label.pack()

        # Navigation items
        self._nav_items = [
            ("dashboard", "📊"),
            ("clone", "📀"),
            ("partitions", "🔧"),
            ("backup", "💾"),
            ("convert", "🔄"),
            ("recovery", "🔍"),
            ("scheduler", "📅"),
            ("history", "📜"),
            ("advanced", "⚙️"),
        ]

        for idx, (page_id, icon) in enumerate(self._nav_items):
            btn = SidebarButton(
                self._sidebar,
                icon=icon,
                text=t(f"sidebar.{page_id}"),
                command=lambda p=page_id: self._show_page(p),
                is_active=(page_id == "dashboard"),
            )
            btn.grid(row=idx + 1, column=0, sticky="ew", padx=8, pady=2)
            self._sidebar_buttons[page_id] = btn

        # Spacer
        self._sidebar.grid_rowconfigure(len(self._nav_items) + 1, weight=1)

        # Language selector
        lang_frame = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        lang_frame.grid(
            row=len(self._nav_items) + 2, column=0, sticky="ew", padx=10, pady=(0, 5),
        )
        self._lang_label = ctk.CTkLabel(
            lang_frame, text=t("common.language"),
            font=("Bahnschrift", 10), text_color=COLORS["text_muted"],
        )
        self._lang_label.pack(anchor="w")

        languages = get_languages()
        lang_display = list(languages.values())
        lang_codes = list(languages.keys())
        current_display = languages.get(get_language(), lang_display[0])

        self._lang_codes = lang_codes
        self._lang_display = lang_display
        self._lang_selector = ctk.CTkOptionMenu(
            lang_frame,
            values=lang_display,
            command=self._on_language_change,
            width=170,
            height=28,
            font=("Bahnschrift", 11),
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["hover"],
            corner_radius=4,
        )
        self._lang_selector.set(current_display)
        self._lang_selector.pack(pady=(4, 0))

        # Admin indicator
        self._admin_frame = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._admin_frame.grid(
            row=len(self._nav_items) + 3, column=0, sticky="ew", padx=10, pady=(5, 10),
        )

        try:
            from src.utils.admin import is_admin
            self._is_admin = is_admin()
        except Exception:
            self._is_admin = False

        status_text = t("admin.ok") if self._is_admin else t("admin.limited")
        status_color = COLORS["accent_green"] if self._is_admin else COLORS["accent_yellow"]
        self._admin_status_label = ctk.CTkLabel(
            self._admin_frame, text=status_text,
            font=("Consolas", 10), text_color=status_color,
        )
        self._admin_status_label.pack(pady=5)

        self._elevate_btn: Optional[ctk.CTkButton] = None
        if not self._is_admin:
            self._elevate_btn = ctk.CTkButton(
                self._admin_frame, text=t("admin.elevate"), width=130, height=28,
                font=("Bahnschrift", 11),
                fg_color=COLORS["accent_orange"],
                hover_color=COLORS["accent_yellow"],
                corner_radius=4,
                command=self._elevate,
            )
            self._elevate_btn.pack()

        # Theme toggle button
        self._theme_btn = ctk.CTkButton(
            self._sidebar, text="🌙 Dark" if self._dark_mode else "☀️ Light",
            width=130, height=28,
            font=("Bahnschrift", 11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["hover"],
            corner_radius=4,
            command=self._toggle_theme,
        )
        self._theme_btn.grid(row=len(self._nav_items) + 4, column=0, padx=10, pady=(5, 5))

        # Tray button
        self._tray_btn = ctk.CTkButton(
            self._sidebar, text="🔽 Minimize to Tray",
            width=130, height=28,
            font=("Bahnschrift", 11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["hover"],
            corner_radius=4,
            command=self._minimize_to_tray,
        )
        self._tray_btn.grid(row=len(self._nav_items) + 5, column=0, padx=10, pady=(0, 5))

        # Version label — monospace for technical feel
        ctk.CTkLabel(
            self._sidebar, text=f"v{self.APP_VERSION}",
            font=("Consolas", 9), text_color=COLORS["text_muted"],
        ).grid(row=len(self._nav_items) + 6, column=0, pady=(0, 12))

        # ---- Content area (centre) ----
        self._content = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=0)
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        # ---- Status bar (bottom) ----
        self._status_bar = StatusBar(self)
        self._status_bar.grid(row=1, column=1, sticky="ew")

    # ------------------------------------------------------------------
    # Page management
    # ------------------------------------------------------------------

    def _get_page_title(self, name: str) -> str:
        return t(f"page.{name}")

    def _create_page(self, name: str) -> ctk.CTkFrame:
        """Instantiate a page widget by *name*."""
        try:
            if name == "dashboard":
                cls = _import_dashboard()
                return cls(self._content, on_operation_requested=self._on_operation_requested)
            page_classes = _import_pages()
            cls = page_classes.get(name)
            if cls is None:
                raise KeyError(name)
            kwargs: dict = {}
            if name in ("clone", "partitions", "convert", "advanced"):
                kwargs["operation_manager"] = self._operation_manager
            if name in ("clone", "backup", "advanced", "scheduler"):
                kwargs["backup_manager"] = self._backup_manager
            return cls(self._content, **kwargs)
        except Exception as exc:
            frame = ctk.CTkFrame(self._content, fg_color="transparent")
            ctk.CTkLabel(
                frame,
                text=f"Error loading page '{name}':\n{exc}",
                text_color=COLORS["accent_red"],
                font=ctk.CTkFont(size=14),
                wraplength=500,
            ).pack(pady=50)
            return frame

    def _show_page(self, name: str) -> None:
        # Update sidebar highlight
        for key, btn in self._sidebar_buttons.items():
            btn.set_active(key == name)

        # Hide all pages
        for pg in self._pages.values():
            pg.grid_forget()

        # Lazy-create
        if name not in self._pages:
            self._pages[name] = self._create_page(name)

        self._pages[name].grid(row=0, column=0, sticky="nsew")
        self._current_page = name
        self._status_bar.set_status(self._get_page_title(name))

    # ------------------------------------------------------------------
    # Language switching
    # ------------------------------------------------------------------

    def _on_language_change(self, display_name: str) -> None:
        """Handle language selection from the sidebar option menu."""
        try:
            idx = self._lang_display.index(display_name)
        except ValueError:
            return
        code = self._lang_codes[idx]
        if code == get_language():
            return
        set_language(code)
        # Destroy cached pages so they are rebuilt with new strings.
        # destroy() also cancels any pending after() callbacks on each page,
        # preventing stale callbacks from firing on destroyed widgets.
        for pg in list(self._pages.values()):
            pg.destroy()
        self._pages.clear()
        self._refresh_ui()
        # Re-show the current page (forces lazy re-creation)
        self._show_page(self._current_page)

    def _refresh_ui(self) -> None:
        """Update all translatable text in the sidebar and window title."""
        self.title(t("app.title"))
        # Re-apply fonts to handle language changes (some fonts may not support all scripts)
        self._logo_label.configure(text=t("app.logo"), font=("Bahnschrift SemiBold", 16))
        self._logo_sub_label.configure(text=t("app.logo_sub"), font=("Bahnschrift Light", 11))

        # RTL support: adjust text alignment for Arabic
        is_rtl = get_language() == "ar"
        anchor = "e" if is_rtl else "w"
        justify = "right" if is_rtl else "left"

        self._logo_label.configure(anchor=anchor, justify=justify)
        self._logo_sub_label.configure(anchor=anchor, justify=justify)

        # Sidebar navigation buttons
        for page_id, _icon in self._nav_items:
            btn = self._sidebar_buttons.get(page_id)
            if btn is not None:
                btn.set_text(t(f"sidebar.{page_id}"))

        # Language label
        self._lang_label.configure(text=t("common.language"), anchor=anchor)

        # Admin status
        status_text = t("admin.ok") if self._is_admin else t("admin.limited")
        self._admin_status_label.configure(text=status_text, anchor=anchor)
        if self._elevate_btn is not None:
            self._elevate_btn.configure(text=t("admin.elevate"))

    # ------------------------------------------------------------------
    # Global keyboard shortcuts
    # ------------------------------------------------------------------

    def _bind_global_shortcuts(self) -> None:
        """Bind application-wide keyboard shortcuts for navigation and actions."""
        # Page order matches self._nav_items
        _page_keys = [
            ("dashboard", "1"),
            ("clone", "2"),
            ("partitions", "3"),
            ("backup", "4"),
            ("convert", "5"),
            ("recovery", "6"),
            ("scheduler", "7"),
            ("history", "8"),
            ("advanced", "9"),
        ]
        for page_id, key in _page_keys:
            self.bind_all(
                f"<Control-Key-{key}>",
                lambda e, p=page_id: self._show_page(p),
            )

        self.bind_all("<Control-Key-r>", lambda e: self._refresh_current())
        self.bind_all("<Control-Key-R>", lambda e: self._refresh_current())
        self.bind_all("<F5>", lambda e: self._refresh_current())
        self.bind_all("<Control-Key-q>", lambda e: self._on_close())
        self.bind_all("<Control-Key-Q>", lambda e: self._on_close())

    def _refresh_current(self) -> None:
        """Refresh the currently displayed page if it supports refreshing."""
        page = self._pages.get(self._current_page)
        if page is None:
            return
        # Try common refresh method names used across page classes
        for method_name in ("_refresh_disks", "_refresh", "refresh"):
            method = getattr(page, method_name, None)
            if callable(method):
                method()
                self._status_bar.set_status(
                    f"{self._get_page_title(self._current_page)} — refreshed"
                )
                return

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_operation_requested(self, action: str, disk_index: int, partition_info) -> None:
        if action in ("resize", "format", "delete", "create", "merge", "change_letter"):
            self._show_page("partitions")
        elif action in ("clone", "migrate"):
            self._show_page("clone")
        elif action in ("backup", "restore"):
            self._show_page("backup")

    def _elevate(self) -> None:
        try:
            from src.utils.admin import run_as_admin
            run_as_admin()
        except Exception as exc:
            messagebox.showerror("Elevation failed", str(exc))

    # ------------------------------------------------------------------
    # Theme toggle (light/dark)
    # ------------------------------------------------------------------

    _THEME_CYCLE = ["dark", "light", "high_contrast"]

    def _toggle_theme(self) -> None:
        """Cycle through dark → light → high contrast themes."""
        if self._dark_mode is True:
            # dark → light
            self._dark_mode = False
            self._current_theme = "light"
        elif self._current_theme == "light":
            # light → high contrast
            self._dark_mode = True
            self._current_theme = "high_contrast"
        else:
            # high contrast → dark
            self._dark_mode = True
            self._current_theme = "dark"

        mode = "dark" if self._dark_mode else "light"
        ctk.set_appearance_mode(mode)

        icons = {"dark": "🌙 Dark", "light": "☀️ Light", "high_contrast": "🔲 HiContrast"}
        self._theme_btn.configure(text=icons.get(self._current_theme, "🌙 Dark"))

        # Rebuild pages with new colors — destroy() cancels pending after() IDs
        for pg in list(self._pages.values()):
            pg.destroy()
        self._pages.clear()
        self._show_page(self._current_page)
        self._status_bar.set_status(f"Theme: {self._current_theme.replace('_', ' ').title()}")

    # ------------------------------------------------------------------
    # USB device monitoring
    # ------------------------------------------------------------------

    def _start_usb_monitor(self) -> None:
        """Start a background thread that watches for USB drive insertions."""
        self._known_drives: set[str] = set()
        try:
            import psutil
            for p in psutil.disk_partitions():
                self._known_drives.add(p.mountpoint)
        except Exception:
            pass
        self._usb_poll_id = self.after(5000, self._poll_usb)

    def _poll_usb(self) -> None:
        """Check for new USB drives every 5 seconds."""
        try:
            import psutil
            current = {p.mountpoint for p in psutil.disk_partitions()}
            new_drives = current - self._known_drives
            if new_drives:
                for drive in new_drives:
                    self._status_bar.set_status(
                        f"USB drive detected: {drive}"
                    )
            self._known_drives = current
        except Exception:
            pass
        self._usb_poll_id = self.after(5000, self._poll_usb)

    # ------------------------------------------------------------------
    # Auto-update check
    # ------------------------------------------------------------------

    def _check_for_updates_async(self) -> None:
        """Check for updates in the background on startup."""
        def _bg():
            try:
                from src.core.updater import AutoUpdater
                updater = AutoUpdater(self.APP_VERSION)
                info = updater.check_for_update()
                if info.is_update_available:
                    self.after(0, lambda: self._notify_update(info))
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

    def _notify_update(self, info) -> None:
        """Show update notification in status bar."""
        self._status_bar.set_status(
            f"Update available: {info.latest_version} (current: {info.current_version})"
        )

    # ------------------------------------------------------------------
    # Portable mode
    # ------------------------------------------------------------------

    @staticmethod
    def is_portable() -> bool:
        """Check if running in portable mode (settings file next to EXE)."""
        exe_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.isfile(os.path.join(exe_dir, ".portable"))

    # ------------------------------------------------------------------
    # Tray icon (minimize to system tray)
    # ------------------------------------------------------------------

    def _minimize_to_tray(self) -> None:
        """Minimize window to system tray (if pystray is available)."""
        try:
            import pystray
            from PIL import Image
            icon_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "assets", "logo.png"
            )
            if os.path.isfile(icon_path):
                image = Image.open(icon_path).resize((64, 64))
            else:
                image = Image.new("RGB", (64, 64), "#6366f1")

            def on_open(icon, item):
                icon.stop()
                self.after(0, self.deiconify)

            def on_quit(icon, item):
                icon.stop()
                self.after(0, self.destroy)

            menu = pystray.Menu(
                pystray.MenuItem("Open", on_open, default=True),
                pystray.MenuItem("Quit", on_quit),
            )
            self.withdraw()
            icon = pystray.Icon("OneClickBackup", image, "OneClickBackup", menu)
            threading.Thread(target=icon.run, daemon=True).start()
        except ImportError:
            # pystray not available — just minimize normally
            self.iconify()

    def _on_close(self) -> None:
        if self._operation_manager:
            pending = self._operation_manager.get_pending()
            if pending and not messagebox.askyesno(
                t("confirm.pending_title"),
                t("confirm.pending_exit", n=len(pending)),
            ):
                return
        self.destroy()
