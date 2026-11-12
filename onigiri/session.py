"""Config, undo, and the current profile / layout / screen."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from onigiri.geometry import Rect
from onigiri.model import Config, Layout, Profile, ScreenLayout
from onigiri.store import default_config_path, load_config, save_config
from onigiri.ui.screens import screen_list

_UNDO_LIMIT = 50


class Session:
    def __init__(self, config_path: Path | None = None, *, screens_fn=None) -> None:
        self.config_path = config_path
        self._screens = screens_fn or screen_list
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self._coalesce: str | None = None
        self.profile_index = -1
        self.monitor_name = "default"
        self.selected_ids: list[str] = []
        self.load_error = ""
        try:
            self.config = load_config(config_path)
        except RuntimeError as exc:
            self.config = Config()
            self.load_error = str(exc)

    def screens(self):
        return self._screens()

    def current_profile(self) -> Profile | None:
        if 0 <= self.profile_index < len(self.config.profiles):
            return self.config.profiles[self.profile_index]
        return None

    def current_layout(self) -> Layout | None:
        profile = self.current_profile()
        if profile is None:
            return None
        return profile.current_layout()

    def current_screen(self) -> ScreenLayout | None:
        profile = self.current_profile()
        if profile is None:
            return None
        return profile.screen_layout(self.monitor_name)

    def layout_screens(self, profile: Profile | None = None) -> dict[str, ScreenLayout]:
        target = profile if profile is not None else self.current_profile()
        if target is None:
            return {}
        return target.current_layout().screens

    def current_area(self) -> Rect:
        profile = self.current_profile()
        screen_state = profile.screen_layout(self.monitor_name) if profile is not None else None
        for screen in self.screens():
            if screen.name == self.monitor_name and screen.connected:
                if screen_state is not None:
                    screen_state.x = screen.x
                    screen_state.y = screen.y
                    screen_state.width = screen.width
                    screen_state.height = screen.height
                return Rect(screen.x, screen.y, screen.width, screen.height)
        if screen_state is not None:
            return Rect(screen_state.x, screen_state.y, max(1, screen_state.width), max(1, screen_state.height))
        return Rect(0, 0, 1920, 1080)

    def remember_geometry(self) -> None:
        self.current_area()

    def save(self) -> None:
        if self.load_error:
            # Preserve the unreadable source before the first intentional edit.
            path = self.save_path()
            backup = path.with_name(f"{path.name}.recovery-{uuid4().hex}")
            with backup.open("xb") as stream:
                stream.write(path.read_bytes())
            self.load_error = ""
        save_config(self.config, self.config_path)

    def save_path(self) -> Path:
        return self.config_path or default_config_path()

    def ensure_initial_profile(self) -> None:
        if self.config.profiles:
            self.profile_index = 0
            return
        self.config.profiles.append(Profile(name="Default"))
        self.profile_index = 0
        if not self.load_error:
            self.save()

    def preferred_monitor(self) -> str:
        screens = [screen for screen in self.screens() if screen.connected]
        profile = self.current_profile()
        known = set(profile.connector_names()) if profile is not None else set()
        if known:
            for screen in screens:
                if screen.name in known:
                    return screen.name
        for screen in screens:
            if screen.primary:
                return screen.name
        if screens:
            return screens[0].name
        if known:
            return next(iter(known))
        return "default"

    def clamp_indexes(self) -> None:
        if not self.config.profiles:
            self.profile_index = -1
            self.selected_ids = []
            return
        if not 0 <= self.profile_index < len(self.config.profiles):
            self.profile_index = 0
        profile = self.current_profile()
        screens = {screen.name for screen in self.screens() if screen.connected}
        known = set(profile.connector_names()) if profile is not None else set()
        if self.monitor_name not in screens and self.monitor_name not in known:
            self.monitor_name = self.preferred_monitor()

    def push_undo(self, coalesce: str | None = None) -> None:
        if coalesce and coalesce == self._coalesce:
            return
        self.undo_stack.append(self.config.to_dict())
        if len(self.undo_stack) > _UNDO_LIMIT:
            del self.undo_stack[0]
        self.redo_stack.clear()
        self._coalesce = coalesce or None

    def drop_last_undo(self) -> None:
        if self.undo_stack:
            self.undo_stack.pop()
        self._coalesce = None

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.config.to_dict())
        self.config = Config.from_dict(self.undo_stack.pop())
        self._coalesce = None
        self.clamp_indexes()
        self.save()
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.config.to_dict())
        self.config = Config.from_dict(self.redo_stack.pop())
        self._coalesce = None
        self.clamp_indexes()
        self.save()
        return True

    def create_profile(self, name: str) -> str | None:
        cleaned = name.strip()
        if not cleaned:
            return "empty"
        if any(profile.name == cleaned for profile in self.config.profiles):
            return f"A profile named {cleaned} already exists."
        self.push_undo()
        self.config.profiles.append(Profile(name=cleaned))
        self.profile_index = len(self.config.profiles) - 1
        self.selected_ids = []
        self.save()
        return None

    def rename_profile(self, name: str) -> str | None:
        profile = self.current_profile()
        cleaned = name.strip()
        if profile is None or not cleaned or cleaned == profile.name:
            return "empty"
        if any(other.name == cleaned for other in self.config.profiles):
            return f"A profile named {cleaned} already exists."
        self.push_undo()
        profile.name = cleaned
        self.save()
        return None

    def delete_current_profile(self) -> None:
        if self.current_profile() is None:
            return
        self.push_undo()
        del self.config.profiles[self.profile_index]
        self.profile_index = min(self.profile_index, len(self.config.profiles) - 1)
        self.selected_ids = []
        self.save()

    def create_layout(self, name: str) -> str | None:
        profile = self.current_profile()
        cleaned = name.strip()
        if profile is None or not cleaned:
            return "empty"
        default = profile.layouts.get("Default")
        blank_default = (
            list(profile.layouts) == ["Default"]
            and default is not None
            and all(screen.root is None for screen in default.screens.values())
        )
        if cleaned in profile.layouts and not blank_default:
            return f"A layout named {cleaned} already exists."
        self.push_undo()
        if blank_default and cleaned != "Default":
            profile.layouts[cleaned] = profile.layouts.pop("Default")
            profile.layouts[cleaned].name = cleaned
        elif cleaned not in profile.layouts:
            profile.layouts[cleaned] = Layout(name=cleaned)
        profile.current = cleaned
        self.selected_ids = []
        self.save()
        return None

    def rename_layout(self, name: str) -> str | None:
        profile = self.current_profile()
        cleaned = name.strip()
        if profile is None or not cleaned:
            return "empty"
        if cleaned == profile.current:
            return "empty"
        if cleaned in profile.layouts:
            return f"A layout named {cleaned} already exists."
        self.push_undo()
        profile.layouts[cleaned] = profile.layouts.pop(profile.current)
        profile.layouts[cleaned].name = cleaned
        profile.current = cleaned
        self.save()
        return None

    def delete_current_layout(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        self.push_undo()
        profile.layouts.pop(profile.current, None)
        if profile.layouts:
            profile.current = next(iter(profile.layouts))
        else:
            profile.layouts["Default"] = Layout(name="Default")
            profile.current = "Default"
        self.selected_ids = []
        self.save()

    def set_current_layout(self, name: str) -> bool:
        profile = self.current_profile()
        if profile is None or not name or profile.current == name or name not in profile.layouts:
            return False
        self.push_undo()
        profile.current = name
        self.selected_ids = []
        self.save()
        return True

    def set_hotkey(self, text: str) -> None:
        self.config.hotkey = text
        self.save()
