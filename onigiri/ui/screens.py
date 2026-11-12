"""Usable monitor rectangles from Qt, which already exclude panels."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True)
class ScreenInfo:
    name: str
    x: int
    y: int
    width: int
    height: int
    connected: bool = True
    primary: bool = False


def screen_list(*, usable: bool = True) -> list[ScreenInfo]:
    app = QApplication.instance()
    if app is None:
        return [ScreenInfo("default", 0, 0, 1920, 1080, True, True)]
    screens: list[ScreenInfo] = []
    primary = app.primaryScreen()
    for screen in app.screens():
        geo = screen.availableGeometry() if usable else screen.geometry()
        screens.append(
            ScreenInfo(
                name=screen.name() or "default",
                x=int(geo.x()),
                y=int(geo.y()),
                width=max(1, int(geo.width())),
                height=max(1, int(geo.height())),
                connected=True,
                primary=screen is primary,
            )
        )
    if not screens:
        return [ScreenInfo("default", 0, 0, 1920, 1080, True, True)]
    return screens
