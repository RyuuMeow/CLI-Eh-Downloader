"""Interactive shell — non-blocking REPL for managing downloads."""

from __future__ import annotations

import asyncio
import os
import shlex

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory

from .config import Config
from .display import (
    console,
    print_banner,
    print_error,
    print_help,
    print_info,
    print_status_table,
    print_success,
    print_task_added,
    print_task_update,
)
from .models import DownloadMethod, DownloadTask, SearchResult, TaskStatus
from .task_manager import TaskManager
from .utils import GALLERY_URL_PATTERN, format_size


class Shell:
    """Interactive command shell with background downloads."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.manager = TaskManager(config)
        self._session: PromptSession[str] = PromptSession(
            history=InMemoryHistory(),
        )

    def run(self) -> None:
        """Start the interactive shell loop."""
        print_banner()

        from prompt_toolkit.patch_stdout import patch_stdout

        try:
            with patch_stdout(raw=True):
                while True:
                    try:
                        raw = self._session.prompt(
                            HTML("<ansigreen><b>goeh</b></ansigreen><ansigray>&gt; </ansigray>")
                        )
                        line = raw.strip()
                        if not line:
                            continue
                        self._dispatch(line)
                    except KeyboardInterrupt:
                        console.print("\n  [dim]Ctrl+C \u2014 type [bold]quit[/bold] to exit[/dim]")
                    except EOFError:
                        break
        finally:
            self._shutdown()

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, line: str) -> None:
        if GALLERY_URL_PATTERN.match(line):
            self._cmd_add(line)
            return

        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()

        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        match cmd:
            case "add" | "a":
                if not args:
                    print_error("Usage: add <url>")
                    return
                self._cmd_add(args[0])

            case "search" | "find":
                if not args:
                    print_error("Usage: search <keyword>")
                    return
                self._cmd_search(" ".join(args))

            case "status" | "s":
                self._cmd_status(args)

            case "cancel":
                self._cmd_cancel(args)

            case "folder" | "f":
                self._cmd_folder()

            case "config":
                self._cmd_config(args)

            case "clear" | "cls":
                console.clear()
                print_banner()

            case "help" | "h":
                print_help()

            case "history":
                self._cmd_history(args)

            case "quit" | "q" | "exit":
                raise EOFError()

            case _:
                print_error(f"Unknown command: {cmd}  (type [bold]help[/bold] for commands)")

    # ------------------------------------------------------------------
    # Add command
    # ------------------------------------------------------------------

    def _cmd_add(self, url: str) -> None:
        parsed = GALLERY_URL_PATTERN.match(url)
        if not parsed:
            print_error("Invalid gallery URL.")
            return

        console.print("  [cyan]Fetching gallery info...[/cyan]")
        try:
            gallery, torrents = self.manager.fetch_info_and_torrents_sync(url)
        except Exception as e:
            print_error(f"Failed to fetch gallery: {e}")
            return

        title = gallery.title_jpn or gallery.title
        console.print(f"  [bold]{title}[/bold]")

        if self.config.auto_select_best:
            self._auto_download(url, gallery, torrents)
        else:
            self._interactive_download(url, gallery, torrents)

    def _auto_download(self, url, gallery, torrents):
        """Auto-select best method and start download without prompts."""
        from .torrent import HAS_LIBTORRENT

        if torrents and self.config.prefer_torrent:
            best = torrents[0]  # sorted by seeds

            if HAS_LIBTORRENT and best.seeds > 0:
                console.print(f"  [green]Using torrent[/green] (Seeds: {best.seeds})")
                task = self.manager.add_task(
                    url,
                    gallery=gallery,
                    force_method=DownloadMethod.TORRENT,
                    selected_torrent=best,
                    on_update=self._on_task_update,
                )
                print_task_added(task)
            else:
                # Torrent-only: save .torrent, open with client, record in history. Done.
                torrent_path = self._save_and_open_torrent(best)
                if torrent_path:
                    from .downloader import save_torrent_metadata
                    save_torrent_metadata(gallery, torrent_path, self.config)
                    print_success("Torrent saved & opened. Download via torrent client.")
                else:
                    # Fallback to direct if torrent save failed
                    self._start_direct(url, gallery)
            return

        # No torrent available → direct download
        self._start_direct(url, gallery)

    def _start_direct(self, url, gallery):
        """Start a direct image download task."""
        task = self.manager.add_task(
            url,
            gallery=gallery,
            force_method=DownloadMethod.DIRECT,
            on_update=self._on_task_update,
        )
        print_task_added(task)

    def _interactive_download(self, url, gallery, torrents):
        """Show interactive selector for download method."""
        import questionary
        from .torrent import HAS_LIBTORRENT

        size_str = format_size(int(gallery.filesize)) if gallery.filesize.isdigit() else gallery.filesize

        # Build options list: index 0 = direct, 1..N = torrents, -1 = cancel
        options = [{"method": DownloadMethod.DIRECT}]
        choices = [
            questionary.Choice(
                title=f"Direct Download  |  {gallery.file_count} images  |  {size_str}",
                value=0,
            )
        ]

        for i, t in enumerate(torrents):
            label = "Torrent" if HAS_LIBTORRENT else "Torrent \u2192 open client"
            choices.append(questionary.Choice(
                title=f"{label}  |  {t.size}  |  Seeds: {t.seeds}  |  Peers: {t.peers}  |  {t.name}",
                value=i + 1,
            ))
            options.append({"method": DownloadMethod.TORRENT, "torrent": t})

        choices.append(questionary.Choice(title="Cancel", value=-1))

        try:
            idx = questionary.select(
                "Select download method:",
                choices=choices,
                instruction="(\u2191\u2193 select, Enter confirm, Ctrl-C cancel)",
            ).ask()
        except KeyboardInterrupt:
            idx = -1

        if idx is None or idx == -1:
            print_info("Cancelled.")
            return

        choice = options[idx]
        method = choice["method"]
        torrent_info = choice.get("torrent")

        # Torrent without libtorrent: save .torrent, record in history, done.
        if method == DownloadMethod.TORRENT and not HAS_LIBTORRENT and torrent_info:
            torrent_path = self._save_and_open_torrent(torrent_info)
            if torrent_path:
                from .downloader import save_torrent_metadata
                save_torrent_metadata(gallery, torrent_path, self.config)
                print_success("Torrent saved & opened. Download via torrent client.")
            return

        task = self.manager.add_task(
            url,
            gallery=gallery,
            force_method=method,
            selected_torrent=torrent_info,
            on_update=self._on_task_update,
        )
        print_task_added(task)

    def _save_and_open_torrent(self, torrent_info) -> str | None:
        """Download .torrent file and open with system torrent client. Returns path or None."""
        console.print("  [cyan]Downloading .torrent...[/cyan]")
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._async_download_torrent(torrent_info), self.manager._loop
            )
            torrent_path = future.result(timeout=30)
            print_success(f"Saved: {torrent_path}")

            # Try to open with system torrent client
            import shutil
            import subprocess

            # Try qBittorrent first (supports --save-path)
            gallery_dir = str(
                __import__("pathlib").Path(self.config.download_dir).resolve()
            )
            qbt = shutil.which("qbittorrent")
            if qbt:
                subprocess.Popen([qbt, f"--save-path={gallery_dir}", torrent_path])
                print_success("Opened with qBittorrent.")
            else:
                try:
                    os.startfile(torrent_path)
                    print_info("Opened with default torrent client.")
                except OSError:
                    print_info(f"No torrent client found. File: {torrent_path}")
            return torrent_path

        except Exception as e:
            print_error(f"Failed to save .torrent: {e}")
            return None

    async def _async_download_torrent(self, torrent_info):
        from .torrent import download_torrent_file
        from .downloader import _get_torrents_dir
        client = self.manager._ensure_client()
        return await download_torrent_file(client, torrent_info, str(_get_torrents_dir(self.config)))

    # ------------------------------------------------------------------
    # Search command
    # ------------------------------------------------------------------

    def _cmd_search(self, query: str) -> None:
        import questionary

        console.print(f"  [cyan]Searching: {query}...[/cyan]")
        try:
            results = self.manager.search_sync(query)
        except Exception as e:
            print_error(f"Search failed: {e}")
            return

        if not results:
            print_info("No results found.")
            return

        console.print(f"  [green]Found {len(results)} results[/green]\n")

        downloaded_gids: set[int] = set()

        while True:
            console.clear()
            print_banner()
            console.print(f"  [cyan]Searching: {query}...[/cyan]")
            console.print(f"  [green]Found {len(results)} results[/green]\n")

            # Build choices with downloaded markers
            choices = []
            for i, r in enumerate(results):
                prefix = "\u2713 " if r.gid in downloaded_gids else "  "
                label = (
                    f"{prefix}[{r.category}] {r.title[:65]}"
                    f"  |  {r.pages}p  |  {r.uploader}"
                )
                choices.append(questionary.Choice(title=label, value=i))

            choices.append(questionary.Choice(title="\u2190 Back to shell", value=-1))

            try:
                selected_idx = questionary.select(
                    f"Search: {query} ({len(results)} results)",
                    choices=choices,
                    instruction="(\u2191\u2193 select, Enter download, Ctrl-C back)",
                ).ask()
            except KeyboardInterrupt:
                selected_idx = -1

            if selected_idx is None or selected_idx == -1:
                print_info("Back to shell.")
                return

            # Download the selected gallery
            selected = results[selected_idx]
            console.print(f"\n  [bold]{selected.title}[/bold]")
            console.print(f"  [dim]{selected.url}[/dim]")

            try:
                gallery, torrents = self.manager.fetch_info_and_torrents_sync(selected.url)
            except Exception as e:
                print_error(f"Failed to fetch: {e}")
                continue

            if self.config.auto_select_best:
                self._auto_download(selected.url, gallery, torrents)
            else:
                self._interactive_download(selected.url, gallery, torrents)

            downloaded_gids.add(selected.gid)

    def _cmd_status(self, args: list[str]) -> None:
        if args and args[0] in ("-clear", "--clear", "clear"):
            self.manager.clear_finished()
            print_success("Cleared completed/failed/cancelled tasks.")
            return
        tasks = self.manager.get_all_tasks()
        print_status_table(tasks)

    # ------------------------------------------------------------------
    # History command
    # ------------------------------------------------------------------

    def _cmd_history(self, args: list[str]) -> None:
        if args and args[0] in ("-clear", "--clear", "clear"):
            self._history_clear()
            return
        self._history_browse()

    def _history_clear(self) -> None:
        import questionary
        from .downloader import _get_meta_dir

        meta_dir = _get_meta_dir(self.config)
        files = list(meta_dir.glob("*.json"))
        if not files:
            print_info("History is empty.")
            return

        confirm = questionary.confirm(
            f"Delete all {len(files)} history entries?",
            default=False,
        ).ask()

        if confirm:
            for f in files:
                f.unlink(missing_ok=True)
            print_success(f"Deleted {len(files)} history entries.")
        else:
            print_info("Cancelled.")

    def _history_browse(self) -> None:
        import json
        import webbrowser
        import questionary
        from .downloader import _get_meta_dir

        meta_dir = _get_meta_dir(self.config)
        files = sorted(meta_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            print_info("No download history yet.")
            return

        # Load all entries
        entries = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["_file"] = f
                entries.append(data)
            except Exception:
                continue

        if not entries:
            print_info("No valid history entries.")
            return

        # Build choice list
        choices = []
        for i, e in enumerate(entries):
            title = e.get("title_jpn") or e.get("title", "?")
            method = e.get("method", "direct")
            date = e.get("downloaded_at", "")[:10]
            method_icon = "\U0001f9f2" if "torrent" in method else "\U0001f310"  # 🧲 or 🌐
            label = f"{method_icon} [{date}] {title[:60]}"
            choices.append(questionary.Choice(title=label, value=i))

        choices.append(questionary.Choice(title="\u2190 Back", value=-1))

        while True:
            try:
                idx = questionary.select(
                    f"Download History ({len(entries)} entries)",
                    choices=choices,
                    instruction="(\u2191\u2193 select, Enter actions, Ctrl-C back)",
                ).ask()
            except KeyboardInterrupt:
                idx = -1

            if idx is None or idx == -1:
                return

            entry = entries[idx]
            self._history_actions(entry)

    def _history_actions(self, entry: dict) -> None:
        import webbrowser
        import questionary

        title = entry.get("title_jpn") or entry.get("title", "?")
        url = entry.get("url", "")
        method = entry.get("method", "direct")
        date = entry.get("downloaded_at", "")[:19].replace("T", " ")

        console.print(f"\n  [bold]{title}[/bold]")
        console.print(f"  [dim]{url}[/dim]")
        console.print(f"  [dim]Method: {method}  |  Date: {date}[/dim]\n")

        # 0=browser, 1=redownload, 2=folder, -1=back
        action_choices = [
            questionary.Choice(title="\U0001f310 Open in browser", value=0),
            questionary.Choice(title="\U0001f504 Re-download (manual select)", value=1),
        ]

        output_dir = entry.get("output_dir", "")
        if output_dir:
            action_choices.append(
                questionary.Choice(title="\U0001f4c2 Open folder", value=2),
            )

        action_choices.append(questionary.Choice(title="\u2190 Back", value=-1))

        try:
            action = questionary.select(
                "Action:",
                choices=action_choices,
                instruction="",
            ).ask()
        except KeyboardInterrupt:
            action = -1

        if action is None or action == -1:
            return

        if action == 0:  # browser
            if url:
                webbrowser.open(url)
                print_success("Opened in browser.")
            else:
                print_error("No URL available.")

        elif action == 1:  # redownload
            if url:
                self._redownload(url)
            else:
                print_error("No URL available.")

        elif action == 2:  # folder
            try:
                os.startfile(output_dir)
            except Exception:
                print_error(f"Could not open: {output_dir}")

    def _redownload(self, url: str) -> None:
        """Re-download a gallery with interactive selection (always manual)."""
        console.print("  [cyan]Fetching gallery info...[/cyan]")
        try:
            gallery, torrents = self.manager.fetch_info_and_torrents_sync(url)
        except Exception as e:
            print_error(f"Failed: {e}")
            return

        title = gallery.title_jpn or gallery.title
        console.print(f"  [bold]{title}[/bold]")
        self._interactive_download(url, gallery, torrents)

    # ------------------------------------------------------------------
    # Cancel command (interactive if no args)
    # ------------------------------------------------------------------

    def _cmd_cancel(self, args: list[str]) -> None:
        if args:
            # Cancel by ID directly
            try:
                task_id = int(args[0])
            except ValueError:
                print_error("Task ID must be a number.")
                return
            if self.manager.cancel_task(task_id):
                print_success(f"Task #{task_id} cancelled.")
            else:
                print_error(f"Cannot cancel task #{task_id}.")
            return

        # Interactive: show list of active tasks
        import questionary

        active = self.manager.get_active_tasks()
        if not active:
            print_info("No active tasks to cancel.")
            return

        choices = []
        for t in active:
            label = f"#{t.id}  {t.short_title}  [{t.status.value}]  {t.downloaded}/{t.total}"
            choices.append(questionary.Choice(title=label, value=t.id))
        choices.append(questionary.Choice(title="\u2190 Back", value=-1))

        try:
            selected = questionary.select(
                "Select task to cancel:",
                choices=choices,
                instruction="(\u2191\u2193 select, Enter cancel, Ctrl-C back)",
            ).ask()
        except KeyboardInterrupt:
            selected = -1

        if selected is None or selected == -1:
            return

        if self.manager.cancel_task(selected):
            print_success(f"Task #{selected} cancelled.")
        else:
            print_error(f"Cannot cancel task #{selected}.")

    # ------------------------------------------------------------------
    # Folder command
    # ------------------------------------------------------------------

    def _cmd_folder(self) -> None:
        from pathlib import Path
        path = Path(self.config.download_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(path))
            print_success(f"Opened: {path}")
        except Exception as e:
            print_error(f"Could not open folder: {e}")

    # ------------------------------------------------------------------
    # Config command (interactive if no args)
    # ------------------------------------------------------------------

    def _cmd_config(self, args: list[str]) -> None:
        if not args:
            self._interactive_config()
            return

        subcmd = args[0].lower()
        if subcmd == "show":
            self._show_config()
        elif subcmd == "set" and len(args) >= 3:
            self._set_config(args[1], " ".join(args[2:]))
        else:
            print_error("Usage: config | config show | config set <key> <value>")

    def _interactive_config(self) -> None:
        """Interactive config editor with arrow key selection."""
        import questionary

        editable = [
            ("download_dir", "Download Directory", self.config.download_dir),
            ("max_parallel", "Max Parallel Downloads", str(self.config.max_parallel)),
            ("rate_limit_delay", "Rate Limit Delay (seconds)", str(self.config.rate_limit_delay)),
            ("retry_count", "Retry Count", str(self.config.retry_count)),
            ("prefer_torrent", "Prefer Torrent", str(self.config.prefer_torrent)),
            ("auto_select_best", "Auto Select Best Method", str(self.config.auto_select_best)),
            ("ipb_member_id", "ExH Cookie: ipb_member_id", self.config.ipb_member_id or "(not set)"),
            ("ipb_pass_hash", "ExH Cookie: ipb_pass_hash", ("***" + self.config.ipb_pass_hash[-4:]) if self.config.ipb_pass_hash else "(not set)"),
            ("igneous", "ExH Cookie: igneous", ("***" + self.config.igneous[-4:]) if self.config.igneous else "(not set)"),
        ]

        _BACK = "__BACK__"

        while True:
            choices = []
            for key, label, val in editable:
                choices.append(questionary.Choice(
                    title=f"{label}: {val}",
                    value=key,
                ))
            choices.append(questionary.Choice(title="\u2190 Back", value=_BACK))

            try:
                selected_key = questionary.select(
                    "Config Editor (select to edit):",
                    choices=choices,
                    instruction="(\u2191\u2193 select, Enter edit, Ctrl-C back)",
                ).ask()
            except KeyboardInterrupt:
                selected_key = None

            if not selected_key or selected_key == _BACK:
                return

            # Find current value
            current = getattr(self.config, selected_key, "")
            if selected_key in ("ipb_pass_hash", "igneous") and current:
                current = ""  # don't prefill secrets

            new_value = questionary.text(
                f"New value for {selected_key}:",
                default=str(current) if current else "",
            ).ask()

            if new_value is None:
                continue

            self._set_config(selected_key, new_value)

            # Refresh the displayed values
            editable = [
                ("download_dir", "Download Directory", self.config.download_dir),
                ("max_parallel", "Max Parallel Downloads", str(self.config.max_parallel)),
                ("rate_limit_delay", "Rate Limit Delay (seconds)", str(self.config.rate_limit_delay)),
                ("retry_count", "Retry Count", str(self.config.retry_count)),
                ("prefer_torrent", "Prefer Torrent", str(self.config.prefer_torrent)),
                ("auto_select_best", "Auto Select Best Method", str(self.config.auto_select_best)),
                ("ipb_member_id", "ExH Cookie: ipb_member_id", self.config.ipb_member_id or "(not set)"),
                ("ipb_pass_hash", "ExH Cookie: ipb_pass_hash", ("***" + self.config.ipb_pass_hash[-4:]) if self.config.ipb_pass_hash else "(not set)"),
                ("igneous", "ExH Cookie: igneous", ("***" + self.config.igneous[-4:]) if self.config.igneous else "(not set)"),
            ]

    def _show_config(self) -> None:
        from rich.table import Table

        table = Table(
            title="Configuration",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )
        table.add_column("Key", style="bold")
        table.add_column("Value")

        c = self.config
        table.add_row("download_dir", c.download_dir)
        table.add_row("max_parallel", str(c.max_parallel))
        table.add_row("rate_limit_delay", f"{c.rate_limit_delay}s")
        table.add_row("retry_count", str(c.retry_count))
        table.add_row("prefer_torrent", str(c.prefer_torrent))
        table.add_row("auto_select_best", str(c.auto_select_best))
        table.add_row("ipb_member_id", c.ipb_member_id or "[dim]not set[/dim]")
        table.add_row("ipb_pass_hash", ("***" + c.ipb_pass_hash[-4:]) if c.ipb_pass_hash else "[dim]not set[/dim]")
        table.add_row("igneous", ("***" + c.igneous[-4:]) if c.igneous else "[dim]not set[/dim]")
        table.add_row("exhentai_ready", "[green]yes[/green]" if c.has_exhentai_cookies else "[red]no[/red]")

        console.print(table)

    def _set_config(self, key: str, value: str) -> None:
        key = key.lower().replace("-", "_")

        config_map: dict[str, str] = {
            "download_dir": "download_dir",
            "max_parallel": "max_parallel",
            "rate_limit_delay": "rate_limit_delay",
            "retry_count": "retry_count",
            "retry_delay": "retry_delay",
            "prefer_torrent": "prefer_torrent",
            "auto_select_best": "auto_select_best",
            "ipb_member_id": "ipb_member_id",
            "ipb_pass_hash": "ipb_pass_hash",
            "igneous": "igneous",
            "sk": "sk",
            "cookie_id": "ipb_member_id",
            "cookie_hash": "ipb_pass_hash",
            "parallel": "max_parallel",
        }

        attr = config_map.get(key)
        if not attr:
            print_error(f"Unknown config key: {key}")
            return

        # Type coercion
        if attr in ("max_parallel", "retry_count"):
            try:
                setattr(self.config, attr, int(value))
            except ValueError:
                print_error(f"{key} must be an integer.")
                return
        elif attr in ("rate_limit_delay", "retry_delay"):
            try:
                setattr(self.config, attr, float(value))
            except ValueError:
                print_error(f"{key} must be a number.")
                return
        elif attr in ("prefer_torrent", "auto_select_best"):
            setattr(self.config, attr, value.lower() in ("true", "1", "yes"))
        else:
            setattr(self.config, attr, value)

        self.config.save()
        print_success(f"{key} = {getattr(self.config, attr)}")

    # ------------------------------------------------------------------
    # Callbacks & lifecycle
    # ------------------------------------------------------------------

    def _on_task_update(self, task: DownloadTask) -> None:
        if task.status in (
            TaskStatus.FETCHING_INFO,
            TaskStatus.CHECKING_TORRENT,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            print_task_update(task)

    def _shutdown(self) -> None:
        active = self.manager.get_active_tasks()
        if active:
            console.print(f"\n  [yellow]Waiting for {len(active)} active task(s)...[/yellow]")
            console.print("  [dim]Ctrl+C to force quit.[/dim]")

        try:
            self.manager.shutdown(wait=True)
        except KeyboardInterrupt:
            console.print("  [red]Force quit.[/red]")
            self.manager.shutdown(wait=False)

        console.print("  [dim]Bye![/dim]")
