"""GPU dual-color lighting integration for aura-starry."""

from _colors import apply_pair

title = "GPU"


def fields():
    return [
        {"id": "blue", "type": "color", "label": "Color 1"},
        {"id": "purple", "type": "color", "label": "Color 2"},
    ]


def on_scene(scene) -> None:
    apply_pair(scene, "gpu")


def create_page(context):
    from _colors import control_page
    return control_page(context, 'gpu', 'Adjust the two colors used by your GPU lighting.')
