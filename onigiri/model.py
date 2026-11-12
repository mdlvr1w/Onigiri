"""Layout profiles: a split tree of tiles for each monitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator
from uuid import uuid4


def new_id() -> str:
    return uuid4().hex[:8]


Rgb = tuple[int, int, int]
_DEFAULT_BLUE: Rgb = (0, 123, 255)
_DEFAULT_PURPLE: Rgb = (212, 0, 255)


def pan_pair(value) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            x_pos, y_pos = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return (0.5, 0.5)
        return (min(1.0, max(0.0, x_pos)), min(1.0, max(0.0, y_pos)))
    return (0.5, 0.5)


def parse_wallpaper_pan(value) -> dict[str, tuple[float, float]]:
    if not isinstance(value, dict):
        return {}
    return {str(name): pan_pair(raw) for name, raw in value.items() if str(name).strip()}


def parse_rgb(value, default: Rgb) -> Rgb:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return default
    try:
        rgb = tuple(int(channel) for channel in value)
    except (TypeError, ValueError):
        return default
    if any(channel < 0 or channel > 255 for channel in rgb):
        return default
    return rgb  # type: ignore[return-value]


def parse_named_colors(value) -> dict[str, Rgb]:
    if not isinstance(value, dict):
        return {}
    found: dict[str, Rgb] = {}
    for name, raw in value.items():
        key = str(name).strip()
        if not key:
            continue
        parsed = parse_rgb(raw, (-1, -1, -1))
        if parsed == (-1, -1, -1):
            continue
        found[key] = parsed
    return found


def parse_plugin_values(value) -> dict:
    if not isinstance(value, dict):
        return {}
    found: dict = {}
    for name, raw in value.items():
        key = str(name).strip()
        if not key:
            continue
        if isinstance(raw, bool):
            found[key] = raw
            continue
        parsed = parse_rgb(raw, (-1, -1, -1))
        if parsed != (-1, -1, -1):
            found[key] = parsed
            continue
        if isinstance(raw, (str, int, float)):
            found[key] = raw
    return found


def parse_plugins(value) -> dict[str, dict] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {}
    parsed = {str(stem): parse_plugin_values(raw) for stem, raw in value.items() if str(stem).strip()}
    return migrate_plugin_stems(parsed)


def migrate_plugin_stems(plugins: dict[str, dict]) -> dict[str, dict]:
    """Rename stems from the shared dual-color / two Lian Li plugins."""

    migrated = dict(plugins)
    dual = migrated.pop("dual_color", None)
    if dual is not None:
        for stem in ("gpu", "ram", "ryuo"):
            migrated.setdefault(stem, dict(dual))
    leds = migrated.pop("lianli_fan_leds", None)
    displays = migrated.pop("lianli_fan_displays", None)
    if leds is not None or displays is not None:
        merged = dict(migrated.get("lianli_fans") or {})
        if leds:
            if "blue" in leds:
                merged.setdefault("led_blue", leds["blue"])
            if "purple" in leds:
                merged.setdefault("led_purple", leds["purple"])
        if displays:
            if "blue" in displays:
                merged.setdefault("text", displays["blue"])
            if "purple" in displays:
                merged.setdefault("gauge", displays["purple"])
        migrated["lianli_fans"] = merged
    return migrated


def aliases_from_plugins(plugins: dict[str, dict] | None) -> tuple[Rgb, Rgb, Rgb | None, Rgb | None]:
    """First selected plugin that has blue/purple (and hardware_*) wins."""

    blue: Rgb | None = None
    purple: Rgb | None = None
    hardware_blue: Rgb | None = None
    hardware_purple: Rgb | None = None
    if plugins:
        for values in plugins.values():
            if blue is None:
                parsed = parse_rgb(values.get("blue"), (-1, -1, -1))
                if parsed != (-1, -1, -1):
                    blue = parsed
            if purple is None:
                parsed = parse_rgb(values.get("purple"), (-1, -1, -1))
                if parsed != (-1, -1, -1):
                    purple = parsed
            if hardware_blue is None:
                parsed = parse_rgb(values.get("hardware_blue"), (-1, -1, -1))
                if parsed != (-1, -1, -1):
                    hardware_blue = parsed
            if hardware_purple is None:
                parsed = parse_rgb(values.get("hardware_purple"), (-1, -1, -1))
                if parsed != (-1, -1, -1):
                    hardware_purple = parsed
    return (
        blue if blue is not None else _DEFAULT_BLUE,
        purple if purple is not None else _DEFAULT_PURPLE,
        hardware_blue,
        hardware_purple,
    )


def _plugin_json(value):
    if isinstance(value, tuple) and len(value) == 3:
        return list(value)
    return value


@dataclass
class Scene:
    id: str
    name: str
    wallpaper: str = ""
    blue: Rgb = _DEFAULT_BLUE
    purple: Rgb = _DEFAULT_PURPLE
    hardware_blue: Rgb | None = None
    hardware_purple: Rgb | None = None
    wallpaper_pan: dict[str, tuple[float, float]] = field(default_factory=dict)
    colors: dict[str, Rgb] = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    script: str = ""
    plugins: dict[str, dict] | None = None
    background_color: Rgb | None = None

    def case_blue(self) -> Rgb:
        return self.blue if self.hardware_blue is None else self.hardware_blue

    def case_purple(self) -> Rgb:
        return self.purple if self.hardware_purple is None else self.hardware_purple

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "wallpaper": self.wallpaper,
            "blue": list(self.blue),
            "purple": list(self.purple),
            "hardware_blue": list(self.case_blue()),
            "hardware_purple": list(self.case_purple()),
            "wallpaper_pan": {name: [x_pos, y_pos] for name, (x_pos, y_pos) in self.wallpaper_pan.items()},
        }
        if self.background_color is not None:
            data["background_color"] = list(self.background_color)
        if self.colors:
            data["colors"] = {name: list(rgb) for name, rgb in self.colors.items()}
        if self.extras:
            data["extras"] = self.extras
        if self.script:
            data["script"] = self.script
        if self.plugins is not None:
            data["plugins"] = {
                stem: {key: _plugin_json(raw) for key, raw in values.items()}
                for stem, values in self.plugins.items()
            }
        return data

    @staticmethod
    def from_dict(data: dict) -> Scene:
        source = data if isinstance(data, dict) else {}
        named = parse_named_colors(source.get("colors"))
        blue = parse_rgb(source.get("blue"), named.get("blue", _DEFAULT_BLUE))
        purple = parse_rgb(source.get("purple"), named.get("purple", _DEFAULT_PURPLE))
        has_hw = "hardware_blue" in source or "hardware_purple" in source
        extras = source.get("extras") if isinstance(source.get("extras"), dict) else {}
        plugins = parse_plugins(source["plugins"]) if "plugins" in source else None
        if plugins:
            alias_blue, alias_purple, alias_hw_blue, alias_hw_purple = aliases_from_plugins(plugins)
            if "blue" not in source:
                blue = alias_blue
            if "purple" not in source:
                purple = alias_purple
            if not has_hw:
                hardware_blue = alias_hw_blue
                hardware_purple = alias_hw_purple
            else:
                hardware_blue = parse_rgb(source.get("hardware_blue"), blue)
                hardware_purple = parse_rgb(source.get("hardware_purple"), purple)
        else:
            hardware_blue = parse_rgb(source.get("hardware_blue"), blue) if has_hw else None
            hardware_purple = parse_rgb(source.get("hardware_purple"), purple) if has_hw else None
        background_color = parse_rgb(source.get("background_color"), (-1, -1, -1))
        return Scene(
            id=str(source.get("id") or new_id()),
            name=str(source.get("name") or "Scene").strip() or "Scene",
            wallpaper=str(source.get("wallpaper") or ""),
            blue=blue,
            purple=purple,
            hardware_blue=hardware_blue,
            hardware_purple=hardware_purple,
            wallpaper_pan=parse_wallpaper_pan(source.get("wallpaper_pan")),
            colors=named,
            extras=dict(extras),
            script=str(source.get("script") or "").strip(),
            plugins=plugins,
            background_color=None if background_color == (-1, -1, -1) else background_color,
        )


@dataclass
class Tile:
    id: str
    name: str
    launch_mode: str = "raw"  # raw, helper, app
    command: str = ""
    shell_command: str = ""
    terminal: str = "alacritty"
    app_id: str = ""
    app_name: str = ""
    no_border: bool = False
    skip_taskbar: bool = False
    show_title: bool = True
    hide_toolbar: bool = False
    opacity: float = 1.0
    font_size: int = 0
    match_kind: str = ""  # class, title, or empty
    match_value: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "launch_mode": self.launch_mode,
            "command": self.command,
            "shell_command": self.shell_command,
            "terminal": self.terminal,
            "app_id": self.app_id,
            "app_name": self.app_name,
            "no_border": self.no_border,
            "skip_taskbar": self.skip_taskbar,
            "show_title": self.show_title,
            "hide_toolbar": self.hide_toolbar,
            "opacity": self.opacity,
            "font_size": self.font_size,
            "match_kind": self.match_kind,
            "match_value": self.match_value,
        }

    @staticmethod
    def from_dict(data: dict) -> Tile:
        mode = str(data.get("launch_mode") or "raw")
        if mode not in {"raw", "helper", "app"}:
            mode = "raw"
        kind = str(data.get("match_kind") or "")
        if kind not in {"class", "title"}:
            kind = ""
        terminal = str(data.get("terminal") or "alacritty")
        try:
            opacity = float(data.get("opacity", 1.0))
        except (TypeError, ValueError):
            opacity = 1.0
        if opacity != opacity:
            opacity = 1.0
        try:
            font_size = int(data.get("font_size", 0) or 0)
        except (TypeError, ValueError):
            font_size = 0
        if font_size < 0:
            font_size = 0
        elif 0 < font_size < 6:
            font_size = 6
        return Tile(
            id=str(data.get("id") or new_id()),
            name=str(data.get("name") or "Tile"),
            launch_mode=mode,
            command=str(data.get("command") or ""),
            shell_command=str(data.get("shell_command") or ""),
            terminal=terminal,
            app_id=str(data.get("app_id") or ""),
            app_name=str(data.get("app_name") or ""),
            no_border=bool(data.get("no_border", False)),
            skip_taskbar=bool(data.get("skip_taskbar", False)),
            show_title=bool(data.get("show_title", True)),
            hide_toolbar=bool(data.get("hide_toolbar", False)),
            opacity=min(1.0, max(0.1, opacity)),
            font_size=min(48, font_size),
            match_kind=kind,
            match_value=str(data.get("match_value") or ""),
        )


@dataclass
class Leaf:
    tile: Tile | None = None
    slot: str = ""

    def __post_init__(self) -> None:
        if not self.slot:
            self.slot = self.tile.id if self.tile is not None else new_id()

    def to_dict(self) -> dict:
        return {"type": "leaf", "slot": self.slot, "tile": None if self.tile is None else self.tile.to_dict()}


@dataclass
class Split:
    orientation: str  # "v" side by side, "h" stacked
    ratio: float
    first: Node
    second: Node

    def to_dict(self) -> dict:
        return {
            "type": "split",
            "orientation": "h" if self.orientation == "h" else "v",
            "ratio": self.ratio,
            "first": self.first.to_dict(),
            "second": self.second.to_dict(),
        }


Node = Leaf | Split


def node_from_dict(data: dict) -> Node:
    if isinstance(data, dict) and data.get("type") == "leaf":
        raw = data.get("tile") if "tile" in data else None
        tile = Tile.from_dict(raw) if isinstance(raw, dict) else None
        slot = str(data.get("slot") or "") or (tile.id if tile is not None else new_id())
        return Leaf(tile, slot)
    if not isinstance(data, dict) or data.get("type") != "split":
        tile_data = data.get("tile", {}) if isinstance(data, dict) else {}
        tile = Tile.from_dict(tile_data if isinstance(tile_data, dict) else {})
        return Leaf(tile, tile.id)
    try:
        ratio = float(data.get("ratio", 0.5))
    except (TypeError, ValueError):
        ratio = 0.5
    if ratio != ratio:
        ratio = 0.5
    ratio = min(0.92, max(0.08, ratio))
    return Split(
        orientation="h" if data.get("orientation") == "h" else "v",
        ratio=ratio,
        first=node_from_dict(data.get("first") or {}),
        second=node_from_dict(data.get("second") or {}),
    )


def iter_tiles(node: Node | None) -> Iterator[Tile]:
    if node is None:
        return
    if isinstance(node, Leaf):
        if node.tile is not None:
            yield node.tile
        return
    yield from iter_tiles(node.first)
    yield from iter_tiles(node.second)


def find_leaf(node: Node | None, tile_id: str) -> Leaf | None:
    if node is None:
        return None
    if isinstance(node, Leaf):
        return node if node.tile is not None and node.tile.id == tile_id else None
    found = find_leaf(node.first, tile_id)
    if found is not None:
        return found
    return find_leaf(node.second, tile_id)


def _locate(node: Node, tile_id: str, parent: Split | None = None) -> tuple[Split | None, Leaf | None]:
    if isinstance(node, Leaf):
        if node.tile is not None and node.tile.id == tile_id:
            return parent, node
        return None, None
    found_parent, found_leaf = _locate(node.first, tile_id, node)
    if found_leaf is not None:
        return found_parent, found_leaf
    return _locate(node.second, tile_id, node)


def _replace_node(node: Node, target: Split, replacement: Node) -> Node:
    if node is target:
        return replacement
    if isinstance(node, Leaf):
        return node
    return Split(
        node.orientation,
        node.ratio,
        _replace_node(node.first, target, replacement),
        _replace_node(node.second, target, replacement),
    )


def _replace_tile(node: Node, tile_id: str, replacement: Node) -> Node:
    if isinstance(node, Leaf):
        if node.tile is not None and node.tile.id == tile_id:
            return replacement
        return node
    return Split(
        node.orientation,
        node.ratio,
        _replace_tile(node.first, tile_id, replacement),
        _replace_tile(node.second, tile_id, replacement),
    )


def next_tile_name(node: Node | None) -> str:
    names = {tile.name for tile in iter_tiles(node)}
    number = 1
    while f"Tile {number}" in names:
        number += 1
    return f"Tile {number}"


def split_leaf(root: Node | None, tile_id: str, orientation: str) -> tuple[Node, str] | None:
    if root is None:
        return None
    leaf = find_leaf(root, tile_id)
    if leaf is None:
        return None
    fresh = Tile(id=new_id(), name=next_tile_name(root))
    created = Split(
        orientation="h" if orientation == "h" else "v",
        ratio=0.5,
        first=Leaf(leaf.tile),
        second=Leaf(fresh),
    )
    return _replace_tile(root, tile_id, created), fresh.id


def delete_tile(root: Node | None, tile_id: str) -> bool:
    leaf = find_leaf(root, tile_id)
    if leaf is None or leaf.tile is None:
        return False
    leaf.tile = None
    return True


def find_slot(node: Node | None, slot: str) -> Leaf | None:
    if node is None:
        return None
    if isinstance(node, Leaf):
        return node if node.slot == slot else None
    found = find_slot(node.first, slot)
    if found is not None:
        return found
    return find_slot(node.second, slot)


def fill_slot(root: Node | None, slot: str) -> Tile | None:
    leaf = find_slot(root, slot)
    if leaf is None or leaf.tile is not None:
        return None
    leaf.tile = Tile(id=new_id(), name=next_tile_name(root))
    return leaf.tile


def can_combine(root: Node | None, tile_id: str) -> bool:
    if root is None:
        return False
    parent, _leaf = _locate(root, tile_id)
    if parent is None:
        return False
    return isinstance(parent.first, Leaf) and isinstance(parent.second, Leaf)


def combine_leaf(root: Node | None, tile_id: str) -> Node | None:
    if root is None:
        return None
    parent, leaf = _locate(root, tile_id)
    if parent is None or leaf is None:
        return None
    if not (isinstance(parent.first, Leaf) and isinstance(parent.second, Leaf)):
        return None
    return _replace_node(root, parent, leaf)


def _int_field(data: dict, key: str, default: int) -> int:
    try:
        return int(data.get(key, default) or 0)
    except (TypeError, ValueError):
        return default


@dataclass
class ScreenLayout:
    root: Node | None = None
    gap: int = 8
    x: int = 0
    y: int = 0
    width: int = 1920
    height: int = 1080

    def to_dict(self) -> dict:
        data: dict = {
            "gap": max(0, int(self.gap)),
            "x": self.x,
            "y": self.y,
            "width": max(1, self.width),
            "height": max(1, self.height),
        }
        if self.root is not None:
            data["root"] = self.root.to_dict()
        return data

    @staticmethod
    def from_dict(data: dict, default_gap: int = 8) -> ScreenLayout:
        source = data if isinstance(data, dict) else {}
        raw = source.get("root")
        root = node_from_dict(raw) if isinstance(raw, dict) and raw else None
        gap = _int_field(source, "gap", default_gap)
        return ScreenLayout(
            root=root,
            gap=max(0, gap),
            x=_int_field(source, "x", 0),
            y=_int_field(source, "y", 0),
            width=max(1, _int_field(source, "width", 1920)),
            height=max(1, _int_field(source, "height", 1080)),
        )


@dataclass
class Layout:
    name: str
    screens: dict[str, ScreenLayout] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"screens": {name: screen.to_dict() for name, screen in self.screens.items()}}

    @staticmethod
    def from_dict(name: str, data: dict, default_gap: int = 8) -> Layout:
        screens: dict[str, ScreenLayout] = {}
        raw = data.get("screens") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            for connector, screen in raw.items():
                if isinstance(screen, dict):
                    screens[str(connector)] = ScreenLayout.from_dict(screen, default_gap)
        elif isinstance(data, dict) and ("root" in data or not data):
            screens = {}
        return Layout(name=name, screens=screens)


def is_placeholder_tile(tile: Tile) -> bool:
    """A tile the app used to invent for a screen the user never edited."""
    return (
        tile.name == "Tile 1"
        and tile.launch_mode == "raw"
        and not tile.command.strip()
        and not tile.shell_command.strip()
        and not tile.app_id
        and not tile.match_kind
        and not tile.match_value
        and not tile.no_border
        and not tile.skip_taskbar
        and tile.show_title
        and not tile.hide_toolbar
        and tile.opacity == 1.0
        and tile.font_size == 0
    )


def is_vacant_root(root: Node | None) -> bool:
    return root is None or (isinstance(root, Leaf) and root.tile is None)


def prune_empty_root(root: Node | None) -> Node | None:
    """Drop a split tree once every tile in it is gone."""

    if root is None or not any(iter_tiles(root)):
        return None
    return root


def is_placeholder_screen(screen: ScreenLayout) -> bool:
    if is_vacant_root(screen.root):
        return True
    return (
        isinstance(screen.root, Leaf)
        and screen.root.tile is not None
        and is_placeholder_tile(screen.root.tile)
    )


def drop_placeholder_screens(profile: Profile) -> bool:
    """Remove screens with no real tiles (empty root or an untouched Tile 1)."""
    removed = False
    for layout in profile.layouts.values():
        drop = [name for name, screen in layout.screens.items() if is_placeholder_screen(screen)]
        for name in drop:
            del layout.screens[name]
            removed = True
    return removed


def _profile_from_legacy_monitors(data: dict) -> Profile:
    gap = max(0, _int_field(data, "gap", 8))
    raw_monitors = data.get("monitors") if isinstance(data.get("monitors"), dict) else {}
    names: list[str] = []
    for state in raw_monitors.values():
        if not isinstance(state, dict):
            continue
        layouts_raw = state.get("layouts")
        if not isinstance(layouts_raw, dict):
            continue
        for layout_name in layouts_raw:
            cleaned = str(layout_name)
            if cleaned not in names:
                names.append(cleaned)
    if not names:
        names = ["Default"]
    layouts: dict[str, Layout] = {}
    for layout_name in names:
        screens: dict[str, ScreenLayout] = {}
        for connector, state in raw_monitors.items():
            if not isinstance(state, dict):
                continue
            layouts_raw = state.get("layouts") if isinstance(state.get("layouts"), dict) else {}
            if layout_name not in layouts_raw:
                continue
            layout_data = layouts_raw.get(layout_name)
            payload = layout_data if isinstance(layout_data, dict) else {}
            raw_root = payload.get("root")
            root = node_from_dict(raw_root) if isinstance(raw_root, dict) and raw_root else None
            screens[str(connector)] = ScreenLayout(
                root=root,
                gap=max(0, _int_field(payload, "gap", gap)),
                x=_int_field(state, "x", 0),
                y=_int_field(state, "y", 0),
                width=max(1, _int_field(state, "width", 1920)),
                height=max(1, _int_field(state, "height", 1080)),
            )
        layouts[layout_name] = Layout(name=layout_name, screens=screens)
    current = ""
    for state in raw_monitors.values():
        if isinstance(state, dict):
            candidate = str(state.get("current") or "")
            if candidate in layouts:
                current = candidate
                break
    if current not in layouts:
        current = next(iter(layouts))
    return Profile(name=str(data.get("name") or "Profile"), current=current, layouts=layouts)


@dataclass
class Profile:
    name: str
    current: str = "Default"
    layouts: dict[str, Layout] = field(default_factory=dict)
    overlays: dict[str, ScreenLayout] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.layouts:
            self.layouts["Default"] = Layout(name="Default")
            self.current = "Default"
        elif self.current not in self.layouts:
            self.current = next(iter(self.layouts))

    def current_layout(self) -> Layout:
        if self.current not in self.layouts:
            self.current = next(iter(self.layouts)) if self.layouts else "Default"
            if self.current not in self.layouts:
                self.layouts[self.current] = Layout(name=self.current)
        return self.layouts[self.current]

    def screen_layout(self, monitor: str) -> ScreenLayout | None:
        return self.current_layout().screens.get(monitor)

    def ensure_screen(self, monitor: str) -> ScreenLayout:
        layout = self.current_layout()
        if monitor not in layout.screens:
            layout.screens[monitor] = ScreenLayout()
        return layout.screens[monitor]

    def connector_names(self) -> list[str]:
        names: list[str] = []
        for layout in self.layouts.values():
            for name in layout.screens:
                if name not in names:
                    names.append(name)
        return names

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "current": self.current,
            "layouts": {name: layout.to_dict() for name, layout in self.layouts.items()},
        }
        if self.overlays:
            data["overlays"] = {name: screen.to_dict() for name, screen in self.overlays.items()}
        return data

    @staticmethod
    def from_dict(data: dict) -> Profile:
        if isinstance(data.get("layouts"), dict) and "monitors" not in data:
            default_gap = max(0, _int_field(data, "gap", 8))
            layouts: dict[str, Layout] = {}
            for name, payload in data["layouts"].items():
                if isinstance(payload, dict):
                    layouts[str(name)] = Layout.from_dict(str(name), payload, default_gap)
            current = str(data.get("current") or "")
            overlays: dict[str, ScreenLayout] = {}
            raw_overlays = data.get("overlays")
            if isinstance(raw_overlays, dict):
                for connector, payload in raw_overlays.items():
                    if isinstance(payload, dict):
                        overlays[str(connector)] = ScreenLayout.from_dict(payload)
            return Profile(
                name=str(data.get("name") or "Profile"),
                current=current,
                layouts=layouts,
                overlays=overlays,
            )
        return _profile_from_legacy_monitors(data)


def parse_scenes(value) -> list[Scene]:
    scenes: list[Scene] = []
    if not isinstance(value, list):
        return scenes
    for item in value:
        if isinstance(item, dict):
            scenes.append(Scene.from_dict(item))
    return scenes


@dataclass
class Playlist:
    interval: int = 5
    unit: str = "minutes"
    mode: str = "repeat"
    playing: bool = False

    def to_dict(self) -> dict:
        return {
            "interval": self.interval,
            "unit": self.unit,
            "mode": self.mode,
            "playing": self.playing,
        }

    @staticmethod
    def from_dict(data) -> Playlist:
        source = data if isinstance(data, dict) else {}
        try:
            interval = int(source.get("interval", 5))
        except (TypeError, ValueError):
            interval = 5
        unit = str(source.get("unit") or "minutes")
        if unit not in ("seconds", "minutes", "hours"):
            unit = "minutes"
        mode = str(source.get("mode") or "repeat")
        if mode not in ("repeat", "random"):
            mode = "repeat"
        return Playlist(
            interval=max(1, min(999, interval)),
            unit=unit,
            mode=mode,
            playing=bool(source.get("playing")),
        )


@dataclass
class Config:
    profiles: list[Profile] = field(default_factory=list)
    hotkey: str = ""
    scenes: list[Scene] = field(default_factory=list)
    playlist: Playlist = field(default_factory=Playlist)

    def to_dict(self) -> dict:
        data = {
            "version": 2,
            "hotkey": self.hotkey,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "playlist": self.playlist.to_dict(),
        }
        if self.scenes:
            data["scenes"] = [scene.to_dict() for scene in self.scenes]
        return data

    @staticmethod
    def from_dict(data: dict) -> Config:
        profiles: list[Profile] = []
        raw = data.get("profiles") if isinstance(data, dict) else None
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and ("layouts" in item or "monitors" in item):
                    profiles.append(Profile.from_dict(item))
        hotkey = str(data.get("hotkey") or "") if isinstance(data, dict) else ""
        scenes = parse_scenes(data.get("scenes") if isinstance(data, dict) else None)
        playlist = Playlist.from_dict(data.get("playlist") if isinstance(data, dict) else None)
        return Config(profiles=profiles, hotkey=hotkey, scenes=scenes, playlist=playlist)
