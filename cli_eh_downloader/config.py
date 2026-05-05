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
            f'download_dir = "{self.download_dir}"\n',
            f"max_parallel = {self.max_parallel}\n",
            f"rate_limit_delay = {self.rate_limit_delay}\n",
            f"retry_count = {self.retry_count}\n",
            f"retry_delay = {self.retry_delay}\n",
            f"prefer_torrent = {'true' if self.prefer_torrent else 'false'}\n",
            f'default_download_mode = "{self.default_download_mode}"\n',
            "\n[cookies]\n",
            f'ipb_member_id = "{self.ipb_member_id}"\n',
            f'ipb_pass_hash = "{self.ipb_pass_hash}"\n',
            f'igneous = "{self.igneous}"\n',
            f'sk = "{self.sk}"\n',
            "\n[display]\n",
            f"show_japanese_title = {'true' if self.show_japanese_title else 'false'}\n",
            f"debug_mode = {'true' if self.debug_mode else 'false'}\n",
            "\n[search]\n",
            f"bulk_mode_default = {'true' if self.search_bulk_mode_default else 'false'}\n",
            f"open_result_website_automatically = {'true' if self.search_open_result_website_automatically else 'false'}\n",
            f"open_gallery_website_onclick = {'true' if self.search_open_gallery_website_onclick else 'false'}\n",
            f"download_gallery_onclick = {'true' if self.search_download_gallery_onclick else 'false'}\n",
            f"no_sub_menu = {'true' if self.search_no_sub_menu else 'false'}\n",
        ]
        save_path.write_text("".join(lines), encoding="utf-8")


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from a TOML file. Falls back to defaults."""
    config = Config()

    config_path: Path | None = None
    if path:
        config_path = Path(path)
    else:
        for p in DEFAULT_CONFIG_PATHS:
            if p.exists():
                config_path = p
                break

    if config_path and config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        _apply_config(config, data)

    return config


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
