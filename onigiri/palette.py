"""Pick the two most present colors from a picture for a scene."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage

from onigiri.lights import Rgb

_BLUE_HUE = 210
_PURPLE_HUE = 290
_MIN_SEPARATION = 80 * 80
_SCALE = 64


def dominant_colors(path: str | Path, count: int = 2) -> list[Rgb]:
    image = QImage(str(path))
    if image.isNull():
        return []
    sample = image.scaled(
        _SCALE,
        _SCALE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for y in range(sample.height()):
        for x in range(sample.width()):
            pixel = sample.pixelColor(x, y)
            if pixel.alpha() < 128:
                continue
            red, green, blue = pixel.red(), pixel.green(), pixel.blue()
            brightest = max(red, green, blue)
            darkest = min(red, green, blue)
            if brightest < 28 or darkest > 232 or (brightest - darkest) < 18:
                continue
            key = (red >> 4, green >> 4, blue >> 4)
            bucket = buckets.get(key)
            if bucket is None:
                buckets[key] = [1, red, green, blue]
            else:
                bucket[0] += 1
                bucket[1] += red
                bucket[2] += green
                bucket[3] += blue
    ranked = sorted(buckets.values(), key=lambda item: item[0], reverse=True)
    picked: list[Rgb] = []
    for count_pixels, red, green, blue in ranked:
        color = (red // count_pixels, green // count_pixels, blue // count_pixels)
        if any(_distance(color, other) < _MIN_SEPARATION for other in picked):
            continue
        picked.append(color)
        if len(picked) >= count:
            break
    return picked


def slot_colors(colors: list[Rgb]) -> tuple[Rgb | None, Rgb | None]:
    """Map extracted colors onto the blue and purple slots by hue."""
    if len(colors) >= 2:
        first, second = colors[0], colors[1]
        if _pair_cost(first, second) <= _pair_cost(second, first):
            return first, second
        return second, first
    if len(colors) == 1:
        color = colors[0]
        if _hue_distance(color, _BLUE_HUE) <= _hue_distance(color, _PURPLE_HUE):
            return color, None
        return None, color
    return None, None


def _distance(left: Rgb, right: Rgb) -> int:
    return (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2 + (left[2] - right[2]) ** 2


def _hue_of(color: Rgb) -> int:
    return QColor(*color).hue()


def _hue_distance(color: Rgb, target: int) -> int:
    hue = _hue_of(color)
    if hue < 0:
        return 180
    delta = abs(hue - target) % 360
    return min(delta, 360 - delta)


def _pair_cost(blue: Rgb, purple: Rgb) -> int:
    return _hue_distance(blue, _BLUE_HUE) + _hue_distance(purple, _PURPLE_HUE)
