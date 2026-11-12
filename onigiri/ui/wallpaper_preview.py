"""Real monitor layout used to pan a scene wallpaper on each screen."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from onigiri.geometry import Rect
from onigiri.ui.canvas import _spread_offsets
from onigiri.ui.screens import ScreenInfo, screen_list
from onigiri.model import pan_pair


class WallpaperStage(QWidget):
    pansChanged = pyqtSignal()

    def __init__(self, parent=None, *, screens: list[ScreenInfo] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scenePreview")
        self.setMinimumHeight(340)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._screens: list[ScreenInfo] = list(screens) if screens is not None else []
        self._pans: dict[str, tuple[float, float]] = {}
        self._pixmap = QPixmap()
        self._areas: list[tuple[ScreenInfo, Rect]] = []
        self._origin_x = 0.0
        self._origin_y = 0.0
        self._span_w = 1920.0
        self._span_h = 1080.0
        self._scale = 1.0
        self._ox = 16.0
        self._oy = 16.0
        self._drag: dict | None = None
        if screens is None:
            self._screens = screen_list(usable=False)
        self._recompute()

    def pans(self) -> dict[str, tuple[float, float]]:
        return dict(self._pans)

    def set_pans(self, pans: dict[str, tuple[float, float]] | None) -> None:
        self._pans = {name: pan_pair(value) for name, value in (pans or {}).items()}
        self.update()

    def reset_pans(self) -> None:
        self._pans = {}
        self.update()

    def set_screens(self, screens: list[ScreenInfo]) -> None:
        self._screens = list(screens)
        self._recompute()
        self.update()

    def set_color(self, color) -> None:
        self._pixmap = QPixmap(1, 1)
        self._pixmap.fill(QColor(*color))
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def set_image(self, path: str) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor if path else Qt.CursorShape.ArrowCursor)
        pixmap = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        self._pixmap = pixmap if not pixmap.isNull() else QPixmap()
        self.update()

    def pan_for(self, name: str) -> tuple[float, float]:
        return pan_pair(self._pans.get(name))

    def _recompute(self) -> None:
        rects = [
            Rect(screen.x, screen.y, max(1, screen.width), max(1, screen.height))
            for screen in self._screens
        ]
        offsets = _spread_offsets(rects)
        self._areas = [
            (screen, Rect(rect.x + dx, rect.y + dy, rect.width, rect.height))
            for screen, rect, (dx, dy) in zip(self._screens, rects, offsets)
        ]
        if self._areas:
            left = min(area.x for _screen, area in self._areas)
            top = min(area.y for _screen, area in self._areas)
            right = max(area.x + area.width for _screen, area in self._areas)
            bottom = max(area.y + area.height for _screen, area in self._areas)
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
        bounds = self.rect().adjusted(12, 12, -12, -20)
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

    def _to_widget(self, x: float, y: float) -> QPointF:
        return QPointF(
            self._ox + (x - self._origin_x) * self._scale,
            self._oy + (y - self._origin_y) * self._scale,
        )

    def _panel(self, area: Rect) -> QRectF:
        top_left = self._to_widget(area.x, area.y)
        return QRectF(
            top_left.x(),
            top_left.y(),
            max(2.0, area.width * self._scale),
            max(2.0, area.height * self._scale),
        )

    def _hit(self, pos: QPointF) -> tuple[ScreenInfo, QRectF] | None:
        for screen, area in reversed(self._areas):
            panel = self._panel(area)
            if panel.contains(pos):
                return screen, panel
        return None

    def resizeEvent(self, event) -> None:
        self._fit()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        self._fit()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#1b1b1b"))
        if not self._areas:
            painter.setPen(QColor("#9a9a9a"))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "No screens")
            painter.end()
            return
        for screen, area in self._areas:
            panel = self._panel(area)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#121212"))
            painter.drawRect(panel)
            self._paint_image(painter, panel, screen.name)
            painter.setPen(QPen(QColor("#8fb4d4"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(panel)
            painter.setPen(QColor("#f3f3f3"))
            font = QFont(painter.font())
            font.setPointSize(9)
            painter.setFont(font)
            label = QRectF(panel.x(), panel.bottom() + 2, panel.width(), 16)
            painter.drawText(label, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), screen.name)
        painter.end()

    def _paint_image(self, painter: QPainter, panel: QRectF, name: str) -> None:
        if self._pixmap.isNull():
            return
        pan_x, pan_y = self.pan_for(name)
        image_w = max(1, self._pixmap.width())
        image_h = max(1, self._pixmap.height())
        scale = max(panel.width() / image_w, panel.height() / image_h)
        drawn_w = image_w * scale
        drawn_h = image_h * scale
        extra_x = drawn_w - panel.width()
        extra_y = drawn_h - panel.height()
        dest = QRectF(
            panel.x() - extra_x * pan_x,
            panel.y() - extra_y * pan_y,
            drawn_w,
            drawn_h,
        )
        painter.save()
        painter.setClipRect(panel)
        painter.drawPixmap(dest, self._pixmap, QRectF(self._pixmap.rect()))
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        hit = self._hit(event.position())
        if hit is None:
            return
        screen, panel = hit
        self._drag = {
            "name": screen.name,
            "start": event.position(),
            "pan": self.pan_for(screen.name),
            "panel": panel,
        }
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        drag = self._drag
        if drag is None:
            return
        panel: QRectF = drag["panel"]
        if self._pixmap.isNull():
            return
        image_w = max(1, self._pixmap.width())
        image_h = max(1, self._pixmap.height())
        scale = max(panel.width() / image_w, panel.height() / image_h)
        extra_x = image_w * scale - panel.width()
        extra_y = image_h * scale - panel.height()
        delta = event.position() - drag["start"]
        pan_x, pan_y = drag["pan"]
        if extra_x > 1:
            pan_x = pan_x - delta.x() / extra_x
        if extra_y > 1:
            pan_y = pan_y - delta.y() / extra_y
        self._pans[drag["name"]] = pan_pair((pan_x, pan_y))
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.pansChanged.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
