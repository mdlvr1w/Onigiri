"""Load and save ~/.config/onigiri/onigiri.json, including the old flat format."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from onigiri.files import atomic_write_text
from onigiri.model import (
    Config,
    Layout,
    Leaf,
    Node,
    Profile,
    ScreenLayout,
    Split,
    Tile,
    drop_placeholder_screens,
    new_id,
)


def default_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "onigiri" / "onigiri.json"


def _is_old_profile(data: dict) -> bool:
    return "monitors" not in data and isinstance(data.get("tiles"), list)


def needs_import(data: dict) -> bool:
    if not isinstance(data, dict) or data.get("version") == 2:
        return False
    profiles = data.get("profiles")
    return isinstance(profiles, list) and any(isinstance(item, dict) and _is_old_profile(item) for item in profiles)


def _tile_from_old(data: dict) -> Tile:
    mode = str(data.get("launch_mode") or "raw")
    if mode not in {"raw", "helper", "app"}:
        mode = "raw"
    match = data.get("match") if isinstance(data.get("match"), dict) else {}
    kind = str(match.get("type") or "")
    if kind == "regex-title":
        kind = "title"
    if kind not in {"class", "title"}:
        kind = ""
    terminal = str(data.get("terminal_app") or data.get("helper_terminal") or "alacritty")
    return Tile(
        id=new_id(),
        name=str(data.get("name") or "Tile"),
        launch_mode=mode,
        command=str(data.get("command") or ""),
        shell_command=str(data.get("shell_command") or ""),
        terminal=terminal,
        app_id=str(data.get("app_id") or ""),
        app_name=str(data.get("app_name") or ""),
        no_border=bool(data.get("no_border", False)),
        skip_taskbar=bool(data.get("skip_taskbar", False)),
        match_kind=kind,
        match_value=str(match.get("value") or ""),
    )


def _chain(items: list[tuple[Tile, tuple[float, float, float, float]]]) -> Node:
    if len(items) == 1:
        return Leaf(items[0][0])
    ratio = 1.0 / len(items)
    return Split("v", min(0.92, max(0.08, ratio)), Leaf(items[0][0]), _chain(items[1:]))


def _tree_from_placed(items: list[tuple[Tile, tuple[float, float, float, float]]]) -> Node:
    """Rebuild a guillotine tree from rectangles. Fall back to an equal vertical chain."""

    eps = 1.0

    def rec(group: list[tuple[Tile, tuple[float, float, float, float]]]) -> Node:
        if len(group) <= 1:
            return Leaf(group[0][0]) if group else Leaf(Tile(id=new_id(), name="Tile 1"))
        xs0 = min(rect[0] for _tile, rect in group)
        ys0 = min(rect[1] for _tile, rect in group)
        xs1 = max(rect[0] + rect[2] for _tile, rect in group)
        ys1 = max(rect[1] + rect[3] for _tile, rect in group)
        best: tuple[float, str, float, list, list] | None = None

        def consider(axis: int, orientation: str, origin: float, span: float) -> None:
            nonlocal best
            if span <= 0:
                return
            cuts = set()
            for _tile, rect in group:
                cuts.add(rect[axis])
                cuts.add(rect[axis] + rect[axis + 2])
            for cut in cuts:
                if cut <= origin + eps or cut >= origin + span - eps:
                    continue
                left: list = []
                right: list = []
                crossed = False
                for item in group:
                    rect = item[1]
                    start = rect[axis]
                    end = rect[axis] + rect[axis + 2]
                    if end <= cut + eps:
                        left.append(item)
                    elif start >= cut - eps:
                        right.append(item)
                    else:
                        crossed = True
                        break
                if crossed or not left or not right or len(left) + len(right) != len(group):
                    continue
                score = abs(cut - (origin + span / 2.0))
                if best is None or score < best[0]:
                    best = (score, orientation, cut, left, right)

        consider(0, "v", xs0, xs1 - xs0)
        consider(1, "h", ys0, ys1 - ys0)
        if best is None:
            return _chain(group)
        _score, orientation, cut, left, right = best
        origin = xs0 if orientation == "v" else ys0
        span = (xs1 - xs0) if orientation == "v" else (ys1 - ys0)
        ratio = (cut - origin) / span if span else 0.5
        ratio = min(0.92, max(0.08, ratio))
        return Split(orientation, ratio, rec(left), rec(right))

    return rec(items)


def import_old(data: dict) -> Config:
    profiles: list[Profile] = []
    for raw in data.get("profiles") or []:
        if not isinstance(raw, dict):
            continue
        if not _is_old_profile(raw):
            if "monitors" in raw or "layouts" in raw:
                profiles.append(Profile.from_dict(raw))
            continue
        try:
            gap = int(raw.get("tile_gap") or 0)
        except (TypeError, ValueError):
            gap = 0
        monitor = str(raw.get("monitor") or "default")
        placed: list[tuple[Tile, tuple[float, float, float, float]]] = []
        for tile_data in raw.get("tiles") or []:
            if not isinstance(tile_data, dict):
                continue
            tile = _tile_from_old(tile_data)
            try:
                rect = (
                    float(tile_data.get("x", 0)),
                    float(tile_data.get("y", 0)),
                    float(tile_data.get("width", 800)),
                    float(tile_data.get("height", 600)),
                )
            except (TypeError, ValueError):
                rect = (0.0, 0.0, 800.0, 600.0)
            placed.append((tile, rect))
        root: Node | None = _tree_from_placed(placed) if placed else None
        profiles.append(
            Profile(
                name=str(raw.get("name") or "Imported"),
                current="Imported",
                layouts={
                    "Imported": Layout(
                        name="Imported",
                        screens={monitor: ScreenLayout(root=root, gap=max(0, gap))},
                    )
                },
            )
        )
    return Config(profiles=profiles)


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Could not read {path}: expected an object")
    if needs_import(data):
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
        imported = import_old(data)
        save_config(imported, path)
        return imported
    config = Config.from_dict(data)
    dirty = any(isinstance(item, dict) and "monitors" in item for item in (data.get("profiles") or []))
    if any(drop_placeholder_screens(profile) for profile in config.profiles):
        dirty = True
    if dirty:
        save_config(config, path)
    return config


def save_config(config: Config, path: Path | None = None) -> None:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(config.to_dict(), indent=2) + "\n")
