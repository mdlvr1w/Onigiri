"""Optional continuous Hue screen sync. See examples/extensions/HUE_SYNC.md."""
from _hue_sync import SyncProcess

title = "Hue screen sync"
exclusive_group = "hue"
explicit_only = True
_worker = None


def fields():
    return [
        {"id": "area", "type": "text", "label": "Entertainment area name or ID (blank if only one)"},
        {"id": "brightness", "type": "number", "label": "Brightness (%)", "min": 1, "max": 100, "default": 60},
        {"id": "capture_fps", "type": "number", "label": "Capture FPS (lower reduces system load)", "min": 5, "max": 25, "default": 15},
        {"id": "midtone_lift", "type": "number", "label": "Midtone lift (0 = off)", "min": 0, "max": 100, "default": 0},
        {"id": "smoothing", "type": "number", "label": "Smoothing (%)", "min": 0, "max": 95, "default": 65},
        {"id": "whole_screen", "type": "bool", "label": "One average color for all lights", "default": False},
    ]


def on_scene(scene):
    global _worker
    if _worker is None:
        _worker = SyncProcess()
    _worker.start(dict(scene.settings))


def on_stop():
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def on_status():
    return _worker.status() if _worker is not None else ""



def create_page(context):
    from _hue_page import HueSyncPage
    return HueSyncPage(context)
