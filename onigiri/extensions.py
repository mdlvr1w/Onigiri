"""Load user Python that runs when a scene (or color swatch) is applied.

Drop a .py with on_scene(scene) in ~/.config/onigiri/extensions/.
Optional fields() tells the scene builder which controls to draw.
Core tiling and wallpaper do not depend on this folder. An empty folder
paints no hardware.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from onigiri.model import Rgb, Scene, parse_named_colors, parse_rgb

log = logging.getLogger("onigiri.extensions")

_FIELD_TYPES = {"color", "bool", "file", "text", "number"}
# Only extensions with on_stop stay loaded. Serialize scene changes and shutdown.
_active: dict[Path, object] = {}
_lifecycle_lock = threading.RLock()
_MISSING = (-1, -1, -1)


@dataclass
class ExtensionField:
    id: str
    type: str
    label: str
    default: object = None
    minimum: int = 0
    maximum: int = 100


@dataclass
class ExtensionInfo:
    stem: str
    path: Path
    title: str
    fields: list[ExtensionField] = field(default_factory=list)
    error: str = ""
    continuous: bool = False
    explicit_only: bool = False


@dataclass
class ScenePayload:
    id: str = ""
    name: str = ""
    wallpaper: str = ""
    wallpaper_pan: dict[str, tuple[float, float]] = field(default_factory=dict)
    colors: dict[str, Rgb] = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    script: str = ""
    plugins: dict[str, dict] | None = None
    settings: dict = field(default_factory=dict)
    background_color: Rgb | None = None


def extensions_dir() -> Path:
    override = os.environ.get("ONIGIRI_EXTENSIONS")
    if override:
        return Path(override)
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "onigiri" / "extensions"


def payload_from_scene(scene: Scene) -> ScenePayload:
    colors: dict[str, Rgb] = {
        "blue": scene.blue,
        "purple": scene.purple,
        "hardware_blue": scene.case_blue(),
        "hardware_purple": scene.case_purple(),
    }
    colors.update(scene.colors)
    extra_named = scene.extras.get("colors")
    if isinstance(extra_named, dict):
        colors.update(parse_named_colors(extra_named))
    plugins = None if scene.plugins is None else {stem: dict(values) for stem, values in scene.plugins.items()}
    return ScenePayload(
        id=scene.id,
        name=scene.name,
        wallpaper=scene.wallpaper,
        background_color=scene.background_color,
        wallpaper_pan=dict(scene.wallpaper_pan),
        colors=colors,
        extras=dict(scene.extras),
        script=scene.script,
        plugins=plugins,
    )


def payload_from_colors(
    blue: Rgb,
    purple: Rgb,
    *,
    hardware_blue: Rgb | None = None,
    hardware_purple: Rgb | None = None,
    extra_colors: dict[str, Rgb] | None = None,
    extras: dict | None = None,
) -> ScenePayload:
    case_blue = blue if hardware_blue is None else hardware_blue
    case_purple = purple if hardware_purple is None else hardware_purple
    colors: dict[str, Rgb] = {
        "blue": blue,
        "purple": purple,
        "hardware_blue": case_blue,
        "hardware_purple": case_purple,
    }
    if extra_colors:
        colors.update(extra_colors)
    return ScenePayload(colors=colors, extras=dict(extras or {}))


def settings_colors(scene: ScenePayload) -> dict[str, Rgb]:
    """Color map for a piece: payload colors plus RGB values from scene.settings."""

    colors = dict(scene.colors)
    for key, value in dict(scene.settings or {}).items():
        rgb = _as_rgb(value)
        if rgb is not None:
            colors[str(key)] = rgb
    return colors


def list_extensions(directory: Path | None = None) -> list[ExtensionInfo]:
    folder = directory if directory is not None else extensions_dir()
    found: list[ExtensionInfo] = []
    if not folder.is_dir():
        return found
    _ensure_import_path(folder)
    for path in _plugin_files(folder):
        found.append(_describe(path))
    return found


def apply_scene(scene: ScenePayload, *, directory: Path | None = None) -> list[str]:
    with _lifecycle_lock:
        return _apply_scene(scene, directory=directory)


def apply_extension(info: ExtensionInfo, settings: dict) -> list[str]:
    """Apply one extension directly, without a scene or unrelated effect changes."""
    with _lifecycle_lock:
        if not info.path.is_file():
            return [f"{info.title}: extension file is missing"]
        _ensure_import_path(info.path.parent)
        try:
            module = _active.get(info.path.resolve()) or _load_module(info.path)
        except Exception as error:
            return [f"{info.path.name}: {error}"]
        group = getattr(module, "exclusive_group", "")
        conflicts = [path for path, active in _active.items()
                     if path != info.path.resolve() and group and getattr(active, "exclusive_group", "") == group]
        errors = _stop_paths(conflicts)
        if errors:
            return errors
        payload = payload_from_colors((0, 123, 255), (212, 0, 255), extras={"standalone": True})
        payload.name = f"Manual {info.title}"
        payload.plugins = {info.stem: dict(settings)}
        return _run_file(info.path, payload, settings)


def stop_extension(info: ExtensionInfo) -> list[str]:
    with _lifecycle_lock:
        return _stop_paths([info.path.resolve()])


def extension_running(info: ExtensionInfo) -> bool:
    return info.path.resolve() in _active


def create_extension_page(info: ExtensionInfo, context):
    """Call an optional UI-thread create_page(context) hook, with a generic fallback."""
    _ensure_import_path(info.path.parent)
    module = _load_module(info.path)
    factory = getattr(module, "create_page", None)
    if callable(factory):
        return factory(context)
    from onigiri.ui.extension_page import ExtensionControlPage
    return ExtensionControlPage(context, description="Control this extension independently of scenes.")


def stop_extensions() -> list[str]:
    """Stop continuous effects before shutdown or when their scene disables them."""
    with _lifecycle_lock:
        return _stop_paths(list(_active))


def extension_status() -> list[str]:
    """Poll nonblocking status hooks (including failures after startup)."""
    if not _lifecycle_lock.acquire(blocking=False):
        return []
    try:
        messages = []
        for path, module in list(_active.items()):
            hook = getattr(module, "on_status", None)
            if callable(hook):
                try:
                    message = hook()
                    if message:
                        messages.append(f"{path.stem}: {message}")
                except Exception as exc:
                    messages.append(f"{path.stem}: {exc}")
        return messages
    finally:
        _lifecycle_lock.release()


def _stop_paths(paths) -> list[str]:
    errors = []
    for path in paths:
        module = _active.pop(path, None)
        if module is not None:
            try:
                module.on_stop()
            except Exception as exc:
                errors.append(f"{path.name}: stopping failed: {exc}")
    return errors


def _apply_scene(scene: ScenePayload, *, directory: Path | None = None) -> list[str]:
    """Run selected on_scene hooks, then the scene's own script. Returns error strings.

    plugins is None: every file in the folder (legacy scenes and live color swatches).
    plugins is a dict: only those stems, even if the dict is empty.
    """

    errors: list[str] = []
    ran: set[Path] = set()
    folder = directory if directory is not None else extensions_dir()
    selected = scene.plugins
    wanted = {path.resolve() for path in _plugin_files(folder)
              if selected is None or path.stem in selected} if folder.is_dir() else set()
    if scene.script and Path(scene.script).expanduser().is_file():
        wanted.add(Path(scene.script).expanduser().resolve())
    errors.extend(_stop_paths([path for path, module in _active.items()
                               if path not in wanted or (selected is None and getattr(module, "explicit_only", False))]))
    if folder.is_dir():
        _ensure_import_path(folder)
        for path in _plugin_files(folder):
            stem = path.stem
            if selected is not None and stem not in selected:
                continue
            settings = dict(selected.get(stem) or {}) if selected is not None else {}
            ran.add(path.resolve())
            errors.extend(_run_file(path, scene, settings))
    script = str(scene.script or "").strip()
    if script:
        path = Path(script).expanduser()
        if path.is_file():
            resolved = path.resolve()
            if resolved not in ran:
                errors.extend(_run_file(path, scene, {}))
        else:
            errors.append(f"{path} is missing")
            log.warning("scene script missing: %s", path)
    return errors


def _plugin_files(folder: Path) -> list[Path]:
    return [path for path in sorted(folder.glob("*.py")) if not path.name.startswith("_")]


def _ensure_import_path(folder: Path) -> None:
    location = str(folder.resolve())
    if location not in sys.path:
        sys.path.insert(0, location)


def _describe(path: Path) -> ExtensionInfo:
    title = path.stem.replace("_", " ")
    try:
        module = _load_module(path)
        raw_title = getattr(module, "title", None)
        if raw_title:
            title = str(raw_title).strip() or title
        maker = getattr(module, "fields", None)
        raw_fields = maker() if callable(maker) else []
        return ExtensionInfo(stem=path.stem, path=path, title=title, fields=_parse_fields(raw_fields),
                             continuous=callable(getattr(module, "on_stop", None)),
                             explicit_only=bool(getattr(module, "explicit_only", False)))
    except Exception as exc:
        log.exception("extension fields failed: %s", path)
        return ExtensionInfo(stem=path.stem, path=path, title=title, error=str(exc))


def _parse_fields(raw) -> list[ExtensionField]:
    if not isinstance(raw, (list, tuple)):
        return []
    fields: list[ExtensionField] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        field_id = str(item.get("id") or "").strip()
        kind = str(item.get("type") or "").strip().lower()
        if not field_id or field_id in seen or kind not in _FIELD_TYPES:
            continue
        seen.add(field_id)
        label = str(item.get("label") or field_id).strip() or field_id
        # Bounds are plugin metadata, not user input; reject malformed definitions.
        minimum = int(item.get("min", 0))
        maximum = max(minimum, int(item.get("max", 100)))
        fields.append(ExtensionField(id=field_id, type=kind, label=label,
                                     default=item.get("default"), minimum=minimum, maximum=maximum))
    return fields


def _run_file(path: Path, scene: ScenePayload, settings: dict) -> list[str]:
    colors = dict(scene.colors)
    for key, value in settings.items():
        rgb = _as_rgb(value)
        if rgb is not None:
            colors[str(key)] = rgb
    piece = replace(scene, settings=dict(settings), colors=colors)
    try:
        resolved = path.resolve()
        module = _active.get(resolved) or _load_module(path)
        if getattr(module, "explicit_only", False) and scene.plugins is None:
            return []
        hook = getattr(module, "on_scene", None)
        if not callable(hook):
            return []
        if callable(getattr(module, "on_stop", None)):
            _active[resolved] = module
        hook(piece)
    except Exception as exc:
        _stop_paths([path.resolve()])
        log.exception("extension failed: %s", path)
        return [f"{path.name}: {exc}"]
    return []


def _as_rgb(value) -> Rgb | None:
    parsed = parse_rgb(value, _MISSING)
    if parsed == _MISSING:
        return None
    return parsed


def _load_module(path: Path):
    name = f"onigiri_ext_{path.stem}_{uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
