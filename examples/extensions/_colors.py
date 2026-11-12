"""Shared dual-color JSON write for GPU, RAM, and Ryuo plugins."""

from __future__ import annotations

import json
from pathlib import Path

from onigiri.extensions import settings_colors

Rgb = tuple[int, int, int]


def dual_color_dir() -> Path:
    return Path.home() / ".config" / "dual-color"


def default_path(stem: str) -> Path:
    return dual_color_dir() / f"{stem}.json"


def pair_from_scene(scene) -> tuple[Rgb, Rgb] | None:
    colors = settings_colors(scene)
    blue = colors.get("blue")
    purple = colors.get("purple")
    if blue is None and purple is None:
        return None
    if blue is None:
        blue = purple
    if purple is None:
        purple = blue
    assert blue is not None and purple is not None
    return blue, purple


def write_pair(blue: Rgb, purple: Rgb, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"blue": list(blue), "purple": list(purple)}, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def apply_pair(scene, stem: str) -> None:
    pair = pair_from_scene(scene)
    if pair is None:
        return
    extras = scene.extras if isinstance(scene.extras, dict) else {}
    override = extras.get(f"{stem}_file") or extras.get("colors_file")
    path = Path(override) if override else default_path(stem)
    write_pair(pair[0], pair[1], path)
    if extras.get(f"{stem}_file"):
        return
    shared = extras.get("colors_file")
    fallback = Path(shared) if shared else dual_color_dir() / "colors.json"
    if fallback.resolve() != path.resolve():
        write_pair(pair[0], pair[1], fallback)


def read_pair(stem: str) -> dict:
    from onigiri.model import parse_named_colors
    path = default_path(stem)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return parse_named_colors(data)


def control_page(context, stem: str, description: str):
    from PyQt6.QtWidgets import QPushButton
    from onigiri.ui.extension_page import ExtensionControlPage
    try:
        initial = read_pair(stem)
    except (OSError, ValueError):
        initial = {}
    page = ExtensionControlPage(context, description=description, initial=initial)
    refresh = QPushButton("Read current colors")
    refresh.setMaximumWidth(200)
    def loaded(values):
        page.editor.set_values(values)
        context.set_status("Current colors loaded." if values else "No saved device colors found.")
    refresh.clicked.connect(lambda: context.run(lambda: read_pair(stem), loaded, message="Reading colors…"))
    page.column.insertWidget(2, refresh)
    return page
