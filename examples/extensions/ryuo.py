"""Ryuo AIO LCD title/content pair, written for ryuo-lcd."""

from _colors import apply_pair

title = "Ryuo"


def fields():
    return [
        {"id": "blue", "type": "color", "label": "Title"},
        {"id": "purple", "type": "color", "label": "Content"},
    ]


def on_scene(scene) -> None:
    apply_pair(scene, "ryuo")


def create_page(context):
    from _colors import control_page
    return control_page(context, 'ryuo', 'Adjust the title and content colors of your Ryuo display.')
