"""Build launch commands and read .desktop applications."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from onigiri.model import Tile

_FIELD_CODE = re.compile(r"%[fFuUdDnNickvm]")
_TERMINALS = {"alacritty", "kitty", "konsole", "xterm"}

# Konsole stores background opacity on the color scheme, not a profile property.
# Breeze colors, with Opacity filled in when a tile is translucent.
_KONSOLE_SCHEME = """\
[Background]
Color=35,38,39

[BackgroundFaint]
Color=49,54,59

[BackgroundIntense]
Color=0,0,0

[Color0]
Color=35,38,39

[Color0Faint]
Color=49,54,59

[Color0Intense]
Color=127,140,141

[Color1]
Color=237,21,21

[Color1Faint]
Color=120,50,40

[Color1Intense]
Color=192,57,43

[Color2]
Color=17,209,22

[Color2Faint]
Color=23,162,98

[Color2Intense]
Color=28,220,154

[Color3]
Color=246,116,0

[Color3Faint]
Color=182,86,25

[Color3Intense]
Color=253,188,75

[Color4]
Color=29,153,243

[Color4Faint]
Color=27,102,143

[Color4Intense]
Color=61,174,233

[Color5]
Color=155,89,182

[Color5Faint]
Color=97,74,115

[Color5Intense]
Color=142,68,173

[Color6]
Color=26,188,156

[Color6Faint]
Color=24,108,96

[Color6Intense]
Color=22,160,133

[Color7]
Color=252,252,252

[Color7Faint]
Color=99,104,109

[Color7Intense]
Color=255,255,255

[Foreground]
Color=252,252,252

[ForegroundFaint]
Color=239,240,241

[ForegroundIntense]
Color=61,174,233

[General]
Description=Onigiri
Opacity={opacity:.2f}
Wallpaper=
"""


@dataclass(frozen=True)
class DesktopApp:
    app_id: str
    name: str
    command: str
    wm_class: str
    icon: str = ""


@dataclass(frozen=True)
class ResolvedTile:
    command: str
    match_kind: str
    match_value: str
    use_shell: bool
    warning: str


def unique_class(profile: str, tile_id: str, *, overlay: bool = False) -> str:
    prefix = "onigiri-ov" if overlay else "onigiri"
    raw = f"{prefix}-{profile}-{tile_id}".lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", raw)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return (cleaned or "onigiri-tile")[:128]


def terminal_name(command: str = "", terminal: str = "") -> str:
    """Return a known terminal binary name, or empty if this is a normal app."""

    exe = exec_basename(command).casefold()
    if exe in _TERMINALS:
        return exe
    name = (terminal or "").casefold()
    if name in _TERMINALS:
        return name
    return ""


def uses_private_window(command: str) -> bool:
    """True when the command already sets a unique class or title for KWin."""

    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    flags = {part.casefold() for part in parts}
    return bool(flags & {"--class", "-class", "--qwindowtitle", "--name"})


def assign_app(tile: Tile, app: DesktopApp) -> Tile:
    """Point a tile at an installed app. Window matching is chosen automatically."""

    tile.app_id = app.app_id
    tile.app_name = app.name
    tile.name = app.name
    program = terminal_name(app.command)
    if program:
        tile.launch_mode = "helper"
        tile.terminal = program
        tile.command = ""
        tile.shell_command = ""
        tile.match_kind = ""
        tile.match_value = ""
        return tile
    tile.launch_mode = "app"
    tile.command = app.command
    tile.shell_command = ""
    tile.match_kind = "class" if app.wm_class else ""
    tile.match_value = app.wm_class
    return tile


def clean_exec(exec_line: str) -> str:
    """Drop desktop field codes and collapse leftover whitespace."""

    placeholder = "\x00"
    text = exec_line.replace("%%", placeholder)
    text = _FIELD_CODE.sub("", text)
    text = text.replace(placeholder, "%")
    return " ".join(text.split())


def exec_basename(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if parts and parts[0] == "env":
        parts = parts[1:]
        while parts and "=" in parts[0] and not parts[0].startswith("-"):
            parts = parts[1:]
    if not parts:
        return ""
    return Path(parts[0]).name


def _konsole_font(points: int) -> str:
    """QFont string Konsole accepts. Size-only is not a profile property."""

    return f"Monospace,{points},-1,5,400,0,0,0,0,0,0,0,0,0,0,1"


def _konsole_data_dir() -> Path:
    root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return root / "konsole"


def ensure_konsole_color_scheme(opacity: float) -> str:
    """Write a Breeze-based scheme with this background opacity. Return its name."""

    percent = int(round(min(1.0, max(0.1, float(opacity))) * 100))
    name = f"onigiri-{percent:02d}"
    contents = _KONSOLE_SCHEME.format(opacity=percent / 100.0)
    path = _konsole_data_dir() / f"{name}.colorscheme"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != contents:
            path.write_text(contents, encoding="utf-8")
    except OSError:
        pass
    return name


def terminal_command(
    terminal: str,
    cls: str,
    title: str,
    shell_cmd: str,
    *,
    opacity: float = 1.0,
    show_title: bool = True,
    hide_toolbar: bool = False,
    font_size: int = 0,
) -> tuple[str, str, str]:
    """Return (command, match kind, match value).

    Alacritty, Kitty, and Xterm get a unique window class. Konsole has no
    class flag, so it is matched by a unique window title and desktop file.
    Opacity changes the terminal background only. 1 means fully opaque, and
    the flag is left off. Xterm has no background-opacity flag.
    """

    program = terminal if terminal in _TERMINALS else "alacritty"
    visible = title.replace("\n", " ").strip() or cls
    inner = f"{shell_cmd.strip()}; exec $SHELL" if shell_cmd.strip() else ""
    try:
        level = float(opacity)
    except (TypeError, ValueError):
        level = 1.0
    if level != level:
        level = 1.0
    level = min(1.0, max(0.1, level))
    translucent = level < 0.999
    try:
        points = int(font_size)
    except (TypeError, ValueError):
        points = 0
    if points < 0:
        points = 0
    elif 0 < points < 6:
        points = 6
    points = min(48, points)
    if program == "konsole":
        # Konsole cannot set --class. --desktopfile becomes the Wayland app id
        # KWin sees, and the tab-title properties stop the shell from replacing
        # --qwindowtitle so title rules still match.
        args = [
            "konsole",
            "--separate",
            "--desktopfile",
            cls,
            "--qwindowtitle",
            cls,
            "-p",
            f"LocalTabTitleFormat={cls}",
            "-p",
            f"RemoteTabTitleFormat={cls}",
            "-p",
            f"tabtitle={cls}",
        ]
        if hide_toolbar:
            args.append("--hide-toolbars")
        if translucent:
            scheme = ensure_konsole_color_scheme(level)
            args += ["-p", f"ColorScheme={scheme}"]
        if points:
            args += ["-p", f"Font={_konsole_font(points)}"]
        if inner:
            args += ["-e", "bash", "-lc", inner]
        return shlex.join(args), "title", cls
    if program == "xterm":
        args = ["xterm", "-class", cls, "-name", cls]
        if show_title:
            args += ["-T", visible]
        if points:
            args += ["-fa", "Monospace", "-fs", str(points)]
        if inner:
            args += ["-e", "bash", "-lc", inner]
        return shlex.join(args), "class", cls
    # Alacritty's second --class field is the X11 instance. Without it, resourceName
    # stays "alacritty" and a layout rule for Alacritty also matches overlay windows.
    if program == "alacritty":
        # Unique class plus no IPC socket: otherwise later spawns attach to the
        # first Alacritty and open a spare window with class "Alacritty".
        args = [program, "--class", f"{cls},{cls}", "-o", "ipc_socket=false"]
    else:
        args = [program, "--class", cls, "--name", cls]
    if show_title:
        args += ["--title", visible]
    if translucent and program == "alacritty":
        args += ["-o", f"window.opacity={level:.2f}"]
    elif translucent and program == "kitty":
        args += ["--override", f"background_opacity={level:.2f}"]
    if points and program == "alacritty":
        args += ["-o", f"font.size={points}"]
    elif points and program == "kitty":
        args += ["--override", f"font_size={points}"]
    if inner:
        if program == "alacritty":
            args += ["-e", "bash", "-lc", inner]
        else:
            args += ["bash", "-lc", inner]
    return shlex.join(args), "class", cls


def resolve_tile(tile: Tile, profile_name: str, *, overlay: bool = False) -> ResolvedTile:
    program = ""
    inner = ""
    if tile.launch_mode == "helper":
        program = terminal_name("", tile.terminal) or "alacritty"
        inner = tile.shell_command
    else:
        program = terminal_name(tile.command)
    if program:
        cls = unique_class(profile_name, tile.id, overlay=overlay)
        helper = tile.launch_mode == "helper"
        command, kind, value = terminal_command(
            program,
            cls,
            tile.name or cls,
            inner,
            opacity=tile.opacity if helper else 1.0,
            show_title=tile.show_title if helper else True,
            hide_toolbar=tile.hide_toolbar if helper else False,
            font_size=tile.font_size if helper else 0,
        )
        warning = ""
        if program == "konsole":
            warning = ""
        elif program == "xterm" and helper and tile.opacity < 0.999:
            warning = "Xterm cannot make its background transparent."
        return ResolvedTile(command, kind, value, False, warning)
    if tile.launch_mode == "app":
        warning = ""
        if not tile.command.strip():
            warning = "Choose an application."
        elif not tile.match_value.strip():
            warning = "This application has no window class, so Onigiri will not write a placement rule for it."
        kind = tile.match_kind if tile.match_value.strip() else ""
        return ResolvedTile(tile.command.strip(), kind, tile.match_value.strip(), False, warning)
    kind = tile.match_kind if tile.match_value.strip() else ""
    return ResolvedTile(tile.command.strip(), kind, tile.match_value.strip(), True, "")


def duplicate_classes(tiles: list[Tile], profile_name: str) -> set[str]:
    counts: dict[str, int] = {}
    for tile in tiles:
        resolved = resolve_tile(tile, profile_name)
        if resolved.match_kind == "class" and resolved.match_value:
            counts[resolved.match_value] = counts.get(resolved.match_value, 0) + 1
    return {value for value, count in counts.items() if count > 1}


def application_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    dirs = [home / "applications"]
    xdg = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    for part in xdg.split(":"):
        if part:
            dirs.append(Path(part) / "applications")
    return dirs


def _parse_desktop(path: Path) -> DesktopApp | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    group = ""
    keys: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if group == "Desktop Entry":
                break
            group = stripped[1:-1]
            continue
        if group != "Desktop Entry" or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        keys.setdefault(key.strip(), value.strip())
    if keys.get("Hidden", "").lower() == "true":
        return None
    if keys.get("NoDisplay", "").lower() == "true":
        return None
    if keys.get("Type", "Application") != "Application":
        return None
    exec_raw = keys.get("Exec", "")
    if not exec_raw:
        return None
    command = clean_exec(exec_raw)
    wm_class = keys.get("StartupWMClass", "").strip() or exec_basename(command)
    return DesktopApp(
        app_id=path.name,
        name=keys.get("Name") or path.stem,
        command=command,
        wm_class=wm_class,
        icon=keys.get("Icon") or "",
    )


def load_applications() -> list[DesktopApp]:
    found: dict[str, DesktopApp] = {}
    for directory in application_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            if path.name in found:
                continue
            app = _parse_desktop(path)
            if app is not None:
                found[path.name] = app
    return sorted(found.values(), key=lambda app: app.name.casefold())


_WRAPPER_PROCESS = {
    "bwrap",
    "env",
    "firejail",
    "flatpak",
    "gamemoderun",
    "gtk-launch",
    "snap",
}

_SKIP_PROCESS = {
    "bash",
    "dbus-daemon",
    "fish",
    "kactivitymanagerd",
    "kded5",
    "kded6",
    "kglobalacceld",
    "ksmserver",
    "kwin",
    "kwin_wayland",
    "kwin_x11",
    "onigiri",
    "pipewire",
    "plasmashell",
    "python",
    "python3",
    "sh",
    "systemd",
    "wireplumber",
    "xdg-desktop-portal",
    "xdg-desktop-portal-kde",
    "zsh",
}


def process_names() -> set[str]:
    names: set[str] = set()
    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return names
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            comm = ""
        if comm:
            names.add(Path(comm).name)
        try:
            names.add(Path(os.readlink(entry / "exe")).name)
        except OSError:
            pass
        try:
            raw = (entry / "cmdline").read_bytes().split(b"\x00")[0].decode("utf-8", "ignore")
        except OSError:
            raw = ""
        if raw:
            names.add(Path(raw.split()[0]).name)
    return {name for name in names if name and name.casefold() not in _SKIP_PROCESS}


def command_is_running(command: str, match_value: str = "", names: set[str] | None = None) -> bool:
    """True when a process that belongs to this app is already alive."""

    keys = {name.casefold() for name in (process_names() if names is None else names)}
    wm = match_value.strip().casefold()
    if wm and wm in keys:
        return True
    exe = exec_basename(command).casefold()
    return bool(exe) and exe not in _WRAPPER_PROCESS and exe in keys


def running_applications(installed: list[DesktopApp], names: set[str] | None = None) -> list[DesktopApp]:
    keys = {name.casefold() for name in (process_names() if names is None else names)}
    found: list[DesktopApp] = []
    seen: set[str] = set()
    for app in installed:
        exe = exec_basename(app.command).casefold()
        wm = app.wm_class.casefold()
        if exe not in keys and wm not in keys:
            continue
        if app.app_id in seen:
            continue
        seen.add(app.app_id)
        found.append(app)
    return found


def desktop_path(app_id: str) -> Path | None:
    name = app_id.strip()
    if not name:
        return None
    if not name.endswith(".desktop"):
        name = name + ".desktop"
    for directory in application_dirs():
        path = directory / name
        if path.is_file():
            return path
    return None


def spawn(command: str, use_shell: bool) -> None:
    if not command.strip():
        return
    if use_shell:
        subprocess.Popen(command, shell=True, start_new_session=True)
        return
    parts = shlex.split(command)
    env = None
    if parts and Path(parts[0]).name.casefold() == "alacritty":
        env = os.environ.copy()
        env.pop("ALACRITTY_SOCKET", None)
    subprocess.Popen(parts, start_new_session=True, env=env)


def activate_app(command: str, use_shell: bool, app_id: str = "") -> None:
    """Raise an existing instance if possible, otherwise start the app."""

    if uses_private_window(command):
        spawn(command, use_shell)
        return
    path = desktop_path(app_id)
    if path is not None:
        for args in (["gio", "launch", str(path)], ["gtk-launch", path.stem]):
            try:
                subprocess.Popen(args, start_new_session=True)
                return
            except FileNotFoundError:
                continue
    spawn(command, use_shell)
