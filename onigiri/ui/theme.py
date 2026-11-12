"""Charcoal surfaces, one accent, Fusion spinboxes that stay clickable."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen, QRegion
from PyQt6.QtWidgets import QApplication, QFrame, QStackedWidget, QVBoxLayout, QWidget

from onigiri import asset_path

FALLBACK_ACCENT = "#1f8a52"
PANEL_RADIUS = 12.0
PANEL_BORDER = 3.0
PANEL_FILL = QColor("#141414")
POPUP_FILL = QColor("#181818")
BUTTON_FILL = QColor("#1c1c1c")
PANEL_EDGE = QColor("#2a2a2a")
WINDOW_FILL = QColor("#101010")
PANEL_INNER_RADIUS = PANEL_RADIUS - PANEL_BORDER
PANEL_ALPHA = 128
WINDOW_ALPHA = 200


def panel_fill() -> QColor:
    color = QColor(PANEL_FILL)
    color.setAlpha(PANEL_ALPHA)
    return color


def window_wash() -> QColor:
    color = QColor(WINDOW_FILL)
    color.setAlpha(WINDOW_ALPHA)
    return color


def _frame_pen() -> QPen:
    pen = QPen(PANEL_EDGE, PANEL_BORDER)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def _paint_rounded_frame(painter: QPainter, bounds: QRectF, fill: QColor | None) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = PANEL_BORDER / 2
    stroke = bounds.adjusted(inset, inset, -inset, -inset)
    radius = PANEL_RADIUS - inset
    if fill is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(stroke, radius, radius)
    painter.setPen(_frame_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(stroke, radius, radius)


class _PanelChrome(QWidget):
    """Sits on the frame ring so square fills cannot cut the rounded border."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panelChrome")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self.width()
        height = self.height()
        radius = int(PANEL_RADIUS)
        strip = int(PANEL_BORDER) + 1
        region = QRegion(0, 0, width, strip)
        region += QRegion(0, height - strip, width, strip)
        region += QRegion(0, 0, strip, height)
        region += QRegion(width - strip, 0, strip, height)
        region += QRegion(0, 0, radius, radius)
        region += QRegion(width - radius, 0, radius, radius)
        region += QRegion(0, height - radius, radius, radius)
        region += QRegion(width - radius, height - radius, radius, radius)
        self.setMask(region)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        radius = PANEL_RADIUS
        width = bounds.width()
        height = bounds.height()
        painter.setPen(Qt.PenStyle.NoPen)
        corners = (
            (QRectF(0, 0, radius, radius), radius, radius),
            (QRectF(width - radius, 0, radius, radius), width - radius, radius),
            (QRectF(0, height - radius, radius, radius), radius, height - radius),
            (QRectF(width - radius, height - radius, radius, radius), width - radius, height - radius),
        )
        fill = self.parent()._fill if self.parent() is not None and hasattr(self.parent(), "_fill") else panel_fill()
        for square, cx, cy in corners:
            painter.save()
            painter.setClipRect(square)
            painter.setBrush(fill)
            painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
            painter.restore()
        _paint_rounded_frame(painter, bounds, None)
        painter.end()


class Panel(QFrame):
    """Rounded frame whose fill and border stay in front of child rectangles."""

    def __init__(self, parent=None, *, fill: QColor | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self._fill = QColor(fill) if fill is not None else panel_fill()
        self._chrome = _PanelChrome(self)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._chrome.setGeometry(self.rect())
        self._chrome.raise_()

    def childEvent(self, event) -> None:
        super().childEvent(event)
        chrome = getattr(self, "_chrome", None)
        if chrome is not None:
            chrome.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_rounded_frame(painter, QRectF(self.rect()), self._fill)
        painter.end()


class BareStack(QStackedWidget):
    def paintEvent(self, event) -> None:
        return


class Shell(QWidget):
    """Window wash with holes so panel cards stay more see-through than the gutters."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:
        wash = QPainterPath()
        wash.addRect(QRectF(self.rect()))
        holes = QPainterPath()
        for panel in self.findChildren(Panel):
            if not panel.isVisible() or panel.parentWidget() is None:
                continue
            top_left = panel.mapTo(self, panel.rect().topLeft())
            hole = QRectF(top_left.x(), top_left.y(), panel.width(), panel.height())
            holes.addRoundedRect(hole, PANEL_RADIUS, PANEL_RADIUS)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(wash.subtracted(holes))
        painter.fillRect(self.rect(), window_wash())
        painter.end()


def wrap_panel(child: QWidget, *, fill: QColor | None = None) -> Panel:
    frame = Panel(fill=fill)
    layout = QVBoxLayout(frame)
    inset = int(PANEL_BORDER)
    layout.setContentsMargins(inset, inset, inset, inset)
    layout.setSpacing(0)
    layout.addWidget(child)
    return frame


def accent_hex(app: QApplication | None = None) -> str:
    instance = app or QApplication.instance()
    if instance is None:
        return FALLBACK_ACCENT
    color = instance.palette().color(QPalette.ColorRole.Highlight)
    if color.lightness() < 40 or color.lightness() > 200:
        return FALLBACK_ACCENT
    return color.name()


def accent_qcolor(app: QApplication | None = None) -> QColor:
    return QColor(accent_hex(app))


STYLESHEET = """
QWidget {{
    color: #f3f3f3;
    font-size: 13px;
}}
QMainWindow {{
    background: transparent;
}}
QDialog#sceneDialog, QDialog#rulesDialog {{
    background: #101010;
}}
QColorDialog {{
    background-color: #181818;
}}
QFrame#panel {{
    background: transparent;
    border: none;
}}
QWidget#canvas {{
    background: transparent;
    border: none;
}}
QFrame#sidePanel, QWidget#sidePage, QStackedWidget#rightStack {{
    background: transparent;
    border: none;
}}
QLabel#brand {{
    font-size: 16px;
    font-weight: 700;
}}
QListWidget#navList {{
    background: transparent;
    border: none;
    outline: none;
    padding: 4px;
    border-radius: 8px;
}}
QListWidget#navList::item {{
    padding: 10px 12px 10px 8px;
    border-radius: 10px;
    margin: 2px 0;
}}
QListWidget#navList::item:selected {{
    background: {accent};
    color: white;
}}
QListWidget#navList::item:hover:!selected {{
    background: #222222;
}}
QLabel#muted {{
    color: #8a8a8a;
}}
QLabel#value {{
    color: #f3f3f3;
    min-width: 44px;
}}
QLabel#warning {{
    color: #e2b657;
}}
QLabel#section {{
    color: #8a8a8a;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#hexValue {{
    color: #c8c8c8;
    font-family: "JetBrains Mono", "Noto Sans Mono", monospace;
    font-size: 12px;
    min-width: 72px;
}}
QPushButton, QToolButton {{
    background: #1c1c1c;
    color: #f3f3f3;
    border: {border}px solid #2c2c2c;
    border-radius: 8px;
    padding: 6px 12px;
    min-height: 28px;
}}
QPushButton:hover, QToolButton:hover {{
    border-color: {accent};
}}
QPushButton:pressed, QToolButton:pressed {{
    background: #161616;
}}
QPushButton:disabled, QToolButton:disabled {{
    color: #666666;
    border-color: #242424;
}}
QPushButton#primary {{
    background: {accent};
    border-color: {accent};
    color: white;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton#colorSwatch {{
    padding: 0;
    min-height: 28px;
    max-height: 28px;
    border-radius: 10px;
    border: {border}px solid #2c2c2c;
}}
QToolButton#toolIcon {{
    background: transparent;
    border: {border}px solid transparent;
    border-radius: 10px;
    padding: 4px;
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
}}
QToolButton#toolIcon:hover {{
    background: #1c1c1c;
    border-color: {accent};
}}
QToolButton#toolIcon:disabled {{
    color: #4a4a4a;
}}
QToolButton::menu-indicator {{
    image: none;
    width: 0;
}}
QWidget#cursorPopup {{
    background: transparent;
    border: {border}px solid {accent};
}}
QWidget#overlayTiler {{
    background: transparent;
}}
QWidget#appPickerPopup {{
    background: #181818;
    border: {border}px solid {accent};
    border-radius: 12px;
}}
QListWidget#popupAppList {{
    background: #181818;
    color: #f3f3f3;
    border: none;
    outline: none;
    padding: 4px;
}}
QLineEdit#popupAppSearch {{
    background: #141414;
    color: #f3f3f3;
    border: {border}px solid #2c2c2c;
    border-radius: 10px;
    padding: 10px 12px;
    margin: 0;
    font-size: 14px;
}}
QLineEdit#popupAppSearch:focus {{
    border-color: {accent};
}}
QListWidget#popupAppList::item {{
    padding: 6px 8px;
    border-radius: 8px;
}}
QListWidget#popupAppList::item:hover,
QListWidget#popupAppList::item:selected {{
    background: {accent};
}}
QFrame#hotkeyChip {{
    background: #1c1c1c;
    border: {border}px solid #2c2c2c;
    border-radius: 10px;
}}
QFrame#hotkeyChip QLabel {{
    color: #f3f3f3;
    padding-right: 2px;
}}
QToolButton#chipRemove {{
    background: transparent;
    border: none;
    min-width: 18px;
    max-width: 18px;
    min-height: 18px;
    max-height: 18px;
    padding: 0;
    color: #8a8a8a;
    font-size: 14px;
}}
QToolButton#chipRemove:hover {{
    color: #f3f3f3;
}}
QLineEdit, QPlainTextEdit, QComboBox {{
    background: #141414;
    border: {border}px solid #2c2c2c;
    border-radius: 10px;
    padding: 6px 10px;
    selection-background-color: {accent};
}}
QComboBox#navCombo {{
    min-width: 140px;
    padding: 6px 12px;
}}
QSpinBox {{
    background: #141414;
    border: {border}px solid #2c2c2c;
    border-radius: 10px;
    padding: 4px 28px 4px 10px;
    selection-background-color: {accent};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    background: #242424;
    border: none;
    width: 20px;
}}
QSpinBox::up-button {{
    subcontrol-position: top right;
    margin: {border}px {border}px 0 0;
    border-top-right-radius: 7px;
}}
QSpinBox::down-button {{
    subcontrol-position: bottom right;
    margin: 0 {border}px {border}px 0;
    border-bottom-right-radius: 7px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {accent};
}}
QSpinBox::up-arrow {{
    image: url({spin_up});
    width: 8px;
    height: 8px;
}}
QSpinBox::down-arrow {{
    image: url({spin_down});
    width: 8px;
    height: 8px;
}}
QPlainTextEdit {{
    font-family: monospace;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: url({spin_down});
    width: 8px;
    height: 8px;
}}
QComboBox QAbstractItemView {{
    background: #181818;
    color: #f3f3f3;
    selection-background-color: {accent};
    border: {border}px solid #2c2c2c;
}}
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
    padding: 4px;
}}
QListWidget::item {{
    padding: 7px 8px;
    border-radius: 8px;
}}
QListWidget::item:selected {{
    background: {accent};
    color: white;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: #2a2a2a;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    background: {accent};
    border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background: {accent};
    border-radius: 2px;
}}
QCheckBox {{
    spacing: 8px;
}}
QMenu {{
    background: #181818;
    color: #f3f3f3;
    border: 1px solid #2c2c2c;
    padding: 4px;
    border-radius: 10px;
}}
QMenu::item {{
    padding: 6px 16px 6px 28px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {accent};
}}
QMenu::item:disabled {{
    color: #666666;
}}
QStatusBar {{
    background: #101010;
    color: #8a8a8a;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollArea > QWidget {{
    background: transparent;
}}
QScrollArea#filmstrip {{
    background: transparent;
    border: none;
}}
QScrollArea#filmstrip > QWidget > QWidget {{
    background: transparent;
}}
QWidget#playlistBar {{
    background: transparent;
    border: none;
}}
QWidget#playlistBar QPushButton {{
    padding: 1px 8px;
    min-height: 22px;
    font-size: 11px;
    font-weight: 500;
}}
QWidget#playlistBar QSpinBox {{
    max-width: 58px;
    min-height: 22px;
    padding: 0 20px 0 6px;
    font-size: 11px;
    border-radius: 6px;
}}
QWidget#playlistBar QSpinBox::up-button, QWidget#playlistBar QSpinBox::down-button {{
    width: 14px;
}}
QWidget#playlistBar QComboBox {{
    padding: 0 6px;
    min-width: 0;
    min-height: 22px;
    font-size: 11px;
    border-radius: 6px;
}}
QLabel#scenePreview, QWidget#scenePreview {{
    background: #181818;
    border: {border}px solid #2a2a2a;
    border-radius: 10px;
    color: #8a8a8a;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0 4px;
}}
QScrollBar::handle:horizontal {{
    background: #343434;
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: #343434;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QToolButton#sectionToggle {{
    background: transparent;
    border: none;
    color: #8a8a8a;
    font-size: 11px;
    font-weight: 700;
    padding: 4px 0;
    text-align: left;
}}
QToolButton#sectionToggle:hover {{
    color: #f3f3f3;
    border: none;
    background: transparent;
}}
"""


def _hover(hex_color: str) -> str:
    color = QColor(hex_color)
    return color.lighter(118).name()


def _asset_url(name: str) -> str:
    return f'"{Path(asset_path(name)).resolve().as_posix()}"'


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    accent = accent_hex(app)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#101010"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#141414"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#181818"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1c1c1c"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#181818"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8a8a8a"))
    app.setPalette(palette)
    app.setStyleSheet(
        STYLESHEET.format(
            accent=accent,
            accent_hover=_hover(accent),
            border=int(PANEL_BORDER),
            spin_up=_asset_url("spin_up.svg"),
            spin_down=_asset_url("spin_down.svg"),
        )
    )
