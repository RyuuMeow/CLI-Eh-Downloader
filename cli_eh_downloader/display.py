"""Rich-based display helpers for the interactive shell."""

from __future__ import annotations

import sys
import time
from typing import Callable, List

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import DownloadMethod, DownloadTask, TaskStatus
from .torrent import HAS_LIBTORRENT

console = Console()


def _make_live_console() -> Console:
    """Create a console that keeps Rich Live in terminal-refresh mode."""
    return Console(
        file=sys.stdout,
        force_terminal=True,
        color_system=console.color_system,
        legacy_windows=console.legacy_windows,
    )

# Status → (colour, icon)
_STATUS_STYLE: dict[TaskStatus, tuple[str, str]] = {
    TaskStatus.QUEUED:           ("dim",          "⏳"),
    TaskStatus.FETCHING_INFO:    ("cyan",         "🔍"),
    TaskStatus.CHECKING_TORRENT: ("cyan",         "🧲"),
    TaskStatus.DOWNLOADING:      ("green",        "⬇️"),
    TaskStatus.SEEDING:          ("blue",         "🌱"),
    TaskStatus.COMPLETED:        ("bold green",   "✅"),
    TaskStatus.FAILED:           ("bold red",     "❌"),
    TaskStatus.PAUSED:           ("yellow",       "⏸️"),
    TaskStatus.CANCELLED:        ("dim",          "🚫"),
}

_ASCII_STATUS_ICON: dict[TaskStatus, str] = {
    TaskStatus.QUEUED: "...",
    TaskStatus.FETCHING_INFO: "GET",
    TaskStatus.CHECKING_TORRENT: "TOR",
    TaskStatus.DOWNLOADING: "DL",
    TaskStatus.SEEDING: "SEED",
    TaskStatus.COMPLETED: "OK",
    TaskStatus.FAILED: "FAIL",
    TaskStatus.PAUSED: "PAUSE",
    TaskStatus.CANCELLED: "CANCEL",
}


def _supports_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


def _can_encode(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except UnicodeEncodeError:
        return False


def _status_style(status: TaskStatus) -> tuple[str, str]:
    style, icon = _STATUS_STYLE.get(status, ("", "?"))
    if not _supports_unicode():
        icon = _ASCII_STATUS_ICON.get(status, "?")
    return style, icon


def _progress_bar(filled: int, empty: int, *, markup: bool = False) -> str:
    fill_char = "\u2588" if _can_encode("\u2588") else "#"
    empty_char = "\u2591" if _can_encode("\u2591") else " "
    if markup:
        return f"[green]{fill_char * filled}[/green][dim]{empty_char * empty}[/dim]"
    return f"{fill_char * filled}{empty_char * empty}"


def _method_icon(method: DownloadMethod) -> str:
    if not _supports_unicode():
        return "TOR" if method == DownloadMethod.TORRENT else "HTTP"
    return "🧲" if method == DownloadMethod.TORRENT else "🌐"


def print_banner() -> None:
    banner = Text.from_markup(
        "[bold cyan]  ___ _    ___    ___ _       ___                  _              _         \n"
        " / __| |  |_ _|__| __| |_ ___|   \\ _____ __ ___ _ | |___  __ _ __| |___ _ _ \n"
        "| (__| |__ | |___| _|| ' \\___| |) / _ \\ V  V / ' \\| / _ \\/ _` / _` / -_) '_|\n"
        " \\___|____|___|  |___|_||_|  |___/\\___/\\_/\\_/|_||_|_\\___/\\__,_\\__,_\\___|_|[/bold cyan]\n"
    )
    panel = Panel(
        banner,
        subtitle="Type [bold]help[/bold] for commands",
        border_style="cyan",
    )
    console.print(panel)

    if HAS_LIBTORRENT:
        console.print("  [dim][green]✓[/green] libtorrent available — embedded torrent downloads enabled[/dim]")
    else:
        console.print("  [dim][yellow]![/yellow] libtorrent not found — torrents will be opened with system default client[/dim]")
    console.print()

    console.print("  ready to go.")
    console.print("  — paste a url to start")
    console.print("  — or type a keyword to search")
    console.print()


def print_task_added(task: DownloadTask) -> None:
    console.print(f"  [bold green]＋[/bold green] Task [bold]#{task.id}[/bold] added: [cyan]{task.url}[/cyan]")


def print_task_update(task: DownloadTask) -> None:
    """Print a one-line status update for a task."""
    style, icon = _status_style(task.status)

    parts = [f"  {icon} [bold]#{task.id}[/bold]"]

    if task.gallery:
        title = task.gallery.title_jpn or task.gallery.title
        if len(title) > 60:
            title = title[:57] + "..."
        parts.append(f"[{style}]{title}[/{style}]")

    parts.append(f"[{style}]{task.status.value}[/{style}]")

    if task.status == TaskStatus.DOWNLOADING:
        pct = int(task.progress * 100)
        bar_filled = pct // 5
        bar_empty = 20 - bar_filled
        bar = _progress_bar(bar_filled, bar_empty, markup=True)
        method_icon = _method_icon(task.method)
        parts.append(f"{bar} {pct}% ({task.downloaded}/{task.total}) {method_icon}")

    if task.error:
        parts.append(f"[red]{task.error}[/red]")

    console.print(" ".join(parts))


def build_status_table(
    tasks: list[DownloadTask],
    *,
    max_rows: int | None = None,
    compact: bool = False,
) -> Table:
    """Build and return a Rich Table showing all task statuses."""
    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("#", style="bold", width=4)
    table.add_column(
        "Title",
        min_width=20 if compact else 30,
        max_width=42 if compact else 50,
        overflow="ellipsis",
        no_wrap=compact,
    )
    table.add_column("Status", width=14, no_wrap=True)
    table.add_column("Progress", width=24, no_wrap=True)
    table.add_column("Method", width=8, no_wrap=True)
    table.add_column("Info", max_width=30, overflow="ellipsis", no_wrap=compact)

    hidden_count = 0
    visible_tasks = tasks
    if max_rows is not None and len(tasks) > max_rows:
        visible_count = max(0, max_rows - 1)
        visible_tasks = tasks[:visible_count]
        hidden_count = len(tasks) - visible_count

    for task in visible_tasks:
        style, icon = _status_style(task.status)

        title = task.short_title
        status_text = f"{icon} {task.status.value}"

        # Progress bar
        if task.total > 0:
            pct = int(task.progress * 100)
            bar_width = 16 if compact else 20
            bar_filled = min(bar_width, max(0, round(pct * bar_width / 100)))
            bar_empty = bar_width - bar_filled
            progress = f"{_progress_bar(bar_filled, bar_empty)} {pct}%"
        else:
            progress = "-" if compact or not _supports_unicode() else "—"

        method = task.method.value
        info = task.error or (f"{task.downloaded}/{task.total}" if task.total else "")

        table.add_row(
            str(task.id),
            f"[{style}]{title}[/{style}]",
            f"[{style}]{status_text}[/{style}]",
            progress,
            method,
            info,
        )

    if hidden_count:
        table.add_row(
            "...",
            f"[dim]{hidden_count} more task(s) hidden; enlarge terminal or use status[/dim]",
            "",
            "",
            "",
            "",
        )

    return table


def print_status_table(tasks: list[DownloadTask]) -> None:
    """Print a static table showing all task statuses."""
    if not tasks:
        console.print("  [dim]No tasks.[/dim]")
        return
    console.print(build_status_table(tasks))


def live_status_display(
    get_tasks: Callable[[], List[DownloadTask]],
    refresh_rate: float = 0.5,
) -> None:
    """Show a live-updating status table that refreshes until all tasks finish or Ctrl+C.

    Args:
        get_tasks: Callable that returns the current list of tasks.
        refresh_rate: Seconds between refreshes.
    """
    active_statuses = {"queued", "fetching_info", "checking_torrent", "downloading", "seeding"}
    live_console = _make_live_console()

    def _row_budget() -> int:
        # Header, footer, table borders, and a little breathing room.
        return max(1, live_console.size.height - 8)

    def _make_display() -> Group:
        tasks = get_tasks()
        if not tasks:
            t = Table(show_header=False, border_style="dim")
            t.add_row("[dim]No tasks.[/dim]")
            table = t
        else:
            table = build_status_table(tasks, max_rows=_row_budget(), compact=True)

        return Group(
            Text.from_markup("Live status  [bold]Ctrl+C[/bold] to return"),
            table,
            Text.from_markup("[dim]Large lists are clipped to the current window height.[/dim]"),
        )

    try:
        with Live(
            _make_display(),
            console=live_console,
            refresh_per_second=4,
            screen=True,
            transient=True,
            redirect_stderr=False,
            vertical_overflow="ellipsis",
        ) as live:
            while True:
                time.sleep(refresh_rate)
                live.update(_make_display())
                # Auto-exit when no more active tasks
                tasks = get_tasks()
                if tasks and not any(t.status.value in active_statuses for t in tasks):
                    # Give one final refresh so the user sees the final state
                    live.update(_make_display())
                    break
    except KeyboardInterrupt:
        pass
    live_console.print("  [dim]Exited live status.[/dim]")


def print_help() -> None:
    groups = [
        (
            "Download",
            [
                ("add <url> [-p preset]", "Add a gallery download task"),
                ("<url> [-p preset]", "Paste a gallery URL directly to add"),
                ("<image page url>", "Resolve a gallery image page back to its gallery"),
                ("<listing url> [-p preset]", "Paste a tag/uploader/category URL for bulk download"),
                ("<keyword>", "Search galleries when auto-detect is enabled"),
                ("search [-e] [-ex] <keyword>", "Search E-Hentai, ExHentai, or both"),
            ],
        ),
        (
            "Tasks",
            [
                ("status / s", "Show all task statuses"),
                ("status -live", "Show live-refreshing task statuses"),
                ("status -clear", "Remove finished tasks from status"),
                ("cancel", "Open the bulk cancel menu"),
                ("cancel <id>", "Cancel one task by task id"),
                ("cancel all", "Cancel all active tasks"),
                ("cancel <start> <end>", "Cancel active tasks in an inclusive id range"),
            ],
        ),
        (
            "History",
            [
                ("history", "Browse download history"),
                ("history -search <keyword>", "Search download history"),
                ("history -clear", "Open history cleanup menu"),
                ("history -bulk", "Open history in bulk-select mode"),
                ("history -bulk -search <keyword>", "Search history, then bulk-select results"),
            ],
        ),
        (
            "Config & Files",
            [
                ("folder / f", "Open download directory in file explorer"),
                ("config", "Interactive config editor"),
                ("config show", "Show current configuration"),
                ("config set <key> <val>", "Update a config value"),
            ],
        ),
        (
            "General",
            [
                ("github / repo", "Open GitHub repository in browser"),
                ("clear", "Clear screen"),
                ("help / h", "Show this help"),
                ("quit / q", "Exit (waits for active downloads)"),
            ],
        ),
    ]

    for title, commands in groups:
        help_table = Table(
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            title=title,
        )
        help_table.add_column("Command", style="bold green", width=32)
        help_table.add_column("Description")

        for cmd, desc in commands:
            help_table.add_row(cmd, desc)

        console.print(help_table)

    console.print("  [dim]Tip: Use arrow keys in menus, Enter to confirm, Ctrl-C to go back[/dim]")


def print_error(msg: str) -> None:
    console.print(f"  [bold red]✗[/bold red] {msg}")


def print_info(msg: str) -> None:
    console.print(f"  [cyan]ℹ[/cyan] {msg}")


def print_success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green] {msg}")
