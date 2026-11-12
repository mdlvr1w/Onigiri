"""Lian Li UNI FAN LEDs and LCD colors in one pass."""

from pathlib import Path

from onigiri.extensions import settings_colors

from _lianli import paint, reload_fans

title = "Lian Li fans"


def fields():
    return [
        {"id": "led_blue", "type": "color", "label": "LED color 1"},
        {"id": "led_purple", "type": "color", "label": "LED color 2"},
        {"id": "text", "type": "color", "label": "LCD text"},
        {"id": "gauge", "type": "color", "label": "LCD gauge"},
    ]


def _rgb(colors: dict, *names):
    for name in names:
        value = colors.get(name)
        if value is not None:
            return value
    return None


def on_scene(scene) -> None:
    colors = settings_colors(scene)
    led_one = _rgb(colors, "led_blue", "blue")
    led_two = _rgb(colors, "led_purple", "purple")
    text = _rgb(colors, "text")
    gauge = _rgb(colors, "gauge")
    if led_one is None and led_two is None and text is None and gauge is None:
        return
    if led_one is None:
        led_one = led_two
    if led_two is None:
        led_two = led_one
    extras = scene.extras if isinstance(scene.extras, dict) else {}
    override = extras.get("lianli_file")
    path = Path(override) if override else None
    if extras.get("standalone"):
        from _lianli import lianli_path
        if not (path or lianli_path()).is_file():
            raise RuntimeError("Lian Li is not configured. Set up the fan daemon before applying colors.")
    paint(led_one, led_two, text, gauge, path)
    reload = extras.get("reload_fn") or reload_fans
    reload()



def create_page(context):
    from PyQt6.QtWidgets import QPushButton
    from onigiri.ui.extension_page import ExtensionControlPage
    from _lianli import read_colors
    try:
        initial = read_colors()
    except (OSError, ValueError):
        initial = {}
    page = ExtensionControlPage(context, description="Control fan LED colors and LCD text and gauges. Apply also reloads the fan daemon.", initial=initial)
    refresh = QPushButton("Read current colors")
    refresh.setMaximumWidth(200)
    def loaded(values):
        page.editor.set_values(values)
        context.set_status("Current colors loaded." if values else "No fan colors found; check the fan daemon setup.")
    refresh.clicked.connect(lambda: context.run(read_colors, loaded, message="Reading fan colors…"))
    page.column.insertWidget(2, refresh)
    return page
