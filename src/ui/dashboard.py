"""
dashboard.py - Main dashboard page for the disk management application.

Provides an overview of all physical disks and partitions with visual
representations, summary statistics, and a detail panel for the
currently selected partition.
"""

from __future__ import annotations

import logging
import customtkinter as ctk
import tkinter as tk
from typing import Callable, Optional
import threading

_log = logging.getLogger(__name__)

# Import from sibling modules
try:
    from src.core.disk_info import get_all_disks, DiskInfo, PartitionInfo
except ImportError:
    get_all_disks = None
    DiskInfo = None
    PartitionInfo = None

try:
    from src.utils.i18n import t
except ImportError:
    def t(key, **kw): return key

# Import shared widgets and utilities (single source of truth)
from src.ui.widgets import (
    COLORS, _lighten, _health_color,
    format_bytes,
)


def _usage_percent(used: int | float, total: int | float) -> float:
    """Return usage as a percentage clamped to [0, 100]."""
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, (used / total) * 100.0))


def _fs_color(file_system: str, partition_type: str = "") -> str:
    """Return the legend color for a given filesystem / partition type."""
    fs = file_system.upper() if file_system else ""
    pt = partition_type.lower() if partition_type else ""
    if "efi" in pt or "efi" in fs:
        return COLORS["efi_color"]
    if "recovery" in pt or "recovery" in fs:
        return COLORS["recovery_color"]
    if fs == "NTFS":
        return COLORS["ntfs_color"]
    if fs == "FAT32":
        return COLORS["fat32_color"]
    if fs == "EXFAT":
        return COLORS["exfat_color"]
    if not fs:
        return COLORS["unallocated_color"]
    return COLORS["other_fs_color"]


class DiskBar(ctk.CTkFrame):
    """A horizontal bar that visualises partition layout on a single disk.

    Each partition is drawn as a proportionally-sized colored segment
    with a subtle vertical gradient for a polished, instrument-panel feel.
    """

    _SEG_GAP = 2  # pixel gap between segments

    def __init__(
        self,
        parent,
        disk_info,
        on_partition_click: Callable[[int, object], None] | None = None,
        bar_height: int = 32,
        **kwargs,
    ):
        super().__init__(parent, fg_color=COLORS["bg_bar"], corner_radius=6,
                         height=bar_height, **kwargs)
        self.grid_propagate(False)
        self._disk_info = disk_info
        self._on_partition_click = on_partition_click
        self._bar_height = bar_height

        self._canvas = tk.Canvas(
            self, height=bar_height, bg=COLORS["bg_bar"],
            highlightthickness=0, bd=0,
        )
        self._canvas.pack(fill="both", expand=True, padx=2, pady=2)
        self._canvas.bind("<Configure>", lambda _event: self._draw())

    # ---- drawing --------------------------------------------------------

    def _draw(self, no_color_updates: bool = False, **kwargs):  # type: ignore[override]
        """Redraw the partition segments on the canvas."""
        self._canvas.delete("all")
        width = self._canvas.winfo_width()
        height = self._canvas.winfo_height()
        if width <= 1:
            return

        total_bytes = getattr(self._disk_info, "size_bytes", 0) or 0
        if total_bytes <= 0:
            self._canvas.create_rectangle(
                0, 0, width, height,
                fill=COLORS["unallocated_color"], outline="",
            )
            return

        partitions = getattr(self._disk_info, "partitions", []) or []
        unallocated = getattr(self._disk_info, "unallocated_bytes", 0) or 0
        gap = self._SEG_GAP

        x = 0
        min_seg = 4

        for idx, part in enumerate(partitions):
            part_size = getattr(part, "size_bytes", 0) or 0
            seg_w = max(min_seg, int((part_size / total_bytes) * width))
            seg_w = min(seg_w, width - int(x))

            fs = getattr(part, "file_system", "") or ""
            pt = getattr(part, "partition_type", "") or ""
            color = _fs_color(fs, pt)
            lighter = _lighten(color, 0.20)
            mid_y = height // 2

            # Gradient: lighter top half, true color bottom half
            ix = int(x)
            self._canvas.create_rectangle(
                ix, 0, ix + seg_w, mid_y,
                fill=lighter, outline="",
            )
            rect_id = self._canvas.create_rectangle(
                ix, mid_y - 1, ix + seg_w, height,
                fill=color, outline="",
            )

            # 1px gloss highlight at the very top
            if seg_w > 6:
                self._canvas.create_line(
                    ix + 2, 1, ix + seg_w - 2, 1,
                    fill=_lighten(color, 0.45), width=1,
                )

            # Label inside the segment
            letter = getattr(part, "letter", "") or ""
            if seg_w > 40 and letter:
                self._canvas.create_text(
                    ix + seg_w // 2, height // 2,
                    text=f"{letter}:",
                    fill="#ffffff",
                    font=("Consolas", 9, "bold"),
                )
            elif seg_w > 25 and fs:
                self._canvas.create_text(
                    ix + seg_w // 2, height // 2,
                    text=fs[:4],
                    fill="#ffffff",
                    font=("Consolas", 7),
                )

            # Bind click
            click_fn = self._on_partition_click
            if click_fn is not None:
                disk_index = getattr(self._disk_info, "index", 0)
                self._canvas.tag_bind(
                    rect_id, "<Button-1>",
                    lambda e, di=disk_index, pi=part, fn=click_fn: fn(di, pi),
                )

            x += seg_w + gap

        # Remaining unallocated space
        if x < width:
            self._canvas.create_rectangle(
                int(x), 0, width, height,
                fill=COLORS["unallocated_color"], outline="",
            )


# ===================================================================
# Widget: DiskCard -- summary card for a single physical disk
# ===================================================================

class DiskCard(ctk.CTkFrame):
    """A card that displays summary information for one physical disk,
    including a DiskBar for visual partition layout and an optional
    selection checkbox (#29 multi-disk selection)."""

    def __init__(
        self,
        parent,
        disk_info,
        on_partition_click: Callable[[int, object], None] | None = None,
        selectable: bool = False,
        on_selection_change: Callable[[int, bool], None] | None = None,
        **kwargs,
    ):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=8,
                         border_width=1, border_color=COLORS["border"], **kwargs)
        self._disk_info = disk_info
        self._partition_click_cb = on_partition_click
        self._selectable = selectable
        self._on_selection_change = on_selection_change
        self._selected = False
        self.grid_columnconfigure(0, weight=0, minsize=3)
        self.grid_columnconfigure(1, weight=1)
        self._build()

    def _build(self):
        d = self._disk_info
        disk_index = getattr(d, "index", 0)
        model = getattr(d, "model", "Unknown Disk") or "Unknown Disk"
        size_bytes = getattr(d, "size_bytes", 0) or 0
        media_type = getattr(d, "media_type", "Unknown") or "Unknown"
        interface = getattr(d, "interface_type", "Unknown") or "Unknown"
        partition_style = getattr(d, "partition_style", "Unknown") or "Unknown"
        health = getattr(d, "health_status", "Unknown") or "Unknown"
        is_system = getattr(d, "is_system_disk", False)
        aligned = getattr(d, "is_4k_aligned", True)

        # Left accent stripe — colored by disk type
        stripe_color = COLORS["accent_blue"] if media_type == "SSD" else COLORS["accent_purple"]
        accent = ctk.CTkFrame(self, width=3, fg_color=stripe_color, corner_radius=0)
        accent.grid(row=0, column=0, rowspan=4, sticky="ns")

        # ---- Row 0: Header row ----
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=1, sticky="ew", padx=14, pady=(10, 2))
        header.grid_columnconfigure(2, weight=1)

        col = 0
        # Selection checkbox (#29)
        if self._selectable:
            self._check_var = ctk.BooleanVar(value=False)
            self._checkbox = ctk.CTkCheckBox(
                header, text="", width=24,
                variable=self._check_var,
                fg_color=COLORS["accent_blue"],
                command=self._on_check_toggle,
            )
            self._checkbox.grid(row=0, column=col, padx=(0, 6))
            col += 1

        # Disk icon
        icon_text = "⚡" if media_type == "SSD" else "\U0001f4bd"
        ctk.CTkLabel(
            header, text=icon_text, font=ctk.CTkFont(size=20),
            text_color=COLORS["text_muted"],
        ).grid(row=0, column=col, padx=(0, 10))

        # Name + badge row — geometric display font
        name_frame = ctk.CTkFrame(header, fg_color="transparent")
        name_frame.grid(row=0, column=col + 1, sticky="w")

        ctk.CTkLabel(
            name_frame,
            text=f"Disk {disk_index}: {model}",
            font=("Bahnschrift SemiBold", 14),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        if is_system:
            ctk.CTkLabel(
                name_frame, text=" SYSTEM ",
                font=("Consolas", 9, "bold"),
                text_color=COLORS["bg_dark"],
                fg_color=COLORS["accent_yellow"],
                corner_radius=3,
                padx=4,
            ).pack(side="left", padx=(8, 0))

        # Right side: size (monospace) + health badge
        info_frame = ctk.CTkFrame(header, fg_color="transparent")
        info_frame.grid(row=0, column=col + 2, sticky="e")

        ctk.CTkLabel(
            info_frame, text=format_bytes(size_bytes),
            font=("Consolas", 13, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=(0, 12))

        health_fg = _health_color(health)
        ctk.CTkLabel(
            info_frame, text=f"● {health}",
            font=("Consolas", 10),
            text_color=health_fg,
        ).pack(side="left")

        # ---- Row 1: Metadata tags ----
        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=1, column=1, sticky="w", padx=18, pady=(0, 4))

        tags = [media_type, interface, partition_style]
        if not aligned:
            tags.append("Not 4K-aligned")
        # RAID info (#21)
        raid_type = getattr(d, "raid_type", "") or ""
        if raid_type:
            tags.append(f"RAID {raid_type}")
        # BitLocker status
        is_bitlocker = getattr(d, "is_bitlocker", False)
        if is_bitlocker:
            tags.append("🔒 BitLocker")
        # Temperature
        temp = getattr(d, "temperature_celsius", None)
        if temp is not None:
            tags.append(f"{temp}°C")
        # Firmware
        fw = getattr(d, "firmware_version", "") or ""
        if fw:
            tags.append(f"FW: {fw}")
        for tag_text in tags:
            tag = ctk.CTkLabel(
                meta, text=tag_text,
                font=("Consolas", 10),
                text_color=COLORS["text_muted"],
                fg_color=COLORS["bg_bar"],
                corner_radius=3,
                padx=6, pady=1,
            )
            tag.pack(side="left", padx=(0, 6))

        # ---- Row 2: DiskBar ----
        bar = DiskBar(
            self, disk_info=d,
            on_partition_click=self._handle_partition_click,
            bar_height=30,
        )
        bar.grid(row=2, column=1, sticky="ew", padx=14, pady=(4, 4))

        # ---- Row 3: Partition summary text ----
        partitions = getattr(d, "partitions", []) or []
        unalloc = getattr(d, "unallocated_bytes", 0) or 0
        summary_parts = []
        for p in partitions:
            letter = getattr(p, "letter", "") or ""
            fs = getattr(p, "file_system", "") or ""
            sz = getattr(p, "size_bytes", 0) or 0
            label = f"{letter}:" if letter else fs if fs else "Partition"
            summary_parts.append(f"{label} {format_bytes(sz)}")
        if unalloc > 0:
            summary_parts.append(f"Unallocated {format_bytes(unalloc)}")

        if summary_parts:
            summary_text = "  │  ".join(summary_parts)
            ctk.CTkLabel(
                self, text=summary_text,
                font=("Consolas", 10),
                text_color=COLORS["text_secondary"],
            ).grid(row=3, column=1, sticky="w", padx=18, pady=(0, 10))

    def _on_check_toggle(self):
        """Handle selection checkbox toggle."""
        self._selected = self._check_var.get()
        disk_index = getattr(self._disk_info, "index", 0)
        # Visual feedback: highlight card border when selected
        border_color = COLORS["accent_blue"] if self._selected else COLORS["border"]
        self.configure(border_color=border_color, border_width=2 if self._selected else 1)
        if self._on_selection_change:
            self._on_selection_change(disk_index, self._selected)

    @property
    def is_selected(self) -> bool:
        return self._selected

    @property
    def disk_index(self) -> int:
        return getattr(self._disk_info, "index", 0)

    def _handle_partition_click(self, disk_index: int, partition_info):
        """Relay partition click to the registered callback."""
        if self._partition_click_cb is not None:
            self._partition_click_cb(disk_index, partition_info)


# ===================================================================
# Widget: PartitionDetailPanel -- right-side panel for one partition
# ===================================================================

class PartitionDetailPanel(ctk.CTkFrame):
    """Displays detailed information about a selected partition,
    with action buttons for common operations."""

    def __init__(
        self,
        parent,
        partition_info,
        disk_index: int = 0,
        on_action: Callable[[str, int, object], None] | None = None,
        **kwargs,
    ):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=8, **kwargs)
        self._partition = partition_info
        self._disk_index = disk_index
        self._on_action = on_action
        self.grid_columnconfigure(0, weight=1)
        self._build()

    def _build(self):
        p = self._partition
        letter = getattr(p, "letter", "") or ""
        label = getattr(p, "label", "") or ""
        fs = getattr(p, "file_system", "") or ""
        size = getattr(p, "size_bytes", 0) or 0
        used = getattr(p, "used_bytes", 0) or 0
        free = getattr(p, "free_bytes", 0) or 0
        pt = getattr(p, "partition_type", "") or ""
        is_boot = getattr(p, "is_boot", False)
        is_system = getattr(p, "is_system", False)
        is_active = getattr(p, "is_active", False)
        offset = getattr(p, "offset_bytes", 0) or 0
        part_index = getattr(p, "index", 0) or 0

        row = 0

        # ---- Accent header line ----
        fs_color = _fs_color(fs, pt)
        ctk.CTkFrame(
            self, height=3, fg_color=fs_color, corner_radius=0,
        ).grid(row=row, column=0, sticky="ew")
        row += 1

        # ---- Title ----
        title = f"{letter}:" if letter else f"Partition {part_index}"
        if label:
            title += f" ({label})"

        ctk.CTkLabel(
            self, text=title,
            font=("Bahnschrift SemiBold", 16),
            text_color=COLORS["text_primary"],
        ).grid(row=row, column=0, sticky="w", padx=15, pady=(12, 5))
        row += 1

        # ---- Filesystem badge ----
        fs_display = fs if fs else "Unknown"
        ctk.CTkLabel(
            self, text=f"● {fs_display}",
            font=("Consolas", 12, "bold"),
            text_color=fs_color,
        ).grid(row=row, column=0, sticky="w", padx=15, pady=(0, 8))
        row += 1

        # ---- Usage bar ----
        if size > 0 and (used > 0 or free > 0):
            pct = _usage_percent(used, size)

            bar_bg = ctk.CTkFrame(self, fg_color=COLORS["bg_bar"],
                                  corner_radius=3, height=12)
            bar_bg.grid(row=row, column=0, sticky="ew", padx=15, pady=(0, 2))
            bar_bg.grid_propagate(False)

            bar_fill_color = (
                COLORS["accent_green"] if pct < 75
                else COLORS["accent_orange"] if pct < 90
                else COLORS["accent_red"]
            )
            bar_fill = ctk.CTkFrame(bar_bg, fg_color=bar_fill_color,
                                    corner_radius=3, height=12)
            bar_fill.place(relx=0, rely=0, relwidth=max(pct / 100.0, 0.01),
                           relheight=1.0)
            row += 1

            ctk.CTkLabel(
                self, text=f"{pct:.1f}% used",
                font=("Consolas", 11),
                text_color=COLORS["text_secondary"],
            ).grid(row=row, column=0, sticky="w", padx=15, pady=(0, 8))
            row += 1

        # ---- Info table ----
        info_pairs = [
            ("Total Size", format_bytes(size)),
            ("Used", format_bytes(used)),
            ("Free", format_bytes(free)),
            ("Type", pt if pt else "N/A"),
            ("Offset", format_bytes(offset)),
        ]

        flags = []
        if is_boot:
            flags.append("Boot")
        if is_system:
            flags.append("System")
        if is_active:
            flags.append("Active")
        if flags:
            info_pairs.append(("Flags", ", ".join(flags)))

        for lbl_text, val_text in info_pairs:
            pair_frame = ctk.CTkFrame(self, fg_color="transparent")
            pair_frame.grid(row=row, column=0, sticky="ew", padx=15, pady=1)
            pair_frame.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                pair_frame, text=lbl_text,
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"],
                anchor="w", width=80,
            ).grid(row=0, column=0, sticky="w")

            ctk.CTkLabel(
                pair_frame, text=val_text,
                font=("Consolas", 11, "bold"),
                text_color=COLORS["text_primary"],
                anchor="e",
            ).grid(row=0, column=1, sticky="e")
            row += 1

        # ---- Separator ----
        sep = ctk.CTkFrame(self, fg_color=COLORS["border"], height=1)
        sep.grid(row=row, column=0, sticky="ew", padx=15, pady=10)
        row += 1

        # ---- Action buttons ----
        actions = [
            ("Resize", "resize", COLORS["accent_blue"]),
            ("Format", "format", COLORS["accent_orange"]),
            ("Change Letter", "change_letter", COLORS["accent_purple"]),
            ("Delete", "delete", COLORS["accent_red"]),
        ]

        for btn_text, action_key, btn_color in actions:
            btn = ctk.CTkButton(
                self, text=btn_text, width=120, height=28,
                fg_color=btn_color,
                hover_color=COLORS["hover"],
                font=("Bahnschrift", 11),
                command=lambda a=action_key: self._fire_action(a),
            )
            btn.grid(row=row, column=0, padx=15, pady=3, sticky="ew")
            row += 1

    def _fire_action(self, action: str):
        if self._on_action is not None:
            self._on_action(action, self._disk_index, self._partition)


# ===================================================================
# DashboardPage -- the main composite page
# ===================================================================

class DashboardPage(ctk.CTkFrame):
    """Main dashboard showing an overview of all physical disks."""

    def __init__(
        self,
        parent,
        on_operation_requested: Callable[[str, int, object], None] | None = None,
    ):
        super().__init__(parent, fg_color="transparent")
        self._disks: list = []
        self._selected_disk_index: int = -1
        self._selected_partition_index: int = -1
        self._selected_disks: set[int] = set()  # #29 multi-disk selection
        self._disk_cards: list[DiskCard] = []
        self._on_operation_requested = on_operation_requested

        self._build_ui()
        self._setup_drag_drop()
        self.refresh_data()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Build the dashboard layout."""
        # Grid: main content (left) + detail panel (right)
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- TOP: Summary cards row ---
        self._summary_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._summary_frame.grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(15, 5),
        )

        self._summary_cards: dict[str, ctk.CTkFrame] = {}
        cards_data = [
            ("disks", "\U0001f4bd", "0", t("dash.total_disks")),
            ("storage", "\U0001f4ca", "0 GB", t("dash.total_storage")),
            ("ssd", "⚡", "0", t("dash.ssds")),
            ("hdd", "\U0001f504", "0", t("dash.hdds")),
        ]
        for i, (key, icon, value, label) in enumerate(cards_data):
            self._summary_frame.grid_columnconfigure(i, weight=1)
            card = self._create_summary_card(self._summary_frame, icon, value, label)
            card.grid(row=0, column=i, padx=5, sticky="ew")
            self._summary_cards[key] = card

        # --- MIDDLE: Disk list (scrollable) ---
        self._disk_list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS["bg_dark"],
            label_text=t("dash.overview"),
            label_fg_color=COLORS["bg_medium"],
            label_text_color=COLORS["text_primary"],
            label_font=("Bahnschrift SemiBold", 13),
        )
        self._disk_list_frame.grid(
            row=1, column=0, sticky="nsew", padx=(15, 5), pady=10,
        )
        self._disk_list_frame.grid_columnconfigure(0, weight=1)

        # --- RIGHT: Detail panel ---
        self._detail_frame = ctk.CTkFrame(
            self, fg_color=COLORS["bg_card"], corner_radius=8,
            border_width=1, border_color=COLORS["border"],
        )
        self._detail_frame.grid(
            row=1, column=1, sticky="nsew", padx=(5, 15), pady=10,
        )
        self._detail_frame.grid_columnconfigure(0, weight=1)

        # Placeholder text when nothing is selected
        self._detail_placeholder: Optional[ctk.CTkLabel] = ctk.CTkLabel(
            self._detail_frame,
            text=t("dash.select_partition"),
            text_color=COLORS["text_muted"],
            font=ctk.CTkFont(size=14),
        )
        self._detail_placeholder.grid(row=0, column=0, padx=20, pady=40)
        self._partition_detail: Optional[PartitionDetailPanel] = None

        # --- BOTTOM: Legend + Refresh ---
        self._bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._bottom_frame.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=15, pady=(0, 10),
        )

        legend_items = [
            ("NTFS", COLORS["ntfs_color"]),
            ("FAT32", COLORS["fat32_color"]),
            ("exFAT", COLORS["exfat_color"]),
            ("EFI", COLORS["efi_color"]),
            ("Recovery", COLORS["recovery_color"]),
            ("Unallocated", COLORS["unallocated_color"]),
        ]
        for i, (name, color) in enumerate(legend_items):
            dot = ctk.CTkLabel(
                self._bottom_frame, text="●", text_color=color,
                font=ctk.CTkFont(size=12),
            )
            dot.grid(row=0, column=i * 2, padx=(10 if i == 0 else 5, 2))
            lbl = ctk.CTkLabel(
                self._bottom_frame, text=name,
                text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(size=11),
            )
            lbl.grid(row=0, column=i * 2 + 1, padx=(0, 10))

        # Refresh button
        refresh_col = len(legend_items) * 2
        self._refresh_btn = ctk.CTkButton(
            self._bottom_frame, text=t("dash.refresh"), width=110,
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["hover"],
            font=("Bahnschrift", 11),
            corner_radius=6,
            command=self.refresh_data,
        )
        self._refresh_btn.grid(row=0, column=refresh_col, padx=10, sticky="e")
        self._bottom_frame.grid_columnconfigure(refresh_col, weight=1)

    # ------------------------------------------------------------------
    # Summary card helper
    # ------------------------------------------------------------------

    def _create_summary_card(
        self, parent, icon: str, value: str, label: str,
    ) -> ctk.CTkFrame:
        """Create a single summary card widget.

        The returned frame has two extra attributes for later updates:
            ``_value_label``  -- the CTkLabel displaying the numeric value
            ``_label_label``  -- the CTkLabel displaying the description
        """
        frame = ctk.CTkFrame(
            parent, fg_color=COLORS["bg_card"], corner_radius=8, height=82,
            border_width=1, border_color=COLORS["border"],
        )
        frame.grid_propagate(False)
        frame.grid_columnconfigure(1, weight=1)

        # Accent top stripe
        accent_line = ctk.CTkFrame(
            frame, height=2, fg_color=COLORS["accent_blue"], corner_radius=0,
        )
        accent_line.grid(row=0, column=0, columnspan=2, sticky="ew")

        icon_lbl = ctk.CTkLabel(frame, text=icon, font=ctk.CTkFont(size=24),
                                text_color=COLORS["text_muted"])
        icon_lbl.grid(row=1, column=0, rowspan=2, padx=(14, 8), pady=8)

        # Value — large monospace for data emphasis
        val_lbl = ctk.CTkLabel(
            frame, text=value,
            font=("Consolas", 18, "bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        val_lbl.grid(row=1, column=1, sticky="sw", padx=5)

        # Label — geometric sans
        label_lbl = ctk.CTkLabel(
            frame, text=label,
            font=("Bahnschrift", 11),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        label_lbl.grid(row=2, column=1, sticky="nw", padx=5)

        # Stash references for programmatic updates
        frame._value_label = val_lbl  # type: ignore[attr-defined]
        frame._label_label = label_lbl  # type: ignore[attr-defined]
        return frame

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh_data(self):
        """Kick off a background thread to reload disk data."""
        self._refresh_btn.configure(state="disabled", text=t("dash.loading"))
        thread = threading.Thread(target=self._load_disk_data, daemon=True)
        thread.start()

    def _load_disk_data(self):
        """Load disk data (runs in a background thread)."""
        disks: list = []
        try:
            if get_all_disks is not None:
                disks = get_all_disks()
            else:
                # Attempt a late import in case the package path was fixed
                from src.core.disk_info import get_all_disks as _gad
                disks = _gad()
        except Exception as exc:
            _log.warning("Error loading disk data: %s", exc)

        # Schedule UI update on the main thread
        self.after(0, lambda: self._update_display(disks))

    # ------------------------------------------------------------------
    # Display update
    # ------------------------------------------------------------------

    def _update_display(self, disks: list):
        """Refresh the entire dashboard with *disks* data."""
        self._disks = disks

        # ---- Update summary cards ----
        total_storage = sum(
            getattr(d, "size_bytes", 0) or 0 for d in disks
        )
        ssd_count = sum(
            1 for d in disks if getattr(d, "media_type", "") == "SSD"
        )
        hdd_count = sum(
            1 for d in disks if getattr(d, "media_type", "") == "HDD"
        )

        if "disks" in self._summary_cards:
            self._summary_cards["disks"]._value_label.configure(  # type: ignore[attr-defined]
                text=str(len(disks)),
            )
        if "storage" in self._summary_cards:
            self._summary_cards["storage"]._value_label.configure(  # type: ignore[attr-defined]
                text=format_bytes(total_storage),
            )
        if "ssd" in self._summary_cards:
            self._summary_cards["ssd"]._value_label.configure(  # type: ignore[attr-defined]
                text=str(ssd_count),
            )
        if "hdd" in self._summary_cards:
            self._summary_cards["hdd"]._value_label.configure(  # type: ignore[attr-defined]
                text=str(hdd_count),
            )

        # ---- Rebuild disk card list ----
        for widget in self._disk_list_frame.winfo_children():
            widget.destroy()

        self._disk_cards.clear()
        self._selected_disks.clear()

        if not disks:
            no_data = ctk.CTkLabel(
                self._disk_list_frame,
                text=t("dash.no_disks"),
                text_color=COLORS["text_muted"],
                font=ctk.CTkFont(size=14),
            )
            no_data.grid(row=0, column=0, pady=40)
        else:
            for i, disk in enumerate(disks):
                card = DiskCard(
                    self._disk_list_frame,
                    disk_info=disk,
                    on_partition_click=self._on_partition_selected,
                    selectable=True,
                    on_selection_change=self._on_disk_selection_change,
                )
                card.grid(row=i, column=0, sticky="ew", padx=5, pady=5)
                self._disk_cards.append(card)

            # Drop zone hint at bottom
            if hasattr(self, "_drop_label"):
                self._drop_label.grid(
                    row=len(disks), column=0, pady=(10, 5),
                )

        # ---- Re-enable refresh ----
        self._refresh_btn.configure(state="normal", text=t("dash.refresh"))

    # ------------------------------------------------------------------
    # Partition selection
    # ------------------------------------------------------------------

    def _on_partition_selected(self, disk_index: int, partition_info):
        """Handle a partition being clicked in the disk bar."""
        self._selected_disk_index = disk_index

        # Tear down previous detail panel
        if self._partition_detail is not None:
            self._partition_detail.destroy()
            self._partition_detail = None
        if self._detail_placeholder is not None:
            self._detail_placeholder.destroy()
            self._detail_placeholder = None

        self._partition_detail = PartitionDetailPanel(
            self._detail_frame,
            partition_info=partition_info,
            disk_index=disk_index,
            on_action=self._on_partition_action,
        )
        self._partition_detail.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    def _on_partition_action(self, action: str, disk_index: int, partition_info):
        """Relay an action request from the detail panel to the caller."""
        if self._on_operation_requested is not None:
            self._on_operation_requested(action, disk_index, partition_info)

    # ------------------------------------------------------------------
    # Multi-disk selection (#29)
    # ------------------------------------------------------------------

    def _on_disk_selection_change(self, disk_index: int, selected: bool):
        """Track multi-disk selection state."""
        if selected:
            self._selected_disks.add(disk_index)
        else:
            self._selected_disks.discard(disk_index)
        count = len(self._selected_disks)
        if count > 0:
            self._refresh_btn.configure(
                text=f"{count} disk{'s' if count > 1 else ''} selected"
            )
        else:
            self._refresh_btn.configure(text=t("dash.refresh"))

    def get_selected_disks(self) -> list[int]:
        """Return list of selected disk indices for batch operations."""
        return sorted(self._selected_disks)

    # ------------------------------------------------------------------
    # Drag & Drop (#30)
    # ------------------------------------------------------------------

    def _setup_drag_drop(self):
        """Enable file drag-and-drop onto the dashboard.

        Uses tkinter DnD bindings if available (tkdnd), otherwise falls
        back to a simple drop-zone indicator.
        """
        # Try tkdnd first (if installed)
        try:
            self.drop_target_register("DND_Files")  # type: ignore[attr-defined]
            self.dnd_bind("<<Drop>>", self._on_file_drop)  # type: ignore[attr-defined]
            _log.info("Drag-and-drop: tkdnd backend active.")
            return
        except (AttributeError, tk.TclError):
            pass

        # Fallback: manual drop zone with visual indicator
        self._drop_label = ctk.CTkLabel(
            self._disk_list_frame,
            text="📂 Drop backup files here or use Backup page",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        )
        # Will be shown at bottom of disk list after refresh

    def _on_file_drop(self, event):
        """Handle file drop event from tkdnd."""
        import os
        files = self._parse_drop_data(event.data)
        if not files:
            return
        for f in files:
            if os.path.isfile(f) and f.lower().endswith((".zip", ".7z", ".vhdx", ".vhd", ".img")):
                from tkinter import messagebox
                messagebox.showinfo(
                    "File Dropped",
                    f"Received: {os.path.basename(f)}\n\n"
                    "Navigate to Backup or Advanced page to process this file.",
                )
                return
        from tkinter import messagebox
        messagebox.showinfo("Drag & Drop", "Drop .zip, .7z, .vhdx, or .img backup files.")

    @staticmethod
    def _parse_drop_data(data: str) -> list[str]:
        """Parse tkdnd drop data into a list of file paths."""
        import re
        # tkdnd wraps paths with spaces in braces: {C:\path with spaces\file.ext}
        paths = re.findall(r'\{([^}]+)\}', data)
        if not paths:
            paths = data.split()
        return [p.strip() for p in paths if p.strip()]
