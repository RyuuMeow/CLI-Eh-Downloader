"""Gallery sorting and filtering helpers."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import Config
from .models import GalleryInfo, SearchResult
from .utils import matches_keyword_filter, sanitize_filename


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
_TEMPLATE_FIELD_RE = re.compile(r"\{([^{}]+)\}")
_TEMPLATE_FALLBACKS = {
    "title": "NoTitle",
    "title_jpn": "NoJapaneseTitle",
    "titlejpn": "NoJapaneseTitle",
    "japanese_title": "NoJapaneseTitle",
    "japanesetitle": "NoJapaneseTitle",
    "jpn_title": "NoJapaneseTitle",
    "jpntitle": "NoJapaneseTitle",
    "category": "Uncategorized",
    "artist": "UnknownArtist",
    "publisher": "UnknownUploader",
    "uploader": "UnknownUploader",
    "keyword": "NoMatchingKeywords",
    "year": "UnknownYear",
    "month": "UnknownMonth",
    "tags": "NoTags",
    "site": "UnknownSite",
    "gid": "UnknownGID",
    "gallery_id": "UnknownGID",
    "galleryid": "UnknownGID",
    "id": "UnknownGID",
    "token": "UnknownToken",
    "file_count": "UnknownFileCount",
    "filecount": "UnknownFileCount",
    "files": "UnknownFileCount",
    "filesize": "UnknownFilesize",
    "file_size": "UnknownFilesize",
    "size": "UnknownFilesize",
    "posted": "UnknownPosted",
    "rating": "UnknownRating",
    "torrent_count": "UnknownTorrentCount",
    "torrentcount": "UnknownTorrentCount",
    "torrents": "UnknownTorrentCount",
}


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
    uploader_folder = _uploader_folder(gallery)
    keyword_folder = _keyword_folder(gallery, config)

    if sort_mode == "artist":
        return str(base / artist_folder) if artist_folder else str(base)
    if sort_mode == "uploader":
        return str(base / uploader_folder) if uploader_folder else str(base)
    if sort_mode == "keyword":
        return str(base / keyword_folder) if keyword_folder else str(base)
    if sort_mode == "custom_template":
        template_folder = _template_folder(gallery, config)
        return str(base / Path(template_folder)) if template_folder else str(base)

    if artist_folder:
        candidates.append((config.auto_sort_artist_priority, artist_folder))
    if uploader_folder:
        candidates.append((config.auto_sort_uploader_priority, uploader_folder))
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
    return _filter_reason_for_text(" ".join(_gallery_search_text_parts(gallery)), config)


def search_result_matches_keyword_filter(result: SearchResult, expression: str) -> bool:
    text = " ".join([result.title, result.category, result.uploader])
    return matches_keyword_filter(text, expression)


def gallery_matches_keyword_filter(gallery: GalleryInfo, expression: str) -> bool:
    return matches_keyword_filter(" ".join(_gallery_search_text_parts(gallery)), expression)


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


def _uploader_folder(gallery: GalleryInfo) -> str:
    return sanitize_filename(gallery.uploader) if gallery.uploader else ""


def _keyword_folder(gallery: GalleryInfo, config: Config) -> str:
    keyword = _matched_keyword(gallery, config)
    return sanitize_filename(keyword) if keyword else ""


def _template_folder(gallery: GalleryInfo, config: Config) -> str:
    template = config.sort_template.strip()
    if not template:
        return ""

    rendered_parts: list[str] = []
    for part in re.split(r"[\\/]+", template):
        if not part.strip():
            continue
        rendered = _render_template_part(part, gallery, config).strip()
        if rendered:
            rendered_parts.append(sanitize_filename(rendered))
    return str(Path(*rendered_parts)) if rendered_parts else ""


def _render_template_part(part: str, gallery: GalleryInfo, config: Config) -> str:
    def replace_field(match: re.Match[str]) -> str:
        return _resolve_template_expression(match.group(1), gallery, config)

    return _TEMPLATE_FIELD_RE.sub(replace_field, part)


def _resolve_template_expression(expression: str, gallery: GalleryInfo, config: Config) -> str:
    options = _split_template_options(expression)
    if not options:
        return "Unknown"

    for option in options:
        value = _template_option_value(option, gallery, config)
        if value:
            return value
    return _fallback_value(options[0])


def _split_template_options(expression: str) -> list[str]:
    options: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0

    while index < len(expression):
        char = expression[index]
        if quote:
            current.append(char)
            if char == "\\" and index + 1 < len(expression):
                index += 1
                current.append(expression[index])
            elif char == quote:
                quote = ""
            index += 1
            continue

        if char in ("'", '"'):
            quote = char
            current.append(char)
            index += 1
            continue

        if expression[index:index + 2] == "||":
            option = "".join(current).strip()
            if option:
                options.append(option)
            current = []
            index += 2
            continue

        current.append(char)
        index += 1

    option = "".join(current).strip()
    if option:
        options.append(option)
    return options


def _template_option_value(option: str, gallery: GalleryInfo, config: Config) -> str:
    literal = _quoted_literal(option)
    if literal is not None:
        return literal
    return _template_value(option, gallery, config)


def _quoted_literal(value: str) -> str | None:
    value = value.strip()
    if len(value) < 2 or value[0] != value[-1] or value[0] not in ("'", '"'):
        return None
    quote = value[0]
    inner = value[1:-1]
    return inner.replace(f"\\{quote}", quote).replace("\\\\", "\\")


def _template_value(field: str, gallery: GalleryInfo, config: Config) -> str:
    key = field.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "japanese_title": "title_jpn",
        "japanesetitle": "title_jpn",
        "jpn_title": "title_jpn",
        "jpntitle": "title_jpn",
        "title_jpn": "title_jpn",
        "titlejpn": "title_jpn",
        "gid": "gid",
        "gallery_id": "gid",
        "galleryid": "gid",
        "id": "gid",
        "file_count": "file_count",
        "files": "file_count",
        "filecount": "file_count",
        "file_size": "filesize",
        "size": "filesize",
        "torrent_count": "torrent_count",
        "torrentcount": "torrent_count",
        "torrents": "torrent_count",
    }
    key = aliases.get(key, key)

    if key == "title":
        return gallery.title
    if key == "title_jpn":
        return gallery.title_jpn
    if key == "category":
        return gallery.category
    if key == "uploader":
        return gallery.uploader
    if key == "artist":
        return _first_tag(gallery, "artist")
    if key == "publisher":
        return gallery.uploader
    if key == "keyword":
        return _matched_keyword(gallery, config)
    if key == "year":
        return _posted_date_part(gallery.posted, "year")
    if key == "month":
        return _posted_date_part(gallery.posted, "month")
    if key == "tags":
        return ", ".join(_all_tags(gallery))
    if key == "site":
        return gallery.site.value
    if key == "gid":
        return str(gallery.gid) if gallery.gid else ""
    if key == "token":
        return gallery.token
    if key == "file_count":
        return str(gallery.file_count) if gallery.file_count else ""
    if key == "filesize":
        return gallery.filesize
    if key == "posted":
        return gallery.posted
    if key == "rating":
        return str(gallery.rating) if gallery.rating else ""
    if key == "torrent_count":
        return str(gallery.torrent_count) if gallery.torrent_count else ""
    return _first_tag(gallery, key)


def _first_tag(gallery: GalleryInfo, namespace: str) -> str:
    values = gallery.tags.get(namespace, [])
    return values[0] if values else ""


def _matched_keyword(gallery: GalleryInfo, config: Config) -> str:
    if not config.sort_by_keyword_keywords.strip():
        return ""
    searchable = " ".join(_gallery_search_text_parts(gallery)).lower()
    for keyword in split_keywords(config.sort_by_keyword_keywords):
        if keyword.lower() in searchable:
            return keyword
    return ""


def _posted_date_part(posted: str, part: str) -> str:
    value = str(posted).strip()
    if not value:
        return ""

    if value.isdigit():
        try:
            dt = datetime.fromtimestamp(int(value))
            return f"{dt.year:04d}" if part == "year" else f"{dt.month:02d}"
        except (OverflowError, OSError, ValueError):
            return ""

    match = re.search(r"(?P<year>\d{4})[-/](?P<month>\d{1,2})", value)
    if match:
        if part == "year":
            return match.group("year")
        return f"{int(match.group('month')):02d}"
    return ""


def _fallback_value(field: str) -> str:
    literal = _quoted_literal(field)
    if literal is not None:
        return literal or "Unknown"

    key = field.strip().lower().replace("-", "_").replace(" ", "_")
    fallback = _TEMPLATE_FALLBACKS.get(key)
    if fallback:
        return fallback

    normalized = re.sub(r"[^0-9A-Za-z]+", " ", field).strip()
    if not normalized:
        return "Unknown"
    return "Unknown" + "".join(part.capitalize() for part in normalized.split())


def _all_tags(gallery: GalleryInfo) -> list[str]:
    return [tag for tags in gallery.tags.values() for tag in tags]


def _gallery_search_text_parts(gallery: GalleryInfo) -> list[str]:
    parts = [
        gallery.title,
        gallery.title_jpn,
        gallery.category,
        gallery.uploader,
    ]
    for namespace, tags in gallery.tags.items():
        parts.extend(tags)
        parts.extend(f"{namespace}:{tag}" for tag in tags)
    return parts
