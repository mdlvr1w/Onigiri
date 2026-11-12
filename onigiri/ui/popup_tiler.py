"""Popup overlay tiler: edits sit above a saved layout, never inside it."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPen, QPixmap
from pathlib import Path
from PyQt6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from onigiri.geometry import Rect, SplitGuide, clamp_ratio, compute_layout
from onigiri.launch import DesktopApp, assign_app, running_applications
from onigiri.model import (
    Leaf,
    Node,
    ScreenLayout,
    Split,
    Tile,
    can_combine,
    combine_leaf,
    delete_tile as clear_leaf,
    fill_slot,
    find_leaf,
    is_vacant_root,
    iter_tiles,
    new_id,
    prune_empty_root,
    split_leaf,
)
from onigiri.ui.canvas import MonitorView, add_split_actions

_GREEN = QColor("#1f8a52")
_RED = QColor("#c23b22")
_TILE = QColor(247, 248, 250, 108)
_SLOP = 6.0


def release_overlay_chrome(tile: Tile) -> Tile:
    tile.skip_taskbar = False
    tile.show_title = True
    tile.no_border = False
    return tile


def release_overlay_tree(root) -> None:
    if root is None:
        return
    for tile in iter_tiles(root):
        release_overlay_chrome(tile)


def overlay_tile(name: str = "Tile 1") -> Tile:
    return release_overlay_chrome(Tile(id=new_id(), name=name))


def apply_app(tile: Tile, app: DesktopApp) -> None:
    assign_app(tile, app)
    release_overlay_chrome(tile)


def app_icon(app: DesktopApp) -> QIcon:
    if app.icon and Path(app.icon).is_file():
        return QIcon(app.icon)
    if app.icon:
        icon = QIcon.fromTheme(app.icon)
        if not icon.isNull():
            return icon
    return QIcon.fromTheme("application-x-executable")


_ADD = "__add__"


class AppPicker(QWidget):
    appChosen = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._installed: list[DesktopApp] = []
        self._running: list[DesktopApp] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("popupAppSearch")
        self.search.setPlaceholderText("Search applications")
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._accept_current)
        self.search.installEventFilter(self)
        self.results = QListWidget()
        self.results.setObjectName("popupAppList")
        self.results.setIconSize(QSize(24, 24))
        self.results.setSpacing(2)
        self.results.itemClicked.connect(self._chosen)
        self.results.itemActivated.connect(self._chosen)
        self.running_list = self.results
        layout.addWidget(self.search)
        layout.addWidget(self.results, 1)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self.search)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                if self.results.count() == 0:
                    return True
                if self.results.currentRow() < 0:
                    self.results.setCurrentRow(0 if key != Qt.Key.Key_Up else self.results.count() - 1)
                    return True
                self.results.keyPressEvent(event)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_current()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.handle_escape():
                return
            self.hide()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept_current()
            return
        super().keyPressEvent(event)

    def set_apps(self, apps: list[DesktopApp]) -> None:
        self._installed = list(apps)

    def prepare(self, running: list[DesktopApp] | None = None) -> None:
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._running = list(running if running is not None else running_applications(self._installed))
        self._fill(self._running)
        self._highlight_first()
        self.focus_search()

    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.OtherFocusReason)

    def handle_escape(self) -> bool:
        if self.search.text():
            self.search.clear()
            return True
        return False

    def _fill(self, apps: list[DesktopApp]) -> None:
        self.results.clear()
        for app in apps:
            item = QListWidgetItem(app_icon(app), app.name)
            item.setData(Qt.ItemDataRole.UserRole, app)
            self.results.addItem(item)

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        if not query:
            self._fill(self._running)
            self._highlight_first()
            return
        matches: list[DesktopApp] = []
        for app in self._installed:
            haystack = f"{app.name} {app.app_id} {app.command}".casefold()
            if query not in haystack:
                continue
            matches.append(app)
            if len(matches) >= 40:
                break
        self._fill(matches)
        self._highlight_first()

    def _highlight_first(self) -> None:
        if self.results.count():
            self.results.setCurrentRow(0)

    def _accept_current(self) -> None:
        item = self.results.currentItem()
        if item is None and self.results.count():
            item = self.results.item(0)
            self.results.setCurrentRow(0)
        if item is not None:
            self._chosen(item)

    def _chosen(self, item: QListWidgetItem) -> None:
        app = item.data(Qt.ItemDataRole.UserRole)
        if app is not None and app != _ADD:
            self.appChosen.emit(app)


class PopupTiler(QWidget):
    overlayChanged = pyqtSignal()
    pickApp = pyqtSignal(str)
    tileRemoved = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("overlayTiler")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._view: MonitorView | None = None
        self._overlay = ScreenLayout()
        self._hover = False
        self._press: dict | None = None
        self._drag: dict | None = None

    @property
    def overlay(self) -> ScreenLayout:
        return self._overlay

    def set_scene(self, view: MonitorView | None, overlay: ScreenLayout | None, wallpaper: QPixmap | None = None) -> None:
        self._view = view
        if overlay is not None:
            self._overlay = overlay
            release_overlay_tree(overlay.root)
        elif view is not None:
            self._overlay = ScreenLayout(x=view.x, y=view.y, width=view.width, height=view.height, gap=view.gap)
        else:
            self._overlay = ScreenLayout()
        self._hover = False
        self._press = None
        self._drag = None
        self.update()

    def overlay_root(self) -> Node | None:
        root = self._overlay.root
        return None if is_vacant_root(root) else root

    def _area(self) -> Rect:
        view = self._view
        if view is None:
            return Rect(0, 0, max(1, self.width()), max(1, self.height()))
        return Rect(int(view.x), int(view.y), max(1, int(view.width)), max(1, int(view.height)))

    def _target(self) -> QRectF:
        return QRectF(self.rect()).adjusted(1, 1, -1, -1)

    def _map_point(self, x: float, y: float) -> QPointF:
        area = self._area()
        target = self._target()
        return QPointF(
            target.x() + (x - area.x) * target.width() / area.width,
            target.y() + (y - area.y) * target.height() / area.height,
        )

    def _map_box(self, rect: Rect) -> QRectF:
        area = self._area()
        target = self._target()
        return QRectF(
            target.x() + (rect.x - area.x) * target.width() / area.width,
            target.y() + (rect.y - area.y) * target.height() / area.height,
            max(2.0, rect.width * target.width() / area.width),
            max(2.0, rect.height * target.height() / area.height),
        )

    def _local(self, pos) -> tuple[float, float]:
        area = self._area()
        target = self._target()
        if target.width() <= 0 or target.height() <= 0:
            return 0.0, 0.0
        return (
            area.x + (pos.x() - target.x()) * area.width / target.width(),
            area.y + (pos.y() - target.y()) * area.height / target.height(),
        )

    def _geometry(self):
        root = self.overlay_root()
        if root is None:
            return None
        return compute_layout(root, self._area(), max(0, int(self._overlay.gap)))

    def _tile_at(self, x: float, y: float) -> Tile | None:
        geometry = self._geometry()
        if geometry is None:
            return None
        for tile, rect in reversed(geometry.tiles):
            if rect.x <= x <= rect.x + rect.width and rect.y <= y <= rect.y + rect.height:
                return tile
        return None

    def _slot_at(self, x: float, y: float) -> str | None:
        geometry = self._geometry()
        if geometry is None:
            return None
        for slot, rect in geometry.slots:
            if rect.x <= x <= rect.x + rect.width and rect.y <= y <= rect.y + rect.height:
                return slot
        return None

    def _guide_at(self, x: float, y: float) -> SplitGuide | None:
        geometry = self._geometry()
        if geometry is None:
            return None
        target = self._target()
        area = self._area()
        scale = target.width() / area.width if area.width else 1
        tolerance = 8.0 / scale if scale else 8.0
        for guide in geometry.splits:
            if guide.orientation == "v":
                if guide.y1 - tolerance <= y <= guide.y2 + tolerance and abs(x - guide.x1) <= tolerance:
                    return guide
            elif guide.x1 - tolerance <= x <= guide.x2 + tolerance and abs(y - guide.y1) <= tolerance:
                return guide
        return None

    def _plus_box(self, rect: Rect | None = None) -> QRectF:
        box = self._map_box(rect) if rect is not None else self._target()
        size = max(12.0, min(22.0, min(box.width(), box.height()) * 0.12))
        return QRectF(box.center().x() - size / 2, box.center().y() - size / 2, size, size)

    def _paint_mark(self, painter: QPainter, box: QRectF, minus: bool) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_RED if minus else _GREEN)
        thickness = max(1.5, box.width() * 0.14)
        span = box.width()
        cx, cy = box.center().x(), box.center().y()
        painter.drawRect(QRectF(cx - span / 2, cy - thickness / 2, span, thickness))
        if not minus:
            painter.drawRect(QRectF(cx - thickness / 2, cy - span / 2, thickness, span))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if self._view is None:
            painter.end()
            return
        target = self._target()
        geometry = self._geometry()
        if geometry is not None:
            for tile, rect in geometry.tiles:
                box = self._map_box(rect).adjusted(1, 1, -1, -1)
                painter.setPen(QPen(_GREEN, 2))
                painter.setBrush(_TILE)
                painter.drawRect(box)
                painter.setPen(QColor("#1a1a1a"))
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
                box = self._map_box(rect).adjusted(1, 1, -1, -1)
                painter.setPen(QPen(_GREEN, 2))
                painter.setBrush(_TILE)
                painter.drawRect(box)
                if self._hover:
                    self._paint_mark(painter, self._plus_box(rect), False)
            drag = self._drag
            if drag and drag.get("moved"):
                node = drag.get("node")
                for guide in geometry.splits:
                    if guide.node is not node:
                        continue
                    painter.setPen(QPen(_GREEN, 1))
                    painter.drawLine(self._map_point(guide.x1, guide.y1), self._map_point(guide.x2, guide.y2))
        elif self._hover:
            self._paint_mark(painter, self._plus_box(), False)
        painter.setPen(QPen(_GREEN, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(target.adjusted(1, 1, -1, -1))
        painter.end()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _emit(self) -> None:
        self.update()
        self.overlayChanged.emit()

    def add_first(self) -> Tile:
        tile = overlay_tile("Tile 1")
        self._overlay.root = Leaf(tile)
        self._emit()
        return tile

    def _remove(self, tile_id: str) -> None:
        if self._overlay.root is None:
            return
        leaf = find_leaf(self._overlay.root, tile_id)
        tile = None if leaf is None else leaf.tile
        if not clear_leaf(self._overlay.root, tile_id):
            return
        self._overlay.root = prune_empty_root(self._overlay.root)
        if tile is not None:
            self.tileRemoved.emit(tile)
        self._emit()

    def mousePressEvent(self, event) -> None:
        if self._view is None:
            super().mousePressEvent(event)
            return
        x, y = self._local(event.position())
        if event.button() == Qt.MouseButton.MiddleButton:
            tile = self._tile_at(x, y)
            if tile is not None:
                self._remove(tile.id)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self.overlay_root() is None:
            self.add_first()
            event.accept()
            return
        slot = self._slot_at(x, y)
        if slot is not None:
            tile = fill_slot(self._overlay.root, slot)
            if tile is not None:
                release_overlay_chrome(tile)
                self._emit()
            event.accept()
            return
        tile = self._tile_at(x, y)
        guide = self._guide_at(x, y) if tile is None else None
        self._press = {"pos": QPointF(event.position()), "tile": tile, "guide": guide}
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None:
            start: QPointF = self._press["pos"]
            delta = QPointF(event.position()) - start
            if abs(delta.x()) > _SLOP or abs(delta.y()) > _SLOP:
                guide = self._press.get("guide")
                self._press = None
                if guide is not None:
                    self._drag = {
                        "node": guide.node,
                        "vertical": guide.orientation == "v",
                        "parent_start": guide.parent_x if guide.orientation == "v" else guide.parent_y,
                        "parent_size": guide.parent_w if guide.orientation == "v" else guide.parent_h,
                        "moved": False,
                    }
                    self._move_split(event)
            event.accept()
            return
        if self._drag:
            self._move_split(event)
            event.accept()
            return
        x, y = self._local(event.position())
        guide = self._guide_at(x, y)
        self.setCursor(
            Qt.CursorShape.SplitHCursor
            if guide and guide.orientation == "v"
            else Qt.CursorShape.SplitVCursor
            if guide
            else Qt.CursorShape.ArrowCursor
        )
        self.update()
        super().mouseMoveEvent(event)

    def _move_split(self, event) -> None:
        drag = self._drag
        if not drag:
            return
        drag["moved"] = True
        x, y = self._local(event.position())
        pos = x if drag["vertical"] else y
        drag["node"].ratio = clamp_ratio(drag["parent_start"], drag["parent_size"], pos)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        press = self._press
        self._press = None
        drag = self._drag
        self._drag = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if event.button() != Qt.MouseButton.LeftButton:
            event.accept()
            return
        if press is not None and press.get("tile") is not None:
            self.pickApp.emit(press["tile"].id)
            event.accept()
            return
        if drag and drag.get("moved"):
            self.overlayChanged.emit()
        event.accept()

    def contextMenuEvent(self, event) -> None:
        if self._view is None:
            return
        host = self.window()
        hold = getattr(host, "hold_dismiss", None)
        if callable(hold):
            hold()
        x, y = self._local(event.pos())
        root = self.overlay_root()
        menu = QMenu(self)
        if root is None:
            menu.addAction("Add tile", self.add_first)
            menu.exec(event.globalPos())
            if callable(hold):
                hold()
            return
        guide = self._guide_at(x, y)
        tile = self._tile_at(x, y)
        if guide is not None and isinstance(guide.node, Split):
            if isinstance(guide.node.first, Leaf) and isinstance(guide.node.second, Leaf):
                keeper = guide.node.first if guide.node.first.tile is not None else guide.node.second
                if keeper.tile is not None:
                    leaf_id = keeper.tile.id
                    menu.addAction("Combine tiles", lambda: self._combine(leaf_id))
        if tile is not None:
            add_split_actions(menu, lambda orientation, current=tile.id: self._split(current, orientation))
            if can_combine(root, tile.id):
                menu.addAction("Combine", lambda: self._combine(tile.id))
            menu.addAction("Remove tile", lambda current=tile: self._remove(current.id))
        if menu.actions():
            menu.exec(event.globalPos())
        if callable(hold):
            hold()
            host.activateWindow()

    def _split(self, tile_id: str, orientation: str) -> None:
        result = split_leaf(self._overlay.root, tile_id, orientation)
        if result is None:
            return
        self._overlay.root, created = result
        leaf = find_leaf(self._overlay.root, created)
        if leaf is not None and leaf.tile is not None:
            release_overlay_chrome(leaf.tile)
        self._emit()

    def _combine(self, tile_id: str) -> None:
        new_root = combine_leaf(self._overlay.root, tile_id)
        if new_root is None:
            return
        self._overlay.root = new_root
        self._emit()

    def assign_app(self, tile_id: str, app: DesktopApp) -> None:
        leaf = find_leaf(self._overlay.root, tile_id)
        if leaf is None or leaf.tile is None:
            return
        apply_app(leaf.tile, app)
        self._emit()
