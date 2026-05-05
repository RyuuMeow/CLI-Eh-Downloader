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
    live_status_display,
    print_banner,
    print_error,
    print_help,
    print_info,
    print_status_table,
    print_success,
    print_task_added,
    print_task_update,
)
from .models import BulkDownloadConfig, BulkDownloadMode, DownloadMethod, DownloadTask, FetchMode, SearchResult, TaskStatus
from .task_manager import TaskManager
from .utils import GALLERY_URL_PATTERN, format_size, is_listing_url, matches_keyword_filter


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
                            HTML("<ansigreen><b>meow </b></ansigreen><ansigray>&gt; </ansigray>")
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

        # Detect listing pages (tag, uploader, category, etc.)
        listing_type = is_listing_url(line.strip())
        if listing_type:
            self._cmd_bulk(line.strip(), listing_type)
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

            case "github" | "repo":
                import webbrowser
                webbrowser.open("https://github.com/RyuuMeow/CLI-Eh-Downloader")
                print_success("Opened GitHub repo in browser.")

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

        self._default_download(url, gallery, torrents)

    def _auto_download(self, url, gallery, torrents) -> bool:
        """Auto-select best method and start download without prompts. Returns True."""
        from .torrent import HAS_LIBTORRENT

        # Filter out 0-seed torrents — they are dead and not useful
        viable_torrents = [t for t in torrents if t.seeds > 0]

        if viable_torrents and self.config.prefer_torrent:
            best = viable_torrents[0]  # sorted by seeds

            if HAS_LIBTORRENT:
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
            return True

        # No viable torrent (all 0 seeds or none available) → direct download
        if torrents and not viable_torrents:
            print_info("All torrents have 0 seeds, using direct download.")
        self._start_direct(url, gallery)
        return True

    def _start_direct(self, url, gallery):
        """Start a direct image download task."""
        task = self.manager.add_task(
            url,
            gallery=gallery,
            force_method=DownloadMethod.DIRECT,
            on_update=self._on_task_update,
        )
        print_task_added(task)
        return True

    def _default_download(self, url, gallery, torrents) -> bool:
        mode = self.config.default_download_mode
        if mode == "direct":
            return self._start_direct(url, gallery)
        if mode == "ask":
            return self._interactive_download(url, gallery, torrents)
        return self._auto_download(url, gallery, torrents)

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
            return False

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
            return True

        task = self.manager.add_task(
            url,
            gallery=gallery,
            force_method=method,
            selected_torrent=torrent_info,
            on_update=self._on_task_update,
        )
        print_task_added(task)
        return True

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
        import urllib.parse
        import webbrowser

        current_page = 0
        downloaded_gids: set[int] = set()
        opened_gids: set[int] = set()
        bulk_mode = self.config.search_bulk_mode_default
        selected_results: dict[int, SearchResult] = {}
        cursor_choice: tuple[str, int | None] = ("result", 0)
        current_search_url = f"https://e-hentai.org/?f_search={urllib.parse.quote(query)}"

        # Fetch first page
        console.print(f"  [cyan]Searching: {query}...[/cyan]")
        try:
            search_page = self.manager.search_sync(query, page=current_page)
        except Exception as e:
            print_error(f"Search failed: {e}")
            return

        if not search_page.results:
            print_info("No results found.")
            return
        if self.config.search_open_result_website_automatically:
            webbrowser.open(current_search_url)

        while True:
            results = search_page.results
            total = search_page.total_results

            console.clear()
            print_banner()

            # Header with pagination info
            total_str = f"{total:,}" if total else f"{len(results)}+"
            page_display = current_page + 1
            console.print(f"  [cyan]Search: {query}[/cyan]  |  [green]{total_str} results[/green]  |  [yellow]Page {page_display}[/yellow]\n")

            selected_action = self._search_select(
                f"Page {page_display}  ({len(results)} on this page)",
                results,
                downloaded_gids,
                opened_gids,
                selected_results,
                bulk_mode,
                search_page.has_next,
                search_page.has_prev,
                page_display,
                cursor_choice,
            )

            if selected_action is None:
                selected_action = ("back", None)

            action, selected_idx = selected_action
            cursor_choice = selected_action

            if action == "back":
                return

            if action == "next":
                next_url = search_page.next_url
                current_page += 1
                console.print(f"\n  [cyan]Loading page {current_page + 1}...[/cyan]")
                try:
                    search_page = self.manager.search_sync(query, page=current_page, url_override=next_url)
                    current_search_url = next_url
                    cursor_choice = ("result", 0)
                    if self.config.search_open_result_website_automatically:
                        webbrowser.open(current_search_url)
                except Exception as e:
                    print_error(f"Failed to load page: {e}")
                    current_page -= 1
                continue

            if action == "prev":
                prev_url = search_page.prev_url
                current_page -= 1
                console.print(f"\n  [cyan]Loading page {current_page + 1}...[/cyan]")
                try:
                    search_page = self.manager.search_sync(query, page=current_page, url_override=prev_url)
                    current_search_url = prev_url
                    cursor_choice = ("result", 0)
                    if self.config.search_open_result_website_automatically:
                        webbrowser.open(current_search_url)
                except Exception as e:
                    print_error(f"Failed to load page: {e}")
                    current_page += 1
                continue

            if action == "open_search_page":
                webbrowser.open(current_search_url)
                print_success("Opened search page in browser.")
                continue

            if action == "toggle_bulk":
                bulk_mode = not bulk_mode
                continue

            if action == "bulk_browser":
                opened = 0
                for result in selected_results.values():
                    webbrowser.open(result.url)
                    opened_gids.add(result.gid)
                    opened += 1
                print_success(f"Opened {opened} selected result(s) in browser.")
                continue

            if action == "bulk_download":
                self._search_bulk_download(list(selected_results.values()), downloaded_gids)
                continue

            if action != "result" or selected_idx is None:
                continue

            # Show action menu for selected gallery
            selected = results[selected_idx]
            if self.config.search_open_gallery_website_onclick:
                webbrowser.open(selected.url)
                opened_gids.add(selected.gid)

            if self.config.search_download_gallery_onclick:
                self._search_download_result(selected, downloaded_gids)

            if self.config.search_no_sub_menu:
                continue

            action = self._search_action_menu(selected, opened_gids)

            if action == "download":
                self._search_download_result(selected, downloaded_gids)

    def _search_select(
        self,
        title: str,
        results: list[SearchResult],
        downloaded_gids: set[int],
        opened_gids: set[int],
        selected_results: dict[int, SearchResult],
        bulk_mode: bool,
        has_next: bool,
        has_prev: bool,
        page_display: int,
        cursor_choice: tuple[str, int | None],
    ) -> tuple[str, int | None] | None:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
        from questionary import utils
        from questionary.constants import DEFAULT_QUESTION_PREFIX, DEFAULT_SELECTED_POINTER
        from questionary.prompts import common
        from questionary.prompts.common import Choice, InquirerControl, Separator
        from questionary.question import Question
        from questionary.styles import merge_styles_default

        result_choices = [
            Choice(
                title=self._search_choice_title(
                    result,
                    downloaded_gids,
                    opened_gids,
                    selected_results,
                    bulk_mode,
                ),
                value=("result", i),
            )
            for i, result in enumerate(results)
        ]
        choices = [*result_choices]

        choices.append(Separator("-------------------"))
        if has_next:
            choices.append(Choice(title=f"\u27a1\ufe0f Next page (page {page_display + 1})", value=("next", None)))
        if has_prev:
            choices.append(Choice(title=f"\u2b05\ufe0f Previous page (page {page_display - 1})", value=("prev", None)))

        bulk_toggle_choice = Choice(title="", value=("toggle_bulk", None))
        select_page_choice = Choice(title="Select All (this page)", value=("select_page", None))
        unselect_page_choice = Choice(title="Unselect All (this page)", value=("unselect_page", None))
        bulk_download_choice = Choice(title="", value=("bulk_download", None))
        bulk_browser_choice = Choice(title="", value=("bulk_browser", None))

        choices.append(Separator("-------------------"))
        choices.append(Choice(title="\U0001f310 Open search page in browser", value=("open_search_page", None)))
        choices.append(bulk_toggle_choice)
        if bulk_mode:
            choices.append(Separator("-------------------"))
            choices.extend([
                select_page_choice,
                unselect_page_choice
            ])
            choices.append(Separator("-------------------"))
            choices.extend([
                bulk_download_choice,
                bulk_browser_choice,
            ])
        choices.append(Separator("-------------------"))
        choices.append(Choice(title="\u2190 Back to shell", value=("back", None)))

        def sync_titles() -> None:
            for i, result in enumerate(results):
                result_choices[i].title = self._search_choice_title(
                    result,
                    downloaded_gids,
                    opened_gids,
                    selected_results,
                    bulk_mode,
                )
            bulk_toggle_choice.title = f"Bulk Mode: {'On' if bulk_mode else 'Off'}"
            selected_count = len(selected_results)
            if selected_count:
                bulk_download_choice.title = f"Bulk Download ({selected_count} selected)"
                bulk_browser_choice.title = f"Bulk Open in browser ({selected_count} selected)"
            else:
                bulk_download_choice.title = [("fg:ansibrightblack", "Bulk Download (select at least one)")]
                bulk_browser_choice.title = [("fg:ansibrightblack", "Bulk Open in browser (select at least one)")]

        sync_titles()

        values = {choice.value for choice in choices}
        if cursor_choice[0] == "result" and cursor_choice[1] is not None and results:
            cursor_choice = ("result", min(cursor_choice[1], len(results) - 1))
        if cursor_choice not in values:
            cursor_choice = ("result", 0)

        ic = InquirerControl(
            choices,
            default=None,
            pointer=DEFAULT_SELECTED_POINTER,
            use_indicator=False,
            use_shortcuts=False,
            show_selected=False,
            show_description=True,
            use_arrow_keys=True,
            initial_choice=cursor_choice,
        )

        def get_prompt_tokens():
            return [
                ("class:qmark", DEFAULT_QUESTION_PREFIX),
                ("class:question", f" {title} "),
                ("class:instruction", "(Enter confirm, Ctrl-C back)"),
            ]

        layout = common.create_inquirer_layout(ic, get_prompt_tokens)
        bindings = KeyBindings()

        @bindings.add(Keys.ControlQ, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def _(event):
            event.app.exit(result=None)

        def move_cursor_down(event):
            ic.select_next()
            while not ic.is_selection_valid():
                ic.select_next()

        def move_cursor_up(event):
            ic.select_previous()
            while not ic.is_selection_valid():
                ic.select_previous()

        bindings.add(Keys.Down, eager=True)(move_cursor_down)
        bindings.add(Keys.Up, eager=True)(move_cursor_up)
        bindings.add("j", eager=True)(move_cursor_down)
        bindings.add("k", eager=True)(move_cursor_up)
        bindings.add(Keys.ControlN, eager=True)(move_cursor_down)
        bindings.add(Keys.ControlP, eager=True)(move_cursor_up)

        @bindings.add(Keys.ControlM, eager=True)
        def set_answer(event):
            action, idx = ic.get_pointed_at().value
            if action == "result" and idx is not None and bulk_mode:
                result = results[idx]
                if result.gid in selected_results:
                    selected_results.pop(result.gid, None)
                else:
                    selected_results[result.gid] = result
                sync_titles()
                event.app.invalidate()
            elif action == "select_page":
                for result in results:
                    selected_results[result.gid] = result
                sync_titles()
                event.app.invalidate()
            elif action == "unselect_page":
                for result in results:
                    selected_results.pop(result.gid, None)
                sync_titles()
                event.app.invalidate()
            elif action in ("bulk_download", "bulk_browser") and not selected_results:
                event.app.invalidate()
            else:
                event.app.exit(result=(action, idx))

        @bindings.add(Keys.Any)
        def other(event):
            pass

        question = Question(
            Application(
                layout=layout,
                key_bindings=bindings,
                style=merge_styles_default([None]),
                **utils.used_kwargs({}, Application.__init__),
            )
        )
        return question.ask()

    def _search_choice_title(
        self,
        result: SearchResult,
        downloaded_gids: set[int],
        opened_gids: set[int],
        selected_results: dict[int, SearchResult],
        bulk_mode: bool,
    ):
        marks = []
        if bulk_mode:
            marks.append("[x]" if result.gid in selected_results else "[ ]")
        if result.gid in downloaded_gids:
            marks.append("\u2713")
        prefix = " ".join(marks)
        if prefix:
            prefix += " "

        label = (
            f"{prefix}[{result.category}] {result.title[:60]}"
            f"  |  {result.pages}p  |  {result.uploader}"
        )

        if result.gid in downloaded_gids:
            return [("fg:ansigreen", label)]
        if result.gid in opened_gids:
            return [("fg:ansibrightblack", label)]
        if bulk_mode and result.gid in selected_results:
            return [("fg:ansicyan", label)]
        return label

    def _search_download_result(self, result: SearchResult, downloaded_gids: set[int]) -> bool:
        console.print(f"  [cyan]Fetching gallery info...[/cyan]")
        try:
            gallery, torrents = self.manager.fetch_info_and_torrents_sync(result.url)
        except Exception as e:
            print_error(f"Failed to fetch: {e}")
            return False

        title = gallery.title_jpn or gallery.title
        console.print(f"  [bold]{title}[/bold]")

        started = self._default_download(result.url, gallery, torrents)
        if started:
            downloaded_gids.add(result.gid)
        return started

    def _search_action_menu(self, result: SearchResult, opened_gids: set[int] | None = None) -> str:
        """Show action menu for a selected search result. Returns 'download' or 'back'."""
        import webbrowser
        import questionary

        console.print(f"\n  [bold]{result.title}[/bold]")
        console.print(f"  [dim]{result.url}[/dim]")
        if result.pages:
            console.print(f"  [dim]{result.category}  |  {result.pages} pages  |  {result.uploader}[/dim]\n")

        action_choices = [
            questionary.Choice(title="\u2b07 Download", value=0),
            questionary.Choice(title="\U0001f310 Open in browser", value=1),
            questionary.Choice(title="\u2190 Back to results", value=-1),
        ]

        while True:
            try:
                action = questionary.select(
                    "Action:",
                    choices=action_choices,
                    instruction="",
                ).ask()
            except KeyboardInterrupt:
                action = -1

            if action is None or action == -1:
                return "back"

            if action == 1:
                webbrowser.open(result.url)
                if opened_gids is not None:
                    opened_gids.add(result.gid)
                print_success("Opened in browser.")
                continue  # stay in menu

            return "download"

    def _search_bulk_download(self, results: list[SearchResult], downloaded_gids: set[int]) -> None:
        import questionary

        if not results:
            print_info("No selected results.")
            return

        try:
            mode = questionary.select(
                f"Bulk download ({len(results)} selected):",
                choices=[
                    questionary.Choice(title="Direct download (all)", value="direct"),
                    questionary.Choice(title="Auto (smart select best method)", value="auto"),
                    questionary.Choice(title="Manual select for each", value="manual"),
                    questionary.Choice(title="Back", value="back"),
                ],
                instruction="",
            ).ask()
        except KeyboardInterrupt:
            mode = "back"

        if not mode or mode == "back":
            return

        started_count = 0
        failed_count = 0
        for i, result in enumerate(results, 1):
            title = result.title[:70] if len(result.title) > 70 else result.title
            console.print(f"  [bold]({i}/{len(results)}) {title}[/bold]")
            console.print(f"  [dim]{result.url}[/dim]")
            console.print("  [cyan]Fetching gallery info...[/cyan]")
            try:
                gallery, torrents = self.manager.fetch_info_and_torrents_sync(result.url)
            except Exception as e:
                failed_count += 1
                print_error(f"Failed to fetch: {e}")
                continue

            if mode == "direct":
                self._start_direct(result.url, gallery)
                started = True
            elif mode == "auto":
                started = self._auto_download(result.url, gallery, torrents)
            else:
                started = self._interactive_download(result.url, gallery, torrents)

            if started:
                downloaded_gids.add(result.gid)
                started_count += 1

        print_success(f"Bulk download queued {started_count} result(s).")
        if failed_count:
            print_error(f"{failed_count} result(s) failed to queue.")

    def _cmd_status(self, args: list[str]) -> None:
        if args and args[0] in ("-clear", "--clear", "clear"):
            self.manager.clear_finished()
            print_success("Cleared completed/failed/cancelled tasks.")
            return

        tasks = self.manager.get_all_tasks()
        if not tasks:
            print_status_table(tasks)
            return

        # If any task is still active, use live-updating display
        active = self.manager.get_active_tasks()
        if active:
            live_status_display(self.manager.get_all_tasks)
        else:
            print_status_table(tasks)

    # ------------------------------------------------------------------
    # Bulk download (listing page)
    # ------------------------------------------------------------------

    def _cmd_bulk(self, url: str, page_type: str) -> None:
        """Handle a listing page URL — show settings, checkout, then bulk download."""
        console.print(f"\n  [bold cyan]📋 Listing page detected[/bold cyan]  [dim]({page_type})[/dim]")
        console.print(f"  [dim]{url}[/dim]")
        console.print(f"  [cyan]Fetching page info...[/cyan]")

        try:
            first_page = self.manager.fetch_listing_sync(url, page=0)
        except Exception as e:
            print_error(f"Failed to fetch listing page: {e}")
            return

        gallery_count = len(first_page.results)
        total_results = first_page.total_results or gallery_count
        per_page = gallery_count if gallery_count > 0 else 25
        estimated_pages = max(1, (total_results + per_page - 1) // per_page)

        console.print(f"  [green]Found ~{total_results:,} galleries[/green] across ~[yellow]{estimated_pages:,} pages[/yellow]")
        console.print(f"  [dim]{gallery_count} galleries on current page[/dim]\n")

        bulk_cfg = BulkDownloadConfig(
            url=url,
            page_type=page_type,
            total_results=total_results,
            download_dir=self.config.download_dir,
        )

        # Loop: Settings ↔ Checkout → Download
        while True:
            confirmed = self._bulk_settings_menu(bulk_cfg, estimated_pages)
            if not confirmed:
                print_info("Cancelled.")
                return

            # Checkout: collect and review the gallery list
            gallery_list = self._bulk_checkout(bulk_cfg, first_page, estimated_pages)
            if gallery_list is None:
                # User chose "Back to Settings" — loop back
                continue
            if not gallery_list:
                print_info("No galleries to download.")
                return

            # Execute download on the curated list
            self._execute_bulk(bulk_cfg, gallery_list)
            return

    def _bulk_settings_menu(self, cfg: BulkDownloadConfig, estimated_pages: int) -> bool:
        """Interactive settings menu for bulk download. Returns True if user confirms."""
        import questionary

        _CONFIRM = "__CONFIRM__"
        _BACK = "__BACK__"

        while True:
            # Build display values
            if cfg.fetch_mode == FetchMode.ITER:
                fetch_display = "All pages (iterate all)"
            elif cfg.fetch_mode == FetchMode.CURRENT_PAGE:
                fetch_display = "Current page only"
            else:
                fetch_display = f"Custom range: page {cfg.start_page} → {cfg.end_page}"

            download_display = {
                BulkDownloadMode.ASK_EACH: "Ask for each gallery",
                BulkDownloadMode.DIRECT: "Direct download (all)",
                BulkDownloadMode.AUTO: "Auto (smart select)",
            }[cfg.download_mode]

            max_gal_display = str(cfg.max_galleries) if cfg.max_galleries > 0 else "Unlimited"
            max_size_display = f"{cfg.max_size_mb:.0f} MB" if cfg.max_size_mb > 0 else "No limit"
            keyword_display = cfg.keyword_filter if cfg.keyword_filter else "(none)"
            dir_display = cfg.download_dir or self.config.download_dir

            choices = [
                questionary.Choice(
                    title=f"📖 Fetch Mode:       {fetch_display}",
                    value="fetch_mode",
                ),
                questionary.Choice(
                    title=f"⬇️  Download Mode:    {download_display}",
                    value="download_mode",
                ),
                questionary.Choice(
                    title=f"📊 Max Galleries:    {max_gal_display}",
                    value="max_galleries",
                ),
                questionary.Choice(
                    title=f"📦 Max Size/Gallery: {max_size_display}",
                    value="max_size",
                ),
                questionary.Choice(
                    title=f"🔍 Keyword Filter:   {keyword_display}",
                    value="keyword",
                ),
                questionary.Choice(
                    title=f"📂 Download Dir:     {dir_display}",
                    value="download_dir",
                ),
                questionary.Choice(
                    title="──────────────────────────────────",
                    value="_sep",
                    disabled="",
                ),
                questionary.Choice(
                    title="🛒 Checkout →",
                    value=_CONFIRM,
                ),
                questionary.Choice(
                    title="← Cancel",
                    value=_BACK,
                ),
            ]

            try:
                selected = questionary.select(
                    "Bulk Download Settings",
                    choices=choices,
                    instruction="(↑↓ select, Enter edit/confirm, Ctrl-C cancel)",
                ).ask()
            except KeyboardInterrupt:
                return False

            if selected is None or selected == _BACK:
                return False

            if selected == _CONFIRM:
                return True

            # --- Edit individual settings ---

            if selected == "fetch_mode":
                mode_choices = [
                    questionary.Choice(title="Iterate all pages", value=FetchMode.ITER),
                    questionary.Choice(title="Current page only", value=FetchMode.CURRENT_PAGE),
                    questionary.Choice(title="Custom page range", value=FetchMode.CUSTOM_RANGE),
                ]
                try:
                    mode = questionary.select(
                        "Fetch Mode:",
                        choices=mode_choices,
                        default=cfg.fetch_mode,
                    ).ask()
                except KeyboardInterrupt:
                    continue

                if mode is not None:
                    cfg.fetch_mode = mode

                    if mode == FetchMode.CUSTOM_RANGE:
                        try:
                            start = questionary.text(
                                f"Start page (1-{estimated_pages}):",
                                default=str(cfg.start_page),
                                validate=lambda x: x.isdigit() and 1 <= int(x) <= estimated_pages,
                            ).ask()
                            if start:
                                cfg.start_page = int(start)

                            end = questionary.text(
                                f"End page ({cfg.start_page}-{estimated_pages}):",
                                default=str(max(cfg.start_page, cfg.end_page)),
                                validate=lambda x: x.isdigit() and cfg.start_page <= int(x) <= estimated_pages,
                            ).ask()
                            if end:
                                cfg.end_page = int(end)
                        except KeyboardInterrupt:
                            pass

            elif selected == "download_mode":
                dl_choices = [
                    questionary.Choice(title="Ask for each gallery", value=BulkDownloadMode.ASK_EACH),
                    questionary.Choice(title="Direct download (all)", value=BulkDownloadMode.DIRECT),
                    questionary.Choice(title="Auto (smart select best method)", value=BulkDownloadMode.AUTO),
                ]
                try:
                    mode = questionary.select(
                        "Download Mode:",
                        choices=dl_choices,
                        default=cfg.download_mode,
                    ).ask()
                except KeyboardInterrupt:
                    continue
                if mode is not None:
                    cfg.download_mode = mode

            elif selected == "max_galleries":
                try:
                    val = questionary.text(
                        "Max galleries to download (0 = unlimited):",
                        default=str(cfg.max_galleries),
                        validate=lambda x: x.isdigit(),
                    ).ask()
                except KeyboardInterrupt:
                    continue
                if val is not None:
                    cfg.max_galleries = int(val)

            elif selected == "max_size":
                try:
                    val = questionary.text(
                        "Max size per gallery in MB (0 = no limit):",
                        default=str(int(cfg.max_size_mb)) if cfg.max_size_mb else "0",
                        validate=lambda x: x.replace(".", "", 1).isdigit(),
                    ).ask()
                except KeyboardInterrupt:
                    continue
                if val is not None:
                    cfg.max_size_mb = float(val)

            elif selected == "keyword":
                try:
                    val = questionary.text(
                        "Keyword filter (|| = OR, && = AND, ! = NOT, empty = none):",
                        default=cfg.keyword_filter,
                    ).ask()
                except KeyboardInterrupt:
                    continue
                if val is not None:
                    cfg.keyword_filter = val.strip()

            elif selected == "download_dir":
                try:
                    val = questionary.text(
                        "Download directory:",
                        default=cfg.download_dir or self.config.download_dir,
                    ).ask()
                except KeyboardInterrupt:
                    continue
                if val is not None:
                    cfg.download_dir = val.strip()

    def _bulk_checkout(
        self, cfg: BulkDownloadConfig, first_page, estimated_pages: int,
    ) -> list[SearchResult] | None:
        """Collect matching galleries and show a review list.

        Returns:
            list[SearchResult] — confirmed list to download
            None               — user chose "Back to Settings"
        """
        import questionary

        # --- Phase 1: Collect all matching galleries ---
        if cfg.fetch_mode == FetchMode.CURRENT_PAGE:
            page_start, page_end = 0, 0
        elif cfg.fetch_mode == FetchMode.CUSTOM_RANGE:
            page_start, page_end = cfg.start_page - 1, cfg.end_page - 1
        else:
            page_start, page_end = 0, estimated_pages - 1

        total_pages = page_end - page_start + 1
        console.print(f"\n  [cyan]Collecting galleries from {total_pages} page(s)...[/cyan]")

        gallery_list: list[SearchResult] = []
        skipped = 0

        for page_idx in range(page_start, page_end + 1):
            if cfg.max_galleries > 0 and len(gallery_list) >= cfg.max_galleries:
                break

            if page_idx == 0 and first_page is not None:
                search_page = first_page
            else:
                console.print(f"  [dim]Fetching page {page_idx + 1}/{page_end + 1}...[/dim]")
                try:
                    search_page = self.manager.fetch_listing_sync(cfg.url, page=page_idx)
                except Exception as e:
                    print_error(f"Failed to fetch page {page_idx + 1}: {e}")
                    continue

            if not search_page.results:
                break

            for result in search_page.results:
                if cfg.max_galleries > 0 and len(gallery_list) >= cfg.max_galleries:
                    break
                if cfg.keyword_filter:
                    if not matches_keyword_filter(result.title, cfg.keyword_filter):
                        skipped += 1
                        continue
                gallery_list.append(result)

        console.print(f"  [green]Collected {len(gallery_list)} galleries[/green]", end="")
        if skipped:
            console.print(f"  [dim]({skipped} filtered out)[/dim]")
        else:
            console.print()

        if not gallery_list:
            return []

        # --- Phase 2: Interactive review list ---
        _START = "__START__"
        _BACK_SETTINGS = "__BACK_SETTINGS__"

        while True:
            choices = []
            for i, r in enumerate(gallery_list):
                title = r.title[:55] if len(r.title) > 55 else r.title
                label = f"  [{r.category}] {title}"
                if r.pages:
                    label += f"  |  {r.pages}p"
                choices.append(questionary.Choice(title=label, value=i))

            choices.append(questionary.Choice(
                title="──────────────────────────────────",
                value="_sep", disabled="",
            ))
            choices.append(questionary.Choice(
                title=f"✅ Start Bulk Download ({len(gallery_list)} galleries)",
                value=_START,
            ))
            choices.append(questionary.Choice(
                title="← Back to Settings  [changes to this list will be lost]",
                value=_BACK_SETTINGS,
            ))

            try:
                selected = questionary.select(
                    f"Checkout — {len(gallery_list)} galleries",
                    choices=choices,
                    instruction="(↑↓ select, Enter actions, Ctrl-C cancel)",
                ).ask()
            except KeyboardInterrupt:
                return None

            if selected is None or selected == _BACK_SETTINGS:
                return None

            if selected == _START:
                return gallery_list

            # --- Item action menu ---
            idx = selected
            item = gallery_list[idx]
            item_title = item.title[:60] if len(item.title) > 60 else item.title

            console.print(f"\n  [bold]{item_title}[/bold]")
            console.print(f"  [dim]{item.url}[/dim]\n")

            action_choices = [
                questionary.Choice(title="🌐 Open in browser", value="browser"),
                questionary.Choice(title="🗑  Remove from list", value="remove"),
                questionary.Choice(title="← Back to list", value="back"),
            ]

            try:
                action = questionary.select(
                    "Action:", choices=action_choices, instruction="",
                ).ask()
            except KeyboardInterrupt:
                action = "back"

            if action == "browser":
                import webbrowser
                webbrowser.open(item.url)
                print_success("Opened in browser.")
            elif action == "remove":
                gallery_list.pop(idx)
                print_success(f"Removed from list. ({len(gallery_list)} remaining)")
                if not gallery_list:
                    print_info("List is now empty.")
                    return []

    def _execute_bulk(self, cfg: BulkDownloadConfig, gallery_list: list[SearchResult]) -> None:
        """Execute the bulk download on a curated list of galleries."""
        console.print(f"\n  [bold cyan]Starting bulk download[/bold cyan]")
        console.print(f"  [dim]{len(gallery_list)} galleries → {cfg.download_dir}[/dim]\n")

        # Temporarily override download dir
        original_dir = self.config.download_dir
        if cfg.download_dir:
            self.config.download_dir = cfg.download_dir

        downloaded_count = 0
        failed_count = 0
        skipped_count = 0

        for i, result in enumerate(gallery_list, 1):
            title_display = result.title[:60] if len(result.title) > 60 else result.title
            console.print(f"  [bold]→[/bold] ({i}/{len(gallery_list)}) [{result.category}] {title_display}")
            console.print(f"    [dim]{result.url}[/dim]")

            # Ask-each mode
            if cfg.download_mode == BulkDownloadMode.ASK_EACH:
                action = self._search_action_menu(result)
                if action != "download":
                    skipped_count += 1
                    continue

            # Fetch gallery info
            console.print(f"    [cyan]Fetching gallery info...[/cyan]")
            try:
                gallery, torrents = self.manager.fetch_info_and_torrents_sync(result.url)
            except Exception as e:
                print_error(f"    Failed to fetch: {e}")
                failed_count += 1
                continue

            # Size filter
            if cfg.max_size_mb > 0 and gallery.filesize:
                try:
                    size_bytes = int(gallery.filesize)
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb > cfg.max_size_mb:
                        print_info(f"    Skipped: {size_mb:.1f} MB > {cfg.max_size_mb:.0f} MB limit")
                        skipped_count += 1
                        continue
                except (ValueError, TypeError):
                    pass

            title = gallery.title_jpn or gallery.title
            console.print(f"    [bold]{title}[/bold]")

            if cfg.download_mode == BulkDownloadMode.ASK_EACH:
                self._default_download(result.url, gallery, torrents)
            elif cfg.download_mode == BulkDownloadMode.DIRECT:
                self._start_direct(result.url, gallery)
            else:  # AUTO
                self._auto_download(result.url, gallery, torrents)

            downloaded_count += 1
            console.print()

        # Summary
        console.print(f"\n  [bold green]Bulk download complete![/bold green]")
        console.print(f"  [green]✓ Downloaded: {downloaded_count}[/green]")
        if skipped_count:
            console.print(f"  [yellow]⏭ Skipped: {skipped_count}[/yellow]")
        if failed_count:
            console.print(f"  [red]✗ Failed: {failed_count}[/red]")
        console.print()

        # Restore original download dir
        self.config.download_dir = original_dir


    # ------------------------------------------------------------------
    # History command
    # ------------------------------------------------------------------

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

    def _cmd_history(self, args: list[str]) -> None:
        keyword = ""
        bulk = False

        i = 0
        while i < len(args):
            arg = args[i].lower()
            if arg in ("-clear", "--clear", "clear"):
                self._history_clear()
                return
            if arg in ("-bulk", "--bulk", "bulk"):
                bulk = True
                i += 1
                continue
            if arg in ("-search", "--search", "search"):
                keyword = " ".join(args[i + 1:]).strip()
                break
            i += 1

        if bulk:
            self._history_bulk(keyword)
            return

        self._history_browse(keyword)

    def _load_history_entries(self, keyword: str = "") -> list[dict]:
        import json
        from .downloader import _get_meta_dir

        meta_dir = _get_meta_dir(self.config)
        files = sorted(meta_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)

        entries = []
        needle = keyword.casefold().strip()
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue

            data["_file"] = f
            if needle and needle not in self._history_search_blob(data):
                continue
            entries.append(data)

        return entries

    def _history_search_blob(self, entry: dict) -> str:
        import json

        parts = [
            entry.get("title", ""),
            entry.get("title_jpn", ""),
            entry.get("url", ""),
            entry.get("category", ""),
            entry.get("uploader", ""),
            entry.get("posted", ""),
            entry.get("method", ""),
            json.dumps(entry.get("tags", {}), ensure_ascii=False),
        ]
        return " ".join(str(p) for p in parts).casefold()

    def _history_entry_date(self, entry: dict):
        from datetime import datetime

        downloaded_at = entry.get("downloaded_at", "")
        if downloaded_at:
            try:
                return datetime.fromisoformat(downloaded_at).date()
            except ValueError:
                pass

        file_path = entry.get("_file")
        if file_path:
            return datetime.fromtimestamp(file_path.stat().st_mtime).date()
        return None

    def _history_choice_label(self, index: int, entry: dict, selected: bool | None = None) -> str:
        title = entry.get("title_jpn") or entry.get("title") or "?"
        method = entry.get("method", "direct")
        date = entry.get("downloaded_at", "")[:10]
        badge = "T" if "torrent" in method else "D"
        mark = ""
        if selected is not None:
            mark = "[x] " if selected else "[ ] "
        return f"{mark}#{index + 1:03d} {badge} [{date}] {title[:60]}"

    def _history_clear(self) -> None:
        import questionary
        from datetime import date

        entries = self._load_history_entries()
        if not entries:
            print_info("History is empty.")
            return

        try:
            mode = questionary.select(
                f"Clear history ({len(entries)} entries):",
                choices=[
                    questionary.Choice(title="Clear all", value="all"),
                    questionary.Choice(title="Before today", value="before_today"),
                    questionary.Choice(title="Delete after <index>", value="after_index"),
                    questionary.Choice(title="Back", value="back"),
                ],
                instruction="(arrow keys select, Enter confirm, Ctrl-C back)",
            ).ask()
        except KeyboardInterrupt:
            mode = "back"

        if not mode or mode == "back":
            return

        if mode == "all":
            to_delete = entries
            message = f"Delete all {len(to_delete)} history entries?"
        elif mode == "before_today":
            today = date.today()
            to_delete = [e for e in entries if (self._history_entry_date(e) or today) < today]
            message = f"Delete {len(to_delete)} history entries before today?"
        else:
            try:
                raw = questionary.text(
                    f"Delete entries after index (0-{len(entries)}):",
                    default=str(len(entries)),
                    validate=lambda x: x.isdigit() and 0 <= int(x) <= len(entries),
                ).ask()
            except KeyboardInterrupt:
                raw = None

            if raw is None:
                return
            keep_until = int(raw)
            to_delete = entries[keep_until:]
            message = f"Delete {len(to_delete)} history entries after index {keep_until}?"

        if not to_delete:
            print_info("No matching history entries to delete.")
            return

        confirm = questionary.confirm(message, default=False).ask()
        if not confirm:
            print_info("Cancelled.")
            return

        deleted = self._delete_history_entries(to_delete)
        print_success(f"Deleted {deleted} history entries.")

    def _history_browse(self, keyword: str = "") -> None:
        import questionary

        entries = self._load_history_entries(keyword)
        if not entries:
            if keyword:
                print_info(f"No history entries matched: {keyword}")
            else:
                print_info("No download history yet.")
            return

        while True:
            choices = [
                questionary.Choice(title=self._history_choice_label(i, e), value=i)
                for i, e in enumerate(entries)
            ]
            choices.append(questionary.Choice(title="Back", value=-1))

            title = f"Download History ({len(entries)} entries)"
            if keyword:
                title += f" - search: {keyword}"

            try:
                idx = questionary.select(
                    title,
                    choices=choices,
                    instruction="(arrow keys select, Enter actions, Ctrl-C back)",
                ).ask()
            except KeyboardInterrupt:
                idx = -1

            if idx is None or idx == -1:
                return

            entry = entries[idx]
            result = self._history_actions(entry)
            if result == "deleted":
                entries.pop(idx)
                if not entries:
                    print_info("No history entries remain.")
                    return

    def _history_actions(self, entry: dict) -> str | None:
        import questionary
        import webbrowser

        title = entry.get("title_jpn") or entry.get("title", "?")
        url = entry.get("url", "")
        method = entry.get("method", "direct")
        date = entry.get("downloaded_at", "")[:19].replace("T", " ")

        console.print(f"\n  [bold]{title}[/bold]")
        console.print(f"  [dim]{url}[/dim]")
        console.print(f"  [dim]Method: {method}  |  Date: {date}[/dim]\n")

        action_choices = [
            questionary.Choice(title="Open in browser", value="browser"),
            questionary.Choice(title="Re-download (manual select)", value="redownload"),
        ]

        output_dir = entry.get("output_dir", "")
        if output_dir:
            action_choices.append(questionary.Choice(title="Open folder", value="folder"))

        action_choices.extend([
            questionary.Choice(title="Remove from history", value="remove"),
            questionary.Choice(title="Back", value="back"),
        ])

        try:
            action = questionary.select(
                "Action:",
                choices=action_choices,
                instruction="",
            ).ask()
        except KeyboardInterrupt:
            action = "back"

        if not action or action == "back":
            return None

        if action == "browser":
            if url:
                webbrowser.open(url)
                print_success("Opened in browser.")
            else:
                print_error("No URL available.")
        elif action == "redownload":
            if url:
                self._redownload(url)
            else:
                print_error("No URL available.")
        elif action == "folder":
            try:
                os.startfile(output_dir)
            except Exception:
                print_error(f"Could not open: {output_dir}")
        elif action == "remove":
            confirm = questionary.confirm("Remove this entry from history?", default=False).ask()
            if confirm and self._delete_history_entries([entry]):
                print_success("Removed from history.")
                return "deleted"
            print_info("Cancelled.")

        return None

    def _history_bulk_select(
        self,
        title: str,
        entries: list[dict],
        selected: set[int],
        cursor_choice: tuple[str, int | None],
    ) -> tuple[str, int | None] | None:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
        from questionary import utils
        from questionary.constants import DEFAULT_QUESTION_PREFIX, DEFAULT_SELECTED_POINTER
        from questionary.prompts import common
        from questionary.prompts.common import Choice, InquirerControl
        from questionary.question import Question
        from questionary.styles import merge_styles_default

        entry_choices = [
            Choice(
                title=self._history_choice_label(i, entry, i in selected),
                value=("toggle", i),
            )
            for i, entry in enumerate(entries)
        ]
        select_all_choice = Choice(title="Select All", value=("select_all", None))
        unselect_all_choice = Choice(title="Unselect All", value=("unselect_all", None))
        bulk_edit_choice = Choice(title="", value=("bulk_edit", None))
        back_choice = Choice(title="Back", value=("back", None))
        choices = [
            *entry_choices,
            select_all_choice,
            unselect_all_choice,
            bulk_edit_choice,
            back_choice,
        ]

        def sync_titles() -> None:
            for i, choice in enumerate(entry_choices):
                choice.title = self._history_choice_label(i, entries[i], i in selected)
            if selected:
                bulk_edit_choice.title = f"Bulk Edit ({len(selected)} selected)"
            else:
                bulk_edit_choice.title = "Bulk Edit (select at least one)"

        sync_titles()

        values = {choice.value for choice in choices}
        if cursor_choice not in values:
            cursor_choice = ("toggle", min(len(entries) - 1, 0))

        ic = InquirerControl(
            choices,
            default=None,
            pointer=DEFAULT_SELECTED_POINTER,
            use_indicator=False,
            use_shortcuts=False,
            show_selected=False,
            show_description=True,
            use_arrow_keys=True,
            initial_choice=cursor_choice,
        )

        def get_prompt_tokens():
            return [
                ("class:qmark", DEFAULT_QUESTION_PREFIX),
                ("class:question", f" {title} "),
                ("class:instruction", "(Enter select/unselect, Ctrl-C back)"),
            ]

        layout = common.create_inquirer_layout(ic, get_prompt_tokens)
        bindings = KeyBindings()

        @bindings.add(Keys.ControlQ, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def _(event):
            event.app.exit(result=None)

        def move_cursor_down(event):
            ic.select_next()
            while not ic.is_selection_valid():
                ic.select_next()

        def move_cursor_up(event):
            ic.select_previous()
            while not ic.is_selection_valid():
                ic.select_previous()

        bindings.add(Keys.Down, eager=True)(move_cursor_down)
        bindings.add(Keys.Up, eager=True)(move_cursor_up)
        bindings.add("j", eager=True)(move_cursor_down)
        bindings.add("k", eager=True)(move_cursor_up)
        bindings.add(Keys.ControlN, eager=True)(move_cursor_down)
        bindings.add(Keys.ControlP, eager=True)(move_cursor_up)

        @bindings.add(Keys.ControlM, eager=True)
        def set_answer(event):
            action, idx = ic.get_pointed_at().value
            if action == "toggle" and idx is not None:
                if idx in selected:
                    selected.remove(idx)
                else:
                    selected.add(idx)
                sync_titles()
                event.app.invalidate()
            elif action == "select_all":
                selected.update(range(len(entries)))
                sync_titles()
                event.app.invalidate()
            elif action == "unselect_all":
                selected.clear()
                sync_titles()
                event.app.invalidate()
            elif action == "bulk_edit":
                if selected:
                    event.app.exit(result=("bulk_edit", None))
                else:
                    event.app.invalidate()
            elif action == "back":
                event.app.exit(result=("back", None))

        @bindings.add(Keys.Any)
        def other(event):
            pass

        question = Question(
            Application(
                layout=layout,
                key_bindings=bindings,
                style=merge_styles_default([None]),
                **utils.used_kwargs({}, Application.__init__),
            )
        )
        return question.ask()

    def _history_bulk(self, keyword: str = "") -> None:
        entries = self._load_history_entries(keyword)
        if not entries:
            if keyword:
                print_info(f"No history entries matched: {keyword}")
            else:
                print_info("No download history yet.")
            return

        selected: set[int] = set()
        cursor_choice = ("toggle", 0)

        while True:
            if cursor_choice[0] == "toggle" and cursor_choice[1] is not None:
                cursor_idx = min(cursor_choice[1], len(entries) - 1)
                cursor_choice = ("toggle", cursor_idx)

            title = f"History Bulk Mode ({len(entries)} entries)"
            if keyword:
                title += f" - search: {keyword}"

            bulk_choice = self._history_bulk_select(title, entries, selected, cursor_choice)

            if bulk_choice is None:
                bulk_choice = ("back", None)
            action, idx = bulk_choice
            cursor_choice = bulk_choice

            if action == "back":
                return
            if action == "bulk_edit":
                chosen = [entries[i] for i in sorted(selected)]
                result = self._history_bulk_actions(chosen)
                if result == "deleted":
                    deleted_files = {e.get("_file") for e in chosen}
                    entries = [e for e in entries if e.get("_file") not in deleted_files]
                    selected.clear()
                    if not entries:
                        print_info("No history entries remain.")
                        return
                    cursor_choice = ("toggle", min(idx or 0, len(entries) - 1))

    def _history_bulk_actions(self, entries: list[dict]) -> str | None:
        import questionary
        import webbrowser

        try:
            action = questionary.select(
                f"Bulk Edit ({len(entries)} selected):",
                choices=[
                    questionary.Choice(title="Open in browser", value="browser"),
                    questionary.Choice(title="Re-download", value="redownload"),
                    questionary.Choice(title="Open folders", value="folders"),
                    questionary.Choice(title="Remove from history", value="remove"),
                    questionary.Choice(title="Back", value="back"),
                ],
                instruction="",
            ).ask()
        except KeyboardInterrupt:
            action = "back"

        if not action or action == "back":
            return None

        if action == "browser":
            opened = 0
            for entry in entries:
                url = entry.get("url", "")
                if url:
                    webbrowser.open(url)
                    opened += 1
            print_success(f"Opened {opened} entries in browser.")
        elif action == "redownload":
            self._history_redownload_bulk(entries)
        elif action == "folders":
            opened = 0
            seen: set[str] = set()
            for entry in entries:
                output_dir = entry.get("output_dir", "")
                if not output_dir or output_dir in seen:
                    continue
                seen.add(output_dir)
                try:
                    os.startfile(output_dir)
                    opened += 1
                except Exception:
                    print_error(f"Could not open: {output_dir}")
            print_success(f"Opened {opened} folder(s).")
        elif action == "remove":
            confirm = questionary.confirm(
                f"Remove {len(entries)} entries from history?",
                default=False,
            ).ask()
            if confirm:
                deleted = self._delete_history_entries(entries)
                print_success(f"Removed {deleted} history entries.")
                return "deleted"
            print_info("Cancelled.")

        return None

    def _history_redownload_bulk(self, entries: list[dict]) -> None:
        import questionary

        try:
            mode = questionary.select(
                "Re-download mode:",
                choices=[
                    questionary.Choice(title="Direct download (all)", value="direct"),
                    questionary.Choice(title="Auto (smart select best method)", value="auto"),
                    questionary.Choice(title="Manual select for each", value="manual"),
                    questionary.Choice(title="Back", value="back"),
                ],
                instruction="",
            ).ask()
        except KeyboardInterrupt:
            mode = "back"

        if not mode or mode == "back":
            return

        started = 0
        failed = 0
        for i, entry in enumerate(entries, 1):
            url = entry.get("url", "")
            title = entry.get("title_jpn") or entry.get("title") or url or "?"
            console.print(f"  [bold]({i}/{len(entries)}) {title[:70]}[/bold]")
            if not url:
                failed += 1
                print_error("No URL available.")
                continue

            if mode == "manual":
                self._redownload(url)
                started += 1
                continue

            console.print("  [cyan]Fetching gallery info...[/cyan]")
            try:
                gallery, torrents = self.manager.fetch_info_and_torrents_sync(url)
            except Exception as e:
                failed += 1
                print_error(f"Failed: {e}")
                continue

            if mode == "direct":
                self._start_direct(url, gallery)
            else:
                self._auto_download(url, gallery, torrents)
            started += 1

        print_success(f"Bulk re-download queued {started} entr{'y' if started == 1 else 'ies'}.")
        if failed:
            print_error(f"{failed} entries failed to queue.")

    def _delete_history_entries(self, entries: list[dict]) -> int:
        deleted = 0
        for entry in entries:
            file_path = entry.get("_file")
            if not file_path:
                continue
            try:
                file_path.unlink(missing_ok=True)
                deleted += 1
            except Exception as e:
                print_error(f"Could not delete {file_path}: {e}")
        return deleted

    # ------------------------------------------------------------------
    # Cancel command (interactive if no args)
    # ------------------------------------------------------------------

    def _cancel_choice_label(self, task: DownloadTask, selected: bool) -> str:
        mark = "[x] " if selected else "[ ] "
        return f"{mark}#{task.id}  {task.short_title}  [{task.status.value}]  {task.downloaded}/{task.total}"

    def _cancel_bulk_select(
        self,
        tasks: list[DownloadTask],
        selected: set[int],
    ) -> str | None:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
        from questionary import utils
        from questionary.constants import DEFAULT_QUESTION_PREFIX, DEFAULT_SELECTED_POINTER
        from questionary.prompts import common
        from questionary.prompts.common import Choice, InquirerControl
        from questionary.question import Question
        from questionary.styles import merge_styles_default

        task_choices = [
            Choice(
                title=self._cancel_choice_label(task, task.id in selected),
                value=("toggle", task.id),
            )
            for task in tasks
        ]
        select_all_choice = Choice(title="Select All", value=("select_all", None))
        unselect_all_choice = Choice(title="Unselect All", value=("unselect_all", None))
        bulk_cancel_choice = Choice(title="", value=("bulk_cancel", None))
        back_choice = Choice(title="Back", value=("back", None))
        choices = [
            *task_choices,
            select_all_choice,
            unselect_all_choice,
            bulk_cancel_choice,
            back_choice,
        ]

        def sync_titles() -> None:
            for i, task in enumerate(tasks):
                task_choices[i].title = self._cancel_choice_label(task, task.id in selected)
            if selected:
                bulk_cancel_choice.title = f"Bulk Cancel ({len(selected)} selected)"
            else:
                bulk_cancel_choice.title = "Bulk Cancel (select at least one)"

        sync_titles()

        ic = InquirerControl(
            choices,
            default=None,
            pointer=DEFAULT_SELECTED_POINTER,
            use_indicator=False,
            use_shortcuts=False,
            show_selected=False,
            show_description=True,
            use_arrow_keys=True,
            initial_choice=("toggle", tasks[0].id),
        )

        def get_prompt_tokens():
            return [
                ("class:qmark", DEFAULT_QUESTION_PREFIX),
                ("class:question", f" Cancel Tasks ({len(tasks)} active) "),
                ("class:instruction", "(Enter select/unselect, Ctrl-C back)"),
            ]

        layout = common.create_inquirer_layout(ic, get_prompt_tokens)
        bindings = KeyBindings()

        @bindings.add(Keys.ControlQ, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def _(event):
            event.app.exit(result=None)

        def move_cursor_down(event):
            ic.select_next()
            while not ic.is_selection_valid():
                ic.select_next()

        def move_cursor_up(event):
            ic.select_previous()
            while not ic.is_selection_valid():
                ic.select_previous()

        bindings.add(Keys.Down, eager=True)(move_cursor_down)
        bindings.add(Keys.Up, eager=True)(move_cursor_up)
        bindings.add("j", eager=True)(move_cursor_down)
        bindings.add("k", eager=True)(move_cursor_up)
        bindings.add(Keys.ControlN, eager=True)(move_cursor_down)
        bindings.add(Keys.ControlP, eager=True)(move_cursor_up)

        @bindings.add(Keys.ControlM, eager=True)
        def set_answer(event):
            action, task_id = ic.get_pointed_at().value
            if action == "toggle" and task_id is not None:
                if task_id in selected:
                    selected.remove(task_id)
                else:
                    selected.add(task_id)
                sync_titles()
                event.app.invalidate()
            elif action == "select_all":
                selected.update(task.id for task in tasks)
                sync_titles()
                event.app.invalidate()
            elif action == "unselect_all":
                selected.clear()
                sync_titles()
                event.app.invalidate()
            elif action == "bulk_cancel":
                if selected:
                    event.app.exit(result="bulk_cancel")
                else:
                    event.app.invalidate()
            elif action == "back":
                event.app.exit(result="back")

        @bindings.add(Keys.Any)
        def other(event):
            pass

        question = Question(
            Application(
                layout=layout,
                key_bindings=bindings,
                style=merge_styles_default([None]),
                **utils.used_kwargs({}, Application.__init__),
            )
        )
        return question.ask()

    def _cmd_cancel(self, args: list[str]) -> None:
        if args:
            first = args[0].lower()
            if first == "all":
                active = self.manager.get_active_tasks()
                cancelled = sum(1 for task in active if self.manager.cancel_task(task.id))
                if cancelled:
                    print_success(f"Cancelled {cancelled} active task(s).")
                else:
                    print_info("No active tasks to cancel.")
                return

            try:
                start_id = int(args[0])
                end_id = int(args[1]) if len(args) >= 2 else start_id
            except ValueError:
                print_error("Usage: cancel | cancel <id> | cancel all | cancel <start id> <end id>")
                return

            if start_id > end_id:
                start_id, end_id = end_id, start_id

            cancelled = 0
            for task_id in range(start_id, end_id + 1):
                if self.manager.cancel_task(task_id):
                    cancelled += 1

            if cancelled:
                if start_id == end_id:
                    print_success(f"Task #{start_id} cancelled.")
                else:
                    print_success(f"Cancelled {cancelled} task(s) in range #{start_id}-#{end_id}.")
            else:
                print_error(f"No cancellable task(s) found in range #{start_id}-#{end_id}.")
            return

        active = self.manager.get_active_tasks()
        if not active:
            print_info("No active tasks to cancel.")
            return

        selected_task_ids: set[int] = set()
        action = self._cancel_bulk_select(active, selected_task_ids)
        if not action or action == "back":
            return

        if action == "bulk_cancel":
            cancelled = sum(1 for task_id in sorted(selected_task_ids) if self.manager.cancel_task(task_id))
            if cancelled:
                print_success(f"Cancelled {cancelled} selected task(s).")
            else:
                print_error("No selected task could be cancelled.")
            return


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

        _BACK = "__BACK__"
        bool_keys = {
            "prefer_torrent",
            "show_japanese_title",
            "debug_mode",
            "search_bulk_mode_default",
            "search_open_result_website_automatically",
            "search_open_gallery_website_onclick",
            "search_download_gallery_onclick",
            "search_no_sub_menu",
        }
        mode_labels = {
            "auto": "Auto",
            "ask": "Ask",
            "direct": "Direct Download",
        }
        sections = [
            (
                "General",
                [
                    ("show_japanese_title", "Show Japanese Title"),
                    ("debug_mode", "Debug Mode"),
                ],
            ),
            (
                "Download",
                [
                    ("download_dir", "Download Directory"),
                    ("default_download_mode", "Default Download Mode"),
                    ("prefer_torrent", "Prefer Torrent"),
                    ("max_parallel", "Max Parallel Downloads"),
                    ("rate_limit_delay", "Rate Limit Delay (seconds)"),
                    ("retry_count", "Retry Count"),
                    ("retry_delay", "Retry Delay (seconds)"),
                ],
            ),
            (
                "Cookie",
                [
                    ("ipb_member_id", "ipb_member_id"),
                    ("ipb_pass_hash", "ipb_pass_hash"),
                    ("igneous", "igneous"),
                    ("sk", "sk"),
                ],
            ),
            (
                "Search",
                [
                    ("search_bulk_mode_default", "Bulk Mode Default"),
                    ("search_open_result_website_automatically", "Open Result Website Automatically"),
                    ("search_open_gallery_website_onclick", "Open Gallery Website Onclick"),
                    ("search_download_gallery_onclick", "Download Gallery Onclick"),
                    ("search_no_sub_menu", "No Sub-Menu"),
                ],
            ),
        ]

        def display_value(key: str) -> str:
            value = getattr(self.config, key, "")
            if key == "default_download_mode":
                return mode_labels.get(value, "Auto")
            if key in ("ipb_pass_hash", "igneous", "sk") and value:
                return "***" + str(value)[-4:]
            if value == "":
                return "(not set)"
            return str(value)

        while True:
            section_choices = [
                questionary.Choice(title=name, value=name)
                for name, _items in sections
            ]
            section_choices.append(questionary.Choice(title="\u2190 Back", value=_BACK))

            try:
                selected_section = questionary.select(
                    "Config Editor:",
                    choices=section_choices,
                    instruction="(\u2191\u2193 select, Enter open, Ctrl-C back)",
                ).ask()
            except KeyboardInterrupt:
                selected_section = None

            if not selected_section or selected_section == _BACK:
                return

            section_items = next(items for name, items in sections if name == selected_section)

            while True:
                item_choices = [
                    questionary.Choice(
                        title=f"{label}: {display_value(key)}",
                        value=key,
                    )
                    for key, label in section_items
                ]
                item_choices.append(questionary.Choice(title="\u2190 Back", value=_BACK))

                try:
                    selected_key = questionary.select(
                        f"{selected_section} Settings:",
                        choices=item_choices,
                        instruction="(\u2191\u2193 select, Enter edit, Ctrl-C back)",
                    ).ask()
                except KeyboardInterrupt:
                    selected_key = None

                if not selected_key or selected_key == _BACK:
                    break

                current = getattr(self.config, selected_key, "")

                if selected_key == "default_download_mode":
                    try:
                        new_value = questionary.select(
                            "Default Download Mode:",
                            choices=[
                                questionary.Choice(title="Auto", value="auto"),
                                questionary.Choice(title="Ask", value="ask"),
                                questionary.Choice(title="Direct Download", value="direct"),
                            ],
                            default=current,
                            instruction="(\u2191\u2193 select, Enter confirm, Ctrl-C back)",
                        ).ask()
                    except KeyboardInterrupt:
                        new_value = None
                elif selected_key in bool_keys:
                    try:
                        new_value = questionary.select(
                            f"New value for {selected_key}:",
                            choices=[
                                questionary.Choice(title="Enabled", value="true"),
                                questionary.Choice(title="Disabled", value="false"),
                            ],
                            default="true" if bool(current) else "false",
                            instruction="(\u2191\u2193 select, Enter confirm, Ctrl-C back)",
                        ).ask()
                    except KeyboardInterrupt:
                        new_value = None
                else:
                    if selected_key in ("ipb_pass_hash", "igneous", "sk") and current:
                        current = ""
                    new_value = questionary.text(
                        f"New value for {selected_key}:",
                        default=str(current) if current else "",
                    ).ask()

                if new_value is None:
                    continue

                self._set_config(selected_key, new_value)

    def _show_config(self) -> None:
        from rich.table import Table

        mode_labels = {
            "auto": "Auto",
            "ask": "Ask",
            "direct": "Direct Download",
        }

        table = Table(
            title="Configuration",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )
        table.add_column("Section", style="bold cyan")
        table.add_column("Key", style="bold")
        table.add_column("Value")

        c = self.config
        rows = [
            ("General", "show_japanese_title", str(c.show_japanese_title)),
            ("General", "debug_mode", str(c.debug_mode)),
            ("Download", "download_dir", c.download_dir),
            ("Download", "default_download_mode", mode_labels.get(c.default_download_mode, "Auto")),
            ("Download", "prefer_torrent", str(c.prefer_torrent)),
            ("Download", "max_parallel", str(c.max_parallel)),
            ("Download", "rate_limit_delay", f"{c.rate_limit_delay}s"),
            ("Download", "retry_count", str(c.retry_count)),
            ("Download", "retry_delay", f"{c.retry_delay}s"),
            ("Cookie", "ipb_member_id", c.ipb_member_id or "[dim]not set[/dim]"),
            ("Cookie", "ipb_pass_hash", ("***" + c.ipb_pass_hash[-4:]) if c.ipb_pass_hash else "[dim]not set[/dim]"),
            ("Cookie", "igneous", ("***" + c.igneous[-4:]) if c.igneous else "[dim]not set[/dim]"),
            ("Cookie", "sk", ("***" + c.sk[-4:]) if c.sk else "[dim]not set[/dim]"),
            ("Cookie", "exhentai_ready", "[green]yes[/green]" if c.has_exhentai_cookies else "[red]no[/red]"),
            ("Search", "bulk_mode_default", str(c.search_bulk_mode_default)),
            ("Search", "open_result_website_automatically", str(c.search_open_result_website_automatically)),
            ("Search", "open_gallery_website_onclick", str(c.search_open_gallery_website_onclick)),
            ("Search", "download_gallery_onclick", str(c.search_download_gallery_onclick)),
            ("Search", "no_sub_menu", str(c.search_no_sub_menu)),
        ]

        for section, key, value in rows:
            table.add_row(section, key, value)

        console.print(table)

    def _set_config(self, key: str, value: str) -> None:
        from .config import _normalize_download_mode

        key = key.lower().replace("-", "_")

        config_map: dict[str, str] = {
            "download_dir": "download_dir",
            "max_parallel": "max_parallel",
            "rate_limit_delay": "rate_limit_delay",
            "retry_count": "retry_count",
            "retry_delay": "retry_delay",
            "prefer_torrent": "prefer_torrent",
            "default_download_mode": "default_download_mode",
            "download_mode": "default_download_mode",
            "mode": "default_download_mode",
            "auto_select_best": "auto_select_best",
            "show_japanese_title": "show_japanese_title",
            "debug_mode": "debug_mode",
            "ipb_member_id": "ipb_member_id",
            "ipb_pass_hash": "ipb_pass_hash",
            "igneous": "igneous",
            "sk": "sk",
            "cookie_id": "ipb_member_id",
            "cookie_hash": "ipb_pass_hash",
            "parallel": "max_parallel",
            "bulk_mode_default": "search_bulk_mode_default",
            "search_bulk_mode_default": "search_bulk_mode_default",
            "open_result_website_automatically": "search_open_result_website_automatically",
            "search_open_result_website_automatically": "search_open_result_website_automatically",
            "open_gallery_website_onclick": "search_open_gallery_website_onclick",
            "search_open_gallery_website_onclick": "search_open_gallery_website_onclick",
            "download_gallery_onclick": "search_download_gallery_onclick",
            "search_download_gallery_onclick": "search_download_gallery_onclick",
            "no_sub_menu": "search_no_sub_menu",
            "search_no_sub_menu": "search_no_sub_menu",
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
        elif attr == "default_download_mode":
            self.config.default_download_mode = _normalize_download_mode(value)
        elif attr == "auto_select_best":
            self.config.default_download_mode = "auto" if value.lower() in ("true", "1", "yes", "on", "enabled") else "ask"
        elif attr in (
            "prefer_torrent",
            "show_japanese_title",
            "debug_mode",
            "search_bulk_mode_default",
            "search_open_result_website_automatically",
            "search_open_gallery_website_onclick",
            "search_download_gallery_onclick",
            "search_no_sub_menu",
        ):
            setattr(self.config, attr, value.lower() in ("true", "1", "yes", "on", "enabled"))
        else:
            setattr(self.config, attr, value)

        self.config.save()
        if attr == "auto_select_best":
            print_success(f"default_download_mode = {self.config.default_download_mode}")
        else:
            print_success(f"{attr} = {getattr(self.config, attr)}")

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
