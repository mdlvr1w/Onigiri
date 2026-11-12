"""A single color swatch button for plugin fields in the scene builder."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QLineEdit,
    QPushButton,
    QSlider,
    QWidget,
)

from onigiri.ui.theme import BUTTON_FILL, PANEL_BORDER, PANEL_FILL, POPUP_FILL


class ColorSwatch(QPushButton):
    colorChanged = pyqtSignal(QColor)

    def __init__(self, color: QColor, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("colorSwatch")
        self.setFixedHeight(28)
        self.set_color(color)

    def set_color(self, color: QColor) -> None:
        self.color = QColor(color)
        self.setStyleSheet(
            "QPushButton#colorSwatch {"
            f"background: {self.color.name()};"
            f"border: {int(PANEL_BORDER)}px solid #343434;"
            "border-radius: 8px;"
            "padding: 0;"
            "}"
        )
        self.colorChanged.emit(self.color)

    def rgb(self) -> tuple[int, int, int]:
        return (self.color.red(), self.color.green(), self.color.blue())


_WELL_CLASSES = frozenset(
    {
        "QColorPicker",
        "QColorLuminancePicker",
        "QWellArray",
        "QColorShowLabel",
    }
)


def _gray_chrome(root: QWidget, palette: QPalette) -> None:
    root.setPalette(palette)
    root.setAutoFillBackground(True)
    root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    for child in root.findChildren(QWidget):
        child.setPalette(palette)
        if child.metaObject().className() in _WELL_CLASSES:
            child.setAutoFillBackground(False)
            continue
        if isinstance(child, (QAbstractSpinBox, QLineEdit, QAbstractButton, QComboBox, QSlider)):
            continue
        child.setAutoFillBackground(True)
        child.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def color_dialog(color: QColor, parent: QWidget | None = None, title: str = "Color") -> QColorDialog:
    """Stock picker on a gray surface so wells and spin arrows stay visible."""

    dialog = QColorDialog(color, parent)
    dialog.setWindowTitle(title)
    dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
    palette = dialog.palette()
    palette.setColor(QPalette.ColorRole.Window, POPUP_FILL)
    palette.setColor(QPalette.ColorRole.Base, PANEL_FILL)
    palette.setColor(QPalette.ColorRole.AlternateBase, POPUP_FILL)
    palette.setColor(QPalette.ColorRole.Button, BUTTON_FILL)
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f3f3f3"))
    dialog.setPalette(palette)
    dialog.setStyleSheet(f"QColorDialog {{ background-color: {POPUP_FILL.name()}; }}")
    _gray_chrome(dialog, palette)
    return dialog


def pick_color(parent: QWidget | None, color: QColor, title: str = "Color") -> QColor:
    dialog = color_dialog(color, parent, title)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.currentColor()
    return QColor()
