"""Docked right column: hint, tile properties, or scene settings."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from onigiri.extensions import list_extensions
from onigiri.model import Scene, aliases_from_plugins
from onigiri.ui.inspector import Inspector
from onigiri.ui.background import BackgroundSelector
from onigiri.ui.scene_dialog import PluginPanel
from onigiri.ui.theme import BareStack


class ScenePanel(QWidget):
    changed = pyqtSignal()
    pictureRequested = pyqtSignal()
    deleteRequested = pyqtSignal()
    testRequested = pyqtSignal(str)

    def __init__(self, parent=None, *, extensions_dir=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._scene_id = ""
        self._wallpaper = ""
        self._script = ""
        self._pan: dict = {}
        self.panels: dict[str, PluginPanel] = {}

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Scene name")
        self.name_edit.textChanged.connect(self._emit_changed)
        self.background_selector = BackgroundSelector()
        self.background_selector.pictureRequested.connect(self.pictureRequested)
        self.background_selector.changed.connect(self._emit_changed)
        self.picture_button = self.background_selector.picture_button
        self.delete_button = QPushButton("Delete scene")
        self.delete_button.clicked.connect(self.deleteRequested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        caption = QLabel("SCENE")
        caption.setObjectName("section")
        layout.addWidget(caption)
        layout.addWidget(self.name_edit)
        layout.addWidget(self.background_selector)

        plugins_caption = QLabel("EXTENSIONS")
        plugins_caption.setObjectName("section")
        layout.addWidget(plugins_caption)
        empty = QLabel("No extensions installed.")
        empty.setObjectName("muted")
        empty.setWordWrap(True)
        infos = list_extensions(extensions_dir)
        if not infos:
            layout.addWidget(empty)
        for info in infos:
            panel = PluginPanel(info)
            panel.changed.connect(self._emit_changed)
            panel.testRequested.connect(self.testRequested)
            self.panels[info.stem] = panel
            layout.addWidget(panel)
        layout.addWidget(self.delete_button)
        layout.addStretch(1)

    def _emit_changed(self, *_args) -> None:
        if not self._loading:
            self.changed.emit()

    def load(self, scene: Scene) -> None:
        self._loading = True
        self._scene_id = scene.id
        self._wallpaper = scene.wallpaper
        self.background_selector.set_background(scene.wallpaper, scene.background_color)
        self._script = scene.script
        self._pan = dict(scene.wallpaper_pan)
        self.name_edit.setText(scene.name)
        selected = scene.plugins
        for stem, panel in self.panels.items():
            on = (selected is None and not panel.info.explicit_only) or (selected is not None and stem in selected)
            panel.set_checked(on)
            panel.fill_from_scene(scene)
        self._loading = False

    def built(self) -> Scene:
        plugins = {stem: panel.values() for stem, panel in self.panels.items() if panel.is_checked()}
        blue, purple, hardware_blue, hardware_purple = aliases_from_plugins(plugins)
        return Scene(
            id=self._scene_id,
            name=self.name_edit.text().strip() or "Scene",
            wallpaper=self.background_selector.wallpaper(),
            background_color=self.background_selector.background_color(),
            blue=blue,
            purple=purple,
            hardware_blue=hardware_blue,
            hardware_purple=hardware_purple,
            wallpaper_pan=dict(self._pan),
            script=self._script,
            plugins=plugins,
        )

    def set_wallpaper(self, path: str) -> None:
        self.background_selector.set_background(path)
        self._wallpaper = path
        self._emit_changed()


class SidePanel(QFrame):
    """Always-visible settings column for the Scenes page."""

    def __init__(self, parent=None, *, extensions_dir=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidePanel")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.inspector = Inspector()
        self.scene_panel = ScenePanel(extensions_dir=extensions_dir)
        self.hint = QLabel("Select a tile on the canvas, or a scene below.")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignTop)
        hint_page = QWidget()
        hint_page.setAutoFillBackground(False)
        hint_layout = QVBoxLayout(hint_page)
        hint_layout.setContentsMargins(0, 0, 0, 0)
        caption = QLabel("EDITOR")
        caption.setObjectName("section")
        hint_layout.addWidget(caption)
        hint_layout.addWidget(self.hint)
        hint_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.viewport().setStyleSheet("background: transparent;")
        inner = QWidget()
        inner.setAutoFillBackground(False)
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        stack_layout = QVBoxLayout(inner)
        stack_layout.setContentsMargins(12, 12, 12, 12)
        self.stack = BareStack()
        self.stack.setAutoFillBackground(False)
        self.stack.addWidget(hint_page)
        self.stack.addWidget(self.inspector)
        self.stack.addWidget(self.scene_panel)
        stack_layout.addWidget(self.stack)
        scroll.setWidget(inner)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def paintEvent(self, event) -> None:
        return

    def show_hint(self) -> None:
        self.stack.setCurrentIndex(0)

    def show_tile(self) -> None:
        self.stack.setCurrentWidget(self.inspector)

    def show_scene(self) -> None:
        self.stack.setCurrentWidget(self.scene_panel)

    def ensure(self, which: str) -> None:
        if which == "tile":
            self.show_tile()
        elif which == "scene":
            self.show_scene()
        else:
            self.show_hint()

    def current_page(self) -> str:
        widget = self.stack.currentWidget()
        if widget is self.inspector:
            return "tile"
        if widget is self.scene_panel:
            return "scene"
        return "hint"
