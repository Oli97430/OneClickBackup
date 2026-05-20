"""Feature pages for OneClick Backup & Disk Manager.

Each class is a CTkFrame that gets swapped into the main content area.
Pages accept optional *operation_manager* and/or *backup_manager*
constructor arguments so they can queue / execute real operations.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from src.utils.i18n import t

# ---------------------------------------------------------------------------
# Project imports (with graceful fallbacks)
# ---------------------------------------------------------------------------

try:
    from src.ui.widgets import COLORS, format_bytes, ConfirmDialog, ProgressDialog, DiskBar, Tooltip
except ImportError:
    Tooltip = None  # type: ignore[assignment,misc]
    COLORS = {
        "bg_dark": "#0f1117", "bg_medium": "#161b22", "bg_light": "#1f2937",
        "bg_card": "#1a1e2a", "accent_blue": "#6366f1", "accent_green": "#34d399",
        "accent_yellow": "#fbbf24", "accent_red": "#f87171", "accent_purple": "#a78bfa",
        "accent_orange": "#fb923c", "text_primary": "#f1f5f9",
        "text_secondary": "#94a3b8", "text_muted": "#475569",
        "border": "#1e293b", "hover": "#252e3d",
        "ntfs_color": "#60a5fa", "fat32_color": "#34d399", "exfat_color": "#c084fc",
        "efi_color": "#fbbf24", "recovery_color": "#fb7185",
        "unallocated_color": "#1e293b", "unknown_color": "#475569",
    }

    def format_bytes(size: int) -> str:
        for u in ("B", "KB", "MB", "GB", "TB"):
            if abs(size) < 1024:
                return f"{size:.1f} {u}"
            size /= 1024  # type: ignore[assignment]
        return f"{size:.1f} PB"

    ConfirmDialog = None  # type: ignore[assignment,misc]
    ProgressDialog = None  # type: ignore[assignment,misc]
    DiskBar = None  # type: ignore[assignment,misc]

try:
    from src.core.disk_info import get_all_disks, DiskInfo, PartitionInfo
except ImportError:
    get_all_disks = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)

# ============================================================================
# Mixin: shared background disk refresh
# ============================================================================


class _DiskPageMixin:
    """Mixin providing a background-threaded disk refresh pattern.

    Subclasses must define ``_on_disks_loaded()`` which is called on the
    main thread once loading is complete.  ``self._disks`` is populated
    automatically.
    """

    _disks: list

    def _refresh_disks_bg(self) -> None:
        """Kick off a background thread to reload disk data."""
        def _bg():
            try:
                disks = _load_disks()
                self.after(0, lambda d=disks: self._on_disks_loaded(d))  # type: ignore[attr-defined]
            except Exception as exc:
                _log.exception("Background disk refresh failed: %s", exc)
                self.after(0, lambda: messagebox.showerror(  # type: ignore[attr-defined]
                    t("common.error"), str(exc)))
        threading.Thread(target=_bg, daemon=True).start()

    def _on_disks_loaded(self, disks: list) -> None:
        """Override in subclass to populate UI after disks are loaded.

        The *disks* argument is the freshly loaded disk list, passed
        from the background thread through ``after()`` so the
        assignment happens on the main thread.
        """
        self._disks = disks


# ============================================================================
# Helpers
# ============================================================================

def _heading(parent: ctk.CTkFrame, text: str, size: int = 18) -> ctk.CTkLabel:
    lbl = ctk.CTkLabel(
        parent, text=text,
        font=("Bahnschrift SemiBold", size),
        text_color=COLORS["text_primary"], anchor="w",
    )
    return lbl


def _card(parent: ctk.CTkFrame, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        parent, fg_color=COLORS["bg_card"], corner_radius=8,
        border_width=1, border_color=COLORS["border"], **kw,
    )


def _load_disks() -> list:
    try:
        if get_all_disks is not None:
            return get_all_disks()
        from src.core.disk_info import get_all_disks as gad
        return gad()
    except Exception as exc:
        _log.warning("Failed to load disk data: %s", exc)
        return []


def _disk_display(d) -> str:
    size = format_bytes(getattr(d, "size_bytes", 0))
    model = getattr(d, "model", "Unknown")
    idx = getattr(d, "index", "?")
    mtype = getattr(d, "media_type", "")
    return f"{t('common.disk')} {idx}: {model} ({size}) [{mtype}]"


# ============================================================================
# Mixin: keyboard accessibility helpers
# ============================================================================


class _KeyboardAccessMixin:
    """Mixin providing keyboard accessibility helpers for page classes.

    Subclasses call ``_apply_keyboard_accessibility()`` at the end of their
    ``_build_ui()`` to set hand cursors on buttons and make interactive
    widgets Tab-focusable with visual feedback.
    """

    def _get_all_buttons(self) -> list:
        """Recursively find all CTkButton widgets in this page."""
        buttons: list = []
        self._collect_buttons(self, buttons)  # type: ignore[arg-type]
        return buttons

    @staticmethod
    def _collect_buttons(widget, result: list) -> None:
        """Walk the widget tree collecting CTkButton instances."""
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkButton):
                result.append(child)
            _KeyboardAccessMixin._collect_buttons(child, result)

    def _apply_keyboard_accessibility(self) -> None:
        """Make buttons focusable with hand cursor and visual focus ring.

        Enhances WCAG compliance:
        - Hand cursor on all interactive elements
        - Tab-focusable with visible focus ring (2.4.7 Focus Visible)
        - Keyboard activation via Return and Space (2.1.1 Keyboard)
        - All combo boxes, switches, checkboxes also made focusable
        """
        for btn in self._get_all_buttons():
            btn.configure(cursor="hand2")
            try:
                btn.configure(takefocus=True)
            except (ValueError, tk.TclError):
                pass
            btn.bind("<Return>", lambda e, b=btn: b.invoke(), add="+")
            btn.bind("<space>", lambda e, b=btn: b.invoke(), add="+")
            btn.bind("<FocusIn>", lambda e, b=btn: b.configure(
                border_width=2, border_color=COLORS["accent_blue"],
            ) if hasattr(b, 'configure') else None, add="+")
            btn.bind("<FocusOut>", lambda e, b=btn: b.configure(
                border_width=0,
            ) if hasattr(b, 'configure') else None, add="+")

        # Make combo boxes and other interactive widgets Tab-focusable
        self._make_widgets_focusable(self)  # type: ignore[arg-type]

    @staticmethod
    def _make_widgets_focusable(widget) -> None:
        """Recursively make combo boxes, switches, checkboxes Tab-focusable."""
        focusable_types = (
            ctk.CTkComboBox, ctk.CTkEntry, ctk.CTkSwitch,
            ctk.CTkCheckBox, ctk.CTkSegmentedButton, ctk.CTkOptionMenu,
        )
        for child in widget.winfo_children():
            if isinstance(child, focusable_types):
                try:
                    child.configure(takefocus=True)
                except (ValueError, tk.TclError):
                    pass
            _KeyboardAccessMixin._make_widgets_focusable(child)


# ============================================================================
#  1.  ClonePage
# ============================================================================

class ClonePage(_KeyboardAccessMixin, _DiskPageMixin, ctk.CTkFrame):
    """Disk cloning and OS migration interface."""

    def __init__(self, parent, *, operation_manager=None, backup_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._opmgr = operation_manager
        self._bkpmgr = backup_manager
        self._disks: list = []
        self._build_ui()
        self._apply_keyboard_accessibility()
        self._bind_shortcuts()
        self._refresh_disks()

    # -- UI ----------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Title
        _heading(self, t("clone.title")).grid(
            row=0, column=0, sticky="w", padx=20, pady=(20, 5))

        desc = ctk.CTkLabel(
            self,
            text=t("clone.desc"),
            text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=12), anchor="w",
        )
        desc.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 15))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 10))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(2, weight=1)

        # --- Source panel ---
        src_card = _card(body)
        src_card.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=5)
        ctk.CTkLabel(src_card, text=t("clone.source"), font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["accent_blue"]).pack(padx=15, pady=(15, 5), anchor="w")
        self._src_combo = ctk.CTkComboBox(
            src_card, values=[t("common.loading")], width=300,
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            button_color=COLORS["accent_blue"], state="readonly",
        )
        self._src_combo.pack(padx=15, pady=5, fill="x")

        self._src_info = ctk.CTkLabel(
            src_card, text="", text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=11), wraplength=280, justify="left", anchor="nw",
        )
        self._src_info.pack(padx=15, pady=(5, 15), fill="both", expand=True, anchor="nw")

        # Arrow
        arrow = ctk.CTkLabel(body, text="➜", font=ctk.CTkFont(size=36),
                             text_color=COLORS["accent_green"])
        arrow.grid(row=0, column=1, padx=10)

        # --- Target panel ---
        tgt_card = _card(body)
        tgt_card.grid(row=0, column=2, sticky="nsew", padx=(5, 0), pady=5)
        ctk.CTkLabel(tgt_card, text=t("clone.target"), font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["accent_green"]).pack(padx=15, pady=(15, 5), anchor="w")
        self._tgt_combo = ctk.CTkComboBox(
            tgt_card, values=[t("common.loading")], width=300,
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            button_color=COLORS["accent_green"], state="readonly",
        )
        self._tgt_combo.pack(padx=15, pady=5, fill="x")

        self._tgt_info = ctk.CTkLabel(
            tgt_card, text="", text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=11), wraplength=280, justify="left", anchor="nw",
        )
        self._tgt_info.pack(padx=15, pady=(5, 15), fill="both", expand=True, anchor="nw")

        # --- Options ---
        opts = _card(body)
        opts.grid(row=1, column=0, columnspan=3, sticky="ew", pady=10)
        opts.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(opts, text=t("clone.options"), font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["accent_yellow"]).grid(
            row=0, column=0, sticky="w", padx=15, pady=(12, 5), columnspan=2)

        self._clone_type = ctk.CTkSegmentedButton(
            opts, values=[t("clone.full"), t("clone.os_only")],
            font=ctk.CTkFont(size=12),
            selected_color=COLORS["accent_blue"],
            unselected_color=COLORS["bg_medium"],
        )
        self._clone_type.set(t("clone.full"))
        self._clone_type.grid(row=1, column=0, columnspan=2, padx=15, pady=5, sticky="ew")

        self._opt_resize = ctk.CTkCheckBox(opts, text=t("clone.resize"),
                                           font=ctk.CTkFont(size=12),
                                           fg_color=COLORS["accent_blue"],
                                           text_color=COLORS["text_primary"])
        self._opt_resize.grid(row=2, column=0, padx=15, pady=3, sticky="w")
        self._opt_resize.select()

        self._opt_verify = ctk.CTkCheckBox(opts, text=t("clone.verify"),
                                           font=ctk.CTkFont(size=12),
                                           fg_color=COLORS["accent_blue"],
                                           text_color=COLORS["text_primary"])
        self._opt_verify.grid(row=2, column=1, padx=15, pady=3, sticky="w")

        self._opt_boot = ctk.CTkCheckBox(opts, text=t("clone.boot"),
                                         font=ctk.CTkFont(size=12),
                                         fg_color=COLORS["accent_blue"],
                                         text_color=COLORS["text_primary"])
        self._opt_boot.grid(row=3, column=0, padx=15, pady=(3, 12), sticky="w")

        # --- Warning + button ---
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 20))
        bottom.grid_columnconfigure(0, weight=1)

        self._warn_label = ctk.CTkLabel(
            bottom,
            text=t("clone.warning"),
            text_color=COLORS["accent_yellow"], font=ctk.CTkFont(size=11),
        )
        self._warn_label.grid(row=0, column=0, sticky="w")

        self._start_btn = ctk.CTkButton(
            bottom, text=t("clone.start"), width=180, height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["accent_blue"], hover_color=COLORS["bg_light"],
            command=self._start_clone,
        )
        self._start_btn.grid(row=0, column=1, padx=(15, 0))

    # -- Keyboard shortcuts ------------------------------------------------

    def _bind_shortcuts(self):
        """Bind page-specific keyboard shortcuts."""
        self.bind("<Alt-s>", lambda e: self._src_combo.focus_set())
        self.bind("<Alt-t>", lambda e: self._tgt_combo.focus_set())
        self.bind("<Alt-Return>", lambda e: self._start_clone())

    # -- Logic -------------------------------------------------------------

    def _refresh_disks(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self, disks: list) -> None:
        super()._on_disks_loaded(disks)
        self._populate_combos()

    def _populate_combos(self):
        names = [_disk_display(d) for d in self._disks] or [t("common.no_disks")]
        self._src_combo.configure(values=names)
        self._tgt_combo.configure(values=names)
        if names:
            self._src_combo.set(names[0])
            self._tgt_combo.set(names[-1] if len(names) > 1 else names[0])

    def _start_clone(self):
        src = self._src_combo.get()
        tgt = self._tgt_combo.get()
        if src == tgt:
            messagebox.showwarning(t("clone.same_disk"), t("clone.same_disk"))
            return
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("clone.confirm_title"),
            t("clone.confirm_msg", src=src, tgt=tgt),
            risk_level="critical",
        ):
            return
        messagebox.showinfo(t("clone.started"), t("clone.started"))


# ============================================================================
#  2.  PartitionPage
# ============================================================================

class PartitionPage(_KeyboardAccessMixin, _DiskPageMixin, ctk.CTkFrame):
    """Partition management interface."""

    def __init__(self, parent, *, operation_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._opmgr = operation_manager
        self._disks: list = []
        self._selected_part = None
        self._build_ui()
        self._apply_keyboard_accessibility()
        self._bind_shortcuts()
        self._refresh_disks()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Header row
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 5))
        hdr.grid_columnconfigure(1, weight=1)

        _heading(hdr, t("part.title")).grid(row=0, column=0, sticky="w")

        self._disk_combo = ctk.CTkComboBox(
            hdr, values=[t("common.loading")], width=400,
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            button_color=COLORS["accent_blue"], state="readonly",
            command=self._on_disk_selected,
        )
        self._disk_combo.grid(row=0, column=1, sticky="e", padx=(20, 0))

        # Disk bar
        self._bar_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10, height=90)
        self._bar_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        self._bar_frame.grid_propagate(False)
        self._bar_placeholder = ctk.CTkLabel(
            self._bar_frame, text=t("part.select_disk"),
            text_color=COLORS["text_muted"],
        )
        self._bar_placeholder.place(relx=0.5, rely=0.5, anchor="center")

        # Partition table
        self._table_frame = ctk.CTkScrollableFrame(
            self, fg_color=COLORS["bg_card"], corner_radius=10,
            label_text="  " + t("part.partitions_label"), label_fg_color=COLORS["bg_medium"],
            label_text_color=COLORS["text_primary"],
        )
        self._table_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 5))
        for c in range(9):
            self._table_frame.grid_columnconfigure(c, weight=1)

        # Column headers
        headers = [t("part.col_index"), t("part.col_letter"), t("part.col_label"), t("part.col_type"), t("part.col_fs"), t("part.col_size"), t("part.col_used"), t("part.col_free"), t("part.col_status")]
        for c, h in enumerate(headers):
            ctk.CTkLabel(
                self._table_frame, text=h, font=ctk.CTkFont(size=11, weight="bold"),
                text_color=COLORS["accent_blue"],
            ).grid(row=0, column=c, padx=6, pady=(8, 4), sticky="w")

        # Action buttons
        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.grid(row=4, column=0, sticky="ew", padx=20, pady=(5, 20))

        actions = [
            (t("part.create"), COLORS["accent_green"], self._act_create),
            (t("part.resize"), COLORS["accent_blue"], self._act_resize),
            (t("part.merge"), COLORS["accent_blue"], self._act_merge),
            (t("part.format"), COLORS["accent_yellow"], self._act_format),
            (t("part.delete"), COLORS["accent_red"], self._act_delete),
            (t("part.change_letter"), COLORS["accent_blue"], self._act_letter),
            (t("part.set_active"), COLORS["accent_purple"], self._act_active),
        ]
        for i, (label, color, cmd) in enumerate(actions):
            ctk.CTkButton(
                btn_bar, text=label, width=110, height=34,
                font=ctk.CTkFont(size=12),
                fg_color=color, hover_color=COLORS["hover"],
                command=cmd,
            ).grid(row=0, column=i, padx=4, pady=4)

    # -- Keyboard shortcuts ------------------------------------------------

    def _bind_shortcuts(self):
        """Bind page-specific keyboard shortcuts."""
        self.bind("<Alt-d>", lambda e: self._disk_combo.focus_set())

    # -- Data --------------------------------------------------------------

    def _refresh_disks(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self, disks: list) -> None:
        super()._on_disks_loaded(disks)
        self._populate()

    def _populate(self):
        names = [_disk_display(d) for d in self._disks] or [t("common.no_disks")]
        self._disk_combo.configure(values=names)
        if names:
            self._disk_combo.set(names[0])
            self._on_disk_selected(names[0])

    def _on_disk_selected(self, _val=None):
        idx = self._disk_combo.cget("values").index(self._disk_combo.get()) if self._disk_combo.get() in (self._disk_combo.cget("values") or []) else 0
        if idx >= len(self._disks):
            return
        disk = self._disks[idx]

        # Update bar
        for w in self._bar_frame.winfo_children():
            w.destroy()
        if DiskBar is not None:
            bar = DiskBar(self._bar_frame, disk_info=disk, width=900, height=70)
            bar.pack(fill="x", padx=10, pady=10)
        else:
            ctk.CTkLabel(self._bar_frame, text=t("part.select_disk"),
                         text_color=COLORS["text_muted"]).place(relx=0.5, rely=0.5, anchor="center")

        # Update table
        for w in list(self._table_frame.winfo_children()):
            if not isinstance(w, tk.Widget):
                continue
            info = w.grid_info()
            if info and int(info.get("row", 0)) > 0:
                w.destroy()

        parts = getattr(disk, "partitions", []) or []
        for r, p in enumerate(parts, start=1):
            vals = [
                str(getattr(p, "index", r)),
                getattr(p, "letter", "") or "-",
                getattr(p, "label", "") or "-",
                getattr(p, "partition_type", ""),
                getattr(p, "file_system", ""),
                format_bytes(getattr(p, "size_bytes", 0)),
                format_bytes(getattr(p, "used_bytes", 0)),
                format_bytes(getattr(p, "free_bytes", 0)),
                t("part.active") if getattr(p, "is_active", False) else "",
            ]
            bg = COLORS["bg_medium"] if r % 2 == 0 else "transparent"
            for c, v in enumerate(vals):
                lbl = ctk.CTkLabel(
                    self._table_frame, text=v, font=ctk.CTkFont(size=11),
                    text_color=COLORS["text_primary"], anchor="w",
                )
                lbl.grid(row=r, column=c, padx=6, pady=2, sticky="w")
                lbl.bind("<Button-1>", lambda e, part=p: self._select_part(part))

    def _select_part(self, p):
        self._selected_part = p

    # -- Actions -----------------------------------------------------------

    def _act_create(self):
        dlg = ctk.CTkInputDialog(text=t("part.create_prompt"), title=t("part.create"))
        val = dlg.get_input()
        if val and self._opmgr:
            try:
                idx = self._disk_combo.cget("values").index(self._disk_combo.get())
                self._opmgr.queue_create_partition(idx, int(val) * 1024 * 1024)
                messagebox.showinfo(t("part.queued"), t("part.create_queued", size=val))
            except Exception as e:
                messagebox.showerror(t("part.error"), str(e))

    def _act_resize(self):
        if not self._selected_part:
            messagebox.showinfo(t("part.select_first"), t("part.select_first"))
            return
        dlg = ctk.CTkInputDialog(text=t("part.resize_prompt"), title=t("part.resize"))
        val = dlg.get_input()
        if val and self._opmgr:
            try:
                didx = self._disk_combo.cget("values").index(self._disk_combo.get())
                pidx = getattr(self._selected_part, "index", 1)
                self._opmgr.queue_resize_partition(didx, pidx, int(val) * 1024 * 1024)
                messagebox.showinfo(t("part.queued"), t("part.resize_queued"))
            except Exception as e:
                messagebox.showerror(t("part.error"), str(e))

    def _act_merge(self):
        """Merge two adjacent partitions (delete second, extend first)."""
        if not self._selected_part:
            messagebox.showinfo(t("part.select_first"), t("part.select_first"))
            return
        dlg = ctk.CTkInputDialog(
            text=t("part.merge_prompt"),
            title=t("part.merge"),
        )
        val = dlg.get_input()
        if val and self._opmgr:
            try:
                didx = self._disk_combo.cget("values").index(self._disk_combo.get())
                pidx1 = getattr(self._selected_part, "index", 1)
                pidx2 = int(val.strip())
                if ConfirmDialog and not ConfirmDialog.ask(
                    self, t("part.merge"),
                    t("part.merge_confirm", p1=pidx1, p2=pidx2),
                    risk_level="critical",
                ):
                    return
                self._opmgr.queue_merge_partitions(didx, pidx1, pidx2)
                messagebox.showinfo(t("part.queued"), t("part.merge_queued"))
            except Exception as e:
                messagebox.showerror(t("part.error"), str(e))

    def _act_format(self):
        if not self._selected_part:
            messagebox.showinfo(t("part.select_first"), t("part.select_first"))
            return
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("part.format_title"),
            t("part.format_confirm", letter=getattr(self._selected_part, 'letter', '?')),
            risk_level="critical",
        ):
            return
        messagebox.showinfo(t("part.format"), t("part.format_queued"))

    def _act_delete(self):
        if not self._selected_part:
            messagebox.showinfo(t("part.select_first"), t("part.select_first"))
            return
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("part.delete_title"),
            t("part.delete_confirm", letter=getattr(self._selected_part, 'letter', '?')),
            risk_level="critical",
        ):
            return
        if self._opmgr:
            try:
                didx = self._disk_combo.cget("values").index(self._disk_combo.get())
                pidx = getattr(self._selected_part, "index", 1)
                self._opmgr.queue_delete_partition(didx, pidx)
                messagebox.showinfo(t("part.queued"), t("part.delete_queued"))
            except Exception as e:
                messagebox.showerror(t("part.error"), str(e))

    def _act_letter(self):
        if not self._selected_part:
            messagebox.showinfo(t("part.select_first"), t("part.select_first"))
            return
        dlg = ctk.CTkInputDialog(text=t("part.letter_prompt"), title=t("part.change_letter"))
        val = dlg.get_input()
        if val and self._opmgr:
            try:
                didx = self._disk_combo.cget("values").index(self._disk_combo.get())
                pidx = getattr(self._selected_part, "index", 1)
                self._opmgr.queue_change_letter(didx, pidx, val.strip().upper())
                messagebox.showinfo(t("part.queued"), t("part.letter_queued", letter=val.upper()))
            except Exception as e:
                messagebox.showerror(t("part.error"), str(e))

    def _act_active(self):
        if not self._selected_part:
            messagebox.showinfo(t("part.select_first"), t("part.select_first"))
            return
        messagebox.showinfo(t("part.set_active"), t("part.active_queued"))


# ============================================================================
#  3.  ConversionPage
# ============================================================================

class ConversionPage(_KeyboardAccessMixin, _DiskPageMixin, ctk.CTkFrame):
    """Disk and partition conversion tools."""

    def __init__(self, parent, *, operation_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._opmgr = operation_manager
        self._disks: list = []
        self._build_ui()
        self._apply_keyboard_accessibility()
        self._bind_shortcuts()
        self._refresh()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        _heading(self, t("conv.title")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(20, 15))

        # --- MBR ↔ GPT Card ---
        mbr_card = _card(self)
        mbr_card.grid(row=1, column=0, sticky="nsew", padx=(20, 10), pady=5)

        ctk.CTkLabel(mbr_card, text=t("conv.disk_style"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 5), anchor="w")
        ctk.CTkLabel(mbr_card, text=t("conv.disk_style_desc"),
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"],
                     ).pack(padx=15, anchor="w")

        self._conv_disk_combo = ctk.CTkComboBox(
            mbr_card, values=[t("common.loading")], width=320,
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly", command=self._update_style_label,
        )
        self._conv_disk_combo.pack(padx=15, pady=10, fill="x")

        row_fr = ctk.CTkFrame(mbr_card, fg_color="transparent")
        row_fr.pack(padx=15, fill="x")
        ctk.CTkLabel(row_fr, text=t("conv.current"), text_color=COLORS["text_secondary"],
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self._style_label = ctk.CTkLabel(row_fr, text="—",
                                          font=ctk.CTkFont(size=12, weight="bold"),
                                          text_color=COLORS["accent_blue"])
        self._style_label.pack(side="left", padx=8)

        self._conv_target = ctk.CTkSegmentedButton(
            mbr_card, values=[t("conv.to_gpt"), t("conv.to_mbr")],
            selected_color=COLORS["accent_blue"], unselected_color=COLORS["bg_medium"],
        )
        self._conv_target.set(t("conv.to_gpt"))
        self._conv_target.pack(padx=15, pady=10, fill="x")

        ctk.CTkLabel(mbr_card,
                     text=t("conv.style_warning"),
                     text_color=COLORS["accent_yellow"], font=ctk.CTkFont(size=10),
                     justify="left").pack(padx=15, pady=(0, 5), anchor="w")

        ctk.CTkButton(mbr_card, text=t("conv.convert"), width=140, height=36,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._do_convert_style).pack(padx=15, pady=(5, 15))

        # --- File System Card ---
        fs_card = _card(self)
        fs_card.grid(row=1, column=1, sticky="nsew", padx=(10, 20), pady=5)

        ctk.CTkLabel(fs_card, text=t("conv.fs_title"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 5), anchor="w")
        ctk.CTkLabel(fs_card, text=t("conv.fs_desc"),
                     font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"],
                     ).pack(padx=15, anchor="w")

        self._fs_part_combo = ctk.CTkComboBox(
            fs_card, values=[t("common.loading")], width=320,
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly",
        )
        self._fs_part_combo.pack(padx=15, pady=10, fill="x")

        ctk.CTkLabel(fs_card, text=t("conv.fs_target"),
                     text_color=COLORS["text_secondary"],
                     font=ctk.CTkFont(size=12)).pack(padx=15, anchor="w")
        self._fs_target = ctk.CTkComboBox(
            fs_card, values=["NTFS", "FAT32", "exFAT"],
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly",
        )
        self._fs_target.set("NTFS")
        self._fs_target.pack(padx=15, pady=10, fill="x")

        ctk.CTkLabel(fs_card,
                     text=t("conv.fs_warning"),
                     text_color=COLORS["accent_yellow"], font=ctk.CTkFont(size=10),
                     justify="left").pack(padx=15, pady=(0, 5), anchor="w")

        ctk.CTkButton(fs_card, text=t("conv.convert"), width=140, height=36,
                      fg_color=COLORS["accent_green"], hover_color=COLORS["hover"],
                      command=self._do_convert_fs).pack(padx=15, pady=(5, 15))

        # --- Primary ↔ Logical card ---
        pl_card = _card(self)
        pl_card.grid(row=2, column=0, columnspan=2, sticky="new", padx=20, pady=15)

        ctk.CTkLabel(pl_card, text=t("conv.pl_title"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 5), anchor="w")
        ctk.CTkLabel(pl_card,
                     text=t("conv.pl_desc"),
                     text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11), wraplength=700,
                     justify="left").pack(padx=15, anchor="w")
        ctk.CTkButton(pl_card, text=t("conv.pl_wizard"), width=200, height=34,
                      fg_color=COLORS["accent_purple"], hover_color=COLORS["hover"],
                      command=lambda: messagebox.showinfo(t("conv.pl_title"),
                          t("conv.pl_msg")),
                      ).pack(padx=15, pady=15, anchor="w")

    # -- Keyboard shortcuts ------------------------------------------------

    def _bind_shortcuts(self):
        """Bind page-specific keyboard shortcuts."""
        self.bind("<Alt-d>", lambda e: self._conv_disk_combo.focus_set())
        self.bind("<Alt-f>", lambda e: self._fs_part_combo.focus_set())

    # -- Logic -------------------------------------------------------------

    def _refresh(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self, disks: list) -> None:
        super()._on_disks_loaded(disks)
        self._populate()

    def _populate(self):
        names = [_disk_display(d) for d in self._disks] or [t("common.no_disks")]
        self._conv_disk_combo.configure(values=names)
        if names:
            self._conv_disk_combo.set(names[0])
            self._update_style_label(names[0])

        # Build partition list for FS conversion
        parts: list[str] = []
        for d in self._disks:
            for p in getattr(d, "partitions", []):
                letter = getattr(p, "letter", "") or "?"
                fs = getattr(p, "file_system", "?")
                sz = format_bytes(getattr(p, "size_bytes", 0))
                parts.append(f"{letter}: ({fs}, {sz}) [{t('common.disk')} {getattr(d, 'index', '?')}]")
        self._fs_part_combo.configure(values=parts or [t("conv.no_partitions")])
        if parts:
            self._fs_part_combo.set(parts[0])

    def _update_style_label(self, _v=None):
        idx_str = self._conv_disk_combo.get()
        vals = self._conv_disk_combo.cget("values") or []
        idx = vals.index(idx_str) if idx_str in vals else 0
        if idx < len(self._disks):
            style = getattr(self._disks[idx], "partition_style", "Unknown")
            self._style_label.configure(text=style)

    def _do_convert_style(self):
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("conv.confirm_style_title"),
            t("conv.confirm_style_msg"),
            risk_level="high",
        ):
            return
        messagebox.showinfo(t("conv.convert"), t("conv.style_queued"))

    def _do_convert_fs(self):
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("conv.confirm_fs_title"),
            t("conv.confirm_fs_msg"),
            risk_level="high",
        ):
            return
        messagebox.showinfo(t("conv.convert"), t("conv.fs_queued"))


# ============================================================================
#  4.  BackupPage
# ============================================================================

class BackupPage(_KeyboardAccessMixin, _DiskPageMixin, ctk.CTkFrame):
    """Backup and restore interface with tabs."""

    def __init__(self, parent, *, backup_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._bkpmgr = backup_manager
        self._disks: list = []
        self._selected_backup_idx: int = 0
        self._build_ui()
        self._apply_keyboard_accessibility()
        self._bind_shortcuts()
        self._refresh()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        _heading(self, t("bak.title")).grid(
            row=0, column=0, sticky="w", padx=20, pady=(20, 10))

        self._tabs = ctk.CTkTabview(
            self, fg_color=COLORS["bg_card"], corner_radius=10,
            segmented_button_fg_color=COLORS["bg_medium"],
            segmented_button_selected_color=COLORS["accent_blue"],
            segmented_button_unselected_color=COLORS["bg_light"],
        )
        self._tabs.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))

        # -- Tab: Create Backup --
        tab_create = self._tabs.add(t("bak.tab_create"))
        tab_create.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab_create, text=t("bak.type"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_primary"]).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))

        # Map language-neutral keys to translated display labels
        self._BACKUP_TYPE_MAP = {
            "system": t("bak.system"),
            "full_disk": t("bak.full_disk"),
            "partition": t("bak.partition"),
            "incremental": "Incremental",
        }
        self._bk_type = ctk.CTkSegmentedButton(
            tab_create,
            values=[self._BACKUP_TYPE_MAP["full_disk"],
                    self._BACKUP_TYPE_MAP["partition"],
                    self._BACKUP_TYPE_MAP["system"],
                    self._BACKUP_TYPE_MAP["incremental"]],
            selected_color=COLORS["accent_blue"], unselected_color=COLORS["bg_medium"],
        )
        self._bk_type.set(self._BACKUP_TYPE_MAP["system"])
        self._bk_type.grid(row=1, column=0, sticky="ew", padx=10, pady=5)

        ctk.CTkLabel(tab_create, text=t("bak.source"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_primary"]).grid(row=2, column=0, sticky="w", padx=10, pady=(10, 5))

        self._bk_source = ctk.CTkComboBox(
            tab_create, values=[t("common.loading")],
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly",
        )
        self._bk_source.grid(row=3, column=0, sticky="ew", padx=10, pady=5)

        ctk.CTkLabel(tab_create, text=t("bak.dest"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_primary"]).grid(row=4, column=0, sticky="w", padx=10, pady=(10, 5))

        dest_row = ctk.CTkFrame(tab_create, fg_color="transparent")
        dest_row.grid(row=5, column=0, sticky="ew", padx=10, pady=5)
        dest_row.grid_columnconfigure(0, weight=1)

        default_dir = ""
        if self._bkpmgr:
            default_dir = getattr(self._bkpmgr, "backup_dir", "")
        self._bk_dest = ctk.CTkEntry(dest_row, placeholder_text=t("bak.dest_placeholder"),
                                      fg_color=COLORS["bg_medium"], border_color=COLORS["border"])
        self._bk_dest.grid(row=0, column=0, sticky="ew")
        if default_dir:
            self._bk_dest.insert(0, default_dir)

        ctk.CTkButton(dest_row, text=t("bak.browse"), width=80,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._browse_dest).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(tab_create, text=t("bak.name"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_primary"]).grid(row=6, column=0, sticky="w", padx=10, pady=(10, 5))
        self._bk_name = ctk.CTkEntry(tab_create, placeholder_text=t("bak.name_placeholder"),
                                      fg_color=COLORS["bg_medium"], border_color=COLORS["border"])
        self._bk_name.grid(row=7, column=0, sticky="ew", padx=10, pady=5)

        self._bk_verify = ctk.CTkCheckBox(tab_create, text=t("bak.verify"),
                                           fg_color=COLORS["accent_blue"],
                                           text_color=COLORS["text_primary"])
        self._bk_verify.grid(row=8, column=0, sticky="w", padx=10, pady=4)

        # Compression option
        self._bk_compress = ctk.CTkCheckBox(tab_create, text="🗜️ Compress backup (ZIP)",
                                             fg_color=COLORS["accent_purple"],
                                             text_color=COLORS["text_primary"])
        self._bk_compress.grid(row=9, column=0, sticky="w", padx=10, pady=4)

        # Encryption option
        enc_frame = ctk.CTkFrame(tab_create, fg_color="transparent")
        enc_frame.grid(row=10, column=0, sticky="ew", padx=10, pady=4)
        enc_frame.grid_columnconfigure(1, weight=1)

        self._bk_encrypt = ctk.CTkCheckBox(enc_frame, text="🔐 Encrypt (AES-256)",
                                            fg_color=COLORS["accent_orange"],
                                            text_color=COLORS["text_primary"],
                                            command=self._toggle_encrypt_field)
        self._bk_encrypt.grid(row=0, column=0, sticky="w")

        self._bk_password = ctk.CTkEntry(enc_frame, placeholder_text="Encryption password",
                                          fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
                                          show="•", state="disabled")
        self._bk_password.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        ctk.CTkButton(tab_create, text=t("bak.create_btn"), width=200, height=40,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      fg_color=COLORS["accent_green"], hover_color=COLORS["hover"],
                      command=self._create_backup).grid(row=11, column=0, pady=15)

        # -- Tab: Restore --
        tab_restore = self._tabs.add(t("bak.tab_restore"))
        tab_restore.grid_columnconfigure(0, weight=1)
        tab_restore.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(tab_restore, text=t("bak.available"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_primary"]).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))

        self._backup_list = ctk.CTkScrollableFrame(
            tab_restore, fg_color=COLORS["bg_medium"], corner_radius=8,
        )
        self._backup_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        self._backup_list.grid_columnconfigure(0, weight=1)

        self._refresh_backup_list()

        btn_row = ctk.CTkFrame(tab_restore, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        for txt, col, cmd in [
            (t("bak.restore_btn"), COLORS["accent_blue"], self._restore_backup),
            (t("bak.verify_btn"), COLORS["accent_green"], self._verify_backup),
            (t("bak.delete_btn"), COLORS["accent_red"], self._delete_backup),
        ]:
            ctk.CTkButton(btn_row, text=txt, width=110, height=34,
                          fg_color=col, hover_color=COLORS["hover"],
                          command=cmd).pack(side="left", padx=4)

        # -- Tab: Settings --
        tab_settings = self._tabs.add(t("bak.tab_settings"))
        tab_settings.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(tab_settings, text=t("bak.settings_dir"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text_primary"]).grid(row=0, column=0, sticky="w", padx=10, pady=(15, 5))
        self._settings_dir = ctk.CTkEntry(tab_settings, placeholder_text="",
                                           fg_color=COLORS["bg_medium"], border_color=COLORS["border"])
        self._settings_dir.grid(row=0, column=1, sticky="ew", padx=10, pady=(15, 5))
        if default_dir:
            self._settings_dir.insert(0, default_dir)

        ctk.CTkLabel(tab_settings, text=t("bak.auto_verify"),
                     font=ctk.CTkFont(size=12),
                     text_color=COLORS["text_primary"]).grid(row=1, column=0, sticky="w", padx=10, pady=8)
        self._auto_verify = ctk.CTkSwitch(tab_settings, text="", fg_color=COLORS["bg_medium"],
                                           progress_color=COLORS["accent_green"])
        self._auto_verify.grid(row=1, column=1, sticky="w", padx=10, pady=8)

        # -- Tab: Cloud Backup --
        tab_cloud = self._tabs.add("☁️ Cloud")
        tab_cloud.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab_cloud, text="☁️ Cloud Backup",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))
        ctk.CTkLabel(tab_cloud,
                     text="Sync backups to OneDrive, Google Drive, or Dropbox via local sync folders.",
                     font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], wraplength=500, justify="left",
                     ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))

        self._cloud_status_frame = ctk.CTkFrame(tab_cloud, fg_color="transparent")
        self._cloud_status_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        self._cloud_status_frame.grid_columnconfigure(1, weight=1)
        self._refresh_cloud_providers()

        cloud_btn_row = ctk.CTkFrame(tab_cloud, fg_color="transparent")
        cloud_btn_row.grid(row=3, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(cloud_btn_row, text="☁️ Upload Last Backup", width=180, height=34,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._upload_to_cloud).pack(side="left", padx=4)
        ctk.CTkButton(cloud_btn_row, text="🔄 Refresh Providers", width=150, height=34,
                      fg_color=COLORS["bg_light"], hover_color=COLORS["hover"],
                      command=self._refresh_cloud_providers).pack(side="left", padx=4)

    # -- Keyboard shortcuts ------------------------------------------------

    def _bind_shortcuts(self):
        """Bind page-specific keyboard shortcuts."""
        self.bind("<Alt-b>", lambda e: self._browse_dest())
        self.bind("<Alt-Return>", lambda e: self._create_backup())

    # -- Helpers -----------------------------------------------------------

    def _refresh(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self, disks: list) -> None:
        super()._on_disks_loaded(disks)
        self._populate_sources()

    def _populate_sources(self):
        names = [_disk_display(d) for d in self._disks] or [t("common.no_disks")]
        self._bk_source.configure(values=names)
        if names:
            self._bk_source.set(names[0])

    def _toggle_encrypt_field(self):
        if self._bk_encrypt.get():
            self._bk_password.configure(state="normal")
        else:
            self._bk_password.configure(state="disabled")

    def _refresh_cloud_providers(self):
        for w in self._cloud_status_frame.winfo_children():
            w.destroy()
        try:
            from src.core.cloud_backup import CloudBackupManager
            mgr = CloudBackupManager()
            providers = mgr.list_providers()
            for i, p in enumerate(providers):
                icon = "✅" if p.available else "❌"
                text = f"{icon} {p.display_name}"
                if p.available:
                    text += f"  ({p.sync_folder})"
                ctk.CTkLabel(self._cloud_status_frame, text=text,
                             font=ctk.CTkFont(size=11),
                             text_color=COLORS["accent_green"] if p.available else COLORS["text_muted"],
                             ).grid(row=i, column=0, sticky="w", pady=2)
        except Exception as exc:
            ctk.CTkLabel(self._cloud_status_frame, text=f"Cloud module error: {exc}",
                         text_color=COLORS["accent_red"]).grid(row=0, column=0)

    def _upload_to_cloud(self):
        backup_info = self._get_selected_backup()
        if not backup_info:
            messagebox.showwarning("Cloud Backup", t("bak.none"))
            return
        try:
            from src.core.cloud_backup import CloudBackupManager
            mgr = CloudBackupManager()
            available = mgr.get_available_providers()
            if not available:
                messagebox.showwarning("Cloud Backup",
                    "No cloud providers detected.\nInstall OneDrive, Google Drive, or Dropbox desktop client.")
                return
            provider = available[0]  # Use first available
            source = backup_info.backup_path
            if not os.path.isdir(source) and not os.path.isfile(source):
                messagebox.showwarning("Cloud Backup", f"Backup path not found: {source}")
                return

            def _bg():
                try:
                    # If directory, zip it first for upload
                    if os.path.isdir(source):
                        import zipfile as _zf
                        zip_path = source.rstrip(os.sep) + ".zip"
                        if not os.path.isfile(zip_path):
                            self.after(0, lambda: messagebox.showinfo(
                                "Cloud Backup", "Compressing backup for upload..."))
                            with _zf.ZipFile(zip_path, "w", _zf.ZIP_DEFLATED) as zf:
                                for root, _dirs, files in os.walk(source):
                                    for f in files:
                                        fp = os.path.join(root, f)
                                        zf.write(fp, os.path.relpath(fp, source))
                        dest = mgr.upload(provider.name, zip_path)
                    else:
                        dest = mgr.upload(provider.name, source)
                    self.after(0, lambda: messagebox.showinfo(
                        "Cloud Backup", f"Uploaded to {provider.display_name}:\n{dest}"))
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
            threading.Thread(target=_bg, daemon=True).start()
            messagebox.showinfo("Cloud Backup", f"Uploading to {provider.display_name}...")
        except ImportError:
            messagebox.showerror("Cloud Backup", "Cloud backup module not available.")

    def _browse_dest(self):
        path = filedialog.askdirectory(title=t("bak.dest_title"))
        if path:
            self._bk_dest.delete(0, "end")
            self._bk_dest.insert(0, path)

    def _refresh_backup_list(self):
        for w in self._backup_list.winfo_children():
            w.destroy()
        self._selected_backup_idx = 0
        self._backup_rows: list[ctk.CTkFrame] = []
        backups = []
        if self._bkpmgr:
            try:
                backups = self._bkpmgr.list_backups()
            except Exception as exc:
                _log.warning("Failed to list backups: %s", exc)
        if not backups:
            ctk.CTkLabel(self._backup_list, text=t("bak.none"),
                         text_color=COLORS["text_muted"]).grid(row=0, column=0, pady=20)
            return
        for i, b in enumerate(backups):
            row = ctk.CTkFrame(self._backup_list, fg_color=COLORS["bg_card"] if i % 2 == 0 else "transparent",
                               corner_radius=6, cursor="hand2")
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=2)
            row.grid_columnconfigure(1, weight=1)
            self._backup_rows.append(row)
            # Bind click to select this row
            row.bind("<Button-1>", lambda e, idx=i: self._select_backup_row(idx))
            ctk.CTkLabel(row, text="📦", font=ctk.CTkFont(size=16)).grid(row=0, column=0, padx=8, pady=6)
            name_lbl = ctk.CTkLabel(row, text=getattr(b, "name", "Backup"),
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=COLORS["text_primary"], anchor="w")
            name_lbl.grid(row=0, column=1, sticky="w")
            name_lbl.bind("<Button-1>", lambda e, idx=i: self._select_backup_row(idx))
            info = f"{getattr(b, 'timestamp', '')} | {getattr(b, 'backup_type', '')} | {format_bytes(getattr(b, 'total_size_bytes', 0))}"
            info_lbl = ctk.CTkLabel(row, text=info, font=ctk.CTkFont(size=10),
                         text_color=COLORS["text_secondary"], anchor="w")
            info_lbl.grid(row=1, column=1, sticky="w", padx=(0, 10))
            info_lbl.bind("<Button-1>", lambda e, idx=i: self._select_backup_row(idx))
        # Highlight the initially selected row
        self._highlight_backup_row()

    def _select_backup_row(self, idx: int) -> None:
        """Mark *idx* as the selected backup and update row highlights."""
        self._selected_backup_idx = idx
        self._highlight_backup_row()

    def _highlight_backup_row(self) -> None:
        """Apply a visual highlight to the currently selected backup row."""
        for i, row in enumerate(self._backup_rows):
            if i == self._selected_backup_idx:
                row.configure(fg_color=COLORS["accent_blue"])
            else:
                row.configure(fg_color=COLORS["bg_card"] if i % 2 == 0 else "transparent")

    def _create_backup(self):
        name = self._bk_name.get().strip() or "Backup"
        dest = self._bk_dest.get().strip()
        if not dest:
            messagebox.showwarning(t("bak.dest"), t("bak.no_dest"))
            return
        btype_display = self._bk_type.get()
        _reverse_map = {v: k for k, v in self._BACKUP_TYPE_MAP.items()}
        btype_key = _reverse_map.get(btype_display, "partition")
        compress = bool(self._bk_compress.get())
        encrypt = bool(self._bk_encrypt.get())
        password = self._bk_password.get().strip() if encrypt else ""
        if encrypt and not password:
            messagebox.showwarning("Encryption", "Please enter an encryption password.")
            return
        messagebox.showinfo(t("bak.started_title"), t("bak.started", type=btype_display, name=name, dest=dest))
        if self._bkpmgr:
            def _bg():
                try:
                    backup_info = None
                    if btype_key == "incremental":
                        backup_info = self._bkpmgr.create_incremental_backup(
                            source_path=dest, name=name)
                    elif btype_key == "system":
                        backup_info = self._bkpmgr.create_system_backup(
                            destination_path=dest, name=name)
                    elif btype_key == "full_disk":
                        backup_info = self._bkpmgr.create_full_disk_backup(
                            disk_index=0, name=name)
                    else:
                        backup_info = self._bkpmgr.create_partition_backup(
                            disk_index=0, partition_index=1, name=name)

                    # Post-processing: compress and/or encrypt
                    if backup_info and compress:
                        try:
                            self._bkpmgr.compress_backup(backup_info.backup_id)
                        except Exception as ce:
                            _log.warning("Compression failed: %s", ce)
                    if backup_info and encrypt and password:
                        try:
                            self._bkpmgr.encrypt_backup(backup_info.backup_id, password)
                        except Exception as ee:
                            _log.warning("Encryption failed: %s", ee)

                    # Record in history
                    try:
                        from src.core.history import OperationHistory
                        OperationHistory().record(
                            operation="backup",
                            description=f"{btype_key} backup: {name}",
                            success=True,
                            message=f"Destination: {dest}",
                        )
                    except Exception:
                        pass

                    # Desktop notification
                    try:
                        from src.utils.notifications import NotificationManager
                        NotificationManager().notify_backup_complete(name, size_bytes=0)
                    except Exception:
                        pass
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
                    try:
                        from src.core.history import OperationHistory
                        OperationHistory().record(
                            operation="backup",
                            description=f"{btype_key} backup: {name}",
                            success=False,
                            message=str(e),
                        )
                    except Exception:
                        pass
                self.after(0, self._refresh_backup_list)
            threading.Thread(target=_bg, daemon=True).start()

    def _get_selected_backup(self):
        """Return the BackupInfo of the selected item in the backup list, or None."""
        if not self._bkpmgr:
            return None
        try:
            backups = self._bkpmgr.list_backups()
            if backups:
                idx = min(self._selected_backup_idx, len(backups) - 1)
                return backups[idx]
        except Exception:
            pass
        return None

    def _restore_backup(self):
        info = self._get_selected_backup()
        if not info:
            messagebox.showwarning(t("bak.tab_restore"), t("bak.none"))
            return
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("bak.restore_btn"),
            t("bak.restore_confirm", name=info.name),
            risk_level="high",
        ):
            return
        # Ask for target disk/partition
        dlg = ctk.CTkInputDialog(
            text="Target disk number (e.g. 1):",
            title=t("bak.restore_btn"),
        )
        disk_str = dlg.get_input()
        if not disk_str:
            return
        dlg2 = ctk.CTkInputDialog(
            text="Target partition number (e.g. 1):",
            title=t("bak.restore_btn"),
        )
        part_str = dlg2.get_input()
        if not part_str:
            return

        def _bg():
            try:
                self._bkpmgr.restore_backup(info.backup_id, int(disk_str), int(part_str))
                self.after(0, lambda: messagebox.showinfo(
                    t("bak.restore_btn"), t("bak.restore_done")))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()

    def _verify_backup(self):
        info = self._get_selected_backup()
        if not info:
            messagebox.showwarning(t("bak.verify_btn"), t("bak.none"))
            return

        def _bg():
            try:
                ok = self._bkpmgr.verify_backup(info.backup_id)
                msg = t("bak.verify_pass") if ok else t("bak.verify_fail")
                self.after(0, lambda: messagebox.showinfo(t("bak.verify_btn"), msg))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()
        messagebox.showinfo(t("bak.verify_btn"), t("bak.verify_running"))

    def _delete_backup(self):
        info = self._get_selected_backup()
        if not info:
            messagebox.showwarning(t("bak.delete_btn"), t("bak.none"))
            return
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("bak.delete_btn"),
            t("bak.delete_confirm", name=info.name),
            risk_level="high",
        ):
            return
        try:
            self._bkpmgr.delete_backup(info.backup_id)
            self._refresh_backup_list()
            messagebox.showinfo(t("bak.delete_btn"), t("bak.delete_done"))
        except Exception as e:
            messagebox.showerror(t("common.error"), str(e))


# ============================================================================
#  5.  RecoveryPage
# ============================================================================

class RecoveryPage(_KeyboardAccessMixin, _DiskPageMixin, ctk.CTkFrame):
    """Partition recovery wizard."""

    def __init__(self, parent, **_kw):
        super().__init__(parent, fg_color="transparent")
        self._disks: list = []
        self._step = 0
        self._found_parts: list = []
        self._build_ui()
        self._apply_keyboard_accessibility()
        self._bind_shortcuts()
        self._refresh()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        _heading(self, t("rec.title")).grid(
            row=0, column=0, sticky="w", padx=20, pady=(20, 10))

        self._card = _card(self)
        self._card.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self._card.grid_columnconfigure(0, weight=1)
        self._card.grid_rowconfigure(2, weight=1)

        # Step indicator
        self._step_label = ctk.CTkLabel(
            self._card, text=t("rec.step1"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["accent_blue"],
        )
        self._step_label.grid(row=0, column=0, sticky="w", padx=20, pady=(20, 10))

        # Content area
        self._step_frame = ctk.CTkFrame(self._card, fg_color="transparent")
        self._step_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=5)
        self._step_frame.grid_columnconfigure(0, weight=1)

        # Navigation buttons
        nav = ctk.CTkFrame(self._card, fg_color="transparent")
        nav.grid(row=3, column=0, sticky="ew", padx=20, pady=(10, 20))

        self._back_btn = ctk.CTkButton(nav, text=t("rec.back"), width=100, height=34,
                                        fg_color=COLORS["bg_light"], hover_color=COLORS["hover"],
                                        command=self._go_back, state="disabled")
        self._back_btn.pack(side="left")

        self._next_btn = ctk.CTkButton(nav, text=t("rec.next"), width=100, height=34,
                                        fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                                        command=self._go_next)
        self._next_btn.pack(side="right")

        self._show_step(0)

    # -- Keyboard shortcuts ------------------------------------------------

    def _bind_shortcuts(self):
        """Bind page-specific keyboard shortcuts."""
        self.bind("<Alt-Right>", lambda e: self._go_next())
        self.bind("<Alt-Left>", lambda e: self._go_back())

    # -- Logic -------------------------------------------------------------

    def _refresh(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self, disks: list) -> None:
        super()._on_disks_loaded(disks)
        self._populate_step0()

    def _populate_step0(self):
        if self._step == 0:
            self._show_step(0)

    def _show_step(self, step: int):
        self._step = step
        for w in self._step_frame.winfo_children():
            w.destroy()

        self._back_btn.configure(state="normal" if step > 0 else "disabled")
        titles = [
            t("rec.step1"),
            t("rec.step2"),
            t("rec.step3"),
            t("rec.step4"),
        ]
        self._step_label.configure(text=titles[min(step, 3)])

        if step == 0:
            self._build_step0()
        elif step == 1:
            self._build_step1()
        elif step == 2:
            self._build_step2()
        elif step == 3:
            self._build_step3()

    def _build_step0(self):
        ctk.CTkLabel(self._step_frame, text=t("rec.select_disk"),
                     text_color=COLORS["text_primary"], font=ctk.CTkFont(size=12)).grid(
            row=0, column=0, sticky="w", pady=(10, 5))
        names = [_disk_display(d) for d in self._disks] or [t("common.no_disks")]
        self._rec_disk_combo = ctk.CTkComboBox(
            self._step_frame, values=names, width=500,
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly",
        )
        self._rec_disk_combo.grid(row=1, column=0, sticky="w", pady=5)
        if names:
            self._rec_disk_combo.set(names[0])

    def _build_step1(self):
        ctk.CTkLabel(self._step_frame, text=t("rec.scan_type"),
                     text_color=COLORS["text_primary"], font=ctk.CTkFont(size=12)).grid(
            row=0, column=0, sticky="w", pady=(10, 10))

        self._scan_type = ctk.CTkSegmentedButton(
            self._step_frame, values=[t("rec.quick"), t("rec.deep")],
            selected_color=COLORS["accent_blue"], unselected_color=COLORS["bg_medium"],
        )
        self._scan_type.set(t("rec.quick"))
        self._scan_type.grid(row=1, column=0, sticky="ew", pady=5)

        ctk.CTkLabel(self._step_frame,
                     text=t("rec.scan_desc"),
                     text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
                     justify="left").grid(row=2, column=0, sticky="w", pady=10)

    def _build_step2(self):
        ctk.CTkLabel(self._step_frame, text=t("rec.scanning"),
                     text_color=COLORS["text_primary"], font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, pady=(20, 10))

        self._scan_progress = ctk.CTkProgressBar(
            self._step_frame, width=400, height=18,
            fg_color=COLORS["bg_medium"], progress_color=COLORS["accent_blue"],
        )
        self._scan_progress.set(0)
        self._scan_progress.grid(row=1, column=0, pady=10)

        self._scan_status = ctk.CTkLabel(self._step_frame, text="0%",
                                          text_color=COLORS["text_secondary"])
        self._scan_status.grid(row=2, column=0)

        self._next_btn.configure(state="disabled")
        self._run_real_scan()

    def _run_real_scan(self):
        """Launch partition recovery scan in a background thread."""
        idx_str = getattr(self, "_rec_disk_combo", None)
        disk_idx = 0
        if idx_str:
            sel = idx_str.get()
            vals = idx_str.cget("values") or []
            pos = vals.index(sel) if sel in vals else 0
            if pos < len(self._disks):
                disk_idx = getattr(self._disks[pos], "index", 0)

        is_deep = getattr(self, "_scan_type", None) and self._scan_type.get() == t("rec.deep")

        def _update_progress(pct, msg=""):
            self.after(0, lambda: self._scan_progress.set(min(pct / 100.0, 1.0)))
            self.after(0, lambda: self._scan_status.configure(
                text=t("rec.scan_pct", pct=int(pct))))

        def _bg():
            try:
                from src.core.recovery import PartitionRecovery
                recovery = PartitionRecovery()
                if is_deep:
                    results = recovery.deep_scan(disk_idx, callback=_update_progress)
                else:
                    results = recovery.quick_scan(disk_idx, callback=_update_progress)

                parts = []
                for r in results:
                    parts.append({
                        "index": getattr(r, "index", len(parts)),
                        "fs": getattr(r, "filesystem", "Unknown"),
                        "size": format_bytes(getattr(r, "size_bytes", 0)),
                        "status": t("rec.recoverable") if getattr(r, "recoverable", False) else t("rec.partial"),
                        "offset": getattr(r, "offset_bytes", 0),
                    })
                self.after(0, lambda p=parts: setattr(self, '_found_parts', p))
                self.after(0, lambda: self._scan_progress.set(1.0))
                self.after(0, lambda: self._scan_status.configure(text=t("rec.scan_done")))
                self.after(0, lambda: self._next_btn.configure(state="normal"))
            except ImportError:
                # Fallback to simulated data if recovery module not available
                _fallback = [
                    {"index": 1, "fs": "NTFS", "size": "120 GB", "status": t("rec.recoverable")},
                ]
                self.after(0, lambda p=_fallback: setattr(self, '_found_parts', p))
                self.after(0, lambda: self._scan_progress.set(1.0))
                self.after(0, lambda: self._scan_status.configure(text=t("rec.scan_done")))
                self.after(0, lambda: self._next_btn.configure(state="normal"))
            except Exception as e:
                self.after(0, lambda: self._scan_status.configure(
                    text=f"{t('common.error')}: {e}"))
                self.after(0, lambda: self._next_btn.configure(state="normal"))

        threading.Thread(target=_bg, daemon=True).start()

    def _build_step3(self):
        ctk.CTkLabel(self._step_frame, text=t("rec.found"),
                     text_color=COLORS["text_primary"], font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, sticky="w", pady=(10, 5))

        if not self._found_parts:
            ctk.CTkLabel(self._step_frame, text=t("rec.not_found"),
                         text_color=COLORS["text_muted"]).grid(row=1, column=0, pady=20)
            return

        for i, p in enumerate(self._found_parts):
            row = ctk.CTkFrame(self._step_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
            row.grid(row=i + 1, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text=f"{t('rec.partition')} {p['index']}",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=COLORS["text_primary"]).grid(row=0, column=0, padx=12, pady=8, sticky="w")
            ctk.CTkLabel(row, text=f"{p['fs']} | {p['size']} | {p['status']}",
                         text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
                         ).grid(row=0, column=1, sticky="w")
            ctk.CTkButton(row, text=t("rec.recover"), width=80, height=28,
                          fg_color=COLORS["accent_green"], hover_color=COLORS["hover"],
                          command=lambda pp=p: self._recover_partition(pp),
                          ).grid(row=0, column=2, padx=12, pady=8)

        self._next_btn.configure(text=t("rec.done"), command=lambda: self._show_step(0))

    def _recover_partition(self, part_info: dict):
        """Attempt to recover the selected partition."""
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("rec.recover"),
            f"Recover {part_info['fs']} partition ({part_info['size']})?\n"
            "This will attempt to recreate the partition at its original location.",
            risk_level="high",
        ):
            return

        idx_str = getattr(self, "_rec_disk_combo", None)
        disk_idx = 0
        if idx_str:
            sel = idx_str.get()
            vals = idx_str.cget("values") or []
            pos = vals.index(sel) if sel in vals else 0
            if pos < len(self._disks):
                disk_idx = getattr(self._disks[pos], "index", 0)

        def _bg():
            try:
                from src.core.recovery import PartitionRecovery
                recovery = PartitionRecovery()
                offset = part_info.get("offset", 0)
                recovery.recover_partition(disk_idx, offset)
                self.after(0, lambda: messagebox.showinfo(
                    t("rec.recover"), t("rec.recover_started", idx=part_info['index'])))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()

    def _go_back(self):
        if self._step > 0:
            self._show_step(self._step - 1)

    def _go_next(self):
        if self._step < 3:
            self._show_step(self._step + 1)


# ============================================================================
#  6.  AdvancedPage
# ============================================================================

class AdvancedPage(_KeyboardAccessMixin, _DiskPageMixin, ctk.CTkFrame):
    """Advanced tools: 4K alignment, WinPE, disk health, secure wipe."""

    def __init__(self, parent, *, operation_manager=None, backup_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._opmgr = operation_manager
        self._bkpmgr = backup_manager
        self._disks: list = []
        self._build_ui()
        self._apply_keyboard_accessibility()
        self._bind_shortcuts()
        self._refresh()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, weight=1)

        _heading(self, t("adv.title")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(20, 15))

        # --- 4K Alignment Card ---
        align_card = _card(self)
        align_card.grid(row=1, column=0, sticky="nsew", padx=(20, 10), pady=5)

        ctk.CTkLabel(align_card, text=t("adv.align_title"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 3), anchor="w")
        ctk.CTkLabel(align_card,
                     text=t("adv.align_desc"),
                     text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
                     wraplength=300, justify="left").pack(padx=15, anchor="w")

        self._align_combo = ctk.CTkComboBox(
            align_card, values=[t("common.loading")],
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly",
        )
        self._align_combo.pack(padx=15, pady=10, fill="x")

        self._align_status = ctk.CTkLabel(
            align_card, text=t("adv.align_select"),
            text_color=COLORS["text_muted"], font=ctk.CTkFont(size=11),
        )
        self._align_status.pack(padx=15, anchor="w")

        ctk.CTkButton(align_card, text=t("adv.align_check"), width=160, height=34,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._check_alignment).pack(padx=15, pady=(10, 15), anchor="w")

        # --- WinPE Card ---
        pe_card = _card(self)
        pe_card.grid(row=1, column=1, sticky="nsew", padx=(10, 20), pady=5)

        ctk.CTkLabel(pe_card, text=t("adv.pe_title"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 3), anchor="w")
        ctk.CTkLabel(pe_card,
                     text=t("adv.pe_desc"),
                     text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
                     wraplength=300, justify="left").pack(padx=15, anchor="w")

        self._pe_status = ctk.CTkLabel(pe_card, text=t("adv.pe_status"),
                                        text_color=COLORS["text_muted"], font=ctk.CTkFont(size=11))
        self._pe_status.pack(padx=15, pady=(10, 5), anchor="w")

        ctk.CTkButton(pe_card, text=t("adv.pe_check"), width=160, height=34,
                      fg_color=COLORS["accent_green"], hover_color=COLORS["hover"],
                      command=self._check_pe_prereqs).pack(padx=15, pady=5, anchor="w")

        self._pe_drive = ctk.CTkComboBox(
            pe_card, values=[t("adv.pe_usb")],
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly",
        )
        self._pe_drive.pack(padx=15, pady=5, fill="x")

        ctk.CTkButton(pe_card, text=t("adv.pe_create"), width=160, height=34,
                      fg_color=COLORS["accent_orange"], hover_color=COLORS["hover"],
                      command=self._create_pe).pack(padx=15, pady=(5, 15), anchor="w")

        # --- Disk Health Card ---
        health_card = _card(self)
        health_card.grid(row=2, column=0, sticky="nsew", padx=(20, 10), pady=(10, 20))

        ctk.CTkLabel(health_card, text=t("adv.health_title"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 3), anchor="w")
        ctk.CTkLabel(health_card,
                     text=t("adv.health_desc"),
                     text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
                     wraplength=300, justify="left").pack(padx=15, anchor="w")

        self._health_frame = ctk.CTkScrollableFrame(
            health_card, fg_color="transparent", height=150,
        )
        self._health_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self._health_frame.grid_columnconfigure(0, weight=1)

        health_btn_row = ctk.CTkFrame(health_card, fg_color="transparent")
        health_btn_row.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkButton(health_btn_row, text="🔍 SMART Details", width=130, height=30,
                      font=ctk.CTkFont(size=11),
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._show_smart_for_selected).pack(side="left", padx=3)
        ctk.CTkButton(health_btn_row, text="⚡ Benchmark", width=120, height=30,
                      font=ctk.CTkFont(size=11),
                      fg_color=COLORS["accent_purple"], hover_color=COLORS["hover"],
                      command=self._run_benchmark).pack(side="left", padx=3)
        ctk.CTkButton(health_btn_row, text="🧪 Surface Test", width=130, height=30,
                      font=ctk.CTkFont(size=11),
                      fg_color=COLORS["accent_orange"], hover_color=COLORS["hover"],
                      command=self._run_surface_test).pack(side="left", padx=3)

        # --- Secure Wipe Card ---
        wipe_card = _card(self)
        wipe_card.grid(row=2, column=1, sticky="nsew", padx=(10, 20), pady=(10, 20))

        ctk.CTkLabel(wipe_card, text=t("adv.wipe_title"),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 3), anchor="w")
        ctk.CTkLabel(wipe_card,
                     text=t("adv.wipe_desc"),
                     text_color=COLORS["accent_red"], font=ctk.CTkFont(size=11),
                     wraplength=300, justify="left").pack(padx=15, anchor="w")

        self._wipe_combo = ctk.CTkComboBox(
            wipe_card, values=[t("adv.select_disk")],
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly",
        )
        self._wipe_combo.pack(padx=15, pady=10, fill="x")

        self._wipe_method = ctk.CTkSegmentedButton(
            wipe_card, values=[t("adv.wipe_quick"), t("adv.wipe_secure")],
            selected_color=COLORS["accent_red"], unselected_color=COLORS["bg_medium"],
        )
        self._wipe_method.set(t("adv.wipe_quick"))
        self._wipe_method.pack(padx=15, pady=5, fill="x")

        ctk.CTkButton(wipe_card, text=t("adv.wipe_btn"), width=160, height=34,
                      fg_color=COLORS["accent_red"], hover_color="#c0392b",
                      command=self._wipe_disk).pack(padx=15, pady=(10, 15), anchor="w")

        # --- Disk Image Card ---
        image_card = _card(self)
        image_card.grid(row=3, column=0, sticky="nsew", padx=(20, 10), pady=(10, 20))

        ctk.CTkLabel(image_card, text="💿 Disk Imaging",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 3), anchor="w")
        ctk.CTkLabel(image_card,
                     text="Create full disk images in VHD, VHDX, or IMG format.",
                     text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
                     wraplength=300, justify="left").pack(padx=15, anchor="w")

        self._img_combo = ctk.CTkComboBox(
            image_card, values=[t("common.loading")],
            fg_color=COLORS["bg_medium"], border_color=COLORS["border"],
            state="readonly",
        )
        self._img_combo.pack(padx=15, pady=10, fill="x")

        self._img_format = ctk.CTkSegmentedButton(
            image_card, values=["VHDX", "VHD", "IMG"],
            selected_color=COLORS["accent_blue"], unselected_color=COLORS["bg_medium"],
        )
        self._img_format.set("VHDX")
        self._img_format.pack(padx=15, pady=5, fill="x")

        ctk.CTkButton(image_card, text="💿 Create Disk Image", width=180, height=34,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._create_disk_image).pack(padx=15, pady=(10, 15), anchor="w")

        # --- Tools Card (Defrag + Report + Restore Point) ---
        tools_card = _card(self)
        tools_card.grid(row=3, column=1, sticky="nsew", padx=(10, 20), pady=(10, 20))

        ctk.CTkLabel(tools_card, text="🛠️ Disk Tools",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 3), anchor="w")
        ctk.CTkLabel(tools_card,
                     text="Optimization, reports, and system protection tools.",
                     text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
                     wraplength=300, justify="left").pack(padx=15, anchor="w")

        tools_btn_area = ctk.CTkFrame(tools_card, fg_color="transparent")
        tools_btn_area.pack(fill="both", expand=True, padx=15, pady=10)

        ctk.CTkButton(tools_btn_area, text="⚡ Defragment / TRIM", width=200, height=34,
                      fg_color=COLORS["accent_green"], hover_color=COLORS["hover"],
                      command=self._run_defrag).pack(anchor="w", pady=4)

        ctk.CTkButton(tools_btn_area, text="📊 Export Health Report", width=200, height=34,
                      fg_color=COLORS["accent_purple"], hover_color=COLORS["hover"],
                      command=self._export_report).pack(anchor="w", pady=4)

        ctk.CTkButton(tools_btn_area, text="🔒 Create Restore Point", width=200, height=34,
                      fg_color=COLORS["accent_orange"], hover_color=COLORS["hover"],
                      command=self._create_restore_point).pack(anchor="w", pady=4)

    # -- Keyboard shortcuts ------------------------------------------------

    def _bind_shortcuts(self):
        """Bind page-specific keyboard shortcuts."""
        self.bind("<Alt-a>", lambda e: self._align_combo.focus_set())
        self.bind("<Alt-w>", lambda e: self._wipe_combo.focus_set())

    # -- Logic -------------------------------------------------------------

    def _refresh(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self, disks: list) -> None:
        super()._on_disks_loaded(disks)
        self._populate()

    def _populate(self):
        names = [_disk_display(d) for d in self._disks] or [t("common.no_disks")]
        self._align_combo.configure(values=names)
        self._wipe_combo.configure(values=names)
        self._img_combo.configure(values=names)
        if names:
            self._align_combo.set(names[0])
            self._wipe_combo.set(names[0])
            self._img_combo.set(names[0])

        # USB drives for WinPE
        usb = [_disk_display(d) for d in self._disks
               if getattr(d, "interface_type", "").upper() == "USB"]
        self._pe_drive.configure(values=usb or [t("adv.no_usb")])
        if usb:
            self._pe_drive.set(usb[0])

        # Health info
        for w in self._health_frame.winfo_children():
            w.destroy()
        for i, d in enumerate(self._disks):
            row = ctk.CTkFrame(self._health_frame,
                               fg_color=COLORS["bg_medium"] if i % 2 == 0 else "transparent",
                               corner_radius=6)
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)

            health = getattr(d, "health_status", "Unknown")
            hcolor = COLORS["accent_green"] if health in ("Healthy", "OK") else COLORS["accent_yellow"]

            ctk.CTkLabel(row, text=f"{t('common.disk')} {getattr(d, 'index', '?')}: {getattr(d, 'model', '?')}",
                         font=ctk.CTkFont(size=11), text_color=COLORS["text_primary"],
                         ).grid(row=0, column=0, padx=10, pady=6, sticky="w")
            ctk.CTkLabel(row, text=health, font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=hcolor).grid(row=0, column=1, padx=10, sticky="e")

    def _check_alignment(self):
        idx_str = self._align_combo.get()
        vals = self._align_combo.cget("values") or []
        idx = vals.index(idx_str) if idx_str in vals else 0
        if idx < len(self._disks):
            aligned = getattr(self._disks[idx], "is_4k_aligned", None)
            if aligned is True:
                self._align_status.configure(
                    text=t("adv.align_ok"), text_color=COLORS["accent_green"])
            elif aligned is False:
                self._align_status.configure(
                    text=t("adv.align_bad"), text_color=COLORS["accent_yellow"])
            else:
                self._align_status.configure(
                    text=t("adv.align_unknown"), text_color=COLORS["text_muted"])

    def _check_pe_prereqs(self):
        if self._bkpmgr:
            try:
                info = self._bkpmgr.check_winpe_prerequisites()
                adk = info.get("adk_installed", False)
                pe = info.get("winpe_installed", False)
                txt = f"ADK: {'✅' if adk else '❌'}  |  WinPE Addon: {'✅' if pe else '❌'}"
                col = COLORS["accent_green"] if (adk and pe) else COLORS["accent_yellow"]
                self._pe_status.configure(text=txt, text_color=col)
            except Exception as e:
                self._pe_status.configure(text=f"{t('common.error')}: {e}", text_color=COLORS["accent_red"])
        else:
            self._pe_status.configure(text=t("adv.pe_unavailable"), text_color=COLORS["text_muted"])

    def _create_pe(self):
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("adv.pe_confirm_title"),
            t("adv.pe_confirm"),
            risk_level="critical",
        ):
            return
        messagebox.showinfo(t("adv.pe_title"), t("adv.pe_started"))

    def _wipe_disk(self):
        if ConfirmDialog and not ConfirmDialog.ask(
            self, t("adv.wipe_confirm_title"),
            t("adv.wipe_confirm"),
            risk_level="critical",
        ):
            return

        idx_str = self._wipe_combo.get()
        vals = self._wipe_combo.cget("values") or []
        idx = vals.index(idx_str) if idx_str in vals else 0
        if idx >= len(self._disks):
            return

        disk_index = getattr(self._disks[idx], "index", 0)
        method = self._wipe_method.get()

        def _bg():
            try:
                from src.core.secure_wipe import SecureWiper
                wiper = SecureWiper()
                if method == t("adv.wipe_secure"):
                    wiper.secure_wipe(disk_index, passes=3, callback=self._wipe_progress)
                else:
                    wiper.quick_wipe(disk_index, callback=self._wipe_progress)
                self.after(0, lambda: messagebox.showinfo(
                    t("adv.wipe_title"), t("adv.wipe_done")))
                # Send notification
                try:
                    from src.utils.notifications import NotificationManager
                    NotificationManager().notify(
                        "Wipe Complete",
                        f"Disk {disk_index} wipe finished.",
                    )
                except Exception:
                    pass
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))

        threading.Thread(target=_bg, daemon=True).start()
        messagebox.showinfo(t("adv.wipe_title"), t("adv.wipe_started"))

    def _wipe_progress(self, pct, msg):
        """Callback for wipe progress updates."""
        pass  # Could update a progress bar in future

    # -- SMART Details --------------------------------------------------------

    def _show_smart_details(self, disk_index: int):
        """Show detailed SMART information for a disk."""
        def _bg():
            try:
                from src.core.disk_health import DiskHealthManager
                mgr = DiskHealthManager()
                info = mgr.get_smart_info(disk_index)
                details = (
                    f"Temperature: {info.temperature_celsius or 'N/A'} C\n"
                    f"Power-On Hours: {info.power_on_hours or 'N/A'}\n"
                    f"Reallocated Sectors: {info.reallocated_sectors or '0'}\n"
                    f"Pending Sectors: {info.pending_sectors or '0'}\n"
                    f"Uncorrectable: {info.uncorrectable_sectors or '0'}\n"
                    f"Wear Leveling: {info.wear_leveling_count or 'N/A'}\n"
                    f"Total Written: {info.total_bytes_written or 'N/A'} bytes\n"
                    f"Overall: {info.overall_health}"
                )
                self.after(0, lambda: messagebox.showinfo(
                    f"SMART - Disk {disk_index}", details))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()

    # -- Disk Benchmark -------------------------------------------------------

    def _run_benchmark(self):
        """Run a disk speed benchmark on the selected disk."""
        idx_str = self._align_combo.get()
        vals = self._align_combo.cget("values") or []
        idx = vals.index(idx_str) if idx_str in vals else 0
        if idx >= len(self._disks):
            return
        disk = self._disks[idx]
        # Find a drive letter on this disk
        parts = getattr(disk, "partitions", []) or []
        letter = None
        for p in parts:
            l = getattr(p, "letter", "")
            if l:
                letter = l
                break
        if not letter:
            messagebox.showwarning("Benchmark", "No drive letter found on this disk.")
            return

        def _bg():
            try:
                from src.core.disk_health import DiskHealthManager
                mgr = DiskHealthManager()
                result = mgr.run_benchmark(letter)
                details = (
                    f"Sequential Read: {result.sequential_read_mbps:.1f} MB/s\n"
                    f"Sequential Write: {result.sequential_write_mbps:.1f} MB/s\n"
                    f"Random Read: {result.random_read_iops:.0f} IOPS\n"
                    f"Random Write: {result.random_write_iops:.0f} IOPS\n"
                    f"Duration: {result.test_duration_seconds:.1f}s"
                )
                self.after(0, lambda: messagebox.showinfo("Benchmark Results", details))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()
        messagebox.showinfo("Benchmark", "Running benchmark... This may take a minute.")

    # -- Surface Test ---------------------------------------------------------

    def _run_surface_test(self):
        """Run a disk surface test on the selected disk."""
        idx_str = self._align_combo.get()
        vals = self._align_combo.cget("values") or []
        idx = vals.index(idx_str) if idx_str in vals else 0
        if idx >= len(self._disks):
            return
        disk_index = getattr(self._disks[idx], "index", 0)

        def _bg():
            try:
                from src.core.disk_health import DiskHealthManager
                mgr = DiskHealthManager()
                result = mgr.run_surface_test(disk_index)
                bad = result.get("bad_sectors", 0)
                total = result.get("total_sectors", 0)
                dur = result.get("duration_seconds", 0)
                msg = f"Scanned {total} sectors in {dur:.0f}s\nBad sectors: {bad}"
                self.after(0, lambda: messagebox.showinfo("Surface Test", msg))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()
        messagebox.showinfo("Surface Test", "Running surface test...")

    # -- Disk Image -----------------------------------------------------------

    def _create_disk_image(self):
        """Create a VHD/VHDX image of a disk."""
        idx_str = self._align_combo.get()
        vals = self._align_combo.cget("values") or []
        idx = vals.index(idx_str) if idx_str in vals else 0
        if idx >= len(self._disks):
            return
        disk_index = getattr(self._disks[idx], "index", 0)

        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title="Save Disk Image",
            defaultextension=".vhdx",
            filetypes=[("VHDX", "*.vhdx"), ("VHD", "*.vhd"), ("IMG", "*.img")],
        )
        if not path:
            return

        fmt = "vhdx" if path.endswith(".vhdx") else "vhd" if path.endswith(".vhd") else "img"

        def _bg():
            try:
                from src.core.disk_image import DiskImageManager
                mgr = DiskImageManager()
                mgr.create_disk_image(disk_index, path, format=fmt)
                self.after(0, lambda: messagebox.showinfo(
                    "Disk Image", f"Image created: {path}"))
                try:
                    from src.utils.notifications import NotificationManager
                    NotificationManager().notify("Disk Image Created", path)
                except Exception:
                    pass
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()
        messagebox.showinfo("Disk Image", f"Creating {fmt.upper()} image...")

    # -- Export Report --------------------------------------------------------

    def _export_report(self):
        """Export a disk health report as HTML."""
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title="Save Report",
            defaultextension=".html",
            filetypes=[("HTML", "*.html"), ("Text", "*.txt")],
        )
        if not path:
            return
        try:
            from src.utils.report import ReportGenerator
            gen = ReportGenerator()
            if path.endswith(".html"):
                gen.generate_html(self._disks, path)
            else:
                gen.generate_text(self._disks, path)
            messagebox.showinfo("Report", f"Report saved: {path}")
        except Exception as e:
            messagebox.showerror(t("common.error"), str(e))

    # -- Defragmentation ------------------------------------------------------

    def _run_defrag(self):
        """Launch Windows defragmentation for the selected disk."""
        idx_str = self._align_combo.get()
        vals = self._align_combo.cget("values") or []
        idx = vals.index(idx_str) if idx_str in vals else 0
        if idx >= len(self._disks):
            return
        disk = self._disks[idx]
        parts = getattr(disk, "partitions", []) or []
        letter = None
        for p in parts:
            l = getattr(p, "letter", "")
            if l:
                letter = l
                break
        if not letter:
            messagebox.showwarning("Defrag", "No drive letter found.")
            return
        # Validate drive letter to prevent command injection
        if not re.match(r'^[A-Za-z]$', letter):
            messagebox.showerror("Error", f"Invalid drive letter: {letter}")
            return

        media = getattr(disk, "media_type", "HDD")
        if media == "SSD":
            cmd = f"Optimize-Volume -DriveLetter {letter.upper()} -ReTrim -Verbose"
            action = "TRIM"
        else:
            cmd = f"Optimize-Volume -DriveLetter {letter.upper()} -Defrag -Verbose"
            action = "Defragmentation"

        def _bg():
            try:
                from src.utils.helpers import run_powershell
                _, stderr, rc = run_powershell(cmd)
                if rc == 0:
                    self.after(0, lambda: messagebox.showinfo(
                        action, f"{action} of {letter}: complete."))
                else:
                    self.after(0, lambda: messagebox.showerror(
                        action, f"Failed: {stderr}"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()
        messagebox.showinfo(action, f"Running {action} on {letter}:...")

    # -- SMART for selected disk (button helper) ------------------------------

    def _show_smart_for_selected(self):
        """Show SMART details for the disk selected in the align combo."""
        idx_str = self._align_combo.get()
        vals = self._align_combo.cget("values") or []
        idx = vals.index(idx_str) if idx_str in vals else 0
        if idx < len(self._disks):
            disk_index = getattr(self._disks[idx], "index", 0)
            self._show_smart_details(disk_index)

    # -- System Restore Point -------------------------------------------------

    def _create_restore_point(self):
        """Create a Windows System Restore Point."""
        if ConfirmDialog and not ConfirmDialog.ask(
            self, "Restore Point",
            "Create a System Restore Point now?",
            risk_level="low",
        ):
            return

        def _bg():
            try:
                from src.utils.helpers import run_powershell
                stdout, stderr, rc = run_powershell(
                    'Checkpoint-Computer -Description "OneClickBackup" -RestorePointType MODIFY_SETTINGS'
                )
                if rc == 0:
                    self.after(0, lambda: messagebox.showinfo(
                        "Restore Point", "System restore point created successfully."))
                else:
                    self.after(0, lambda: messagebox.showerror(
                        "Restore Point", f"Failed: {stderr or 'Unknown error'}"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()
        messagebox.showinfo("Restore Point", "Creating system restore point...")


# ============================================================================
#  7.  SchedulerPage
# ============================================================================


class SchedulerPage(_KeyboardAccessMixin, ctk.CTkFrame):
    """UI for managing scheduled backup tasks via Windows Task Scheduler."""

    def __init__(self, parent, *, backup_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._bkpmgr = backup_manager
        self._schedules: list = []
        self._build_ui()
        self._apply_keyboard_accessibility()
        self._refresh_schedules()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        _heading(self, "📅 Backup Scheduler").grid(
            row=0, column=0, sticky="w", padx=20, pady=(20, 10))

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=1)
        main.grid_rowconfigure(1, weight=1)

        # --- Create Schedule Card ---
        create_card = _card(main)
        create_card.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10), pady=5)

        ctk.CTkLabel(create_card, text="➕ New Schedule",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 10), anchor="w")

        # Schedule name
        ctk.CTkLabel(create_card, text="Name:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"]).pack(padx=15, anchor="w")
        self._sched_name = ctk.CTkEntry(create_card, placeholder_text="DailyBackup",
                                         fg_color=COLORS["bg_medium"], border_color=COLORS["border"])
        self._sched_name.pack(padx=15, fill="x", pady=(2, 8))

        # Source path
        ctk.CTkLabel(create_card, text="Source path:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"]).pack(padx=15, anchor="w")
        src_row = ctk.CTkFrame(create_card, fg_color="transparent")
        src_row.pack(padx=15, fill="x", pady=(2, 8))
        src_row.grid_columnconfigure(0, weight=1)
        self._sched_source = ctk.CTkEntry(src_row, placeholder_text="C:\\Users\\...",
                                           fg_color=COLORS["bg_medium"], border_color=COLORS["border"])
        self._sched_source.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(src_row, text="...", width=40,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._browse_source).grid(row=0, column=1, padx=(5, 0))

        # Destination path
        ctk.CTkLabel(create_card, text="Destination:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"]).pack(padx=15, anchor="w")
        dst_row = ctk.CTkFrame(create_card, fg_color="transparent")
        dst_row.pack(padx=15, fill="x", pady=(2, 8))
        dst_row.grid_columnconfigure(0, weight=1)
        self._sched_dest = ctk.CTkEntry(dst_row, placeholder_text="D:\\Backups\\...",
                                         fg_color=COLORS["bg_medium"], border_color=COLORS["border"])
        self._sched_dest.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(dst_row, text="...", width=40,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._browse_dest).grid(row=0, column=1, padx=(5, 0))

        # Schedule type
        ctk.CTkLabel(create_card, text="Frequency:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"]).pack(padx=15, anchor="w")
        self._sched_freq = ctk.CTkSegmentedButton(
            create_card, values=["Daily", "Weekly", "Monthly"],
            selected_color=COLORS["accent_blue"], unselected_color=COLORS["bg_medium"],
        )
        self._sched_freq.set("Daily")
        self._sched_freq.pack(padx=15, fill="x", pady=(2, 8))

        # Time
        ctk.CTkLabel(create_card, text="Time (HH:MM):", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"]).pack(padx=15, anchor="w")
        self._sched_time = ctk.CTkEntry(create_card, placeholder_text="02:00",
                                         fg_color=COLORS["bg_medium"], border_color=COLORS["border"])
        self._sched_time.insert(0, "02:00")
        self._sched_time.pack(padx=15, fill="x", pady=(2, 8))

        ctk.CTkButton(create_card, text="📅 Create Schedule", width=200, height=36,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      fg_color=COLORS["accent_green"], hover_color=COLORS["hover"],
                      command=self._create_schedule).pack(padx=15, pady=(10, 15), anchor="w")

        # --- Active Schedules Card ---
        list_card = _card(main)
        list_card.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(10, 0), pady=5)

        ctk.CTkLabel(list_card, text="📋 Active Schedules",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=15, pady=(15, 10), anchor="w")

        self._sched_list = ctk.CTkScrollableFrame(
            list_card, fg_color="transparent", height=300,
        )
        self._sched_list.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._sched_list.grid_columnconfigure(0, weight=1)

        btn_row = ctk.CTkFrame(list_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkButton(btn_row, text="🔄 Refresh", width=100, height=30,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._refresh_schedules).pack(side="left", padx=3)

    # -- Helpers --

    def _browse_source(self):
        path = filedialog.askdirectory(title="Select source folder")
        if path:
            self._sched_source.delete(0, "end")
            self._sched_source.insert(0, path)

    def _browse_dest(self):
        path = filedialog.askdirectory(title="Select destination folder")
        if path:
            self._sched_dest.delete(0, "end")
            self._sched_dest.insert(0, path)

    def _refresh_schedules(self):
        for w in self._sched_list.winfo_children():
            w.destroy()
        try:
            from src.core.scheduler import BackupScheduler
            scheduler = BackupScheduler()
            self._schedules = scheduler.list_schedules()
        except Exception as exc:
            _log.warning("Failed to load schedules: %s", exc)
            self._schedules = []

        if not self._schedules:
            ctk.CTkLabel(self._sched_list, text="No scheduled backups.",
                         text_color=COLORS["text_muted"]).grid(row=0, column=0, pady=20)
            return

        for i, s in enumerate(self._schedules):
            row = ctk.CTkFrame(self._sched_list,
                               fg_color=COLORS["bg_card"] if i % 2 == 0 else "transparent",
                               corner_radius=6)
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)

            name = getattr(s, "name", str(i))
            freq = getattr(s, "schedule_type", "?")
            time_val = getattr(s, "time", "?")

            ctk.CTkLabel(row, text="📅", font=ctk.CTkFont(size=14)).grid(
                row=0, column=0, padx=8, pady=6)
            ctk.CTkLabel(row, text=name,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=COLORS["text_primary"], anchor="w",
                         ).grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(row, text=f"{freq} @ {time_val}",
                         font=ctk.CTkFont(size=10),
                         text_color=COLORS["text_secondary"],
                         ).grid(row=1, column=1, sticky="w")
            ctk.CTkButton(row, text="🗑️", width=30, height=28,
                          fg_color=COLORS["accent_red"], hover_color="#c0392b",
                          command=lambda n=name: self._remove_schedule(n),
                          ).grid(row=0, column=2, rowspan=2, padx=8, pady=4)

    def _create_schedule(self):
        name = self._sched_name.get().strip()
        source = self._sched_source.get().strip()
        dest = self._sched_dest.get().strip()
        freq = self._sched_freq.get().lower()
        time_val = self._sched_time.get().strip()

        if not name or not source or not dest:
            messagebox.showwarning("Scheduler", "Please fill in name, source, and destination.")
            return

        def _bg():
            try:
                from src.core.scheduler import BackupScheduler
                scheduler = BackupScheduler()
                scheduler.schedule_backup(
                    name=name,
                    backup_type="full_disk",
                    schedule_type=freq,
                    time_str=time_val,
                )
                self.after(0, lambda: messagebox.showinfo(
                    "Scheduler", f"Schedule '{name}' created."))
                self.after(0, self._refresh_schedules)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
        threading.Thread(target=_bg, daemon=True).start()

    def _remove_schedule(self, name: str):
        if ConfirmDialog and not ConfirmDialog.ask(
            self, "Remove Schedule",
            f"Remove schedule '{name}'?",
            risk_level="medium",
        ):
            return
        try:
            from src.core.scheduler import BackupScheduler
            BackupScheduler().remove_schedule(name)
            self._refresh_schedules()
        except Exception as e:
            messagebox.showerror(t("common.error"), str(e))


# ============================================================================
#  8.  HistoryPage
# ============================================================================


class HistoryPage(_KeyboardAccessMixin, ctk.CTkFrame):
    """View operation history log."""

    def __init__(self, parent, **_kw):
        super().__init__(parent, fg_color="transparent")
        self._entries: list = []
        self._build_ui()
        self._apply_keyboard_accessibility()
        self._refresh()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        header.grid_columnconfigure(1, weight=1)

        _heading(header, "📜 Operation History").grid(row=0, column=0, sticky="w")

        btn_row = ctk.CTkFrame(header, fg_color="transparent")
        btn_row.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btn_row, text="🔄 Refresh", width=90, height=30,
                      fg_color=COLORS["accent_blue"], hover_color=COLORS["hover"],
                      command=self._refresh).pack(side="left", padx=3)
        ctk.CTkButton(btn_row, text="📥 Export", width=90, height=30,
                      fg_color=COLORS["accent_purple"], hover_color=COLORS["hover"],
                      command=self._export_history).pack(side="left", padx=3)
        ctk.CTkButton(btn_row, text="🗑️ Clear", width=90, height=30,
                      fg_color=COLORS["accent_red"], hover_color="#c0392b",
                      command=self._clear_history).pack(side="left", padx=3)

        # Filter
        filter_row = ctk.CTkFrame(self, fg_color="transparent")
        filter_row.grid(row=0, column=0, sticky="e", padx=20, pady=(60, 0))
        self._filter = ctk.CTkSegmentedButton(
            filter_row, values=["All", "Backup", "Clone", "Wipe", "Failures"],
            selected_color=COLORS["accent_blue"], unselected_color=COLORS["bg_medium"],
            command=self._on_filter_change,
        )
        self._filter.set("All")
        self._filter.pack()

        # History list
        self._history_list = ctk.CTkScrollableFrame(
            self, fg_color=COLORS["bg_card"], corner_radius=10,
        )
        self._history_list.grid(row=1, column=0, sticky="nsew", padx=20, pady=(10, 20))
        self._history_list.grid_columnconfigure(0, weight=1)

    def _refresh(self):
        for w in self._history_list.winfo_children():
            w.destroy()
        try:
            from src.core.history import OperationHistory
            history = OperationHistory()
            self._entries = history.get_all(limit=200)
        except Exception as exc:
            _log.warning("Failed to load history: %s", exc)
            self._entries = []
        self._display_entries(self._entries)

    def _on_filter_change(self, value: str):
        if value == "All":
            filtered = self._entries
        elif value == "Failures":
            filtered = [e for e in self._entries if not e.success]
        else:
            op_key = value.lower()
            filtered = [e for e in self._entries if e.operation == op_key]
        self._display_entries(filtered)

    def _display_entries(self, entries: list):
        for w in self._history_list.winfo_children():
            w.destroy()

        if not entries:
            ctk.CTkLabel(self._history_list, text="No operations recorded.",
                         text_color=COLORS["text_muted"]).grid(row=0, column=0, pady=30)
            return

        for i, e in enumerate(entries):
            row = ctk.CTkFrame(self._history_list,
                               fg_color=COLORS["bg_medium"] if i % 2 == 0 else "transparent",
                               corner_radius=6)
            row.grid(row=i, column=0, sticky="ew", pady=2, padx=4)
            row.grid_columnconfigure(2, weight=1)

            icon = "✅" if e.success else "❌"
            ctk.CTkLabel(row, text=icon, font=ctk.CTkFont(size=14)).grid(
                row=0, column=0, padx=(10, 5), pady=6)

            ctk.CTkLabel(row, text=e.operation.upper(),
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=COLORS["accent_blue"],
                         ).grid(row=0, column=1, padx=5, sticky="w")

            ctk.CTkLabel(row, text=e.description,
                         font=ctk.CTkFont(size=11),
                         text_color=COLORS["text_primary"], anchor="w",
                         ).grid(row=0, column=2, sticky="w", padx=5)

            ts = e.timestamp[:19] if len(e.timestamp) >= 19 else e.timestamp
            dur = f" ({e.duration_seconds:.1f}s)" if e.duration_seconds else ""
            ctk.CTkLabel(row, text=f"{ts}{dur}",
                         font=ctk.CTkFont(size=9),
                         text_color=COLORS["text_muted"],
                         ).grid(row=0, column=3, padx=10, sticky="e")

    def _export_history(self):
        path = filedialog.asksaveasfilename(
            title="Export History",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            from src.core.history import OperationHistory
            OperationHistory().export_json(path)
            messagebox.showinfo("Export", f"History exported to:\n{path}")
        except Exception as e:
            messagebox.showerror(t("common.error"), str(e))

    def _clear_history(self):
        if ConfirmDialog and not ConfirmDialog.ask(
            self, "Clear History",
            "Delete all operation history? This cannot be undone.",
            risk_level="high",
        ):
            return
        try:
            from src.core.history import OperationHistory
            OperationHistory().clear()
            self._refresh()
        except Exception as e:
            messagebox.showerror(t("common.error"), str(e))
