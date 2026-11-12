"""Wallpaper, lights, and playlist apply for scenes."""

from __future__ import annotations

from onigiri.extensions import apply_scene, payload_from_scene
from onigiri.model import Scene
from onigiri.playlist import next_index
from onigiri.session import Session
from onigiri.ui.screens import screen_list
from onigiri.wallpaper import set_scene_wallpaper, color_hex


class Scenes:
    def __init__(
        self,
        session: Session,
        *,
        set_wallpaper_fn=None,
        apply_colors_fn=None,
        screens_fn=None,
    ) -> None:
        self.session = session
        self._set_wallpaper = set_wallpaper_fn
        self._apply_colors = apply_colors_fn
        self._screens = screens_fn or (lambda: screen_list(usable=False))
        self.playlist_index = 0

    def by_id(self, scene_id: str) -> Scene | None:
        for scene in self.session.config.scenes:
            if scene.id == scene_id:
                return scene
        return None

    def push_wallpaper(self, scene: Scene) -> None:
        if scene.background_color is None and not scene.wallpaper.strip():
            return
        source = color_hex(scene.background_color) if scene.background_color is not None else scene.wallpaper
        if self._set_wallpaper is not None:
            try:
                self._set_wallpaper(source, scene.wallpaper_pan)
            except TypeError:
                self._set_wallpaper(source)
            return
        screens = [
            (item.name, item.x, item.y, item.width, item.height) for item in self._screens()
        ]
        set_scene_wallpaper(scene.wallpaper, screens, scene.wallpaper_pan, background_color=scene.background_color)

    def paint(self, scene: Scene) -> list[str]:
        if self._apply_colors is not None:
            try:
                self._apply_colors(
                    scene.blue,
                    scene.purple,
                    hardware_blue=scene.case_blue(),
                    hardware_purple=scene.case_purple(),
                )
            except TypeError:
                self._apply_colors(scene.blue, scene.purple)
            return []
        return apply_scene(payload_from_scene(scene))

    def apply(self, scene: Scene) -> list[str]:
        self.push_wallpaper(scene)
        errors = self.paint(scene)
        for index, item in enumerate(self.session.config.scenes):
            if item.id == scene.id:
                self.playlist_index = index
                break
        return errors

    def append(self, scene: Scene) -> None:
        self.session.config.scenes.append(scene)
        self.session.save()

    def replace_or_delete(self, scene: Scene, updated: Scene | None, deleted: bool) -> None:
        if deleted:
            self.session.config.scenes = [item for item in self.session.config.scenes if item.id != scene.id]
        elif updated is not None:
            self.session.config.scenes = [
                updated if item.id == scene.id else item for item in self.session.config.scenes
            ]
        self.session.save()

    def reorder(self, ids: list[str]) -> None:
        by_id = {scene.id: scene for scene in self.session.config.scenes}
        current = ""
        if self.session.config.scenes and 0 <= self.playlist_index < len(self.session.config.scenes):
            current = self.session.config.scenes[self.playlist_index].id
        order = list(dict.fromkeys([*ids, *by_id]))
        self.session.config.scenes = [by_id[key] for key in order if key in by_id]
        if current:
            self.playlist_index = next(
                (index for index, scene in enumerate(self.session.config.scenes) if scene.id == current),
                0,
            )
        self.session.save()

    def tick(self) -> Scene | None:
        scenes = self.session.config.scenes
        if len(scenes) < 2:
            self.session.config.playlist.playing = False
            return None
        self.playlist_index = next_index(len(scenes), self.playlist_index, self.session.config.playlist.mode)
        return scenes[self.playlist_index]
