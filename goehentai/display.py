"""Rich-based display helpers for the interactive shell."""

from __future__ import annotations

from rich.console import Console
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
        "[bold cyan]   ____       _____ _   _            _        _ \n"
        "  / ___| ___ | ____| | | | ___ _ __ | |_ __ _(_)\n"
        " | |  _ / _ \\|  _| | |_| |/ _ \\ '_ \\| __/ _` | |\n"
        " | |_| | (_) | |___|  _  |  __/ | | | || (_| | |\n"
        "  \\____|\\___/|_____|_| |_|\\___|_| |_|\\__\\__,_|_|[/bold cyan]\n"
    )
    panel = Panel(
        banner,
        subtitle="[dim]Type [bold]help[/bold] for commands[/dim]",
        border_style="cyan",
    )
    console.print(panel)

    if HAS_LIBTORRENT:
        console.print("  [green]✓[/green] libtorrent available — embedded torrent downloads enabled")
    else:
        console.print("  [yellow]![/yellow] libtorrent not found — torrents saved as .torrent files")
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


def print_status_table(tasks: list[DownloadTask]) -> None:
    """Print a table showing all task statuses."""
    if not tasks:
        console.print("  [dim]No tasks.[/dim]")
        return

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

    console.print(table)


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
        ("status / s",            "Show all task statuses"),
        ("status -clear",         "Remove finished tasks from status"),
        ("cancel",                "Interactive cancel (or cancel <id>)"),
        ("folder / f",            "Open download directory in file explorer"),
        ("config",                "Interactive config editor"),
        ("config show",           "Show current configuration"),
        ("config set <key> <val>","Update a config value"),
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
