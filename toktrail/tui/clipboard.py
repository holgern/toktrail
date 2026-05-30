from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def export_dir() -> Path:
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home).expanduser() / "toktrail" / "exports"
    return Path.home() / ".cache" / "toktrail" / "exports"


def copy_fallback_path() -> Path:
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home).expanduser() / "toktrail" / "tui-last-view.txt"
    return Path.home() / ".cache" / "toktrail" / "tui-last-view.txt"


def copy_to_clipboard(text: str) -> bool:
    if _run_clipboard(["termux-clipboard-set"], text):
        return True
    wayland = os.environ.get("WAYLAND_DISPLAY")
    display = os.environ.get("DISPLAY")
    if wayland and _run_clipboard(["wl-copy"], text):
        return True
    if display and _run_clipboard(["xclip", "-selection", "clipboard"], text):
        return True
    if display and _run_clipboard(["xsel", "--clipboard", "--input"], text):
        return True
    if _run_clipboard(["pbcopy"], text):
        return True
    if _run_clipboard(["clip.exe"], text):
        return True
    return _run_clipboard(
        ["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard"],
        text,
    )


def _run_clipboard(command: list[str], text: str) -> bool:
    executable = command[0]
    if shutil.which(executable) is None:
        return False
    try:
        subprocess.run(
            command,
            input=text,
            text=True,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return False
    return True
