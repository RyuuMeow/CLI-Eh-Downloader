"""Data models for GoEHentai."""

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
