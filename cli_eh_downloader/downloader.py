"""Download engine — orchestrates gallery image and torrent downloads."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx

from .client import EHClient
from .config import Config
from .models import (
    DownloadMethod,
    DownloadTask,
    GalleryImage,
    GalleryInfo,
    SiteType,
    TaskStatus,
)
from .parser import (
    fetch_gallery_info,
    fetch_image_list,
    fetch_image_url,
    fetch_torrent_list,
    resolve_gallery_url_from_image_page,
)
from .sorting import gallery_filter_reason, gallery_matches_keyword_filter, resolve_sorted_download_dir
from .torrent import HAS_LIBTORRENT, download_torrent_file, download_via_torrent
from .torrent_client import (
    ensure_torrent_client_settings,
    is_torrent_client_open_success,
    open_torrent_external,
)
from .utils import IMAGE_PAGE_URL_PATTERN, ensure_dir, parse_gallery_url, sanitize_filename

log = logging.getLogger(__name__)

# Type alias for the progress callback
ProgressCallback = Callable[[DownloadTask], None] | None


def _get_meta_dir(config: Config) -> Path:
    """Return the .meta directory at project root (for metadata JSON)."""
    p = Path(".meta")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_torrents_dir(config: Config) -> Path:
    """Return the .torrents directory at project root (for .torrent files)."""
    p = Path(".torrents")
    p.mkdir(parents=True, exist_ok=True)
    return p


async def process_task(
    task: DownloadTask,
    client: EHClient,
    config: Config,
    on_update: ProgressCallback = None,
) -> None:
    """Execute a full download task (fetch info → torrent/direct → save metadata)."""
    try:
        # --- Step 1: Parse URL ---
        parsed = parse_gallery_url(task.url)
        if not parsed and IMAGE_PAGE_URL_PATTERN.match(task.url):
            task.status = TaskStatus.FETCHING_INFO
            _notify(on_update, task)
            gallery_url = await resolve_gallery_url_from_image_page(client, task.url)
            task.url = gallery_url
            parsed = parse_gallery_url(gallery_url)

        if not parsed:
            task.status = TaskStatus.FAILED
            task.error = "Invalid gallery URL"
            _notify(on_update, task)
            return

        gid, token, site = parsed

        # Check ExHentai access
        if site == SiteType.EX_HENTAI and not client.can_access_exhentai():
            task.status = TaskStatus.FAILED
            task.error = "ExHentai requires cookies. Run: config set cookie"
            _notify(on_update, task)
            return

        # --- Step 2: Fetch gallery metadata ---
        if task.gallery:
            gallery = task.gallery
        else:
            task.status = TaskStatus.FETCHING_INFO
            _notify(on_update, task)
            gallery = await fetch_gallery_info(client, gid, token, site)
            task.gallery = gallery

        task.total = gallery.file_count

        if task.apply_filters:
            reason = gallery_filter_reason(gallery, config)
            if reason:
                task.status = TaskStatus.CANCELLED
                task.error = f"Skipped by filter: {reason}"
                _notify(on_update, task)
                return

        if task.keyword_filter and not gallery_matches_keyword_filter(gallery, task.keyword_filter):
            task.status = TaskStatus.CANCELLED
            task.error = f"Skipped: keyword filter did not match '{task.keyword_filter}'"
            _notify(on_update, task)
            return

        if task.max_size_mb > 0 and gallery.filesize:
            try:
                size_mb = int(gallery.filesize) / (1024 * 1024)
                if size_mb > task.max_size_mb:
                    task.status = TaskStatus.CANCELLED
                    task.error = f"Skipped: {size_mb:.1f} MB > {task.max_size_mb:.0f} MB limit"
                    _notify(on_update, task)
                    return
            except (ValueError, TypeError):
                pass

        # Determine output directory (images only go here)
        dir_name = sanitize_filename(gallery.title_jpn or gallery.title)
        download_dir = resolve_sorted_download_dir(task.download_dir or config.download_dir, gallery, config)
        task.output_dir = str(Path(download_dir) / dir_name)

        _notify(on_update, task)

        # --- Step 3: Try torrent if preferred or forced ---
        direct_reason: str | None = None
        method_to_use = task.force_method
        if not method_to_use:
            if gallery.torrent_count > 0:
                method_to_use = DownloadMethod.TORRENT
            else:
                method_to_use = DownloadMethod.DIRECT
                direct_reason = "this gallery has no listed torrents"
        elif method_to_use == DownloadMethod.DIRECT:
            direct_reason = "Download Mode is Direct Download"

        if method_to_use == DownloadMethod.TORRENT and gallery.torrent_count > 0:
            task.status = TaskStatus.CHECKING_TORRENT
            _notify(on_update, task)

            best_torrent = task.selected_torrent
            if not best_torrent:
                try:
                    torrents = await fetch_torrent_list(client, gallery)
                    if torrents:
                        best_torrent = next((t for t in torrents if t.seeds > 0), torrents[0])
                except Exception as e:
                    log.warning("Failed to fetch torrent list: %s", e)

            if best_torrent:
                # Download the .torrent file into .torrents/ directory
                torrents_dir = _get_torrents_dir(config)
                try:
                    torrent_path = await download_torrent_file(client, best_torrent, str(torrents_dir))
                    task.torrent_path = torrent_path
                    log.info("Torrent file saved: %s", torrent_path)
                except Exception as e:
                    log.warning("Failed to download .torrent file: %s", e)
                    torrent_path = None

                use_external_client = ensure_torrent_client_settings(config)
                if torrent_path and use_external_client:
                    external_msg = open_torrent_external(
                        torrent_path,
                        download_dir,
                        config,
                    )
                    if is_torrent_client_open_success(external_msg):
                        task.method = DownloadMethod.TORRENT
                        task.status = TaskStatus.COMPLETED
                        task.progress = 1.0
                        task.downloaded = task.total
                        _set_fast_queue_notice(
                            task,
                            (
                                f"Task #{task.id}: using torrent via external client "
                                f"({best_torrent.seeds} seed(s)). {external_msg}"
                            ),
                        )
                        _save_torrent_task_metadata(task, config)
                        _notify(on_update, task)
                        return
                    direct_reason = external_msg
                elif torrent_path and HAS_LIBTORRENT and best_torrent.seeds > 0:
                    task.method = DownloadMethod.TORRENT
                    task.status = TaskStatus.DOWNLOADING
                    ensure_dir(task.output_dir)
                    _set_fast_queue_notice(
                        task,
                        f"Task #{task.id}: using torrent ({best_torrent.seeds} seed(s); Auto mode selected torrent).",
                    )
                    _notify(on_update, task)

                    def _torrent_progress(progress: float, downloaded: int, total: int) -> None:
                        task.progress = progress
                        task.downloaded = downloaded
                        task.total = total
                        _notify(on_update, task)

                    success = await download_via_torrent(
                        torrent_path, task.output_dir, _torrent_progress
                    )
                    if success:
                        task.status = TaskStatus.COMPLETED
                        task.progress = 1.0
                        _save_metadata(task, config)
                        _notify(on_update, task)
                        return
                    else:
                        log.warning("Torrent download failed/timed out. Falling back to direct download.")
                        direct_reason = "torrent download failed or timed out"
                elif best_torrent.seeds <= 0:
                    direct_reason = "available torrents have 0 seeds"
                else:
                    direct_reason = "torrent file could not be saved"
            else:
                direct_reason = "torrent list could not be loaded"

        # --- Step 4: Direct image download (fallback or primary) ---
        task.method = DownloadMethod.DIRECT
        task.status = TaskStatus.DOWNLOADING
        task.downloaded = 0
        ensure_dir(task.output_dir)
        _set_fast_queue_notice(
            task,
            f"Task #{task.id}: using direct download ({direct_reason or 'Direct Download selected'}).",
        )
        _notify(on_update, task)

        images = await fetch_image_list(client, gallery)
        task.total = len(images)
        log.info("Found %d image pages for gallery %d", len(images), gallery.gid)

        if not images:
            task.status = TaskStatus.FAILED
            task.error = "No images found on gallery page"
            _notify(on_update, task)
            return

        # Check which images are already downloaded
        output_path = Path(task.output_dir)
        existing_files = {f.name for f in output_path.iterdir() if f.is_file()} if output_path.exists() else set()

        # Resolve each H@H image URL immediately before downloading it. Those URLs
        # carry a short-lived keystamp, so resolving the whole gallery up front can
        # leave later downloads with stale URLs and noisy 403 failures.

        semaphore = asyncio.Semaphore(config.max_parallel)
        failure_reasons: dict[str, int] = {}

        def _record_failure(reason: str) -> None:
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

        async def _download_one(img: GalleryImage) -> bool:
            if task.status in (TaskStatus.CANCELLED, TaskStatus.PAUSED):
                return False

            # Skip if already downloaded
            if img.filename and img.filename in existing_files:
                task.downloaded += 1
                task.progress = task.downloaded / task.total if task.total else 0
                _notify(on_update, task)
                return True

            async with semaphore:
                if task.status in (TaskStatus.CANCELLED, TaskStatus.PAUSED):
                    return False

                last_error: Exception | None = None
                attempts = max(1, config.retry_count)
                for attempt in range(attempts):
                    try:
                        if not img.image_url:
                            await fetch_image_url(client, img)
                            log.debug("Resolved image %d: %s -> %s", img.index, img.filename, img.image_url)

                        if not img.image_url:
                            raise RuntimeError("no direct image URL resolved")

                        if img.filename and img.filename in existing_files:
                            task.downloaded += 1
                            task.progress = task.downloaded / task.total if task.total else 0
                            _notify(on_update, task)
                            return True

                        dest = str(output_path / (img.filename or f"{img.index:04d}.jpg"))
                        # Pass the image page URL as Referer; H@H servers require it.
                        await client.download_file(
                            img.image_url,
                            dest,
                            referer=img.page_url,
                            max_attempts=1,
                            quiet=True,
                        )

                        task.downloaded += 1
                        task.progress = task.downloaded / task.total if task.total else 0
                        _notify(on_update, task)
                        return True
                    except httpx.HTTPStatusError as e:
                        last_error = e
                        status_code = e.response.status_code
                        if status_code == 403 and attempt < attempts - 1:
                            img.image_url = None
                            await asyncio.sleep(config.retry_delay)
                            continue
                        _record_failure(f"HTTP {status_code}")
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < attempts - 1:
                            await asyncio.sleep(config.retry_delay)
                            continue
                        _record_failure(type(e).__name__)

                if config.debug_mode and last_error:
                    log.debug(
                        "Failed to download image %d (%s): %s",
                        img.index,
                        img.filename,
                        last_error,
                        exc_info=True,
                    )
                return False

        results = await asyncio.gather(*[_download_one(img) for img in images])

        if task.status == TaskStatus.CANCELLED:
            return

        failed_count = results.count(False)
        if failed_count == 0:
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
        elif task.downloaded > 0:
            task.status = TaskStatus.COMPLETED
            task.error = _format_download_failures(failed_count, failure_reasons, config.debug_mode)
        else:
            task.status = TaskStatus.FAILED
            task.error = _format_download_failures(failed_count, failure_reasons, config.debug_mode, all_failed=True)

        _save_metadata(task, config)
        _notify(on_update, task)

    except asyncio.CancelledError:
        task.status = TaskStatus.CANCELLED
        _notify(on_update, task)
    except Exception as e:
        log.exception("Task %d fatal error", task.id)
        task.status = TaskStatus.FAILED
        if config.debug_mode:
            import traceback
            task.error = f"{e}\n{traceback.format_exc()}"
        else:
            task.error = str(e)
        _notify(on_update, task)


def _save_metadata(task: DownloadTask, config: Config) -> None:
    """Save gallery metadata as a JSON file in the .meta/ directory."""
    if not task.gallery:
        return

    g = task.gallery
    metadata = {
        "gid": g.gid,
        "token": g.token,
        "url": g.url,
        "title": g.title,
        "title_jpn": g.title_jpn,
        "category": g.category,
        "uploader": g.uploader,
        "tags": g.tags,
        "file_count": g.file_count,
        "rating": g.rating,
        "posted": g.posted,
        "output_dir": task.output_dir,
        "method": task.method.value,
        "torrent_path": task.torrent_path,
        "downloaded_at": datetime.now().isoformat(),
    }

    meta_dir = _get_meta_dir(config)
    dir_name = Path(task.output_dir).name
    meta_path = meta_dir / f"{dir_name}.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def save_torrent_metadata(gallery, torrent_path: str, config: Config) -> None:
    """Save metadata for a torrent-only download (no direct download task)."""
    from .utils import sanitize_filename

    g = gallery
    title = g.title_jpn or g.title or f"gallery_{g.gid}"
    metadata = {
        "gid": g.gid,
        "token": g.token,
        "url": g.url,
        "title": g.title,
        "title_jpn": g.title_jpn,
        "category": g.category,
        "uploader": g.uploader,
        "tags": g.tags,
        "file_count": g.file_count,
        "rating": g.rating,
        "posted": g.posted,
        "output_dir": "",
        "method": "torrent_external",
        "torrent_path": torrent_path,
        "downloaded_at": datetime.now().isoformat(),
    }

    meta_dir = _get_meta_dir(config)
    safe_name = sanitize_filename(title)
    meta_path = meta_dir / f"{safe_name}.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_torrent_task_metadata(task: DownloadTask, config: Config) -> None:
    if not task.gallery or not task.torrent_path:
        return
    save_torrent_metadata(task.gallery, task.torrent_path, config)


def _set_fast_queue_notice(task: DownloadTask, message: str) -> None:
    if task.fast_queue:
        task.notice = message


def _format_download_failures(
    failed_count: int,
    failure_reasons: dict[str, int],
    debug_mode: bool,
    *,
    all_failed: bool = False,
) -> str:
    prefix = "All downloads failed" if all_failed else f"{failed_count} images failed"
    if failure_reasons:
        details = ", ".join(
            f"{reason}: {count}" for reason, count in sorted(failure_reasons.items())
        )
        prefix = f"{prefix} ({details})"
    if not debug_mode:
        prefix = f"{prefix}; enable debug_mode for per-image details"
    return prefix


def _notify(callback: ProgressCallback, task: DownloadTask) -> None:
    if callback:
        callback(task)
