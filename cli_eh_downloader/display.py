"""Rich-based display helpers for the interactive shell."""

from __future__ import annotations

import time
from typing import Callable, List

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import DownloadMethod, DownloadTask, TaskStatus
from .torrent import HAS_LIBTORRENT

console = Console()

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
    console.print("  — or type [cyan]search <keyword>[/cyan] to find something")
    console.print()


def print_task_added(task: DownloadTask) -> None:
    console.print(f"  [bold green]＋[/bold green] Task [bold]#{task.id}[/bold] added: [cyan]{task.url}[/cyan]")


def print_task_update(task: DownloadTask) -> None:
    """Print a one-line status update for a task."""
    style, icon = _STATUS_STYLE.get(task.status, ("", "?"))

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
        bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim]"
        method_icon = "🧲" if task.method == DownloadMethod.TORRENT else "🌐"
        parts.append(f"{bar} {pct}% ({task.downloaded}/{task.total}) {method_icon}")

    if task.error:
        parts.append(f"[red]{task.error}[/red]")

    console.print(" ".join(parts))


def build_status_table(tasks: list[DownloadTask]) -> Table:
    """Build and return a Rich Table showing all task statuses."""
    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("#", style="bold", width=4)
    table.add_column("Title", min_width=30, max_width=50)
    table.add_column("Status", width=14)
    table.add_column("Progress", width=24)
    table.add_column("Method", width=8)
    table.add_column("Info", max_width=30)

    for task in tasks:
        style, icon = _STATUS_STYLE.get(task.status, ("", "?"))

        title = task.short_title
        status_text = f"{icon} {task.status.value}"

        # Progress bar
        if task.total > 0:
            pct = int(task.progress * 100)
            bar_filled = pct // 5
            bar_empty = 20 - bar_filled
            progress = f"{'█' * bar_filled}{'░' * bar_empty} {pct}%"
        else:
            progress = "—"

        method = task.method.value
        info = task.error or f"{task.downloaded}/{task.total}" if task.total else ""

        table.add_row(
            str(task.id),
            f"[{style}]{title}[/{style}]",
            f"[{style}]{status_text}[/{style}]",
            progress,
            method,
            info,
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

    def _make_display() -> Table:
        tasks = get_tasks()
        if not tasks:
            t = Table(show_header=False, border_style="dim")
            t.add_row("[dim]No tasks.[/dim]")
            return t
        return build_status_table(tasks)

    console.print("  [dim]Live status — press [bold]Ctrl+C[/bold] to return[/dim]\n")

    try:
        with Live(_make_display(), console=console, refresh_per_second=4, transient=False) as live:
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
    console.print("  [dim]Exited live status.[/dim]")


def print_help() -> None:
    help_table = Table(show_header=True, header_style="bold cyan", border_style="dim", title="Commands")
    help_table.add_column("Command", style="bold green", width=28)
    help_table.add_column("Description")

    commands = [
        ("add <url>",             "Add a gallery download task"),
        ("<url>",                 "Shortcut — paste a URL directly to add"),
        ("search <keyword>",     "Search galleries and browse results"),
        ("history",               "Browse download history (open, re-download)"),
        ("history -clear",        "Clear all download history"),
        ("status / s",            "Show all task statuses (live refresh if active)"),
        ("status -clear",         "Remove finished tasks from status"),
        ("cancel",                "Interactive cancel (or cancel <id>)"),
        ("folder / f",            "Open download directory in file explorer"),
        ("config",                "Interactive config editor"),
        ("config show",           "Show current configuration"),
        ("config set <key> <val>","Update a config value"),
        ("github / repo",         "Open GitHub repository in browser"),
        ("clear",                 "Clear screen"),
        ("help / h",              "Show this help"),
        ("quit / q",              "Exit (waits for active downloads)"),
    ]
    for cmd, desc in commands:
        help_table.add_row(cmd, desc)

    console.print(help_table)
    console.print("  [dim]Tip: Use ↑↓ arrow keys in menus, Ctrl-C to go back[/dim]")


def print_error(msg: str) -> None:
    console.print(f"  [bold red]✗[/bold red] {msg}")


def print_info(msg: str) -> None:
    console.print(f"  [cyan]ℹ[/cyan] {msg}")


def print_success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green] {msg}")
