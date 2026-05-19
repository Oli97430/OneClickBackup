"""Feature pages for OneClick Backup & Disk Manager.

Each class is a CTkFrame that gets swapped into the main content area.
Pages accept optional *operation_manager* and/or *backup_manager*
constructor arguments so they can queue / execute real operations.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any, Callable, Optional

import customtkinter as ctk

from src.utils.i18n import t

# ---------------------------------------------------------------------------
# Project imports (with graceful fallbacks)
# ---------------------------------------------------------------------------

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    from src.ui.widgets import COLORS, format_bytes, ConfirmDialog, ProgressDialog, DiskBar
except ImportError:
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

    def format_bytes(n: int) -> str:
        for u in ("B", "KB", "MB", "GB", "TB"):
            if abs(n) < 1024:
                return f"{n:.1f} {u}"
            n /= 1024  # type: ignore[assignment]
        return f"{n:.1f} PB"

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
                self._disks = _load_disks()
                self.after(0, self._on_disks_loaded)  # type: ignore[attr-defined]
            except Exception as exc:
                _log.exception("Background disk refresh failed: %s", exc)
                self.after(0, lambda: messagebox.showerror(  # type: ignore[attr-defined]
                    t("common.error"), str(exc)))
        threading.Thread(target=_bg, daemon=True).start()

    def _on_disks_loaded(self) -> None:
        """Override in subclass to populate UI after disks are loaded."""
        raise NotImplementedError


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
#  1.  ClonePage
# ============================================================================

class ClonePage(_DiskPageMixin, ctk.CTkFrame):
    """Disk cloning and OS migration interface."""

    def __init__(self, parent, *, operation_manager=None, backup_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._opmgr = operation_manager
        self._bkpmgr = backup_manager
        self._disks: list = []
        self._build_ui()
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

    # -- Logic -------------------------------------------------------------

    def _refresh_disks(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self):
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

class PartitionPage(_DiskPageMixin, ctk.CTkFrame):
    """Partition management interface."""

    def __init__(self, parent, *, operation_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._opmgr = operation_manager
        self._disks: list = []
        self._selected_part = None
        self._build_ui()
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

    # -- Data --------------------------------------------------------------

    def _refresh_disks(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self):
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
        messagebox.showinfo(t("part.merge"), t("part.merge_msg"))

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

class ConversionPage(_DiskPageMixin, ctk.CTkFrame):
    """Disk and partition conversion tools."""

    def __init__(self, parent, *, operation_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._opmgr = operation_manager
        self._disks: list = []
        self._build_ui()
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

    def _refresh(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self):
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

class BackupPage(_DiskPageMixin, ctk.CTkFrame):
    """Backup and restore interface with tabs."""

    def __init__(self, parent, *, backup_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._bkpmgr = backup_manager
        self._disks: list = []
        self._build_ui()
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
        }
        self._bk_type = ctk.CTkSegmentedButton(
            tab_create,
            values=[self._BACKUP_TYPE_MAP["full_disk"],
                    self._BACKUP_TYPE_MAP["partition"],
                    self._BACKUP_TYPE_MAP["system"]],
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
        self._bk_verify.grid(row=8, column=0, sticky="w", padx=10, pady=8)

        ctk.CTkButton(tab_create, text=t("bak.create_btn"), width=200, height=40,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      fg_color=COLORS["accent_green"], hover_color=COLORS["hover"],
                      command=self._create_backup).grid(row=9, column=0, pady=15)

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

    # -- Helpers -----------------------------------------------------------

    def _refresh(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self):
        self._populate_sources()

    def _populate_sources(self):
        names = [_disk_display(d) for d in self._disks] or [t("common.no_disks")]
        self._bk_source.configure(values=names)
        if names:
            self._bk_source.set(names[0])

    def _browse_dest(self):
        path = filedialog.askdirectory(title=t("bak.dest_title"))
        if path:
            self._bk_dest.delete(0, "end")
            self._bk_dest.insert(0, path)

    def _refresh_backup_list(self):
        for w in self._backup_list.winfo_children():
            w.destroy()
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
                               corner_radius=6)
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=2)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="📦", font=ctk.CTkFont(size=16)).grid(row=0, column=0, padx=8, pady=6)
            ctk.CTkLabel(row, text=getattr(b, "name", "Backup"),
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=COLORS["text_primary"], anchor="w",
                         ).grid(row=0, column=1, sticky="w")
            info = f"{getattr(b, 'timestamp', '')} | {getattr(b, 'backup_type', '')} | {format_bytes(getattr(b, 'total_size_bytes', 0))}"
            ctk.CTkLabel(row, text=info, font=ctk.CTkFont(size=10),
                         text_color=COLORS["text_secondary"], anchor="w",
                         ).grid(row=1, column=1, sticky="w", padx=(0, 10))

    def _create_backup(self):
        name = self._bk_name.get().strip() or "Backup"
        dest = self._bk_dest.get().strip()
        if not dest:
            messagebox.showwarning(t("bak.dest"), t("bak.no_dest"))
            return
        btype_display = self._bk_type.get()
        # Resolve display label back to language-neutral key
        _reverse_map = {v: k for k, v in self._BACKUP_TYPE_MAP.items()}
        btype_key = _reverse_map.get(btype_display, "partition")
        messagebox.showinfo(t("bak.started_title"), t("bak.started", type=btype_display, name=name, dest=dest))
        if self._bkpmgr:
            def _bg():
                try:
                    if btype_key == "system":
                        self._bkpmgr.create_system_backup(destination_path=dest, name=name)
                    elif btype_key == "full_disk":
                        self._bkpmgr.create_full_disk_backup(disk_index=0, name=name)
                    else:
                        self._bkpmgr.create_partition_backup(disk_index=0, partition_index=1, name=name)
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror(t("common.error"), str(e)))
                self.after(0, self._refresh_backup_list)
            threading.Thread(target=_bg, daemon=True).start()

    def _restore_backup(self):
        messagebox.showinfo(t("bak.tab_restore"), t("bak.restore_info"))

    def _verify_backup(self):
        messagebox.showinfo(t("bak.verify_btn"), t("bak.verify_info"))

    def _delete_backup(self):
        messagebox.showinfo(t("bak.delete_btn"), t("bak.delete_info"))


# ============================================================================
#  5.  RecoveryPage
# ============================================================================

class RecoveryPage(_DiskPageMixin, ctk.CTkFrame):
    """Partition recovery wizard."""

    def __init__(self, parent, **_kw):
        super().__init__(parent, fg_color="transparent")
        self._disks: list = []
        self._step = 0
        self._found_parts: list = []
        self._build_ui()
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

    def _refresh(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self):
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
        self._simulate_scan()

    def _simulate_scan(self, pct: float = 0.0):
        if pct >= 1.0:
            self._scan_progress.set(1.0)
            self._scan_status.configure(text=t("rec.scan_done"))
            self._next_btn.configure(state="normal")
            self._found_parts = [
                {"index": 5, "fs": "NTFS", "size": "120 GB", "status": t("rec.recoverable")},
                {"index": 6, "fs": "FAT32", "size": "32 GB", "status": t("rec.partial")},
            ]
            return
        self._scan_progress.set(pct)
        self._scan_status.configure(text=t("rec.scan_pct", pct=int(pct * 100)))
        self.after(80, lambda: self._simulate_scan(pct + 0.02))

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
                          command=lambda pp=p: messagebox.showinfo(t("rec.recover"),
                              t("rec.recover_started", idx=pp['index'])),
                          ).grid(row=0, column=2, padx=12, pady=8)

        self._next_btn.configure(text=t("rec.done"), command=lambda: self._show_step(0))

    def _go_back(self):
        if self._step > 0:
            self._show_step(self._step - 1)

    def _go_next(self):
        if self._step < 3:
            self._show_step(self._step + 1)


# ============================================================================
#  6.  AdvancedPage
# ============================================================================

class AdvancedPage(_DiskPageMixin, ctk.CTkFrame):
    """Advanced tools: 4K alignment, WinPE, disk health, secure wipe."""

    def __init__(self, parent, *, operation_manager=None, backup_manager=None):
        super().__init__(parent, fg_color="transparent")
        self._opmgr = operation_manager
        self._bkpmgr = backup_manager
        self._disks: list = []
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

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

    def _refresh(self):
        self._refresh_disks_bg()

    def _on_disks_loaded(self):
        self._populate()

    def _populate(self):
        names = [_disk_display(d) for d in self._disks] or [t("common.no_disks")]
        self._align_combo.configure(values=names)
        self._wipe_combo.configure(values=names)
        if names:
            self._align_combo.set(names[0])
            self._wipe_combo.set(names[0])

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
        messagebox.showinfo(t("adv.wipe_title"), t("adv.wipe_started"))
