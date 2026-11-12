"""Desktop canvas: every screen in its real position, with that screen's tiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QWidget

from onigiri.geometry import Rect, SplitGuide, clamp_ratio, compute_layout
from onigiri.model import Leaf, Node, Split, Tile, can_combine, is_vacant_root
from onigiri.ui.theme import PANEL_BORDER, PANEL_EDGE, PANEL_FILL, PANEL_INNER_RADIUS, PANEL_RADIUS, accent_qcolor


@dataclass
class MonitorView:
    """One output, in the same global coordinates Qt reports."""

    name: str
    x: int
    y: int
    width: int
    height: int
    root: Node | None
    background: str = ""
    fill: str = "crop"
    active: bool = False
    gap: int = 8


def monitor_at(views: list[MonitorView], point: QPoint) -> MonitorView | None:
    hits = [
        view
        for view in views
        if view.x <= point.x() < view.x + max(1, view.width)
        and view.y <= point.y() < view.y + max(1, view.height)
    ]
    if not hits:
        return None
    return min(hits, key=lambda view: max(1, view.width) * max(1, view.height))


_PREVIEW_FRACTION = 360 / 1920


def split_icon(orientation: str, size: int = 20) -> QIcon:
    """Two panes side by side (`v`) or stacked (`h`)."""

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#9aa3a7"), 1))
    painter.setBrush(QColor("#1f8a52"))
    inset = 1.5
    gap = 2.5
    inner = size - inset * 2
    if orientation == "v":
        pane = (inner - gap) / 2
        painter.drawRoundedRect(QRectF(inset, inset, pane, inner), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(inset + pane + gap, inset, pane, inner), 2.5, 2.5)
    else:
        pane = (inner - gap) / 2
        painter.drawRoundedRect(QRectF(inset, inset, inner, pane), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(inset, inset + pane + gap, inner, pane), 2.5, 2.5)
    painter.end()
    return QIcon(pix)


def add_split_actions(menu: QMenu, on_split) -> None:
    menu.addAction(split_icon("h"), "Top / bottom", lambda: on_split("h"))
    menu.addAction(split_icon("v"), "Left / right", lambda: on_split("v"))


def preview_size(width: int, height: int) -> QSize:
    width = max(1, int(width))
    height = max(1, int(height))
    return QSize(
        max(1, int(round(width * _PREVIEW_FRACTION))),
        max(1, int(round(height * _PREVIEW_FRACTION))),
    )


def _load_background(source: str) -> QPixmap | None:
    if source.startswith("#") and len(source) == 7 and QColor(source).isValid():
        pixmap = QPixmap(1, 1)
        pixmap.fill(QColor(source))
        return pixmap
    pixmap = QPixmap(source) if source and Path(source).is_file() else QPixmap()
    return pixmap if not pixmap.isNull() else None


def paint_monitor_preview(
    painter: QPainter,
    view: MonitorView,
    target: QRectF,
    wallpaper: QPixmap | None = None,
    outline: str = "#314556",
    outline_width: int = 1,
    tile_alpha: int = 108,
) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1b1b1b"))
    painter.drawRect(target)
    pixmap = wallpaper
    if pixmap is None and view.background:
        pixmap = _load_background(view.background)
    if pixmap is not None:
        width = max(1, int(target.width()))
        height = max(1, int(target.height()))
        painter.save()
        painter.setClipRect(target)
        smooth = Qt.TransformationMode.SmoothTransformation
        if view.fill == "stretch":
            scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
            painter.drawPixmap(int(target.x()), int(target.y()), scaled)
        elif view.fill == "fit":
            scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, smooth)
            painter.drawPixmap(_centered(target, scaled.width(), scaled.height()), scaled)
        elif view.fill == "center":
            painter.drawPixmap(_centered(target, pixmap.width(), pixmap.height()), pixmap)
        else:
            scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding, smooth)
            painter.drawPixmap(_centered(target, scaled.width(), scaled.height()), scaled)
        painter.restore()

    area = Rect(int(view.x), int(view.y), max(1, int(view.width)), max(1, int(view.height)))
    root = None if is_vacant_root(view.root) else view.root
    scale_x = target.width() / area.width
    scale_y = target.height() / area.height

    def mapped(rect: Rect) -> QRectF:
        return QRectF(
            target.x() + (rect.x - area.x) * scale_x,
            target.y() + (rect.y - area.y) * scale_y,
            max(2.0, rect.width * scale_x),
            max(2.0, rect.height * scale_y),
        )

    if root is not None:
        geometry = compute_layout(root, area, max(0, int(view.gap)))
        for tile, rect in geometry.tiles:
            box = mapped(rect)
            path = QPainterPath()
            path.addRoundedRect(box, 8, 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(16, 16, 16, min(180, tile_alpha + 40)))
            painter.drawPath(path)
            painter.setPen(QColor("#f3f3f3"))
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSizeF(max(7.0, min(13.0, box.height() / 7)))
            painter.setFont(font)
            inner = box.adjusted(8, 6, -8, -6)
            if inner.width() >= 8 and inner.height() >= 8:
                name = painter.fontMetrics().elidedText(
                    tile.name or "Tile",
                    Qt.TextElideMode.ElideRight,
                    int(inner.width()),
                )
                painter.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, name)
        for _slot, rect in geometry.slots:
            path = QPainterPath()
            path.addRoundedRect(mapped(rect), 8, 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(16, 16, 16, min(180, tile_alpha + 40)))
            painter.drawPath(path)

    if outline_width > 0:
        painter.setPen(QPen(QColor(outline), outline_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(target)


@dataclass
class _Drawn:
    name: str
    area: Rect
    active: bool
    pixmap: QPixmap | None
    fill: str
    root: Node | None
    tiles: list[tuple[Tile, Rect]]
    slots: list[tuple[str, Rect]]
    guides: list[SplitGuide]


_MARQUEE_SLOP = 6.0
_SCREEN_GUTTER = 140


def _offset_rect(rect: Rect, dx: int, dy: int) -> Rect:
    return Rect(rect.x + dx, rect.y + dy, rect.width, rect.height)


def _offset_guide(guide: SplitGuide, dx: int, dy: int) -> SplitGuide:
    return SplitGuide(
        orientation=guide.orientation,
        x1=guide.x1 + dx,
        y1=guide.y1 + dy,
        x2=guide.x2 + dx,
        y2=guide.y2 + dy,
        parent_x=guide.parent_x + dx,
        parent_y=guide.parent_y + dy,
        parent_w=guide.parent_w,
        parent_h=guide.parent_h,
        node=guide.node,
    )


def _spread_offsets(rects: list[Rect], gutter: int = _SCREEN_GUTTER) -> list[tuple[int, int]]:
    """Push touching screens apart so the canvas shows a gap between them."""
    if len(rects) < 2 or gutter <= 0:
        return [(0, 0) for _ in rects]
    xs = [rect.x for rect in rects]
    ys = [rect.y for rect in rects]

    def overlap(a1: int, a2: int, b1: int, b2: int) -> bool:
        return a1 < b2 and b1 < a2

    order = sorted(range(len(rects)), key=lambda index: (xs[index], ys[index]))
    for first in order:
        for second in order:
            if first == second:
                continue
            left, right = rects[first], rects[second]
            if not overlap(ys[first], ys[first] + left.height, ys[second], ys[second] + right.height):
                continue
            edge = xs[first] + left.width
            if xs[second] < edge - 1:
                continue
            gap = xs[second] - edge
            if gap >= gutter:
                continue
            shift = gutter - max(0, gap)
            origin = xs[second]
            for index in range(len(rects)):
                if xs[index] >= origin:
                    xs[index] += shift

    order = sorted(range(len(rects)), key=lambda index: (ys[index], xs[index]))
    for first in order:
        for second in order:
            if first == second:
                continue
            top, bottom = rects[first], rects[second]
            if not overlap(xs[first], xs[first] + top.width, xs[second], xs[second] + bottom.width):
                continue
            edge = ys[first] + top.height
            if ys[second] < edge - 1:
                continue
            gap = ys[second] - edge
            if gap >= gutter:
                continue
            shift = gutter - max(0, gap)
            origin = ys[second]
            for index in range(len(rects)):
                if ys[index] >= origin:
                    ys[index] += shift

    return [(xs[index] - rects[index].x, ys[index] - rects[index].y) for index in range(len(rects))]


def _centered(panel: QRectF, width: int, height: int) -> QPoint:
    return QPoint(
        int(panel.x() + (panel.width() - width) / 2),
        int(panel.y() + (panel.height() - height) / 2),
    )


class LayoutCanvas(QWidget):
    tileSelected = pyqtSignal(str)
    tilesSelected = pyqtSignal(list)
    splitRequested = pyqtSignal(str, str)
    combineRequested = pyqtSignal(str)
    tileRequested = pyqtSignal(str)
    slotRequested = pyqtSignal(str, str)
    tileOpened = pyqtSignal(str)
    deleteRequested = pyqtSignal(str)
    editBegan = pyqtSignal()
    editEnded = pyqtSignal()
    monitorActivated = pyqtSignal(str)
    resized = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 280)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._views: list[MonitorView] = []
        self._screens: list[_Drawn] = []
        self._gap = None
        self._origin_x = 0.0
        self._origin_y = 0.0
        self._span_w = 1920.0
        self._span_h = 1080.0
        self._active = ""
        self._selected: set[str] = set()
        self._pixmaps: dict[str, QPixmap | None] = {}
        self._scale = 1.0
        self._ox = 16.0
        self._oy = 16.0
        self._drag: dict | None = None
        self._press: dict | None = None
        self._marquee: QRectF | None = None

    @property
    def views(self) -> list[MonitorView]:
        return list(self._views)

    def set_monitors(self, monitors: list[MonitorView], gap: int | None = None, selected=None) -> None:
        self._views = list(monitors)
        self._gap = None if gap is None else max(0, int(gap))
        self.set_selected(selected)
        self._active = next((monitor.name for monitor in monitors if monitor.active), "")
        self._recompute()
        self.update()

    def set_selected(self, tile_ids) -> None:
        if not tile_ids:
            self._selected = set()
        elif isinstance(tile_ids, str):
            self._selected = {tile_ids}
        else:
            self._selected = {str(item) for item in tile_ids if item}
        self.update()

    def _pixmap_for(self, path: str) -> QPixmap | None:
        if path in self._pixmaps:
            return self._pixmaps[path]
        stored = _load_background(path)
        self._pixmaps[path] = stored
        return stored

    def _recompute(self) -> None:
        prepared: list[tuple[MonitorView, Node | None, Rect, list[tuple[Tile, Rect]], list[tuple[str, Rect]], list[SplitGuide]]] = []
        for monitor in self._views:
            area = Rect(int(monitor.x), int(monitor.y), max(1, int(monitor.width)), max(1, int(monitor.height)))
            tiles: list[tuple[Tile, Rect]] = []
            slots: list[tuple[str, Rect]] = []
            guides: list[SplitGuide] = []
            root = None if is_vacant_root(monitor.root) else monitor.root
            if root is not None and area.width > 0 and area.height > 0:
                used_gap = self._gap if self._gap is not None else max(0, int(monitor.gap))
                geometry = compute_layout(root, area, used_gap)
                tiles = geometry.tiles
                slots = geometry.slots
                guides = geometry.splits
            prepared.append((monitor, root, area, tiles, slots, guides))
        offsets = _spread_offsets([item[2] for item in prepared])
        screens: list[_Drawn] = []
        for (monitor, root, area, tiles, slots, guides), (dx, dy) in zip(prepared, offsets):
            screens.append(
                _Drawn(
                    name=monitor.name,
                    area=_offset_rect(area, dx, dy),
                    active=monitor.active,
                    pixmap=self._pixmap_for(monitor.background),
                    fill=monitor.fill or "crop",
                    root=root,
                    tiles=[(tile, _offset_rect(rect, dx, dy)) for tile, rect in tiles],
                    slots=[(slot, _offset_rect(rect, dx, dy)) for slot, rect in slots],
                    guides=[_offset_guide(guide, dx, dy) for guide in guides],
                )
            )
        self._screens = screens
        if screens:
            left = min(screen.area.x for screen in screens)
            top = min(screen.area.y for screen in screens)
            right = max(screen.area.x + screen.area.width for screen in screens)
            bottom = max(screen.area.y + screen.area.height for screen in screens)
            self._origin_x = float(left)
            self._origin_y = float(top)
            self._span_w = max(1.0, float(right - left))
            self._span_h = max(1.0, float(bottom - top))
        else:
            self._origin_x = 0.0
            self._origin_y = 0.0
            self._span_w = 1920.0
            self._span_h = 1080.0
        self._fit()

    def _fit(self) -> None:
        bounds = self.rect().adjusted(18, 18, -18, -18)
        if bounds.width() <= 0 or bounds.height() <= 0:
            self._scale = 1.0
            self._ox = 0.0
            self._oy = 0.0
            return
        scale = min(bounds.width() / self._span_w, bounds.height() / self._span_h)
        self._scale = scale if scale > 0 else 1.0
        drawn_w = self._span_w * self._scale
        drawn_h = self._span_h * self._scale
        self._ox = bounds.x() + (bounds.width() - drawn_w) / 2.0
        self._oy = bounds.y() + (bounds.height() - drawn_h) / 2.0

    def _local(self, pos) -> tuple[float, float]:
        if self._scale <= 0:
            return 0.0, 0.0
        return (
            self._origin_x + (pos.x() - self._ox) / self._scale,
            self._origin_y + (pos.y() - self._oy) / self._scale,
        )

    def _to_widget(self, x: float, y: float) -> QPointF:
        return QPointF(
            self._ox + (x - self._origin_x) * self._scale,
            self._oy + (y - self._origin_y) * self._scale,
        )

    def _screen_at(self, x: float, y: float) -> _Drawn | None:
        for screen in reversed(self._screens):
            area = screen.area
            if area.x <= x <= area.x + area.width and area.y <= y <= area.y + area.height:
                return screen
        return None

    def _guide_at(self, x: float, y: float) -> SplitGuide | None:
        screen = self._screen_at(x, y)
        if screen is None:
            return None
        tolerance = 8.0 / self._scale if self._scale else 8.0
        for guide in screen.guides:
            if guide.orientation == "v":
                if guide.y1 - tolerance <= y <= guide.y2 + tolerance and abs(x - guide.x1) <= tolerance:
                    return guide
            elif guide.x1 - tolerance <= x <= guide.x2 + tolerance and abs(y - guide.y1) <= tolerance:
                return guide
        return None

    def _tile_at(self, x: float, y: float) -> Tile | None:
        screen = self._screen_at(x, y)
        if screen is None:
            return None
        for tile, rect in reversed(screen.tiles):
            if rect.x <= x <= rect.x + rect.width and rect.y <= y <= rect.y + rect.height:
                return tile
        return None

    def _activate(self, screen: _Drawn) -> None:
        self._active = screen.name
        self.monitorActivated.emit(screen.name)

    def resizeEvent(self, event) -> None:
        self._recompute()
        super().resizeEvent(event)
        self.resized.emit()

    def paintEvent(self, _event) -> None:
        self._fit()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), PANEL_INNER_RADIUS, PANEL_INNER_RADIUS)
        painter.setClipPath(clip)
        if not self._screens:
            painter.end()
            return

        for screen in self._screens:
            self._paint_screen(painter, screen)
        if self._marquee is not None:
            accent = accent_qcolor()
            painter.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 48))
            painter.drawRect(self._marquee)
        painter.end()

    def _paint_screen(self, painter: QPainter, screen: _Drawn) -> None:
        top_left = self._to_widget(screen.area.x, screen.area.y)
        panel = QRectF(
            top_left.x(),
            top_left.y(),
            max(2.0, screen.area.width * self._scale),
            max(2.0, screen.area.height * self._scale),
        )
        inset = PANEL_BORDER / 2
        stroke = panel.adjusted(inset, inset, -inset, -inset)
        radius = PANEL_RADIUS - inset
        frame = QPainterPath()
        frame.addRoundedRect(stroke, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(PANEL_FILL)
        painter.drawPath(frame)
        painter.save()
        painter.setClipPath(frame)
        wall = self._paint_wallpaper(painter, panel, screen)
        for tile, rect in screen.tiles:
            self._paint_tile(painter, tile, rect, wall)
        for _slot, rect in screen.slots:
            self._paint_slot(painter, rect, wall)
        if screen.active and screen.root is None:
            plus = self._plus_box(screen.area, modest=True)
            self._paint_plus(painter, plus)
            if panel.width() >= 160 and panel.height() >= 100:
                painter.setPen(QColor("#f3f3f3"))
                painter.drawText(
                    QRectF(panel.left() + 8, plus.bottom() + 10, panel.width() - 16, 28),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    "Add your first tile",
                )
        self._paint_drag_seam(painter, screen)
        painter.restore()
        outline = QPen(accent_qcolor() if screen.active else PANEL_EDGE, PANEL_BORDER)
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(outline)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(stroke, radius, radius)

    def _paint_wallpaper(self, painter: QPainter, panel: QRectF, screen: _Drawn) -> tuple[QPixmap, QPoint] | None:
        pixmap = screen.pixmap
        if pixmap is None:
            return None
        width = max(1, int(panel.width()))
        height = max(1, int(panel.height()))
        smooth = Qt.TransformationMode.SmoothTransformation
        if screen.fill == "stretch":
            scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
            origin = QPoint(int(panel.x()), int(panel.y()))
        elif screen.fill == "fit":
            scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, smooth)
            origin = _centered(panel, scaled.width(), scaled.height())
        elif screen.fill == "center":
            scaled_w = max(1, int(pixmap.width() * self._scale))
            scaled_h = max(1, int(pixmap.height() * self._scale))
            scaled = pixmap.scaled(scaled_w, scaled_h, Qt.AspectRatioMode.IgnoreAspectRatio, smooth)
            origin = _centered(panel, scaled.width(), scaled.height())
        else:
            scaled = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding, smooth)
            origin = _centered(panel, scaled.width(), scaled.height())
        painter.save()
        clip = QPainterPath()
        inset = PANEL_BORDER / 2
        clip.addRoundedRect(
            panel.adjusted(inset, inset, -inset, -inset),
            PANEL_RADIUS - inset,
            PANEL_RADIUS - inset,
        )
        painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        painter.drawPixmap(origin, scaled)
        painter.restore()
        return scaled, origin

    def _panel_rect(self, screen: _Drawn) -> QRectF:
        top_left = self._to_widget(screen.area.x, screen.area.y)
        return QRectF(
            top_left.x(),
            top_left.y(),
            max(2.0, screen.area.width * self._scale),
            max(2.0, screen.area.height * self._scale),
        )

    def _plus_box(self, rect: Rect, modest: bool = False) -> QRectF:
        top_left = self._to_widget(rect.x, rect.y)
        width = max(2.0, rect.width * self._scale)
        height = max(2.0, rect.height * self._scale)
        fraction = 0.12 if modest else 0.16
        size = min(width, height) * fraction
        size = max(12.0 if modest else 10.0, min(size, 22.0))
        return QRectF(top_left.x() + (width - size) / 2, top_left.y() + (height - size) / 2, size, size)

    def _paint_plus(self, painter: QPainter, box: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent_qcolor())
        thickness = max(1.5, box.width() * 0.14)
        span = box.width()
        center_x = box.center().x()
        center_y = box.center().y()
        painter.drawRect(QRectF(center_x - span / 2, center_y - thickness / 2, span, thickness))
        painter.drawRect(QRectF(center_x - thickness / 2, center_y - span / 2, thickness, span))

    def _paint_slot(self, painter: QPainter, rect: Rect, wall=None) -> None:
        box = self._tile_box(rect)
        self._paint_glass(painter, box, wall, selected=False, dashed=True)
        self._paint_plus(painter, self._plus_box(rect))

    def _paint_glass(self, painter: QPainter, box: QRectF, wall, *, selected: bool, dashed: bool = False) -> None:
        path = QPainterPath()
        path.addRoundedRect(box, 10, 10)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(path)
        if wall is not None:
            scaled, origin = wall
            crop = QRect(
                int(box.x() - origin.x()),
                int(box.y() - origin.y()),
                max(1, int(box.width())),
                max(1, int(box.height())),
            )
            piece = scaled.copy(crop)
            if not piece.isNull():
                small = piece.scaled(
                    max(1, piece.width() // 8),
                    max(1, piece.height() // 8),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                painter.drawPixmap(
                    int(box.x()),
                    int(box.y()),
                    small.scaled(piece.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation),
                )
        painter.fillPath(path, QColor(16, 16, 16, 175 if selected else 130))
        painter.restore()
        accent = accent_qcolor()
        pen = QPen(accent if selected else QColor(255, 255, 255, 40), 2 if selected else 1)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _paint_drag_seam(self, painter: QPainter, screen: _Drawn) -> None:
        drag = self._drag
        if not drag or not drag.get("moved"):
            return
        node = drag.get("node")
        for guide in screen.guides:
            if guide.node is not node:
                continue
            painter.setPen(QPen(QColor("#1a1a1a"), 1))
            painter.drawLine(
                self._to_widget(guide.x1, guide.y1),
                self._to_widget(guide.x2, guide.y2),
            )

    def _slot_at(self, screen: _Drawn, x: float, y: float) -> str | None:
        for slot, rect in screen.slots:
            if rect.x <= x <= rect.x + rect.width and rect.y <= y <= rect.y + rect.height:
                return slot
        return None

    def _screen_plus_hit(self, screen: _Drawn, pos) -> bool:
        box = self._plus_box(screen.area)
        if self._panel_rect(screen).width() >= 160 and self._panel_rect(screen).height() >= 100:
            box = box.united(QRectF(box.center().x() - 80, box.bottom(), 160, 42))
        return bool(screen.active and screen.root is None and box.contains(pos))

    def _paint_tile(self, painter: QPainter, tile: Tile, rect: Rect, wall=None) -> None:
        top_left = self._to_widget(rect.x, rect.y)
        size_w = max(2.0, rect.width * self._scale)
        size_h = max(2.0, rect.height * self._scale)
        box = QRectF(top_left.x(), top_left.y(), size_w, size_h)
        selected = tile.id in self._selected
        self._paint_glass(painter, box, wall, selected=selected)
        painter.setPen(QColor("#f3f3f3"))
        name_font = QFont(painter.font())
        name_font.setBold(True)
        name_font.setPointSizeF(max(7.0, min(13.0, size_h / 7)))
        painter.setFont(name_font)
        metrics = painter.fontMetrics()
        inner = box.adjusted(10, 8, -10, -8)
        if inner.width() < 8 or inner.height() < 8:
            return
        name = metrics.elidedText(tile.name or "Tile", Qt.TextElideMode.ElideRight, int(inner.width()))
        painter.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, name)

    def _tile_box(self, rect: Rect) -> QRectF:
        top_left = self._to_widget(rect.x, rect.y)
        return QRectF(
            top_left.x(),
            top_left.y(),
            max(2.0, rect.width * self._scale),
            max(2.0, rect.height * self._scale),
        )

    def _tiles_in_marquee(self, box: QRectF) -> list[str]:
        ids: list[str] = []
        for screen in self._screens:
            for tile, rect in screen.tiles:
                if box.intersects(self._tile_box(rect)) and tile.id not in ids:
                    ids.append(tile.id)
        return ids

    def _emit_selection(self, ids: list[str] | set[str]) -> None:
        ordered = list(ids) if isinstance(ids, list) else [item for item in ids]
        self._selected = set(ordered)
        self.tilesSelected.emit(ordered)
        self.tileSelected.emit(ordered[-1] if ordered else "")
        self.update()

    def _apply_click_selection(self, tile: Tile | None, mods) -> None:
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if tile is None:
            if not shift and not ctrl:
                self._emit_selection([])
            return
        chosen = set(self._selected)
        if ctrl:
            if tile.id in chosen:
                chosen.discard(tile.id)
            else:
                chosen.add(tile.id)
            self._emit_selection(list(chosen) if chosen else [])
        elif shift:
            chosen.add(tile.id)
            self._emit_selection(list(chosen))
        else:
            self._emit_selection([tile.id])

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._screens or self._scale <= 0:
            super().mousePressEvent(event)
            return
        local_x, local_y = self._local(event.position())
        screen = self._screen_at(local_x, local_y)
        if screen is not None:
            slot = self._slot_at(screen, local_x, local_y)
            if slot is not None:
                self._activate(screen)
                self.slotRequested.emit(screen.name, slot)
                event.accept()
                return
            if self._screen_plus_hit(screen, event.position()):
                self.tileRequested.emit(screen.name)
                event.accept()
                return
            self._activate(screen)
        tile = self._tile_at(local_x, local_y) if screen is not None else None
        guide = self._guide_at(local_x, local_y) if screen is not None and tile is None else None
        self._press = {
            "pos": QPointF(event.position()),
            "tile": tile,
            "mods": event.modifiers(),
            "anchor": set(self._selected),
            "guide": guide,
        }
        self._marquee = None
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None:
            start: QPointF = self._press["pos"]
            current = QPointF(event.position())
            delta = current - start
            if self._marquee is None and (abs(delta.x()) > _MARQUEE_SLOP or abs(delta.y()) > _MARQUEE_SLOP):
                guide = self._press.get("guide")
                if guide is not None:
                    self._press = None
                    self._start_split_drag(guide)
                    self._move_split(event)
                    event.accept()
                    return
                self._marquee = QRectF(start, current).normalized()
            if self._marquee is not None:
                self._marquee = QRectF(start, current).normalized()
                ids = self._tiles_in_marquee(self._marquee)
                mods = self._press["mods"]
                additive = bool(
                    mods
                    & (
                        Qt.KeyboardModifier.ShiftModifier
                        | Qt.KeyboardModifier.ControlModifier
                        | Qt.KeyboardModifier.MetaModifier
                    )
                )
                if additive:
                    chosen = set(self._press["anchor"]) | set(ids)
                    self._emit_selection(list(chosen))
                else:
                    self._emit_selection(ids)
            event.accept()
            return
        if not self._drag:
            guide = None
            if self._scale > 0:
                guide = self._guide_at(*self._local(event.position()))
            self.setCursor(
                Qt.CursorShape.SplitHCursor
                if guide and guide.orientation == "v"
                else Qt.CursorShape.SplitVCursor
                if guide
                else Qt.CursorShape.ArrowCursor
            )
            super().mouseMoveEvent(event)
            return
        self._move_split(event)
        event.accept()

    def _start_split_drag(self, guide: SplitGuide) -> None:
        vertical = guide.orientation == "v"
        self._drag = {
            "node": guide.node,
            "vertical": vertical,
            "parent_start": guide.parent_x if vertical else guide.parent_y,
            "parent_size": guide.parent_w if vertical else guide.parent_h,
            "moved": False,
        }

    def _move_split(self, event) -> None:
        if not self._drag:
            return
        if not self._drag["moved"]:
            self._drag["moved"] = True
            self.editBegan.emit()
        local_x, local_y = self._local(event.position())
        pos = local_x if self._drag["vertical"] else local_y
        self._drag["node"].ratio = clamp_ratio(self._drag["parent_start"], self._drag["parent_size"], pos)
        self._recompute()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        press = self._press
        self._press = None
        marquee = self._marquee
        self._marquee = None
        if press is not None:
            if marquee is None:
                self._apply_click_selection(press["tile"], press["mods"])
            self.update()
            event.accept()
            return
        drag = self._drag
        self._drag = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if drag and drag["moved"]:
            self.editEnded.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._screens or self._scale <= 0:
            super().mouseDoubleClickEvent(event)
            return
        local_x, local_y = self._local(event.position())
        screen = self._screen_at(local_x, local_y)
        if screen is None:
            super().mouseDoubleClickEvent(event)
            return
        slot = self._slot_at(screen, local_x, local_y)
        if slot is not None:
            self._activate(screen)
            self.slotRequested.emit(screen.name, slot)
            event.accept()
            return
        if self._screen_plus_hit(screen, event.position()):
            self.tileRequested.emit(screen.name)
            event.accept()
            return
        tile = self._tile_at(local_x, local_y)
        if tile is not None:
            self._activate(screen)
            self._emit_selection([tile.id])
            self.tileOpened.emit(tile.id)
            event.accept()
            return
        self._activate(screen)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        if not self._screens or self._scale <= 0:
            return
        # QContextMenuEvent has pos()/globalPos(), not the mouse-event position().
        local_x, local_y = self._local(event.pos())
        screen = self._screen_at(local_x, local_y)
        if screen is None:
            return
        self._activate(screen)
        menu = QMenu(self)
        if screen.root is None:
            menu.addAction("Add tile", lambda: self.tileRequested.emit(screen.name))
            menu.exec(event.globalPos())
            return
        guide = self._guide_at(local_x, local_y)
        tile = self._tile_at(local_x, local_y)
        if guide is not None and isinstance(guide.node, Split):
            if isinstance(guide.node.first, Leaf) and isinstance(guide.node.second, Leaf):
                keeper = guide.node.first if guide.node.first.tile is not None else guide.node.second
                if keeper.tile is not None:
                    leaf_id = keeper.tile.id
                    menu.addAction("Combine tiles", lambda: self.combineRequested.emit(leaf_id))
        if tile is not None:
            if tile.id not in self._selected:
                self._emit_selection([tile.id])
            add_split_actions(menu, lambda orientation, current=tile.id: self.splitRequested.emit(current, orientation))
            if can_combine(screen.root, tile.id):
                menu.addAction("Combine", lambda: self.combineRequested.emit(tile.id))
            menu.addAction("Delete tile", lambda tile_id=tile.id: self.deleteRequested.emit(tile_id))
        if menu.actions():
            menu.exec(event.globalPos())
