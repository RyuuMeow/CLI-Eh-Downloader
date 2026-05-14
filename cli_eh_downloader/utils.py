"""Utility functions for CLI-Eh-Downloader."""

from __future__ import annotations

import re
from pathlib import Path

from .models import SiteType

# URL pattern: https://e-hentai.org/g/{gid}/{token}/ or https://exhentai.org/g/{gid}/{token}/
GALLERY_URL_PATTERN = re.compile(
    r"https?://(?P<site>e-hentai|exhentai)\.org/g/(?P<gid>\d+)/(?P<token>[a-f0-9]+)/?"
)

# URL pattern: https://e-hentai.org/s/{page_token}/{gid}-{page}
IMAGE_PAGE_URL_PATTERN = re.compile(
    r"https?://(?P<site>e-hentai|exhentai)\.org/s/(?P<page_token>[A-Za-z0-9]+)/(?P<gid>\d+)-(?P<page>\d+)/?"
)

# URL pattern for listing pages: tags, categories, uploaders, search, favorites, etc.
# Matches any e-hentai/exhentai URL that is NOT a single gallery page.
PAGE_URL_PATTERN = re.compile(
    r"https?://(?P<site>e-hentai|exhentai)\.org(?P<path>/(?:tag|uploader|favorites|watched|popular|toplists|[?]f_search=).*)$"
)

# Broader check: any valid e-hentai/exhentai URL that is not a gallery
_EH_DOMAIN_PATTERN = re.compile(
    r"https?://(?P<site>e-hentai|exhentai)\.org(?P<path>.*)$"
)


def is_listing_url(url: str) -> str | None:
    """Check if a URL is a listing page (not a single gallery).

    Returns a descriptive label (e.g. 'tag', 'uploader', 'search') or None.
    """
    url = url.strip()

    # If it's a gallery URL, it's not a listing
    if GALLERY_URL_PATTERN.match(url):
        return None

    # Check explicit listing patterns
    m = PAGE_URL_PATTERN.match(url)
    if m:
        path = m.group("path")
        if path.startswith("/tag/"):
            return "tag"
        elif path.startswith("/uploader/"):
            return "uploader"
        elif path.startswith("/favorites"):
            return "favorites"
        elif path.startswith("/watched"):
            return "watched"
        elif path.startswith("/popular"):
            return "popular"
        elif path.startswith("/toplists"):
            return "toplists"
        elif "f_search=" in path:
            return "search"
        return "listing"

    # Fallback: any e-hentai URL that we didn't match as gallery
    dm = _EH_DOMAIN_PATTERN.match(url)
    if dm:
        path = dm.group("path")
        # Skip single-image pages (/s/) and API endpoints
        if path.startswith("/s/") or path.startswith("/api"):
            return None
        # The root page or any category-style path
        if path in ("", "/") or path.startswith("/?"):
            return "search"
        return "listing"

    return None


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


def matches_keyword_filter(title: str, expression: str) -> bool:
    """Evaluate a keyword filter expression against a title.

    Operators (precedence: NOT > AND > OR):
        ||   OR  — matches if ANY group matches
        &&   AND — matches if ALL terms in the group match
        !    NOT — negates a single term

    Examples:
        "cat"                → title contains "cat"
        "cat || dog"         → title contains "cat" OR "dog"
        "cat && dog"         → title contains "cat" AND "dog"
        "cat && !dog"        → title contains "cat" AND does NOT contain "dog"
        "cat || dog && !fox" → title contains "cat" OR (title contains "dog" AND NOT "fox")
    """
    expression = expression.strip()
    if not expression:
        return True  # empty filter matches everything

    title_lower = title.lower()

    # Split by || to get OR groups
    or_groups = expression.split("||")

    for group in or_groups:
        # Split each OR group by && to get AND terms
        and_terms = group.split("&&")
        group_matches = True

        for term in and_terms:
            term = term.strip()
            if not term:
                continue

            # Check for NOT operator
            if term.startswith("!"):
                keyword = term[1:].strip()
                if not keyword:
                    continue
                if keyword.lower() in title_lower:
                    group_matches = False
                    break
            else:
                if term.lower() not in title_lower:
                    group_matches = False
                    break

        if group_matches:
            return True

    return False
