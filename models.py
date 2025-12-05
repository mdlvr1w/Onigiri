from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


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


class ConfigValidator:
    """
    Central place for validating tiles and profiles before saving/applying/launching.
    Returns lists of human-readable error messages.
    """

    # noinspection PyMethodMayBeStatic
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
