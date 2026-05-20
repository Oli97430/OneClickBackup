"""
Custom UI widgets for the OneClick disk management application.

Uses customtkinter and tkinter Canvas to create professional, reusable
UI components with a dark theme.  All widgets communicate via plain dicts
for loose coupling -- they do **not** import dataclasses from ``src.core``.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Optional

import customtkinter as ctk

try:
    from src.utils.i18n import t as _t
except ImportError:
    def _t(key: str, **kw) -> str:  # type: ignore[misc]
        return key

# ---------------------------------------------------------------------------
# Color theme
# ---------------------------------------------------------------------------

COLORS = {
    # ── Backgrounds: near-black to dark-slate spectrum ──
    "bg_dark":       "#0f1117",
    "bg_medium":     "#161b22",
    "bg_light":      "#1f2937",
    "bg_card":       "#1a1e2a",
    # ── Primary accent: electric indigo ──
    "accent_blue":   "#6366f1",
    # ── Semantic accents ──
    "accent_green":  "#34d399",
    "accent_yellow": "#fbbf24",
    "accent_red":    "#f87171",
    "accent_purple": "#a78bfa",
    "accent_orange": "#fb923c",
    # ── Text hierarchy ──
    "text_primary":  "#f1f5f9",
    "text_secondary": "#94a3b8",
    "text_muted":    "#475569",
    # ── Structural ──
    "border":        "#1e293b",
    "hover":         "#252e3d",
    # ── Filesystem partition colors ──
    "ntfs_color":    "#60a5fa",
    "fat32_color":   "#34d399",
    "exfat_color":   "#c084fc",
    "efi_color":     "#fbbf24",
    "recovery_color": "#fb7185",
    "unallocated_color": "#1e293b",
    "unknown_color": "#475569",
}

PARTITION_COLORS = {
    "NTFS": COLORS["ntfs_color"],
    "FAT32": COLORS["fat32_color"],
    "exFAT": COLORS["exfat_color"],
    "EFI": COLORS["efi_color"],
    "Recovery": COLORS["recovery_color"],
    "Unallocated": COLORS["unallocated_color"],
}

# Light theme variant (Feature #27)
COLORS_LIGHT = {
    "bg_dark":       "#f8fafc",
    "bg_medium":     "#f1f5f9",
    "bg_light":      "#e2e8f0",
    "bg_card":       "#ffffff",
    "accent_blue":   "#4f46e5",
    "accent_green":  "#059669",
    "accent_yellow": "#d97706",
    "accent_red":    "#dc2626",
    "accent_purple": "#7c3aed",
    "accent_orange": "#ea580c",
    "text_primary":  "#0f172a",
    "text_secondary": "#475569",
    "text_muted":    "#94a3b8",
    "border":        "#cbd5e1",
    "hover":         "#e2e8f0",
    "ntfs_color":    "#3b82f6",
    "fat32_color":   "#10b981",
    "exfat_color":   "#8b5cf6",
    "efi_color":     "#f59e0b",
    "recovery_color": "#f43f5e",
    "unallocated_color": "#e2e8f0",
    "unknown_color": "#94a3b8",
    "bg_bar":        "#e2e8f0",
    "other_fs_color": "#0ea5e9",
    "health_healthy": "#059669",
    "health_warning": "#d97706",
    "health_unhealthy": "#dc2626",
    "health_unknown": "#94a3b8",
}

# High contrast theme for WCAG accessibility (#50)
COLORS_HIGH_CONTRAST = {
    "bg_dark":       "#000000",
    "bg_medium":     "#1a1a1a",
    "bg_light":      "#333333",
    "bg_card":       "#0a0a0a",
    "accent_blue":   "#00ccff",
    "accent_green":  "#00ff7f",
    "accent_yellow": "#ffff00",
    "accent_red":    "#ff3333",
    "accent_purple": "#cc66ff",
    "accent_orange": "#ff9900",
    "text_primary":  "#ffffff",
    "text_secondary": "#cccccc",
    "text_muted":    "#999999",
    "border":        "#666666",
    "hover":         "#444444",
    "ntfs_color":    "#00aaff",
    "fat32_color":   "#00ff7f",
    "exfat_color":   "#cc66ff",
    "efi_color":     "#ffff00",
    "recovery_color": "#ff6699",
    "unallocated_color": "#333333",
    "unknown_color": "#999999",
    "bg_bar":        "#333333",
    "other_fs_color": "#00ccff",
    "health_healthy": "#00ff7f",
    "health_warning": "#ffff00",
    "health_unhealthy": "#ff3333",
    "health_unknown": "#999999",
}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Get attribute from dict or dataclass transparently."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# Dashboard-specific extensions
COLORS["bg_bar"] = "#252e3d"
COLORS["other_fs_color"] = "#38bdf8"
COLORS["health_healthy"] = "#34d399"
COLORS["health_warning"] = "#fbbf24"
COLORS["health_unhealthy"] = "#f87171"
COLORS["health_unknown"] = "#475569"


def _health_color(status: str) -> str:
    """Return a color for a disk health status string."""
    s = status.lower() if status else ""
    if s in ("healthy", "ok"):
        return COLORS["health_healthy"]
    if s in ("warning", "degraded"):
        return COLORS["health_warning"]
    if s in ("unhealthy", "failed", "error"):
        return COLORS["health_unhealthy"]
    return COLORS["health_unknown"]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _lighten(hex_color: str, factor: float = 0.25) -> str:
    """Return a lightened version of *hex_color* for gradient effects."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def _darken(hex_color: str, factor: float = 0.30) -> str:
    """Return a darkened version of *hex_color*."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = max(0, int(r * (1 - factor)))
    g = max(0, int(g * (1 - factor)))
    b = max(0, int(b * (1 - factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _canvas_rounded_rect(
    canvas: tk.Canvas,
    x0: float, y0: float, x1: float, y1: float,
    r: float = 4,
    **kwargs,
) -> int:
    """Draw a rounded rectangle on a tkinter Canvas."""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    if r < 1:
        return canvas.create_rectangle(x0, y0, x1, y1, **kwargs)
    pts = [
        x0 + r, y0,  x1 - r, y0,
        x1, y0,  x1, y0 + r,
        x1, y1 - r,
        x1, y1,  x1 - r, y1,
        x0 + r, y1,
        x0, y1,  x0, y1 - r,
        x0, y0 + r,
        x0, y0,  x0 + r, y0,
    ]
    return canvas.create_polygon(pts, smooth=True, **kwargs)


def format_bytes(size: int) -> str:
    """Convert *size* bytes to a human-readable string."""
    if size < 0:
        size = 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0  # type: ignore[assignment]
    return f"{size:.1f} PB"


def get_fs_color(file_system: str) -> str:
    """Return the theme color for a file-system type string."""
    if not file_system:
        return COLORS["unknown_color"]
    fs_upper = file_system.upper()
    for key, color in PARTITION_COLORS.items():
        if key.upper() in fs_upper:
            return color
    return COLORS["unknown_color"]


# ---------------------------------------------------------------------------
# 0. Tooltip
# ---------------------------------------------------------------------------


class Tooltip:
    """Simple hover tooltip for any widget."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500):
        self._widget = widget
        self._text = text
        self._delay = delay
        self._tip_window: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")

    def _on_enter(self, event=None):
        self._after_id = self._widget.after(self._delay, self._show)

    def _on_leave(self, event=None):
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self):
        if self._tip_window:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 5
        self._tip_window = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw, text=self._text, justify="left",
            background=COLORS["bg_light"], foreground=COLORS["text_primary"],
            relief="solid", borderwidth=1,
            font=("Segoe UI", 9), padx=8, pady=4,
        )
        label.pack()

    def _hide(self):
        if self._tip_window:
            try:
                self._tip_window.destroy()
            except tk.TclError:
                pass
            self._tip_window = None

    def update_text(self, text: str):
        """Update the tooltip text (takes effect on next hover)."""
        self._text = text


# ---------------------------------------------------------------------------
# 1. DiskBar
# ---------------------------------------------------------------------------


class DiskBar(ctk.CTkFrame):
    """Horizontal bar visualising the partitions of a single disk.

    Parameters
    ----------
    parent : widget
        Parent tkinter / CTk widget.
    disk_info : dict
        Dictionary with at least ``size_bytes`` (int) and ``partitions``
        (list of partition dicts).  Each partition dict should contain
        ``size_bytes``, ``file_system``, ``letter``, ``label``, and
        ``partition_type``.
    width, height : int
        Pixel dimensions of the canvas.
    on_partition_click : callable | None
        ``callback(partition_dict)`` invoked when a segment is clicked.
    """

    _BAR_PAD = 2  # internal padding around the drawn bar
    _SEG_GAP = 2  # pixel gap between adjacent segments

    def __init__(
        self,
        parent: Any,
        disk_info: dict,
        width: int = 700,
        height: int = 50,
        on_partition_click: Optional[Callable[[dict], None]] = None,
    ) -> None:
        super().__init__(parent, fg_color="transparent")
        self._disk_info = disk_info
        self._bar_width = width
        self._bar_height = height
        self._on_click = on_partition_click
        self._segments: list[dict] = []  # {x0, x1, partition, tag}
        self._hover_tag: Optional[str] = None

        self._canvas = tk.Canvas(
            self,
            width=width,
            height=height,
            bg=COLORS["bg_dark"],
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Button-1>", self._on_click_event)
        self._canvas.bind("<Leave>", self._on_leave)

        self._draw()

    # -- drawing ------------------------------------------------------------

    def _draw(self, no_color_updates: bool = False) -> None:  # type: ignore[override]
        self._canvas.delete("all")
        self._segments.clear()

        disk_size = self._disk_info.get("size_bytes", 0) or 1
        partitions = self._disk_info.get("partitions", [])
        unallocated = self._disk_info.get("unallocated_bytes", 0)

        pad = self._BAR_PAD
        gap = self._SEG_GAP
        usable_w = self._bar_width - 2 * pad
        bar_y0 = pad
        bar_y1 = self._bar_height - pad

        # Compute pixel widths -- give every visible segment at least 4 px
        raw_widths: list[tuple[dict | None, float]] = []
        for p in partitions:
            sz = p.get("size_bytes", 0) or 0
            raw_widths.append((p, sz / disk_size * usable_w))
        if unallocated > 0:
            raw_widths.append((None, unallocated / disk_size * usable_w))

        # Enforce minimum pixel width so tiny partitions stay visible
        min_px = 4
        widths: list[tuple[dict | None, float]] = []
        for item, w in raw_widths:
            widths.append((item, max(w, min_px)))
        # Re-normalise so the total equals usable_w (accounting for gaps)
        n_gaps = max(0, len(widths) - 1)
        total_adj = sum(w for _, w in widths) or 1
        scale = (usable_w - n_gaps * gap) / total_adj

        x = float(pad)
        for idx, (part, w) in enumerate(widths):
            seg_w = w * scale
            x0 = x
            x1 = x + seg_w
            tag = f"seg_{idx}"

            if part is None:
                # Unallocated space — subtle dark fill
                self._draw_unallocated(x0, bar_y0, x1, bar_y1, tag)
                self._segments.append(
                    {"x0": x0, "x1": x1, "partition": None, "tag": tag}
                )
            else:
                color = get_fs_color(part.get("file_system", ""))
                self._draw_gradient_segment(x0, bar_y0, x1, bar_y1, color, tag)
                # Label text
                label = self._partition_label(part)
                mid_x = (x0 + x1) / 2
                mid_y = (bar_y0 + bar_y1) / 2
                if seg_w > 40:
                    self._canvas.create_text(
                        mid_x, mid_y, text=label,
                        fill="#ffffff",
                        font=("Consolas", 9, "bold"),
                        tags=tag,
                    )
                self._segments.append(
                    {"x0": x0, "x1": x1, "partition": part, "tag": tag}
                )

            x = x1 + gap

        # If no partitions at all, draw the whole bar as unallocated
        if not raw_widths:
            self._draw_unallocated(pad, bar_y0, pad + usable_w, bar_y1, "empty")

    def _draw_gradient_segment(
        self, x0: float, y0: float, x1: float, y1: float,
        color: str, tag: str,
    ) -> None:
        """Draw a partition segment with a subtle vertical gradient + gloss."""
        mid_y = (y0 + y1) / 2
        lighter = _lighten(color, 0.20)
        # Top half — lighter shade (gloss effect)
        _canvas_rounded_rect(
            self._canvas, x0, y0, x1, mid_y,
            r=3, fill=lighter, outline="", tags=tag,
        )
        # Bottom half — true color
        _canvas_rounded_rect(
            self._canvas, x0, mid_y - 1, x1, y1,
            r=3, fill=color, outline="", tags=tag,
        )
        # 1px highlight at the very top
        if x1 - x0 > 6:
            self._canvas.create_line(
                x0 + 3, y0 + 1, x1 - 3, y0 + 1,
                fill=_lighten(color, 0.45), width=1, tags=tag,
            )

    def _draw_unallocated(
        self, x0: float, y0: float, x1: float, y1: float, tag: str
    ) -> None:
        """Draw a subtle pattern representing unallocated space."""
        color = COLORS["unallocated_color"]
        _canvas_rounded_rect(
            self._canvas, x0, y0, x1, y1,
            r=3, fill=color, outline="", tags=tag,
        )
        # Subtle horizontal dashes for texture
        ix0, ix1 = int(x0) + 4, int(x1) - 4
        mid_y = (y0 + y1) / 2
        dash_color = _lighten(color, 0.12)
        if ix1 - ix0 > 12:
            self._canvas.create_line(
                ix0, mid_y, ix1, mid_y,
                fill=dash_color, width=1, dash=(4, 6), tags=tag,
            )

    @staticmethod
    def _partition_label(part: dict) -> str:
        letter = part.get("letter", "")
        size = part.get("size_bytes", 0)
        label_parts: list[str] = []
        if letter:
            label_parts.append(f"{letter}:")
        label_parts.append(format_bytes(size))
        return " ".join(label_parts)

    # -- interaction --------------------------------------------------------

    def _segment_at(self, x: float) -> Optional[dict]:
        for seg in self._segments:
            if seg["x0"] <= x <= seg["x1"]:
                return seg
        return None

    def _on_motion(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        seg = self._segment_at(event.x)
        new_tag = seg["tag"] if seg else None
        if new_tag == self._hover_tag:
            return
        # Remove previous highlight
        if self._hover_tag is not None:
            self._canvas.delete("highlight")
        self._hover_tag = new_tag
        if seg is not None:
            pad = self._BAR_PAD
            # Outer glow (wider, translucent-look border)
            _canvas_rounded_rect(
                self._canvas,
                seg["x0"] - 1, pad - 1, seg["x1"] + 1,
                self._bar_height - pad + 1,
                r=4, fill="", outline=COLORS["accent_blue"], width=2,
                tags="highlight",
            )

    def _on_leave(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._canvas.delete("highlight")
        self._hover_tag = None

    def _on_click_event(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        if self._on_click is None:
            return
        seg = self._segment_at(event.x)
        if seg is not None and seg["partition"] is not None:
            self._on_click(seg["partition"])

    # -- public API ---------------------------------------------------------

    def update_disk(self, disk_info: dict) -> None:
        """Redraw the bar with new disk data."""
        self._disk_info = disk_info
        self._draw()


# ---------------------------------------------------------------------------
# 2. DiskCard
# ---------------------------------------------------------------------------


class DiskCard(ctk.CTkFrame):
    """Card showing a disk summary and its :class:`DiskBar`.

    Parameters
    ----------
    parent : widget
    disk_info : dict
        Must have ``index``, ``model``, ``size_bytes``, ``media_type``,
        ``partition_style``, ``partitions``, ``unallocated_bytes``, and
        optionally ``is_system_disk``.
    on_select : callable | None
        ``callback(disk_info)`` when the card is selected.
    on_partition_click : callable | None
        Forwarded to :class:`DiskBar`.
    """

    def __init__(
        self,
        parent: Any,
        disk_info: dict,
        on_select: Optional[Callable[[dict], None]] = None,
        on_partition_click: Optional[Callable[[dict], None]] = None,
    ) -> None:
        super().__init__(
            parent,
            fg_color=COLORS["bg_card"],
            corner_radius=8,
            border_width=1,
            border_color=COLORS["border"],
        )
        self._disk_info = disk_info
        self._on_select = on_select
        self._selected = False

        # --- accent stripe + content wrapper ---
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True)

        # Left accent stripe (3px colored bar)
        media = disk_info.get("media_type", "Unknown")
        stripe_color = COLORS["accent_blue"] if "SSD" in media.upper() else COLORS["accent_purple"]
        self._accent_stripe = ctk.CTkFrame(
            inner, width=3, fg_color=stripe_color, corner_radius=0,
        )
        self._accent_stripe.pack(side="left", fill="y", padx=(0, 0))

        content = ctk.CTkFrame(inner, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True)

        # --- header row ---
        header = ctk.CTkFrame(content, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(10, 4))

        # Disk icon
        icon = "⚡" if "SSD" in media.upper() else "\U0001f4bf"
        icon_label = ctk.CTkLabel(
            header, text=icon, font=("Segoe UI Emoji", 20),
            text_color=COLORS["text_secondary"],
        )
        icon_label.pack(side="left", padx=(0, 10))

        # Model name — geometric display font
        model = disk_info.get("model", "Unknown Disk")
        model_label = ctk.CTkLabel(
            header, text=model, font=("Bahnschrift SemiBold", 14),
            text_color=COLORS["text_primary"], anchor="w",
        )
        model_label.pack(side="left", fill="x", expand=True)

        # System badge — sharp pill shape
        if disk_info.get("is_system_disk"):
            sys_badge = ctk.CTkLabel(
                header, text=" SYSTEM ", font=("Consolas", 9, "bold"),
                text_color=COLORS["bg_dark"],
                fg_color=COLORS["accent_yellow"], corner_radius=3,
                padx=6, pady=1,
            )
            sys_badge.pack(side="right", padx=(4, 0))

        # Select indicator
        self._select_dot = ctk.CTkLabel(
            header, text="", width=10, height=10,
            fg_color="transparent", corner_radius=5,
        )
        self._select_dot.pack(side="right", padx=(4, 0))

        # --- info row with monospace data ---
        info_frame = ctk.CTkFrame(content, fg_color="transparent")
        info_frame.pack(fill="x", padx=14, pady=(0, 4))

        size_text = format_bytes(disk_info.get("size_bytes", 0))
        style = disk_info.get("partition_style", "Unknown")
        # Tags as individual labels for refined spacing
        for tag_text in (size_text, media, style):
            tag_lbl = ctk.CTkLabel(
                info_frame, text=tag_text, font=("Consolas", 10),
                text_color=COLORS["text_muted"],
                fg_color=COLORS["bg_light"], corner_radius=3,
                padx=6, pady=1,
            )
            tag_lbl.pack(side="left", padx=(0, 6))

        # --- disk bar ---
        self._disk_bar = DiskBar(
            content, disk_info,
            width=680, height=44,
            on_partition_click=on_partition_click,
        )
        self._disk_bar.pack(fill="x", padx=14, pady=(4, 12))

        # --- click binding on the whole card ---
        self.bind("<Button-1>", self._on_card_click)
        for child in (header, icon_label, model_label, info_frame, content, inner):
            child.bind("<Button-1>", self._on_card_click)

        # Hover effect
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave_card)

    # -- interaction --------------------------------------------------------

    def _on_card_click(self, _event: Any = None) -> None:
        self.set_selected(True)
        if self._on_select:
            self._on_select(self._disk_info)

    def _on_enter(self, _event: Any = None) -> None:
        if not self._selected:
            self.configure(border_color=COLORS["hover"])

    def _on_leave_card(self, _event: Any = None) -> None:
        if not self._selected:
            self.configure(border_color=COLORS["border"])

    def set_selected(self, selected: bool) -> None:
        """Toggle the visual *selected* state of this card."""
        self._selected = selected
        if selected:
            self.configure(border_color=COLORS["accent_blue"])
            self._select_dot.configure(fg_color=COLORS["accent_blue"])
        else:
            self.configure(border_color=COLORS["border"])
            self._select_dot.configure(fg_color="transparent")

    def update_disk(self, disk_info: dict) -> None:
        """Refresh the card with new data (does not change layout)."""
        self._disk_info = disk_info
        self._disk_bar.update_disk(disk_info)


# ---------------------------------------------------------------------------
# 3. InfoRow
# ---------------------------------------------------------------------------


class InfoRow(ctk.CTkFrame):
    """A simple **label : value** row for detail panels.

    Parameters
    ----------
    parent : widget
    label : str
        Descriptive text (left side).
    value : str
        Data text (right side).
    value_color : str | None
        Override colour for the value label.
    """

    def __init__(
        self,
        parent: Any,
        label: str,
        value: str,
        value_color: Optional[str] = None,
    ) -> None:
        super().__init__(parent, fg_color="transparent")

        lbl = ctk.CTkLabel(
            self, text=label, font=("Segoe UI", 11),
            text_color=COLORS["text_muted"], anchor="w", width=120,
        )
        lbl.pack(side="left", padx=(0, 8))

        val_color = value_color or COLORS["text_primary"]
        self._value_label = ctk.CTkLabel(
            self, text=value, font=("Consolas", 11),
            text_color=val_color, anchor="w",
        )
        self._value_label.pack(side="left", fill="x", expand=True)

    def set_value(self, value: str, color: Optional[str] = None) -> None:
        """Update the displayed value (and optionally its colour)."""
        self._value_label.configure(text=value)
        if color:
            self._value_label.configure(text_color=color)


# ---------------------------------------------------------------------------
# 4. PartitionDetailPanel
# ---------------------------------------------------------------------------


class PartitionDetailPanel(ctk.CTkFrame):
    """Panel showing detailed information about a selected partition.

    Parameters
    ----------
    parent : widget
    on_action : callable | None
        ``callback(action_name: str, partition: dict)`` when an action
        button is pressed.  *action_name* is one of ``"resize"``,
        ``"format"``, ``"delete"``, ``"change_letter"``.
    """

    def __init__(
        self,
        parent: Any,
        on_action: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=8)
        self._on_action = on_action
        self._partition: Optional[dict] = None

        # Accent header line
        self._header_accent = ctk.CTkFrame(
            self, height=3, fg_color=COLORS["accent_blue"], corner_radius=0,
        )
        self._header_accent.pack(fill="x")

        # Title
        self._title = ctk.CTkLabel(
            self, text="Partition Details",
            font=("Bahnschrift SemiBold", 15),
            text_color=COLORS["text_primary"], anchor="w",
        )
        self._title.pack(fill="x", padx=14, pady=(12, 6))

        # Scrollable info area
        self._info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._info_frame.pack(fill="both", expand=True, padx=14, pady=(0, 4))

        # Pre-create info rows
        self._rows: dict[str, InfoRow] = {}
        for key, label in (
            ("letter", "Drive Letter"),
            ("label", "Label"),
            ("file_system", "File System"),
            ("size", "Total Size"),
            ("used", "Used"),
            ("free", "Free"),
            ("type", "Type"),
            ("flags", "Flags"),
        ):
            row = InfoRow(self._info_frame, label, "--")
            row.pack(fill="x", pady=1)
            self._rows[key] = row

        # Usage progress bar
        self._usage_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._usage_frame.pack(fill="x", padx=14, pady=(2, 4))

        self._usage_label = ctk.CTkLabel(
            self._usage_frame, text="Usage", font=("Segoe UI", 10),
            text_color=COLORS["text_muted"], anchor="w",
        )
        self._usage_label.pack(side="left", padx=(0, 8))

        self._usage_bar = ctk.CTkProgressBar(
            self._usage_frame, width=200, height=12,
            progress_color=COLORS["accent_blue"],
            fg_color=COLORS["bg_dark"],
        )
        self._usage_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._usage_bar.set(0)

        self._usage_pct = ctk.CTkLabel(
            self._usage_frame, text="0 %", font=("Consolas", 10),
            text_color=COLORS["text_secondary"], width=48, anchor="e",
        )
        self._usage_pct.pack(side="right")

        # Action buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=14, pady=(6, 12))

        actions = [
            ("Resize", "resize", COLORS["accent_blue"]),
            ("Format", "format", COLORS["accent_orange"]),
            ("Delete", "delete", COLORS["accent_red"]),
            ("Change Letter", "change_letter", COLORS["accent_purple"]),
        ]
        for text, action, color in actions:
            btn = ctk.CTkButton(
                btn_frame, text=text, width=90, height=28,
                font=("Segoe UI", 11), fg_color=color,
                hover_color=COLORS["hover"],
                command=lambda a=action: self._handle_action(a),
            )
            btn.pack(side="left", padx=(0, 6))

        # Start with placeholder
        self.show_placeholder()

    # -- public API ---------------------------------------------------------

    def show_partition(self, partition: dict) -> None:
        """Populate the panel with *partition* data."""
        self._partition = partition

        letter = partition.get("letter", "")
        self._rows["letter"].set_value(
            f"{letter}:" if letter else "(none)",
            COLORS["text_primary"] if letter else COLORS["text_muted"],
        )
        self._rows["label"].set_value(partition.get("label", "") or "(no label)")

        fs = partition.get("file_system", "") or "Unknown"
        self._rows["file_system"].set_value(fs, get_fs_color(fs))

        size_bytes = partition.get("size_bytes", 0) or 0
        used_bytes = partition.get("used_bytes", 0) or 0
        free_bytes = partition.get("free_bytes", 0) or 0

        self._rows["size"].set_value(format_bytes(size_bytes))
        self._rows["used"].set_value(format_bytes(used_bytes))
        self._rows["free"].set_value(format_bytes(free_bytes))

        ptype = partition.get("partition_type", "") or "Unknown"
        self._rows["type"].set_value(ptype)

        flags: list[str] = []
        if partition.get("is_active"):
            flags.append("Active")
        if partition.get("is_boot"):
            flags.append("Boot")
        if partition.get("is_system"):
            flags.append("System")
        self._rows["flags"].set_value(", ".join(flags) if flags else "(none)")

        # Progress
        if size_bytes > 0:
            ratio = min(used_bytes / size_bytes, 1.0)
            self._usage_bar.set(ratio)
            pct = ratio * 100
            self._usage_pct.configure(text=f"{pct:.0f} %")
            # Colour the bar by usage severity
            if pct >= 90:
                self._usage_bar.configure(progress_color=COLORS["accent_red"])
            elif pct >= 70:
                self._usage_bar.configure(progress_color=COLORS["accent_yellow"])
            else:
                self._usage_bar.configure(progress_color=COLORS["accent_blue"])
        else:
            self._usage_bar.set(0)
            self._usage_pct.configure(text="-- %")

    def show_placeholder(self) -> None:
        """Reset the panel to an empty state."""
        self._partition = None
        for row in self._rows.values():
            row.set_value("--")
        self._usage_bar.set(0)
        self._usage_pct.configure(text="-- %")

    # -- internal -----------------------------------------------------------

    def _handle_action(self, action: str) -> None:
        if self._on_action and self._partition:
            self._on_action(action, self._partition)


# ---------------------------------------------------------------------------
# 5. ProgressDialog
# ---------------------------------------------------------------------------


class ProgressDialog(ctk.CTkToplevel):
    """Modal dialog showing the progress of a long-running operation.

    Parameters
    ----------
    parent : widget
    title : str
    message : str
        Initial message displayed above the progress bar.
    """

    def __init__(
        self,
        parent: Any,
        title: str = "Operation in Progress",
        message: str = "Please wait...",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("440x220")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        # Centre on parent
        self.after(10, self._centre_on_parent, parent)

        # Accent header line
        ctk.CTkFrame(
            self, height=3, fg_color=COLORS["accent_blue"], corner_radius=0,
        ).pack(fill="x")

        # Message
        self._msg_label = ctk.CTkLabel(
            self, text=message, font=("Bahnschrift", 13),
            text_color=COLORS["text_primary"], wraplength=400,
        )
        self._msg_label.pack(pady=(20, 12), padx=24)

        # Progress bar
        self._progress = ctk.CTkProgressBar(
            self, width=380, height=14,
            progress_color=COLORS["accent_blue"],
            fg_color=COLORS["bg_medium"],
            corner_radius=3,
        )
        self._progress.pack(pady=(0, 6), padx=24)
        self._progress.set(0)

        # Percentage — monospace for sharp data display
        self._pct_label = ctk.CTkLabel(
            self, text="0 %", font=("Consolas", 13, "bold"),
            text_color=COLORS["text_secondary"],
        )
        self._pct_label.pack(pady=(0, 10))

        # Cancel button
        self._cancelled = False
        self._cancel_btn = ctk.CTkButton(
            self, text=_t("common.cancel"), width=100, height=30,
            font=("Segoe UI", 11),
            fg_color=COLORS["accent_red"],
            hover_color=COLORS["hover"],
            command=self._on_cancel,
        )
        self._cancel_btn.pack(pady=(0, 16))

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # -- public API ---------------------------------------------------------

    def update_progress(self, message: str, percent: float) -> None:
        """Update the displayed *message* and *percent* (0--100)."""
        self._msg_label.configure(text=message)
        clamped = max(0.0, min(percent, 100.0))
        self._progress.set(clamped / 100.0)
        self._pct_label.configure(text=f"{clamped:.0f} %")
        self.update_idletasks()

    def set_complete(self, message: str = "") -> None:
        """Mark the operation as finished successfully."""
        if not message:
            message = _t("common.ok")
        self._msg_label.configure(text=message)
        self._progress.set(1.0)
        self._progress.configure(progress_color=COLORS["accent_green"])
        self._pct_label.configure(text="100 %")
        self._cancel_btn.configure(text=_t("common.ok"), fg_color=COLORS["accent_green"])
        self.update_idletasks()

    def set_error(self, message: str = "") -> None:
        """Show an error state."""
        if not message:
            message = _t("common.error")
        self._msg_label.configure(
            text=message, text_color=COLORS["accent_red"],
        )
        self._progress.configure(progress_color=COLORS["accent_red"])
        self._cancel_btn.configure(text=_t("common.ok"), fg_color=COLORS["accent_red"])
        self.update_idletasks()

    @property
    def cancelled(self) -> bool:
        """Whether the user has pressed *Cancel*."""
        return self._cancelled

    # -- internal -----------------------------------------------------------

    def _on_cancel(self) -> None:
        self._cancelled = True
        self.grab_release()
        self.destroy()

    def _centre_on_parent(self, parent: Any) -> None:
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 6. ConfirmDialog
# ---------------------------------------------------------------------------


_RISK_COLORS = {
    "low": COLORS["accent_green"],
    "medium": COLORS["accent_yellow"],
    "high": COLORS["accent_orange"],
    "critical": COLORS["accent_red"],
}


class ConfirmDialog(ctk.CTkToplevel):
    """Modal confirmation dialog for dangerous operations.

    Prefer the static :meth:`ask` helper for simple yes/no confirmation.

    Parameters
    ----------
    parent : widget
    title, message : str
    risk_level : str
        One of ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.
    details : str
        Additional operation detail text shown below the message.
    """

    def __init__(
        self,
        parent: Any,
        title: str = "Confirm Operation",
        message: str = "Are you sure?",
        risk_level: str = "medium",
        details: str = "",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("440x270")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        self.result: bool = False

        risk_color = _RISK_COLORS.get(risk_level.lower(), COLORS["accent_yellow"])

        # Accent header line — colored by risk
        ctk.CTkFrame(
            self, height=3, fg_color=risk_color, corner_radius=0,
        ).pack(fill="x")

        # Warning icon
        icon_text = "⚠"
        icon_label = ctk.CTkLabel(
            self, text=icon_text, font=("Segoe UI Emoji", 32),
            text_color=risk_color,
        )
        icon_label.pack(pady=(16, 4))

        # Message
        msg_label = ctk.CTkLabel(
            self, text=message, font=("Bahnschrift SemiBold", 13),
            text_color=COLORS["text_primary"], wraplength=400,
        )
        msg_label.pack(pady=(0, 6), padx=20)

        # Details
        if details:
            det_label = ctk.CTkLabel(
                self, text=details, font=("Segoe UI", 11),
                text_color=COLORS["text_secondary"], wraplength=400,
            )
            det_label.pack(pady=(0, 6), padx=20)

        # Risk indicator bar
        risk_bar_frame = ctk.CTkFrame(self, fg_color="transparent", height=20)
        risk_bar_frame.pack(fill="x", padx=30, pady=(2, 8))

        risk_label = ctk.CTkLabel(
            risk_bar_frame,
            text=f"Risk: {risk_level.capitalize()}",
            font=("Consolas", 11, "bold"),
            text_color=risk_color,
        )
        risk_label.pack(side="left")

        risk_indicator = ctk.CTkProgressBar(
            risk_bar_frame, width=120, height=8,
            progress_color=risk_color,
            fg_color=COLORS["bg_medium"],
        )
        risk_indicator.pack(side="right")
        risk_levels = {"low": 0.25, "medium": 0.50, "high": 0.75, "critical": 1.0}
        risk_indicator.set(risk_levels.get(risk_level.lower(), 0.5))

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=30, pady=(4, 18))

        cancel_btn = ctk.CTkButton(
            btn_frame, text=_t("common.cancel"), width=100, height=32,
            font=("Segoe UI", 12), fg_color=COLORS["bg_light"],
            hover_color=COLORS["hover"],
            command=self._on_cancel,
        )
        cancel_btn.pack(side="right", padx=(8, 0))

        apply_btn = ctk.CTkButton(
            btn_frame, text=_t("common.apply"), width=100, height=32,
            font=("Segoe UI Semibold", 12),
            fg_color=risk_color,
            hover_color=COLORS["hover"],
            command=self._on_apply,
        )
        apply_btn.pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Centre on parent
        self.after(10, self._centre_on_parent, parent)

    # -- actions ------------------------------------------------------------

    def _on_apply(self) -> None:
        self.result = True
        self.grab_release()
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = False
        self.grab_release()
        self.destroy()

    def _centre_on_parent(self, parent: Any) -> None:
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # -- static helper ------------------------------------------------------

    @staticmethod
    def ask(
        parent: Any,
        title: str,
        message: str,
        risk_level: str = "medium",
        details: str = "",
    ) -> bool:
        """Show a blocking confirmation dialog and return ``True`` if the
        user clicked *Apply*, ``False`` otherwise."""
        dialog = ConfirmDialog(parent, title, message, risk_level, details)
        dialog.wait_window()
        return dialog.result


# ---------------------------------------------------------------------------
# 7. SidebarButton
# ---------------------------------------------------------------------------


class SidebarButton(ctk.CTkFrame):
    """Navigation button used in the application sidebar.

    Parameters
    ----------
    parent : widget
    icon : str
        Text or emoji displayed as the icon.
    text : str
        Button label.
    command : callable
        Invoked on click.
    is_active : bool
        Initial active state.
    """

    def __init__(
        self,
        parent: Any,
        icon: str,
        text: str,
        command: Callable[[], None],
        is_active: bool = False,
    ) -> None:
        super().__init__(parent, fg_color="transparent", corner_radius=6)
        self._command = command
        self._active = False

        # Active indicator (left bar — 4px wide for visual punch)
        self._indicator = ctk.CTkFrame(
            self, width=4, height=28,
            fg_color="transparent", corner_radius=2,
        )
        self._indicator.pack(side="left", padx=(3, 8), pady=6)
        self._indicator.pack_propagate(False)

        # Icon
        self._icon_label = ctk.CTkLabel(
            self, text=icon, font=("Segoe UI Emoji", 17),
            text_color=COLORS["text_muted"], width=28,
        )
        self._icon_label.pack(side="left", padx=(0, 8))

        # Text — geometric display font
        self._text_label = ctk.CTkLabel(
            self, text=text, font=("Bahnschrift", 12),
            text_color=COLORS["text_secondary"], anchor="w",
        )
        self._text_label.pack(side="left", fill="x", expand=True)

        # Mouse bindings
        for widget in (self, self._icon_label, self._text_label, self._indicator):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        # Keyboard accessibility: allow Tab-focus + activation with Return/Space
        try:
            self.configure(takefocus=True)
        except (ValueError, tk.TclError):
            pass  # CTk version may not pass takefocus through
        self.bind("<Return>", self._on_click)
        self.bind("<space>", self._on_click)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)

        if is_active:
            self.set_active(True)

    # -- public API ---------------------------------------------------------

    def set_text(self, text: str) -> None:
        """Update the displayed button label."""
        self._text_label.configure(text=text)

    def set_active(self, active: bool) -> None:
        """Toggle the visual *active* state."""
        self._active = active
        if active:
            self.configure(fg_color=COLORS["bg_light"])
            self._indicator.configure(fg_color=COLORS["accent_blue"])
            self._icon_label.configure(text_color=COLORS["text_primary"])
            self._text_label.configure(text_color=COLORS["text_primary"])
        else:
            self.configure(fg_color="transparent")
            self._indicator.configure(fg_color="transparent")
            self._icon_label.configure(text_color=COLORS["text_secondary"])
            self._text_label.configure(text_color=COLORS["text_secondary"])

    # -- internal -----------------------------------------------------------

    def _on_click(self, _event: Any = None) -> None:
        self._command()

    def _on_enter(self, _event: Any = None) -> None:
        if not self._active:
            self.configure(fg_color=COLORS["hover"])

    def _on_leave(self, _event: Any = None) -> None:
        if not self._active:
            self.configure(fg_color="transparent")

    def _on_focus_in(self, _event: Any = None) -> None:
        if not self._active:
            self._indicator.configure(fg_color=COLORS["text_muted"])

    def _on_focus_out(self, _event: Any = None) -> None:
        if not self._active:
            self._indicator.configure(fg_color="transparent")


# ---------------------------------------------------------------------------
# 8. OperationQueuePanel
# ---------------------------------------------------------------------------


class OperationQueuePanel(ctk.CTkFrame):
    """Panel listing pending disk operations.

    Parameters
    ----------
    parent : widget
    on_apply : callable | None
        Called (no args) when the user clicks *Apply All*.
    on_clear : callable | None
        Called (no args) when the user clicks *Clear*.
    """

    _RISK_DOT = {
        "low": COLORS["accent_green"],
        "medium": COLORS["accent_yellow"],
        "high": COLORS["accent_orange"],
        "critical": COLORS["accent_red"],
    }

    def __init__(
        self,
        parent: Any,
        on_apply: Optional[Callable[[], None]] = None,
        on_clear: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=8)
        self._on_apply = on_apply
        self._on_clear = on_clear
        self._operations: list[dict] = []

        # Header with count badge
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))

        title = ctk.CTkLabel(
            header, text=_t("queue.title"),
            font=("Bahnschrift SemiBold", 13),
            text_color=COLORS["text_primary"], anchor="w",
        )
        title.pack(side="left")

        self._count_badge = ctk.CTkLabel(
            header, text="0", font=("Consolas", 10, "bold"),
            text_color=COLORS["bg_dark"],
            fg_color=COLORS["accent_blue"],
            corner_radius=10, width=24, height=20,
        )
        self._count_badge.pack(side="left", padx=(8, 0))

        # Scrollable list
        self._list_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent", height=160,
        )
        self._list_frame.pack(fill="both", expand=True, padx=8, pady=4)

        # Placeholder
        self._placeholder = ctk.CTkLabel(
            self._list_frame, text=_t("queue.empty"),
            font=("Segoe UI", 11), text_color=COLORS["text_muted"],
        )
        self._placeholder.pack(pady=20)

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(4, 10))

        self._apply_btn = ctk.CTkButton(
            btn_frame, text=_t("queue.apply"), width=100, height=30,
            font=("Segoe UI Semibold", 11),
            fg_color=COLORS["accent_green"],
            hover_color=COLORS["hover"],
            command=self._handle_apply,
        )
        self._apply_btn.pack(side="right", padx=(6, 0))

        self._clear_btn = ctk.CTkButton(
            btn_frame, text=_t("queue.clear"), width=80, height=30,
            font=("Segoe UI", 11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["hover"],
            command=self._handle_clear,
        )
        self._clear_btn.pack(side="right")

    # -- public API ---------------------------------------------------------

    def add_operation(self, operation: dict) -> None:
        """Add an operation dict.

        Expected keys: ``description`` (str), ``risk`` (str, e.g.
        ``"low"``/``"high"``), and an optional ``id``.
        """
        self._operations.append(operation)
        self._rebuild_list()

    def remove_operation(self, index: int) -> None:
        """Remove the operation at *index*."""
        if 0 <= index < len(self._operations):
            self._operations.pop(index)
            self._rebuild_list()

    def clear_operations(self) -> None:
        """Remove every pending operation."""
        self._operations.clear()
        self._rebuild_list()

    def get_operations(self) -> list[dict]:
        """Return a copy of the current operations list."""
        return list(self._operations)

    # -- internal -----------------------------------------------------------

    def _rebuild_list(self) -> None:
        # Clear existing children
        for child in self._list_frame.winfo_children():
            child.destroy()

        count = len(self._operations)
        self._count_badge.configure(text=str(count))

        if count == 0:
            self._placeholder = ctk.CTkLabel(
                self._list_frame, text=_t("queue.empty"),
                font=("Segoe UI", 11), text_color=COLORS["text_muted"],
            )
            self._placeholder.pack(pady=20)
            return

        for idx, op in enumerate(self._operations):
            row = ctk.CTkFrame(self._list_frame, fg_color="transparent", height=32)
            row.pack(fill="x", pady=1)

            # Risk dot
            risk = op.get("risk", "medium").lower()
            dot_color = self._RISK_DOT.get(risk, COLORS["accent_yellow"])
            dot = ctk.CTkLabel(
                row, text="●", font=("Segoe UI", 10),
                text_color=dot_color, width=16,
            )
            dot.pack(side="left", padx=(4, 4))

            desc = ctk.CTkLabel(
                row, text=op.get("description", "Unknown operation"),
                font=("Segoe UI", 11),
                text_color=COLORS["text_primary"], anchor="w",
            )
            desc.pack(side="left", fill="x", expand=True)

            remove_btn = ctk.CTkButton(
                row, text="✕", width=24, height=24,
                font=("Segoe UI", 11),
                fg_color="transparent",
                hover_color=COLORS["accent_red"],
                text_color=COLORS["text_muted"],
                command=lambda i=idx: self.remove_operation(i),
            )
            remove_btn.pack(side="right", padx=(4, 2))

    def _handle_apply(self) -> None:
        if self._on_apply:
            self._on_apply()

    def _handle_clear(self) -> None:
        self.clear_operations()
        if self._on_clear:
            self._on_clear()


# ---------------------------------------------------------------------------
# 9. StatusBar
# ---------------------------------------------------------------------------


class StatusBar(ctk.CTkFrame):
    """Bottom status bar for the application window.

    Parameters
    ----------
    parent : widget
    on_refresh : callable | None
        Called when the refresh button is clicked.
    """

    def __init__(
        self,
        parent: Any,
        on_refresh: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(
            parent, fg_color=COLORS["bg_medium"], height=34, corner_radius=0,
        )
        self.pack_propagate(False)

        # Accent top separator — thin indigo line
        self._top_accent = ctk.CTkFrame(
            self, height=1, fg_color=COLORS["accent_blue"], corner_radius=0,
        )
        self._top_accent.pack(fill="x", side="top")

        # Content frame below accent
        bar_content = ctk.CTkFrame(self, fg_color="transparent")
        bar_content.pack(fill="both", expand=True)

        # Status message (left)
        self._status_label = ctk.CTkLabel(
            bar_content, text=_t("status.ready"), font=("Bahnschrift", 11),
            text_color=COLORS["text_secondary"], anchor="w",
        )
        self._status_label.pack(side="left", padx=(12, 8), fill="x", expand=True)

        # Pending ops count (right) — monospace
        self._pending_label = ctk.CTkLabel(
            bar_content, text=_t("status.pending_n", n=0), font=("Consolas", 10),
            text_color=COLORS["text_muted"], anchor="e",
        )
        self._pending_label.pack(side="right", padx=(8, 4))

        # Refresh button
        self._refresh_btn = ctk.CTkButton(
            bar_content, text="↻", width=28, height=24,
            font=("Segoe UI", 14),
            fg_color="transparent",
            hover_color=COLORS["hover"],
            text_color=COLORS["text_secondary"],
            command=on_refresh if on_refresh else lambda: None,
        )
        self._refresh_btn.pack(side="right", padx=(0, 8))

    # -- public API ---------------------------------------------------------

    def set_status(self, message: str) -> None:
        """Update the status message text."""
        self._status_label.configure(text=message)

    def set_pending_count(self, count: int) -> None:
        """Update the pending operations counter."""
        text = _t("status.pending_one") if count == 1 else _t("status.pending_n", n=count)
        color = COLORS["accent_yellow"] if count > 0 else COLORS["text_muted"]
        self._pending_label.configure(text=text, text_color=color)
