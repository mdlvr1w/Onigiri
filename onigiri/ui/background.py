"""Shared background choice for scene creation and the docked scene editor."""
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from onigiri.ui.colors import ColorSwatch, pick_color


class BackgroundSelector(QWidget):
    changed = pyqtSignal()
    pictureRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.mode = QComboBox()
        self.mode.addItem("Keep current background", "keep")
        self.mode.addItem("Picture", "image")
        self.mode.addItem("Solid color", "color")
        self.mode.setAccessibleName("Scene background")
        self.picture_path = QLineEdit()
        self.picture_path.setReadOnly(True)
        self.picture_path.setPlaceholderText("No picture selected")
        self.picture_button = QPushButton("Choose picture")
        self.picture_button.clicked.connect(self.pictureRequested)
        self.color_swatch = ColorSwatch(QColor("#141414"))
        self.color_swatch.clicked.connect(self._pick_color)
        self.color_swatch.colorChanged.connect(self._color_changed)
        self.color_label = QLabel()
        self.color_label.setObjectName("hexValue")
        self.color_row = QWidget()
        row = QHBoxLayout(self.color_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.color_swatch, 1)
        row.addWidget(self.color_label)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.mode)
        layout.addWidget(self.hint)
        layout.addWidget(self.picture_button)
        layout.addWidget(self.picture_path)
        layout.addWidget(self.color_row)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._sync_controls()

    def wallpaper(self):
        return self.picture_path.text().strip() if self.mode.currentData() == "image" else ""

    def background_color(self):
        return self.color_swatch.rgb() if self.mode.currentData() == "color" else None

    def ready(self):
        return self.mode.currentData() != "image" or bool(self.wallpaper() and Path(self.wallpaper()).is_file())

    def set_background(self, wallpaper="", color=None):
        self._loading = True
        try:
            self.picture_path.setText(wallpaper)
            if color is not None:
                self.color_swatch.set_color(QColor(*color))
            choice = "color" if color is not None else "image" if wallpaper else "keep"
            self.mode.setCurrentIndex(self.mode.findData(choice))
            self._sync_controls()
        finally:
            self._loading = False

    def _sync_controls(self):
        mode = self.mode.currentData()
        self.picture_button.setVisible(mode == "image")
        self.picture_path.setVisible(mode == "image")
        self.color_row.setVisible(mode == "color")
        self.color_label.setText(self.color_swatch.color.name().upper())
        self.hint.setText({
            "keep": "Apply this scene’s extensions without changing the desktop background.",
            "image": "Choose a picture, then drag it on a screen below to adjust the crop.",
            "color": "Choose a color for every screen’s desktop background.",
        }[mode])

    def _mode_changed(self):
        self._sync_controls()
        if not self._loading:
            self.changed.emit()

    def _color_changed(self):
        self._sync_controls()
        if not self._loading and self.mode.currentData() == "color":
            self.changed.emit()

    def _pick_color(self):
        color = pick_color(self, self.color_swatch.color, "Background color")
        if color.isValid():
            self.color_swatch.set_color(color)
