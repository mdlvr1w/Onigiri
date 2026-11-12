import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage

from onigiri.palette import dominant_colors, slot_colors


def _two_tone(path: Path, left: tuple[int, int, int], right: tuple[int, int, int], *, left_cols: int = 7, right_cols: int = 3, height: int = 8) -> Path:
    image = QImage(left_cols + right_cols, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(left_cols + right_cols):
            color = left if x < left_cols else right
            image.setPixelColor(x, y, QColor(*color))
    image.save(str(path), "PNG")
    return path


class PaletteTests(unittest.TestCase):
    def test_two_most_present_colors_mapped_to_blue_and_purple(self) -> None:
        blue = (0, 80, 220)
        magenta = (200, 0, 180)
        with tempfile.TemporaryDirectory() as tmp:
            path = _two_tone(Path(tmp) / "wall.png", magenta, blue, left_cols=7, right_cols=3)
            colors = dominant_colors(path)
            self.assertEqual(len(colors), 2)
            self.assertLess(_distance(colors[0], magenta), _distance(colors[0], blue))
            self.assertLess(_distance(colors[1], blue), _distance(colors[1], magenta))
            slotted_blue, slotted_purple = slot_colors(colors)
            self.assertIsNotNone(slotted_blue)
            self.assertIsNotNone(slotted_purple)
            self.assertLess(_distance(slotted_blue, blue), _distance(slotted_blue, magenta))
            self.assertLess(_distance(slotted_purple, magenta), _distance(slotted_purple, blue))

    def test_missing_image_has_no_colors(self) -> None:
        self.assertEqual(dominant_colors("/tmp/onigiri-missing-wallpaper.png"), [])
        self.assertEqual(slot_colors([]), (None, None))


def _distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> int:
    return (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2 + (left[2] - right[2]) ** 2
