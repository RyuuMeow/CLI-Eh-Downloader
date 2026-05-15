"""External torrent client detection and launch helpers."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import Config


QBITTORRENT_COMMAND_TEMPLATE = '"{exe}" --save-path="{download_dir}" "{torrent_path}"'
GENERIC_COMMAND_TEMPLATE = '"{exe}" "{torrent_path}"'


def open_torrent_external(
    torrent_path: str,
    download_dir: str,
    config: Config,
    *,
    save_detected: bool = True,
) -> str:
    """Open a .torrent file with the configured or detected external client."""
    target_dir = str(Path(download_dir).resolve())
    torrent_file = str(Path(torrent_path).resolve())

    exe_path, detected = _resolve_client_exe(config)
    if not exe_path:
        return f"No torrent client found; saved .torrent at {torrent_path}."

    template = config.torrent_command_template.strip() or _default_template_for_exe(exe_path)
    command = _render_command(template, exe_path, torrent_file, target_dir)

    try:
        if os.name == "nt":
            subprocess.Popen(command)
        else:
            import shlex

            subprocess.Popen(shlex.split(command))
    except OSError as e:
        return f"Could not open torrent client: {e}; saved .torrent at {torrent_path}."

    remembered = False
    if detected and save_detected:
        _remember_detected_client(config, exe_path, template)
        remembered = True

    if remembered:
        return f"Opened with {Path(exe_path).name}; saved detected torrent client settings."
    return f"Opened with {Path(exe_path).name}."


def is_torrent_client_open_success(message: str) -> bool:
    return message.startswith("Opened with")


def ensure_torrent_client_settings(config: Config, *, save_detected: bool = True) -> bool:
    exe_path, detected = _resolve_client_exe(config)
    if not exe_path:
        return False
    if detected and save_detected:
        _remember_detected_client(config, exe_path, _default_template_for_exe(exe_path))
    return True


def _resolve_client_exe(config: Config) -> tuple[str, bool]:
    configured = config.torrent_client_exe_path.strip()
    if configured:
        return configured, False

    qbt = shutil.which("qbittorrent")
    if qbt:
        return qbt, True

    default_client = _detect_default_torrent_client()
    if default_client:
        return default_client, True

    return "", False


def _remember_detected_client(config: Config, exe_path: str, template: str) -> None:
    config.torrent_client_exe_path = exe_path
    config.torrent_command_template = template
    try:
        config.save()
    except OSError:
        pass


def _default_template_for_exe(exe_path: str) -> str:
    exe_name = Path(exe_path).name.lower()
    if "qbittorrent" in exe_name:
        return QBITTORRENT_COMMAND_TEMPLATE
    return GENERIC_COMMAND_TEMPLATE


def _render_command(template: str, exe_path: str, torrent_path: str, download_dir: str) -> str:
    values = {
        "exe": exe_path,
        "torrent_path": torrent_path,
        "download_dir": download_dir,
    }
    try:
        return template.format(**values)
    except KeyError:
        return GENERIC_COMMAND_TEMPLATE.format(**values)


def _detect_default_torrent_client() -> str:
    if os.name != "nt":
        return ""

    try:
        import winreg
    except ImportError:
        return ""

    prog_ids: list[str] = []

    user_choice = _read_registry_value(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.torrent\UserChoice",
        "ProgId",
    )
    if user_choice:
        prog_ids.append(user_choice)

    default_prog_id = _read_registry_value(winreg.HKEY_CLASSES_ROOT, r".torrent", "")
    if default_prog_id:
        prog_ids.append(default_prog_id)

    prog_ids.extend(_read_registry_value_names(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.torrent\OpenWithProgids",
    ))
    prog_ids.extend(_read_registry_value_names(
        winreg.HKEY_CLASSES_ROOT,
        r".torrent\OpenWithProgids",
    ))
    prog_ids.extend(_find_registered_torrent_prog_ids(winreg))

    seen: set[str] = set()
    candidates: list[str] = []
    for prog_id in prog_ids:
        if not prog_id or prog_id in seen:
            continue
        seen.add(prog_id)
        command = _read_registry_value(
            winreg.HKEY_CLASSES_ROOT,
            rf"{prog_id}\shell\open\command",
            "",
        )
        exe = _extract_exe_from_open_command(command)
        if exe:
            candidates.append(exe)

    qbt = next((exe for exe in candidates if "qbittorrent" in Path(exe).name.lower()), "")
    if qbt:
        return qbt
    if candidates:
        return candidates[0]

    return ""


def _read_registry_value(root, key_path: str, value_name: str) -> str:
    try:
        import winreg

        with winreg.OpenKey(root, key_path) as key:
            value, _value_type = winreg.QueryValueEx(key, value_name)
            return str(value)
    except OSError:
        return ""


def _read_registry_value_names(root, key_path: str) -> list[str]:
    try:
        import winreg

        names: list[str] = []
        with winreg.OpenKey(root, key_path) as key:
            index = 0
            while True:
                try:
                    name, _value, _value_type = winreg.EnumValue(key, index)
                except OSError:
                    break
                if name:
                    names.append(str(name))
                index += 1
            return names
    except OSError:
        return []


def _find_registered_torrent_prog_ids(winreg_module) -> list[str]:
    common = [
        "qBittorrent.File.Torrent",
        "Transmission.torrent",
        "Deluge.Torrent",
        "uTorrent",
        "BitTorrent",
        "BaiduYunGuanjia.torrent",
    ]
    prog_ids = [prog_id for prog_id in common if _registry_key_exists(winreg_module, prog_id)]

    try:
        with winreg_module.OpenKey(winreg_module.HKEY_CLASSES_ROOT, "") as root:
            index = 0
            while True:
                try:
                    name = winreg_module.EnumKey(root, index)
                except OSError:
                    break
                if "torrent" in name.lower() and _registry_key_exists(winreg_module, name):
                    prog_ids.append(name)
                index += 1
    except OSError:
        pass

    return prog_ids


def _registry_key_exists(winreg_module, key_path: str) -> bool:
    try:
        with winreg_module.OpenKey(winreg_module.HKEY_CLASSES_ROOT, key_path):
            return True
    except OSError:
        return False


def _extract_exe_from_open_command(command: str) -> str:
    if not command:
        return ""

    expanded = os.path.expandvars(command.strip())
    quoted = re.match(r'^"([^"]+\.exe)"', expanded, flags=re.IGNORECASE)
    if quoted:
        exe = quoted.group(1)
        return exe if Path(exe).exists() else ""

    unquoted = re.match(r"^(.+?\.exe)(?:\s|$)", expanded, flags=re.IGNORECASE)
    if unquoted:
        exe = unquoted.group(1)
        return exe if Path(exe).exists() else ""

    return ""
