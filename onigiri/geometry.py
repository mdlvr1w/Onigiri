"""Turn a split tree into pixel rectangles on a monitor's usable area."""

from __future__ import annotations

from dataclasses import dataclass

from onigiri.model import Leaf, Node, Split, Tile

MIN_RATIO = 0.08
MAX_RATIO = 0.92


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass
class SplitGuide:
    """A divider in the same coordinate space as the area passed to compute_layout."""

    orientation: str
    x1: float
    y1: float
    x2: float
    y2: float
    parent_x: float
    parent_y: float
    parent_w: float
    parent_h: float
    node: Split


@dataclass
class LayoutGeometry:
    tiles: list[tuple[Tile, Rect]]
    slots: list[tuple[str, Rect]]
    splits: list[SplitGuide]


def clamp_ratio(parent_start: float, parent_size: float, pos: float) -> float:
    if parent_size <= 0:
        return 0.5
    ratio = (pos - parent_start) / parent_size
    return min(MAX_RATIO, max(MIN_RATIO, ratio))


def _inset(
    x: float,
    y: float,
    w: float,
    h: float,
    screen_w: float,
    screen_h: float,
    gap: float,
) -> tuple[float, float, float, float]:
    if gap <= 0:
        return x, y, max(1.0, w), max(1.0, h)
    eps = 0.5
    left = gap if abs(x) <= eps else gap / 2.0
    right = gap if abs((x + w) - screen_w) <= eps else gap / 2.0
    top = gap if abs(y) <= eps else gap / 2.0
    bottom = gap if abs((y + h) - screen_h) <= eps else gap / 2.0
    if left + right > w - 1 and left + right > 0:
        scale = (w - 1) / (left + right)
        left *= scale
        right *= scale
    if top + bottom > h - 1 and top + bottom > 0:
        scale = (h - 1) / (top + bottom)
        top *= scale
        bottom *= scale
    return x + left, y + top, max(1.0, w - left - right), max(1.0, h - top - bottom)


def compute_layout(root: Node, area: Rect, gap: int) -> LayoutGeometry:
    """Leaf rectangles include `area`'s origin. Guides are in that same space."""

    tiles: list[tuple[Tile, Rect]] = []
    slots: list[tuple[str, Rect]] = []
    splits: list[SplitGuide] = []
    gap_f = float(max(0, gap))
    screen_w = float(area.width)
    screen_h = float(area.height)

    def walk(node: Node, x: float, y: float, w: float, h: float) -> None:
        if isinstance(node, Leaf):
            ix, iy, iw, ih = _inset(x, y, w, h, screen_w, screen_h, gap_f)
            rect = Rect(
                int(round(area.x + ix)),
                int(round(area.y + iy)),
                max(1, int(round(iw))),
                max(1, int(round(ih))),
            )
            if node.tile is None:
                slots.append((node.slot, rect))
            else:
                tiles.append((node.tile, rect))
            return
        if node.orientation == "v":
            first_w = w * node.ratio
            split_x = x + first_w
            splits.append(
                SplitGuide(
                    orientation="v",
                    x1=area.x + split_x,
                    y1=area.y + y,
                    x2=area.x + split_x,
                    y2=area.y + y + h,
                    parent_x=area.x + x,
                    parent_y=area.y + y,
                    parent_w=w,
                    parent_h=h,
                    node=node,
                )
            )
            walk(node.first, x, y, first_w, h)
            walk(node.second, split_x, y, w - first_w, h)
            return
        first_h = h * node.ratio
        split_y = y + first_h
        splits.append(
            SplitGuide(
                orientation="h",
                x1=area.x + x,
                y1=area.y + split_y,
                x2=area.x + x + w,
                y2=area.y + split_y,
                parent_x=area.x + x,
                parent_y=area.y + y,
                parent_w=w,
                parent_h=h,
                node=node,
            )
        )
        walk(node.first, x, y, w, first_h)
        walk(node.second, x, split_y, w, h - first_h)

    if area.width > 0 and area.height > 0:
        walk(root, 0.0, 0.0, screen_w, screen_h)
    return LayoutGeometry(tiles=tiles, slots=slots, splits=splits)


def layout_rects(root: Node, area: Rect, gap: int) -> list[tuple[Tile, Rect]]:
    return compute_layout(root, area, gap).tiles
