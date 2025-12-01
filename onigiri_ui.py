#!/usr/bin/env python3
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess
import copy
import logging

logger = logging.getLogger(__name__)

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
    QInputDialog,
    QFileDialog,
    QGroupBox,
)

from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QStandardPaths
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QMouseEvent, QIcon, QAction, QPixmap

import onigiri  # engine module


# ===================== Data Model (OOP wrapper over JSON) =====================


class TileModel:
    """
    OOP wrapper around a tile dict.
    All access to tile data from the UI should go through this class.
    """

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self._data: Dict[str, Any] = data or {}

    # --- generic ---

    def to_dict(self) -> Dict[str, Any]:
        return self._data

    # --- basic props ---

    @property
    def name(self) -> str:
        return str(self._data.get("name", ""))

    @name.setter
    def name(self, value: str) -> None:
        self._data["name"] = value

    @property
    def x(self) -> int:
        return int(self._data.get("x", 0))

    @x.setter
    def x(self, value: int) -> None:
        self._data["x"] = int(value)

    @property
    def y(self) -> int:
        return int(self._data.get("y", 0))

    @y.setter
    def y(self, value: int) -> None:
        self._data["y"] = int(value)

    @property
    def width(self) -> int:
        return int(self._data.get("width", 800))

    @width.setter
    def width(self, value: int) -> None:
        self._data["width"] = int(value)

    @property
    def height(self) -> int:
        return int(self._data.get("height", 600))

    @height.setter
    def height(self, value: int) -> None:
        self._data["height"] = int(value)

    def set_geometry(self, x: int, y: int, w: int, h: int) -> None:
        self.x = x
        self.y = y
        self.width = w
        self.height = h

    # --- matching ---

    @property
    def match_type(self) -> str:
        match = self._data.get("match", {}) or {}
        return str(match.get("type", "none"))

    @match_type.setter
    def match_type(self, value: str) -> None:
        m = self._data.get("match", {}) or {}
        m["type"] = value
        self._data["match"] = m

    @property
    def match_value(self) -> str:
        match = self._data.get("match", {}) or {}
        return str(match.get("value", ""))

    @match_value.setter
    def match_value(self, value: str) -> None:
        m = self._data.get("match", {}) or {}
        m["value"] = value
        self._data["match"] = m

    def clear_match(self) -> None:
        self._data.pop("match", None)

    def set_match(self, mtype: str, value: str) -> None:
        self._data["match"] = {"type": mtype, "value": value}

    # --- flags ---

    @property
    def no_border(self) -> bool:
        return bool(self._data.get("no_border", False))

    @no_border.setter
    def no_border(self, value: bool) -> None:
        self._data["no_border"] = bool(value)

    @property
    def skip_taskbar(self) -> bool:
        return bool(self._data.get("skip_taskbar", False))

    @skip_taskbar.setter
    def skip_taskbar(self, value: bool) -> None:
        self._data["skip_taskbar"] = bool(value)

    # --- launching / commands ---

    @property
    def launch_mode(self) -> str:
        return str(self._data.get("launch_mode", "raw"))

    @launch_mode.setter
    def launch_mode(self, value: str) -> None:
        self._data["launch_mode"] = value

    @property
    def command(self) -> str:
        return str(self._data.get("command", ""))

    @command.setter
    def command(self, value: str) -> None:
        if value:
            self._data["command"] = value
        else:
            self._data.pop("command", None)

    @property
    def shell_command(self) -> str:
        return str(self._data.get("shell_command", ""))

    @shell_command.setter
    def shell_command(self, value: str) -> None:
        self._data["shell_command"] = value

    @property
    def terminal_app(self) -> str:
        return str(self._data.get("terminal_app", "alacritty"))

    @terminal_app.setter
    def terminal_app(self, value: str) -> None:
        self._data["terminal_app"] = value

    @property
    def app_id(self) -> str:
        return str(self._data.get("app_id", ""))

    @app_id.setter
    def app_id(self, value: str) -> None:
        if value:
            self._data["app_id"] = value
        else:
            self._data.pop("app_id", None)

    @property
    def app_name(self) -> str:
        return str(self._data.get("app_name", ""))

    @app_name.setter
    def app_name(self, value: str) -> None:
        if value:
            self._data["app_name"] = value
        else:
            self._data.pop("app_name", None)


class ProfileModel:
    """
    OOP wrapper around a profile dict.
    Holds TileModel objects for the profile's tiles.
    """

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self._data: Dict[str, Any] = data or {}
        tiles_raw = self._data.setdefault("tiles", [])
        self._tiles: List[TileModel] = [TileModel(t) for t in tiles_raw]

    # --- generic ---

    def to_dict(self) -> Dict[str, Any]:
        # sync tiles back into underlying dict
        self._data["tiles"] = [t.to_dict() for t in self._tiles]
        return self._data

    # --- basic props ---

    @property
    def name(self) -> str:
        return str(self._data.get("name", ""))

    @name.setter
    def name(self, value: str) -> None:
        self._data["name"] = value

    @property
    def monitor(self) -> str:
        return str(self._data.get("monitor", "default"))

    @monitor.setter
    def monitor(self, value: str) -> None:
        self._data["monitor"] = value

    @property
    def monitor_backgrounds(self) -> Dict[str, str]:
        """
        Mapping: monitor_name -> background image path.
        Example:
            {
                "default": "/path/to/bg1.png",
                "HDMI-0": "/path/to/bg2.png",
            }
        """
        return self._data.setdefault("monitor_backgrounds", {})

    @monitor_backgrounds.setter
    def monitor_backgrounds(self, value: Dict[str, str]) -> None:
        self._data["monitor_backgrounds"] = value

    @property
    def tile_gap(self) -> int:
        return int(self._data.get("tile_gap", 0) or 0)

    @tile_gap.setter
    def tile_gap(self, value: int) -> None:
        self._data["tile_gap"] = int(value)

    @property
    def last_tile_gap(self) -> int:
        return int(self._data.get("_last_tile_gap", self.tile_gap))

    @last_tile_gap.setter
    def last_tile_gap(self, value: int) -> None:
        self._data["_last_tile_gap"] = int(value)

    # --- layout management (per-monitor, multi-layout) ---

    def _get_layout_info_for_current_monitor(self, create: bool = True) -> Dict[str, Any]:
        """
        Internal helper that ensures layout_slots is stored in the new
        multi-layout structure:

            layout_slots = {
                "default": {
                    "current": "Default",
                    "layouts": {
                        "Default": [...],
                        "Tall":    [...],
                    },
                },
                "HDMI-0": { ... },
            }
        """
        monitor = self.monitor or "default"
        raw = self._data.get("layout_slots")

        # Case 1: old global list -> migrate to default monitor / Default layout
        if isinstance(raw, list):
            raw = {
                "default": {
                    "current": "Default",
                    "layouts": {"Default": raw},
                }
            }
            self._data["layout_slots"] = raw

        # Case 2: dict but old format (monitor -> list)
        elif isinstance(raw, dict):
            if raw and all(isinstance(v, list) for v in raw.values()):
                new_raw: Dict[str, Any] = {}
                for mon, slots in raw.items():
                    new_raw[mon] = {
                        "current": "Default",
                        "layouts": {"Default": slots},
                    }
                raw = new_raw
                self._data["layout_slots"] = raw
            elif raw and all(isinstance(v, dict) and "layouts" in v for v in raw.values()):
                # already in new format
                pass
            else:
                # unknown/invalid structure -> reset
                raw = {}
                self._data["layout_slots"] = raw

        # Case 3: no layouts yet
        else:
            raw = {}
            self._data["layout_slots"] = raw

        # Ensure entry for current monitor
        if monitor not in raw:
            if create:
                raw[monitor] = {"current": "Default", "layouts": {}}
            else:
                return {"current": "Default", "layouts": {}}

        info = raw[monitor]
        layouts = info.get("layouts")
        if not isinstance(layouts, dict):
            layouts = {}
            info["layouts"] = layouts
        if "current" not in info:
            info["current"] = "Default"

        return info

    @property
    def layout_slots(self) -> List[Dict[str, Any]]:
        """
        Return the slot list for the *currently selected layout*
        on the *current monitor*.
        """
        info = self._get_layout_info_for_current_monitor(create=True)
        layouts: Dict[str, List[Dict[str, Any]]] = info["layouts"]
        current = info.get("current") or "Default"

        # Ensure at least one layout exists
        if not layouts:
            layouts["Default"] = []
            info["current"] = "Default"
            return layouts["Default"]

        # If current layout name is invalid, fall back to first
        if current not in layouts:
            current = sorted(layouts.keys())[0]
            info["current"] = current

        return layouts[current]

    @layout_slots.setter
    def layout_slots(self, value: List[Dict[str, Any]]) -> None:
        """
        Store slot data into the *currently selected layout*
        on the *current monitor*.
        """
        info = self._get_layout_info_for_current_monitor(create=True)
        layouts: Dict[str, List[Dict[str, Any]]] = info["layouts"]
        current = info.get("current") or "Default"
        if not current:
            current = "Default"
        info["current"] = current
        layouts[current] = value or []

    @property
    def layout_names(self) -> List[str]:
        """
        List of layout names for the current monitor.
        Always returns at least ["Default"].
        """
        info = self._get_layout_info_for_current_monitor(create=True)
        layouts: Dict[str, List[Dict[str, Any]]] = info["layouts"]
        if not layouts:
            layouts["Default"] = []
            info["current"] = "Default"
        names = sorted(layouts.keys())
        return names

    @property
    def current_layout_name(self) -> str:
        """
        Name of the currently active layout for this monitor.
        """
        info = self._get_layout_info_for_current_monitor(create=True)
        layouts: Dict[str, List[Dict[str, Any]]] = info["layouts"]
        current = info.get("current") or "Default"
        if not layouts:
            layouts["Default"] = []
            current = "Default"
            info["current"] = current
        elif current not in layouts:
            current = sorted(layouts.keys())[0]
            info["current"] = current
        return current

    @current_layout_name.setter
    def current_layout_name(self, name: str) -> None:
        info = self._get_layout_info_for_current_monitor(create=True)
        layouts: Dict[str, List[Dict[str, Any]]] = info["layouts"]
        if not layouts:
            layouts[name] = []
        info["current"] = name

    def create_empty_layout(self, name: str) -> None:
        """
        Create an empty layout with the given name for the current monitor
        and make it current.
        """
        info = self._get_layout_info_for_current_monitor(create=True)
        layouts: Dict[str, List[Dict[str, Any]]] = info["layouts"]
        if name in layouts:
            raise ValueError(f"Layout '{name}' already exists.")
        layouts[name] = []
        info["current"] = name

    def delete_layout_by_name(self, name: str) -> None:
        """
        Delete a named layout for the current monitor. If it was current,
        switch to another existing layout or create an empty Default.
        """
        info = self._get_layout_info_for_current_monitor(create=True)
        layouts: Dict[str, List[Dict[str, Any]]] = info["layouts"]
        if name in layouts:
            del layouts[name]

        if not layouts:
            layouts["Default"] = []
            info["current"] = "Default"
        elif info.get("current") == name:
            info["current"] = sorted(layouts.keys())[0]

    def rename_layout(self, old_name: str, new_name: str) -> bool:
        """
        Rename a layout for the current monitor. Returns True on success.
        """
        info = self._get_layout_info_for_current_monitor(create=True)
        layouts: Dict[str, List[Dict[str, Any]]] = info["layouts"]

        old_name = str(old_name)
        new_name = str(new_name)

        if old_name not in layouts or not new_name or new_name in layouts:
            return False

        layouts[new_name] = layouts.pop(old_name)
        if info.get("current") == old_name:
            info["current"] = new_name
        return True

    # --- tiles ---

    @property
    def tiles(self) -> List[TileModel]:
        return self._tiles

    def add_tile(self) -> TileModel:
        tile_data = {
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
        tile = TileModel(tile_data)
        self._tiles.append(tile)
        self._data.setdefault("tiles", []).append(tile_data)
        return tile

    def remove_tile(self, index: int) -> None:
        if 0 <= index < len(self._tiles):
            self._tiles.pop(index)
            tiles_raw = self._data.setdefault("tiles", [])
            if 0 <= index < len(tiles_raw):
                tiles_raw.pop(index)


class ConfigModel:
    """
    Root configuration model holding all profiles.
    Wraps the dict structure from onigiri.load_profiles().
    """

    def __init__(self, raw: Optional[Dict[str, Any]] = None):
        self._data: Dict[str, Any] = raw or {}
        profiles_raw = self._data.setdefault("profiles", [])
        self._profiles: List[ProfileModel] = [ProfileModel(p) for p in profiles_raw]

    @property
    def profiles(self) -> List[ProfileModel]:
        return self._profiles

    def add_profile(self, name: str) -> ProfileModel:
        profile_data: Dict[str, Any] = {
            "name": name,
            "monitor": "default",
            "tiles": [],
            "tile_gap": 0,
        }
        profile = ProfileModel(profile_data)
        self._profiles.append(profile)
        self._data.setdefault("profiles", []).append(profile_data)
        return profile

    def remove_profile(self, index: int) -> None:
        if 0 <= index < len(self._profiles):
            self._profiles.pop(index)
            profiles_raw = self._data.setdefault("profiles", [])
            if 0 <= index < len(profiles_raw):
                profiles_raw.pop(index)

    def to_dict(self) -> Dict[str, Any]:
        self._data["profiles"] = [p.to_dict() for p in self._profiles]
        return self._data


class OnigiriService:
    """
    Thin wrapper around the onigiri engine module so the UI
    doesn't call onigiri.* all over the place.
    """

    def load_config(self) -> ConfigModel:
        """Load config from JSON and wrap it in ConfigModel."""
        raw = onigiri.load_profiles()
        return ConfigModel(raw)

    def save_config(self, config: ConfigModel) -> None:
        """Persist ConfigModel back to JSON."""
        onigiri.save_profiles(config.to_dict())

    # ----- profile / rules -----

    def apply_profile_rules(self, config: ConfigModel, profile: ProfileModel) -> None:
        """
        Save config, clear old rules for this profile and re-apply KWin rules.
        """
        name = profile.name
        if not name:
            raise ValueError("Profile needs a name before applying rules.")

        self.save_config(config)
        onigiri.remove_profile_rules(name)
        onigiri.apply_profile(name)

    def launch_profile_apps(self, config: ConfigModel, profile: ProfileModel) -> None:
        """
        Save config, re-apply KWin rules for this profile and launch all commands.

        This makes sure that any recent geometry changes (including tile gaps,
        layout edits, etc.) are actually reflected in the KWin rules *before*
        we start the apps.
        """
        name = profile.name
        if not name:
            raise ValueError("Profile needs a name before launching apps.")

        # 1) Persist current config (tiles, gap, monitor, etc.)
        self.save_config(config)

        # 2) Re-apply rules for this profile so KWin uses the latest geometry
        try:
            onigiri.apply_profile(name)
        except Exception as e:
            logger.error(
                "Failed to apply rules for profile '%s' before launching apps: %s",
                name,
                e,
            )

        # 3) Launch the configured commands/apps
        onigiri.launch_profile_commands(name)

    def remove_profile_rules(self, profile: ProfileModel) -> None:
        """
        Remove KWin rules for a profile (used when deleting a profile).
        """
        name = profile.name
        if not name:
            return
        onigiri.remove_profile_rules(name)

    # ----- KWin rules list / toggle -----

    def list_rules(self) -> List[Dict[str, Any]]:
        return onigiri.list_kwin_rules()

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        onigiri.set_rule_enabled(rule_id, enabled)

    def delete_rule(self, rule_id: str) -> None:
        onigiri.delete_kwin_rule(rule_id)

    def launch_tile_command(self, tile: TileModel) -> None:
        """
        Launch only this tile's command.
        Uses the command stored on the TileModel.
        """
        cmd = (tile.command or "").strip()
        if not cmd:
            raise ValueError("Tile has no command configured.")

        # Simple implementation: just spawn the command.
        # (This mirrors what the engine does for profile commands.)
        subprocess.Popen(cmd, shell=True)


class ConfigValidator:
    """
    Central place for validating tiles and profiles before saving/applying/launching.
    Returns lists of human-readable error messages.
    """

    def validate_tile(self, tile: TileModel) -> List[str]:
        errors: List[str] = []
        tile_label = tile.name or "<tile>"

        # Geometry sanity
        if tile.width <= 0 or tile.height <= 0:
            errors.append(f"Tile '{tile_label}' has invalid size (width/height must be > 0).")

        # Launch mode specific checks
        mode = tile.launch_mode

        if mode == "helper":
            if not tile.shell_command.strip():
                errors.append(
                    f"Tile '{tile_label}' uses Terminal helper but has no shell command."
                )
            if not tile.command.strip():
                errors.append(
                    f"Tile '{tile_label}' helper did not generate a final command."
                )

        elif mode == "app":
            if not tile.command.strip():
                errors.append(
                    f"Tile '{tile_label}' uses Application mode but has no command."
                )

        # Raw mode: command is optional, so we don't force it.

        return errors

    def validate_profile(self, profile: ProfileModel) -> List[str]:
        """
        Validate a single profile and all of its tiles.
        Returns a flat list of error strings.
        """
        errors: List[str] = []

        # Profile name
        if not profile.name.strip():
            errors.append("Profile name is empty.")

        # Duplicate tile names
        seen_names = set()
        for tile in profile.tiles:
            if tile.name:
                if tile.name in seen_names:
                    errors.append(f"Duplicate tile name: '{tile.name}'.")
                seen_names.add(tile.name)

            # Tile-specific checks
            errors.extend(self.validate_tile(tile))

        return errors


# ===================== Layout Canvas =====================


class LayoutCanvas(QWidget):
    """
    Recursive split-based layout editor.

    - Internal representation is a binary tree of splits.
    - Leaves represent slots that can be assigned to tiles.
    - Splits are draggable edges; moving them adjusts child ratios.
    - No per-slot manual geometry; all rects are derived from the tree.
    """

    tileSelected = pyqtSignal(int)       # index in profile.tiles, or -1 if none
    geometryChanged = pyqtSignal(int)    # index in profile.tiles whose geometry changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

        self._profile: Optional[ProfileModel] = None
        self._gap: int = 0

        # Optional background image for the canvas
        self._background_pixmap: Optional[QPixmap] = None

        # minimal width/height for a leaf region in SCREEN pixels
        # (change this value if you want bigger/smaller minimum tiles)
        self._min_leaf_size: float = 10.0

        # Recursive tree root:
        #   leaf: {"type": "leaf", "id": int, "tile_name": str}
        #   split: {"type": "split", "orientation": "h"|"v", "ratio": float,
        #           "first": node, "second": node}
        self._root: Optional[dict] = None
        self._next_leaf_id: int = 0

        # Cached geometry derived from the tree
        self._leaf_rects: dict[int, dict] = {}   # id -> {"x","y","w","h","tile_name"}
        self._split_lines: list[dict] = []       # {"node", "orientation", "x1","y1","x2","y2","parent_x","parent_y","parent_w","parent_h"}

        # Selection & interaction
        self._selected_leaf_id: Optional[int] = None
        self._active_split_node: Optional[dict] = None
        self._active_split_orientation: Optional[str] = None
        self._last_mouse_pos: Optional[QPointF] = None

        # World->canvas transform
        self._scale: float = 1.0
        self._offset_x: float = 0.0
        self._offset_y: float = 0.0

    def set_background_image(self, path: Optional[str]) -> None:
        """
        Load an image from disk and use it as canvas background.
        Pass None or an empty string to clear the background.
        """
        if not path:
            self._background_pixmap = None
        else:
            pm = QPixmap(path)
            if pm.isNull():
                # Failed to load -> just clear
                self._background_pixmap = None
            else:
                self._background_pixmap = pm

        self.update()

    # ========= basic helpers =========

    def _alloc_leaf_id(self) -> int:
        lid = self._next_leaf_id
        self._next_leaf_id += 1
        return lid

    def _compute_screen_bbox(self) -> tuple[int, int]:
        """
        Compute the virtual screen size for the currently selected monitor.

        - If the profile has monitor == "default", use the primary screen.
        - Otherwise, try to find the QScreen with that name.
        """
        monitor_name = None
        if self._profile is not None:
            monitor_name = getattr(self._profile, "monitor", None)

        screen = None
        if monitor_name and monitor_name != "default":
            # Look for the matching QScreen by name
            for s in QApplication.screens():
                if s.name() == monitor_name:
                    screen = s
                    break

        if screen is None:
            screen = QApplication.primaryScreen()

        if screen:
            geo = screen.geometry()
            screen_w = geo.width()
            screen_h = geo.height()
        else:
            screen_w, screen_h = 1920, 1080

        return max(screen_w, 1), max(screen_h, 1)

    def _recompute_transform(self) -> None:
        """
        Compute scale and offset so that the whole screen fits into the canvas,
        using the entire widget area (no extra margins).
        """
        screen_w, screen_h = self._compute_screen_bbox()
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            self._scale = 1.0
            self._offset_x = 0.0
            self._offset_y = 0.0
            return

        # Use full widget space, no outer margin
        available_w = max(rect.width(), 1)
        available_h = max(rect.height(), 1)

        sx = available_w / float(screen_w)
        sy = available_h / float(screen_h)
        self._scale = min(sx, sy)

        canvas_w = screen_w * self._scale
        canvas_h = screen_h * self._scale

        # Center the screen inside the widget (no extra padding beyond
        # what comes from aspect ratio differences)
        self._offset_x = (rect.width() - canvas_w) / 2.0
        self._offset_y = (rect.height() - canvas_h) / 2.0

    def _world_to_canvas(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        cx = self._offset_x + x * self._scale
        cy = self._offset_y + y * self._scale
        cw = w * self._scale
        ch = h * self._scale
        return cx, cy, cw, ch

    def _canvas_to_world(self, x: float, y: float) -> tuple[float, float]:
        wx = (x - self._offset_x) / self._scale
        wy = (y - self._offset_y) / self._scale
        return wx, wy

    # ========= tree operations =========

    def _ensure_root(self) -> None:
        """Ensure there is at least one full-screen leaf."""
        if self._root is not None:
            return
        leaf_id = self._alloc_leaf_id()
        self._root = {"type": "leaf", "id": leaf_id, "tile_name": ""}

    def _rebuild_from_tree(self) -> None:
        """
        Compute leaf rectangles and split lines from the current tree.
        """
        self._leaf_rects.clear()
        self._split_lines.clear()
        if self._root is None:
            return

        screen_w, screen_h = self._compute_screen_bbox()

        def walk(node: dict, x: float, y: float, w: float, h: float) -> None:
            if node["type"] == "leaf":
                self._leaf_rects[node["id"]] = {
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "tile_name": node.get("tile_name", ""),
                }
                return

            orient = node.get("orientation", "v")
            ratio = float(node.get("ratio", 0.5))

            # derive a min ratio from the desired leaf pixel size
            if orient == "v":
                # children share parent width
                min_ratio = self._min_leaf_size / max(w, 1.0)
            else:
                # children share parent height
                min_ratio = self._min_leaf_size / max(h, 1.0)

            # keep it sane on very small parents and leave room for both sides
            min_ratio = max(0.01, min(min_ratio, 0.49))
            max_ratio = 1.0 - min_ratio

            ratio = max(min_ratio, min(ratio, max_ratio))
            node["ratio"] = ratio

            if orient == "v":
                w1 = w * ratio
                w2 = w - w1
                x_split = x + w1
                # record split line
                self._split_lines.append(
                    {
                        "node": node,
                        "orientation": "v",
                        "x1": x_split,
                        "y1": y,
                        "x2": x_split,
                        "y2": y + h,
                        "parent_x": x,
                        "parent_y": y,
                        "parent_w": w,
                        "parent_h": h,
                    }
                )
                walk(node["first"], x, y, w1, h)
                walk(node["second"], x_split, y, w2, h)
            else:
                h1 = h * ratio
                h2 = h - h1
                y_split = y + h1
                self._split_lines.append(
                    {
                        "node": node,
                        "orientation": "h",
                        "x1": x,
                        "y1": y_split,
                        "x2": x + w,
                        "y2": y_split,
                        "parent_x": x,
                        "parent_y": y,
                        "parent_w": w,
                        "parent_h": h,
                    }
                )
                walk(node["first"], x, y, w, h1)
                walk(node["second"], x, y_split, w, h2)

        walk(self._root, 0.0, 0.0, float(screen_w), float(screen_h))

    def _find_leaf_at_canvas_pos(self, pos: QPointF) -> Optional[int]:
        if not self._leaf_rects:
            return None
        x = pos.x()
        y = pos.y()
        for lid, rect in self._leaf_rects.items():
            cx, cy, cw, ch = self._world_to_canvas(
                rect["x"], rect["y"], rect["w"], rect["h"]
            )
            if cx <= x <= cx + cw and cy <= y <= cy + ch:
                return lid
        return None

    def _find_split_at_canvas_pos(self, pos: QPointF, tol: float = 6.0) -> Optional[dict]:
        if not self._split_lines:
            return None
        x = pos.x()
        y = pos.y()
        best = None
        best_dist = None
        for info in self._split_lines:
            if info["orientation"] == "v":
                cx1, cy1, _, _ = self._world_to_canvas(info["x1"], info["y1"], 0.0, 0.0)
                _, cy2, _, _ = self._world_to_canvas(info["x2"], info["y2"], 0.0, 0.0)
                # vertical line at x = cx1, y in [cy1, cy2]
                if cy1 <= y <= cy2:
                    dist = abs(x - cx1)
                else:
                    continue
            else:
                cx1, cy1, _, _ = self._world_to_canvas(info["x1"], info["y1"], 0.0, 0.0)
                cx2, _, _, _ = self._world_to_canvas(info["x2"], info["y2"], 0.0, 0.0)
                if cx1 <= x <= cx2:
                    dist = abs(y - cy1)
                else:
                    continue

            if dist <= tol and (best is None or dist < best_dist):
                best = info
                best_dist = dist
        return best

    # ========= public API =========

    def set_profile(self, profile: Optional[ProfileModel]) -> None:
        """
        Called by the main window whenever the current profile changes
        or when the user explicitly clicks "Edit Layout".

        Behavior:
        - If the profile has saved layout_slots, reconstruct the split tree
          from those rectangles.
        - Otherwise start with a single full-screen leaf.
        """
        self._profile = profile
        self._selected_leaf_id = None
        self._active_split_node = None
        self._active_split_orientation = None
        self._last_mouse_pos = None
        self._leaf_rects.clear()
        self._split_lines.clear()
        self._root = None
        self._next_leaf_id = 0

        if profile is None:
            # No profile -> no background, no tree
            self.set_background_image(None)
            self.update()
            return

        # Gap from profile
        self._gap = profile.tile_gap

        # Monitor-specific background image (if configured)
        bg_path = ""
        try:
            backgrounds = profile.monitor_backgrounds
            if isinstance(backgrounds, dict):
                bg_path = backgrounds.get(profile.monitor, "") or ""
        except Exception:
            bg_path = ""

        if bg_path:
            self.set_background_image(bg_path)
        else:
            self.set_background_image(None)


        # If a saved layout exists, rebuild the tree from it.
        raw_slots = getattr(profile, "layout_slots", None) or []
        if raw_slots:
            self._init_tree_from_profile_layout(raw_slots)
        else:
            # No saved layout yet – start with a single full-screen leaf
            self._ensure_root()

        self._rebuild_from_tree()
        self._recompute_transform()
        self.update()

    def _init_tree_from_profile_layout(self, raw_slots: list[dict[str, Any]]) -> None:
        """
        Build the internal split tree (_root) from saved layout_slots.

        Assumes:
        - raw_slots are non-overlapping, axis-aligned rectangles
        - they were originally produced by this same split-based layout
        """
        # Convert to a clean rect list
        rects: list[dict[str, Any]] = []
        for s in raw_slots:
            rects.append(
                {
                    "x": float(s.get("x", 0)),
                    "y": float(s.get("y", 0)),
                    "w": float(s.get("w", 0)),
                    "h": float(s.get("h", 0)),
                    "tile_name": str(s.get("tile_name") or ""),
                }
            )

        if not rects:
            self._ensure_root()
            return

        # Compute bounding box of all rects – this becomes the root region
        min_x = min(r["x"] for r in rects)
        min_y = min(r["y"] for r in rects)
        max_x = max(r["x"] + r["w"] for r in rects)
        max_y = max(r["y"] + r["h"] for r in rects)
        bounds = (min_x, min_y, max_x - min_x, max_y - min_y)

        # Reset ID allocator and build tree
        self._next_leaf_id = 0
        self._root = self._build_tree_from_rects(rects, bounds)

        if self._root is None:
            # Fallback: just one full-screen leaf
            self._ensure_root()

    def _build_tree_from_rects(
        self,
        rects: list[dict[str, Any]],
        bounds: tuple[float, float, float, float],
        eps: float = 0.5,
    ) -> Optional[dict]:
        """
        Recursively reconstruct a split tree from a set of rectangles.

        - If the region can be partitioned by a vertical line where no rect
          crosses that line, we create a vertical split.
        - Otherwise, we try the same with a horizontal line.
        - If no clean split is found and there is exactly one rect, we make
          a leaf.
        """
        x0, y0, w0, h0 = bounds
        if not rects:
            return None

        if len(rects) == 1:
            r = rects[0]
            leaf_id = self._alloc_leaf_id()
            return {
                "type": "leaf",
                "id": leaf_id,
                "tile_name": r.get("tile_name", ""),
            }

        # ----- try vertical splits -----
        xs = set()
        for r in rects:
            xs.add(r["x"])
            xs.add(r["x"] + r["w"])
        xs = sorted(xs)

        # candidate split lines (ignore outer edges)
        candidates_x = [x for x in xs if x0 + eps < x < x0 + w0 - eps]

        for split_x in candidates_x:
            left: list[dict[str, Any]] = []
            right: list[dict[str, Any]] = []
            crossing: list[dict[str, Any]] = []

            for r in rects:
                rx1 = r["x"]
                rx2 = r["x"] + r["w"]
                if rx2 <= split_x + eps:
                    left.append(r)
                elif rx1 >= split_x - eps:
                    right.append(r)
                else:
                    crossing.append(r)

            if not crossing and left and right:
                # valid vertical partition
                # left bounds
                l_min_x = min(r["x"] for r in left)
                l_min_y = min(r["y"] for r in left)
                l_max_x = max(r["x"] + r["w"] for r in left)
                l_max_y = max(r["y"] + r["h"] for r in left)
                left_bounds = (l_min_x, l_min_y, l_max_x - l_min_x, l_max_y - l_min_y)

                # right bounds
                r_min_x = min(r["x"] for r in right)
                r_min_y = min(r["y"] for r in right)
                r_max_x = max(r["x"] + r["w"] for r in right)
                r_max_y = max(r["y"] + r["h"] for r in right)
                right_bounds = (r_min_x, r_min_y, r_max_x - r_min_x, r_max_y - r_min_y)

                node: dict[str, Any] = {
                    "type": "split",
                    "orientation": "v",
                    "ratio": (split_x - x0) / float(w0) if w0 > 0 else 0.5,
                }
                node["first"] = self._build_tree_from_rects(left, left_bounds, eps)
                node["second"] = self._build_tree_from_rects(right, right_bounds, eps)
                return node

        # ----- try horizontal splits -----
        ys = set()
        for r in rects:
            ys.add(r["y"])
            ys.add(r["y"] + r["h"])
        ys = sorted(ys)

        candidates_y = [y for y in ys if y0 + eps < y < y0 + h0 - eps]

        for split_y in candidates_y:
            top: list[dict[str, Any]] = []
            bottom: list[dict[str, Any]] = []
            crossing: list[dict[str, Any]] = []

            for r in rects:
                ry1 = r["y"]
                ry2 = r["y"] + r["h"]
                if ry2 <= split_y + eps:
                    top.append(r)
                elif ry1 >= split_y - eps:
                    bottom.append(r)
                else:
                    crossing.append(r)

            if not crossing and top and bottom:
                # valid horizontal partition
                t_min_x = min(r["x"] for r in top)
                t_min_y = min(r["y"] for r in top)
                t_max_x = max(r["x"] + r["w"] for r in top)
                t_max_y = max(r["y"] + r["h"] for r in top)
                top_bounds = (t_min_x, t_min_y, t_max_x - t_min_x, t_max_y - t_min_y)

                b_min_x = min(r["x"] for r in bottom)
                b_min_y = min(r["y"] for r in bottom)
                b_max_x = max(r["x"] + r["w"] for r in bottom)
                b_max_y = max(r["y"] + r["h"] for r in bottom)
                bottom_bounds = (b_min_x, b_min_y, b_max_x - b_min_x, b_max_y - b_min_y)

                node = {
                    "type": "split",
                    "orientation": "h",
                    "ratio": (split_y - y0) / float(h0) if h0 > 0 else 0.5,
                }
                node["first"] = self._build_tree_from_rects(top, top_bounds, eps)
                node["second"] = self._build_tree_from_rects(bottom, bottom_bounds, eps)
                return node

        # Fallback: treat this region as a single leaf (geometry will still
        # be correct due to export_slots_for_profile using computed rects).
        leaf_id = self._alloc_leaf_id()
        # pick a tile name if all rects share the same name, otherwise empty
        names = {r.get("tile_name", "") for r in rects}
        tile_name = names.pop() if len(names) == 1 else ""
        return {"type": "leaf", "id": leaf_id, "tile_name": tile_name}

    def export_slots_for_profile(self) -> list[dict[str, Any]]:
        """
        Export current leaf rectangles to a flat list compatible with
        ProfileModel.layout_slots.
        """
        out: list[dict[str, Any]] = []
        for lid, rect in self._leaf_rects.items():
            out.append(
                {
                    "x": int(round(rect["x"])),
                    "y": int(round(rect["y"])),
                    "w": int(round(rect["w"])),
                    "h": int(round(rect["h"])),
                    "tile_name": str(rect.get("tile_name", "")),
                }
            )
        return out

    def set_selected_index(self, idx: Optional[int]) -> None:
        """
        MainWindow -> Canvas:
        - idx is a tile index in profile.tiles
        - We find the leaf that is assigned to that tile name and select it.
        - If idx is None, we clear selection.
        """
        self._selected_leaf_id = None
        if self._profile is None or idx is None:
            self.update()
            return

        tiles = self._profile.tiles
        if not (0 <= idx < len(tiles)):
            self.update()
            return

        target_name = tiles[idx].name
        for lid, rect in self._leaf_rects.items():
            if rect.get("tile_name") == target_name:
                self._selected_leaf_id = lid
                break

        self.update()

    # ========= Qt events =========

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute_transform()
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        if self._root is None or self._profile is None:
            return

        # Rebuild geometry each paint to keep it in sync
        self._rebuild_from_tree()

        from PyQt6.QtGui import QPainter, QColor, QPen, QBrush
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background: fill everything with dark gray
        painter.fillRect(self.rect(), QColor(30, 30, 30))

        # If we have a background image, draw it only over the usable screen area
        if self._background_pixmap is not None:
            # "World" coords: 0..screen_w, 0..screen_h (the virtual screen)
            screen_w, screen_h = self._compute_screen_bbox()
            cx, cy, cw, ch = self._world_to_canvas(0.0, 0.0, float(screen_w), float(screen_h))

            # Draw the pixmap scaled into that screen rectangle
            painter.drawPixmap(int(cx), int(cy), int(cw), int(ch), self._background_pixmap)

        # Draw leaves (slots) — no visual gap, use logical rects directly
        for lid, rect in self._leaf_rects.items():
            x = rect["x"]
            y = rect["y"]
            w = rect["w"]
            h = rect["h"]

            cx, cy, cw, ch = self._world_to_canvas(x, y, w, h)

            is_selected = (lid == self._selected_leaf_id)

            painter.setBrush(QBrush(QColor(70, 90, 110, 180)))
            painter.setPen(QPen(QColor(200, 200, 200) if is_selected else QColor(120, 120, 120), 1.0))
            painter.drawRect(int(cx), int(cy), int(cw), int(ch))

            name = rect.get("tile_name") or ""
            if name:
                painter.setPen(QColor(230, 230, 230))
                painter.drawText(int(cx) + 4, int(cy) + 16, name)


        # Draw split lines as guides
        painter.setPen(QPen(QColor(220, 180, 80), 1.0))
        for info in self._split_lines:
            if info["orientation"] == "v":
                cx1, cy1, _, _ = self._world_to_canvas(info["x1"], info["y1"], 0.0, 0.0)
                _, cy2, _, _ = self._world_to_canvas(info["x2"], info["y2"], 0.0, 0.0)
                painter.drawLine(int(cx1), int(cy1), int(cx1), int(cy2))
            else:
                cx1, cy1, _, _ = self._world_to_canvas(info["x1"], info["y1"], 0.0, 0.0)
                cx2, _, _, _ = self._world_to_canvas(info["x2"], info["y2"], 0.0, 0.0)
                painter.drawLine(int(cx1), int(cy1), int(cx2), int(cy1))

        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._profile is None or self._root is None:
            return

        pos = event.position()

        if event.button() == Qt.MouseButton.LeftButton:
            # First see if we hit a split line
            split_info = self._find_split_at_canvas_pos(pos)
            if split_info is not None:
                self._active_split_node = split_info["node"]
                self._active_split_orientation = split_info["orientation"]
                self._last_mouse_pos = pos
                return

            # Otherwise select a leaf
            lid = self._find_leaf_at_canvas_pos(pos)
            self._selected_leaf_id = lid
            self._last_mouse_pos = pos

            if self._profile is not None and lid is not None:
                leaf = self._leaf_rects.get(lid)
                if leaf:
                    tile_name = leaf.get("tile_name") or ""
                    if tile_name:
                        idx = self._find_tile_index_by_name(self._profile, tile_name)
                        if idx is not None:
                            self.tileSelected.emit(idx)

            self.update()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._active_split_node is None
            or self._active_split_orientation is None
            or self._last_mouse_pos is None
        ):
            return super().mouseMoveEvent(event)

        pos = event.position()
        dx_canvas = pos.x() - self._last_mouse_pos.x()
        dy_canvas = pos.y() - self._last_mouse_pos.y()
        self._last_mouse_pos = pos

        if self._scale <= 0:
            return

        dx_world = dx_canvas / self._scale
        dy_world = dy_canvas / self._scale

        # Find the split info for this node (using latest geometry)
        self._rebuild_from_tree()
        info = None
        for s in self._split_lines:
            if s["node"] is self._active_split_node:
                info = s
                break
        if info is None:
            return

        # minimal child size in pixels (same value as used in _rebuild_from_tree)
        min_size = self._min_leaf_size
        snap_dist = 8.0

        if self._active_split_orientation == "v":
            # move x_split
            parent_x = info["parent_x"]
            parent_w = info["parent_w"]
            x_old = info["x1"]
            x_new = x_old + dx_world

            # clamp inside parent with min_size
            left_min = parent_x + min_size
            right_max = parent_x + parent_w - min_size
            if right_max <= left_min:
                return
            x_new = max(left_min, min(x_new, right_max))

            # magnetic snap against other vertical lines that overlap this parent rect
            candidates: list[float] = []
            for s in self._split_lines:
                if s["orientation"] != "v" or s["node"] is self._active_split_node:
                    continue
                # Only splits that overlap vertically with this parent region
                if not (s["y2"] <= info["parent_y"] or s["y1"] >= info["parent_y"] + info["parent_h"]):
                    candidates.append(s["x1"])
            for cx in candidates:
                if abs(cx - x_new) <= snap_dist:
                    x_new = cx
                    break

            # derive new ratio and clamp based on _min_leaf_size
            new_ratio = (x_new - parent_x) / parent_w

            min_ratio = self._min_leaf_size / max(parent_w, 1.0)
            min_ratio = max(0.01, min(min_ratio, 0.49))
            max_ratio = 1.0 - min_ratio

            new_ratio = max(min_ratio, min(new_ratio, max_ratio))
            self._active_split_node["orientation"] = "v"
            self._active_split_node["ratio"] = new_ratio

        else:
            # horizontal split, move y_split
            parent_y = info["parent_y"]
            parent_h = info["parent_h"]
            y_old = info["y1"]
            y_new = y_old + dy_world

            top_min = parent_y + min_size
            bottom_max = parent_y + parent_h - min_size
            if bottom_max <= top_min:
                return
            y_new = max(top_min, min(y_new, bottom_max))

            candidates: list[float] = []
            for s in self._split_lines:
                if s["orientation"] != "h" or s["node"] is self._active_split_node:
                    continue
                if not (s["x2"] <= info["parent_x"] or s["x1"] >= info["parent_x"] + info["parent_w"]):
                    candidates.append(s["y1"])
            for cy in candidates:
                if abs(cy - y_new) <= snap_dist:
                    y_new = cy
                    break

            new_ratio = (y_new - parent_y) / parent_h

            min_ratio = self._min_leaf_size / max(parent_h, 1.0)
            min_ratio = max(0.01, min(min_ratio, 0.49))
            max_ratio = 1.0 - min_ratio

            new_ratio = max(min_ratio, min(new_ratio, max_ratio))
            self._active_split_node["orientation"] = "h"
            self._active_split_node["ratio"] = new_ratio

        # After adjusting ratio, rebuild geometry and propagate into tiles
        self._rebuild_from_tree()
        self._push_geometry_into_tiles()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active_split_node = None
            self._active_split_orientation = None
            self._last_mouse_pos = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._profile is None or self._root is None:
            return

        pos = event.pos()
        pos_f = QPointF(pos)

        # Make sure geometry is up to date
        self._rebuild_from_tree()

        # ---------- 1) Edge menu: right-click on split line ----------
        split_info = self._find_split_at_canvas_pos(pos_f)
        if split_info is not None:
            node = split_info["node"]
            menu = QMenu(self)

            act_combine = None
            can_combine = False

            # Combine only if both children are leaves and same size
            first = node.get("first")
            second = node.get("second")
            if (
                isinstance(first, dict)
                and isinstance(second, dict)
                and first.get("type") == "leaf"
                and second.get("type") == "leaf"
            ):
                lid1 = first.get("id")
                lid2 = second.get("id")
                r1 = self._leaf_rects.get(lid1)
                r2 = self._leaf_rects.get(lid2)
                if r1 and r2:
                    if (
                        abs(r1["w"] - r2["w"]) < 1e-3
                        and abs(r1["h"] - r2["h"]) < 1e-3
                    ):
                        can_combine = True

            if can_combine:
                act_combine = QAction("Combine tiles (remove split)", self)
                menu.addAction(act_combine)

            chosen = menu.exec(event.globalPos())
            if chosen is None:
                return

            if act_combine is not None and chosen == act_combine:
                self._combine_split_node(node)

            return

        # ---------- 2) Tile menu: right-click inside a tile ----------
        lid = self._find_leaf_at_canvas_pos(pos_f)
        if lid is None:
            return

        leaf_rect = self._leaf_rects.get(lid)
        if leaf_rect is None:
            return

        menu = QMenu(self)

        act_split_h = QAction("Split horizontally", self)
        act_split_v = QAction("Split vertically", self)
        menu.addAction(act_split_h)
        menu.addAction(act_split_v)
        menu.addSeparator()

        assign_menu = menu.addMenu("Assign tile")

        # collect tile names that are already used by some other leaf
        used_names = {
            r.get("tile_name")
            for k, r in self._leaf_rects.items()
            if k != lid and r.get("tile_name")
        }
        tiles = self._profile.tiles

        act_none = QAction("<none>", self)
        assign_menu.addAction(act_none)

        tile_actions: dict[QAction, str] = {}
        for t in tiles:
            name = t.name or "<unnamed>"
            if name in used_names and name != leaf_rect.get("tile_name"):
                continue
            act = QAction(name, self)
            assign_menu.addAction(act)
            tile_actions[act] = name

        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return

        if chosen == act_split_h:
            count, ok = QInputDialog.getInt(
                self,
                "Horizontal split",
                "How many horizontal tiles?",
                2,   # default
                2,   # min
                16,  # max
                1,   # step
            )
            if ok:
                self._split_leaf_into(lid, count, horizontal=True)

        elif chosen == act_split_v:
            count, ok = QInputDialog.getInt(
                self,
                "Vertical split",
                "How many vertical tiles?",
                2,
                2,
                16,
                1,
            )
            if ok:
                self._split_leaf_into(lid, count, horizontal=False)

        elif chosen == act_none:
            leaf_rect["tile_name"] = ""
            # also clear in tree
            self._set_leaf_tile_name(lid, "")
            self._push_geometry_into_tiles()
            self.update()

        elif chosen in tile_actions:
            new_name = tile_actions[chosen]
            leaf_rect["tile_name"] = new_name
            self._set_leaf_tile_name(lid, new_name)
            # select that tile in the UI
            tile_idx = self._find_tile_index_by_name(self._profile, new_name)
            if tile_idx is not None:
                self.tileSelected.emit(tile_idx)
            self._push_geometry_into_tiles()
            self.update()

    # ========= split / leaf helpers =========

    def _find_leaf_node(self, node: dict, leaf_id: int) -> Optional[dict]:
        if node["type"] == "leaf":
            return node if node["id"] == leaf_id else None
        res = self._find_leaf_node(node["first"], leaf_id)
        if res is not None:
            return res
        return self._find_leaf_node(node["second"], leaf_id)

    def _replace_leaf_with_split(self, node: dict, leaf_id: int, new_node: dict) -> bool:
        """
        Recursively replace a leaf with id leaf_id by new_node.
        Returns True if replacement happened.
        """
        if node["type"] == "leaf":
            return False
        if node["first"]["type"] == "leaf" and node["first"]["id"] == leaf_id:
            node["first"] = new_node
            return True
        if node["second"]["type"] == "leaf" and node["second"]["id"] == leaf_id:
            node["second"] = new_node
            return True
        if self._replace_leaf_with_split(node["first"], leaf_id, new_node):
            return True
        return self._replace_leaf_with_split(node["second"], leaf_id, new_node)

    def _replace_split_with_leaf(self, node: dict, target_split: dict, new_leaf: dict) -> bool:
        """
        Recursively replace a split node with a leaf.
        Returns True if replacement happened.
        """
        if node.get("type") != "split":
            return False

        if node.get("first") is target_split:
            node["first"] = new_leaf
            return True
        if node.get("second") is target_split:
            node["second"] = new_leaf
            return True

        if self._replace_split_with_leaf(node.get("first"), target_split, new_leaf):
            return True
        return self._replace_split_with_leaf(node.get("second"), target_split, new_leaf)

    def _combine_split_node(self, split_node: dict) -> None:
        """
        Remove a split node and merge its two child leaves into one leaf.
        Keeps one of the tile assignments (if any).
        """
        if self._root is None:
            return

        first = split_node.get("first")
        second = split_node.get("second")
        if not (
            isinstance(first, dict)
            and isinstance(second, dict)
            and first.get("type") == "leaf"
            and second.get("type") == "leaf"
        ):
            return

        # Prefer a non-empty tile_name if present
        tile_name = (first.get("tile_name") or "") or (second.get("tile_name") or "")

        new_leaf = {
            "type": "leaf",
            "id": self._alloc_leaf_id(),
            "tile_name": tile_name,
        }

        if self._root is split_node:
            self._root = new_leaf
        else:
            self._replace_split_with_leaf(self._root, split_node, new_leaf)

        self._rebuild_from_tree()
        self._push_geometry_into_tiles()
        self.update()

    def _split_leaf(self, leaf_id: int, horizontal: bool) -> None:
        """
        Backwards-compatible: split into exactly 2 parts.
        """
        self._split_leaf_into(leaf_id, 2, horizontal)

    def _split_leaf_into(self, leaf_id: int, count: int, horizontal: bool) -> None:
        """
        Split a leaf into `count` equal parts in the given orientation.
        """
        if self._root is None:
            return
        if count <= 1:
            return

        # find leaf node
        if self._root.get("type") == "leaf" and self._root.get("id") == leaf_id:
            leaf_node = self._root
            is_root = True
        else:
            leaf_node = self._find_leaf_node(self._root, leaf_id)
            is_root = False

        if leaf_node is None or leaf_node.get("type") != "leaf":
            return

        tile_name = leaf_node.get("tile_name", "")

        # Build a subtree that splits this leaf into `count` equal parts
        new_subtree = self._build_equal_split_chain(count, horizontal, tile_name)

        if is_root:
            self._root = new_subtree
        else:
            self._replace_leaf_with_split(self._root, leaf_id, new_subtree)

        self._rebuild_from_tree()
        self._push_geometry_into_tiles()
        self.update()

    def _build_equal_split_chain(self, count: int, horizontal: bool, tile_name: str) -> dict:
        """
        Build a chain of splits that divides a region into `count` equal parts.

        The first leaf keeps the original tile assignment, the rest start empty.
        """
        orientation = "h" if horizontal else "v"

        if count == 1:
            return {
                "type": "leaf",
                "id": self._alloc_leaf_id(),
                "tile_name": tile_name,
            }

        # First leaf keeps the tile_name
        first_leaf = {
            "type": "leaf",
            "id": self._alloc_leaf_id(),
            "tile_name": tile_name,
        }

        # Remaining parts built recursively (they start without tile assignment)
        rest_subtree = self._build_equal_split_chain(count - 1, horizontal, "")

        # ratio gives first_leaf 1/count of the parent region
        node: dict[str, Any] = {
            "type": "split",
            "orientation": orientation,
            "ratio": 1.0 / float(count),
            "first": first_leaf,
            "second": rest_subtree,
        }
        return node

    def _set_leaf_tile_name(self, leaf_id: int, name: str) -> None:
        if self._root is None:
            return

        def walk(node: dict) -> None:
            if node["type"] == "leaf":
                if node["id"] == leaf_id:
                    node["tile_name"] = name
                return
            walk(node["first"])
            walk(node["second"])

        walk(self._root)

    def _push_geometry_into_tiles(self) -> None:
        """Push current leaf rects into the corresponding TileModel objects."""
        if self._profile is None:
            return

        tiles = self._profile.tiles

        # gap in pixels; comes from the profile / UI
        gap = float(self._gap)
        if gap < 0.0:
            gap = 0.0

        screen_w, screen_h = self._compute_screen_bbox()
        screen_w = float(screen_w)
        screen_h = float(screen_h)
        eps = 0.5  # tolerance for boundary checks

        for lid, rect in self._leaf_rects.items():
            name = rect.get("tile_name") or ""
            if not name:
                continue

            idx = self._find_tile_index_by_name(self._profile, name)
            if idx is None or not (0 <= idx < len(tiles)):
                continue

            x = float(rect["x"])
            y = float(rect["y"])
            w = float(rect["w"])
            h = float(rect["h"])

            if gap > 0.0:
                # Internal shared edges: gap/2 on each side -> total gap between tiles = gap
                # Outer screen edges: full gap to the screen border
                left_pad = gap if abs(x - 0.0) <= eps else gap / 2.0
                right_pad = gap if abs((x + w) - screen_w) <= eps else gap / 2.0
                top_pad = gap if abs(y - 0.0) <= eps else gap / 2.0
                bottom_pad = gap if abs((y + h) - screen_h) <= eps else gap / 2.0

                # Clamp so we don't collapse rectangles if gap is huge
                total_w_pad = min(left_pad + right_pad, max(w - 1.0, 0.0))
                total_h_pad = min(top_pad + bottom_pad, max(h - 1.0, 0.0))

                x_out = x + left_pad
                y_out = y + top_pad
                w_out = max(1.0, w - total_w_pad)
                h_out = max(1.0, h - total_h_pad)
            else:
                x_out = x
                y_out = y
                w_out = w
                h_out = h

            tiles[idx].set_geometry(
                int(round(x_out)),
                int(round(y_out)),
                int(round(w_out)),
                int(round(h_out)),
            )
            self.geometryChanged.emit(idx)

    def _find_tile_index_by_name(self, profile: ProfileModel, name: str) -> Optional[int]:
        tiles = profile.tiles
        for i, t in enumerate(tiles):
            if t.name == name:
                return i
        return None


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
        self.mode_combo.currentIndexChanged.connect(self._update_mode_enabled_state)
        self.terminal_combo.currentIndexChanged.connect(self._recompute_command_from_helper)
        self.shell_command_edit.textChanged.connect(self._recompute_command_from_helper)
        self.app_combo.currentIndexChanged.connect(self._on_app_changed)
        self.name_edit.textChanged.connect(self._on_name_changed)
        self.btn_launch_tile.clicked.connect(self._on_launch_tile_clicked)

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
        self.launchTileRequested.emit()

    def _on_geometry_spin_changed(self) -> None:
        if self._loading:
            return
        self.geometryEdited.emit()

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
                    with desktop_file.open("r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                except Exception:
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

    def _on_app_changed(self, index: int) -> None:
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
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

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
        self.count_spin.valueChanged.connect(self._rebuild_entries)

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


# ===================== Main Window =====================


class MainWindow(QWidget):
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
                border-radius: 6px;
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
        self.btn_delete_rule.setToolTip(
            "Delete the selected KWin window rule from kwinrulesrc."
        )

        # Right: tile editor
        self.tile_editor = TileEditor()

        # Bottom canvas (profile designer)
        self.canvas = LayoutCanvas()

        # Make sure the canvas asks for more vertical space
        self.canvas.setMinimumHeight(450)

        # Button to load a background image for the canvas
        self.btn_canvas_bg = QPushButton("Load Canvas Background")
        self.btn_canvas_bg.setToolTip("Pick an image (e.g. a desktop screenshot) as canvas background")

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

        # Buttons to open detail dialogs
        self.btn_open_profiles = QPushButton("Edit Profiles…")
        self.btn_open_tiles = QPushButton("Edit Tiles…")
        self.btn_open_rules = QPushButton("KWin Rules…")

        # Layout editor buttons
        self.btn_edit_layout = QPushButton("Edit Layout")
        self.btn_new_layout = QPushButton("New Layout")
        self.btn_rename_layout = QPushButton("Rename Layout")
        self.btn_save_layout = QPushButton("Save Layout")
        self.btn_load_layout = QPushButton("Load Layout")
        self.btn_delete_layout = QPushButton("Delete Layout")

        # Bottom buttons
        button_layout = QHBoxLayout()
        self.btn_new_profile = QPushButton("New Profile")
        self.btn_rename_profile = QPushButton("Rename Profile")
        self.btn_delete_profile = QPushButton("Delete Profile")
        self.btn_new_tile = QPushButton("New Tile")
        self.btn_delete_tile = QPushButton("Delete Tile")

        self.btn_undo = QPushButton("Undo")
        self.btn_redo = QPushButton("Redo")
        self.btn_undo.setEnabled(False)
        self.btn_redo.setEnabled(False)

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

        # Global bottom action row: undo/redo on the left, run actions on the right
        button_layout.addStretch()
        button_layout.addWidget(self.btn_undo)
        button_layout.addWidget(self.btn_redo)
        button_layout.addSpacing(20)
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_apply)
        button_layout.addWidget(self.btn_launch)
        button_layout.addWidget(self.btn_autostart)

        # Assemble main layout (new dashboard-style UI)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        # --- Top bar: title + profile selector + dialog shortcuts ---
        top_bar = QHBoxLayout()

        # Logo + Title (tight spacing)
        logo_label = QLabel()

        icon = self.geticon()
        pix = icon.pixmap(64, 64)
        logo_label.setPixmap(pix)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title_label = QLabel("Onigiri")
        title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Add logo + text RIGHT next to each other (no spacing)
        top_bar.addWidget(logo_label)
        top_bar.addWidget(title_label)

        # Add a tiny gap only AFTER the pair (to separate from the rest)
        top_bar.addSpacing(4)

        top_bar.addStretch(1)

        # Profile selector
        top_bar.addWidget(QLabel("Profile:"))
        top_bar.addWidget(self.profile_combo)

        # Dialog buttons
        top_bar.addSpacing(12)
        top_bar.addWidget(self.btn_open_profiles)
        top_bar.addWidget(self.btn_open_tiles)
        top_bar.addWidget(self.btn_open_rules)

        main_layout.addLayout(top_bar)

        # --- Row: profile settings (gap + monitor + layout) ---
        profile_settings_layout = QHBoxLayout()

        # Gap
        profile_settings_layout.addWidget(QLabel("Tile gap (px):"))
        profile_settings_layout.addWidget(self.tile_gap_spin)
        profile_settings_layout.addSpacing(20)

        # Monitor selector
        profile_settings_layout.addWidget(QLabel("Monitor:"))
        profile_settings_layout.addWidget(self.monitor_combo)
        profile_settings_layout.addSpacing(20)

        # Layout selector
        profile_settings_layout.addWidget(QLabel("Layout:"))
        profile_settings_layout.addWidget(self.layout_combo)
        profile_settings_layout.addSpacing(10)

        # Layout actions
        profile_settings_layout.addWidget(self.btn_edit_layout)
        profile_settings_layout.addWidget(self.btn_new_layout)
        profile_settings_layout.addWidget(self.btn_rename_layout)
        profile_settings_layout.addWidget(self.btn_save_layout)
        profile_settings_layout.addWidget(self.btn_load_layout)
        profile_settings_layout.addWidget(self.btn_delete_layout)

        profile_settings_layout.addStretch(1)
        main_layout.addLayout(profile_settings_layout)

        # --- Canvas card: background button + canvas ---
        canvas_block = QVBoxLayout()

        bg_button_row = QHBoxLayout()
        bg_button_row.addStretch(1)
        bg_button_row.addWidget(self.btn_canvas_bg)

        canvas_block.addLayout(bg_button_row)
        canvas_block.addWidget(self.canvas, stretch=1)

        main_layout.addLayout(canvas_block, stretch=1)

        # --- Bottom action row (undo/redo/save/apply/launch/autostart) ---
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

        # === Signals ===
        self.profile_list.currentItemChanged.connect(self.on_profile_selected)
        self.tile_list.currentItemChanged.connect(self.on_tile_selected)
        self.rules_list.itemChanged.connect(self.on_rule_toggled)
        self.btn_delete_rule.clicked.connect(self.on_delete_rule)
        self.btn_new_profile.clicked.connect(self.on_new_profile)
        self.btn_rename_profile.clicked.connect(self.on_rename_profile)
        self.btn_delete_profile.clicked.connect(self.on_delete_profile)
        self.btn_new_tile.clicked.connect(self.on_new_tile)
        self.btn_delete_tile.clicked.connect(self.on_delete_tile)
        self.btn_undo.clicked.connect(self.on_undo)
        self.btn_redo.clicked.connect(self.on_redo)
        self.btn_save.clicked.connect(self.on_save_config)
        self.btn_apply.clicked.connect(self.on_apply_profile)
        self.btn_launch.clicked.connect(self.on_launch_apps)
        self.btn_autostart.clicked.connect(self.on_create_autostart)
        self.btn_canvas_bg.clicked.connect(self.on_load_canvas_background)
        self.profile_combo.currentIndexChanged.connect(self.on_profile_combo_changed)

        # Profile settings changes (gap + monitor + layout)
        self._init_monitor_list()
        self.monitor_combo.currentIndexChanged.connect(self.on_monitor_changed)

        self.layout_combo.currentIndexChanged.connect(self.on_layout_combo_changed)

        self.tile_gap_spin.valueChanged.connect(self.on_profile_settings_changed)
        self.btn_edit_layout.clicked.connect(self.on_edit_layout)
        self.btn_new_layout.clicked.connect(self.on_new_layout)
        self.btn_rename_layout.clicked.connect(self.on_rename_layout)
        self.btn_save_layout.clicked.connect(self.on_save_layout)
        self.btn_load_layout.clicked.connect(self.on_load_layout)
        self.btn_delete_layout.clicked.connect(self.on_delete_layout)
        self.btn_open_profiles.clicked.connect(self.show_profiles_dialog)
        self.btn_open_tiles.clicked.connect(self.show_tiles_dialog)
        self.btn_open_rules.clicked.connect(self.show_rules_dialog)

        # Canvas signals
        self.canvas.tileSelected.connect(self.on_canvas_tile_selected)
        self.canvas.geometryChanged.connect(self.on_canvas_geometry_changed)

        # Tile editor live geometry updates -> update model + canvas
        self.tile_editor.geometryEdited.connect(self.flush_tile_edits)

        # Launch a single tile from the editor
        self.tile_editor.launchTileRequested.connect(self.on_launch_single_tile)

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

    def show_profiles_dialog(self) -> None:
        if not hasattr(self, "_profiles_dialog"):
            self._profiles_dialog = ProfilesDialog(self)
        self._profiles_dialog.show()
        self._profiles_dialog.raise_()
        self._profiles_dialog.activateWindow()

    def show_tiles_dialog(self) -> None:
        if not hasattr(self, "_tiles_dialog"):
            self._tiles_dialog = TilesDialog(self)
        self._tiles_dialog.show()
        self._tiles_dialog.raise_()
        self._tiles_dialog.activateWindow()

    def show_rules_dialog(self) -> None:
        if not hasattr(self, "_rules_dialog"):
            self._rules_dialog = KWinRulesDialog(self)
        # Always refresh rules before showing
        self.populate_system_rules()
        self._rules_dialog.show()
        self._rules_dialog.raise_()
        self._rules_dialog.activateWindow()

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
        return self._get_tile_from_item(current_item)

    def _get_tile_from_item(self, item: QListWidgetItem) -> Optional[TileModel]:
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
        if hasattr(self, "profile_combo"):
            self.profile_combo.blockSignals(True)
            self.profile_combo.clear()

        for idx, profile in enumerate(self.get_profiles()):
            display_name = profile.name or "<unnamed>"

            # Hidden list (logic driver)
            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.profile_list.addItem(item)

            # Top-bar combo (visual selector)
            if hasattr(self, "profile_combo"):
                self.profile_combo.addItem(display_name, idx)

        if hasattr(self, "profile_combo"):
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

    def _reload_profiles_and_rules(self) -> None:
        """
        Refresh the Profiles list and the KWin Rules list from the current config.
        Does NOT change any selection; callers are responsible for that.
        """
        self.populate_profiles()
        self.populate_system_rules()

    def _clear_tile_selection_and_editor(self) -> None:
        """
        Clear the tile list and the tile editor, and reset the current tile index.
        Used when the current profile is deleted or when config is fully restored.
        """
        self.current_tile_index = None
        self.tile_list.clear()
        self.tile_editor.clear()

    def _save_config_with_error(self, action_description: str) -> bool:
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
        self._reload_profiles_and_rules()
        self._clear_tile_selection_and_editor()
        self.canvas.set_profile(None)

        # Optional: select first profile again if exists
        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(0)

    def _push_undo_state(self) -> None:
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

                # Reset monitor combo to "Primary (default)" if it exists
                if hasattr(self, "monitor_combo"):
                    self.monitor_combo.setCurrentIndex(0)

                self.canvas.set_profile(None)
                # Also clear layout combo
                self.refresh_layout_combo()
                return

            # Gap
            gap = int(profile.tile_gap)
            self.tile_gap_spin.blockSignals(True)
            self.tile_gap_spin.setValue(gap)
            self.tile_gap_spin.blockSignals(False)

            # Monitor selection
            if hasattr(self, "monitor_combo"):
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
        if not hasattr(self, "layout_combo"):
            return

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

        # Get layout names from the model
        try:
            names = profile.layout_names
        except Exception:
            names = ["Default"]

        if not names:
            names = ["Default"]

        # Find current layout name
        try:
            current_name = profile.current_layout_name
        except Exception:
            current_name = names[0]

        current_index = 0
        for i, name in enumerate(names):
            self.layout_combo.addItem(name, userData=name)
            if name == current_name:
                current_index = i

        self.layout_combo.setCurrentIndex(current_index)
        self.layout_combo.setEnabled(True)

        # Enable/disable layout actions
        has_any_layouts = len(names) > 0
        has_slots = bool(profile.layout_slots)

        self.btn_edit_layout.setEnabled(True)
        self.btn_new_layout.setEnabled(True)
        self.btn_rename_layout.setEnabled(has_any_layouts)
        self.btn_save_layout.setEnabled(True)
        self.btn_load_layout.setEnabled(has_slots)
        self.btn_delete_layout.setEnabled(has_slots)

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

        try:
            profile.current_layout_name = name
            self.engine.save_config(self.config)
        except Exception:
            # Non-fatal; just ignore
            pass

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

        # Suggest a name like "Layout 1", "Layout 2", ...
        try:
            existing = set(profile.layout_names)
        except Exception:
            existing = set()

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
            if hasattr(profile, "create_empty_layout"):
                profile.create_empty_layout(name)
            else:
                # Fallback: just use layout_slots property
                if hasattr(profile, "current_layout_name"):
                    profile.current_layout_name = name
                profile.layout_slots = []
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to create layout: {e}")
            return

        # Persist + refresh UI
        try:
            self.engine.save_config(self.config)
        except Exception:
            pass

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
        try:
            current_name = profile.current_layout_name
        except Exception:
            current_name = ""

        if not current_name:
            QMessageBox.information(
                self,
                "No layout",
                "There is no active layout to rename.",
            )
            return

        # Existing layout names, to avoid duplicates
        try:
            existing_names = set(profile.layout_names)
        except Exception:
            existing_names = set()

        # Ask user for new name
        new_name, ok = self.simple_prompt(
            "Rename Layout",
            f"New name for layout '{current_name}':",
            default=current_name,
        )
        if not ok:
            return

        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid name", "Layout name cannot be empty.")
            return

        if new_name == current_name:
            # No change, silently ignore
            return

        if new_name in existing_names:
            QMessageBox.warning(
                self,
                "Duplicate name",
                f"A layout named '{new_name}' already exists for this monitor.",
            )
            return

        # Apply rename on the model
        self._push_undo_state()
        try:
            renamed = False
            if hasattr(profile, "rename_layout"):
                renamed = profile.rename_layout(current_name, new_name)
            if not renamed:
                QMessageBox.warning(
                    self,
                    "Rename failed",
                    "Could not rename this layout in the profile model.",
                )
                return
            self.engine.save_config(self.config)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to rename layout: {e}")
            return

        # Refresh UI (combo text, enabled states)
        self.refresh_layout_combo()

        QMessageBox.information(
            self,
            "Layout renamed",
            f"Layout '{current_name}' has been renamed to '{new_name}'.",
        )

    def _apply_tile_gap_delta(self, profile: ProfileModel, old_gap: int, new_gap: int) -> None:
        """
        Legacy helper for gap changes.
        Geometry is now recalculated by LayoutCanvas._push_geometry_into_tiles(),
        so this function is intentionally empty.
        """
        return

    # ----- Slots -----

    def on_profile_selected(self, current: QListWidgetItem, previous: Optional[QListWidgetItem]) -> None:
        self.flush_tile_edits()

        if not current:
            self.current_profile_index = None
            self._clear_tile_selection_and_editor()
            self.load_profile_settings_to_ui(None)

            # Keep combo in sync
            if hasattr(self, "profile_combo"):
                self.profile_combo.blockSignals(True)
                self.profile_combo.setCurrentIndex(-1)
                self.profile_combo.blockSignals(False)
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

        # Sync top-bar combo with the new index
        if hasattr(self, "profile_combo") and 0 <= profile_index < self.profile_combo.count():
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(profile_index)
            self.profile_combo.blockSignals(False)

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

    def on_tile_selected(self, current: QListWidgetItem, previous: Optional[QListWidgetItem]) -> None:
        """
        Called when the tile selection in the list changes.
        Flushes edits into the previously selected tile, then loads the new one.
        """
        # 1) Flush edits for the previously selected tile, if any
        if previous is not None:
            self.flush_tile_edits(previous)

        # 2) Clear UI if nothing is selected now
        if not current:
            self.current_tile_index = None
            self.tile_editor.clear()
            self.canvas.set_selected_index(None)
            return

        profile = self.get_current_profile()
        if not profile:
            return

        tile = self._get_tile_from_item(current)
        if not tile:
            return

        # Keep index in sync for other code that still uses it
        row = self.tile_list.row(current)
        self.current_tile_index = row

        # Load selected tile into editor and canvas
        self.tile_editor.load_tile(profile, tile)
        self.canvas.set_selected_index(row)

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

    def on_profile_settings_changed(self, *args) -> None:
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
            if hasattr(self.canvas, "_push_geometry_into_tiles"):
                self.canvas._push_geometry_into_tiles()
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

        if not hasattr(self.canvas, "export_slots_for_profile"):
            QMessageBox.critical(self, "Error", "Canvas does not support layout export.")
            return

        slots = self.canvas.export_slots_for_profile()
        profile.layout_slots = slots

        # Let the canvas apply the current gap setting to all tiles.
        # This uses the split tree + profile.tile_gap to compute
        # the final geometry, so you get:
        # - 'gap' pixels between tiles
        # - 'gap' pixels between tiles and screen edges.
        if hasattr(self.canvas, "_push_geometry_into_tiles"):
            self.canvas._push_geometry_into_tiles()

        # Canvas already reflects the current layout; just refresh tile editor
        if self.current_tile_index is not None:
            tile = self.get_current_tile()
            if tile is not None:
                self.tile_editor.load_tile(profile, tile)

        # Persist to disk
        try:
            self.engine.save_config(self.config)
        except Exception as e:
            QMessageBox.warning(self, "Save error", f"Failed to save layout: {e}")
            return

        # Update layout buttons (load/delete now valid if previously empty)
        self.refresh_layout_combo()

        try:
            layout_name = profile.current_layout_name
        except Exception:
            layout_name = ""
        if layout_name:
            msg = f"Layout '{layout_name}' and tile geometry have been saved."
        else:
            msg = "Layout and tile geometry have been saved."

        QMessageBox.information(self, "Layout saved", msg)

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
        if not profile.layout_slots:
            QMessageBox.information(
                self,
                "No layout",
                "This layout has no saved geometry to delete.",
            )
            return

        try:
            layout_name = profile.current_layout_name
        except Exception:
            layout_name = ""

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
            if hasattr(profile, "delete_layout_by_name") and layout_name:
                profile.delete_layout_by_name(layout_name)
            else:
                # Fallback: clear slots of current layout
                profile.layout_slots = []
            self.engine.save_config(self.config)
        except Exception as e:
            QMessageBox.warning(self, "Save error", f"Failed to delete layout: {e}")
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

    def on_canvas_tile_selected(self, idx: int) -> None:
        """Canvas clicked a tile -> select same row in list."""
        if 0 <= idx < self.tile_list.count():
            self.tile_list.setCurrentRow(idx)

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

    def flush_tile_edits(self, item: Optional[QListWidgetItem] = None) -> None:
        """
        Push the editor's current values into the TileModel for the given item.
        If no item is passed, it uses the currently selected item.
        """
        profile = self.get_current_profile()
        if not profile:
            return

        if item is None:
            item = self.tile_list.currentItem()
        if not item:
            return

        tile = self._get_tile_from_item(item)
        if not tile:
            return

        # Apply changes into that tile
        self.tile_editor.current_profile = profile
        self.tile_editor.current_tile = tile
        self.tile_editor.apply_changes()

        # Refresh canvas (no more _recompute_rects in the new LayoutCanvas)
        self.canvas.update()

        # Update list label
        item.setText(tile.name or "<tile>")

    def on_new_profile(self) -> None:
        self._push_undo_state()

        name, ok = self.simple_prompt("New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        self.config.add_profile(name.strip())
        self.populate_profiles()
        new_index = len(self.get_profiles()) - 1
        self.profile_list.setCurrentRow(new_index)

    def on_rename_profile(self) -> None:
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile to rename.")
            return

        old_name = profile.name or "<unnamed>"

        new_name, ok = self.simple_prompt("Rename Profile", "New profile name:", default=old_name)
        if not ok:
            return

        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid name", "Profile name cannot be empty.")
            return

        # Check for duplicate names
        for p in self.get_profiles():
            if p is profile:
                continue
            if p.name == new_name:
                QMessageBox.warning(
                    self,
                    "Duplicate name",
                    f"Another profile is already called '{new_name}'.",
                )
                return

        self._push_undo_state()
        profile.name = new_name

        # Update the list item text
        current_item = self.profile_list.currentItem()
        if current_item is not None:
            current_item.setText(new_name)

        # Persist + refresh rule list UI
        self.engine.save_config(self.config)
        self.populate_system_rules()

    def on_new_tile(self) -> None:
        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile first.")
            return

        self._push_undo_state()

        profile.add_tile()
        self.populate_tiles(self.current_profile_index)

        new_tile_index = len(profile.tiles) - 1
        self.current_tile_index = new_tile_index

        if new_tile_index >= 0:
            self.tile_list.setCurrentRow(new_tile_index)
            self.canvas.set_profile(profile)

    def on_save_config(self) -> None:
        """
        Save the current configuration to disk.
        If there is a current profile, validate it first.
        (You could also validate all profiles here later.)
        """
        self.flush_tile_edits()

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
        """
        self.flush_tile_edits()

        profile = self.validate_current_profile("apply this profile")
        if not profile:
            return  # validation failed or no profile selected

        try:
            self.engine.apply_profile_rules(self.config, profile)
            QMessageBox.information(self, "Applied", f"KWin rules for '{profile.name}' refreshed.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply profile:\n{e}")

    def on_launch_apps(self) -> None:
        """
        Validate current profile, then launch only that profile's commands.
        """
        self.flush_tile_edits()

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

    def on_delete_profile(self) -> None:
        self._push_undo_state()

        profile = self.get_current_profile()
        if not profile:
            QMessageBox.warning(self, "No profile", "Select a profile to delete.")
            return

        name = profile.name or "<unnamed>"
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{name}' and its KWin Window Rules?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Try to remove profile rules first
        try:
            self.engine.remove_profile_rules(profile)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Warning",
                f"Failed to remove KWin rules for '{name}':\n{e}\n"
                "The profile will still be removed from the config.",
            )

        if self.current_profile_index is not None:
            self.config.remove_profile(self.current_profile_index)

        self.current_profile_index = None
        self.current_tile_index = None

        if not self._save_config_with_error("save config after deleting profile"):
            return

        # Refresh lists and clear the now-invalid tile selection/editor
        self._reload_profiles_and_rules()
        self._clear_tile_selection_and_editor()
        self.load_profile_settings_to_ui(None)

        QMessageBox.information(self, "Deleted", f"Profile '{name}' deleted.")

    def on_delete_tile(self) -> None:
        profile = self.get_current_profile()
        if not profile:
            return

        current_item = self.tile_list.currentItem()
        if not current_item:
            return

        tile = self._get_tile_from_item(current_item)
        if not tile:
            return

        tiles = profile.tiles
        try:
            idx = tiles.index(tile)
        except ValueError:
            # Fallback: row-based deletion
            idx = self.tile_list.currentRow()

        if not (0 <= idx < len(tiles)):
            return

        # 🔸 Take snapshot BEFORE actually deleting anything
        self._push_undo_state()

        reply = QMessageBox.question(
            self,
            "Delete Tile",
            f"Delete tile '{tile.name or '<tile>'}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            # Optional: if you want to avoid a useless undo entry when user cancels:
            # if self.undo_stack:
            #     self.undo_stack.pop()
            # self._update_undo_redo_buttons()
            return

        # Remove from profile by the *real* index of that TileModel
        profile.remove_tile(idx)

        # Update selection index
        if len(profile.tiles) == 0:
            self.current_tile_index = None
        else:
            if idx >= len(profile.tiles):
                idx = len(profile.tiles) - 1
            self.current_tile_index = idx

        # Refresh UI
        self.populate_tiles(self.current_profile_index)
        self.canvas.set_profile(profile)

        if self.current_tile_index is not None:
            self.tile_list.setCurrentRow(self.current_tile_index)
            new_tile = profile.tiles[self.current_tile_index]
            self.tile_editor.load_tile(profile, new_tile)
        else:
            self.tile_editor.clear()

        # Persist config (best-effort)
        try:
            self.engine.save_config(self.config)
        except Exception:
            pass

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
        target_idx = None
        target_profile = None

        for i, p in enumerate(profiles):
            if p.name == profile_name:
                target_idx = i
                target_profile = p
                break

            logger.error(
                "[Onigiri Autostart] Profile '%s' not found in config.",
                profile_name,
            )
            return

        # Keep internal UI state consistent
        self.current_profile_index = target_idx
        if 0 <= target_idx < self.profile_list.count():
            self.profile_list.setCurrentRow(target_idx)

        # Apply KWin rules for that profile
        try:
            self.engine.apply_profile_rules(self.config, target_profile)
        except Exception as e:
            logger.error(
                "[Onigiri Autostart] Failed to apply profile '%s': %s",
                profile_name,
                e,
            )

        # Launch the apps/commands for that profile
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
        except Exception:
            pass

        config_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.ConfigLocation
        )
        autostart_dir = os.path.join(config_dir, "autostart")
        os.makedirs(autostart_dir, exist_ok=True)

        desktop_path = os.path.join(autostart_dir, "onigiri.desktop")

        exe = sys.executable
        script_path = os.path.abspath(__file__)

        # 👇 IMPORTANT: pass --autostart-profile to onigiri_ui.py
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
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to create autostart file:\n{e}",
            )

    def on_create_autostart(self) -> None:
        self.flush_tile_edits()
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

class ProfilesDialog(QDialog):
    def __init__(self, main: "MainWindow"):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Profiles")
        self.setModal(False)
        self.resize(500, 400)

        layout = QVBoxLayout(self)

        group = QGroupBox("Profiles")
        g_layout = QVBoxLayout()
        g_layout.addWidget(self.main.profile_list)

        buttons_row = QHBoxLayout()
        buttons_row.addWidget(self.main.btn_new_profile)
        buttons_row.addWidget(self.main.btn_rename_profile)
        buttons_row.addWidget(self.main.btn_delete_profile)
        buttons_row.addStretch(1)
        g_layout.addLayout(buttons_row)

        group.setLayout(g_layout)
        layout.addWidget(group)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)


class TilesDialog(QDialog):
    def __init__(self, main: "MainWindow"):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Tiles")
        self.setModal(False)
        self.resize(900, 600)

        layout = QVBoxLayout(self)

        # Left: tiles list + buttons
        tiles_group = QGroupBox("Tiles in Profile")
        tiles_group_layout = QVBoxLayout()
        tiles_group_layout.addWidget(self.main.tile_list)

        tile_buttons_row = QHBoxLayout()
        tile_buttons_row.addWidget(self.main.btn_new_tile)
        tile_buttons_row.addWidget(self.main.btn_delete_tile)
        tile_buttons_row.addStretch(1)
        tiles_group_layout.addLayout(tile_buttons_row)
        tiles_group.setLayout(tiles_group_layout)

        # Right: tile editor
        editor_group = QGroupBox("Tile Editor")
        editor_layout = QVBoxLayout()
        editor_layout.addWidget(self.main.tile_editor)
        editor_group.setLayout(editor_layout)

        # Combined row
        content_row = QHBoxLayout()
        content_row.addWidget(tiles_group, stretch=1)
        content_row.addWidget(editor_group, stretch=2)

        layout.addLayout(content_row)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)


class KWinRulesDialog(QDialog):
    def __init__(self, main: "MainWindow"):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("KWin Rules")
        self.setModal(False)
        self.resize(600, 500)

        layout = QVBoxLayout(self)

        rules_group = QGroupBox("KWin Rules (enabled/disabled)")
        rules_layout = QVBoxLayout()
        rules_layout.addWidget(self.main.rules_list)

        rule_buttons_row = QHBoxLayout()
        rule_buttons_row.addWidget(self.main.btn_delete_rule)
        rule_buttons_row.addStretch(1)
        rules_layout.addLayout(rule_buttons_row)

        rules_group.setLayout(rules_layout)
        layout.addWidget(rules_group)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

if __name__ == "__main__":
    raise SystemExit(main())
