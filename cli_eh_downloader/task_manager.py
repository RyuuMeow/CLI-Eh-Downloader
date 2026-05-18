"""Task manager — runs download tasks in a background async loop."""

from __future__ import annotations

import asyncio
import threading
from typing import Callable, Optional

from .client import EHClient
from .config import Config
from .downloader import process_task
from .models import DownloadMethod, DownloadTask, GalleryInfo, SiteType, TaskStatus, TorrentInfo


class TaskManager:
    """Manages download tasks running in a background event loop.

    The event loop runs in a dedicated daemon thread so the main thread
    (the interactive shell) is never blocked.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._tasks: dict[int, DownloadTask] = {}
        self._async_tasks: dict[int, asyncio.Task] = {}
        self._next_id = 1
        self._lock = threading.Lock()

        # Background event loop
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # Shared HTTP client (created inside the loop)
        self._client: EHClient | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _ensure_client(self) -> EHClient:
        if self._client is None:
            self._client = EHClient(self.config)
        return self._client

    # ------------------------------------------------------------------
    # Public API (called from the main / shell thread)
    # ------------------------------------------------------------------

    def fetch_info_and_torrents_sync(self, url: str) -> tuple[GalleryInfo, list[TorrentInfo]]:
        """Synchronously fetch gallery info and torrents (blocks the caller)."""
        future = asyncio.run_coroutine_threadsafe(
            self._fetch_info_async(url), self._loop
        )
        return future.result()

    def fetch_gallery_sync(self, url: str) -> GalleryInfo:
        """Synchronously fetch gallery metadata without loading torrents."""
        future = asyncio.run_coroutine_threadsafe(
            self._fetch_gallery_async(url), self._loop
        )
        return future.result()

    async def _fetch_gallery_async(self, url: str) -> GalleryInfo:
        from .utils import parse_gallery_url
        from .parser import fetch_gallery_info
        from .models import SiteType

        parsed = parse_gallery_url(url)
        if not parsed:
            raise ValueError("Invalid gallery URL")
        gid, token, site = parsed
        client = self._ensure_client()
        if site == SiteType.EX_HENTAI and not client.can_access_exhentai():
            raise ValueError("ExHentai requires cookies. Run: config set cookie")
        return await fetch_gallery_info(client, gid, token, site)

    async def _fetch_info_async(self, url: str) -> tuple[GalleryInfo, list[TorrentInfo]]:
        from .utils import parse_gallery_url
        from .parser import fetch_gallery_info, fetch_torrent_list
        from .models import SiteType
        
        parsed = parse_gallery_url(url)
        if not parsed:
            raise ValueError("Invalid gallery URL")
        gid, token, site = parsed
        client = self._ensure_client()
        if site == SiteType.EX_HENTAI and not client.can_access_exhentai():
            raise ValueError("ExHentai requires cookies. Run: config set cookie")
        
        gallery = await fetch_gallery_info(client, gid, token, site)
        torrents = []
        if gallery.torrent_count > 0:
            torrents = await fetch_torrent_list(client, gallery)
        return gallery, torrents

    def search_sync(self, query: str, page: int = 0, url_override: str = "", site: SiteType = SiteType.E_HENTAI):
        """Synchronously search galleries (blocks the caller). Returns SearchPage."""
        future = asyncio.run_coroutine_threadsafe(
            self._search_async(query, page, url_override, site), self._loop
        )
        return future.result()

    async def _search_async(
        self,
        query: str,
        page: int = 0,
        url_override: str = "",
        site: SiteType = SiteType.E_HENTAI,
    ):
        from .parser import search_galleries
        client = self._ensure_client()
        return await search_galleries(client, query, site=site, page=page, url_override=url_override)

    def fetch_listing_sync(self, url: str, page: int = 0, url_override: str = ""):
        """Synchronously fetch a listing page (tag, uploader, etc.). Returns SearchPage."""
        future = asyncio.run_coroutine_threadsafe(
            self._fetch_listing_async(url, page, url_override), self._loop
        )
        return future.result()

    async def _fetch_listing_async(self, url: str, page: int = 0, url_override: str = ""):
        from .parser import fetch_listing_page
        client = self._ensure_client()
        return await fetch_listing_page(client, url, page=page, url_override=url_override)

    def resolve_gallery_url_sync(self, image_page_url: str) -> str:
        """Synchronously resolve an image page URL back to its gallery URL."""
        future = asyncio.run_coroutine_threadsafe(
            self._resolve_gallery_url_async(image_page_url), self._loop
        )
        return future.result()

    async def _resolve_gallery_url_async(self, image_page_url: str) -> str:
        from .parser import resolve_gallery_url_from_image_page
        client = self._ensure_client()
        return await resolve_gallery_url_from_image_page(client, image_page_url)

    def add_task(
        self,
        url: str,
        gallery: Optional[GalleryInfo] = None,
        force_method: Optional[DownloadMethod] = None,
        selected_torrent: Optional[TorrentInfo] = None,
        download_dir: Optional[str] = None,
        max_size_mb: float = 0.0,
        fast_queue: bool = False,
        apply_filters: bool = False,
        keyword_filter: str = "",
        save_preset: str = "Default",
        on_update: Callable[[DownloadTask], None] | None = None,
    ) -> DownloadTask:
        """Add a new download task. Returns the task immediately (non-blocking)."""
        with self._lock:
            task_id = self._next_id
            self._next_id += 1

        task = DownloadTask(
            id=task_id, 
            url=url,
            gallery=gallery,
            force_method=force_method,
            selected_torrent=selected_torrent,
            download_dir=download_dir,
            max_size_mb=max_size_mb,
            fast_queue=fast_queue,
            apply_filters=apply_filters,
            keyword_filter=keyword_filter,
            save_preset=save_preset,
        )
        with self._lock:
            self._tasks[task_id] = task

        # Schedule the coroutine on the background loop
        future = asyncio.run_coroutine_threadsafe(
            self._run_task(task, on_update), self._loop
        )
        # Keep a reference so we can cancel
        with self._lock:
            self._async_tasks[task_id] = future  # type: ignore[assignment]

        return task

    def cancel_task(self, task_id: int) -> bool:
        """Cancel a task by ID."""
        with self._lock:
            task = self._tasks.get(task_id)
            async_task = self._async_tasks.get(task_id)

        if not task:
            return False

        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
            return False

        task.status = TaskStatus.CANCELLED
        if async_task and hasattr(async_task, "cancel"):
            async_task.cancel()
        return True

    def get_task(self, task_id: int) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())

    def get_active_tasks(self) -> list[DownloadTask]:
        active_statuses = {
            TaskStatus.QUEUED,
            TaskStatus.FETCHING_INFO,
            TaskStatus.CHECKING_TORRENT,
            TaskStatus.DOWNLOADING,
        }
        with self._lock:
            return [t for t in self._tasks.values() if t.status in active_statuses]

    def clear_finished(self) -> None:
        """Remove completed/failed/cancelled tasks from the in-memory list."""
        finished = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        with self._lock:
            to_remove = [tid for tid, t in self._tasks.items() if t.status in finished]
            for tid in to_remove:
                del self._tasks[tid]
                self._async_tasks.pop(tid, None)
            # Reset counter if no tasks remain
            if not self._tasks:
                self._next_id = 1

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the task manager and its background loop."""
        if self._loop.is_running():
            # Schedule client cleanup and wait for it
            if self._client:
                future = asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)
                try:
                    future.result(timeout=2.0)
                except Exception:
                    pass

            self._loop.call_soon_threadsafe(self._loop.stop)

        if wait and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_task(
        self,
        task: DownloadTask,
        on_update: Callable[[DownloadTask], None] | None,
    ) -> None:
        client = self._ensure_client()
        await process_task(task, client, self.config, on_update)
