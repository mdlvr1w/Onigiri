"""Editor for the selected tile."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class FontSizeSpin(QSpinBox):
    """0 means the terminal default; the next step is 6pt so typing 12 is not snapped to 6."""

    def stepBy(self, steps: int) -> None:
        current = self.value()
        if current <= 0 and steps > 0:
            self.setValue(6)
            return
        if current <= 6 and steps < 0:
            self.setValue(0)
            return
        super().stepBy(steps)

from onigiri.launch import DesktopApp, assign_app, resolve_tile
from onigiri.model import Tile

_TERMINALS = (
    ("alacritty", "Alacritty"),
    ("kitty", "Kitty"),
    ("konsole", "Konsole"),
    ("xterm", "Xterm"),
)


class Inspector(QWidget):
    aboutToEdit = pyqtSignal(str)
    edited = pyqtSignal()
    launchRequested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self._tile: Tile | None = None
        self._tiles: list[Tile] = []
        self._profile_name = ""
        self._duplicates: set[str] = set()
        self._apps: list[DesktopApp] = []
        self._apps_by_id: dict[str, DesktopApp] = {}
        self._loading = False

        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("nameEdit")
        self.name_edit.setPlaceholderText("Tile name")

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Terminal", "helper")
        self.mode_combo.addItem("Application", "app")
        self.mode_combo.addItem("Raw command", "raw")

        self.terminal_combo = QComboBox()
        for key, label in _TERMINALS:
            self.terminal_combo.addItem(label, key)
        self.shell_edit = QLineEdit()
        self.shell_edit.setPlaceholderText("Command inside the terminal, such as btop")
        self.show_title = QCheckBox("Use the tile name as the window title")
        self.show_title.setToolTip(
            "Turn this off to leave Alacritty, Kitty, or Xterm with their own title. "
            "Konsole still uses a unique title so the window can be placed."
        )
        self.hide_toolbar = QCheckBox("Hide the toolbar")
        self.hide_toolbar.setToolTip(
            "Konsole only. Hides New Tab, Split, Copy, and the rest of the toolbar."
        )
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setToolTip("Drag left for a clearer terminal background, right for a solid one. Text stays solid.")
        self.opacity_value = QLabel("100%")
        self.opacity_value.setObjectName("value")
        self.opacity_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.opacity_value.setMinimumWidth(44)
        self.font_spin = FontSizeSpin()
        self.font_spin.setRange(0, 48)
        self.font_spin.setSpecialValueText("Default")
        self.font_spin.setValue(0)
        self.font_spin.setKeyboardTracking(False)
        self.font_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.font_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.font_spin.setToolTip(
            "Point size for the terminal text. Click the number and type a new one. "
            "Default keeps the terminal's own size. "
            "Konsole and Xterm use a monospace face when a size is set."
        )
        self.font_value = QLabel("Default")
        self.font_value.setObjectName("value")
        self.font_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.font_value.setMinimumWidth(44)

        self.app_search = QLineEdit()
        self.app_search.setPlaceholderText("Search applications")
        self.app_combo = QComboBox()

        self.command_edit = QPlainTextEdit()
        self.command_edit.setPlaceholderText("Shell command")
        self.command_edit.setFixedHeight(72)
        self.match_kind = QComboBox()
        self.match_kind.addItem("No window match", "")
        self.match_kind.addItem("Window class", "class")
        self.match_kind.addItem("Window title", "title")
        self.match_value = QLineEdit()
        self.match_value.setPlaceholderText("Class or title to match")

        self.no_border = QCheckBox("Hide the title bar")
        self.skip_taskbar = QCheckBox("Skip the taskbar")

        self.warning = QLabel("")
        self.warning.setObjectName("warning")
        self.warning.setWordWrap(True)

        self.launch_button = QPushButton("Launch this tile")

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("Name", self.name_edit)
        form.addRow("Opens", self.mode_combo)
        self.form = form

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        opens = QLabel("OPENS")
        opens.setObjectName("section")
        layout.addWidget(opens)
        self.gap_caption = self._caption("Gap")
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(0, 200)
        self.gap_spin.setSuffix(" px")
        self.gap_spin.setKeyboardTracking(False)
        self.gap_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.gap_spin.setToolTip("Space between tiles on this screen")
        layout.addWidget(self.gap_caption)
        layout.addWidget(self.gap_spin)
        layout.addLayout(form)
        self.terminal_caption = self._caption("Terminal")
        self.app_caption = self._caption("Application")
        self.command_caption = self._caption("Command")
        layout.addWidget(self.terminal_caption)
        layout.addWidget(self.terminal_combo)
        layout.addWidget(self.shell_edit)
        layout.addWidget(self.show_title)
        layout.addWidget(self.hide_toolbar)
        self.look_caption = QLabel("LOOK")
        self.look_caption.setObjectName("section")
        layout.addWidget(self.look_caption)
        self.opacity_caption = self._caption("Background")
        layout.addWidget(self.opacity_caption)
        self.opacity_row = QWidget()
        opacity_row = QHBoxLayout(self.opacity_row)
        opacity_row.setContentsMargins(0, 0, 0, 0)
        opacity_row.setSpacing(10)
        opacity_row.addWidget(self.opacity_slider, 1)
        opacity_row.addWidget(self.opacity_value)
        layout.addWidget(self.opacity_row)
        self.font_caption = self._caption("Font size")
        layout.addWidget(self.font_caption)
        self.font_row = QWidget()
        font_row = QHBoxLayout(self.font_row)
        font_row.setContentsMargins(0, 0, 0, 0)
        font_row.setSpacing(10)
        font_row.addWidget(self.font_spin, 1)
        font_row.addWidget(self.font_value)
        layout.addWidget(self.font_row)
        layout.addWidget(self.app_caption)
        layout.addWidget(self.app_search)
        layout.addWidget(self.app_combo)
        layout.addWidget(self.command_caption)
        layout.addWidget(self.command_edit)
        layout.addWidget(self.match_kind)
        layout.addWidget(self.match_value)
        layout.addWidget(self.no_border)
        layout.addWidget(self.skip_taskbar)
        layout.addWidget(self.warning)
        layout.addWidget(self.launch_button)
        layout.addStretch(1)

        self._helper_widgets = (
            self.terminal_caption,
            self.terminal_combo,
            self.shell_edit,
            self.show_title,
            self.look_caption,
            self.opacity_caption,
            self.opacity_row,
            self.font_caption,
            self.font_row,
        )
        self._app_widgets = (self.app_caption, self.app_search, self.app_combo)
        self._raw_widgets = (self.command_caption, self.command_edit)
        self.match_kind.setVisible(False)
        self.match_value.setVisible(False)

        self.name_edit.textChanged.connect(self._on_name)
        self.mode_combo.currentIndexChanged.connect(self._on_mode)
        self.terminal_combo.currentIndexChanged.connect(self._on_terminal)
        self.shell_edit.textChanged.connect(self._on_shell)
        self.show_title.toggled.connect(self._on_show_title)
        self.hide_toolbar.toggled.connect(self._on_hide_toolbar)
        self.opacity_slider.valueChanged.connect(self._on_opacity)
        self.font_spin.valueChanged.connect(self._on_font_size)
        self.font_spin.editingFinished.connect(self._clamp_font_size)
        self.app_search.textChanged.connect(lambda _text: self._refill_apps())
        self.app_combo.currentIndexChanged.connect(self._on_app)
        self.command_edit.textChanged.connect(self._on_command)
        self.match_kind.currentIndexChanged.connect(self._on_match)
        self.match_value.textChanged.connect(self._on_match_value)
        self.no_border.toggled.connect(self._on_border)
        self.skip_taskbar.toggled.connect(self._on_taskbar)
        self.launch_button.clicked.connect(self.launchRequested)

        self.gap_caption.setVisible(False)
        self.gap_spin.setVisible(False)
        self.set_apps([])
        self.set_tile(None, "", set())

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("section")
        return label

    def set_apps(self, apps: list[DesktopApp]) -> None:
        self._apps = list(apps)
        self._apps_by_id = {app.app_id: app for app in self._apps}
        self._refill_apps()

    def set_duplicates(self, duplicates: set[str]) -> None:
        self._duplicates = set(duplicates)

    def set_tile(self, tile: Tile | None, profile_name: str, duplicates: set[str]) -> None:
        self.set_selection([] if tile is None else [tile], profile_name, duplicates)

    def set_selection(
        self,
        tiles: list[Tile],
        profile_name: str,
        duplicates: set[str],
        gap: int | None = None,
    ) -> None:
        self._loading = True
        self._tiles = list(tiles)
        self._tile = tiles[0] if len(tiles) == 1 else None
        self._profile_name = profile_name
        self._duplicates = set(duplicates)
        bulk = len(tiles) > 1
        has_tile = bool(tiles)
        show_gap = gap is not None
        self.gap_caption.setVisible(show_gap)
        self.gap_spin.setVisible(show_gap)
        self.gap_spin.setEnabled(show_gap)
        if show_gap:
            self.gap_spin.blockSignals(True)
            self.gap_spin.setValue(int(gap))
            self.gap_spin.blockSignals(False)
        if not has_tile:
            self.name_edit.clear()
            self.shell_edit.clear()
            self.command_edit.clear()
            self.match_value.clear()
            self.warning.clear()
            self.warning.setVisible(False)
            self._show_gap_only()
            self._loading = False
            return
        tile = tiles[0]
        self.name_edit.setEnabled(not bulk)
        self.mode_combo.setEnabled(not bulk)
        self.terminal_combo.setEnabled(not bulk)
        self.shell_edit.setEnabled(not bulk)
        self.app_search.setEnabled(not bulk)
        self.app_combo.setEnabled(not bulk)
        self.command_edit.setEnabled(not bulk)
        self.match_kind.setEnabled(not bulk)
        self.match_value.setEnabled(not bulk)
        self.show_title.setEnabled(True)
        self.hide_toolbar.setEnabled(True)
        self.opacity_slider.setEnabled(True)
        self.font_spin.setEnabled(True)
        self.no_border.setEnabled(True)
        self.skip_taskbar.setEnabled(True)
        self.launch_button.setEnabled(True)
        self.name_edit.setText(tile.name)
        mode_index = self.mode_combo.findData(tile.launch_mode)
        self.mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 2)
        terminal_index = self.terminal_combo.findData(tile.terminal)
        self.terminal_combo.setCurrentIndex(terminal_index if terminal_index >= 0 else 0)
        self.shell_edit.setText(tile.shell_command)
        opacities = {int(round(item.opacity * 100)) for item in tiles}
        opacity_percent = next(iter(opacities)) if len(opacities) == 1 else int(round(tile.opacity * 100))
        self.opacity_slider.setValue(opacity_percent)
        self.opacity_value.setText(f"{opacity_percent}%")
        fonts = {item.font_size for item in tiles}
        font_size = next(iter(fonts)) if len(fonts) == 1 else tile.font_size
        self.font_spin.setValue(font_size if font_size else 0)
        self._sync_font_label(self.font_spin.value())
        self.command_edit.setPlainText(tile.command)
        kind_index = self.match_kind.findData(tile.match_kind)
        self.match_kind.setCurrentIndex(kind_index if kind_index >= 0 else 0)
        self.match_value.setText(tile.match_value)
        self.app_search.clear()
        self._refill_apps(prefer=tile.app_id)
        self._set_mixed_check(self.show_title, {item.show_title for item in tiles}, bulk)
        self._set_mixed_check(self.hide_toolbar, {item.hide_toolbar for item in tiles}, bulk)
        self._set_mixed_check(self.no_border, {item.no_border for item in tiles}, bulk)
        self._set_mixed_check(self.skip_taskbar, {item.skip_taskbar for item in tiles}, bulk)
        if bulk:
            self._show_bulk()
        else:
            self.launch_button.setText("Launch this tile")
            self._show_mode(tile.launch_mode)
        self._loading = False
        self.refresh_preview()

    def _set_mixed_check(self, box: QCheckBox, values: set[bool], bulk: bool) -> None:
        box.blockSignals(True)
        if bulk:
            box.setTristate(True)
            if len(values) == 1:
                box.setCheckState(Qt.CheckState.Checked if next(iter(values)) else Qt.CheckState.Unchecked)
            else:
                box.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            box.setTristate(False)
            box.setChecked(bool(next(iter(values))) if values else False)
        box.blockSignals(False)

    def _show_gap_only(self) -> None:
        self.form.setRowVisible(0, False)
        self.form.setRowVisible(1, False)
        for widget in (
            self.terminal_caption,
            self.terminal_combo,
            self.shell_edit,
            self.show_title,
            self.hide_toolbar,
            self.look_caption,
            self.opacity_caption,
            self.opacity_row,
            self.font_caption,
            self.font_row,
            self.app_caption,
            self.app_search,
            self.app_combo,
            self.command_caption,
            self.command_edit,
            self.match_kind,
            self.match_value,
            self.no_border,
            self.skip_taskbar,
            self.warning,
            self.launch_button,
        ):
            widget.setVisible(False)

    def _show_bulk(self) -> None:
        self.form.setRowVisible(0, False)
        self.form.setRowVisible(1, False)
        helpers = all(tile.launch_mode == "helper" for tile in self._tiles)
        for widget in (
            self.terminal_caption,
            self.terminal_combo,
            self.shell_edit,
            self.app_caption,
            self.app_search,
            self.app_combo,
            self.command_caption,
            self.command_edit,
            self.match_kind,
            self.match_value,
            self.warning,
        ):
            widget.setVisible(False)
        self.look_caption.setVisible(helpers)
        self.opacity_caption.setVisible(True)
        self.opacity_row.setVisible(True)
        self.no_border.setVisible(True)
        self.skip_taskbar.setVisible(True)
        self.launch_button.setVisible(True)
        self.launch_button.setText("Launch these tiles")
        self.show_title.setVisible(helpers)
        self.hide_toolbar.setVisible(helpers and self._all_konsole())
        self.font_caption.setVisible(helpers)
        self.font_row.setVisible(helpers)

    def refresh_preview(self) -> None:
        if len(self._tiles) != 1 or self._tile is None:
            self.warning.clear()
            self.warning.setVisible(False)
            return
        resolved = resolve_tile(self._tile, self._profile_name)
        warning = resolved.warning
        if (
            resolved.match_kind == "class"
            and resolved.match_value
            and resolved.match_value in self._duplicates
        ):
            extra = "Another tile uses the same window class, so KWin cannot tell them apart."
            warning = f"{warning} {extra}".strip()
        self.warning.setText(warning)
        self.warning.setVisible(bool(warning))

    def _show_mode(self, mode: str) -> None:
        self.form.setRowVisible(0, True)
        self.form.setRowVisible(1, True)
        for widget in self._helper_widgets:
            widget.setVisible(mode == "helper")
        self.hide_toolbar.setVisible(mode == "helper" and self._all_konsole())
        for widget in self._app_widgets:
            widget.setVisible(mode == "app")
        for widget in self._raw_widgets:
            widget.setVisible(mode == "raw")
        self.match_kind.setVisible(False)
        self.match_value.setVisible(False)
        self.no_border.setVisible(True)
        self.skip_taskbar.setVisible(True)
        self.launch_button.setVisible(True)
        self.launch_button.setText("Launch this tile")

    def _refill_apps(self, prefer: str = "") -> None:
        current = prefer or (self._tile.app_id if self._tile is not None else "")
        query = self.app_search.text().strip().casefold()
        counts: dict[str, int] = {}
        for app in self._apps:
            counts[app.name] = counts.get(app.name, 0) + 1
        self.app_combo.blockSignals(True)
        self.app_combo.clear()
        for app in self._apps:
            label = app.name if counts[app.name] == 1 else f"{app.name} ({app.app_id})"
            haystack = f"{label} {app.app_id}".casefold()
            if query and query not in haystack:
                continue
            self.app_combo.addItem(label, app.app_id)
        index = self.app_combo.findData(current)
        self.app_combo.setCurrentIndex(index)
        self.app_combo.blockSignals(False)

    def _on_name(self, text: str) -> None:
        if self._loading or self._tile is None:
            return
        self.aboutToEdit.emit("name")
        self._tile.name = text.strip() or "Tile"
        self.edited.emit()

    def _on_mode(self) -> None:
        if self._loading or self._tile is None:
            return
        mode = str(self.mode_combo.currentData() or "raw")
        self.aboutToEdit.emit("")
        self._tile.launch_mode = mode
        self._show_mode(mode)
        self.edited.emit()

    def _on_terminal(self) -> None:
        if self._loading or self._tile is None:
            return
        self.aboutToEdit.emit("")
        self._tile.terminal = str(self.terminal_combo.currentData() or "alacritty")
        self._tile.launch_mode = "helper"
        self.hide_toolbar.setVisible(self._all_konsole())
        self.edited.emit()

    def _on_show_title(self, checked: bool) -> None:
        if self._loading or not self._tiles:
            return
        self.show_title.setTristate(False)
        self.aboutToEdit.emit("")
        for tile in self._tiles:
            tile.show_title = bool(checked)
            if len(self._tiles) == 1:
                tile.launch_mode = "helper"
        self.edited.emit()

    def _all_konsole(self) -> bool:
        return bool(self._tiles) and all(
            tile.launch_mode == "helper" and tile.terminal == "konsole" for tile in self._tiles
        )

    def _on_hide_toolbar(self, checked: bool) -> None:
        if self._loading or not self._tiles:
            return
        self.hide_toolbar.setTristate(False)
        self.aboutToEdit.emit("")
        for tile in self._tiles:
            tile.hide_toolbar = bool(checked)
            if len(self._tiles) == 1:
                tile.launch_mode = "helper"
        self.edited.emit()

    def _font_label(self, value: int) -> str:
        return "Default" if value <= 0 else f"{int(value)} pt"

    def _sync_font_label(self, value: int) -> None:
        self.font_value.setText(self._font_label(value))

    def _clamp_font_size(self) -> None:
        value = self.font_spin.value()
        if 0 < value < 6:
            self.font_spin.setValue(6)

    def _on_font_size(self, value: int) -> None:
        if self._loading or not self._tiles:
            return
        if 0 < value < 6:
            return
        self._sync_font_label(value)
        self.aboutToEdit.emit("font")
        size = 0 if value <= 0 else int(value)
        for tile in self._tiles:
            tile.font_size = size
            if len(self._tiles) == 1:
                tile.launch_mode = "helper"
        self.edited.emit()

    def _on_opacity(self, value: int) -> None:
        if self._loading or not self._tiles:
            return
        self.opacity_value.setText(f"{int(value)}%")
        self.aboutToEdit.emit("opacity")
        level = max(0.1, min(1.0, int(value) / 100.0))
        for tile in self._tiles:
            tile.opacity = level
            if len(self._tiles) == 1:
                tile.launch_mode = "helper"
        self.edited.emit()

    def _on_shell(self, text: str) -> None:
        if self._loading or self._tile is None:
            return
        self.aboutToEdit.emit("shell")
        self._tile.shell_command = text
        self._tile.launch_mode = "helper"
        self.edited.emit()

    def _on_app(self) -> None:
        if self._loading or self._tile is None:
            return
        app_id = self.app_combo.currentData()
        app = self._apps_by_id.get(str(app_id)) if app_id else None
        if app is None:
            return
        self.aboutToEdit.emit("")
        assign_app(self._tile, app)
        self.mode_combo.blockSignals(True)
        index = self.mode_combo.findData(self._tile.launch_mode)
        self.mode_combo.setCurrentIndex(index if index >= 0 else 1)
        self.mode_combo.blockSignals(False)
        self._show_mode(self._tile.launch_mode)
        self.edited.emit()

    def _on_command(self) -> None:
        if self._loading or self._tile is None:
            return
        self.aboutToEdit.emit("command")
        self._tile.command = self.command_edit.toPlainText().strip()
        self._tile.launch_mode = "raw"
        self.edited.emit()

    def _on_match(self) -> None:
        if self._loading or self._tile is None:
            return
        self.aboutToEdit.emit("")
        self._tile.match_kind = str(self.match_kind.currentData() or "")
        self._tile.launch_mode = "raw"
        self.edited.emit()

    def _on_match_value(self, text: str) -> None:
        if self._loading or self._tile is None:
            return
        self.aboutToEdit.emit("match")
        self._tile.match_value = text.strip()
        self._tile.launch_mode = "raw"
        self.edited.emit()

    def _on_border(self, checked: bool) -> None:
        if self._loading or not self._tiles:
            return
        self.no_border.setTristate(False)
        self.aboutToEdit.emit("")
        for tile in self._tiles:
            tile.no_border = bool(checked)
        self.edited.emit()

    def _on_taskbar(self, checked: bool) -> None:
        if self._loading or not self._tiles:
            return
        self.skip_taskbar.setTristate(False)
        self.aboutToEdit.emit("")
        for tile in self._tiles:
            tile.skip_taskbar = bool(checked)
        self.edited.emit()
