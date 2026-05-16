"""Torrent download support for CLI-Eh-Downloader.

Attempts to use libtorrent for embedded torrent downloading.
Falls back to saving the .torrent file if libtorrent is not available.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .client import EHClient
from .models import TorrentInfo
from .parser import parse_torrent_list_html

# Try to import libtorrent (optional dependency)
try:
    import libtorrent as lt
    HAS_LIBTORRENT = True
except ImportError:
    HAS_LIBTORRENT = False


async def download_torrent_file(
    client: EHClient,
    torrent: TorrentInfo,
    output_dir: str,
) -> str:
    """Download the .torrent file to output_dir. Returns path to the file."""
    dest_dir = Path(output_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Use the torrent name from the page (e.g. "[Artist] Title.zip")
    # Strip the .zip extension if present and add .torrent
    base_name = torrent.name
    if base_name.lower().endswith(".zip"):
        base_name = base_name[:-4]
    
    # Sanitize for filesystem (Windows-safe)
    import re
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', base_name)
    safe_name = safe_name.rstrip('. ')
    if not safe_name:
        safe_name = f"torrent_{id(torrent)}"
    safe_name += ".torrent"
    
    dest_path = str(dest_dir / safe_name)
    download_url = await _resolve_torrent_download_url(client, torrent)
    await client.download_file(download_url, dest_path)
    return dest_path


async def _resolve_torrent_download_url(client: EHClient, torrent: TorrentInfo) -> str:
    if not torrent.gtid:
        return torrent.url

    response = await client.post_form(
        torrent.url,
        {
            "gtid": torrent.gtid,
            "torrent_info": "Information",
        },
    )
    direct_url = _find_personalized_torrent_url(response.text, str(response.url))
    if direct_url:
        return direct_url

    parsed = parse_torrent_list_html(response.text, str(response.url))
    if parsed:
        return parsed[0].url

    raise ValueError(f"Could not resolve torrent download URL for gtid {torrent.gtid}")


def _find_personalized_torrent_url(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    redistributable = ""
    first_torrent = ""

    for link in soup.select("a[href]"):
        href = str(link.get("href", ""))
        if "/torrent/" not in href.lower() or not href.lower().split("?", 1)[0].endswith(".torrent"):
            continue
        url = urljoin(base_url, href)
        text = link.get_text(" ", strip=True).lower()
        if not first_torrent:
            first_torrent = url
        if "personalized" in text:
            return url
        if "redistributable" in text:
            redistributable = url

    return redistributable or first_torrent


async def download_via_torrent(
    torrent_path: str,
    output_dir: str,
    on_progress: Callable[[float, int, int], None] | None = None,
    timeout: int = 3600,
) -> bool:
    """Download gallery content using a .torrent file via libtorrent.

    Args:
        torrent_path: Path to the .torrent file.
        output_dir: Directory to save downloaded files.
        on_progress: Callback(progress_fraction, downloaded_bytes, total_bytes).
        timeout: Maximum seconds to wait for completion.

    Returns:
        True if download completed, False if timed out or failed.
    """
    if not HAS_LIBTORRENT:
        return False

    # Run libtorrent in a thread to avoid blocking the event loop
    return await asyncio.to_thread(
        _torrent_download_blocking, torrent_path, output_dir, on_progress, timeout
    )


def _torrent_download_blocking(
    torrent_path: str,
    output_dir: str,
    on_progress: Callable[[float, int, int], None] | None,
    timeout: int,
) -> bool:
    """Blocking torrent download using libtorrent (runs in thread)."""
    if not HAS_LIBTORRENT:
        return False

    ses = lt.session()
    ses.listen_on(6881, 6891)

    # Add reasonable settings
    settings = {
        "active_downloads": 1,
        "active_seeds": 0,
        "active_limit": 1,
    }
    ses.apply_settings(settings)

    info = lt.torrent_info(torrent_path)
    params = {
        "ti": info,
        "save_path": output_dir,
        "storage_mode": lt.storage_mode_t.storage_mode_sparse,
    }
    handle = ses.add_torrent(params)
    handle.set_sequential_download(True)

    import time
    start = time.time()

    while not handle.status().is_seeding:
        s = handle.status()

        if on_progress:
            total = s.total_wanted
            downloaded = s.total_wanted_done
            progress = s.progress
            on_progress(progress, downloaded, total)

        if time.time() - start > timeout:
            ses.remove_torrent(handle)
            return False

        time.sleep(1.0)

    # Done — remove torrent from session (keep files)
    ses.remove_torrent(handle)
    return True
