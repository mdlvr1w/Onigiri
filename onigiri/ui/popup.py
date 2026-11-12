"""Small popup at the pointer."""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
import time
from dataclasses import replace

from PyQt6.QtCore import QEvent, QEventLoop, QObject, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QCursor, QGuiApplication, QWindow
from PyQt6.QtWidgets import QApplication, QGridLayout, QMenu, QVBoxLayout, QWidget

from onigiri.launch import DesktopApp
from onigiri.model import ScreenLayout
from onigiri.ui.canvas import MonitorView, preview_size
from onigiri.ui.popup_tiler import AppPicker, PopupTiler

POPUP_SIZE = QSize(240, 160)
_PICKER_SIZE = QSize(280, 320)
_SERVICE = "org.onigiri.Cursor"
_PATH = "/cursor"
_IFACE = "org.onigiri.Cursor"
_SCRIPT_NAME = "onigiri-cursor"


def popup_position(cursor: QPoint, size: QSize, area: QRect) -> QPoint:
    width = min(size.width(), max(1, area.width()))
    height = min(size.height(), max(1, area.height()))
    x = min(max(cursor.x() - width // 2, area.x()), area.x() + area.width() - width)
    y = min(max(cursor.y() - height // 2, area.y()), area.y() + area.height() - height)
    return QPoint(x, y)


def _platform() -> str:
    app = QApplication.instance()
    return app.platformName() if app is not None else ""


def _is_wayland() -> bool:
    return _platform() == "wayland"


class _CursorSink(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.point: QPoint | None = None
        self._loop: QEventLoop | None = None

    @pyqtSlot(int, int)
    def set(self, x: int, y: int) -> None:
        self.point = QPoint(int(x), int(y))
        if self._loop is not None:
            self._loop.quit()


_sink: _CursorSink | None = None


def _ensure_sink() -> _CursorSink | None:
    global _sink
    if _sink is not None:
        return _sink
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    bus.registerService(_SERVICE)
    sink = _CursorSink()
    ok = bus.registerObject(_PATH, _IFACE, sink, QDBusConnection.RegisterOption.ExportAllSlots)
    if not ok:
        return None
    _sink = sink
    return _sink


def _gdbus(*args: str) -> str:
    try:
        ran = subprocess.run(
            ["gdbus", "call", "--session", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""
    if ran.returncode != 0:
        return ""
    return ran.stdout.strip()


def _kwin_cursor() -> QPoint | None:
    if not _is_wayland():
        return None
    sink = _ensure_sink()
    if sink is None:
        return None
    sink.point = None
    script = os.path.join(tempfile.gettempdir(), f"onigiri-cursor-{os.getuid()}.js")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(
            f'callDBus("{_SERVICE}", "{_PATH}", "{_IFACE}", "set", '
            "workspace.cursorPos.x, workspace.cursorPos.y);\n"
        )
    _gdbus("--dest", "org.kde.KWin", "--object-path", "/Scripting", "--method", "org.kde.kwin.Scripting.unloadScript", _SCRIPT_NAME)
    loaded = _gdbus(
        "--dest",
        "org.kde.KWin",
        "--object-path",
        "/Scripting",
        "--method",
        "org.kde.kwin.Scripting.loadScript",
        script,
        _SCRIPT_NAME,
    )
    sid = "".join(ch for ch in loaded if ch.isdigit())
    if not sid:
        return None
    loop = QEventLoop()
    sink._loop = loop
    QTimer.singleShot(250, loop.quit)
    _gdbus(
        "--dest",
        "org.kde.KWin",
        "--object-path",
        f"/Scripting/Script{sid}",
        "--method",
        "org.kde.kwin.Script.run",
    )
    if sink.point is None:
        loop.exec()
    sink._loop = None
    _gdbus("--dest", "org.kde.KWin", "--object-path", "/Scripting", "--method", "org.kde.kwin.Scripting.unloadScript", _SCRIPT_NAME)
    return sink.point


def cursor_point() -> QPoint:
    point = _kwin_cursor()
    return QPoint(point) if point is not None else QCursor.pos()


class _Margins(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_int),
        ("top", ctypes.c_int),
        ("right", ctypes.c_int),
        ("bottom", ctypes.c_int),
    )


class _LayerShell:
    _ANCHOR_TOP = 1
    _ANCHOR_BOTTOM = 2
    _ANCHOR_LEFT = 4
    _ANCHOR_RIGHT = 8
    _LAYER_OVERLAY = 3
    _KEYBOARD_ON_DEMAND = 2

    def __init__(self, window) -> None:
        self._ok = False
        self._ptr = 0
        self._obj: QObject | None = None
        try:
            from PyQt6 import sip
        except ImportError:
            return
        try:
            lib = ctypes.CDLL("libLayerShellQtInterface.so.6")
        except OSError:
            return
        get = lib._ZN12LayerShellQt6Window3getEP7QWindow
        get.restype = ctypes.c_void_p
        get.argtypes = [ctypes.c_void_p]
        ptr = get(ctypes.c_void_p(sip.unwrapinstance(window)))
        if not ptr:
            return
        self._ptr = int(ptr)
        self._lib = lib
        self._obj = sip.wrapinstance(ptr, QObject)
        self._obj.setProperty("layer", self._LAYER_OVERLAY)
        self._obj.setProperty("anchors", self._ANCHOR_TOP | self._ANCHOR_LEFT)
        self._obj.setProperty("keyboardInteractivity", self._KEYBOARD_ON_DEMAND)
        self._obj.setProperty("scope", "onigiri-popup")
        self._obj.setProperty("wantsToBeOnActiveScreen", False)
        self._ok = True

    @property
    def ok(self) -> bool:
        return self._ok

    def set_exclusive(self, on: bool) -> None:
        if self._obj is not None:
            self._obj.setProperty("keyboardInteractivity", 1 if on else self._KEYBOARD_ON_DEMAND)

    def place(self, screen, top_left: QPoint) -> None:
        if not self._ok or self._obj is None:
            return
        self._obj.setProperty("screen", screen)
        self._obj.setProperty("wantsToBeOnActiveScreen", False)
        self._obj.setProperty("anchors", self._ANCHOR_TOP | self._ANCHOR_LEFT)
        self._set_margins(max(0, int(top_left.x())), max(0, int(top_left.y())), 0, 0)

    def fill(self, screen) -> None:
        if not self._ok or self._obj is None:
            return
        self._obj.setProperty("screen", screen)
        self._obj.setProperty("wantsToBeOnActiveScreen", False)
        self._obj.setProperty("exclusiveZone", 0)
        self._obj.setProperty(
            "anchors",
            self._ANCHOR_TOP | self._ANCHOR_BOTTOM | self._ANCHOR_LEFT | self._ANCHOR_RIGHT,
        )
        self._set_margins(0, 0, 0, 0)

    def _set_margins(self, left: int, top: int, right: int, bottom: int) -> None:
        set_margins = self._lib._ZN12LayerShellQt6Window10setMarginsERK8QMargins
        set_margins.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        margins = _Margins(left, top, right, bottom)
        set_margins(self._ptr, ctypes.byref(margins))


class CursorPopup(QWidget):
    overlayChanged = pyqtSignal(str, object)
    closed = pyqtSignal()
    tileRemoved = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        wayland = _is_wayland()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        flags |= Qt.WindowType.Tool if wayland else Qt.WindowType.Popup
        super().__init__(None if wayland else parent, flags)
        self.setObjectName("cursorPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFixedSize(POPUP_SIZE)
        self._layer: _LayerShell | None = None
        self._armed = False
        self._filter = False
        self._hold_until = 0.0
        self._view: MonitorView | None = None
        self._pick_tile = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        holder.setAutoFillBackground(False)
        self._holder = holder
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self.tiler = PopupTiler()
        grid.addWidget(self.tiler, 0, 0)
        layout.addWidget(holder)
        self.picker = AppPicker(holder)
        self.picker.setObjectName("appPickerPopup")
        self.picker.setFixedSize(_PICKER_SIZE)
        self.picker.hide()
        self.tiler.overlayChanged.connect(self._emit_overlay)
        self.tiler.pickApp.connect(self._show_apps)
        self.tiler.tileRemoved.connect(self.tileRemoved)
        self.picker.appChosen.connect(self._apply_app)
        if wayland:
            self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            self.winId()
            handle = self.windowHandle()
            if handle is not None:
                layer = _LayerShell(handle)
                if layer.ok:
                    self._layer = layer

    @property
    def monitor_name(self) -> str:
        return "" if self._view is None else self._view.name

    def show_at(
        self,
        cursor: QPoint | None = None,
        view: MonitorView | None = None,
        overlay: ScreenLayout | None = None,
        apps: list[DesktopApp] | None = None,
        fill_screen: bool = False,
    ) -> None:
        point = QPoint(cursor) if cursor is not None else cursor_point()
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        if fill_screen and screen is not None:
            geo = screen.availableGeometry()
            if view is not None:
                view = replace(
                    view,
                    x=int(geo.x()),
                    y=int(geo.y()),
                    width=max(1, int(geo.width())),
                    height=max(1, int(geo.height())),
                )
            else:
                view = MonitorView(
                    screen.name() or "",
                    int(geo.x()),
                    int(geo.y()),
                    max(1, int(geo.width())),
                    max(1, int(geo.height())),
                    None,
                )
        self._view = view
        self.tiler.set_scene(view, overlay, None)
        self.picker.set_apps(apps or [])
        self._hide_picker()
        if fill_screen:
            self._show_fullscreen(point, view, screen)
        else:
            self._show_mini(point, view, screen)
        self._armed = False
        self._listen(True)
        if self._layer is not None:
            self._layer.set_exclusive(True)
        self.show()
        self.raise_()
        self.activateWindow()
        if fill_screen and self._layer is not None and screen is not None:
            self._layer.fill(screen)
        QTimer.singleShot(0, self._arm)

    def _clear_fixed_size(self) -> None:
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)

    def _show_mini(self, point: QPoint, view: MonitorView | None, screen) -> None:
        size = preview_size(view.width, view.height) if view is not None else POPUP_SIZE
        self.setFixedSize(size)
        area = screen.availableGeometry() if screen is not None else QRect(point, size)
        pinned = popup_position(point, size, area)
        self.move(pinned)
        if self._layer is not None and screen is not None:
            origin = screen.geometry().topLeft()
            self._layer.place(
                screen,
                QPoint(pinned.x() - origin.x(), pinned.y() - origin.y()),
            )

    def _show_fullscreen(self, point: QPoint, view: MonitorView | None, screen) -> None:
        if screen is not None:
            area = screen.availableGeometry()
        elif view is not None:
            area = QRect(int(view.x), int(view.y), max(1, int(view.width)), max(1, int(view.height)))
        else:
            area = QRect(point, POPUP_SIZE)
        self._clear_fixed_size()
        self.setGeometry(area)
        if self._layer is not None and screen is not None:
            self._layer.fill(screen)

    def _emit_overlay(self) -> None:
        if self._view is None:
            return
        root = self.tiler.overlay_root()
        layout = None if root is None else self.tiler.overlay
        self.overlayChanged.emit(self._view.name, layout)

    def _show_apps(self, tile_id: str) -> None:
        self._pick_tile = tile_id
        self.picker.prepare()
        self._place_picker()
        self.picker.show()
        self.picker.raise_()
        self.picker.focus_search()
        QTimer.singleShot(0, self.picker.focus_search)

    def _place_picker(self) -> None:
        parent = self.picker.parentWidget() or self
        cursor = parent.mapFromGlobal(QCursor.pos())
        width = min(_PICKER_SIZE.width(), max(120, parent.width() - 16))
        height = min(_PICKER_SIZE.height(), max(120, parent.height() - 16))
        self.picker.setFixedSize(width, height)
        x = min(max(cursor.x() - width // 2, 8), max(8, parent.width() - width - 8))
        y = min(max(cursor.y() - height // 2, 8), max(8, parent.height() - height - 8))
        self.picker.move(x, y)
        self.picker.raise_()

    def _hide_picker(self) -> None:
        self.picker.hide()

    def _apply_app(self, app: DesktopApp) -> None:
        if self._pick_tile:
            self.tiler.assign_app(self._pick_tile, app)
        self._pick_tile = ""
        self._hide_picker()

    def _arm(self) -> None:
        if self.isVisible():
            self._armed = True

    def hold_dismiss(self, ms: int = 400) -> None:
        self._hold_until = time.monotonic() + max(0, ms) / 1000.0

    def _held(self) -> bool:
        return time.monotonic() < self._hold_until or self._menu_open()

    def _menu_open(self) -> bool:
        app = QApplication.instance()
        if app is None:
            return False
        return any(isinstance(widget, QMenu) and widget.isVisible() for widget in app.topLevelWidgets())

    def _owns(self, watched) -> bool:
        if watched is self or watched is self.windowHandle():
            return True
        if isinstance(watched, QMenu):
            return True
        if isinstance(watched, QWidget) and (self._inside(watched) or isinstance(watched.window(), QMenu)):
            return True
        handle = watched if isinstance(watched, QWindow) else None
        app = QApplication.instance()
        if app is None:
            return False
        for widget in app.topLevelWidgets():
            if not isinstance(widget, QMenu) or not widget.isVisible():
                continue
            if watched is widget or watched is widget.windowHandle():
                return True
            if handle is not None and widget.windowHandle() is handle:
                return True
            if isinstance(watched, QWidget) and widget.isAncestorOf(watched):
                return True
        return False

    def _listen(self, on: bool) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if on and not self._filter:
            app.installEventFilter(self)
            app.applicationStateChanged.connect(self._on_app_state)
            self._filter = True
            return
        if not on and self._filter:
            app.removeEventFilter(self)
            try:
                app.applicationStateChanged.disconnect(self._on_app_state)
            except TypeError:
                pass
            self._filter = False

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if not self._armed or not self.isVisible():
            return
        if state == Qt.ApplicationState.ApplicationActive:
            return
        if self._held():
            return
        QTimer.singleShot(0, self._dismiss_if_background)

    def _dismiss_if_background(self) -> None:
        if not self._armed or not self.isVisible() or self._held():
            return
        app = QApplication.instance()
        if app is not None and app.applicationState() == Qt.ApplicationState.ApplicationActive:
            return
        self.hide()

    def _inside(self, widget) -> bool:
        current = widget
        while isinstance(current, QWidget):
            if current is self:
                return True
            current = current.parentWidget()
        return False

    def _inside_widget(self, watched, target: QWidget) -> bool:
        current = watched if isinstance(watched, QWidget) else None
        while isinstance(current, QWidget):
            if current is target:
                return True
            current = current.parentWidget()
        return False

    def _event_global_pos(self, event) -> QPoint | None:
        if hasattr(event, "globalPosition"):
            try:
                return event.globalPosition().toPoint()
            except TypeError:
                pass
        if hasattr(event, "globalPos"):
            try:
                return event.globalPos()
            except TypeError:
                pass
        return None

    def _picker_contains(self, event) -> bool:
        if not self.picker.isVisible():
            return False
        pos = self._event_global_pos(event)
        if pos is None:
            return False
        return self.picker.rect().contains(self.picker.mapFromGlobal(pos))

    def eventFilter(self, watched, event) -> bool:
        if not self._armed or not self.isVisible():
            return False
        if event.type() != QEvent.Type.MouseButtonPress:
            return False
        if self.picker.isVisible():
            if self._picker_contains(event) or self._inside_widget(watched, self.picker):
                return False
            if self._owns(watched) or watched is self or watched is self.windowHandle():
                self._hide_picker()
                return True
        if self._held() or self._owns(watched):
            return False
        self.hide()
        return False

    def hideEvent(self, event) -> None:
        was_open = self._armed
        self._armed = False
        self._listen(False)
        self._hide_picker()
        if self._layer is not None:
            self._layer.set_exclusive(False)
        super().hideEvent(event)
        if was_open:
            self.closed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.picker.isVisible():
                if self.picker.handle_escape():
                    return
                self._hide_picker()
                return
            self.hide()
            return
        super().keyPressEvent(event)
