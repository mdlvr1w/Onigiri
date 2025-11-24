#!/usr/bin/env python3
import sys
import os
from pathlib import Path
from typing import Any, Dict, List

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QComboBox,
    QTextEdit,
    QPushButton,
    QMessageBox,
    QSizePolicy,
    QCheckBox,
    QSystemTrayIcon,
    QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QStandardPaths, QSettings
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QMouseEvent, QIcon, QAction

import onigiri  # engine module


# ===================== Layout Canvas =====================


class LayoutCanvas(QWidget):
    """
    Visual screen preview for the current profile.
    - Draws rectangles for tiles (scaled to fit canvas)
    - Lets you drag tiles to move them
    - On mouse release, snaps tile position (not size) to grid / edges
    """

    tileSelected = pyqtSignal(int)       # tile index clicked
    geometryChanged = pyqtSignal(int)    # tile index moved/snap-updated

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile: Dict[str, Any] | None = None
        self._tiles: List[Dict[str, Any]] = []
        self._selected_index: int | None = None

        # for interaction
        self._rects: Dict[int, tuple[float, float, float, float]] = {}  # idx -> (x, y, w, h) in canvas coords
        self._dragging_index: int | None = None
        self._last_mouse_pos: QPointF | None = None
        self._scale: float = 1.0
        self._offset_x: float = 0.0
        self._offset_y: float = 0.0

        # visual gap between tiles (canvas only)
        self._gap: int = 0

        # snapping grid (in screen/world pixels)
        self._grid_size: int = 32

        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ----- data binding -----

    def set_profile(self, profile: Dict[str, Any] | None) -> None:
        self._profile = profile
        if profile is None:
            self._tiles = []
            self._gap = 0
        else:
            self._tiles = profile.setdefault("tiles", [])
            self._gap = int(profile.get("tile_gap", 0) or 0)

        self._selected_index = None
        self._dragging_index = None
        self._recompute_rects()
        self.update()

    def set_selected_index(self, idx: int | None) -> None:
        self._selected_index = idx
        self.update()

    # ----- helpers -----

    def _compute_screen_bbox(self) -> tuple[int, int]:
        """
        Use the actual primary screen resolution as world coordinates.
        This way, tile x/y/width/height map 1:1 to real pixels.
        """
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.geometry()
            screen_w = geo.width()
            screen_h = geo.height()
        else:
            # Fallback if Qt can't see a screen
            screen_w, screen_h = 1920, 1080

        return max(screen_w, 1), max(screen_h, 1)

    def _world_to_canvas(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        cx = self._offset_x + x * self._scale
        cy = self._offset_y + y * self._scale
        cw = w * self._scale
        ch = h * self._scale
        return cx, cy, cw, ch

    def _canvas_to_world_delta(self, dx: float, dy: float) -> tuple[float, float]:
        if self._scale <= 0:
            return 0.0, 0.0
        return dx / self._scale, dy / self._scale

    def _recompute_rects(self) -> None:
        """Recompute mapping from tile index to canvas rectangles."""
        self._rects.clear()

        screen_w, screen_h = self._compute_screen_bbox()
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        margin = 20

        usable_w = max(w - 2 * margin, 1)
        usable_h = max(h - 2 * margin, 1)

        sx = usable_w / screen_w
        sy = usable_h / screen_h
        self._scale = min(sx, sy)

        # Center the virtual screen
        canvas_w = screen_w * self._scale
        canvas_h = screen_h * self._scale
        self._offset_x = (w - canvas_w) / 2.0
        self._offset_y = (h - canvas_h) / 2.0

        for idx, t in enumerate(self._tiles):
            x = float(t.get("x", 0))
            y = float(t.get("y", 0))
            tw = float(t.get("width", 800))
            th = float(t.get("height", 600))
            cx, cy, cw, ch = self._world_to_canvas(x, y, tw, th)
            self._rects[idx] = (cx, cy, cw, ch)

    def _work_area(self) -> tuple[int, int, int, int]:
        """
        Compute usable work area in world coords.
        NOW: simply the full screen, no taskbar math.
        Returns (x0, y0, x1, y1).
        """
        screen_w, screen_h = self._compute_screen_bbox()
        x0, y0, x1, y1 = 0, 0, screen_w, screen_h
        return x0, y0, x1, y1

    def _snap_tile_to_grid_and_edges(self, tile: Dict[str, Any]) -> None:
        """
        Snap tile position (NOT size) to grid and to the screen edges.
        Modifies tile["x"],["y"] in-place.
        """
        grid = max(self._grid_size, 1)
        x0, y0, x1, y1 = self._work_area()

        x = float(tile.get("x", 0))
        y = float(tile.get("y", 0))
        w = float(tile.get("width", 800))
        h = float(tile.get("height", 600))

        def snap(v: float) -> int:
            return int(round(v / grid) * grid)

        left = snap(x)
        top = snap(y)

        margin = grid  # how close to snap to edge

        # Snap to edges if close (without changing width/height)
        if abs(left - x0) < margin:
            left = x0
        if abs((left + w) - x1) < margin:
            left = x1 - w
        if abs(top - y0) < margin:
            top = y0
        if abs((top + h) - y1) < margin:
            top = y1 - h

        # Clamp so the whole tile stays in screen
        if left < x0:
            left = x0
        if top < y0:
            top = y0
        if left + w > x1:
            left = x1 - w
        if top + h > y1:
            top = y1 - h

        tile["x"] = int(left)
        tile["y"] = int(top)

    # ----- painting -----

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute_rects()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # background
        painter.fillRect(self.rect(), QColor(15, 15, 20))

        screen_w, screen_h = self._compute_screen_bbox()

        if not self._tiles:
            painter.setPen(QColor(120, 120, 130))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                f"No tiles in this profile yet\nScreen: {screen_w}×{screen_h}",
            )
            return

        # draw virtual desktop outline
        sw_cx, sw_cy, sw_cw, sw_ch = self._world_to_canvas(0, 0, screen_w, screen_h)
        painter.setPen(QPen(QColor(80, 80, 90), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(
            int(sw_cx),
            int(sw_cy),
            int(sw_cw),
            int(sw_ch),
        )

        # draw each tile
        for idx, rect in self._rects.items():
            x, y, w, h = rect

            # visual gap: shrink inside rect by gap amount
            gap = float(self._gap)
            if gap > 0:
                gx = x + gap / 2.0
                gy = y + gap / 2.0
                gw = max(0.0, w - gap)
                gh = max(0.0, h - gap)
            else:
                gx, gy, gw, gh = x, y, w, h

            selected = (self._selected_index == idx)

            if selected:
                fill = QColor(120, 210, 255, 160)
                border = QColor(120, 230, 255)
            else:
                fill = QColor(180, 180, 180, 90)
                border = QColor(230, 230, 230, 200)

            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(border, 2))
            painter.drawRect(
                int(gx),
                int(gy),
                int(gw),
                int(gh),
            )

            # tile name
            if gw > 40 and gh > 20:
                painter.setPen(QColor(10, 10, 15))
                name = self._tiles[idx].get("name", f"Tile {idx}")
                painter.drawText(
                    int(gx) + 6,
                    int(gy) + 18,
                    str(name)[:32],
                )

    # ----- interaction -----

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        pos = event.position()
        clicked_idx = None

        # Prefer topmost (last drawn) tile
        for idx in reversed(list(self._rects.keys())):
            x, y, w, h = self._rects[idx]
            if x <= pos.x() <= x + w and y <= pos.y() <= y + h:
                clicked_idx = idx
                break

        if clicked_idx is not None:
            self._dragging_index = clicked_idx
            self._last_mouse_pos = pos
            self._selected_index = clicked_idx
            self.tileSelected.emit(clicked_idx)
            self.update()
        else:
            self._dragging_index = None
            self._last_mouse_pos = None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging_index is None or self._last_mouse_pos is None:
            return super().mouseMoveEvent(event)

        pos = event.position()
        dx_canvas = pos.x() - self._last_mouse_pos.x()
        dy_canvas = pos.y() - self._last_mouse_pos.y()

        dx_world, dy_world = self._canvas_to_world_delta(dx_canvas, dy_canvas)

        idx = self._dragging_index
        if 0 <= idx < len(self._tiles):
            t = self._tiles[idx]

            # Current geometry in world coords
            cur_x = float(t.get("x", 0))
            cur_y = float(t.get("y", 0))
            w = float(t.get("width", 800))
            h = float(t.get("height", 600))

            # Move
            new_x = cur_x + dx_world
            new_y = cur_y + dy_world

            # Clamp to screen
            x0, y0, x1, y1 = self._work_area()
            max_x = max(x0, x1 - w)
            max_y = max(y0, y1 - h)

            if new_x < x0:
                new_x = x0
            if new_y < y0:
                new_y = y0
            if new_x > max_x:
                new_x = max_x
            if new_y > max_y:
                new_y = max_y

            t["x"] = int(new_x)
            t["y"] = int(new_y)

            # Live update into editor / model
            self.geometryChanged.emit(idx)

        self._last_mouse_pos = pos
        self._recompute_rects()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging_index is not None:
            idx = self._dragging_index
            self._dragging_index = None
            self._last_mouse_pos = None

            # Final snap of position (not size) when you drop the tile
            if 0 <= idx < len(self._tiles):
                self._snap_tile_to_grid_and_edges(self._tiles[idx])
                self.geometryChanged.emit(idx)
                self._recompute_rects()
                self.update()

        super().mouseReleaseEvent(event)


# ===================== Tile Editor =====================


class TileEditor(QWidget):
    """
    Right-side editor for a single tile.
    Edits the in-memory data structure passed in, doesn’t touch disk directly.
    """
    geometryEdited = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_profile: Dict[str, Any] | None = None
        self.current_tile: Dict[str, Any] | None = None
        self._loading: bool = False
        self._apps: List[Dict[str, str]] = []

        layout = QFormLayout()
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        # Basic geometry
        self.name_edit = QLineEdit()
        self.x_spin = QSpinBox()
        self.y_spin = QSpinBox()
        self.w_spin = QSpinBox()
        self.h_spin = QSpinBox()

        for spin in (self.x_spin, self.y_spin, self.w_spin, self.h_spin):
            spin.setRange(0, 10000)
            spin.valueChanged.connect(self._on_geometry_spin_changed)

        # Match config
        self.match_type_combo = QComboBox()
        self.match_type_combo.addItems(["class", "title", "regex-title", "none"])
        self.match_value_edit = QLineEdit()

        # Terminal / application launch mode
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Raw command", "Terminal helper", "Application"])

        self.terminal_combo = QComboBox()
        self.terminal_combo.addItems(["alacritty", "konsole", "kitty", "xterm"])

        self.shell_command_edit = QLineEdit()
        self.shell_command_edit.setPlaceholderText("e.g. btop, fastfetch, htop")

        # Application selection
        self.app_combo = QComboBox()
        self.app_combo.setEditable(False)

        # Command display (read-only when using helper / application mode)
        self.command_edit = QTextEdit()
        self.command_edit.setFixedHeight(80)

        # Flags
        self.no_border_check = QCheckBox("No window border / titlebar")
        self.skip_taskbar_check = QCheckBox("Skip taskbar")

        # Add to form
        layout.addRow("Tile name:", self.name_edit)
        layout.addRow("X:", self.x_spin)
        layout.addRow("Y:", self.y_spin)
        layout.addRow("Width:", self.w_spin)
        layout.addRow("Height:", self.h_spin)

        layout.addRow(QLabel("Match settings"))
        layout.addRow("Match type:", self.match_type_combo)
        layout.addRow("Match value:", self.match_value_edit)

        layout.addRow(QLabel("Launch mode"))
        layout.addRow("Mode:", self.mode_combo)
        layout.addRow("Terminal app:", self.terminal_combo)
        layout.addRow("Shell command:", self.shell_command_edit)
        layout.addRow("Application:", self.app_combo)
        layout.addRow("Command (advanced):", self.command_edit)

        layout.addRow(self.no_border_check)
        layout.addRow(self.skip_taskbar_check)

        self.setLayout(layout)

        # Signals
        self.mode_combo.currentIndexChanged.connect(self._update_mode_enabled_state)
        self.terminal_combo.currentIndexChanged.connect(self._recompute_command_from_helper)
        self.shell_command_edit.textChanged.connect(self._recompute_command_from_helper)
        self.name_edit.textChanged.connect(self._recompute_command_from_helper)
        self.app_combo.currentIndexChanged.connect(self._on_app_changed)

        # Load available applications into the dropdown
        self._load_applications()

        # Initialize mode state once
        self._update_mode_enabled_state()

    def _on_geometry_spin_changed(self, value: int) -> None:
        """
        Called whenever X/Y/Width/Height spinboxes change.
        Lets the MainWindow know geometry changed so it can flush + repaint.
        """
        if self._loading:
            return
        if not self.current_tile:
            return
        self.geometryEdited.emit()

    def _load_applications(self) -> None:
        """
        Populate app_combo with applications from .desktop files.
        """
        self._apps = []
        self.app_combo.blockSignals(True)
        self.app_combo.clear()

        locations = QStandardPaths.standardLocations(QStandardPaths.StandardLocation.ApplicationsLocation)
        seen_ids = set()

        for base in locations:
            if not base:
                continue
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for fname in files:
                    if not fname.endswith(".desktop"):
                        continue
                    path = os.path.join(root, fname)

                    # Use relative path as an ID to avoid collisions across dirs
                    app_id = os.path.relpath(path, base)
                    if app_id in seen_ids:
                        continue
                    seen_ids.add(app_id)

                    settings = QSettings(path, QSettings.Format.IniFormat)
                    settings.beginGroup("Desktop Entry")
                    name = settings.value("Name", "")
                    exec_cmd = settings.value("Exec", "")
                    no_display = settings.value("NoDisplay", "false")
                    settings.endGroup()

                    if not name or not exec_cmd:
                        continue

                    if str(no_display).lower() == "true":
                        continue

                    # Remove field codes like %U, %u, %F, etc.
                    parts = str(exec_cmd).split()
                    cleaned_parts = [p for p in parts if not p.startswith("%")]
                    cleaned_exec = " ".join(cleaned_parts).strip()
                    if not cleaned_exec:
                        continue

                    display_name = str(name)
                    index = self.app_combo.count()
                    self.app_combo.addItem(display_name)
                    self.app_combo.setItemData(
                        index,
                        {"id": app_id, "name": display_name, "exec": cleaned_exec},
                        role=Qt.ItemDataRole.UserRole,
                    )
                    self._apps.append({"id": app_id, "name": display_name, "exec": cleaned_exec})

        self.app_combo.blockSignals(False)

    def _update_mode_enabled_state(self) -> None:
        mode = self.mode_combo.currentText()
        use_helper = mode == "Terminal helper"
        use_app = mode == "Application"

        # Terminal helper controls
        self.terminal_combo.setEnabled(use_helper)
        self.shell_command_edit.setEnabled(use_helper)

        # Application dropdown
        self.app_combo.setEnabled(use_app)

        # Command editor is read-only for helper + app, editable for raw
        self.command_edit.setReadOnly(use_helper or use_app)

        # Only auto-rebuild command when changing into helper/app mode and not loading
        if self._loading:
            return

        if use_helper:
            self._recompute_command_from_helper()
        elif use_app:
            self._update_command_from_app()

    def _recompute_command_from_helper(self) -> None:
        # Never touch data while we're loading a tile
        if self._loading:
            return

        if self.mode_combo.currentText() != "Terminal helper":
            return

        terminal = self.terminal_combo.currentText().strip() or "alacritty"
        cmd = self.shell_command_edit.text().strip() or ""

        tile_name = self.name_edit.text().strip() or "Dash Tile"
        wm_class = tile_name.lower().replace(" ", "-")

        if self.match_type_combo.currentText() in ("none", ""):
            self.match_type_combo.setCurrentText("class")
        if not self.match_value_edit.text().strip():
            self.match_value_edit.setText(wm_class)

        if terminal == "alacritty":
            if cmd:
                built = (
                    f"{terminal} --class {wm_class} --title '{tile_name}' "
                    f"-e bash -lc '{cmd}; exec $SHELL'"
                )
            else:
                built = f"{terminal} --class {wm_class} --title '{tile_name}'"
        elif terminal == "konsole":
            if cmd:
                built = (
                    f"{terminal} --new-tab --hold -p tabtitle='{tile_name}' "
                    f"-e bash -lc '{cmd}; exec $SHELL'"
                )
            else:
                built = f"{terminal} --new-tab -p tabtitle='{tile_name}'"
        else:
            if cmd:
                built = f"{terminal} -e bash -lc '{cmd}; exec $SHELL'"
            else:
                built = terminal

        self.command_edit.setPlainText(built)

    def _update_command_from_app(self) -> None:
        """
        When in Application mode, update the command field from the selected app.
        """
        if self._loading:
            return
        if self.mode_combo.currentText() != "Application":
            return

        idx = self.app_combo.currentIndex()
        if idx < 0:
            return

        data = self.app_combo.itemData(idx, role=Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            exec_cmd = data.get("exec", "") or ""
            self.command_edit.setPlainText(exec_cmd)

    def _on_app_changed(self, index: int) -> None:
        if self._loading:
            return
        if self.mode_combo.currentText() != "Application":
            return
        self._update_command_from_app()

    def load_tile(self, profile: Dict[str, Any], tile: Dict[str, Any]) -> None:
        """Load tile data into the editor widgets."""
        self._loading = True
        self.current_profile = profile
        self.current_tile = tile

        self.name_edit.setText(str(tile.get("name", "")))
        self.x_spin.setValue(int(tile.get("x", 0)))
        self.y_spin.setValue(int(tile.get("y", 0)))
        self.w_spin.setValue(int(tile.get("width", 800)))
        self.h_spin.setValue(int(tile.get("height", 600)))

        match = tile.get("match", {})
        mtype = match.get("type", "none")
        mvalue = match.get("value", "")

        idx = self.match_type_combo.findText(mtype)
        if idx == -1:
            idx = self.match_type_combo.findText("none")
        self.match_type_combo.setCurrentIndex(idx)
        self.match_value_edit.setText(str(mvalue))

        # Flags
        self.no_border_check.setChecked(bool(tile.get("no_border", False)))
        self.skip_taskbar_check.setChecked(bool(tile.get("skip_taskbar", False)))

        # Command + helper meta
        cmd = tile.get("command", "")
        self.command_edit.setPlainText(str(cmd))

        launch_mode = tile.get("launch_mode", "raw")
        shell_cmd = tile.get("shell_command", "")

        self.shell_command_edit.setText(shell_cmd)

        if launch_mode == "helper":
            m = self.mode_combo.findText("Terminal helper")
            if m == -1:
                m = 0
            self.mode_combo.setCurrentIndex(m)

            term = tile.get("terminal_app", "alacritty")
            ti = self.terminal_combo.findText(term)
            if ti == -1:
                ti = 0
            self.terminal_combo.setCurrentIndex(ti)
        elif launch_mode == "app":
            m = self.mode_combo.findText("Application")
            if m == -1:
                m = 0
            self.mode_combo.setCurrentIndex(m)

            # Try to restore selected application
            app_id = tile.get("app_id", "")
            app_name = tile.get("app_name", "")

            selected_index = -1

            if app_id:
                for i in range(self.app_combo.count()):
                    data = self.app_combo.itemData(i, role=Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict) and data.get("id") == app_id:
                        selected_index = i
                        break

            if selected_index == -1 and app_name:
                i = self.app_combo.findText(app_name)
                if i != -1:
                    selected_index = i

            if selected_index >= 0:
                self.app_combo.setCurrentIndex(selected_index)
        else:
            m = self.mode_combo.findText("Raw command")
            if m == -1:
                m = 0
            self.mode_combo.setCurrentIndex(m)

        # Update enabled/readonly states without recomputing commands
        self._update_mode_enabled_state()

        self._loading = False

    def clear(self) -> None:
        """Clear the editor fields."""
        self._loading = True
        self.current_profile = None
        self.current_tile = None
        self.name_edit.clear()
        self.x_spin.setValue(0)
        self.y_spin.setValue(0)
        self.w_spin.setValue(800)
        self.h_spin.setValue(600)
        self.match_type_combo.setCurrentIndex(self.match_type_combo.findText("none"))
        self.match_value_edit.clear()
        self.shell_command_edit.clear()
        self.command_edit.clear()
        self.no_border_check.setChecked(False)
        self.skip_taskbar_check.setChecked(False)
        if self.app_combo.count() > 0:
            self.app_combo.setCurrentIndex(0)
        self.mode_combo.setCurrentIndex(self.mode_combo.findText("Raw command"))
        self._update_mode_enabled_state()
        self._loading = False

    def apply_changes(self) -> None:
        """Write editor values back into the tile dict."""
        if not self.current_tile:
            return

        t = self.current_tile

        t["name"] = self.name_edit.text().strip()
        t["x"] = int(self.x_spin.value())
        t["y"] = int(self.y_spin.value())
        t["width"] = int(self.w_spin.value())
        t["height"] = int(self.h_spin.value())

        mtype = self.match_type_combo.currentText()
        mvalue = self.match_value_edit.text().strip()

        if mtype == "none" or not mvalue:
            t.pop("match", None)
        else:
            t["match"] = {"type": mtype, "value": mvalue}

        # Flags
        t["no_border"] = bool(self.no_border_check.isChecked())
        t["skip_taskbar"] = bool(self.skip_taskbar_check.isChecked())

        mode_text = self.mode_combo.currentText()

        if mode_text == "Terminal helper":
            t["launch_mode"] = "helper"
            term = self.terminal_combo.currentText().strip() or "alacritty"
            t["terminal_app"] = term
            t["shell_command"] = self.shell_command_edit.text().strip()

            # Generate the final command from helper
            self._recompute_command_from_helper()
            cmd_text = self.command_edit.toPlainText().strip()
            if cmd_text:
                t["command"] = cmd_text
            else:
                t.pop("command", None)

            # Application-specific fields not used in this mode
            t.pop("app_id", None)
            t.pop("app_name", None)

        elif mode_text == "Application":
            t["launch_mode"] = "app"
            idx = self.app_combo.currentIndex()
            data = self.app_combo.itemData(idx, role=Qt.ItemDataRole.UserRole) if idx >= 0 else None

            exec_cmd = ""
            if isinstance(data, dict):
                t["app_id"] = data.get("id")
                t["app_name"] = data.get("name")
                exec_cmd = (data.get("exec") or "").strip()
            else:
                t.pop("app_id", None)
                t.pop("app_name", None)

            # No shell command / terminal in this mode
            t["shell_command"] = ""
            t.pop("terminal_app", None)

            if exec_cmd:
                t["command"] = exec_cmd
                self.command_edit.setPlainText(exec_cmd)
            else:
                t.pop("command", None)

        else:
            # Raw command mode
            t["launch_mode"] = "raw"
            # keep shell_command as "raw" text for this tile
            t["shell_command"] = self.shell_command_edit.text().strip()
            t.pop("terminal_app", None)
            t.pop("app_id", None)
            t.pop("app_name", None)

            cmd_text = self.command_edit.toPlainText().strip()
            if cmd_text:
                t["command"] = cmd_text
            else:
                t.pop("command", None)


# ===================== Main Window =====================


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowIcon(self.geticon())

        self.setWindowTitle("Onigiri")
        self.resize(1400, 750)

        # Full config loaded from onigiri.json
        self.data: Dict[str, Any] = onigiri.load_profiles()

        # track indices, not dicts
        self.current_profile_index: int | None = None
        self.current_tile_index: int | None = None

        # internal flag to avoid reacting to programmatic checkbox changes
        self._rules_updating: bool = False

        # === Widgets ===
        main_layout = QHBoxLayout(self)

        # Left: profiles list
        self.profile_list = QListWidget()
        self.profile_list.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.profile_list.setMinimumWidth(180)

        # Middle: tiles list
        self.tile_list = QListWidget()
        self.tile_list.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.tile_list.setMinimumWidth(220)

        # Right-middle: system rules list
        self.rules_list = QListWidget()
        self.rules_list.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.rules_list.setMinimumWidth(260)

        # Right: tile editor
        self.tile_editor = TileEditor()

        # Bottom canvas (profile designer)
        self.canvas = LayoutCanvas()

        # Profile-level settings (tile gap only now)
        self.tile_gap_spin = QSpinBox()
        self.tile_gap_spin.setRange(0, 200)
        self.tile_gap_spin.setValue(0)

        # Bottom buttons
        button_layout = QHBoxLayout()
        self.btn_new_profile = QPushButton("New Profile")
        self.btn_delete_profile = QPushButton("Delete Profile")
        self.btn_new_tile = QPushButton("New Tile")
        self.btn_delete_tile = QPushButton("Delete Tile")
        self.btn_save = QPushButton("Save Config")
        self.btn_apply = QPushButton("Apply Profile (Rules)")
        self.btn_launch = QPushButton("Launch Apps")
        self.btn_autostart = QPushButton("Create Autostart")

        button_layout.addWidget(self.btn_new_profile)
        button_layout.addWidget(self.btn_delete_profile)
        button_layout.addWidget(self.btn_new_tile)
        button_layout.addWidget(self.btn_delete_tile)
        button_layout.addStretch()
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_apply)
        button_layout.addWidget(self.btn_launch)
        button_layout.addWidget(self.btn_autostart)

        # Assemble main layout
        side_layout = QVBoxLayout()
        labels_layout = QHBoxLayout()
        labels_layout.addWidget(QLabel("Profiles"))
        labels_layout.addSpacing(80)
        labels_layout.addWidget(QLabel("Tiles"))
        labels_layout.addSpacing(80)
        labels_layout.addWidget(QLabel("KWin Rules (enabled/disabled)"))

        lists_layout = QHBoxLayout()
        lists_layout.addWidget(self.profile_list)
        lists_layout.addWidget(self.tile_list)
        lists_layout.addWidget(self.rules_list)

        # Profile settings row (only tile gap now)
        profile_settings_layout = QHBoxLayout()
        profile_settings_layout.addWidget(QLabel("Tile gap (visual, px):"))
        profile_settings_layout.addWidget(self.tile_gap_spin)
        profile_settings_layout.addStretch()

        side_layout.addLayout(labels_layout)
        side_layout.addLayout(lists_layout)
        side_layout.addLayout(profile_settings_layout)
        side_layout.addWidget(QLabel("Profile Designer"))
        side_layout.addWidget(self.canvas, stretch=1)
        side_layout.addLayout(button_layout)

        main_layout.addLayout(side_layout)
        main_layout.addWidget(self.tile_editor, stretch=1)

        self.setLayout(main_layout)

        # === Signals ===
        self.profile_list.currentItemChanged.connect(self.on_profile_selected)
        self.tile_list.currentItemChanged.connect(self.on_tile_selected)
        self.rules_list.itemChanged.connect(self.on_rule_toggled)

        self.btn_new_profile.clicked.connect(self.on_new_profile)
        self.btn_delete_profile.clicked.connect(self.on_delete_profile)
        self.btn_new_tile.clicked.connect(self.on_new_tile)
        self.btn_delete_tile.clicked.connect(self.on_delete_tile)
        self.btn_save.clicked.connect(self.on_save_config)
        self.btn_apply.clicked.connect(self.on_apply_profile)
        self.btn_launch.clicked.connect(self.on_launch_apps)
        self.btn_autostart.clicked.connect(self.on_create_autostart)

        # Profile settings changes (only gap)
        self.tile_gap_spin.valueChanged.connect(self.on_profile_settings_changed)

        # Canvas signals
        self.canvas.tileSelected.connect(self.on_canvas_tile_selected)
        self.canvas.geometryChanged.connect(self.on_canvas_geometry_changed)

        # Tile editor live geometry updates -> update model + canvas
        self.tile_editor.geometryEdited.connect(self.flush_tile_edits)

        # Populate initial lists
        self.populate_profiles()
        self.populate_system_rules()

        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(0)

        # ---- System tray icon ----
        self._create_tray_icon()

      # ----- tray -----
    def geticon(self):
        from PyQt6.QtGui import QIcon
        import os

        # Try to load icon from system icon theme
        icon = QIcon.fromTheme("onigiri")

        # Fallback for systems where theme lookup fails
        if icon.isNull():
            local_icon = os.path.join(os.path.dirname(__file__), "onigiri.png")
            if os.path.isfile(local_icon):
                icon = QIcon(local_icon)
            else:
                # Final fallback: use window icon
                icon = self.windowIcon()
        return icon

    def _create_tray_icon(self) -> None:
        icon = self.geticon()
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Onigiri")

        menu = QMenu(self)
        show_action = QAction("Show", self)
        quit_action = QAction("Quit", self)

        show_action.triggered.connect(self._show_from_tray)
        quit_action.triggered.connect(QApplication.instance().quit)

        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible():
                self.hide()
            else:
                self._show_from_tray()

    def closeEvent(self, event) -> None:
        """
        Closing the window just hides it to the tray so the app keeps running.
        Use the tray context menu -> Quit to fully exit.
        """
        if hasattr(self, "tray_icon") and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)

    # ----- Data helpers -----

    def get_profiles(self) -> List[Dict[str, Any]]:
        return self.data.setdefault("profiles", [])

    def get_current_profile(self) -> Dict[str, Any] | None:
        if self.current_profile_index is None:
            return None
        profiles = self.get_profiles()
        if 0 <= self.current_profile_index < len(profiles):
            return profiles[self.current_profile_index]
        return None

    def get_current_tile(self) -> Dict[str, Any] | None:
        profile = self.get_current_profile()
        if not profile:
            return None
        tiles = profile.setdefault("tiles", [])
        if self.current_tile_index is None:
            return None
        if 0 <= self.current_tile_index < len(tiles):
            return tiles[self.current_tile_index]
        return None

    def populate_profiles(self) -> None:
        self.profile_list.clear()
        for idx, profile in enumerate(self.get_profiles()):
            item = QListWidgetItem(profile.get("name", "<unnamed>"))
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.profile_list.addItem(item)

    def populate_tiles(self, profile_index: int | None) -> None:
        self.tile_list.clear()
        if profile_index is None:
            return
        profiles = self.get_profiles()
        if not (0 <= profile_index < len(profiles)):
            return
        profile = profiles[profile_index]
        tiles = profile.setdefault("tiles", [])
        for t_idx, tile in enumerate(tiles):
            item = QListWidgetItem(tile.get("name", "<tile>"))
            item.setData(Qt.ItemDataRole.UserRole, t_idx)
            self.tile_list.addItem(item)

    def populate_system_rules(self) -> None:
        """Read kwinrulesrc and show all rules with checkboxes."""
        self._rules_updating = True
        self.rules_list.clear()
        try:
            rules = onigiri.list_kwin_rules()
        except Exception as e:
            item = QListWidgetItem(f"Error reading kwinrulesrc: {e}")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.rules_list.addItem(item)
            self._rules_updating = False
            return

        for r in rules:
            label = r["description"] or r["id"]
            if r.get("from_kwintiler"):
                label = f"★ {label}"
            item = QListWidgetItem(label)

            # Store the rule ID so we can toggle it later
            item.setData(Qt.ItemDataRole.UserRole, r["id"])

            # Make it checkable
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )

            if r.get("enabled", True):
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)

            self.rules_list.addItem(item)

        self._rules_updating = False

    def load_profile_settings_to_ui(self, profile: Dict[str, Any] | None) -> None:
        """Load tile gap from profile into UI + canvas."""
        if not profile:
            self.tile_gap_spin.blockSignals(True)
            self.tile_gap_spin.setValue(0)
            self.tile_gap_spin.blockSignals(False)
            self.canvas.set_profile(None)
            return

        gap = int(profile.get("tile_gap", 0) or 0)

        self.tile_gap_spin.blockSignals(True)
        self.tile_gap_spin.setValue(gap)
        self.tile_gap_spin.blockSignals(False)

        self.canvas.set_profile(profile)

    def _apply_tile_gap_delta(self, profile: Dict[str, Any], old_gap: int, new_gap: int) -> None:
        """
        Adjust all tiles' geometry when the tile gap changes.

        For a positive delta (gap grows), each tile:
          - moves down/right by delta
          - shrinks width/height by 2*delta

        For a negative delta (gap shrinks), the inverse happens.
        """
        delta = new_gap - old_gap
        if delta == 0:
            return

        tiles = profile.get("tiles", [])
        for t in tiles:
            x = int(t.get("x", 0))
            y = int(t.get("y", 0))
            w = int(t.get("width", 800))
            h = int(t.get("height", 600))

            # Move tile
            new_x = x + delta
            new_y = y + delta

            # Shrink/expand tile
            new_w = w - 2 * delta
            new_h = h - 2 * delta

            # Avoid degenerate sizes
            if new_w < 1:
                new_w = 1
            if new_h < 1:
                new_h = 1

            t["x"] = int(new_x)
            t["y"] = int(new_y)
            t["width"] = int(new_w)
            t["height"] = int(new_h)

    # ----- Slots -----

    def on_profile_selected(self, current: QListWidgetItem, previous: QListWidgetItem | None) -> None:
        self.flush_tile_edits()

        if not current:
            self.current_profile_index = None
            self.current_tile_index = None
            self.tile_list.clear()
            self.tile_editor.clear()
            self.load_profile_settings_to_ui(None)
            return

        profile_index = int(current.data(Qt.ItemDataRole.UserRole))
        self.current_profile_index = profile_index
        self.current_tile_index = None

        self.populate_tiles(profile_index)
        profile = self.get_current_profile()
        self.load_profile_settings_to_ui(profile)
        self.tile_editor.clear()

        if self.tile_list.count() > 0:
            self.tile_list.setCurrentRow(0)

    def on_tile_selected(self, current: QListWidgetItem, previous: QListWidgetItem | None) -> None:
        self.flush_tile_edits()

        if not current:
            self.current_tile_index = None
            self.tile_editor.clear()
            self.canvas.set_selected_index(None)
            return

        tile_index = int(current.data(Qt.ItemDataRole.UserRole))
        self.current_tile_index = tile_index

        profile = self.get_current_profile()
        tile = self.get_current_tile()
        if profile is not None and tile is not None:
            self.tile_editor.load_tile(profile, tile)

        self.canvas.set_selected_index(tile_index)

    def on_rule_toggled(self, item: QListWidgetItem) -> None:
        """Called when a rule checkbox is toggled."""
        if self._rules_updating:
            return

        rule_id = item.data(Qt.ItemDataRole.UserRole)
        if not rule_id:
            return

        enabled = item.checkState() == Qt.CheckState.Checked

        try:
            onigiri.set_rule_enabled(rule_id, enabled)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to change rule state:\n{e}")
            # revert checkbox to previous state (roughly)
            self._rules_updating = True
            item.setCheckState(Qt.CheckState.Unchecked if enabled else Qt.CheckState.Checked)
            self._rules_updating = False

    def on_profile_settings_changed(self, *args) -> None:
        """User changed tile gap -> update profile + canvas."""
        profile = self.get_current_profile()
        if not profile:
            return

        gap = int(self.tile_gap_spin.value())

        # Previous value (for delta calculation)
        old_gap = int(profile.get("_last_tile_gap", profile.get("tile_gap", 0) or 0))

        # Update profile fields
        profile["tile_gap"] = gap

        # Adjust geometry for gap change
        if gap != old_gap:
            self._apply_tile_gap_delta(profile, old_gap, gap)
            profile["_last_tile_gap"] = gap

        # If we're editing a tile, refresh its fields in the editor
        if self.current_tile_index is not None:
            tile = self.get_current_tile()
            if tile is not None:
                self.tile_editor.load_tile(profile, tile)

        # Refresh canvas to show new geometry + gap
        self.canvas.set_profile(profile)

    def on_canvas_tile_selected(self, idx: int) -> None:
        """Canvas clicked a tile -> select in list."""
        if self.current_profile_index is None:
            return
        if 0 <= idx < self.tile_list.count():
            self.tile_list.setCurrentRow(idx)

    def on_canvas_geometry_changed(self, idx: int) -> None:
        """
        Canvas drag moved a tile -> sync editor + in-memory model.
        """
        profile = self.get_current_profile()
        tile = self.get_current_tile()
        if not profile or tile is None:
            return

        self.tile_editor.load_tile(profile, tile)
        if self.current_tile_index is not None:
            for i in range(self.tile_list.count()):
                item = self.tile_list.item(i)
                t_idx = int(item.data(Qt.ItemDataRole.UserRole))
                if t_idx == self.current_tile_index:
                    item.setText(tile.get("name", "<tile>"))
                    break

    def flush_tile_edits(self) -> None:
        profile = self.get_current_profile()
        tile = self.get_current_tile()
        if not profile or not tile:
            return

        self.tile_editor.current_profile = profile
        self.tile_editor.current_tile = tile
        self.tile_editor.apply_changes()

        # After edits, refresh canvas
        self.canvas._recompute_rects()
        self.canvas.update()

        if self.current_tile_index is not None:
            for i in range(self.tile_list.count()):
                item = self.tile_list.item(i)
                idx = int(item.data(Qt.ItemDataRole.UserRole))
                if idx == self.current_tile_index:
                    item.setText(tile.get("name", "<tile>"))
                    break

    def on_new_profile(self) -> None:
        name, ok = self.simple_prompt("New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        profile = {
            "name": name.strip(),
            "monitor": "default",
            "tiles": [],
            "tile_gap": 0,
        }
        profiles = self.get_profiles()
        profiles.append(profile)
        self.populate_profiles()
        new_index = len(profiles) - 1
        self.profile_list.setCurrentRow(new_index)

    def on_new_tile(self) -> None:
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        tile = {
            "name": "new-tile",
            "x": 0,
            "y": 0,
            "width": 800,
            "height": 600,
            "match": {"type": "none", "value": ""},
            "command": "",
            "no_border": False,
            "skip_taskbar": False,
            "launch_mode": "raw",
            "terminal_app": "alacritty",
            "shell_command": "",
        }
        tiles = profile.setdefault("tiles", [])
        tiles.append(tile)
        self.populate_tiles(self.current_profile_index)
        new_tile_index = len(tiles) - 1
        self.current_tile_index = new_tile_index
        self.tile_list.setCurrentRow(new_tile_index)
        self.canvas.set_profile(profile)

    def on_save_config(self) -> None:
        self.flush_tile_edits()
        try:
            onigiri.save_profiles(self.data)
            QMessageBox.information(self, "Saved", f"Config saved to:\n{onigiri.TILER_CONFIG}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save config:\n{e}")

    def on_apply_profile(self) -> None:
        self.flush_tile_edits()

        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile to apply.")
            return

        name = profile.get("name")
        try:
            onigiri.save_profiles(self.data)
            onigiri.remove_profile_rules(name)
            onigiri.apply_profile(name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply profile '{name}':\n{e}")
        else:
            self.populate_system_rules()
            QMessageBox.information(self, "Applied", f"Profile '{name}' applied (rules only).")

    def on_launch_apps(self) -> None:
        self.flush_tile_edits()

        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        name = profile.get("name")
        try:
            onigiri.save_profiles(self.data)
            onigiri.launch_profile_commands(name)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch apps for profile '{name}':\n{e}")
        else:
            QMessageBox.information(self, "Launched", f"Apps for profile '{name}' launched.")

    def on_delete_profile(self) -> None:
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile to delete.")
            return

        name = profile.get("name", "<unnamed>")
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{name}' and its KWin Window Rules?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            onigiri.remove_profile_rules(name)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Warning",
                f"Failed to remove KWin rules for '{name}':\n{e}\n"
                "The profile will still be removed from the config.",
            )

        profiles = self.get_profiles()
        if self.current_profile_index is not None and 0 <= self.current_profile_index < len(profiles):
            profiles.pop(self.current_profile_index)

        self.current_profile_index = None
        self.current_tile_index = None

        try:
            onigiri.save_profiles(self.data)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to save config after deleting profile:\n{e}",
            )

        self.populate_profiles()
        self.populate_system_rules()
        self.tile_list.clear()
        self.tile_editor.clear()
        self.load_profile_settings_to_ui(None)
        QMessageBox.information(self, "Deleted", f"Profile '{name}' deleted.")

    def on_delete_tile(self) -> None:
        profile = self.get_current_profile()
        tile = self.get_current_tile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return
        if not tile:
            QMessageBox.warning(self, "No tile", "Select a tile to delete.")
            return

        profile_name = profile.get("name", "<unnamed>")
        tile_name = tile.get("name", "<tile>")

        reply = QMessageBox.question(
            self,
            "Delete Tile",
            f"Delete tile '{tile_name}' from profile '{profile_name}' "
            "and update its KWin Window Rules?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        tiles = profile.setdefault("tiles", [])
        if self.current_tile_index is not None and 0 <= self.current_tile_index < len(tiles):
            tiles.pop(self.current_tile_index)

        self.current_tile_index = None
        self.tile_editor.clear()

        try:
            onigiri.save_profiles(self.data)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to save config after deleting tile:\n{e}",
            )
            return

        try:
            onigiri.remove_profile_rules(profile_name)
            onigiri.apply_profile(profile_name)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Warning",
                f"Tile removed from profile, but failed to update KWin rules:\n{e}",
            )

        self.populate_tiles(self.current_profile_index)
        self.populate_system_rules()
        self.canvas.set_profile(profile)
        QMessageBox.information(self, "Deleted", f"Tile '{tile_name}' deleted.")

    def on_create_autostart(self) -> None:
        """
        Create:
          - ~/.config/onigiri/onigiri_start.sh
          - ~/.config/autostart/onigiri_autostart.desktop
        so that dash-tiler starts on login, hidden to tray, and
        applies + launches the current profile.
        """
        try:
            python = sys.executable or "python3"
            script_path = os.path.abspath(sys.argv[0])

            # Startup helper script
            config_dir = Path.home() / ".config" / "onigiri"
            config_dir.mkdir(parents=True, exist_ok=True)
            script_file = config_dir / "onigiri_start.sh"

            script_content = f"""#!/bin/bash
"{python}" "{script_path}" --autostart
"""
            script_file.write_text(script_content, encoding="utf-8")
            os.chmod(script_file, 0o755)

            # KDE autostart .desktop entry
            autostart_dir = Path.home() / ".config" / "autostart"
            autostart_dir.mkdir(parents=True, exist_ok=True)
            desktop_file = autostart_dir / "onigiri_autostart.desktop"

            desktop_content = f"""[Desktop Entry]
Type=Application
Name=Onigiri Autostart
Exec={script_file}
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
"""
            desktop_file.write_text(desktop_content, encoding="utf-8")

            QMessageBox.information(
                self,
                "Autostart created",
                f"Startup script created at:\n{script_file}\n\n"
                f"Autostart entry created at:\n{desktop_file}\n\n"
                "Your current profile will be applied and apps launched on login "
                "with the app running in the system tray.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create autostart:\n{e}")

    def perform_autostart(self) -> None:
        """
        Called when launched with --autostart:
        - applies the current profile's rules
        - launches its apps
        - no message boxes, silent errors
        """
        profile = self.get_current_profile()
        if not profile:
            return
        name = profile.get("name")
        if not name:
            return
        try:
            onigiri.save_profiles(self.data)
            onigiri.remove_profile_rules(name)
            onigiri.apply_profile(name)
            onigiri.launch_profile_commands(name)
        except Exception as e:
            print(f"[onigiri autostart] Failed: {e}", file=sys.stderr)

    # ----- Small helpers -----

    def simple_prompt(self, title: str, label: str) -> tuple[str, bool]:
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, title, label)
        return text, ok

def main():
    app = QApplication(sys.argv)

    autostart = "--autostart" in sys.argv

    win = MainWindow()

    if autostart:
        # Use the first profile (if any) as the "current" one for autostart
        if win.profile_list.count() > 0:
            win.profile_list.setCurrentRow(0)
        win.perform_autostart()
        win.hide()
    else:
        win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
