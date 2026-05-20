"""Disk status report generation for OneClick Backup & Disk Manager.

Generates self-contained HTML or plain-text reports summarising the
state of all physical disks and their partitions.

Usage:
    from src.utils.report import ReportGenerator
    from src.core.disk_info import get_all_disks

    gen = ReportGenerator()
    path = gen.generate_html(get_all_disks(), "C:/reports/disk_report.html")
    print(f"Report written to {path}")
"""

from __future__ import annotations

import html
import logging
import os
import platform
from datetime import datetime

from src.utils.helpers import format_bytes

_log = logging.getLogger(__name__)


class ReportGenerator:
    """Generate disk status reports in HTML and plain-text formats."""

    # ------------------------------------------------------------------
    # HTML report
    # ------------------------------------------------------------------

    def generate_html(self, disks: list, output_path: str) -> str:
        """Create a self-contained HTML report of disk status.

        The resulting file uses only inline CSS so it can be opened in
        any browser without external dependencies.

        Args:
            disks: List of :class:`~src.core.disk_info.DiskInfo` objects.
            output_path: Destination file path for the HTML report.

        Returns:
            The absolute path of the written file.
        """
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        rows = self._build_disk_rows_html(disks)
        partition_sections = self._build_partition_sections_html(disks)

        content = _HTML_TEMPLATE.format(
            timestamp=html.escape(timestamp),
            hostname=html.escape(platform.node()),
            os_info=html.escape(
                f"{platform.system()} {platform.version()}"
            ),
            python_version=html.escape(platform.python_version()),
            disk_count=len(disks),
            disk_rows=rows,
            partition_sections=partition_sections,
        )

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(content)

        _log.info("HTML report written to %s", output_path)
        return os.path.abspath(output_path)

    # ------------------------------------------------------------------
    # Plain-text report
    # ------------------------------------------------------------------

    def generate_text(self, disks: list, output_path: str) -> str:
        """Create a plain-text report of disk status.

        Args:
            disks: List of :class:`~src.core.disk_info.DiskInfo` objects.
            output_path: Destination file path for the text report.

        Returns:
            The absolute path of the written file.
        """
        lines: list[str] = []
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        lines.append("=" * 70)
        lines.append("  OneClick Backup & Disk Manager -- Disk Status Report")
        lines.append("=" * 70)
        lines.append(f"  Generated : {timestamp}")
        lines.append(f"  Hostname  : {platform.node()}")
        lines.append(f"  OS        : {platform.system()} {platform.version()}")
        lines.append(f"  Python    : {platform.python_version()}")
        lines.append(f"  Disks     : {len(disks)}")
        lines.append("=" * 70)
        lines.append("")

        for disk in disks:
            lines.append("-" * 70)
            lines.append(
                f"  Disk {disk.index}: {disk.model}"
            )
            lines.append("-" * 70)
            lines.append(f"    Size        : {format_bytes(disk.size_bytes)}")
            lines.append(f"    Media       : {disk.media_type}")
            lines.append(f"    Interface   : {disk.interface_type}")
            lines.append(f"    Style       : {disk.partition_style}")
            lines.append(f"    Health      : {disk.health_status}")
            lines.append(f"    System Disk : {'Yes' if disk.is_system_disk else 'No'}")
            lines.append(f"    4K Aligned  : {'Yes' if disk.is_4k_aligned else 'No'}")
            lines.append(f"    Unallocated : {format_bytes(disk.unallocated_bytes)}")
            lines.append("")

            if disk.partitions:
                lines.append("    Partitions:")
                lines.append(
                    f"    {'#':<4} {'Letter':<8} {'Label':<16} "
                    f"{'FS':<8} {'Size':<12} {'Used':<12} {'Free':<12} "
                    f"{'Type':<12}"
                )
                lines.append("    " + "-" * 84)
                for part in disk.partitions:
                    letter = f"{part.letter}:" if part.letter else "--"
                    lines.append(
                        f"    {part.index:<4} {letter:<8} "
                        f"{(part.label or '--'):<16} "
                        f"{(part.file_system or '--'):<8} "
                        f"{format_bytes(part.size_bytes):<12} "
                        f"{format_bytes(part.used_bytes):<12} "
                        f"{format_bytes(part.free_bytes):<12} "
                        f"{(part.partition_type or '--'):<12}"
                    )
            else:
                lines.append("    No partitions found.")

            lines.append("")

        lines.append("=" * 70)
        lines.append("  End of report")
        lines.append("=" * 70)

        text = "\n".join(lines) + "\n"

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)

        _log.info("Text report written to %s", output_path)
        return os.path.abspath(output_path)

    # ------------------------------------------------------------------
    # Internal: HTML builders
    # ------------------------------------------------------------------

    def _build_disk_rows_html(self, disks: list) -> str:
        """Build ``<tr>`` rows for the disk summary table."""
        rows: list[str] = []
        for disk in disks:
            health_class = self._health_css_class(disk.health_status)
            rows.append(
                f"<tr>"
                f"<td>{disk.index}</td>"
                f"<td>{html.escape(disk.model)}</td>"
                f"<td>{format_bytes(disk.size_bytes)}</td>"
                f"<td>{html.escape(disk.media_type)}</td>"
                f"<td>{html.escape(disk.interface_type)}</td>"
                f"<td>{html.escape(disk.partition_style)}</td>"
                f'<td class="{health_class}">{html.escape(disk.health_status)}</td>'
                f"<td>{'Yes' if disk.is_system_disk else 'No'}</td>"
                f"<td>{'Yes' if disk.is_4k_aligned else 'No'}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    def _build_partition_sections_html(self, disks: list) -> str:
        """Build per-disk partition detail sections."""
        sections: list[str] = []
        for disk in disks:
            section = f'<h2>Disk {disk.index}: {html.escape(disk.model)}</h2>\n'
            if not disk.partitions:
                section += "<p>No partitions found.</p>\n"
                sections.append(section)
                continue

            section += (
                '<table>\n<thead><tr>'
                '<th>#</th><th>Letter</th><th>Label</th>'
                '<th>File System</th><th>Size</th><th>Used</th>'
                '<th>Free</th><th>Type</th><th>Active</th>'
                '<th>Boot</th><th>System</th>'
                '</tr></thead>\n<tbody>\n'
            )
            for part in disk.partitions:
                letter = f"{part.letter}:" if part.letter else "--"
                section += (
                    f"<tr>"
                    f"<td>{part.index}</td>"
                    f"<td>{html.escape(letter)}</td>"
                    f"<td>{html.escape(part.label or '--')}</td>"
                    f"<td>{html.escape(part.file_system or '--')}</td>"
                    f"<td>{format_bytes(part.size_bytes)}</td>"
                    f"<td>{format_bytes(part.used_bytes)}</td>"
                    f"<td>{format_bytes(part.free_bytes)}</td>"
                    f"<td>{html.escape(part.partition_type or '--')}</td>"
                    f"<td>{'Yes' if part.is_active else 'No'}</td>"
                    f"<td>{'Yes' if part.is_boot else 'No'}</td>"
                    f"<td>{'Yes' if part.is_system else 'No'}</td>"
                    f"</tr>\n"
                )
            section += "</tbody>\n</table>\n"
            sections.append(section)
        return "\n".join(sections)

    @staticmethod
    def _health_css_class(status: str) -> str:
        """Map a health status string to a CSS class name."""
        lower = status.lower()
        if lower == "healthy":
            return "health-ok"
        if lower in ("warning", "degraded"):
            return "health-warn"
        if lower in ("unhealthy", "error", "failed"):
            return "health-bad"
        return "health-unknown"


# ---------------------------------------------------------------------------
# HTML template (kept at module level to avoid deeply nested f-strings)
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Disk Status Report</title>
<style>
  :root {{
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e0e0e8;
    --muted: #888;
    --accent: #4a90d9;
    --green: #27ae60;
    --yellow: #f39c12;
    --red: #e74c3c;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: "Segoe UI", system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 2rem;
    line-height: 1.5;
  }}
  h1 {{ color: var(--accent); margin-bottom: .25rem; }}
  h2 {{ color: var(--accent); margin: 1.5rem 0 .75rem; }}
  .meta {{ color: var(--muted); margin-bottom: 1.5rem; font-size: .9rem; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 1rem;
    background: var(--card);
    border-radius: 6px;
    overflow: hidden;
  }}
  th, td {{
    padding: .5rem .75rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }}
  th {{ background: var(--border); font-weight: 600; }}
  tr:last-child td {{ border-bottom: none; }}
  .health-ok   {{ color: var(--green); font-weight: 600; }}
  .health-warn  {{ color: var(--yellow); font-weight: 600; }}
  .health-bad   {{ color: var(--red); font-weight: 600; }}
  .health-unknown {{ color: var(--muted); }}
  footer {{
    margin-top: 2rem;
    color: var(--muted);
    font-size: .8rem;
    text-align: center;
  }}
</style>
</head>
<body>
<h1>Disk Status Report</h1>
<p class="meta">
  Generated: {timestamp} &middot; Host: {hostname} &middot;
  OS: {os_info} &middot; Python {python_version} &middot;
  {disk_count} disk(s)
</p>

<h2>Disk Summary</h2>
<table>
<thead>
<tr>
  <th>#</th><th>Model</th><th>Size</th><th>Media</th>
  <th>Interface</th><th>Style</th><th>Health</th>
  <th>System</th><th>4K Aligned</th>
</tr>
</thead>
<tbody>
{disk_rows}
</tbody>
</table>

{partition_sections}

<footer>
  OneClick Backup &amp; Disk Manager &mdash; Report generated automatically.
</footer>
</body>
</html>
"""
