"""Build a hotkey by adding keys one at a time."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from onigiri.hotkey import MODIFIERS, HotkeySpec, parse_hotkey
from onigiri.ui.popup import popup_position

KEYS = (
    ("Ctrl", "Ctrl"),
    ("Alt", "Alt"),
    ("Shift", "Shift"),
    ("Meta", "Meta"),
    ("Middle", "Middle click"),
    ("Right", "Right click"),
    ("Back", "Back"),
    ("Forward", "Forward"),
    ("Space", "Space"),
    ("Return", "Enter"),
    ("Tab", "Tab"),
    ("Backspace", "Backspace"),
    *tuple((letter, letter) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    *tuple((digit, digit) for digit in "0123456789"),
    *tuple((f"F{number}", f"F{number}") for number in range(1, 13)),
)
LABELS = {token: label for token, label in KEYS}


def _qt_key(name: str) -> int | None:
    value = getattr(Qt.Key, name, None)
    return None if value is None else int(value)


_KEY_TOKEN = {
    int(Qt.Key.Key_Control): "Ctrl",
    int(Qt.Key.Key_Alt): "Alt",
    int(Qt.Key.Key_AltGr): "Alt",
    int(Qt.Key.Key_Shift): "Shift",
    int(Qt.Key.Key_Meta): "Meta",
    int(Qt.Key.Key_Space): "Space",
    int(Qt.Key.Key_Return): "Return",
    int(Qt.Key.Key_Enter): "Return",
    int(Qt.Key.Key_Tab): "Tab",
    int(Qt.Key.Key_Backtab): "Tab",
    int(Qt.Key.Key_Backspace): "Backspace",
}
for _name in ("Key_Super_L", "Key_Super_R"):
    _code = _qt_key(_name)
    if _code is not None:
        _KEY_TOKEN[_code] = "Meta"
_MOUSE_TOKEN = {
    Qt.MouseButton.MiddleButton: "Middle",
    Qt.MouseButton.RightButton: "Right",
    Qt.MouseButton.BackButton: "Back",
    Qt.MouseButton.ForwardButton: "Forward",
    Qt.MouseButton.XButton1: "Back",
    Qt.MouseButton.XButton2: "Forward",
}


def token_from_key(key: int) -> str | None:
    code = int(key)
    token = _KEY_TOKEN.get(code)
    if token:
        return token
    if int(Qt.Key.Key_A) <= code <= int(Qt.Key.Key_Z):
        return chr(code)
    if int(Qt.Key.Key_0) <= code <= int(Qt.Key.Key_9):
        return chr(code)
    if int(Qt.Key.Key_F1) <= code <= int(Qt.Key.Key_F12):
        return f"F{code - int(Qt.Key.Key_F1) + 1}"
    return None


def token_from_mouse(button: Qt.MouseButton) -> str | None:
    return _MOUSE_TOKEN.get(button)


class KeyPicker(QWidget):
    """List at the pointer that records a key or mouse button, or Escape to cancel."""

    tokenPicked = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("hotkeyTrigger")
        self._capturing = False
        self._picked = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setObjectName("hotkeyTriggerList")
        for token, label in KEYS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, token)
            self.list.addItem(item)
        self.list.itemClicked.connect(self._on_item)
        layout.addWidget(self.list)
        self.setFixedSize(220, 280)

    def findData(self, token: str) -> int:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item is not None and str(item.data(Qt.ItemDataRole.UserRole) or "") == token:
                return row
        return -1

    def show_at(self, pos: QPoint) -> None:
        self._picked = False
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else self.rect()
        self.move(popup_position(pos, self.size(), area))
        self.show()
        self.list.setFocus()
        self._set_capturing(True)

    def hideEvent(self, event) -> None:
        self._set_capturing(False)
        if not self._picked:
            self.cancelled.emit()
        super().hideEvent(event)

    def _on_item(self, item: QListWidgetItem) -> None:
        self._pick(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _pick(self, token: str) -> None:
        if self._picked or not token:
            return
        self._picked = True
        self.tokenPicked.emit(token)
        self.hide()

    def _set_capturing(self, on: bool) -> None:
        app = QApplication.instance()
        if on and not self._capturing:
            if app is not None:
                app.installEventFilter(self)
            self.grabKeyboard()
            self._capturing = True
            return
        if not on and self._capturing:
            if app is not None:
                app.removeEventFilter(self)
            self.releaseKeyboard()
            self._capturing = False

    def eventFilter(self, watched, event) -> bool:
        if not self._capturing:
            return False
        kind = event.type()
        if kind == QEvent.Type.KeyPress and int(event.key()) == int(Qt.Key.Key_Escape):
            self.hide()
            return True
        if kind == QEvent.Type.ShortcutOverride and hasattr(event, "key"):
            if int(event.key()) == int(Qt.Key.Key_Escape) or token_from_key(event.key()):
                event.accept()
                return True
        if kind == QEvent.Type.KeyPress:
            if event.isAutoRepeat():
                return True
            token = token_from_key(event.key())
            if token:
                self._pick(token)
                return True
        if kind == QEvent.Type.MouseButtonPress:
            token = token_from_mouse(event.button())
            if token:
                self._pick(token)
                return True
        return False


class _Chip(QFrame):
    removed = pyqtSignal()

    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("hotkeyChip")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 2, 2)
        row.setSpacing(2)
        label = QLabel(name)
        button = QToolButton()
        button.setObjectName("chipRemove")
        button.setText("×")
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Remove " + name)
        button.clicked.connect(lambda: self.removed.emit())
        row.addWidget(label)
        row.addWidget(button)


class HotkeyBuilder(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tokens: list[str] = []
        self._picker: KeyPicker | None = None
        self.setObjectName("hotkeyEdit")
        self.setToolTip("Press + to add a key, then press it or pick from the list. Escape cancels.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._chip_row = QHBoxLayout()
        self._chip_row.setContentsMargins(0, 0, 0, 0)
        self._chip_row.setSpacing(6)
        self.add_button = QToolButton()
        self.add_button.setObjectName("addButton")
        self.add_button.setText("+")
        self.add_button.setToolTip("Add a key")
        self.add_button.setAutoRaise(True)
        self.add_button.clicked.connect(self._start_add)
        self._chip_row.addWidget(self.add_button)
        self._chip_row.addStretch(1)
        layout.addLayout(self._chip_row)

    def spec(self) -> HotkeySpec:
        modifiers = tuple(name for name in MODIFIERS if name in self._tokens)
        trigger = ""
        for token in self._tokens:
            if token not in MODIFIERS:
                trigger = token
        return HotkeySpec(modifiers, trigger)

    def text(self) -> str:
        return self.spec().to_text()

    def set_text(self, text: str) -> None:
        self.set_spec(parse_hotkey(text))

    def set_spec(self, spec: HotkeySpec) -> None:
        self._abort_add()
        self._tokens = [name for name in MODIFIERS if name in spec.modifiers]
        if spec.trigger:
            self._tokens.append(spec.trigger)
        self._rebuild_chips()

    def _start_add(self) -> None:
        if self._picker is not None:
            self._picker.show_at(QCursor.pos())
            return
        picker = KeyPicker(self)
        picker.tokenPicked.connect(self._commit_token)
        picker.cancelled.connect(self._abort_add)
        self._picker = picker
        picker.show_at(QCursor.pos())

    def _commit_token(self, token: str) -> None:
        if not token:
            self._abort_add()
            return
        if token in MODIFIERS:
            if token not in self._tokens:
                self._tokens.append(token)
            self._tokens = [name for name in MODIFIERS if name in self._tokens] + [
                item for item in self._tokens if item not in MODIFIERS
            ]
        else:
            self._tokens = [item for item in self._tokens if item in MODIFIERS]
            self._tokens.append(token)
        self._remove_picker()
        self._rebuild_chips()
        self._emit_changed()

    def _abort_add(self) -> None:
        self._remove_picker()

    def _remove_token(self, token: str) -> None:
        self._abort_add()
        self._tokens = [item for item in self._tokens if item != token]
        self._rebuild_chips()
        self._emit_changed()

    def _remove_picker(self) -> None:
        picker = self._picker
        if picker is None:
            return
        self._picker = None
        picker.blockSignals(True)
        picker.hide()
        picker.deleteLater()

    def _rebuild_chips(self) -> None:
        while self._chip_row.count():
            item = self._chip_row.takeAt(0)
            widget = item.widget()
            if widget is self.add_button:
                continue
            if widget is not None:
                widget.deleteLater()
        for token in self._tokens:
            chip = _Chip(LABELS.get(token, token))
            chip.removed.connect(lambda chosen=token: self._remove_token(chosen))
            self._chip_row.addWidget(chip)
        self._chip_row.addWidget(self.add_button)
        self._chip_row.addStretch(1)
        self.add_button.setVisible(self._picker is None)

    def _emit_changed(self) -> None:
        self.changed.emit()
