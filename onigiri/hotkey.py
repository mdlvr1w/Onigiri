"""App-wide hotkey: keyboard via KGlobalAccel, mouse combos via evdev."""

from __future__ import annotations

import os
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QSocketNotifier, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication

SERVICE = "org.kde.kglobalaccel"
MANAGER = "/kglobalaccel"
MANAGER_IFACE = "org.kde.KGlobalAccel"
COMPONENT_IFACE = "org.kde.kglobalaccel.Component"
COMPONENT = "onigiri"
ACTION = "show-popup"
FRIENDLY_APP = "Onigiri"
FRIENDLY_ACTION = "Show popup"
_NO_AUTOLOAD = "4"

MODIFIERS = ("Ctrl", "Alt", "Shift", "Meta")
MOUSE_TRIGGERS = ("Middle", "Left", "Right", "Back", "Forward")
_EVENT = struct.Struct("llHHi")
_EV_KEY = 1
_BTN = {
    "Left": 0x110,
    "Right": 0x111,
    "Middle": 0x112,
    "Back": 0x113,
    "Forward": 0x114,
}
_MOD_CODES = {
    "Ctrl": {29, 97},
    "Alt": {56, 100},
    "Shift": {42, 54},
    "Meta": {125, 126},
}
_ALL_MOD_CODES = {code for codes in _MOD_CODES.values() for code in codes}

_ALIAS = {
    "control": "Ctrl",
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "super": "Meta",
    "win": "Meta",
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "middle click": "Middle",
    "middle": "Middle",
    "left click": "Left",
    "left": "Left",
    "right click": "Right",
    "right": "Right",
    "back": "Back",
    "forward": "Forward",
    "space": "Space",
    "tab": "Tab",
}


def _dbus_enabled() -> bool:
    app = QApplication.instance()
    return app is None or app.platformName() != "offscreen"


def _normalize_token(token: str) -> str:
    cleaned = token.strip()
    if not cleaned:
        return ""
    aliased = _ALIAS.get(cleaned.casefold())
    if aliased:
        return aliased
    if len(cleaned) == 1:
        return cleaned.upper()
    if cleaned[:1].casefold() == "f" and cleaned[1:].isdigit():
        return "F" + cleaned[1:]
    return cleaned[0].upper() + cleaned[1:]


@dataclass(frozen=True)
class HotkeySpec:
    modifiers: tuple[str, ...] = ()
    trigger: str = ""

    @property
    def mouse(self) -> bool:
        return self.trigger in MOUSE_TRIGGERS

    def to_text(self) -> str:
        parts = [name for name in MODIFIERS if name in self.modifiers]
        if self.trigger:
            parts.append(self.trigger)
        return "+".join(parts)

    def to_sequence(self) -> QKeySequence:
        if not self.trigger or self.mouse:
            return QKeySequence()
        return QKeySequence.fromString(self.to_text(), QKeySequence.SequenceFormat.PortableText)

    @staticmethod
    def from_text(text: str) -> HotkeySpec:
        mods: list[str] = []
        trigger = ""
        for raw in text.replace("-", "+").split("+"):
            token = _normalize_token(raw)
            if not token:
                continue
            if token in MODIFIERS:
                if token not in mods:
                    mods.append(token)
                continue
            trigger = token
        return HotkeySpec(tuple(mods), trigger)


def parse_hotkey(text: str) -> HotkeySpec:
    return HotkeySpec.from_text(text or "")


def action_id() -> list[str]:
    return [COMPONENT, ACTION, FRIENDLY_APP, FRIENDLY_ACTION]


def sequence_from_text(text: str) -> QKeySequence:
    return parse_hotkey(text).to_sequence()


def sequence_to_text(sequence: QKeySequence) -> str:
    if sequence.isEmpty():
        return ""
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


def combined_keys(sequence: QKeySequence) -> list[int]:
    keys: list[int] = []
    for index in range(sequence.count()):
        combo = sequence[index]
        combined = combo.toCombined() if hasattr(combo, "toCombined") else int(combo)
        if combined:
            keys.append(int(combined))
    return keys


def _gvariant_strings(values: list[str]) -> str:
    inner = ", ".join("'" + item.replace("'", r"\'") + "'" for item in values)
    return f"[{inner}]"


def _gdbus(method: str, *args: str) -> bool:
    if not _dbus_enabled():
        return False
    try:
        ran = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                SERVICE,
                "--object-path",
                MANAGER,
                "--method",
                f"{MANAGER_IFACE}.{method}",
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return ran.returncode == 0


def register_shortcut(sequence: QKeySequence) -> bool:
    if sequence.isEmpty():
        return clear_shortcut()
    keys = combined_keys(sequence)
    if not keys:
        return clear_shortcut()
    identity = _gvariant_strings(action_id())
    registered = _gdbus("doRegister", identity)
    assigned = _gdbus("setShortcut", identity, f"[{keys[0]}]", _NO_AUTOLOAD)
    return registered and assigned


def clear_shortcut() -> bool:
    return _gdbus("unRegister", _gvariant_strings(action_id()))


class PointerHotkey(QObject):
    """Watch /dev/input for a modifier + mouse-button combination."""

    pressed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._spec = HotkeySpec()
        self._held: set[int] = set()
        self._fds: list[int] = []
        self._notifiers: list[QSocketNotifier] = []

    def set_spec(self, spec: HotkeySpec | None) -> None:
        wanted = spec if spec is not None and spec.mouse and spec.trigger else HotkeySpec()
        self._spec = wanted
        if wanted.trigger:
            self._open()
        else:
            self._close()

    def _open(self) -> None:
        self._close()
        app = QApplication.instance()
        if app is not None and app.platformName() == "offscreen":
            return
        root = Path("/dev/input")
        if not root.is_dir():
            return
        for path in sorted(root.glob("event*")):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, self)
            notifier.activated.connect(lambda _fd=0, handle=fd: self._read(handle))
            self._fds.append(fd)
            self._notifiers.append(notifier)

    def _close(self) -> None:
        for notifier in self._notifiers:
            notifier.setEnabled(False)
            notifier.deleteLater()
        self._notifiers = []
        for fd in self._fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = []
        self._held.clear()

    def _read(self, fd: int) -> None:
        wanted = _BTN.get(self._spec.trigger)
        if wanted is None:
            return
        while True:
            try:
                blob = os.read(fd, _EVENT.size)
            except BlockingIOError:
                return
            except OSError:
                return
            if len(blob) != _EVENT.size:
                return
            _sec, _usec, kind, code, value = _EVENT.unpack(blob)
            if kind != _EV_KEY:
                continue
            if value == 1:
                self._held.add(code)
            elif value == 0:
                self._held.discard(code)
            if value == 1 and code == wanted and self._mods_match():
                self.pressed.emit()

    def _mods_match(self) -> bool:
        for name in MODIFIERS:
            held = bool(self._held & _MOD_CODES[name])
            needed = name in self._spec.modifiers
            if held != needed:
                return False
        return True


class GlobalHotkey(QObject):
    """Fires when the configured keyboard or mouse combination is used."""

    pressed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._listening = False
        self._pointer = PointerHotkey(self)
        self._pointer.pressed.connect(self.pressed)
        self._listen()

    def set_spec(self, spec: HotkeySpec) -> bool:
        if spec.mouse:
            clear_shortcut()
            self._pointer.set_spec(spec)
            return True
        self._pointer.set_spec(None)
        ok = register_shortcut(spec.to_sequence())
        self._listen()
        return ok

    def set_sequence(self, sequence: QKeySequence) -> bool:
        return self.set_spec(parse_hotkey(sequence_to_text(sequence)))

    def _listen(self) -> None:
        if self._listening or not _dbus_enabled():
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return
        ok = bus.connect(
            SERVICE,
            f"/component/{COMPONENT}",
            COMPONENT_IFACE,
            "globalShortcutPressed",
            self._on_pressed,
        )
        self._listening = bool(ok)

    @pyqtSlot(str, str, int)
    @pyqtSlot(str, str, "qlonglong")
    def _on_pressed(self, _component: str, action: str, _timestamp) -> None:
        if action == ACTION:
            self.pressed.emit()
