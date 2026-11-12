"""RAM stick starry-night pair, written for aura-starry."""

from _colors import apply_pair

title = "RAM"


def fields():
    return [
        {"id": "blue", "type": "color", "label": "Color 1"},
        {"id": "purple", "type": "color", "label": "Color 2"},
    ]


def on_scene(scene) -> None:
    apply_pair(scene, "ram")


def create_page(context):
    from _colors import control_page
    return control_page(context, 'ram', 'Adjust the two colors used by your RAM lighting.')
