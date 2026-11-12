import tempfile
import unittest
from pathlib import Path

from onigiri.lights import (
    DEFAULT_BLUE,
    DEFAULT_PURPLE,
    apply_colors,
    load_colors,
    paint_lianli,
    pair_for_hardware,
    write_colors,
)


class LightsTests(unittest.TestCase):
    def test_missing_file_uses_the_default_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "colors.json"
            self.assertEqual(load_colors(path), (DEFAULT_BLUE, DEFAULT_PURPLE))

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "colors.json"
            write_colors((1, 2, 3), (4, 5, 6), path)
            self.assertEqual(load_colors(path), ((1, 2, 3), (4, 5, 6)))

    def test_apply_without_extensions_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            colors = folder / "colors.json"
            apply_colors((10, 20, 30), (40, 50, 60), colors_file=colors, extensions_dir=folder / "none")
            self.assertFalse(colors.exists())

    def test_paint_skips_a_missing_lianli_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paint_lianli((1, 2, 3), (4, 5, 6), Path(tmp) / "missing.json")

    def test_custom_backend_is_the_only_sink(self) -> None:
        painted: list[tuple] = []

        class Probe:
            def apply(self, blue, purple, *, hardware_blue, hardware_purple) -> None:
                painted.append((blue, purple, hardware_blue, hardware_purple))

        apply_colors((1, 2, 3), (4, 5, 6), hardware_blue=(9, 8, 7), hardware_purple=(6, 5, 4), backends=[Probe()])
        self.assertEqual(painted, [((1, 2, 3), (4, 5, 6), (9, 8, 7), (6, 5, 4))])

    def test_pair_for_hardware_accepts_one_or_many(self) -> None:
        self.assertEqual(pair_for_hardware({"accent": (1, 2, 3)}), ((1, 2, 3), (1, 2, 3)))
        self.assertEqual(pair_for_hardware({"blue": (1, 1, 1), "purple": (2, 2, 2)}), ((1, 1, 1), (2, 2, 2)))
        self.assertIsNone(pair_for_hardware({}))
