"""Download engine — orchestrates gallery image and torrent downloads."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

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
from .parser import fetch_gallery_info, fetch_image_list, fetch_image_url, fetch_torrent_list
from .torrent import HAS_LIBTORRENT, download_torrent_file, download_via_torrent
from .utils import ensure_dir, parse_gallery_url, sanitize_filename

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

        # Determine output directory (images only go here)
        dir_name = sanitize_filename(gallery.title_jpn or gallery.title)
        task.output_dir = str(Path(config.download_dir) / dir_name)
        ensure_dir(task.output_dir)

        _notify(on_update, task)

        # --- Step 3: Try torrent if preferred or forced ---
        method_to_use = task.force_method
        if not method_to_use:
            method_to_use = DownloadMethod.TORRENT if config.prefer_torrent and gallery.torrent_count > 0 else DownloadMethod.DIRECT

        if method_to_use == DownloadMethod.TORRENT and gallery.torrent_count > 0:
            task.status = TaskStatus.CHECKING_TORRENT
            _notify(on_update, task)

            best_torrent = task.selected_torrent
            if not best_torrent:
                try:
                    torrents = await fetch_torrent_list(client, gallery)
                    if torrents:
                        best_torrent = torrents[0]  # Already sorted by seeds
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

                if torrent_path and HAS_LIBTORRENT and best_torrent.seeds > 0:
                    task.method = DownloadMethod.TORRENT
                    task.status = TaskStatus.DOWNLOADING
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
                elif torrent_path and not HAS_LIBTORRENT:
                    log.info(
                        "libtorrent not installed. .torrent saved to: %s — falling back to direct download.",
                        torrent_path,
                    )

        # --- Step 4: Direct image download (fallback or primary) ---
        task.method = DownloadMethod.DIRECT
        task.status = TaskStatus.DOWNLOADING
        task.downloaded = 0
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

        # --- Phase A: Resolve image URLs sequentially (respects rate limit) ---
        for img in images:
            if task.status in (TaskStatus.CANCELLED, TaskStatus.PAUSED):
                break
            try:
                await fetch_image_url(client, img)
                log.debug("Resolved image %d: %s -> %s", img.index, img.filename, img.image_url)
            except Exception as e:
                log.warning("Failed to resolve image %d URL: %s", img.index, e)
                # Keep going — we'll skip this image in the download phase

        # --- Phase B: Download resolved images with concurrency ---
        semaphore = asyncio.Semaphore(config.max_parallel)

        async def _download_one(img: GalleryImage) -> bool:
            if task.status in (TaskStatus.CANCELLED, TaskStatus.PAUSED):
                return False

            if not img.image_url:
                log.warning("Skipping image %d: no URL resolved", img.index)
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

                try:
                    dest = str(output_path / (img.filename or f"{img.index:04d}.jpg"))
                    # Pass the image page URL as Referer — hath network servers require it
                    await client.download_file(
                        img.image_url, dest, referer=img.page_url
                    )

                    task.downloaded += 1
                    task.progress = task.downloaded / task.total if task.total else 0
                    _notify(on_update, task)
                    return True
                except Exception as e:
                    log.error("Failed to download image %d (%s): %s", img.index, img.filename, e)
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
            task.error = f"{failed_count} images failed"
        else:
            task.status = TaskStatus.FAILED
            task.error = "All downloads failed"

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


def _notify(callback: ProgressCallback, task: DownloadTask) -> None:
    if callback:
        callback(task)
