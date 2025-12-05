#!/usr/bin/env python3
from __future__ import annotations
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, cast, Callable
import copy
import logging

logger = logging.getLogger(__name__)

def qt_connect(signal: Any, slot: Callable[..., None]) -> None:
    signal.connect(slot)

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
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
)

from PyQt6.QtCore import Qt, pyqtSignal, QStandardPaths
from PyQt6.QtGui import QIcon, QAction
from models import TileModel, ProfileModel, ConfigModel, ConfigValidator
from service import OnigiriService
from layout_canvas import LayoutCanvas

import onigiri  # engine module


# ===================== Tile Editor =====================


class TileEditor(QWidget):
    """
    Right-side editor for a single tile.
    Edits the in-memory model passed in, doesn’t touch disk directly.
    """
    geometryEdited = pyqtSignal()
    launchTileRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_profile: Optional[ProfileModel] = None
        self.current_tile: Optional[TileModel] = None
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
            qt_connect(spin.valueChanged, self._on_geometry_spin_changed)

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

        layout.addRow(QLabel("Launch mode"))
        layout.addRow("Mode:", self.mode_combo)
        layout.addRow("Terminal:", self.terminal_combo)
        layout.addRow("Shell command:", self.shell_command_edit)
        layout.addRow("Application:", self.app_combo)

        layout.addRow(QLabel("Final command"))
        layout.addRow(self.command_edit)

        layout.addRow(self.no_border_check)
        layout.addRow(self.skip_taskbar_check)

        # Launch-this-tile button
        self.btn_launch_tile = QPushButton("Launch this tile")
        layout.addRow(self.btn_launch_tile)

        self.setLayout(layout)

        # Connections
        qt_connect(self.mode_combo.currentIndexChanged, self._update_mode_enabled_state)
        qt_connect(self.terminal_combo.currentIndexChanged, self._recompute_command_from_helper)
        qt_connect(self.shell_command_edit.textChanged, self._recompute_command_from_helper)
        qt_connect(self.app_combo.currentIndexChanged, self._on_app_changed)
        qt_connect(self.name_edit.textChanged, self._on_name_changed)
        qt_connect(self.btn_launch_tile.clicked, self._on_launch_tile_clicked)

        # Load system applications
        self._load_applications()

        # Initialize state
        self._update_mode_enabled_state()

    # ----- internal helpers -----

    def _on_launch_tile_clicked(self) -> None:
        """
        User clicked 'Launch this tile' in the editor.
        We push the current editor values into the TileModel
        and then signal the MainWindow to actually launch it.
        """
        if not self.current_tile:
            return

        # Make sure the TileModel has the latest values
        self.apply_changes()

        # Tell MainWindow: "launch the currently selected tile"
        cast(Any, self.launchTileRequested).emit()

    def _on_geometry_spin_changed(self) -> None:
        if self._loading:
            return
        # this should notify “geometry changed”
        signal = cast(Any, self.geometryEdited)
        signal.emit()

    def _on_name_changed(self, _text: str) -> None:
        if self._loading:
            return
        # In helper mode we might want to adjust WM class, etc.
        if self.mode_combo.currentText() == "Terminal helper":
            self._recompute_command_from_helper()

    def _load_applications(self) -> None:
        """Populate app_combo from .desktop files."""
        self._apps.clear()
        self.app_combo.blockSignals(True)
        self.app_combo.clear()

        data_dirs = (
            QStandardPaths.standardLocations(QStandardPaths.StandardLocation.ApplicationsLocation)
            or ["/usr/share/applications"]
        )

        for base in data_dirs:
            base_path = Path(base)
            if not base_path.is_dir():
                continue
            for desktop_file in base_path.glob("*.desktop"):
                try:
                    with open(desktop_file, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                except OSError:
                    continue

                app_id = desktop_file.name
                name = None
                exec_cmd = None
                no_display = False

                for ln in lines:
                    ln = ln.strip()
                    if ln.startswith("Name=") and name is None:
                        name = ln.split("=", 1)[1].strip()
                    elif ln.startswith("Exec=") and exec_cmd is None:
                        exec_cmd = ln.split("=", 1)[1].strip()
                    elif ln.startswith("NoDisplay="):
                        v = ln.split("=", 1)[1].strip().lower()
                        no_display = (v == "true")

                if not name or not exec_cmd:
                    continue

                if no_display:
                    continue

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

        # Build terminal command with proper window title flags
        if terminal == "alacritty":
            if cmd:
                built = (
                    f"{terminal} --title '{tile_name}' "
                    f"-e bash -lc '{cmd}; exec $SHELL'"
                )
            else:
                built = f"{terminal} --title '{tile_name}'"

        elif terminal == "kitty":
            if cmd:
                built = (
                    f"{terminal} --title '{tile_name}' "
                    f"-e bash -lc '{cmd}; exec $SHELL'"
                )
            else:
                built = f"{terminal} --title '{tile_name}'"

        elif terminal == "konsole":
            # tabtitle usually appears in the window title
            if cmd:
                built = (
                    f"{terminal} --new-tab --hold -p tabtitle='{tile_name}' "
                    f"-e bash -lc '{cmd}; exec $SHELL'"
                )
            else:
                built = f"{terminal} --new-tab -p tabtitle='{tile_name}'"

        elif terminal == "xterm":
            if cmd:
                built = (
                    f"{terminal} -T '{tile_name}' "
                    f"-e bash -lc '{cmd}; exec $SHELL'"
                )
            else:
                built = f"{terminal} -T '{tile_name}'"

        else:
            # Fallback: unknown terminal, just run the command
            if cmd:
                built = f"{terminal} -e bash -lc '{cmd}; exec $SHELL'"
            else:
                built = terminal

        # In helper mode, we MATCH BY TITLE now (the real logic lives in apply_changes)
        self.match_type_combo.setCurrentText("title")
        self.match_value_edit.setText(tile_name)

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

    def _on_app_changed(self, _index: int) -> None:
        if self._loading:
            return
        if self.mode_combo.currentText() != "Application":
            return
        self._update_command_from_app()

    # ----- public API -----

    def load_tile(self, profile: ProfileModel, tile: TileModel) -> None:
        """Load tile data into the editor widgets."""
        self._loading = True
        self.current_profile = profile
        self.current_tile = tile

        self.name_edit.setText(tile.name)
        self.x_spin.setValue(tile.x)
        self.y_spin.setValue(tile.y)
        self.w_spin.setValue(tile.width)
        self.h_spin.setValue(tile.height)

        mtype = tile.match_type
        mvalue = tile.match_value

        idx = self.match_type_combo.findText(mtype)
        if idx == -1:
            idx = self.match_type_combo.findText("none")
        self.match_type_combo.setCurrentIndex(idx)
        self.match_value_edit.setText(str(mvalue))

        # Flags
        self.no_border_check.setChecked(tile.no_border)
        self.skip_taskbar_check.setChecked(tile.skip_taskbar)

        # Command + helper meta
        self.command_edit.setPlainText(tile.command)

        launch_mode = tile.launch_mode
        shell_cmd = tile.shell_command
        self.shell_command_edit.setText(shell_cmd)

        if launch_mode == "helper":
            m = self.mode_combo.findText("Terminal helper")
            if m == -1:
                m = 0
            self.mode_combo.setCurrentIndex(m)

            term = tile.terminal_app
            ti = self.terminal_combo.findText(term)
            if ti == -1:
                ti = 0
            self.terminal_combo.setCurrentIndex(ti)
        elif launch_mode == "app":
            m = self.mode_combo.findText("Application")
            if m == -1:
                m = 0
            self.mode_combo.setCurrentIndex(m)

            app_id = tile.app_id
            app_name = tile.app_name

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
        """Write editor values back into the tile model."""
        if not self.current_tile:
            return

        t = self.current_tile

        t.name = self.name_edit.text().strip()
        t.x = int(self.x_spin.value())
        t.y = int(self.y_spin.value())
        t.width = int(self.w_spin.value())
        t.height = int(self.h_spin.value())

        # Flags
        t.no_border = bool(self.no_border_check.isChecked())
        t.skip_taskbar = bool(self.skip_taskbar_check.isChecked())

        mode_text = self.mode_combo.currentText()

        if mode_text == "Terminal helper":
            t.launch_mode = "helper"
            term = self.terminal_combo.currentText().strip() or "alacritty"
            t.terminal_app = term
            t.shell_command = self.shell_command_edit.text().strip()

            # Generate the final command from helper
            self._recompute_command_from_helper()
            cmd_text = self.command_edit.toPlainText().strip()
            t.command = cmd_text

            # Application-specific fields not used in this mode
            t.app_id = ""
            t.app_name = ""

        elif mode_text == "Application":
            t.launch_mode = "app"
            idx = self.app_combo.currentIndex()
            data = self.app_combo.itemData(idx, role=Qt.ItemDataRole.UserRole) if idx >= 0 else None

            exec_cmd = ""
            if isinstance(data, dict):
                t.app_id = str(data.get("id") or "")
                t.app_name = str(data.get("name") or "")
                exec_cmd = (data.get("exec") or "").strip()
            else:
                t.app_id = ""
                t.app_name = ""

            # No shell command / terminal in this mode
            t.shell_command = ""
            t.terminal_app = ""

            if exec_cmd:
                t.command = exec_cmd
                self.command_edit.setPlainText(exec_cmd)
            else:
                t.command = ""
        else:
            # Raw command
            t.launch_mode = "raw"
            t.shell_command = ""
            raw_cmd = self.command_edit.toPlainText().strip()
            t.command = raw_cmd

        # --- Automatic match configuration based on mode ---
        tile_name = t.name or "Dash Tile"

        if mode_text == "Terminal helper":
            # Match helper terminals by WINDOW TITLE substring using the tile name.
            # This works across different terminals (kitty, konsole, alacritty, xterm...)
            t.set_match("title", tile_name)
            self.match_type_combo.setCurrentText("title")
            self.match_value_edit.setText(tile_name)

        elif mode_text == "Application":
            # Use the application name as a substring match on window title
            app_name = (t.app_name or "").strip()
            if app_name:
                t.set_match("title", app_name)
                self.match_type_combo.setCurrentText("title")
                self.match_value_edit.setText(app_name)
            else:
                t.clear_match()
                self.match_type_combo.setCurrentText("none")
                self.match_value_edit.clear()

        else:
            # Raw command or unsupported mode: no automatic rule
            t.clear_match()
            self.match_type_combo.setCurrentText("none")
            self.match_value_edit.clear()


# =================== Advanced Grid ===================


class GridTemplateDialog(QDialog):
    """
    Simple dialog to define an advanced grid:
    - Mode 1: columns -> rows per column
    - Mode 2: rows    -> columns per row
    Returns (mode, counts) where mode is 'columns' or 'rows'.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced grid layout")

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Columns → rows per column",
            "Rows → columns per row",
        ])

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 16)
        self.count_spin.setValue(2)

        self._entry_spins: List[QSpinBox] = []

        # Area for "entry" spinboxes
        self.entries_widget = QWidget()
        self.entries_layout = QFormLayout(self.entries_widget)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)

        cancel_btn = QPushButton("Cancel")
        buttons.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)

        qt_connect(buttons.accepted, self.accept)
        qt_connect(buttons.rejected, self.reject)

        # Main layout
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Layout mode:"))
        layout.addWidget(self.mode_combo)
        layout.addWidget(QLabel("Number of columns / rows:"))
        layout.addWidget(self.count_spin)
        layout.addWidget(QLabel("Entries:"))
        layout.addWidget(self.entries_widget)
        layout.addWidget(buttons)

        self.setLayout(layout)

        # Rebuild when count changes
        qt_connect(self.count_spin.valueChanged, self._rebuild_entries)
        # Initial entries
        self._rebuild_entries()

    def _rebuild_entries(self) -> None:
        # Clear old widgets
        while self.entries_layout.count():
            item = self.entries_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._entry_spins.clear()

        n = self.count_spin.value()
        for i in range(n):
            spin = QSpinBox()
            spin.setRange(1, 16)
            spin.setValue(1)
            self._entry_spins.append(spin)
            self.entries_layout.addRow(f"Entry {i + 1}:", spin)

    def get_template(self) -> tuple[str, List[int]]:
        """
        :return: (mode, counts)
                 mode: 'columns' or 'rows'
                 counts: list of positive ints
        """
        mode_index = self.mode_combo.currentIndex()
        mode = "columns" if mode_index == 0 else "rows"
        counts = [spin.value() for spin in self._entry_spins]
        return mode, counts


# ===================== Controllers =====================

class ProfileController:
    """
    Handles profile-related actions (select, new, rename, delete).
    Operates on the MainWindow, but keeps the logic in one place.
    """
    def __init__(self, window: "MainWindow") -> None:
        self.window = window

    def on_profile_selected(self, current: QListWidgetItem, _previous: Optional[QListWidgetItem]) -> None:
        w = self.window

        # flush pending tile edits via TileController
        w.tile_controller.flush_tile_edits()

        if not current:
            w.current_profile_index = None
            w.clear_tile_selection_and_editor()
            w.load_profile_settings_to_ui(None)

            # Keep combo in sync
            w.profile_combo.blockSignals(True)
            w.profile_combo.setCurrentIndex(-1)
            w.profile_combo.blockSignals(False)
            return

        profile_index = int(current.data(Qt.ItemDataRole.UserRole))
        w.current_profile_index = profile_index

        profile = w.get_current_profile()
        w.populate_tiles(profile_index)
        w.load_profile_settings_to_ui(profile)

        # Sync top-bar combo with the new index
        if 0 <= profile_index < w.profile_combo.count():
            w.profile_combo.blockSignals(True)
            w.profile_combo.setCurrentIndex(profile_index)
            w.profile_combo.blockSignals(False)

    def on_new_profile(self) -> None:
        w = self.window

        w.push_undo_state()

        name, ok = w.simple_prompt("New Profile", "Profile name:")
        if not ok or not name.strip():
            return

        w.config.add_profile(name.strip())
        w.populate_profiles()
        new_index = len(w.get_profiles()) - 1
        w.profile_list.setCurrentRow(new_index)

    def on_rename_profile(self) -> None:
        w = self.window

        profile = w.get_current_profile()
        if not profile:
            QMessageBox.warning(w, "No profile", "Select a profile to rename.")
            return

        old_name = profile.name or "<unnamed>"

        new_name, ok = w.simple_prompt("Rename Profile", "New profile name:", default=old_name)
        if not ok:
            return

        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(w, "Invalid name", "Profile name cannot be empty.")
            return

        # Check for duplicate names
        for p in w.get_profiles():
            if p is profile:
                continue
            if p.name == new_name:
                QMessageBox.warning(
                    w,
                    "Duplicate name",
                    f"Another profile is already called '{new_name}'.",
                )
                return

        w.push_undo_state()
        profile.name = new_name

        # Update the list item text
        current_item = w.profile_list.currentItem()
        if current_item is not None:
            current_item.setText(new_name)

        # Persist + refresh rule list UI
        w.engine.save_config(w.config)
        w.populate_system_rules()

    def on_delete_profile(self) -> None:
        w = self.window

        w.push_undo_state()

        profile = w.get_current_profile()
        if not profile:
            QMessageBox.warning(w, "No profile", "Select a profile to delete.")
            return

        name = profile.name or "<unnamed>"
        reply = QMessageBox.question(
            w,
            "Delete Profile",
            f"Delete profile '{name}' and its KWin Window Rules?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Try to remove profile rules first
        try:
            w.engine.remove_profile_rules(profile)
        except Exception as e:
            QMessageBox.warning(
                w,
                "Warning",
                f"Failed to remove KWin rules for '{name}':\n{e}\n"
                "The profile will still be removed from the config.",
            )

        if w.current_profile_index is not None:
            w.config.remove_profile(w.current_profile_index)

        w.current_profile_index = None
        w.current_tile_index = None

        if not w.save_config_with_error("save config after deleting profile"):
            return

        # Refresh lists and clear the now-invalid tile selection/editor
        w.reload_profiles_and_rules()
        w.clear_tile_selection_and_editor()
        w.load_profile_settings_to_ui(None)

        QMessageBox.information(w, "Deleted", f"Profile '{name}' deleted.")


class TileController:
    """
    Handles tile-related actions (select, new, delete, editor flush, canvas sync).
    Operates on the MainWindow, but keeps the logic in one place.
    """
    def __init__(self, window: "MainWindow") -> None:
        self.window = window

    def flush_tile_edits(self, item: Optional[QListWidgetItem] = None) -> None:
        """
        Push the editor's current values into the TileModel for the given item.
        If no item is passed, it uses the currently selected item.
        """
        w = self.window

        profile = w.get_current_profile()
        if not profile:
            return

        if item is None:
            item = w.tile_list.currentItem()
        if not item:
            return

        tile = w.get_tile_from_item(item)
        if not tile:
            return

        # Apply changes into that tile
        w.tile_editor.current_profile = profile
        w.tile_editor.current_tile = tile
        w.tile_editor.apply_changes()

        # Refresh canvas
        w.canvas.update()

        # Update list label
        item.setText(tile.name or "<tile>")

    def on_tile_selected(self, current: QListWidgetItem, previous: Optional[QListWidgetItem]) -> None:
        """
        Called when the tile selection in the list changes.
        Flushes edits into the previously selected tile, then loads the new one.
        """
        w = self.window

        # 1) Flush edits for the previously selected tile, if any
        if previous is not None:
            self.flush_tile_edits(previous)

        # 2) Clear UI if nothing is selected now
        if not current:
            w.current_tile_index = None
            w.tile_editor.clear()
            w.canvas.set_selected_index(None)
            return

        profile = w.get_current_profile()
        if not profile:
            return

        tile = w.get_tile_from_item(current)
        if not tile:
            return

        # Keep index in sync for other code that still uses it
        row = w.tile_list.row(current)
        w.current_tile_index = row

        # Load selected tile into editor and canvas
        w.tile_editor.load_tile(profile, tile)
        w.canvas.set_selected_index(row)

    def on_canvas_tile_selected(self, idx: int) -> None:
        """Canvas clicked a tile -> select same row in list."""
        w = self.window
        if 0 <= idx < w.tile_list.count():
            w.tile_list.setCurrentRow(idx)

    def on_new_tile(self) -> None:
        w = self.window

        profile = w.get_current_profile()
        if not profile:
            QMessageBox.warning(w, "No profile", "Select a profile first.")
            return

        w.push_undo_state()

        profile.add_tile()
        w.populate_tiles(w.current_profile_index)

        new_tile_index = len(profile.tiles) - 1
        w.current_tile_index = new_tile_index

        if new_tile_index >= 0:
            w.tile_list.setCurrentRow(new_tile_index)
            w.canvas.set_profile(profile)

    def on_delete_tile(self) -> None:
        w = self.window

        profile = w.get_current_profile()
        if not profile:
            return

        current_item = w.tile_list.currentItem()
        if not current_item:
            return

        tile = w.get_tile_from_item(current_item)
        if not tile:
            return

        tiles = profile.tiles
        try:
            idx = tiles.index(tile)
        except ValueError:
            # Fallback: row-based deletion
            idx = w.tile_list.currentRow()

        if not (0 <= idx < len(tiles)):
            return

        # 🔸 Take snapshot BEFORE actually deleting anything
        w.push_undo_state()

        reply = QMessageBox.question(
            w,
            "Delete Tile",
            f"Delete tile '{tile.name or '<tile>'}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Remove from profile by the *real* index of that TileModel
        profile.remove_tile(idx)

        # Update selection index
        if len(profile.tiles) == 0:
            w.current_tile_index = None
        else:
            if idx >= len(profile.tiles):
                idx = len(profile.tiles) - 1
            w.current_tile_index = idx

        # Refresh UI
        w.populate_tiles(w.current_profile_index)
        w.canvas.set_profile(profile)

        if w.current_tile_index is not None:
            w.tile_list.setCurrentRow(w.current_tile_index)
            new_tile = profile.tiles[w.current_tile_index]
            w.tile_editor.load_tile(profile, new_tile)
        else:
            w.tile_editor.clear()

        # Persist config (best-effort)
        try:
            w.engine.save_config(w.config)
        except (OSError, ValueError, RuntimeError) as e:
            logger.warning("Failed to save config after deleting tile: %s", e)


# ===================== Main Window =====================


class MainWindow(QWidget):

    # Attribute declarations for type checkers / linters
    engine: OnigiriService
    config: ConfigModel
    validator: ConfigValidator

    current_profile_index: Optional[int]
    current_tile_index: Optional[int]

    undo_stack: List[Dict[str, Any]]
    redo_stack: List[Dict[str, Any]]

    _rules_updating: bool
    _loading_profile_settings: bool



    def __init__(self):
        super().__init__()

        self.setWindowIcon(self.geticon())
        self.setWindowTitle("Onigiri")
        # Give the UI more space so the canvas can actually breathe
        self.resize(1700, 900)

        # Simple dark-grey theme for the whole app
        self.setStyleSheet("""
            QWidget {
                background-color: #222222;
                color: #f0f0f0;
            }

            QGroupBox {
                border: 1px solid #444444;
                border-radius: 6px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px 0 4px;
            }

            QPushButton {
                background-color: #333333;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #3f3f3f;
            }
            QPushButton:pressed {
                background-color: #292929;
            }

            QListWidget, QComboBox, QSpinBox, QLineEdit, QTextEdit {
                background-color: #2a2a2a;
                border: 1px solid #555555;
                border-radius: 4px;
            }
        """)

        self._init_engine_and_state()
        self._init_widgets_and_layout()
        self._init_controllers()
        self._init_signals()

    def _init_engine_and_state(self) -> None:
        # Engine service and config
        self.engine = OnigiriService()
        self.config = self.engine.load_config()

        # Validation helper
        self.validator = ConfigValidator()

        # track indices for tiles
        self.current_profile_index: Optional[int] = None
        self.current_tile_index: Optional[int] = None

        # Undo / Redo stacks (each entry is a deep-copied config dict)
        self.undo_stack: List[Dict[str, Any]] = []
        self.redo_stack: List[Dict[str, Any]] = []

        # internal flag to avoid reacting to programmatic checkbox changes
        self._rules_updating: bool = False

        # internal flag to avoid reacting while loading profile settings
        self._loading_profile_settings: bool = False

    def _init_widgets_and_layout(self) -> None:
        # === Widgets ===
        # main_layout = QHBoxLayout(self)

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

        # Button to delete the currently selected KWin rule
        self.btn_delete_rule = QPushButton("Delete Rule")

        # Buttons for profiles
        self.btn_new_profile = QPushButton("New Profile")
        self.btn_rename_profile = QPushButton("Rename")
        self.btn_delete_profile = QPushButton("Delete")

        # Buttons for tiles
        self.btn_new_tile = QPushButton("New Tile")
        self.btn_delete_tile = QPushButton("Delete")

        # Undo/redo buttons
        self.btn_undo = QPushButton("Undo")
        self.btn_redo = QPushButton("Redo")

        # Canvas & editor
        self.canvas = LayoutCanvas()

        # Tile editor
        self.tile_editor = TileEditor()

        # Profile-level settings (gap)
        self.tile_gap_spin = QSpinBox()
        self.tile_gap_spin.setRange(0, 200)
        self.tile_gap_spin.setValue(0)

        # Monitor selector for the profile
        self.monitor_combo = QComboBox()
        self.monitor_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)

        # Layout selector for the current monitor
        self.layout_combo = QComboBox()
        self.layout_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.layout_combo.setToolTip("Select a named layout for this profile and monitor.")

        # Top-bar profile selector (separate from the hidden list)
        self.profile_combo = QComboBox()
        self.profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.profile_combo.setToolTip("Select the active profile.")

        # Layout editor buttons
        self.btn_edit_layout = QPushButton("Edit Layout")
        self.btn_new_layout = QPushButton("New Layout")
        self.btn_rename_layout = QPushButton("Rename Layout")
        self.btn_save_layout = QPushButton("Save Layout")
        self.btn_load_layout = QPushButton("Load Layout")
        self.btn_delete_layout = QPushButton("Delete Layout")

        # Canvas background button
        self.btn_canvas_bg = QPushButton("Canvas Background…")
        self.btn_canvas_bg.setToolTip("Pick an image (e.g. a desktop screenshot) as canvas background")

        # Bottom action buttons
        self.btn_save = QPushButton("Save Config")
        self.btn_apply = QPushButton("Apply Profile (Rules)")
        self.btn_launch = QPushButton("Launch Apps")
        self.btn_autostart = QPushButton("Create Autostart")

        # Helpful tooltips for end users
        self.btn_apply.setToolTip(
            "Write KWin window rules for this profile (does not launch apps)."
        )
        self.btn_launch.setToolTip(
            "Apply rules and launch all apps configured for this profile."
        )
        self.btn_autostart.setToolTip(
            "Create an autostart entry that starts Onigiri with this profile."
        )

        # --- Top bar: logo + profile selector + dialogs + rules toggle ---
        top_bar_layout = QHBoxLayout()

        # Tiny logo / icon on the left
        logo_label = QLabel()
        logo_label.setPixmap(self.geticon().pixmap(24, 24))
        top_bar_layout.addWidget(logo_label)

        top_bar_layout.addSpacing(8)

        # Profile selector row
        top_bar_layout.addWidget(QLabel("Profile:"))
        top_bar_layout.addWidget(self.profile_combo, stretch=1)

        # Spacer and maybe future rule toggles
        top_bar_layout.addStretch(1)

        # --- Center grid: left (profiles), middle (tiles), right (rules) ---
        center_layout = QHBoxLayout()

        # Profiles group
        profiles_group = QGroupBox("Profiles")
        profiles_layout = QVBoxLayout(profiles_group)
        profiles_layout.addWidget(self.profile_list)

        profile_buttons_row = QHBoxLayout()
        profile_buttons_row.addWidget(self.btn_new_profile)
        profile_buttons_row.addWidget(self.btn_rename_profile)
        profile_buttons_row.addWidget(self.btn_delete_profile)
        profiles_layout.addLayout(profile_buttons_row)

        center_layout.addWidget(profiles_group)

        # Tiles group
        tiles_group = QGroupBox("Tiles")
        tiles_layout = QVBoxLayout(tiles_group)
        tiles_layout.addWidget(self.tile_list)

        tile_buttons_row = QHBoxLayout()
        tile_buttons_row.addWidget(self.btn_new_tile)
        tile_buttons_row.addWidget(self.btn_delete_tile)
        tiles_layout.addLayout(tile_buttons_row)

        center_layout.addWidget(tiles_group)

        # KWin rules group
        rules_group = QGroupBox("System KWin Rules")
        rules_layout = QVBoxLayout(rules_group)
        rules_layout.addWidget(self.rules_list)
        rules_layout.addWidget(self.btn_delete_rule)

        center_layout.addWidget(rules_group)

        # Make the center layout more compact and aligned
        center_layout.setStretchFactor(profiles_group, 1)
        center_layout.setStretchFactor(tiles_group, 1)
        center_layout.setStretchFactor(rules_group, 1)

        # --- Layout canvas + editor ---
        canvas_and_editor = QHBoxLayout()

        # Left: canvas
        canvas_card = QGroupBox("Layout Canvas")
        canvas_card_layout = QVBoxLayout(canvas_card)
        canvas_card_layout.addWidget(self.canvas)

        canvas_and_editor.addWidget(canvas_card, stretch=2)

        # Right: tile editor
        editor_card = QGroupBox("Tile Editor")
        editor_card_layout = QVBoxLayout(editor_card)
        editor_card_layout.addWidget(self.tile_editor)

        canvas_and_editor.addWidget(editor_card, stretch=1)

        # --- Profile settings row (gap + monitor + layout selector + layout buttons) ---
        profile_settings_layout = QHBoxLayout()

        profile_settings_layout.addWidget(QLabel("Tile gap:"))
        profile_settings_layout.addWidget(self.tile_gap_spin)

        profile_settings_layout.addSpacing(16)

        profile_settings_layout.addWidget(QLabel("Monitor:"))
        profile_settings_layout.addWidget(self.monitor_combo)

        profile_settings_layout.addSpacing(16)

        profile_settings_layout.addWidget(QLabel("Layout:"))
        profile_settings_layout.addWidget(self.layout_combo, stretch=1)

        profile_settings_layout.addSpacing(16)

        profile_settings_layout.addWidget(self.btn_edit_layout)
        profile_settings_layout.addWidget(self.btn_new_layout)
        profile_settings_layout.addWidget(self.btn_rename_layout)
        profile_settings_layout.addWidget(self.btn_save_layout)
        profile_settings_layout.addWidget(self.btn_load_layout)
        profile_settings_layout.addWidget(self.btn_delete_layout)

        profile_settings_layout.addStretch(1)

        # --- Bottom action row (undo/redo/save/apply/launch/autostart) ---
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.btn_undo)
        button_layout.addWidget(self.btn_redo)
        button_layout.addSpacing(20)
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_apply)
        button_layout.addWidget(self.btn_launch)
        button_layout.addWidget(self.btn_autostart)

        # --- Canvas block: background button + canvas ---
        canvas_block = QVBoxLayout()

        bg_button_row = QHBoxLayout()
        bg_button_row.addStretch(1)
        bg_button_row.addWidget(self.btn_canvas_bg)

        canvas_block.addLayout(bg_button_row)
        canvas_block.addLayout(canvas_and_editor, stretch=1)

        # --- Assemble main layout (new dashboard-style UI) ---
        main_layout = QVBoxLayout(self)
        main_layout.addLayout(top_bar_layout)
        main_layout.addLayout(center_layout)
        main_layout.addLayout(profile_settings_layout)
        main_layout.addLayout(canvas_block, stretch=1)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def _init_controllers(self) -> None:
        """
        Create controller objects that encapsulate profile and tile behaviors.
        """
        self.profile_controller = ProfileController(self)
        self.tile_controller = TileController(self)

    def _init_signals(self) -> None:
        # === Signals ===
        qt_connect(self.profile_list.currentItemChanged, self.profile_controller.on_profile_selected)
        qt_connect(self.tile_list.currentItemChanged, self.tile_controller.on_tile_selected)
        qt_connect(self.rules_list.itemChanged, self.on_rule_toggled)
        qt_connect(self.btn_delete_rule.clicked, self.on_delete_rule)
        qt_connect(self.btn_new_profile.clicked, self.profile_controller.on_new_profile)
        qt_connect(self.btn_rename_profile.clicked, self.profile_controller.on_rename_profile)
        qt_connect(self.btn_delete_profile.clicked, self.profile_controller.on_delete_profile)
        qt_connect(self.btn_new_tile.clicked, self.tile_controller.on_new_tile)
        qt_connect(self.btn_delete_tile.clicked, self.tile_controller.on_delete_tile)
        qt_connect(self.btn_undo.clicked, self.on_undo)
        qt_connect(self.btn_redo.clicked, self.on_redo)
        qt_connect(self.btn_save.clicked, self.on_save_config)
        qt_connect(self.btn_apply.clicked, self.on_apply_profile)
        qt_connect(self.btn_launch.clicked, self.on_launch_apps)
        qt_connect(self.btn_autostart.clicked, self.on_create_autostart)

        # Canvas background
        qt_connect(self.btn_canvas_bg.clicked, self.on_load_canvas_background)
        qt_connect(self.profile_combo.currentIndexChanged, self.on_profile_combo_changed)

        # Profile settings changes (gap + monitor + layout)
        self._init_monitor_list()
        qt_connect(self.monitor_combo.currentIndexChanged, self.on_monitor_changed)
        qt_connect(self.layout_combo.currentIndexChanged, self.on_layout_combo_changed)

        qt_connect(self.tile_gap_spin.valueChanged, self.on_profile_settings_changed)
        qt_connect(self.btn_edit_layout.clicked, self.on_edit_layout)
        qt_connect(self.btn_new_layout.clicked, self.on_new_layout)
        qt_connect(self.btn_rename_layout.clicked, self.on_rename_layout)
        qt_connect(self.btn_save_layout.clicked, self.on_save_layout)
        qt_connect(self.btn_load_layout.clicked, self.on_load_layout)
        qt_connect(self.btn_delete_layout.clicked, self.on_delete_layout)

        # Tile editor live geometry updates -> update model + canvas
        qt_connect(self.tile_editor.geometryEdited, self.tile_controller.flush_tile_edits)

        # Launch a single tile from the editor
        qt_connect(self.tile_editor.launchTileRequested, self.on_launch_single_tile)

        # Canvas → MainWindow: tile clicked and geometry changed
        qt_connect(self.canvas.tileSelected, self.tile_controller.on_canvas_tile_selected)
        qt_connect(self.canvas.geometryChanged, self.on_canvas_geometry_changed)

        # Populate initial lists
        self.populate_profiles()
        self.populate_system_rules()

        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(0)

        # ---- System tray icon ----
        self._create_tray_icon()

    # ----- tray -----
    def geticon(self):
        # Try to load icon from system icon theme
        icon = QIcon.fromTheme("onigiri")

        # Fallback for systems where theme lookup fails
        if icon.isNull():
            local_icon = os.path.join(os.path.dirname(__file__), "onigiri_icon.png")
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

        qt_connect(show_action.triggered, self._show_from_tray)
        qt_connect(quit_action.triggered, QApplication.instance().quit)

        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        qt_connect(self.tray_icon.activated, self._on_tray_activated)
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

    def _init_monitor_list(self) -> None:
        """
        Fill the monitor combo box with all available screens.
        Data value is either 'default' or QScreen.name().
        """
        self.monitor_combo.clear()
        self.monitor_combo.addItem("Primary (default)", "default")

        for screen in QApplication.screens():
            geo = screen.geometry()
            label = f"{screen.name()} ({geo.width()}x{geo.height()})"
            self.monitor_combo.addItem(label, screen.name())

    def on_monitor_changed(self, index: int) -> None:
        """
        Called when the user selects a different monitor in the combo box.
        Updates the profile and refreshes the canvas.
        """
        if self._loading_profile_settings:
            return

        profile = self.get_current_profile()
        if not profile:
            return

        data = self.monitor_combo.itemData(index)
        monitor_value = str(data) if data is not None else "default"

        profile.monitor = monitor_value

        # Refresh canvas so it uses the new monitor geometry
        self.canvas.set_profile(profile)

        # Layouts are per-monitor -> refresh list
        self.refresh_layout_combo()

    def get_profiles(self) -> List[ProfileModel]:
        return self.config.profiles

    def get_current_profile(self) -> Optional[ProfileModel]:
        profiles = self.get_profiles()
        if self.current_profile_index is None:
            return None
        if 0 <= self.current_profile_index < len(profiles):
            return profiles[self.current_profile_index]
        return None

    def get_current_tile(self) -> Optional[TileModel]:
        """
        Return the TileModel represented by the *currently selected* list item.
        """
        current_item = self.tile_list.currentItem()
        if not current_item:
            return None
        return self.get_tile_from_item(current_item)

    def get_tile_from_item(self, item: QListWidgetItem) -> Optional[TileModel]:
        """
        Safely resolve the TileModel that a QListWidgetItem represents.
        First tries the stored TileModel in UserRole+1, then falls back to row index.
        """
        if not item:
            return None

        # Preferred: directly stored TileModel
        tile_obj = item.data(Qt.ItemDataRole.UserRole + 1)
        if isinstance(tile_obj, TileModel):
            return tile_obj

        # Fallback: row index mapping
        row = self.tile_list.row(item)
        profile = self.get_current_profile()
        if not profile:
            return None

        tiles = profile.tiles
        if 0 <= row < len(tiles):
            return tiles[row]

        return None

    def validate_current_profile(self, for_action: str) -> Optional[ProfileModel]:
        """
        Validate the currently selected profile before doing an action.

        for_action: short label used in error messages, e.g. "apply profile" or "launch apps".
        Returns the profile if valid, or None if validation failed.
        """
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", f"Select a profile first to {for_action}.")
            return None

        errors = self.validator.validate_profile(profile)
        if errors:
            QMessageBox.warning(
                self,
                "Validation errors",
                "Cannot {action} because of the following problems:\n\n{errors}".format(
                    action=for_action,
                    errors="\n".join(f"- {e}" for e in errors),
                ),
            )
            return None

        return profile

    def populate_profiles(self) -> None:
        self.profile_list.clear()

        # Also keep the top-bar combo in sync
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()

        for idx, profile in enumerate(self.get_profiles()):
            display_name = profile.name or "<unnamed>"

            # Hidden list (logic driver)
            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.profile_list.addItem(item)

            # Top-bar combo (visual selector)
            self.profile_combo.addItem(display_name, idx)

        self.profile_combo.blockSignals(False)

    def populate_tiles(self, profile_index: Optional[int]) -> None:
        self.tile_list.clear()
        if profile_index is None:
            return
        profiles = self.get_profiles()
        if not (0 <= profile_index < len(profiles)):
            return

        profile = profiles[profile_index]

        for idx, tile in enumerate(profile.tiles):
            label = tile.name or "<tile>"
            item = QListWidgetItem(label)

            # Store BOTH the logical index and the TileModel itself
            item.setData(Qt.ItemDataRole.UserRole, idx)
            item.setData(Qt.ItemDataRole.UserRole + 1, tile)

            self.tile_list.addItem(item)

    def populate_system_rules(self) -> None:
        """Read kwinrulesrc and show all rules with checkboxes."""
        self._rules_updating = True
        self.rules_list.clear()
        try:
            rules = self.engine.list_rules()
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

    # ----- Internal UI refresh helpers -----

    def reload_profiles_and_rules(self) -> None:
        """
        Refresh the Profiles list and the KWin Rules list from the current config.
        Does NOT change any selection; callers are responsible for that.
        """
        self.populate_profiles()
        self.populate_system_rules()

    def clear_tile_selection_and_editor(self) -> None:
        """
        Clear the tile list and the tile editor, and reset the current tile index.
        Used when the current profile is deleted or when config is fully restored.
        """
        self.current_tile_index = None
        self.tile_list.clear()
        self.tile_editor.clear()

    def save_config_with_error(self, action_description: str) -> bool:
        """
        Try to save the current config using the engine.

        :param action_description: Short phrase describing what we were doing,
                                   used in the error dialog (e.g. 'save config after deleting profile').
        :return: True if save succeeded, False if there was an error.
        """
        try:
            self.engine.save_config(self.config)
            return True
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to {action_description}:\n{e}",
            )
            return False

    # ----- Undo / Redo helpers -----

    def _make_config_snapshot(self) -> Dict[str, Any]:
        """
        Take a deep copy of the current config as a plain dict.
        Used for undo/redo snapshots.
        """
        return copy.deepcopy(self.config.to_dict())

    def _restore_config_from_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """
        Replace current config with the snapshot and refresh the UI.
        """
        # Rebuild ConfigModel from snapshot
        self.config = ConfigModel(copy.deepcopy(snapshot))

        # Reset selection indices
        self.current_profile_index = None
        self.current_tile_index = None

        # Refresh UI from this config
        self.reload_profiles_and_rules()
        self.clear_tile_selection_and_editor()
        self.canvas.set_profile(None)

        # Optional: select first profile again if exists
        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(0)

    def push_undo_state(self) -> None:
        """
        Save the current config to the undo stack.
        Clears the redo stack (classic undo/redo behavior).
        Call this BEFORE making a change.
        """
        snapshot = self._make_config_snapshot()
        self.undo_stack.append(snapshot)
        self.redo_stack.clear()
        self._update_undo_redo_buttons()

    def _update_undo_redo_buttons(self) -> None:
        """
        Enable/disable undo/redo buttons based on stack state.
        """
        self.btn_undo.setEnabled(bool(self.undo_stack))
        self.btn_redo.setEnabled(bool(self.redo_stack))

    def load_profile_settings_to_ui(self, profile: Optional[ProfileModel]) -> None:
        """Load tile gap + monitor from profile into UI + canvas."""
        self._loading_profile_settings = True
        try:
            if not profile:
                # Reset gap
                self.tile_gap_spin.blockSignals(True)
                self.tile_gap_spin.setValue(0)
                self.tile_gap_spin.blockSignals(False)

                # Reset monitor combo to "Primary (default)"
                self.monitor_combo.setCurrentIndex(0)

                self.canvas.set_profile(None)
                # Also clear layout combo
                self.refresh_layout_combo()
                return

            # Gap from profile
            self.tile_gap_spin.blockSignals(True)
            self.tile_gap_spin.setValue(int(profile.tile_gap))
            self.tile_gap_spin.blockSignals(False)

            # Monitor selection from profile
            monitor_name = profile.monitor or "default"
            idx = 0
            for i in range(self.monitor_combo.count()):
                data = self.monitor_combo.itemData(i)
                if str(data) == monitor_name:
                    idx = i
                    break
            self.monitor_combo.setCurrentIndex(idx)

            # Canvas uses this profile (and its monitor) now
            self.canvas.set_profile(profile)
            # Update layout selector for this profile + monitor
            self.refresh_layout_combo()
        finally:
            self._loading_profile_settings = False

    def refresh_layout_combo(self) -> None:
        """
        Update the layout combo box for the current profile + monitor.
        """
        profile = self.get_current_profile()

        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()

        if not profile:
            self.layout_combo.setEnabled(False)
            self.btn_edit_layout.setEnabled(False)
            self.btn_new_layout.setEnabled(False)
            self.btn_save_layout.setEnabled(False)
            self.btn_load_layout.setEnabled(False)
            self.btn_delete_layout.setEnabled(False)
            self.layout_combo.blockSignals(False)
            return

        # Get layout names from the model (ProfileModel guarantees at least ["Default"])
        names = profile.layout_names

        # Find current layout name, fall back to first name
        current_name = profile.current_layout_name or names[0]

        current_index = 0
        for i, name in enumerate(names):
            self.layout_combo.addItem(name, userData=name)
            if name == current_name:
                current_index = i

        self.layout_combo.setCurrentIndex(current_index)
        self.layout_combo.setEnabled(True)
        self.btn_edit_layout.setEnabled(True)
        self.btn_new_layout.setEnabled(True)
        self.btn_save_layout.setEnabled(True)
        self.btn_load_layout.setEnabled(True)
        self.btn_delete_layout.setEnabled(True)
        self.layout_combo.blockSignals(False)

    def on_layout_combo_changed(self, index: int) -> None:
        """
        User picked a different layout name in the combo.
        We just switch the active layout; we do NOT auto-load geometry
        into the canvas – that still happens via Edit/Load.
        """
        if getattr(self, "_loading_profile_settings", False):
            return

        profile = self.get_current_profile()
        if not profile or index < 0:
            return

        data = self.layout_combo.itemData(index)
        name = str(data) if data is not None else self.layout_combo.currentText().strip()
        if not name:
            return

        # Let errors surface instead of swallowing all Exception
        profile.current_layout_name = name
        self.engine.save_config(self.config)

        # Buttons might change availability depending on whether this layout has slots
        self.refresh_layout_combo()

    def on_new_layout(self) -> None:
        """
        Create a new empty layout for the current profile + monitor.
        """
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        # Existing layout names
        existing = set(profile.layout_names)

        # Suggest a name like "Layout 1", "Layout 2", ...
        base = "Layout"
        counter = 1
        suggested = f"{base} {counter}"
        while suggested in existing:
            counter += 1
            suggested = f"{base} {counter}"

        name, ok = self.simple_prompt("New Layout", "Layout name:", default=suggested)
        if not ok:
            return

        name = name.strip()
        if not name:
            QMessageBox.warning(self, "Invalid name", "Layout name cannot be empty.")
            return

        if name in existing:
            QMessageBox.warning(
                self,
                "Duplicate name",
                f"A layout named '{name}' already exists for this monitor.",
            )
            return

        # Create empty layout in the model
        try:
            profile.create_empty_layout(name)
        except (ValueError, TypeError) as e:
            QMessageBox.warning(self, "Error", f"Failed to create layout: {e}")
            return

        # Persist + refresh UI
        try:
            self.engine.save_config(self.config)
        except OSError as e:
            QMessageBox.warning(self, "Save error", f"Failed to save layout: {e}")
            return

        self.refresh_layout_combo()
        QMessageBox.information(
            self,
            "New layout created",
            f"Layout '{name}' has been created.\n"
            "Use 'Edit Layout' to design it and 'Save Layout' to store its geometry.",
        )

    def on_rename_layout(self) -> None:
        """
        Rename the currently selected layout for the current profile + monitor.
        """
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        # Fetch current layout name
        current_name = profile.current_layout_name or ""
        if not current_name:
            QMessageBox.information(
                self,
                "No layout",
                "There is no active layout to rename.",
            )
            return

        # Existing layout names, to avoid duplicates
        existing_names = set(profile.layout_names)

        # Ask user for new name
        new_name, ok = self.simple_prompt(
            "Rename Layout",
            "New layout name:",
            default=current_name,
        )
        if not ok:
            return

        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid name", "Layout name cannot be empty.")
            return

        if new_name == current_name:
            return

        if new_name in existing_names:
            QMessageBox.warning(
                self,
                "Duplicate name",
                f"A layout named '{new_name}' already exists for this monitor.",
            )
            return

        # Apply rename on the model
        self.push_undo_state()

        try:
            renamed = profile.rename_layout(current_name, new_name)
        except (ValueError, TypeError) as e:
            QMessageBox.warning(self, "Error", f"Failed to rename layout: {e}")
            return

        if not renamed:
            QMessageBox.warning(
                self,
                "Rename failed",
                "Could not rename this layout in the profile model.",
            )
            return

        # Persist changes
        try:
            self.engine.save_config(self.config)
        except OSError as e:
            QMessageBox.warning(self, "Save error", f"Failed to save renamed layout:\n{e}")
            return

        self.refresh_layout_combo()

    def _apply_tile_gap_delta(self, profile: ProfileModel, old_gap: int, new_gap: int) -> None:
        """
        Legacy helper for gap changes.
        Geometry is now recalculated by LayoutCanvas._push_geometry_into_tiles(),
        so this function is intentionally empty.
        """
        return

    # ----- Slots -----

    def on_undo(self) -> None:
        """
        Undo the last change by restoring the previous snapshot.
        """
        if not self.undo_stack:
            return

        # Push current state to redo, restore last undo snapshot
        current_snapshot = self._make_config_snapshot()
        snapshot = self.undo_stack.pop()

        self.redo_stack.append(current_snapshot)
        self._restore_config_from_snapshot(snapshot)
        self._update_undo_redo_buttons()

    def on_redo(self) -> None:
        """
        Redo the last undone change by restoring from redo stack.
        """
        if not self.redo_stack:
            return

        current_snapshot = self._make_config_snapshot()
        snapshot = self.redo_stack.pop()

        self.undo_stack.append(current_snapshot)
        self._restore_config_from_snapshot(snapshot)
        self._update_undo_redo_buttons()

    def on_rule_toggled(self, item: QListWidgetItem) -> None:
        """Called when a rule checkbox is toggled."""
        if self._rules_updating:
            return

        rule_id = item.data(Qt.ItemDataRole.UserRole)
        if not rule_id:
            return

        enabled = item.checkState() == Qt.CheckState.Checked

        try:
            self.engine.set_rule_enabled(rule_id, enabled)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to change rule state:\n{e}")
            # revert checkbox to previous state (roughly)
            self._rules_updating = True
            item.setCheckState(Qt.CheckState.Unchecked if enabled else Qt.CheckState.Checked)
            self._rules_updating = False

    def on_delete_rule(self) -> None:
        """
        Delete the currently selected KWin rule from kwinrulesrc.
        """
        current = self.rules_list.currentItem()
        if current is None:
            QMessageBox.information(
                self,
                "No rule selected",
                "Select a KWin rule in the list first.",
            )
            return

        rule_id = current.data(Qt.ItemDataRole.UserRole)
        if not rule_id:
            QMessageBox.warning(
                self,
                "Cannot delete",
                "This list entry is not a real rule (no rule ID).",
            )
            return

        rule_label = current.text() or "<unnamed>"

        reply = QMessageBox.question(
            self,
            "Delete KWin Rule",
            f"Delete KWin rule:\n\n    {rule_label}\n\n"
            "This will permanently remove it from your kwinrulesrc file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.engine.delete_rule(str(rule_id))
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to delete KWin rule:\n{e}",
            )
            return

        # Refresh the list after deletion
        self.populate_system_rules()

    def on_profile_settings_changed(self, *_args) -> None:
        """User changed tile gap -> update profile + canvas."""
        profile = self.get_current_profile()
        if not profile:
            return

        gap = int(self.tile_gap_spin.value())

        # Previous value (for comparison / UI memory)
        old_gap = int(profile.last_tile_gap)

        # Update profile fields
        profile.tile_gap = gap

        if gap != old_gap:
            # Remember last used gap
            profile.last_tile_gap = gap

            # Rebuild the canvas with the current layout_slots and new gap
            self.canvas.set_profile(profile)

            # Apply the gap to all tile geometries
            self.canvas.apply_geometry_to_tiles()
        else:
            # No change, just ensure canvas shows current profile
            self.canvas.set_profile(profile)

        # If we're editing a tile, refresh its fields in the editor
        if self.current_tile_index is not None:
            tile = self.get_current_tile()
            if tile is not None:
                self.tile_editor.load_tile(profile, tile)

        # Finally, repaint the canvas
        self.canvas.update()

    # ----- Layout editor buttons -----

    def on_edit_layout(self) -> None:
        """
        Open the layout editor for the current profile.
        If no layout exists yet, the canvas will start with a single full-screen slot.
        """
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        self.canvas.set_profile(profile)

    def on_save_layout(self) -> None:
        """
        Save the current layout slots into the profile and push geometry
        into assigned tiles.
        """
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        # Export current leaf rectangles to profile layout slots
        slots = self.canvas.export_slots_for_profile()
        if not slots:
            QMessageBox.information(
                self,
                "No tiles",
                "There are no assigned tiles in this layout to save.",
            )
            profile.layout_slots = []
        else:
            profile.layout_slots = slots

        # Let the canvas apply the current gap setting to all tiles.
        self.canvas.apply_geometry_to_tiles()

        # Canvas already reflects the current layout; just refresh tile editor
        if self.current_tile_index is not None:
            tile = self.get_current_tile()
            if tile is not None:
                self.tile_editor.load_tile(profile, tile)

        # Persist to disk
        try:
            self.engine.save_config(self.config)
        except OSError as e:
            # File system / IO error
            QMessageBox.warning(self, "Save error", f"Failed to save layout:\n{e}")
            return

        QMessageBox.information(
            self,
            "Layout saved",
            "Current layout has been saved to this profile and monitor.",
        )

    def on_load_layout(self) -> None:
        """
        Reload the saved layout for the current profile into the canvas.
        """
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        if not profile.layout_slots:
            QMessageBox.information(self, "No layout", "This profile has no saved layout yet.")
            return

        self.canvas.set_profile(profile)

    def on_delete_layout(self) -> None:
        """
        Delete the saved layout for the current profile and monitor.
        Uses the *currently selected layout name*.
        """
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        # If there are no slots in the current layout, treat as "no layout"
        slots = list(profile.layout_slots)
        if not slots:
            QMessageBox.information(self, "No layout", "This profile has no saved layout yet.")
            return

        data = self.layout_combo.currentData()
        layout_name = str(data) if data is not None else self.layout_combo.currentText().strip()
        name_for_msg = layout_name or "<unnamed>"

        reply = QMessageBox.question(
            self,
            "Delete Layout",
            f"Delete layout '{name_for_msg}' for profile '{profile.name}' on this monitor?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            if layout_name:
                profile.delete_layout_by_name(layout_name)
            else:
                # Fallback: clear slots of current layout
                profile.layout_slots = []
            self.engine.save_config(self.config)
        except (OSError, ValueError, RuntimeError) as e:
            # Narrowed from 'Exception' to realistic error types
            QMessageBox.warning(self, "Save error", f"Failed to delete layout:\n{e}")
            return

        # Reset canvas + layout combo
        self.canvas.set_profile(profile)
        self.refresh_layout_combo()

    def on_load_canvas_background(self) -> None:
        """
        Let the user choose an image file and store it as the background
        for the current profile + monitor, then apply it to the canvas.
        """
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select background image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.xpm)",
        )
        if not file_path:
            return

        # Store per-monitor background in the profile
        bgs = dict(profile.monitor_backgrounds)
        monitor_name = profile.monitor or "default"
        bgs[monitor_name] = file_path
        profile.monitor_backgrounds = bgs

        # Apply to canvas immediately
        self.canvas.set_background_image(file_path)

        # Persist config so the background is remembered
        try:
            self.engine.save_config(self.config)
        except Exception as e:
            QMessageBox.warning(self, "Save error", f"Failed to save background: {e}")

    def on_canvas_geometry_changed(self, tile_index: int) -> None:
        """
        Called when the LayoutCanvas updates the geometry of a tile
        (during border dragging).

        We just sync the tile editor if that tile is selected.
        """
        profile = self.get_current_profile()
        if not profile:
            return

        tiles = profile.tiles
        if not (0 <= tile_index < len(tiles)):
            return

        # If this tile is currently selected in the list, refresh editor fields
        if self.current_tile_index == tile_index:
            tile = tiles[tile_index]
            self.tile_editor.load_tile(profile, tile)

        # Canvas already has the updated geometry; just repaint
        self.canvas.update()

    def on_profile_combo_changed(self, index: int) -> None:
        """
        When the user picks a profile in the top-bar combo, drive the hidden
        profile_list selection (which already updates everything else).
        """
        if index < 0 or index >= self.profile_list.count():
            return
        self.profile_list.setCurrentRow(index)

    def on_save_config(self) -> None:
        """
        Save the current configuration to disk.
        If there is a current profile, validate it first.
        (You could also validate all profiles here later.)
        """
        self.tile_controller.flush_tile_edits()

        profile = self.get_current_profile()
        if profile is not None:
            errors = self.validator.validate_profile(profile)
            if errors:
                reply = QMessageBox.question(
                    self,
                    "Validation warnings",
                    "There are validation issues in the current profile:\n\n"
                    + "\n".join(f"- {e}" for e in errors)
                    + "\n\nSave anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        try:
            self.engine.save_config(self.config)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save config:\n{e}")
        else:
            QMessageBox.information(self, "Saved", f"Config saved to:\n{onigiri.TILER_CONFIG}")

    def on_apply_profile(self) -> None:
        """
        Validate current profile, then save config and (re)apply KWin rules.
        Also refreshes the KWin rules list in the UI.
        """
        self.tile_controller.flush_tile_edits()

        profile = self.validate_current_profile("apply this profile")
        if not profile:
            return  # validation failed or no profile selected

        try:
            self.engine.apply_profile_rules(self.config, profile)
            # Refresh the KWin rules list so the UI reflects the new rules
            self.populate_system_rules()
            QMessageBox.information(
                self,
                "Applied",
                f"KWin rules for '{profile.name}' refreshed.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply profile:\n{e}")

    def on_launch_apps(self) -> None:
        """
        Validate current profile, then launch only that profile's commands.
        """
        self.tile_controller.flush_tile_edits()

        profile = self.validate_current_profile("launch its apps")
        if not profile:
            return  # validation failed or no profile selected

        try:
            self.engine.launch_profile_apps(self.config, profile)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to launch apps for profile '{profile.name}':\n{e}",
            )
        else:
            QMessageBox.information(
                self,
                "Launched",
                f"Apps for profile '{profile.name}' launched.",
            )

    def on_launch_single_tile(self) -> None:
        """
        Called when TileEditor requests to launch the currently selected tile.
        - Refreshes KWin rules for the current profile
        - Then launches only this tile's command
        """
        profile = self.get_current_profile()
        tile = self.get_current_tile()

        if not profile or not tile:
            QMessageBox.warning(self, "No tile", "Select a tile first.")
            return

        # 1) Make sure KWin rules match the current profile config
        try:
            # This saves the config + rebuilds rules for this profile
            self.engine.apply_profile_rules(self.config, profile)
        except Exception as e:
            # Not fatal, but explain why geometry might be wrong
            QMessageBox.warning(
                self,
                "Warning",
                f"Failed to refresh KWin rules before launch:\n{e}\n"
                f"The tile may not get the correct size/position.",
            )

        # 2) Now launch just this tile's command
        try:
            self.engine.launch_tile_command(tile)
        except ValueError as e:
            # Typically: tile has no command
            QMessageBox.warning(
                self,
                "Cannot launch tile",
                str(e),
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to launch tile '{tile.name or '<tile>'}':\n{e}",
            )

    def autostart_profile(self, profile_name: str) -> None:
        """
        Called on startup when launched with --autostart-profile <name>.

        - Finds that profile in the loaded config
        - Selects it in the UI (internally)
        - Applies its KWin rules
        - Launches all commands/apps for that profile

        No dialogs are shown; errors go to stderr so they don't block login.
        """
        profiles = self.config.profiles

        # 1) Guard: no profiles at all
        if not profiles:
            logger.error("[Onigiri Autostart] No profiles available in config.")
            return

        target_idx: Optional[int] = None
        target_profile = None

        # 2) Search for the matching profile
        for i, p in enumerate(profiles):
            if p.name == profile_name:
                target_idx = i
                target_profile = p
                break

        # 3) If nothing was found, log once and bail
        if target_profile is None or target_idx is None:
            logger.error(
                "[Onigiri Autostart] Profile '%s' not found in config.",
                profile_name,
            )
            return

        # 4) Keep internal UI state consistent
        self.current_profile_index = target_idx
        if 0 <= target_idx < self.profile_list.count():
            self.profile_list.setCurrentRow(target_idx)

        # 5) Apply KWin rules for that profile
        try:
            self.engine.apply_profile_rules(self.config, target_profile)
        except Exception as e:
            logger.error(
                "[Onigiri Autostart] Failed to apply profile '%s': %s",
                profile_name,
                e,
            )

        # 6) Launch the apps/commands for that profile
        try:
            self.engine.launch_profile_apps(self.config, target_profile)
        except Exception as e:
            logger.error(
                "[Onigiri Autostart] Failed to launch apps for '%s': %s",
                profile_name,
                e,
            )

    # ----- simple utils -----

    def simple_prompt(self, title: str, label: str, default: str = "") -> tuple[str, bool]:
        from PyQt6.QtWidgets import QInputDialog
        from PyQt6.QtWidgets import QLineEdit

        text, ok = QInputDialog.getText(
            self,
            title,
            label,
            QLineEdit.EchoMode.Normal,
            default,
        )
        return text, ok

    # ----- Autostart -----

    def perform_autostart(self, profile_name: str) -> None:
        """
        Create a .desktop file in the user's autostart dir that:
          - starts Onigiri
          - applies the given profile
          - launches apps for that profile
        """

        # Save current config so the autostart run sees the latest data
        try:
            self.engine.save_config(self.config)
        except (OSError, ValueError, RuntimeError) as e:
            logger.warning("Failed to save config before autostart: %s", e)

        config_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.ConfigLocation
        )
        autostart_dir = os.path.join(config_dir, "autostart")
        os.makedirs(autostart_dir, exist_ok=True)

        desktop_path = os.path.join(autostart_dir, "onigiri.desktop")

        exe = sys.executable
        script_path = os.path.abspath(__file__)

        contents = f"""[Desktop Entry]
    Type=Application
    Exec={exe} "{script_path}" --autostart-profile "{profile_name}"
    Hidden=false
    NoDisplay=false
    X-GNOME-Autostart-enabled=true
    Name=Onigiri
    Comment=Start Onigiri tiler and apply profile '{profile_name}'
    """

        try:
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write(contents)
            QMessageBox.information(
                self,
                "Autostart created",
                f"Autostart file created at:\n{desktop_path}",
            )
        except OSError as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to create autostart file:\n{e}",
            )

    def on_create_autostart(self) -> None:
        self.tile_controller.flush_tile_edits()
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return
        name = profile.name
        if not name:
            QMessageBox.warning(self, "No name", "Profile needs a name first.")
            return
        self.perform_autostart(name)


# ===================== Main =====================


def main() -> int:
    # Simple default logging setup; you can tune level/format later
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)

    # Look for --autostart-profile <name> in argv
    autostart_profile_name = None
    if "--autostart-profile" in sys.argv:
        try:
            idx = sys.argv.index("--autostart-profile")
            if idx + 1 < len(sys.argv):
                autostart_profile_name = sys.argv[idx + 1]
        except ValueError:
            autostart_profile_name = None

    win = MainWindow()

    # If started via autostart .desktop, apply+launch the profile
    if autostart_profile_name:
        win.autostart_profile(autostart_profile_name)

    # Start minimized to tray
    win.hide()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
