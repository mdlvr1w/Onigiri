"""Horizontal strip of substantial scene cards with a quiet playlist chip."""

from __future__ import annotations

from PyQt6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

from onigiri.model import Playlist, Scene
from onigiri.playlist import move_id
from onigiri.ui.theme import accent_qcolor

_CARD_W = 220
_CARD_H = 148
_RADIUS = 12.0
_SCENE_MIME = "application/x-onigiri-scene"


class PlaylistBar(QWidget):
    playToggled = pyqtSignal()
    settingsChanged = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("playlistBar")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.playToggled)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 999)
        self.interval_spin.setValue(5)
        self.interval_spin.valueChanged.connect(self.settingsChanged)
        self.unit_combo = QComboBox()
        self.unit_combo.addItem("sec", "seconds")
        self.unit_combo.addItem("min", "minutes")
        self.unit_combo.addItem("hr", "hours")
        self.unit_combo.setCurrentIndex(1)
        self.unit_combo.currentIndexChanged.connect(self.settingsChanged)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Loop", "repeat")
        self.mode_combo.addItem("Random", "random")
        self.mode_combo.setToolTip("Both modes repeat continuously until Stop: Loop follows the scene order; Random chooses another scene.")
        self.mode_combo.currentIndexChanged.connect(self.settingsChanged)
        for widget in (self.play_button, self.interval_spin, self.unit_combo, self.mode_combo):
            widget.setFixedHeight(30)
        self.interval_spin.setFixedWidth(58)
        self.unit_combo.setFixedWidth(62)
        self.mode_combo.setFixedWidth(78)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.play_button)
        layout.addWidget(self.interval_spin)
        layout.addWidget(self.unit_combo)
        layout.addStretch(1)
        layout.addWidget(self.mode_combo)

    def set_playlist(self, playlist: Playlist) -> None:
        self.interval_spin.blockSignals(True)
        self.unit_combo.blockSignals(True)
        self.mode_combo.blockSignals(True)
        self.interval_spin.setValue(playlist.interval)
        unit = self.unit_combo.findData(playlist.unit)
        self.unit_combo.setCurrentIndex(unit if unit >= 0 else 1)
        mode = self.mode_combo.findData(playlist.mode)
        self.mode_combo.setCurrentIndex(mode if mode >= 0 else 0)
        self.play_button.setText("Stop" if playlist.playing else "Play")
        self.interval_spin.blockSignals(False)
        self.unit_combo.blockSignals(False)
        self.mode_combo.blockSignals(False)

    def playlist(self) -> Playlist:
        return Playlist(
            interval=self.interval_spin.value(),
            unit=str(self.unit_combo.currentData() or "minutes"),
            mode=str(self.mode_combo.currentData() or "repeat"),
            playing=self.play_button.text() == "Stop",
        )


class SceneCard(QWidget):
    clicked = pyqtSignal(str)

    def __init__(self, scene: Scene, strip: "Filmstrip", parent=None) -> None:
        super().__init__(parent)
        self.scene = scene
        self._strip = strip
        self._press: QPoint | None = None
        self._dragging = False
        self._selected = False
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Select {scene.name}; use Apply to activate it")
        self.setAcceptDrops(True)
        self._pixmap = QPixmap(scene.wallpaper) if scene.wallpaper else QPixmap()

    def set_selected(self, on: bool) -> None:
        if self._selected != on:
            self._selected = on
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._dragging = True
        mime = QMimeData()
        mime.setData(_SCENE_MIME, self.scene.id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        grab = self.grab()
        drag.setPixmap(grab)
        drag.setHotSpot(self._press)
        drag.exec(Qt.DropAction.MoveAction)
        self._press = None

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None and not self._dragging:
            self.clicked.emit(self.scene.id)
            event.accept()
            self._press = None
            return
        self._press = None
        self._dragging = False
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:
        self._strip._accept_scene_drag(event)

    def dragMoveEvent(self, event) -> None:
        self._strip._accept_scene_drag(event)

    def dropEvent(self, event) -> None:
        self._strip._drop_event(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        frame = QRectF(1, 1, self.width() - 2, self.height() - 2)
        path = QPainterPath()
        path.addRoundedRect(frame, _RADIUS, _RADIUS)
        painter.setClipPath(path)
        if self.scene.background_color is not None:
            painter.fillPath(path, QColor(*self.scene.background_color))
        elif not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                int(frame.width()),
                int(frame.height()),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = frame.x() + (frame.width() - scaled.width()) / 2
            y = frame.y() + (frame.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)
        else:
            painter.fillPath(path, QColor("#181818"))
        shade = QLinearGradient(frame.bottomLeft(), QPointF(frame.left(), frame.bottom() - 48))
        shade.setColorAt(0.0, QColor(16, 16, 16, 200))
        shade.setColorAt(1.0, QColor(16, 16, 16, 0))
        painter.fillRect(QRectF(frame.left(), frame.bottom() - 48, frame.width(), 48), shade)
        painter.setClipping(False)
        accent = accent_qcolor()
        painter.setPen(QPen(accent if self._selected else QColor(255, 255, 255, 28), 2 if self._selected else 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(QColor("#f3f3f3"))
        name = painter.fontMetrics().elidedText(self.scene.name, Qt.TextElideMode.ElideRight, self.width() - 20)
        painter.drawText(
            QRectF(12, self.height() - 28, self.width() - 24, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            name,
        )


class PlusCard(QWidget):
    clicked = pyqtSignal()

    def __init__(self, strip: "Filmstrip", parent=None) -> None:
        super().__init__(parent)
        self._strip = strip
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("New scene")
        self.setAcceptDrops(True)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:
        self._strip._accept_scene_drag(event)

    def dragMoveEvent(self, event) -> None:
        self._strip._accept_scene_drag(event)

    def dropEvent(self, event) -> None:
        self._strip._drop_event(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(1, 1, self.width() - 2, self.height() - 2)
        path = QPainterPath()
        path.addRoundedRect(frame, _RADIUS, _RADIUS)
        pen = QPen(QColor("#343434"))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor("#141414"))
        painter.drawPath(path)
        painter.setPen(accent_qcolor())
        font = painter.font()
        font.setPointSize(28)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(frame, int(Qt.AlignmentFlag.AlignCenter), "+")


class _DropRow(QWidget):
    def __init__(self, strip: "Filmstrip", parent=None) -> None:
        super().__init__(parent)
        self._strip = strip
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def dragEnterEvent(self, event) -> None:
        self._strip._accept_scene_drag(event)

    def dragMoveEvent(self, event) -> None:
        self._strip._accept_scene_drag(event)

    def dropEvent(self, event) -> None:
        self._strip._drop_event(event)


class Filmstrip(QScrollArea):
    createRequested = pyqtSignal()
    sceneClicked = pyqtSignal(str)
    reordered = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("filmstrip")
        self.setWidgetResizable(True)
        self.setAcceptDrops(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(_CARD_H + 20)
        inner = _DropRow(self)
        self._row = QHBoxLayout(inner)
        self._row.setContentsMargins(4, 4, 12, 4)
        self._row.setSpacing(14)
        self.setWidget(inner)
        self.playlist_bar = PlaylistBar()
        self.plus = PlusCard(self)
        self.plus.clicked.connect(self.createRequested)
        self._cards: list[SceneCard] = []
        self._selected_id = ""
        self.set_scenes([])

    def set_selected(self, scene_id: str) -> None:
        self._selected_id = scene_id
        for card in self._cards:
            card.set_selected(card.scene.id == scene_id)

    def set_scenes(self, scenes: list[Scene]) -> None:
        self.plus.setParent(None)
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.plus:
                widget.deleteLater()
        self._cards = []
        for scene in scenes:
            card = SceneCard(scene, self)
            card.clicked.connect(self.sceneClicked)
            card.set_selected(scene.id == self._selected_id)
            self._cards.append(card)
            self._row.addWidget(card)
        self._row.addWidget(self.plus)
        self._row.addStretch(1)

    def _accept_scene_drag(self, event) -> None:
        if event.mimeData().hasFormat(_SCENE_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragEnterEvent(self, event) -> None:
        self._accept_scene_drag(event)

    def dragMoveEvent(self, event) -> None:
        self._accept_scene_drag(event)

    def dropEvent(self, event) -> None:
        self._drop_event(event)

    def _drop_event(self, event) -> None:
        raw = bytes(event.mimeData().data(_SCENE_MIME)).decode("utf-8")
        if not raw:
            event.ignore()
            return
        event.acceptProposedAction()
        insert_at = self._insert_index(event)
        ids = move_id([card.scene.id for card in self._cards], raw, insert_at)
        if ids != [card.scene.id for card in self._cards]:
            self.reordered.emit(ids)

    def _insert_index(self, event) -> int:
        pos = inner_pos(self, event)
        for index, card in enumerate(self._cards):
            if pos.x() < card.geometry().center().x():
                return index
        return len(self._cards)


def inner_pos(strip: Filmstrip, event) -> QPoint:
    inner = strip.widget()
    if inner is None:
        return event.position().toPoint()
    if hasattr(event, "position"):
        global_pos = strip.mapToGlobal(event.position().toPoint())
    else:
        global_pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
    return inner.mapFromGlobal(global_pos)
