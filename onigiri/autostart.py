"""Write a valid XDG autostart entry for one profile."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def autostart_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "onigiri.desktop"


def quote_desktop_arg(value: str) -> str:
    cleaned = value.replace('"', "'").replace("\n", " ").strip()
    if not cleaned or any(char.isspace() for char in cleaned) or any(char in cleaned for char in "\\$`"):
        return f'"{cleaned}"'
    return cleaned


def installed_launcher() -> Path | None:
    home_bin = Path.home() / ".local" / "bin"
    for name in ("onigiri2", "onigiri"):
        candidate = home_bin / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("onigiri2") or shutil.which("onigiri")
    return Path(found) if found else None


def relaunch_exec(profile: str) -> str:
    arg = quote_desktop_arg(profile)
    launcher = installed_launcher()
    if launcher is not None:
        return f"{quote_desktop_arg(str(launcher))} --profile {arg} --hidden"
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.name == "onigiri" and os.access(argv0, os.X_OK):
        return f"{quote_desktop_arg(str(argv0))} --profile {arg} --hidden"
    root = Path(__file__).resolve().parent.parent
    python = quote_desktop_arg(sys.executable)
    return f"env PYTHONPATH={quote_desktop_arg(str(root))} {python} -m onigiri --profile {arg} --hidden"


def autostart_text(profile: str, exec_line: str) -> str:
    comment_name = " ".join(profile.split()) or "profile"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Onigiri",
        f"Comment=Start Onigiri in the tray, apply {comment_name}, and launch its tiles",
        f"Exec={exec_line}",
        "Icon=onigiri",
        "Terminal=false",
        "StartupNotify=false",
        "X-GNOME-Autostart-enabled=true",
        "X-KDE-autostart-after=panel",
        "X-KDE-autostart-phase=2",
    ]
    return "\n".join(lines) + "\n"


def write_autostart(profile: str, path: Path | None = None) -> Path:
    destination = path or autostart_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(autostart_text(profile, relaunch_exec(profile)), encoding="utf-8")
    return destination
