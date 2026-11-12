import unittest

from onigiri.geometry import Rect, clamp_ratio, layout_rects
from onigiri.model import Leaf, Split, Tile


def _leaf(name: str) -> Leaf:
    return Leaf(Tile(id=name, name=name))


class GeometryTests(unittest.TestCase):
    def test_gapless_tile_keeps_the_screen_origin(self) -> None:
        rects = layout_rects(_leaf("only"), Rect(100, 50, 200, 100), 0)
        self.assertEqual([(tile.name, rect) for tile, rect in rects], [("only", Rect(100, 50, 200, 100))])

    def test_vertical_gap_is_shared(self) -> None:
        root = Split("v", 0.5, _leaf("left"), _leaf("right"))
        rects = {tile.name: rect for tile, rect in layout_rects(root, Rect(0, 0, 1000, 800), 10)}
        self.assertEqual(rects["left"], Rect(10, 10, 485, 780))
        self.assertEqual(rects["right"], Rect(505, 10, 485, 780))
        self.assertEqual(rects["right"].x - (rects["left"].x + rects["left"].width), 10)

    def test_nested_splits_keep_a_uniform_gap(self) -> None:
        root = Split(
            "v",
            0.5,
            _leaf("left"),
            Split("h", 0.5, _leaf("top"), _leaf("bottom")),
        )
        rects = {tile.name: rect for tile, rect in layout_rects(root, Rect(0, 0, 1000, 800), 10)}
        self.assertEqual(rects["left"], Rect(10, 10, 485, 780))
        self.assertEqual(rects["top"], Rect(505, 10, 485, 385))
        self.assertEqual(rects["bottom"], Rect(505, 405, 485, 385))
        self.assertEqual(rects["bottom"].y - (rects["top"].y + rects["top"].height), 10)

    def test_ratio_stays_inside_the_tile(self) -> None:
        self.assertEqual(clamp_ratio(0, 100, 50), 0.5)
        self.assertEqual(clamp_ratio(0, 100, -20), 0.08)
        self.assertEqual(clamp_ratio(0, 100, 500), 0.92)
        self.assertEqual(clamp_ratio(10, 0, 40), 0.5)


if __name__ == "__main__":
    unittest.main()
