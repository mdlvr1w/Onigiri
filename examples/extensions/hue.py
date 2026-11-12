"""Onigiri extension blueprint.

Define on_scene(scene) and optional fields() / title.
Pair lamps by running python _hue.py in this folder (writes hue.json).
This file only paints. Copy both hue.py and _hue.py to
~/.config/onigiri/extensions/.
"""

from onigiri.extensions import settings_colors

from _hue import load_hue, paint_hue

title = "Hue lamps"
exclusive_group = "hue"


def fields():
    return [
        {"id": "blue", "type": "color", "label": "Color 1"},
        {"id": "purple", "type": "color", "label": "Color 2"},
    ]


def on_scene(scene) -> None:
    # Streaming owns the lamps while explicitly selected for this scene.
    if scene.plugins is not None and "hue_sync" in scene.plugins:
        return
    if scene.extras.get("paint_hue") is False:
        return
    colors = settings_colors(scene)
    blue = colors.get("blue")
    purple = colors.get("purple")
    if blue is None or purple is None:
        return
    path = scene.extras.get("hue_file")
    if not scene.extras.get("hue_fn") and not load_hue(path).paired():
        if scene.extras.get("standalone"):
            raise RuntimeError("Pair the Hue bridge and assign lamps on this page first.")
        return
    if scene.extras.get("standalone") and not scene.extras.get("hue_fn"):
        settings = load_hue(path)
        if not settings.blue and not settings.purple:
            raise RuntimeError("Refresh lamps and save at least one color assignment before applying colors.")
    painter = scene.extras.get("hue_fn") or paint_hue
    painter(blue, purple, path)



def create_page(context):
    from _hue_page import HuePage
    return HuePage(context)
