"""Read the Plasma wallpaper already configured for each screen."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from onigiri.model import Rgb, pan_pair

_DESKTOP_PLUGINS = {"org.kde.desktopcontainment", "org.kde.plasma.folder"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".svg"}
# Qt Image.FillMode values used by the Plasma image wallpaper.
_FILL_MODES = {0: "stretch", 1: "fit", 2: "crop", 6: "center"}
_FILL_IDS = {name: number for number, name in _FILL_MODES.items()}

_SCREEN_SCRIPT = r"""
var lines = [];
var list = desktops();
for (var i = 0; i < list.length; i++) {
  var d = list[i];
  var g = screenGeometry(d.screen);
  lines.push([d.screen, g.x, g.y, g.width, g.height].join("\t"));
}
print(lines.join("\n"));
"""


@dataclass(frozen=True)
class ScreenWallpaper:
    screen: int
    path: str
    fill: str
    desktop: bool


def plasma_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "plasma-org.kde.plasma.desktop-appletsrc"


def read_desktop_wallpapers(text: str) -> list[ScreenWallpaper]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(text)
    found: list[ScreenWallpaper] = []
    for section in parser.sections():
        containment = _containment_id(section)
        if containment is None:
            continue
        plugin = parser.get(section, "plugin", fallback="")
        if plugin not in _DESKTOP_PLUGINS:
            continue
        try:
            screen = parser.getint(section, "lastScreen")
        except (ValueError, configparser.Error):
            continue
        wallpaper_plugin = parser.get(section, "wallpaperplugin", fallback="org.kde.image")
        general = f"Containments][{containment}][Wallpaper][{wallpaper_plugin}][General"
        if not parser.has_section(general):
            continue
        if wallpaper_plugin == "org.kde.color":
            color = _read_color(parser[general].get("Color", ""))
            if color is not None:
                found.append(ScreenWallpaper(screen, color_hex(color), "stretch", plugin == "org.kde.desktopcontainment"))
            continue
        path = _image_from_section(parser[general])
        if not path:
            continue
        fill = _fill_mode(parser[general].get("FillMode", "2"))
        found.append(ScreenWallpaper(screen, path, fill, plugin == "org.kde.desktopcontainment"))
    return found


def parse_screen_script(output: str) -> dict[int, tuple[int, int, int, int]]:
    geometries: dict[int, tuple[int, int, int, int]] = {}
    for line in output.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 5:
            continue
        try:
            screen, x, y, width, height = (int(part) for part in parts[:5])
        except ValueError:
            continue
        if width > 0 and height > 0:
            geometries[screen] = (x, y, width, height)
    return geometries


def assign_wallpapers(
    screens: list[tuple[str, int, int, int, int]],
    wallpapers: list[ScreenWallpaper],
    geometries: dict[int, tuple[int, int, int, int]] | None,
) -> dict[str, tuple[str, str]]:
    """Map a screen name to ``(image path, fill mode)``."""
    by_screen: dict[int, ScreenWallpaper] = {}
    for wallpaper in wallpapers:
        current = by_screen.get(wallpaper.screen)
        if current is None or (wallpaper.desktop and not current.desktop):
            by_screen[wallpaper.screen] = wallpaper
    assigned: dict[str, tuple[str, str]] = {}
    if geometries:
        for name, x, y, width, height in screens:
            center_x = x + width / 2
            center_y = y + height / 2
            candidates: list[tuple[int, int, ScreenWallpaper]] = []
            for index, (left, top, span_w, span_h) in geometries.items():
                wallpaper = by_screen.get(index)
                if wallpaper is None:
                    continue
                if left <= center_x <= left + span_w and top <= center_y <= top + span_h:
                    candidates.append((0 if wallpaper.desktop else 1, span_w * span_h, wallpaper))
            if candidates:
                wallpaper = min(candidates, key=lambda item: (item[0], item[1]))[2]
                assigned[name] = (wallpaper.path, wallpaper.fill)
    if assigned or not by_screen:
        return assigned
    paths = {wallpaper.path for wallpaper in by_screen.values()}
    fills = {wallpaper.fill for wallpaper in by_screen.values()}
    if len(paths) == 1 and len(fills) == 1:
        only = next(iter(by_screen.values()))
        return {name: (only.path, only.fill) for name, *_rest in screens}
    return {}


def wallpaper_for(
    screens: list[tuple[str, int, int, int, int]],
    *,
    config_text: str | None = None,
    geometries: dict[int, tuple[int, int, int, int]] | None = None,
    config_path: Path | None = None,
    query_screens: bool = True,
) -> dict[str, tuple[str, str]]:
    if config_text is None:
        path = config_path or plasma_config_path()
        try:
            config_text = path.read_text(encoding="utf-8")
        except OSError:
            return {}
    wallpapers = read_desktop_wallpapers(config_text)
    if geometries is None and query_screens:
        geometries = plasma_screen_geometries()
    return assign_wallpapers(screens, wallpapers, geometries)


def plasma_screen_geometries() -> dict[int, tuple[int, int, int, int]]:
    output = evaluate_plasma(_SCREEN_SCRIPT, timeout=2)
    return parse_screen_script(output) if output else {}


def wallpaper_script(path: str, fill: str = "crop") -> str:
    """JS that points every Plasma desktop at this image."""

    fill_id = _FILL_IDS.get(fill, 2)
    uri = Path(path).expanduser().resolve().as_uri()
    escaped = uri.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "var list = desktops();\n"
        "for (var i = 0; i < list.length; i++) {\n"
        "  var d = list[i];\n"
        '  d.wallpaperPlugin = "org.kde.image";\n'
        '  d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];\n'
        f'  d.writeConfig("Image", "{escaped}");\n'
        f'  d.writeConfig("FillMode", {fill_id});\n'
        "}\n"
    )


def evaluate_plasma(script: str, timeout: float = 5) -> str:
    for program in ("qdbus6", "qdbus"):
        try:
            result = subprocess.run(
                [program, "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return result.stdout
    return ""


def set_wallpaper(path: str, fill: str = "crop", run=None) -> None:
    image = Path(path).expanduser()
    if not image.is_file():
        raise RuntimeError("That picture is missing.")
    runner = run or (lambda script: bool(evaluate_plasma(script)))
    if not runner(wallpaper_script(str(image), fill)):
        raise RuntimeError("Could not set the wallpaper.")


def cover_source_rect(
    image_w: int,
    image_h: int,
    screen_w: int,
    screen_h: int,
    pan_x: float = 0.5,
    pan_y: float = 0.5,
) -> tuple[int, int, int, int]:
    """Source rectangle in image pixels for a cover-crop with pan 0..1."""
    image_w = max(1, int(image_w))
    image_h = max(1, int(image_h))
    screen_w = max(1, int(screen_w))
    screen_h = max(1, int(screen_h))
    scale = max(screen_w / image_w, screen_h / image_h)
    src_w = min(float(image_w), screen_w / scale)
    src_h = min(float(image_h), screen_h / scale)
    pan_x, pan_y = pan_pair((pan_x, pan_y))
    src_x = (image_w - src_w) * pan_x
    src_y = (image_h - src_h) * pan_y
    width = max(1, min(image_w, int(round(src_w))))
    height = max(1, min(image_h, int(round(src_h))))
    x_pos = max(0, min(image_w - width, int(round(src_x))))
    y_pos = max(0, min(image_h - height, int(round(src_y))))
    return (x_pos, y_pos, width, height)


def write_cover_crop(path: str, dest: Path, width: int, height: int, pan_x: float, pan_y: float) -> None:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage

    image = QImage(path)
    if image.isNull():
        raise RuntimeError("That picture is missing.")
    x_pos, y_pos, src_w, src_h = cover_source_rect(
        image.width(), image.height(), width, height, pan_x, pan_y
    )
    cropped = image.copy(x_pos, y_pos, src_w, src_h)
    scaled = cropped.scaled(
        max(1, int(width)),
        max(1, int(height)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not scaled.save(str(dest), "PNG"):
        raise RuntimeError("Could not write the cropped wallpaper.")


def scene_wallpaper_script(targets: list[tuple[int, int, int, int, str]]) -> str:
    """Point each Plasma desktop at the crop whose geometry matches that screen."""
    payload = []
    for x_pos, y_pos, width, height, uri in targets:
        payload.append(
            {
                "x": int(x_pos),
                "y": int(y_pos),
                "w": int(width),
                "h": int(height),
                "uri": str(uri).replace("\\", "\\\\").replace("'", "\\'"),
            }
        )
    encoded = json.dumps(payload)
    return (
        f"var targets = {encoded};\n"
        "var list = desktops();\n"
        "for (var i = 0; i < list.length; i++) {\n"
        "  var d = list[i];\n"
        "  var g = screenGeometry(d.screen);\n"
        "  var best = -1;\n"
        "  var bestDist = 1e15;\n"
        "  for (var t = 0; t < targets.length; t++) {\n"
        "    var item = targets[t];\n"
        "    var dx = (g.x + g.width / 2) - (item.x + item.w / 2);\n"
        "    var dy = (g.y + g.height / 2) - (item.y + item.h / 2);\n"
        "    var dist = dx * dx + dy * dy;\n"
        "    if (dist < bestDist) { bestDist = dist; best = t; }\n"
        "  }\n"
        "  if (best < 0) { continue; }\n"
        "  var chosen = targets[best];\n"
        '  d.wallpaperPlugin = "org.kde.image";\n'
        '  d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];\n'
        '  d.writeConfig("Image", chosen.uri);\n'
        '  d.writeConfig("FillMode", 0);\n'
        "}\n"
    )


def color_hex(color: Rgb) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in color)


def _read_color(raw: str) -> Rgb | None:
    text = raw.strip()
    try:
        if len(text) == 7 and text.startswith("#"):
            rgb = tuple(int(text[start:start + 2], 16) for start in (1, 3, 5))
        else:
            rgb = tuple(int(value) for value in text.split(",")[:3])
        if len(rgb) == 3 and all(0 <= channel <= 255 for channel in rgb):
            return rgb
    except ValueError:
        pass
    return None


def solid_background_script(color: Rgb) -> str:
    return (
        "var list = desktops();\n"
        "for (var i = 0; i < list.length; i++) {\n"
        "  var d = list[i];\n"
        '  d.wallpaperPlugin = "org.kde.color";\n'
        '  d.currentConfigGroup = ["Wallpaper", "org.kde.color", "General"];\n'
        f'  d.writeConfig("Color", "{color_hex(color)}");\n'
        "}\n"
        'print("onigiri-background-set");\n'
    )


def set_scene_wallpaper(
    path: str,
    screens: list[tuple[str, int, int, int, int]],
    pans: dict[str, tuple[float, float]] | None = None,
    *,
    run=None,
    cache_dir: Path | None = None,
    render=None,
    background_color: Rgb | None = None,
) -> None:
    if background_color is not None:
        runner = run or (lambda script: bool(evaluate_plasma(script)))
        if not runner(solid_background_script(background_color)):
            raise RuntimeError("Could not set the background color.")
        return
    if not str(path).strip():
        return  # A scene can change lighting without touching the desktop background.
    image = Path(path).expanduser()
    if not image.is_file():
        raise RuntimeError("That picture is missing.")
    if not screens:
        set_wallpaper(str(image), run=run)
        return
    cache = Path(cache_dir) if cache_dir is not None else Path.home() / ".cache" / "onigiri" / "crops"
    cache.mkdir(parents=True, exist_ok=True)
    writer = render or write_cover_crop
    pans = pans or {}
    targets: list[tuple[int, int, int, int, str]] = []
    for name, x_pos, y_pos, width, height in screens:
        pan_x, pan_y = pan_pair(pans.get(name))
        dest = cache / f"{_crop_key(image, name, width, height, pan_x, pan_y)}.png"
        writer(str(image), dest, width, height, pan_x, pan_y)
        targets.append((x_pos, y_pos, width, height, dest.resolve().as_uri()))
    runner = run or (lambda script: bool(evaluate_plasma(script)))
    if not runner(scene_wallpaper_script(targets)):
        raise RuntimeError("Could not set the wallpaper.")


def _crop_key(image: Path, name: str, width: int, height: int, pan_x: float, pan_y: float) -> str:
    raw = f"{image.resolve()}|{name}|{width}x{height}|{pan_x:.4f}|{pan_y:.4f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _containment_id(section: str) -> str | None:
    parts = section.split("][")
    if len(parts) != 2 or parts[0] != "Containments":
        return None
    return parts[1]


def _fill_mode(value: str) -> str:
    try:
        return _FILL_MODES.get(int(str(value).strip() or "2"), "crop")
    except ValueError:
        return "crop"


def _image_from_section(section: configparser.SectionProxy) -> str:
    direct = _resolve_image(section.get("Image", ""))
    if direct:
        return direct
    for raw in _split_list(section.get("SlidePaths", "")):
        found = _resolve_image(raw)
        if found:
            return found
    return ""


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _resolve_image(value: str) -> str:
    raw = value.strip().strip('"')
    if not raw or raw.startswith("preferred:"):
        return ""
    if raw.startswith("file:"):
        raw = unquote(urlparse(raw).path)
    else:
        raw = raw.split("#", 1)[0].strip()
    path = Path(raw)
    if path.is_file():
        return str(path)
    if path.is_dir():
        return _first_image(path)
    return ""


def _first_image(directory: Path) -> str:
    images = sorted(
        (item for item in directory.iterdir() if item.is_file() and item.suffix.lower() in _IMAGE_SUFFIXES),
        key=lambda item: item.name,
    )
    return str(images[0]) if images else ""
