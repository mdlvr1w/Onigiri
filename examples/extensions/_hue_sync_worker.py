"""Isolated Qt capture process; configuration arrives through stdin, never argv."""
from __future__ import annotations

import asyncio
import json
import signal
import sys
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtMultimedia import QMediaCaptureSession, QScreenCapture, QVideoSink

from _hue_sync import channel_colors, normalized_settings, sample_regions


def report(message, *, cleanup_error=False):
    print(json.dumps({"status": message, "cleanup_error": cleanup_error}), flush=True)


def restore_body(light):
    body = {}
    if "on" in light:
        body["on"] = {"on": light["on"]["on"]}
    if "dimming" in light:
        body["dimming"] = {"brightness": light["dimming"]["brightness"]}
    temperature = light.get("color_temperature", {})
    if temperature.get("mirek_valid") and temperature.get("mirek") is not None:
        body["color_temperature"] = {"mirek": temperature["mirek"]}
    elif light.get("color", {}).get("xy"):
        body["color"] = {"xy": light["color"]["xy"]}
    if light.get("gradient", {}).get("points"):
        body["gradient"] = {"points": light["gradient"]["points"]}
    return body


async def restore_lights(request, area_id, snapshots, *, pause=asyncio.sleep):
    """Force real lamp changes; cached bridge values alone don't prove LED output."""
    from copy import deepcopy
    from _hue import _nudge_xy

    # DTLS disconnect and bridge deactivation are separate operations.
    async with asyncio.timeout(4):
        while True:
            areas = await request("GET", "entertainment_configuration")
            area = next((item for item in areas if item.get("id") == area_id), None)
            if area is None or area.get("status") != "inactive":
                await pause(0.2)
                continue
            break
    await pause(0.3)

    async def restore_one(light_id, saved):
        nudge = deepcopy(saved)
        # Existing Play-bar workaround: change the bridge's cached state first.
        if not saved.get("on", {}).get("on", True):
            nudge = {"on": {"on": True}, "dimming": {"brightness": 0.1}}
        elif saved.get("gradient", {}).get("points"):
            for point in nudge["gradient"]["points"]:
                xy = point.get("color", {}).get("xy")
                if xy:
                    x, y = _nudge_xy(xy["x"], xy["y"])
                    point["color"]["xy"] = {"x": x, "y": y}
        elif saved.get("color", {}).get("xy"):
            xy = saved["color"]["xy"]
            x, y = _nudge_xy(xy["x"], xy["y"])
            nudge["color"]["xy"] = {"x": x, "y": y}
        elif saved.get("color_temperature", {}).get("mirek"):
            mirek = saved["color_temperature"]["mirek"]
            nudge["color_temperature"]["mirek"] = mirek - 1 if mirek > 153 else mirek + 1
        elif "dimming" in saved:
            brightness = saved["dimming"]["brightness"]
            nudge["dimming"]["brightness"] = brightness - 1 if brightness >= 99 else brightness + 1
        # Don't add transitions to a no-color on/off command: devices differ in support.
        first_error = None
        try:
            await request("PUT", f"light/{light_id}", nudge)
        except Exception as error:
            first_error = error
        # A failed nudge must never prevent attempting the original state.
        await pause(0.25)
        restored = False
        for _ in range(3):
            try:
                await request("PUT", f"light/{light_id}", saved)
                restored = True
            except Exception as error:
                first_error = error
            await pause(0.25)
        if not restored:
            raise SyncError("A lamp rejected every restoration request.") from first_error

    # Each lamp gets ordered commands; different lamps may settle concurrently.
    results = await asyncio.gather(*(restore_one(light_id, saved) for light_id, saved in snapshots.items()),
                                   return_exceptions=True)
    if any(isinstance(result, BaseException) for result in results):
        raise SyncError("Some lamps could not be restored. Apply a static Hue scene once the bridge is reachable.")


class Runtime(QObject):
    captureRequested = pyqtSignal()

    def __init__(self, config, app):
        super().__init__()
        self.config = config
        self.app = app
        self.stop_event = threading.Event()
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.regions = None
        self.last_frame = 0.0
        self.error = ""
        self.capture = QScreenCapture(self)
        self.sink = QVideoSink(self)
        self.session = QMediaCaptureSession(self)
        self.session.setScreenCapture(self.capture)
        self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.frame)
        self.capture.errorOccurred.connect(self.capture_error)
        self.capture.activeChanged.connect(self.capture_active)
        self.captureRequested.connect(self.capture.start)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.tick)
        self.poll.start(100)
        self.thread = threading.Thread(target=self.run_network, daemon=True)
        self.thread.start()
        threading.Thread(target=self.watch_parent, daemon=True).start()

    def watch_parent(self):
        # The parent keeps this pipe open for the worker's lifetime. EOF also covers crashes.
        for _line in sys.stdin:
            pass
        self.stop_event.set()

    def frame(self, frame):
        now = time.monotonic()
        if now - self.last_frame < 1 / 25 or self.stop_event.is_set():
            return
        self.last_frame = now
        regions = sample_regions(frame.toImage())
        if regions is not None:
            with self.lock:
                self.regions = regions

    def capture_active(self, active):
        if not active and self.regions is not None and not self.stop_event.is_set():
            self.error = "Screen sharing stopped. Apply the scene again to restart sync."
            self.stop_event.set()

    def capture_error(self, _code, _message):
        self.error = "Screen capture failed or was cancelled. Check KDE screen-sharing permission and PipeWire."
        self.stop_event.set()

    def tick(self):
        if self.stop_event.is_set():
            self.capture.stop()
        if self.done.is_set():
            self.capture.stop()
            self.app.quit()

    def run_network(self):
        try:
            asyncio.run(self.stream())
        except Exception as exc:
            # HTTP errors can include secret-bearing URLs; surface only controlled messages.
            message = str(exc) if isinstance(exc, SyncError) else "Bridge connection or streaming failed. Check the bridge, streaming key and network."
            report("Error: " + message)
        finally:
            self.stop_event.set()
            self.done.set()

    async def stream(self):
        import aiohttp
        from hue_entertainment import EntertainmentSession
        settings = normalized_settings(self.config.get("settings", {}))
        hue = self.config["hue"]
        stream = EntertainmentSession(hue["bridge_ip"], hue["username"], hue["clientkey"], idle_timeout=0)
        timeout = aiohttp.ClientTimeout(total=4)
        snapshots = {}
        attempted = False
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async def request(method, resource, body=None):
                url = f"https://{hue['bridge_ip']}/clip/v2/resource/{resource}"
                async with http.request(method, url, headers={"hue-application-key": hue["username"]}, json=body, ssl=False) as response:
                    response.raise_for_status()
                    result = await response.json()
                    if result.get("errors"):
                        raise SyncError("The bridge rejected a lighting request. Check authorization and the Entertainment area.")
                    return result.get("data", [])

            try:
                areas = await asyncio.wait_for(stream.get_entertainment_areas(), timeout=12)
                query = settings["area"]
                matches = [a for a in areas if a.id == query or a.name.casefold() == query.casefold()] if query else areas
                if len(matches) != 1:
                    raise SyncError("Choose one Entertainment area by name or ID; create an area in the Hue app if none exists.")
                area = matches[0]
                if not area.channels:
                    raise SyncError("The selected Entertainment area has no light channels.")
                configurations = await request("GET", "entertainment_configuration")
                if any(item.get("status") == "active" for item in configurations):
                    raise SyncError("Another Hue sync session is active. Stop it before starting Onigiri screen sync.")
                entertainments = await request("GET", "entertainment")
                lights = await request("GET", "light")
                services = {c.service_id for c in area.channels}
                owners = {e.get("owner", {}).get("rid") for e in entertainments if e.get("id") in services}
                snapshots = {light["id"]: restore_body(light) for light in lights
                             if light.get("id") in services or light.get("owner", {}).get("rid") in owners}
                if not snapshots:
                    raise SyncError("Could not identify the area's lamps for state restoration.")
                if self.stop_event.is_set():
                    return
                report("Choose a screen in KDE’s screen-sharing dialog…")
                self.captureRequested.emit()
                deadline = time.monotonic() + 120
                while not self.stop_event.is_set():
                    with self.lock:
                        ready = self.regions is not None
                    if ready:
                        break
                    if time.monotonic() >= deadline:
                        raise SyncError("No screen frames received. Apply the scene again and choose a screen.")
                    await asyncio.sleep(0.05)
                if self.stop_event.is_set():
                    if self.error:
                        raise SyncError(self.error)
                    return
                # Recheck after the user has spent time in the permission dialog.
                configurations = await request("GET", "entertainment_configuration")
                if any(item.get("status") == "active" for item in configurations):
                    raise SyncError("Another Hue sync session started while choosing the screen.")
                # Snapshot immediately before changing lights, not before the permission prompt.
                lights = await request("GET", "light")
                snapshots = {light["id"]: restore_body(light) for light in lights if light.get("id") in snapshots}
                attempted = True
                await asyncio.wait_for(stream.start(area.id, stop_others=False), timeout=15)
                report(f"Syncing: {area.name}")
                previous = {}
                next_check = time.monotonic() + 5
                while not self.stop_event.is_set():
                    with self.lock:
                        regions = self.regions
                    stream.send(channel_colors(area.channels, regions, settings, previous))
                    if not stream.is_streaming:
                        raise SyncError("The Hue streaming connection stopped. Apply the scene again to reconnect.")
                    if time.monotonic() >= next_check:
                        status, _owner = await asyncio.wait_for(stream.remote_status(), timeout=5)
                        if status != "active":
                            raise SyncError("The bridge ended screen sync. Apply the scene again to reconnect.")
                        next_check = time.monotonic() + 5
                    await asyncio.sleep(1 / 25)
                if self.error:
                    raise SyncError(self.error)
            finally:
                try:
                    await asyncio.wait_for(stream.aclose(), timeout=5)
                finally:
                    if attempted:
                        try:
                            await asyncio.wait_for(restore_lights(request, area.id, snapshots), timeout=20)
                            report("Sync stopped; saved lamp settings re-sent.")
                        except Exception:
                            report("Error: lamp restoration did not complete. Apply a static Hue scene to restore lighting.",
                                   cleanup_error=True)


class SyncError(RuntimeError):
    pass


def main():
    config = json.loads(sys.stdin.readline())
    app = QGuiApplication([])
    app.setApplicationName("Onigiri Hue screen sync")
    runtime = Runtime(config, app)
    signal.signal(signal.SIGTERM, lambda *_: runtime.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: runtime.stop_event.set())
    app.exec()
    runtime.thread.join(timeout=1)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        report("Error: screen-sync capture worker could not initialize. Check Qt Multimedia and optional dependencies.")
        raise SystemExit(1)
