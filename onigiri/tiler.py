"""Placements, KWin rules, and spawning tiles / overlays."""

from __future__ import annotations

from onigiri.geometry import Rect, compute_layout
from onigiri.kwin import (
    DESCRIPTION_PREFIX,
    OVERLAY_PREFIX,
    KWinRules,
    Placement,
    close_onigiri_windows,
    match_keys,
    place_windows,
)
from onigiri.launch import (
    activate_app,
    duplicate_classes,
    process_names,
    resolve_tile,
    spawn,
)
from onigiri.model import (
    Leaf,
    Profile,
    ScreenLayout,
    Tile,
    combine_leaf,
    delete_tile as clear_leaf,
    fill_slot,
    find_leaf,
    is_vacant_root,
    iter_tiles,
    new_id,
    prune_empty_root,
    split_leaf,
)
from onigiri.session import Session
from onigiri.ui.popup_tiler import release_overlay_tree
from onigiri.ui.screens import screen_list


class Tiler:
    def __init__(
        self,
        session: Session,
        kwin: KWinRules,
        *,
        close_windows_fn=None,
        place_windows_fn=None,
        running_names_fn=None,
        spawn_fn=None,
        activate_fn=None,
        screens_fn=None,
        reconfigure_later=None,
        place_later=None,
    ) -> None:
        self.session = session
        self.kwin = kwin
        self._close_windows = close_windows_fn or close_onigiri_windows
        self._place_windows = place_windows_fn or place_windows
        self._running_names = running_names_fn or process_names
        self._spawn = spawn_fn or spawn
        self._activate = activate_fn or activate_app
        self._screens = screens_fn or screen_list
        self._reconfigure_later = reconfigure_later
        self._place_later = place_later

    def tiles_for_launch(self, profile: Profile | None = None) -> list[Tile]:
        tiles: list[Tile] = []
        for state in self.session.layout_screens(profile).values():
            if state.root is not None:
                tiles.extend(iter_tiles(state.root))
        return tiles

    def tiles_by_id(self) -> dict[str, Tile]:
        found: dict[str, Tile] = {}
        for state in self.session.layout_screens().values():
            if state.root is not None:
                for tile in iter_tiles(state.root):
                    found[tile.id] = tile
        return found

    def duplicate_set(self) -> set[str]:
        profile = self.session.current_profile()
        if profile is None:
            return set()
        return duplicate_classes(self.tiles_for_launch(profile), profile.name)

    def collect_placements(self) -> list[Placement]:
        profile = self.session.current_profile()
        if profile is None:
            return []
        layout = profile.current_layout()
        screens = {screen.name: screen for screen in self._screens() if screen.connected}
        names = list(dict.fromkeys([*screens.keys(), *layout.screens.keys()]))
        safe_name = " ".join(profile.name.split())
        placements: list[Placement] = []
        for name in names:
            placements.extend(
                self.placements_for(profile, name, layout.screens.get(name), screens, safe_name, DESCRIPTION_PREFIX)
            )
        return placements

    def collect_overlay_placements(self) -> list[Placement]:
        profile = self.session.current_profile()
        if profile is None:
            return []
        screens = {screen.name: screen for screen in self._screens() if screen.connected}
        safe_name = " ".join(profile.name.split())
        placements: list[Placement] = []
        for name, state in profile.overlays.items():
            placements.extend(
                self.placements_for(
                    profile,
                    name,
                    state,
                    screens,
                    safe_name,
                    OVERLAY_PREFIX,
                    steal_chrome=False,
                    overlay=True,
                )
            )
        return placements

    def placements_for(
        self,
        profile,
        name: str,
        state,
        screens: dict,
        safe_name: str,
        prefix: str,
        steal_chrome: bool = True,
        overlay: bool = False,
    ) -> list[Placement]:
        if state is None or is_vacant_root(state.root):
            return []
        if name in screens:
            screen = screens[name]
            area = Rect(screen.x, screen.y, screen.width, screen.height)
            state.x = screen.x
            state.y = screen.y
            state.width = screen.width
            state.height = screen.height
        else:
            area = Rect(state.x, state.y, max(1, state.width), max(1, state.height))
        geometry = compute_layout(state.root, area, state.gap)
        placements: list[Placement] = []
        for tile, rect in geometry.tiles:
            resolved = resolve_tile(tile, profile.name, overlay=overlay)
            placements.append(
                Placement(
                    tile_id=tile.id,
                    description=f"{prefix}{safe_name}:{tile.id}",
                    x=rect.x,
                    y=rect.y,
                    width=rect.width,
                    height=rect.height,
                    match_kind=resolved.match_kind,
                    match_value=resolved.match_value.replace("\n", " "),
                    no_border=tile.no_border if steal_chrome else False,
                    skip_taskbar=tile.skip_taskbar if steal_chrome else False,
                    command=resolved.command,
                    use_shell=resolved.use_shell,
                    app_id=getattr(tile, "app_id", "") or "",
                )
            )
        return placements

    def apply_rules(self) -> int:
        profile = self.session.current_profile()
        if profile is None:
            return 0
        return self.kwin.replace_onigiri_rules(self.collect_placements())

    def apply_overlay_rules(self) -> int:
        if self.session.current_profile() is None:
            return 0
        return self.kwin.replace_onigiri_rules(self.collect_overlay_placements(), prefix=OVERLAY_PREFIX)

    def launch_tiles(self, only_tile: str | None = None, only_tiles: list[str] | None = None) -> tuple[int, str]:
        if self.session.current_profile() is None:
            return 0, ""
        self.apply_rules()
        wanted = set(only_tiles) if only_tiles else ({only_tile} if only_tile else None)
        launching = [
            placement
            for placement in self.collect_placements()
            if placement.command and (wanted is None or placement.tile_id in wanted)
        ]
        return self.spawn_placements(launching)

    def launch_overlay_tiles(self, only_tiles: list[str] | None = None) -> tuple[int, int, str]:
        if self.session.current_profile() is None:
            return 0, 0, ""
        wanted = set(only_tiles) if only_tiles else None
        launching = [
            placement
            for placement in self.collect_overlay_placements()
            if placement.command and (wanted is None or placement.tile_id in wanted)
        ]
        launched, moved, error = self.place_or_spawn(launching)
        self.apply_overlay_rules()
        return launched, moved, error

    def spawn_placements(self, launching: list[Placement]) -> tuple[int, str]:
        self._close_windows({placement.match_value for placement in launching if placement.match_value})
        launched = 0
        first_error = ""
        for placement in launching:
            try:
                self._spawn(placement.command, placement.use_shell)
            except (OSError, ValueError) as exc:
                first_error = first_error or str(exc)
                continue
            launched += 1
        pending = list(launching)
        if self._reconfigure_later is not None:
            self._reconfigure_later(400, self.kwin._reconfigure)
        if any(placement.match_kind == "title" for placement in pending) and self._place_later is not None:
            self._place_later(500, pending)
            self._place_later(1600, pending)
        return launched, first_error

    def place_or_spawn(self, launching: list[Placement]) -> tuple[int, int, str]:
        placed = self._place_windows(launching)
        found = {
            str(value).casefold()
            for value in (placed if isinstance(placed, (list, tuple, set)) else [])
            if value
        }
        launched = 0
        moved = 0
        first_error = ""
        for placement in launching:
            if placement.match_value and placement.match_value.casefold() in found:
                moved += 1
                continue
            try:
                self._activate(placement.command, placement.use_shell, placement.app_id)
            except (OSError, ValueError) as exc:
                first_error = first_error or str(exc)
                continue
            launched += 1
        pending = list(launching)
        if self._reconfigure_later is not None:
            self._reconfigure_later(400, self.kwin._reconfigure)
        if self._place_later is not None:
            self._place_later(500, pending)
            self._place_later(1600, pending)
        return launched, moved, first_error

    def close_assigned(self, tile, *, overlay: bool = False) -> None:
        profile = self.session.current_profile()
        if profile is None or tile is None:
            return
        resolved = resolve_tile(tile, profile.name, overlay=overlay)
        keys = match_keys(resolved.match_value, getattr(tile, "app_id", "") or "", resolved.command)
        if keys:
            self._close_windows(set(keys))

    def store_overlay(self, connector: str, layout) -> None:
        profile = self.session.current_profile()
        if profile is None or not connector:
            return
        if layout is None or getattr(layout, "root", None) is None:
            profile.overlays.pop(connector, None)
        else:
            release_overlay_tree(layout.root)
            profile.overlays[connector] = layout
        self.session.save()
        self.apply_overlay_rules()

    def overlay_closed(self, connector: str) -> bool:
        """Return True if overlay tiles should be launched."""
        profile = self.session.current_profile()
        if profile is None or not connector:
            self.apply_overlay_rules()
            return False
        overlay = profile.overlays.get(connector)
        if overlay is None or overlay.root is None:
            self.apply_overlay_rules()
            return False
        release_overlay_tree(overlay.root)
        self.session.save()
        return True

    def add_first_tile(self, monitor: str) -> str | None:
        profile = self.session.current_profile()
        if profile is None or not monitor:
            return None
        existing = profile.screen_layout(monitor)
        if existing is not None and existing.root is not None:
            return None
        self.session.push_undo()
        screen = profile.ensure_screen(monitor)
        tile = Tile(id=new_id(), name="Tile 1")
        screen.root = Leaf(tile)
        self.session.monitor_name = monitor
        self.session.selected_ids = [tile.id]
        self.session.remember_geometry()
        self.session.save()
        return tile.id

    def remove_tile(self, tile_id: str) -> bool:
        screen = self.session.current_screen()
        if screen is None or screen.root is None or not tile_id:
            return False
        leaf = find_leaf(screen.root, tile_id)
        tile = None if leaf is None else leaf.tile
        self.session.push_undo()
        if not clear_leaf(screen.root, tile_id):
            self.session.drop_last_undo()
            return False
        self.close_assigned(tile)
        screen.root = prune_empty_root(screen.root)
        self.session.selected_ids = [item for item in self.session.selected_ids if item != tile_id]
        self.session.save()
        return True

    def fill_empty_slot(self, monitor: str, slot: str) -> str | None:
        profile = self.session.current_profile()
        if profile is None:
            return None
        screen = profile.screen_layout(monitor)
        if screen is None or screen.root is None:
            return None
        self.session.push_undo()
        tile = fill_slot(screen.root, slot)
        if tile is None:
            self.session.drop_last_undo()
            return None
        self.session.monitor_name = monitor
        self.session.selected_ids = [tile.id]
        self.session.save()
        return tile.id

    def clear_screen(self, monitor: str) -> bool:
        profile = self.session.current_profile()
        if profile is None:
            return False
        screen = profile.screen_layout(monitor)
        if screen is None or not isinstance(screen.root, Leaf):
            return False
        self.session.push_undo()
        screen.root = None
        if monitor == self.session.monitor_name:
            self.session.selected_ids = []
        self.session.save()
        return True

    def split(self, tile_id: str, orientation: str) -> str | None:
        screen = self.session.current_screen()
        if screen is None or not tile_id:
            return None
        self.session.push_undo()
        result = split_leaf(screen.root, tile_id, orientation)
        if result is None:
            self.session.drop_last_undo()
            return None
        screen.root, selected = result
        self.session.selected_ids = [selected]
        self.session._coalesce = None
        self.session.save()
        return selected

    def combine(self, tile_id: str) -> bool:
        screen = self.session.current_screen()
        if screen is None:
            return False
        self.session.push_undo()
        new_root = combine_leaf(screen.root, tile_id)
        if new_root is None:
            self.session.drop_last_undo()
            return False
        screen.root = new_root
        self.session.selected_ids = [tile_id]
        self.session._coalesce = None
        self.session.save()
        return True

    def set_gap(self, value: int) -> bool:
        screen = self.session.current_screen()
        if screen is None or screen.gap == value:
            return False
        self.session.push_undo("gap")
        screen.gap = int(value)
        self.session.save()
        return True
