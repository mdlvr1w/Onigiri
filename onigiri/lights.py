"""Color helpers and a compatibility apply that only runs the extension host."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Protocol

from onigiri.extensions import apply_scene, payload_from_colors

DEFAULT_BLUE = (0, 123, 255)
DEFAULT_PURPLE = (212, 0, 255)

Rgb = tuple[int, int, int]


class ColorBackend(Protocol):
    """Optional test sink. Production apply_colors does not construct these."""

    def apply(
        self,
        blue: Rgb,
        purple: Rgb,
        *,
        hardware_blue: Rgb,
        hardware_purple: Rgb,
    ) -> None: ...


def colors_path() -> Path:
    return Path.home() / ".config" / "dual-color" / "colors.json"


def lianli_path() -> Path:
    return Path.home() / ".config" / "lianli" / "config.json"


def _channels(value) -> Rgb | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        rgb = tuple(int(channel) for channel in value)
    except (TypeError, ValueError):
        return None
    if any(channel < 0 or channel > 255 for channel in rgb):
        return None
    return rgb  # type: ignore[return-value]


def load_colors(path: Path | None = None) -> tuple[Rgb, Rgb]:
    target = path or colors_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        blue = _channels(data.get("blue"))
        purple = _channels(data.get("purple"))
        if blue is not None and purple is not None:
            return blue, purple
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return DEFAULT_BLUE, DEFAULT_PURPLE


def write_colors(blue: Rgb, purple: Rgb, path: Path | None = None) -> None:
    target = path or colors_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"blue": list(blue), "purple": list(purple)}, indent=2) + "\n"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)


def _is_rgb(value) -> bool:
    return _channels(value) is not None


def _edit_lianli(visit, path: Path | None = None) -> None:
    target = path or lianli_path()
    if not target.exists():
        return
    data = json.loads(target.read_text(encoding="utf-8"))
    visit(data)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _walk(node, on_dict) -> None:
    if isinstance(node, dict):
        on_dict(node)
        for value in node.values():
            _walk(value, on_dict)
    elif isinstance(node, list):
        for value in node:
            _walk(value, on_dict)


def paint_lianli_leds(blue: Rgb, purple: Rgb, path: Path | None = None) -> None:
    def on_dict(node) -> None:
        colors = node.get("colors")
        if (
            node.get("mode") == "TailChasing"
            and isinstance(colors, list)
            and len(colors) == 2
            and all(_is_rgb(color) for color in colors)
        ):
            node["colors"] = [list(blue), list(purple)]

    _edit_lianli(lambda data: _walk(data, on_dict), path)


def paint_lianli_displays(blue: Rgb, purple: Rgb, path: Path | None = None) -> None:
    def on_dict(node) -> None:
        if _is_rgb(node.get("text_color")):
            node["text_color"] = list(blue)
        for gauge in node.get("gauge_ranges") or []:
            if isinstance(gauge, dict) and _is_rgb(gauge.get("color")):
                gauge["color"] = list(purple)

    _edit_lianli(lambda data: _walk(data, on_dict), path)


def paint_lianli(blue: Rgb, purple: Rgb, path: Path | None = None) -> None:
    paint_lianli_leds(blue, purple, path)
    paint_lianli_displays(blue, purple, path)


def reload_fans() -> None:
    result = subprocess.run(
        ["systemctl", "--user", "restart", "lianli-daemon.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "the fan daemon did not restart").strip()
        raise RuntimeError(detail)


def pair_for_hardware(colors: dict[str, Rgb]) -> tuple[Rgb, Rgb] | None:
    """Pick two RGB values from a named map. One color is used twice. Empty map is None."""

    blue = colors.get("hardware_blue") or colors.get("blue")
    purple = colors.get("hardware_purple") or colors.get("purple")
    if blue is None and purple is None:
        values = [rgb for rgb in colors.values() if _channels(rgb)]
        if not values:
            return None
        if len(values) == 1:
            return values[0], values[0]
        return values[0], values[1]
    if blue is None:
        blue = purple
    if purple is None:
        purple = blue
    assert blue is not None and purple is not None
    return blue, purple


def apply_colors(
    blue: Rgb,
    purple: Rgb,
    *,
    hardware_blue: Rgb | None = None,
    hardware_purple: Rgb | None = None,
    paint_hue: bool = True,
    colors_file: Path | None = None,
    gpu_file: Path | None = None,
    ram_file: Path | None = None,
    ryuo_file: Path | None = None,
    lianli_file: Path | None = None,
    hue_file: Path | None = None,
    reload_fn=None,
    hue_fn=None,
    backends: list[ColorBackend] | None = None,
    extensions_dir: Path | None = None,
) -> None:
    case_blue = blue if hardware_blue is None else hardware_blue
    case_purple = purple if hardware_purple is None else hardware_purple
    if backends is not None:
        for backend in backends:
            backend.apply(blue, purple, hardware_blue=case_blue, hardware_purple=case_purple)
        return
    extras = {
        "paint_hue": paint_hue,
        "colors_file": colors_file,
        "gpu_file": gpu_file or colors_file,
        "ram_file": ram_file or colors_file,
        "ryuo_file": ryuo_file or colors_file,
        "lianli_file": lianli_file,
        "hue_file": hue_file,
        "reload_fn": reload_fn,
        "hue_fn": hue_fn,
    }
    apply_scene(
        payload_from_colors(
            blue,
            purple,
            hardware_blue=case_blue,
            hardware_purple=case_purple,
            extras=extras,
        ),
        directory=extensions_dir,
    )
