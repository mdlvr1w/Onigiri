"""Hue Sync process supervisor and bounded screen-color sampling. No network on import."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from collections import deque

from _hue import load_hue


def bounded(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def normalized_settings(settings):
    return {
        "area": str(settings.get("area") or "").strip(),
        "brightness": bounded(settings.get("brightness"), 1, 100, 60),
        "smoothing": bounded(settings.get("smoothing"), 0, 95, 65),
        "whole_screen": bool(settings.get("whole_screen", False)),
    }


def sample_regions(image):
    """Reduce to 48x27 pixels; trim black letterboxing, then sample left/center/right."""
    from PyQt6.QtCore import Qt
    if image.isNull():
        return None
    small = image.scaled(48, 27, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    rows = [[small.pixelColor(x, y).getRgb()[:3] for x in range(48)] for y in range(27)]
    # Only trim contiguous near-black rows/columns at the outer edges.
    top, bottom, left, right = 0, 27, 0, 48
    while top < bottom - 1 and max(max(p) for p in rows[top]) < 8:
        top += 1
    while bottom > top + 1 and max(max(p) for p in rows[bottom - 1]) < 8:
        bottom -= 1
    while left < right - 1 and max(max(rows[y][left]) for y in range(top, bottom)) < 8:
        left += 1
    while right > left + 1 and max(max(rows[y][right - 1]) for y in range(top, bottom)) < 8:
        right -= 1

    def average(start, end):
        pixels = [rows[y][x] for y in range(top, bottom) for x in range(start, end)]
        return tuple(sum(p[c] for p in pixels) / len(pixels) for c in range(3))

    width = right - left
    third = max(1, width // 3)
    return (average(left, min(right, left + third)), average(left, right),
            average(max(left, right - third), right))


def channel_colors(channels, regions, settings, previous=None):
    """Map entertainment-area horizontal positions to zones, with temporal smoothing."""
    from hue_entertainment import LightColorCommand
    previous = previous if previous is not None else {}
    alpha = 1.0 - settings["smoothing"] / 100.0
    brightness = settings["brightness"] / 100.0
    commands = []
    for channel in channels:
        x = channel.position[0]
        region = 1 if settings["whole_screen"] else (0 if x < -0.15 else 2 if x > 0.15 else 1)
        target = tuple(c * 257 * brightness for c in regions[region])
        old = previous.get(channel.channel_id, target)
        rgb = tuple(max(0, min(65535, round(a + alpha * (b - a)))) for a, b in zip(old, target))
        previous[channel.channel_id] = rgb
        commands.append(LightColorCommand(channel_id=channel.channel_id, red=rgb[0], green=rgb[1], blue=rgb[2]))
    return commands


class SyncProcess:
    def __init__(self):
        self.process = None
        self.settings = None
        self.messages = deque(maxlen=8)
        self._last = ""
        self._reader = None
        self.cleanup_errors = deque(maxlen=4)

    def start(self, settings):
        settings = normalized_settings(settings)
        if self.process is not None and self.process.poll() is None and settings == self.settings:
            return
        self.stop()
        hue = load_hue()
        if not hue.paired() or not hue.clientkey:
            raise RuntimeError("Enable streaming first: run _hue.py --entertainment after pressing the bridge button.")
        try:
            if len(bytes.fromhex(hue.clientkey)) != 16:
                raise ValueError()
        except ValueError:
            raise RuntimeError("Invalid streaming key. Run _hue.py --entertainment again.") from None
        if importlib.util.find_spec("hue_entertainment") is None:
            raise RuntimeError("Install optional dependencies with: python -m pip install -r requirements-hue-sync.txt")
        env = dict(os.environ, QT_MEDIA_BACKEND="ffmpeg")
        command = [sys.executable, str(Path(__file__).with_name("_hue_sync_worker.py"))]
        self.messages.clear()
        self.cleanup_errors.clear()
        self._last = ""
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=env)
        self.settings = settings
        try:
            self.process.stdin.write(json.dumps({"settings": settings, "hue": hue.to_dict()}) + "\n")
            self.process.stdin.flush()
        except (OSError, BrokenPipeError):
            self.stop()
            raise RuntimeError("Screen-sync worker could not start.") from None
        # Read only our structured status events; credentials never appear in logs/argv.
        self._reader = threading.Thread(target=self._read, args=(self.process,), daemon=True)
        self._reader.start()

    def _read(self, process):
        for line in process.stdout:
            try:
                message = json.loads(line)
                if isinstance(message, dict) and isinstance(message.get("status"), str):
                    self.messages.append(message["status"])
                    if message.get("cleanup_error"):
                        self.cleanup_errors.append(message["status"])
            except (ValueError, TypeError):
                pass

    def status(self):
        message = self.messages[-1] if self.messages else "Starting screen capture…"
        if self.process is not None and self.process.poll() is not None:
            if not message.startswith("Error:"):
                message = "Error: screen sync stopped; apply the scene again to restart."
        if message == self._last:
            return ""
        self._last = message
        return message

    def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin:
            process.stdin.close()  # EOF requests capture stop, stream release, then state restoration.
        forced = False
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            forced = True
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self._reader:
            self._reader.join(timeout=1)
        if process.stdout:
            process.stdout.close()
        self.settings = None
        if forced:
            raise RuntimeError("Screen-sync worker required forced shutdown; lamp restoration is unconfirmed.")
        if self.cleanup_errors:
            raise RuntimeError(self.cleanup_errors[-1])
