"""Data models for CLI-Eh-Downloader."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SiteType(Enum):
    E_HENTAI = "e-hentai"
    EX_HENTAI = "exhentai"


class TaskStatus(Enum):
    QUEUED = "queued"
    FETCHING_INFO = "fetching_info"
    CHECKING_TORRENT = "checking_torrent"
    DOWNLOADING = "downloading"
    SEEDING = "seeding"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class DownloadMethod(Enum):
    TORRENT = "torrent"
    DIRECT = "direct"


@dataclass
class GalleryInfo:
    """Gallery metadata."""
    gid: int
    token: str
    url: str
    site: SiteType
    title: str = ""
    title_jpn: str = ""
    category: str = ""
    uploader: str = ""
    tags: dict[str, list[str]] = field(default_factory=dict)
    file_count: int = 0
    filesize: str = ""
    posted: str = ""
    rating: float = 0.0
    torrent_count: int = 0
    thumb: str = ""


@dataclass
class GalleryImage:
    """A single image in a gallery."""
    index: int          # 1-based page number
    page_url: str       # URL to the image viewer page
    image_url: Optional[str] = None   # Direct image URL (resolved later)
    filename: Optional[str] = None


@dataclass
class TorrentInfo:
    """Torrent file information from gallery torrent page."""
    name: str
    url: str            # Download URL for the .torrent file
    size: str
    seeds: int
    peers: int
    downloads: int
    posted: str
    gtid: str = ""      # ExHentai gallery torrent id; used when URL requires an info POST first


@dataclass
class SearchResult:
    """A single gallery from search results."""
    gid: int
    token: str
    url: str
    title: str
    category: str = ""
    rating: str = ""
    uploader: str = ""
    pages: str = ""
    posted: str = ""
    site: SiteType = SiteType.E_HENTAI


@dataclass
class DownloadTask:
    """A download task managed by the task manager."""
    id: int
    url: str
    gallery: Optional[GalleryInfo] = None
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0
    downloaded: int = 0
    total: int = 0
    method: DownloadMethod = DownloadMethod.DIRECT
    error: Optional[str] = None
    output_dir: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    torrent_path: Optional[str] = None
    selected_torrent: Optional[TorrentInfo] = None
    force_method: Optional[DownloadMethod] = None
    download_dir: Optional[str] = None
    max_size_mb: float = 0.0
    fast_queue: bool = False
    notice: Optional[str] = None
    apply_filters: bool = False
    keyword_filter: str = ""

    @property
    def display_title(self) -> str:
        if self.gallery:
            return self.gallery.title_jpn or self.gallery.title or f"Gallery #{self.gallery.gid}"
        return self.url

    @property
    def short_title(self) -> str:
        title = self.display_title
        if len(title) > 50:
            return title[:47] + "..."
        return title


@dataclass
class SearchPage:
    """Paginated search results."""
    results: list[SearchResult]
    current_page: int = 0
    total_results: int = 0
    next_url: str = ""
    prev_url: str = ""

    @property
    def has_next(self) -> bool:
        return bool(self.next_url)

    @property
    def has_prev(self) -> bool:
        return bool(self.prev_url)


class FetchMode(Enum):
    """How to iterate listing pages for bulk download."""
    ITER = "iter"              # All pages from start to end
    CURRENT_PAGE = "current"   # Only the current (first) page
    CUSTOM_RANGE = "range"     # Custom start–end page range


class BulkDownloadMode(Enum):
    """How to handle each gallery in bulk download."""
    ASK_EACH = "ask"           # Prompt for each gallery
    DIRECT = "direct"          # Always use direct download
    AUTO = "auto"              # Auto-select best method (smart)


@dataclass
class BulkDownloadConfig:
    """Configuration for a bulk (listing page) download session."""
    url: str                                         # The listing page URL
    page_type: str = "listing"                       # e.g. 'tag', 'uploader', 'search'
    fetch_mode: FetchMode = FetchMode.CURRENT_PAGE
    start_page: int = 1                              # 1-indexed, for CUSTOM_RANGE
    end_page: int = 1                                # 1-indexed, for CUSTOM_RANGE
    download_mode: BulkDownloadMode = BulkDownloadMode.AUTO
    max_galleries: int = 0                           # 0 = unlimited
    max_size_mb: float = 0.0                         # 0 = no limit (in MB)
    keyword_filter: str = ""                         # Only download if title contains this
    download_dir: str = ""                           # Override download dir (empty = global)
    total_results: int = 0                           # Populated after first fetch
