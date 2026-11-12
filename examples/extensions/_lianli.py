"""Lian Li fan LED + LCD paint, then one daemon restart."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

Rgb = tuple[int, int, int]


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


def _is_rgb(value) -> bool:
    return _channels(value) is not None


def _walk(node, on_dict) -> None:
    if isinstance(node, dict):
        on_dict(node)
        for value in node.values():
            _walk(value, on_dict)
    elif isinstance(node, list):
        for value in node:
            _walk(value, on_dict)


def paint(
    led_one: Rgb | None,
    led_two: Rgb | None,
    text: Rgb | None,
    gauge: Rgb | None,
    path: Path | None = None,
) -> None:
    target = path or lianli_path()
    if not target.exists():
        return
    data = json.loads(target.read_text(encoding="utf-8"))
    if led_one is not None and led_two is None:
        led_two = led_one
    if led_two is not None and led_one is None:
        led_one = led_two
    rgb = data.get("rgb")
    if led_one is not None and led_two is not None and isinstance(rgb, dict):

        def on_led(node) -> None:
            colors = node.get("colors")
            if (
                node.get("mode") == "TailChasing"
                and isinstance(colors, list)
                and len(colors) == 2
                and all(_is_rgb(color) for color in colors)
            ):
                node["colors"] = [list(led_one), list(led_two)]

        _walk(rgb, on_led)
    for lcd in data.get("lcds") or []:
        if not isinstance(lcd, dict) or lcd.get("type") != "sensor":
            continue
        sensor = lcd.get("sensor")
        if not isinstance(sensor, dict):
            continue
        if text is not None and _is_rgb(sensor.get("text_color")):
            sensor["text_color"] = list(text)
        if gauge is None:
            continue
        for range_row in sensor.get("gauge_ranges") or []:
            if isinstance(range_row, dict) and _is_rgb(range_row.get("color")):
                range_row["color"] = list(gauge)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


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



def read_colors(path: Path | None = None) -> dict:
    target = path or lianli_path()
    if not target.is_file():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    found = {}
    def on_led(node):
        colors = node.get("colors")
        if node.get("mode") == "TailChasing" and isinstance(colors, list) and len(colors) == 2:
            for key, color in zip(("led_blue", "led_purple"), colors):
                if _is_rgb(color):
                    found.setdefault(key, tuple(color))
    _walk(data.get("rgb", {}), on_led)
    for lcd in data.get("lcds") or []:
        if not isinstance(lcd, dict):
            continue
        sensor = lcd.get("sensor") or {}
        if not isinstance(sensor, dict):
            continue
        if _is_rgb(sensor.get("text_color")):
            found.setdefault("text", tuple(sensor["text_color"]))
        for item in sensor.get("gauge_ranges") or []:
            if isinstance(item, dict) and _is_rgb(item.get("color")):
                found.setdefault("gauge", tuple(item["color"]))
    return found
