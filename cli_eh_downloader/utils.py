"""Utility functions for CLI-Eh-Downloader."""

from __future__ import annotations

import re
from pathlib import Path

from .models import SiteType

# URL pattern: https://e-hentai.org/g/{gid}/{token}/ or https://exhentai.org/g/{gid}/{token}/
GALLERY_URL_PATTERN = re.compile(
    r"https?://(?P<site>e-hentai|exhentai)\.org/g/(?P<gid>\d+)/(?P<token>[a-f0-9]+)/?"
)


def parse_gallery_url(url: str) -> tuple[int, str, SiteType] | None:
    """Parse a gallery URL and return (gid, token, site_type), or None if invalid."""
    match = GALLERY_URL_PATTERN.match(url.strip())
    if not match:
        return None
    gid = int(match.group("gid"))
    token = match.group("token")
    site_str = match.group("site")
    site = SiteType.EX_HENTAI if site_str == "exhentai" else SiteType.E_HENTAI
    return gid, token, site


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Remove invalid characters from a filename (Windows-safe)."""
    invalid_chars = r'[<>:"/\\|?*\x00-\x1f]'
    sanitized = re.sub(invalid_chars, "_", name)
    sanitized = sanitized.rstrip(". ")
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    return sanitized or "untitled"


def format_size(size_bytes: int | float) -> str:
    """Format bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def get_base_url(site: SiteType) -> str:
    """Get the base URL for a site type."""
    if site == SiteType.EX_HENTAI:
        return "https://exhentai.org"
    return "https://e-hentai.org"


def build_gallery_url(gid: int, token: str, site: SiteType) -> str:
    """Build a full gallery URL from components."""
    return f"{get_base_url(site)}/g/{gid}/{token}/"


def build_torrent_page_url(gid: int, token: str, site: SiteType) -> str:
    """Build the torrent page URL for a gallery."""
    return f"{get_base_url(site)}/gallerytorrents.php?gid={gid}&t={token}"


def ensure_dir(path: str | Path) -> Path:
    """Ensure a directory exists, creating it if necessary."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
