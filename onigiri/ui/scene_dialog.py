"""Create or edit a scene: optional background, extensions, and a name."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from onigiri.extensions import apply_scene, list_extensions, payload_from_scene
from onigiri.lights import DEFAULT_BLUE, DEFAULT_PURPLE
from onigiri.model import Scene, aliases_from_plugins, new_id
from onigiri.palette import dominant_colors, slot_colors
from onigiri.ui.colors import ColorSwatch, pick_color
from onigiri.ui.background import BackgroundSelector
from onigiri.ui.screens import ScreenInfo
from onigiri.ui.wallpaper_preview import WallpaperStage

_IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.svg);;All files (*)"
_SCRIPT_FILTER = "Python (*.py);;All files (*)"
_PICTURE = 0
_PLUGINS = 1
_SAVE = 2


def _extension_checked(scene: Scene | None, stem: str) -> bool:
    """New scenes start with every extension off. Legacy (plugins is None) keeps them all on."""

    if scene is None:
        return False
    if scene.plugins is None:
        return True
    return stem in scene.plugins


def _picture_defaults(fields, blue, purple) -> dict:
    values = {}
    extra = [item for item in (blue, purple) if item is not None]
    extra_index = 0
    for item in fields:
        if item.type != "color":
            continue
        if item.id in {"blue", "hardware_blue", "led_blue", "text"} and blue is not None:
            values[item.id] = blue
        elif item.id in {"purple", "hardware_purple", "led_purple", "gauge"} and purple is not None:
            values[item.id] = purple
        elif extra:
            values[item.id] = extra[min(extra_index, len(extra) - 1)]
            extra_index += 1
    return values


class PluginPanel(QWidget):
    changed = pyqtSignal()
    testRequested = pyqtSignal(str)

    def __init__(self, info, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self._touched: set[str] = set()
        self._swatches: dict[str, ColorSwatch] = {}
        self._hex: dict[str, QLabel] = {}
        self._bools: dict[str, QCheckBox] = {}
        self._files: dict[str, QLineEdit] = {}
        self._texts: dict[str, QLineEdit] = {}
        self._numbers: dict[str, QSpinBox] = {}
        self.enabled = QCheckBox(info.title)
        self.body = QWidget()
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(lambda: self.testRequested.emit(self.info.stem))
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(8, 0, 0, 0)
        body_layout.setSpacing(8)
        if info.error:
            note = QLabel(info.error)
            note.setObjectName("muted")
            note.setWordWrap(True)
            body_layout.addWidget(note)
        for item in info.fields:
            if item.type == "color":
                body_layout.addWidget(self._color_row(item))
            elif item.type == "bool":
                box = QCheckBox(item.label)
                box.toggled.connect(self.changed)
                self._bools[item.id] = box
                body_layout.addWidget(box)
            elif item.type == "file":
                body_layout.addLayout(self._file_row(item))
            elif item.type in {"text", "number"}:
                caption = QLabel(item.label)
                caption.setObjectName("muted")
                caption.setWordWrap(True)
                body_layout.addWidget(caption)
                if item.type == "text":
                    control = QLineEdit()
                    control.editingFinished.connect(self.changed)
                    self._texts[item.id] = control
                else:
                    control = QSpinBox()
                    control.setRange(item.minimum, item.maximum)
                    control.valueChanged.connect(self.changed)
                    self._numbers[item.id] = control
                body_layout.addWidget(control)
        body_layout.addWidget(self.test_button)
        body_layout.addWidget(self.status)
        self.enabled.toggled.connect(self.body.setVisible)
        self.enabled.toggled.connect(self.changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.enabled)
        layout.addWidget(self.body)
        self.body.setVisible(self.enabled.isChecked())
        self.set_values({item.id: item.default for item in info.fields if item.default is not None}, touched=False)

    def _color_row(self, item) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        caption = QLabel(item.label)
        caption.setObjectName("muted")
        swatch = ColorSwatch(QColor(*(DEFAULT_PURPLE if "purple" in item.id else DEFAULT_BLUE)))
        swatch.clicked.connect(lambda _=False, target=swatch, field_id=item.id: self._pick(target, field_id))
        hex_label = QLabel(swatch.color.name().upper())
        hex_label.setObjectName("hexValue")
        hex_label.setFixedWidth(72)
        swatch.colorChanged.connect(lambda color, label=hex_label: label.setText(color.name().upper()))
        swatch.colorChanged.connect(lambda _color: self.changed.emit())
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(swatch, 1)
        row.addWidget(hex_label)
        layout.addWidget(caption)
        layout.addLayout(row)
        self._swatches[item.id] = swatch
        self._hex[item.id] = hex_label
        return block

    def _file_row(self, item) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText(item.label)
        browse = QPushButton("Choose")
        browse.clicked.connect(lambda _=False, field_id=item.id: self._browse_file(field_id))
        self._files[item.id] = edit
        row.addWidget(edit, 1)
        row.addWidget(browse)
        return row

    def _pick(self, swatch: ColorSwatch, field_id: str) -> None:
        title = next((item.label for item in self.info.fields if item.id == field_id), "Color")
        chosen = pick_color(self, swatch.color, title)
        if chosen.isValid():
            self._touched.add(field_id)
            swatch.set_color(chosen)

    def _browse_file(self, field_id: str) -> None:
        edit = self._files[field_id]
        path, _filter = QFileDialog.getOpenFileName(self, "Choose file", edit.text())
        if path:
            self._touched.add(field_id)
            edit.setText(path)
            self.changed.emit()

    def set_checked(self, on: bool) -> None:
        self.enabled.setChecked(on)
        self.body.setVisible(on)

    def is_checked(self) -> bool:
        return self.enabled.isChecked()

    def values(self) -> dict:
        data: dict = {}
        for field_id, swatch in self._swatches.items():
            data[field_id] = swatch.rgb()
        for field_id, box in self._bools.items():
            data[field_id] = box.isChecked()
        for field_id, edit in self._files.items():
            data[field_id] = edit.text().strip()
        data.update({key: edit.text().strip() for key, edit in self._texts.items()})
        data.update({key: spin.value() for key, spin in self._numbers.items()})
        return data

    def set_values(self, values: dict, *, touched: bool = True) -> None:
        for field_id, raw in values.items():
            if field_id in self._swatches and isinstance(raw, (list, tuple)) and len(raw) == 3:
                self._swatches[field_id].set_color(QColor(*raw))
                if touched:
                    self._touched.add(field_id)
            elif field_id in self._bools:
                self._bools[field_id].setChecked(bool(raw))
                if touched:
                    self._touched.add(field_id)
            elif field_id in self._files or field_id in self._texts:
                (self._files.get(field_id) or self._texts[field_id]).setText(str(raw or ""))
                if touched:
                    self._touched.add(field_id)
            elif field_id in self._numbers:
                self._numbers[field_id].setValue(int(raw or 0))
                if touched:
                    self._touched.add(field_id)

    def fill_from_scene(self, scene: Scene) -> None:
        values: dict = {item.id: item.default if item.default is not None else
                        (False if item.type == "bool" else 0 if item.type == "number" else "")
                        for item in self.info.fields if item.type != "color"}
        for item in self.info.fields:
            if item.type != "color":
                continue
            if item.id in {"blue", "led_blue"}:
                values[item.id] = scene.blue
            elif item.id in {"purple", "led_purple"}:
                values[item.id] = scene.purple
            elif item.id == "hardware_blue":
                values[item.id] = scene.case_blue()
            elif item.id == "hardware_purple":
                values[item.id] = scene.case_purple()
            else:
                values[item.id] = DEFAULT_PURPLE if "purple" in item.id else DEFAULT_BLUE
        stored = {}
        if scene.plugins and self.info.stem in scene.plugins:
            stored = dict(scene.plugins[self.info.stem])
        values.update(stored)
        self.set_values(values, touched=True)

    def apply_picture_colors(self, blue, purple, *, only_untouched: bool = True) -> None:
        defaults = _picture_defaults(self.info.fields, blue, purple)
        pending = {
            field_id: rgb
            for field_id, rgb in defaults.items()
            if not only_untouched or field_id not in self._touched
        }
        if pending:
            self.set_values(pending, touched=False)


class SceneDialog(QDialog):
    def __init__(
        self,
        scene: Scene | None = None,
        parent=None,
        *,
        colors_file: Path | None = None,
        lianli_file: Path | None = None,
        hue_file: Path | None = None,
        reload_fn=None,
        hue_fn=None,
        list_lights_fn=None,
        screens: list[ScreenInfo] | None = None,
        extensions_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sceneDialog")
        self._scene_id = scene.id if scene is not None else new_id()
        self._colors_file = colors_file
        self._lianli_file = lianli_file
        self._hue_file = hue_file
        self._reload_fn = reload_fn
        self._hue_fn = hue_fn
        self._list_lights = list_lights_fn
        self._screens = screens
        self._extensions_dir = extensions_dir
        self._stage = _PICTURE
        self._picture_pair = (DEFAULT_BLUE, DEFAULT_PURPLE)
        self.deleted = False
        self.panels: dict[str, PluginPanel] = {}
        self.setWindowTitle("Edit scene" if scene is not None else "New scene")
        self.setMinimumSize(560, 620)
        self.resize(640, 720)
        self.setMaximumHeight(920)

        inner = QWidget()
        form = QVBoxLayout(inner)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(16)
        form.addWidget(self._picture_section(), 1)
        form.addWidget(self._plugins_section(scene))
        form.addWidget(self._save_section())
        form.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(inner)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.next_button = QPushButton("Continue")
        self.next_button.setObjectName("primary")
        self.next_button.clicked.connect(self._next)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        if scene is not None:
            remove = QPushButton("Delete")
            remove.clicked.connect(self._delete)
            buttons.addWidget(remove)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        buttons.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(buttons)

        if scene is not None:
            self.name_edit.setText(scene.name)
            self.script_edit.setText(scene.script)
            self.background_selector.set_background(scene.wallpaper, scene.background_color)
            self._background_changed()
            self.preview.set_pans(scene.wallpaper_pan)
            self._reveal(_SAVE)
        else:
            self._reveal(_PICTURE)
        self._background_changed()
        self._sync_script_label()

    def built(self) -> Scene:
        plugins = {stem: panel.values() for stem, panel in self.panels.items() if panel.is_checked()}
        blue, purple, hardware_blue, hardware_purple = aliases_from_plugins(plugins)
        if not plugins:
            blue, purple = self._picture_pair
        return Scene(
            id=self._scene_id,
            name=self.name_edit.text().strip() or "Scene",
            wallpaper=self.background_selector.wallpaper(),
            background_color=self.background_selector.background_color(),
            blue=blue,
            purple=purple,
            hardware_blue=hardware_blue,
            hardware_purple=hardware_purple,
            wallpaper_pan=self.preview.pans(),
            script=self.script_edit.text().strip(),
            plugins=plugins,
        )

    def _picture_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.background_selector = BackgroundSelector()
        self.picture_path = self.background_selector.picture_path
        self.preview = WallpaperStage(screens=self._screens)
        self.background_selector.pictureRequested.connect(self._browse)
        self.background_selector.changed.connect(self._background_changed)
        layout.addWidget(self._caption("Background"))
        layout.addWidget(self.background_selector)
        layout.addWidget(self.preview, 1)
        layout.addStretch(1)
        return section

    def _plugins_section(self, scene: Scene | None) -> QWidget:
        self.plugins_section = QWidget()
        layout = QVBoxLayout(self.plugins_section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        hint = QLabel("Pick which extensions this scene runs. Each one keeps its own settings.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(self._caption("Extensions"))
        layout.addWidget(hint)
        installed = list_extensions(self._extensions_dir)
        if not installed:
            empty = QLabel("No extensions in ~/.config/onigiri/extensions/. Wallpaper still applies.")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            return self.plugins_section
        for info in installed:
            panel = PluginPanel(info)
            panel.test_button.clicked.connect(lambda _=False, stem=info.stem: self._test_plugin(stem))
            panel.set_checked(_extension_checked(scene, info.stem) and not (
                scene is not None and scene.plugins is None and info.explicit_only))
            if info.continuous:
                panel.test_button.setEnabled(False)
                panel.test_button.setText("Apply the saved scene to start")
            if scene is not None:
                panel.fill_from_scene(scene)
            else:
                panel.apply_picture_colors(*self._picture_pair, only_untouched=False)
            self.panels[info.stem] = panel
            layout.addWidget(panel)
        return self.plugins_section

    def _save_section(self) -> QWidget:
        self.save_section = QWidget()
        layout = QVBoxLayout(self.save_section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        hint = QLabel("Name the scene, then save it. An optional script runs after the selected extensions.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Scene name")
        self.script_edit = QLineEdit()
        self.script_edit.setReadOnly(True)
        self.script_edit.setPlaceholderText("No extra script")
        browse = QPushButton("Choose script")
        browse.clicked.connect(self._browse_script)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_script)
        script_row = QHBoxLayout()
        script_row.setSpacing(8)
        script_row.addWidget(browse)
        script_row.addWidget(clear)
        layout.addWidget(self._caption("Save"))
        layout.addWidget(hint)
        layout.addWidget(self.name_edit)
        layout.addWidget(self.script_edit)
        layout.addLayout(script_row)
        return self.save_section

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("section")
        return label

    def _reveal(self, stage: int, *, scroll: bool = False) -> None:
        previous = self._stage
        self._stage = max(self._stage, stage)
        self.plugins_section.setVisible(self._stage >= _PLUGINS)
        self.save_section.setVisible(self._stage >= _SAVE)
        self._sync_next()
        if scroll:
            target = {
                _PLUGINS: self.plugins_section,
                _SAVE: self.save_section,
            }.get(max(stage, previous + 1 if stage > previous else stage))
            if target is not None:
                self._scroll_down_to(target)

    def _scroll_down_to(self, widget: QWidget) -> None:
        def go() -> None:
            try:
                inner = self.scroll.widget()
                if inner is None or not widget.isVisible():
                    return
                y = widget.mapTo(inner, QPoint(0, 0)).y() - 8
                bar = self.scroll.verticalScrollBar()
            except RuntimeError:
                return
            bar.setValue(max(bar.value(), max(0, y)))

        QTimer.singleShot(0, go)
        QTimer.singleShot(40, go)

    def _sync_next(self) -> None:
        self.next_button.setEnabled(self.background_selector.ready())
        self.next_button.setText("Save" if self._stage >= _SAVE else "Continue")

    def _next(self) -> None:
        if not self.next_button.isEnabled():
            return
        if self._stage >= _SAVE:
            self._finish()
            return
        self._reveal(self._stage + 1, scroll=True)

    def _browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Choose picture", self.picture_path.text(), _IMAGE_FILTER)
        if path:
            self._set_picture(path, pick_colors=True)

    def _browse_script(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Choose script", self.script_edit.text(), _SCRIPT_FILTER)
        if path:
            self.script_edit.setText(path)
            self._sync_script_label()

    def _clear_script(self) -> None:
        self.script_edit.setText("")
        self._sync_script_label()

    def _sync_script_label(self) -> None:
        if not self.script_edit.text().strip():
            self.script_edit.setPlaceholderText("No extra script")

    def _set_picture(self, path: str, *, pick_colors: bool = False, keep_pans: bool = False) -> None:
        if not keep_pans:
            self.preview.reset_pans()
        self.background_selector.set_background(path)
        self.preview.setVisible(bool(path))
        self.preview.set_image(path)
        if pick_colors:
            self._apply_picture_colors(path)
        if path and Path(path).is_file() and self._stage < _PLUGINS:
            self._reveal(_PLUGINS, scroll=True)
        else:
            self._sync_next()

    def _background_changed(self) -> None:
        self.preview.setVisible(self.background_selector.mode.currentData() != "keep")
        color = self.background_selector.background_color()
        if color is not None:
            self.preview.set_color(color)
            self._picture_pair = (color, color)
            for panel in self.panels.values():
                panel.apply_picture_colors(color, color, only_untouched=True)
        else:
            self.preview.set_image(self.background_selector.wallpaper())
        self._sync_next()

    def _apply_picture_colors(self, path: str) -> None:
        blue, purple = slot_colors(dominant_colors(path))
        if blue is None and purple is None:
            return
        self._picture_pair = (
            blue if blue is not None else self._picture_pair[0],
            purple if purple is not None else self._picture_pair[1],
        )
        for panel in self.panels.values():
            panel.apply_picture_colors(blue, purple, only_untouched=True)

    def _payload_extras(self) -> dict:
        return {
            "paint_hue": True,
            "colors_file": self._colors_file,
            "lianli_file": self._lianli_file,
            "hue_file": self._hue_file,
            "reload_fn": self._reload_fn,
            "hue_fn": self._hue_fn,
        }

    def _test_plugin(self, stem: str) -> None:
        panel = self.panels.get(stem)
        if panel is None or not panel.is_checked():
            return
        payload = payload_from_scene(self.built())
        payload.plugins = {stem: panel.values()}
        payload.extras.update(self._payload_extras())
        try:
            errors = apply_scene(payload, directory=self._extensions_dir)
        except (OSError, ValueError, RuntimeError) as error:
            panel.status.setText(str(error))
            return
        panel.status.setText("; ".join(errors) if errors else "Updated.")

    def _finish(self) -> None:
        if not self.background_selector.ready():
            return
        self.deleted = False
        self.accept()

    def _delete(self) -> None:
        self.deleted = True
        self.accept()
