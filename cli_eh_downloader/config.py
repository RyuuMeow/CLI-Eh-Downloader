"""Configuration management for CLI-Eh-Downloader."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_FILENAME = "config.toml"
DEFAULT_CONFIG_PATHS = [
    Path.cwd() / CONFIG_FILENAME,
    Path.home() / ".config" / "cli-eh-downloader" / CONFIG_FILENAME,
]


@dataclass
class Config:
    """Application configuration."""

    # Download settings
    download_dir: str = "./downloads"
    max_parallel: int = 3
    rate_limit_delay: float = 1.5
    retry_count: int = 3
    retry_delay: float = 5.0
    prefer_torrent: bool = True
    default_download_mode: str = "auto"  # auto, ask, direct
    fast_queue: bool = True

    # Cookie settings (for ExHentai)
    ipb_member_id: str = ""
    ipb_pass_hash: str = ""
    igneous: str = ""
    sk: str = ""

    # Display settings
    show_japanese_title: bool = True
    debug_mode: bool = False

    # Search settings
    search_bulk_mode_default: bool = False
    search_open_result_website_automatically: bool = False
    search_open_gallery_website_onclick: bool = False
    search_download_gallery_onclick: bool = False
    search_no_sub_menu: bool = False
    search_auto_detect_search_keyword: bool = True

    # Sorting settings
    auto_sort: str = "off"  # auto, artist, keyword, off
    sort_by_keyword_keywords: str = ""
    auto_sort_artist_priority: int = 10
    auto_sort_keyword_priority: int = 20

    # Filter settings
    anti_ai: bool = False
    filter_keyword_filter: str = ""

    # Page download settings (last used values)
    page_download_fetch_mode: str = "current"  # iter, current, range
    page_download_start_page: int = 1
    page_download_end_page: int = 1
    page_download_mode: str = "auto"  # auto, ask, direct
    page_download_max_galleries: int = 0
    page_download_max_size_mb: float = 0.0
    page_download_keyword_filter: str = ""
    page_download_dir: str = ""

    @property
    def auto_select_best(self) -> bool:
        """Legacy compatibility for older config callers."""
        return self.default_download_mode == "auto"

    @auto_select_best.setter
    def auto_select_best(self, value: bool) -> None:
        self.default_download_mode = "auto" if value else "ask"

    @property
    def has_exhentai_cookies(self) -> bool:
        return bool(self.ipb_member_id and self.ipb_pass_hash and self.igneous)

    def get_cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        if self.ipb_member_id:
            cookies["ipb_member_id"] = self.ipb_member_id
        if self.ipb_pass_hash:
            cookies["ipb_pass_hash"] = self.ipb_pass_hash
        if self.igneous:
            cookies["igneous"] = self.igneous
        if self.sk:
            cookies["sk"] = self.sk
        return cookies

    def save(self, path: str | Path | None = None) -> None:
        """Save current config to a TOML file."""
        save_path = Path(path) if path else Path.cwd() / CONFIG_FILENAME
        save_path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            "# CLI-Eh-Downloader Configuration\n",
            "\n[download]\n",
            f"download_dir = {_toml_string(self.download_dir)}\n",
            f"max_parallel = {self.max_parallel}\n",
            f"rate_limit_delay = {self.rate_limit_delay}\n",
            f"retry_count = {self.retry_count}\n",
            f"retry_delay = {self.retry_delay}\n",
            f"prefer_torrent = {'true' if self.prefer_torrent else 'false'}\n",
            f"default_download_mode = {_toml_string(_normalize_download_mode(self.default_download_mode))}\n",
            f"fast_queue = {'true' if self.fast_queue else 'false'}\n",
            "\n[cookies]\n",
            f"ipb_member_id = {_toml_string(self.ipb_member_id)}\n",
            f"ipb_pass_hash = {_toml_string(self.ipb_pass_hash)}\n",
            f"igneous = {_toml_string(self.igneous)}\n",
            f"sk = {_toml_string(self.sk)}\n",
            "\n[display]\n",
            f"show_japanese_title = {'true' if self.show_japanese_title else 'false'}\n",
            f"debug_mode = {'true' if self.debug_mode else 'false'}\n",
            "\n[search]\n",
            f"bulk_mode_default = {'true' if self.search_bulk_mode_default else 'false'}\n",
            f"open_result_website_automatically = {'true' if self.search_open_result_website_automatically else 'false'}\n",
            f"open_gallery_website_onclick = {'true' if self.search_open_gallery_website_onclick else 'false'}\n",
            f"download_gallery_onclick = {'true' if self.search_download_gallery_onclick else 'false'}\n",
            f"no_sub_menu = {'true' if self.search_no_sub_menu else 'false'}\n",
            f"auto_detect_search_keyword = {'true' if self.search_auto_detect_search_keyword else 'false'}\n",
            "\n[sorting]\n",
            f"auto_sort = {_toml_string(_normalize_auto_sort(self.auto_sort))}\n",
            f"sort_by_keyword_keywords = {_toml_string(self.sort_by_keyword_keywords)}\n",
            f"auto_sort_artist_priority = {self.auto_sort_artist_priority}\n",
            f"auto_sort_keyword_priority = {self.auto_sort_keyword_priority}\n",
            "\n[filter]\n",
            f"anti_ai = {'true' if self.anti_ai else 'false'}\n",
            f"keyword_filter = {_toml_string(self.filter_keyword_filter)}\n",
            "\n[page_download]\n",
            f"fetch_mode = {_toml_string(_normalize_page_fetch_mode(self.page_download_fetch_mode))}\n",
            f"start_page = {self.page_download_start_page}\n",
            f"end_page = {self.page_download_end_page}\n",
            f"download_mode = {_toml_string(_normalize_download_mode(self.page_download_mode))}\n",
            f"max_galleries = {self.page_download_max_galleries}\n",
            f"max_size_mb = {self.page_download_max_size_mb}\n",
            f"keyword_filter = {_toml_string(self.page_download_keyword_filter)}\n",
            f"download_dir = {_toml_string(self.page_download_dir)}\n",
        ]
        save_path.write_text("".join(lines), encoding="utf-8")


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from a TOML file and create/backfill it when needed."""
    config = Config()

    config_path: Path | None = None
    if path:
        config_path = Path(path)
    else:
        for p in DEFAULT_CONFIG_PATHS:
            if p.exists():
                config_path = p
                break
        if config_path is None:
            config_path = DEFAULT_CONFIG_PATHS[0]

    should_save = False
    if config_path and config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        _apply_config(config, data)
        should_save = _config_has_missing_fields(data)
    else:
        should_save = True

    if config_path and should_save:
        config.save(config_path)

    return config


def _config_has_missing_fields(data: dict[str, Any]) -> bool:
    required: dict[str, tuple[str, ...]] = {
        "download": (
            "download_dir",
            "max_parallel",
            "rate_limit_delay",
            "retry_count",
            "retry_delay",
            "prefer_torrent",
            "default_download_mode",
            "fast_queue",
        ),
        "cookies": ("ipb_member_id", "ipb_pass_hash", "igneous", "sk"),
        "display": ("show_japanese_title", "debug_mode"),
        "search": (
            "bulk_mode_default",
            "open_result_website_automatically",
            "open_gallery_website_onclick",
            "download_gallery_onclick",
            "no_sub_menu",
            "auto_detect_search_keyword",
        ),
        "sorting": (
            "auto_sort",
            "sort_by_keyword_keywords",
            "auto_sort_artist_priority",
            "auto_sort_keyword_priority",
        ),
        "filter": ("anti_ai", "keyword_filter"),
        "page_download": (
            "fetch_mode",
            "start_page",
            "end_page",
            "download_mode",
            "max_galleries",
            "max_size_mb",
            "keyword_filter",
            "download_dir",
        ),
    }

    for section, keys in required.items():
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            return True
        for key in keys:
            if key not in section_data:
                return True
    return False


def _apply_config(config: Config, data: dict[str, Any]) -> None:
    """Apply parsed TOML data to a Config instance."""
    dl = data.get("download", {})
    if "download_dir" in dl:
        config.download_dir = str(dl["download_dir"])
    if "max_parallel" in dl:
        config.max_parallel = int(dl["max_parallel"])
    if "rate_limit_delay" in dl:
        config.rate_limit_delay = float(dl["rate_limit_delay"])
    if "retry_count" in dl:
        config.retry_count = int(dl["retry_count"])
    if "retry_delay" in dl:
        config.retry_delay = float(dl["retry_delay"])
    if "prefer_torrent" in dl:
        config.prefer_torrent = bool(dl["prefer_torrent"])
    if "default_download_mode" in dl:
        config.default_download_mode = _normalize_download_mode(str(dl["default_download_mode"]))
    elif "auto_select_best" in dl:
        config.default_download_mode = "auto" if bool(dl["auto_select_best"]) else "ask"
    if "fast_queue" in dl:
        config.fast_queue = bool(dl["fast_queue"])

    cookies = data.get("cookies", {})
    if "ipb_member_id" in cookies:
        config.ipb_member_id = str(cookies["ipb_member_id"])
    if "ipb_pass_hash" in cookies:
        config.ipb_pass_hash = str(cookies["ipb_pass_hash"])
    if "igneous" in cookies:
        config.igneous = str(cookies["igneous"])
    if "sk" in cookies:
        config.sk = str(cookies["sk"])

    display = data.get("display", {})
    if "show_japanese_title" in display:
        config.show_japanese_title = bool(display["show_japanese_title"])
    if "debug_mode" in display:
        config.debug_mode = bool(display["debug_mode"])

    search = data.get("search", {})
    if "bulk_mode_default" in search:
        config.search_bulk_mode_default = bool(search["bulk_mode_default"])
    if "open_result_website_automatically" in search:
        config.search_open_result_website_automatically = bool(search["open_result_website_automatically"])
    if "open_gallery_website_onclick" in search:
        config.search_open_gallery_website_onclick = bool(search["open_gallery_website_onclick"])
    if "download_gallery_onclick" in search:
        config.search_download_gallery_onclick = bool(search["download_gallery_onclick"])
    if "no_sub_menu" in search:
        config.search_no_sub_menu = bool(search["no_sub_menu"])
    if "auto_detect_search_keyword" in search:
        config.search_auto_detect_search_keyword = bool(search["auto_detect_search_keyword"])

    sorting = data.get("sorting", {})
    if "auto_sort" in sorting:
        config.auto_sort = _normalize_auto_sort(str(sorting["auto_sort"]))
    if "sort_by_keyword_keywords" in sorting:
        config.sort_by_keyword_keywords = str(sorting["sort_by_keyword_keywords"])
    elif "keywords" in sorting:
        config.sort_by_keyword_keywords = str(sorting["keywords"])
    if "auto_sort_artist_priority" in sorting:
        config.auto_sort_artist_priority = int(sorting["auto_sort_artist_priority"])
    if "auto_sort_keyword_priority" in sorting:
        config.auto_sort_keyword_priority = int(sorting["auto_sort_keyword_priority"])

    filters = data.get("filter", {})
    if "anti_ai" in filters:
        config.anti_ai = bool(filters["anti_ai"])
    if "keyword_filter" in filters:
        config.filter_keyword_filter = str(filters["keyword_filter"])

    page_download = data.get("page_download", {})
    if "fetch_mode" in page_download:
        config.page_download_fetch_mode = _normalize_page_fetch_mode(str(page_download["fetch_mode"]))
    if "start_page" in page_download:
        config.page_download_start_page = max(1, int(page_download["start_page"]))
    if "end_page" in page_download:
        config.page_download_end_page = max(1, int(page_download["end_page"]))
    if "download_mode" in page_download:
        config.page_download_mode = _normalize_download_mode(str(page_download["download_mode"]))
    if "max_galleries" in page_download:
        config.page_download_max_galleries = max(0, int(page_download["max_galleries"]))
    if "max_size_mb" in page_download:
        config.page_download_max_size_mb = max(0.0, float(page_download["max_size_mb"]))
    if "keyword_filter" in page_download:
        config.page_download_keyword_filter = str(page_download["keyword_filter"])
    if "download_dir" in page_download:
        config.page_download_dir = str(page_download["download_dir"])


def _normalize_download_mode(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "auto": "auto",
        "ask": "ask",
        "manual": "ask",
        "direct": "direct",
        "direct_download": "direct",
    }
    return aliases.get(normalized, "auto")


def _normalize_auto_sort(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "auto": "auto",
        "artist": "artist",
        "sort_by_artist": "artist",
        "keyword": "keyword",
        "sort_by_keyword": "keyword",
        "off": "off",
        "none": "off",
        "false": "off",
        "disabled": "off",
    }
    return aliases.get(normalized, "off")


def _normalize_page_fetch_mode(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "iter": "iter",
        "all": "iter",
        "all_pages": "iter",
        "current": "current",
        "current_page": "current",
        "range": "range",
        "custom": "range",
        "custom_range": "range",
    }
    return aliases.get(normalized, "current")


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'
