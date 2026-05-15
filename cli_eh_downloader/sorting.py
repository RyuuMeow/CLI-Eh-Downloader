"""Gallery sorting and filtering helpers."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .models import GalleryInfo, SearchResult
from .utils import sanitize_filename


AI_KEYWORDS = (
    "ai generated",
    "ai-generated",
    "ai-generated content",
    "ai generated content",
    "ai生成",
    "ai 生成",
    "ai繪",
    "ai 绘",
    "ai繪圖",
    "ai 绘图",
    "ai art",
    "aiart",
    "stable diffusion",
    "midjourney",
    "novelai",
)


def split_keywords(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def resolve_sorted_download_dir(base_dir: str, gallery: GalleryInfo, config: Config) -> str:
    """Return the base download directory after applying save sorting."""
    base = Path(base_dir)
    sort_mode = config.auto_sort
    if sort_mode == "off":
        return str(base)

    candidates: list[tuple[int, str]] = []
    artist_folder = _artist_folder(gallery)
    publisher_folder = _publisher_folder(gallery)
    keyword_folder = _keyword_folder(gallery, config)

    if sort_mode == "artist":
        return str(base / artist_folder) if artist_folder else str(base)
    if sort_mode == "publisher":
        return str(base / publisher_folder) if publisher_folder else str(base)
    if sort_mode == "keyword":
        return str(base / keyword_folder) if keyword_folder else str(base)

    if artist_folder:
        candidates.append((config.auto_sort_artist_priority, artist_folder))
    if publisher_folder:
        candidates.append((config.auto_sort_publisher_priority, publisher_folder))
    if keyword_folder:
        candidates.append((config.auto_sort_keyword_priority, keyword_folder))
    if not candidates:
        return str(base)

    _priority, folder = sorted(candidates, key=lambda item: item[0])[0]
    return str(base / folder)


def search_result_filter_reason(result: SearchResult, config: Config) -> str:
    text = " ".join([result.title, result.category, result.uploader])
    return _filter_reason_for_text(text, config)


def gallery_filter_reason(gallery: GalleryInfo, config: Config) -> str:
    text_parts = [
        gallery.title,
        gallery.title_jpn,
        gallery.category,
        gallery.uploader,
        *_all_tags(gallery),
    ]
    return _filter_reason_for_text(" ".join(text_parts), config)


def _filter_reason_for_text(text: str, config: Config) -> str:
    normalized = text.lower()
    if config.anti_ai:
        for keyword in AI_KEYWORDS:
            if keyword.lower() in normalized:
                return f"Anti AI matched '{keyword}'"

    for keyword in split_keywords(config.filter_keyword_filter):
        if keyword.lower() in normalized:
            return f"Keyword filter matched '{keyword}'"

    return ""


def _artist_folder(gallery: GalleryInfo) -> str:
    artists = gallery.tags.get("artist", [])
    if not artists:
        return ""
    return sanitize_filename(artists[0])


def _publisher_folder(gallery: GalleryInfo) -> str:
    publishers = gallery.tags.get("publisher", [])
    if publishers:
        return sanitize_filename(publishers[0])
    return sanitize_filename(gallery.uploader) if gallery.uploader else ""


def _keyword_folder(gallery: GalleryInfo, config: Config) -> str:
    if not config.sort_by_keyword_keywords.strip():
        return ""
    searchable = " ".join([gallery.title, gallery.title_jpn, *_all_tags(gallery)]).lower()
    for keyword in split_keywords(config.sort_by_keyword_keywords):
        if keyword.lower() in searchable:
            return sanitize_filename(keyword)
    return ""


def _all_tags(gallery: GalleryInfo) -> list[str]:
    return [tag for tags in gallery.tags.values() for tag in tags]
