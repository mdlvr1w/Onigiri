"""Continuous-extension lifecycle, Hue credential migration and sync processing."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "extensions"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from PyQt6.QtGui import QColor, QImage
from _hue import HueSettings, load_hue, pair_entertainment, save_hue
from _hue_sync import SyncProcess, channel_colors, normalized_settings, sample_regions
from onigiri.extensions import ScenePayload, apply_scene, extension_status, list_extensions, stop_extensions

HAS_STREAMING = importlib.util.find_spec("hue_entertainment") is not None


class LifecycleTests(unittest.TestCase):
    def tearDown(self):
        stop_extensions()

    def test_continuous_updates_reuse_module_and_stop_before_static_paint(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            events = folder / "events"
            (folder / "continuous.py").write_text(
                "from pathlib import Path\n"
                f"sink = Path({str(events)!r})\n"
                "counter = 0\n"
                "def write(text):\n    with sink.open('a') as f: f.write(text + '\\n')\n"
                "def on_scene(scene):\n    global counter\n    counter += 1\n    write(str(counter))\n"
                "def on_stop(): write('stop')\n"
                "def on_status(): return 'running'\n")
            (folder / "static.py").write_text(
                "from pathlib import Path\n"
                f"def on_scene(scene):\n    with Path({str(events)!r}).open('a') as f: f.write('paint\\n')\n")
            for _ in range(2):
                self.assertEqual(apply_scene(ScenePayload(plugins={"continuous": {}}), directory=folder), [])
            self.assertEqual(extension_status(), ["continuous: running"])
            self.assertEqual(apply_scene(ScenePayload(plugins={"static": {}}), directory=folder), [])
            self.assertEqual(events.read_text().splitlines(), ["1", "2", "stop", "paint"])
            self.assertEqual(extension_status(), [])

    def test_explicit_only_never_starts_for_legacy_and_stops_on_empty_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            events = folder / "events"
            (folder / "continuous.py").write_text(
                "from pathlib import Path\nexplicit_only = True\n"
                f"sink = Path({str(events)!r})\n"
                "def on_scene(scene): sink.write_text('start')\n"
                "def on_stop(): sink.write_text('stop')\n")
            apply_scene(ScenePayload(), directory=folder)
            self.assertFalse(events.exists())
            apply_scene(ScenePayload(plugins={"continuous": {}}), directory=folder)
            self.assertEqual(events.read_text(), "start")
            apply_scene(ScenePayload(), directory=folder)
            self.assertEqual(events.read_text(), "stop")
            apply_scene(ScenePayload(plugins={"continuous": {}}), directory=folder)
            apply_scene(ScenePayload(plugins={}), directory=folder)
            self.assertEqual(events.read_text(), "stop")

    def test_failed_start_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            stopped = folder / "stopped"
            (folder / "fail.py").write_text(
                "from pathlib import Path\n"
                "def on_scene(scene): raise RuntimeError('start failed')\n"
                f"def on_stop(): Path({str(stopped)!r}).touch()\n")
            self.assertIn("start failed", apply_scene(ScenePayload(plugins={"fail": {}}), directory=folder)[0])
            self.assertTrue(stopped.exists())
            self.assertEqual(extension_status(), [])


class SyncTests(unittest.TestCase):
    def test_pair_upgrade_preserves_assignments_and_old_config(self):
        old = HueSettings(bridge_ip="127.0.0.1", username="old", blue=["1"], purple=["2"])
        upgraded = pair_entertainment(old, post=lambda: [{"success": {"username": "new", "clientkey": "ab" * 16}}])
        self.assertEqual(old.username, "old")
        self.assertEqual(upgraded.blue, ["1"])
        self.assertEqual(upgraded.purple, ["2"])
        self.assertEqual(upgraded.clientkey, "ab" * 16)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hue.json"
            save_hue(upgraded, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(load_hue(path), upgraded)
        with self.assertRaisesRegex(RuntimeError, "client key"):
            pair_entertainment(old, post=lambda: [{"success": {"username": "new"}}])
        self.assertEqual(old.username, "old")

    def test_black_borders_and_zones(self):
        image = QImage(48, 27, QImage.Format.Format_RGB32)
        image.fill(QColor("black"))
        for y in range(4, 23):
            for x in range(48):
                image.setPixelColor(x, y, QColor("red") if x < 24 else QColor("blue"))
        left, center, right = sample_regions(image)
        self.assertEqual(left, (255, 0, 0))
        self.assertEqual(right, (0, 0, 255))
        self.assertEqual(center, (127.5, 0, 127.5))
        image.fill(QColor("black"))
        self.assertEqual(sample_regions(image), ((0, 0, 0),) * 3)
        self.assertIsNone(sample_regions(QImage()))

    @unittest.skipUnless(HAS_STREAMING, "optional hue-entertainment dependency")
    def test_channel_mapping_brightness_smoothing_and_average(self):
        channels = [SimpleNamespace(channel_id=i, position=(x, 0, 0)) for i, x in enumerate((-1, 0, 1))]
        settings = normalized_settings({"brightness": 100, "smoothing": 0})
        regions = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
        previous = {}
        commands = channel_colors(channels, regions, settings, previous)
        self.assertEqual([(c.red, c.green, c.blue) for c in commands], [(65535, 0, 0), (0, 65535, 0), (0, 0, 65535)])
        settings.update(whole_screen=True, brightness=50, smoothing=50)
        commands = channel_colors(channels, regions, settings, previous)
        self.assertEqual((commands[0].red, commands[0].green), (32768, 16384))
        self.assertEqual(normalized_settings({"brightness": -10, "smoothing": 200})["brightness"], 1)
        self.assertEqual(normalized_settings({"smoothing": 200})["smoothing"], 95)

    def test_missing_key_never_launches_worker(self):
        process = SyncProcess()
        with patch('_hue_sync.load_hue', return_value=HueSettings(bridge_ip="127.0.0.1", username="old")), patch('_hue_sync.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, "--entertainment"):
                process.start({})
            spawn.assert_not_called()

    def test_defaults_and_controls_round_trip_and_reset_between_scenes(self):
        from tests.test_ui import app
        from onigiri.ui.scene_dialog import PluginPanel
        from onigiri.model import Scene
        app()
        info = next(i for i in list_extensions(EXAMPLES) if i.stem == 'hue_sync')
        self.assertTrue(info.continuous)
        self.assertTrue(info.explicit_only)
        panel = PluginPanel(info)
        self.assertEqual(panel.values()["brightness"], 60)
        panel.fill_from_scene(Scene(id="a", name="A", plugins={"hue_sync": {"area": "Desk", "brightness": 90, "whole_screen": True}}))
        self.assertEqual(panel.values()["area"], "Desk")
        self.assertTrue(panel.values()["whole_screen"])
        panel.fill_from_scene(Scene(id="b", name="B", plugins={"hue_sync": {}}))
        self.assertEqual(panel.values()["brightness"], 60)
        self.assertEqual(panel.values()["area"], "")
        self.assertFalse(panel.values()["whole_screen"])

    @unittest.skipUnless(HAS_STREAMING, "optional hue-entertainment dependency")
    def test_real_worker_reports_connection_failure_without_credentials(self):
        config = {"settings": {}, "hue": {"bridge_ip": "127.0.0.1", "username": "private-app-key", "clientkey": "ab" * 16}}
        result = subprocess.run([sys.executable, str(EXAMPLES / '_hue_sync_worker.py')], input=json.dumps(config) + '\n',
                                capture_output=True, text=True, timeout=20, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        self.assertIn('"status": "Error:', result.stdout)
        self.assertNotIn("private-app-key", result.stdout + result.stderr)
        self.assertNotIn("ab" * 16, result.stdout + result.stderr)


@unittest.skipUnless(HAS_STREAMING, "optional hue-entertainment dependency")
class StreamLifecycleTests(unittest.TestCase):
    def exercise(self, busy=False, start_error=False, cancel=False):
        from _hue_sync_worker import Runtime, SyncError
        runtime = Runtime.__new__(Runtime)
        # No Qt objects are constructed: exercise network sequencing using fake capture/bridge.
        runtime.config = {"settings": {}, "hue": {"bridge_ip": "127.0.0.1", "username": "test", "clientkey": "ab" * 16}}
        runtime.stop_event = threading.Event()
        runtime.lock = threading.Lock()
        runtime.regions = ((255, 0, 0),) * 3
        runtime.error = ""
        runtime.captureRequested = SimpleNamespace(emit=lambda: None)
        if cancel:
            runtime.stop_event.set()
        events = []
        area = SimpleNamespace(id="area", name="Desk", channels=[SimpleNamespace(service_id="ent", channel_id=0, position=(0, 0, 0))])

        class Stream:
            is_streaming = True
            def __init__(self, *a, **kw): pass
            async def get_entertainment_areas(self): return [area]
            async def start(self, *a, **kw):
                events.append("start")
                if start_error: raise RuntimeError("DTLS failed")
            def send(self, colors):
                events.append("send")
                runtime.stop_event.set()
            async def aclose(self): events.append("close")

        class Response:
            def __init__(self, method, url, body): self.method, self.url, self.body = method, url, body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def raise_for_status(self): pass
            async def json(self):
                if self.method == "PUT":
                    events.append(("restore", self.body))
                    return {"data": []}
                if self.url.endswith("entertainment_configuration"):
                    return {"data": [{"id": "area", "status": "active" if busy else "inactive"}]}
                if self.url.endswith("entertainment"):
                    return {"data": [{"id": "ent", "owner": {"rid": "device"}}]}
                return {"data": [{"id": "lamp", "owner": {"rid": "device"}, "on": {"on": False}, "dimming": {"brightness": 42}, "color": {"xy": {"x": 0.2, "y": 0.3}}}]}

        class HTTP:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def request(self, method, url, **kw): return Response(method, url, kw.get('json'))

        with patch('hue_entertainment.EntertainmentSession', Stream), patch('aiohttp.ClientSession', HTTP), patch('_hue_sync_worker.report'):
            if busy:
                with self.assertRaisesRegex(SyncError, "Another Hue"):
                    asyncio.run(runtime.stream())
            elif start_error:
                with self.assertRaisesRegex(RuntimeError, "DTLS failed"):
                    asyncio.run(runtime.stream())
            else:
                asyncio.run(runtime.stream())
        return events

    def test_stream_closes_before_restoring_previous_lighting(self):
        events = self.exercise()
        self.assertEqual(events[:3], ["start", "send", "close"])
        self.assertEqual(events[3][0], "restore")
        self.assertEqual(events[3][1]["on"], {"on": True})
        self.assertEqual(events[3][1]["dimming"], {"brightness": 0.1})
        self.assertEqual(len(events[4:]), 3)
        for _, body in events[4:]:
            self.assertEqual(body["on"], {"on": False})
            self.assertEqual(body["dimming"], {"brightness": 42})

    def test_busy_bridge_is_left_untouched(self):
        self.assertEqual(self.exercise(busy=True), ["close"])

    def test_cancelled_capture_does_not_activate_or_restore_lights(self):
        self.assertEqual(self.exercise(cancel=True), ["close"])

    def test_failed_start_still_restores_lights(self):
        events = self.exercise(start_error=True)
        self.assertEqual(events[:2], ["start", "close"])
        self.assertEqual(events[2][0], "restore")


@unittest.skipUnless(HAS_STREAMING, "optional hue-entertainment dependency")
class RestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_bridge_state_is_nudged_before_three_exact_restores(self):
        from copy import deepcopy
        from _hue_sync_worker import restore_lights
        original = {"on": {"on": True}, "dimming": {"brightness": 37}, "color": {"xy": {"x": 0.2, "y": 0.3}}}
        cached = deepcopy(original)
        physical = {"color": {"xy": {"x": 0.6, "y": 0.3}}}
        writes = []
        inactive_checks = 0
        delays = []
        async def pause(duration): delays.append(duration)
        async def request(method, resource, body=None):
            nonlocal cached, physical, inactive_checks
            if method == "GET":
                inactive_checks += 1
                return [{"id": "area", "status": "active" if inactive_checks == 1 else "inactive"}]
            self.assertGreaterEqual(inactive_checks, 2)
            writes.append(deepcopy(body))
            # Model a bridge that accepts duplicate commands but leaves streamed LEDs unchanged.
            if body != cached:
                physical = deepcopy(body)
                cached = deepcopy(body)
            return []
        snapshots = {"lamp": deepcopy(original)}
        await restore_lights(request, "area", snapshots, pause=pause)
        self.assertNotEqual(writes[0]["color"], original["color"])
        self.assertEqual(writes[1:], [original] * 3)
        self.assertEqual(physical, original)
        self.assertEqual(snapshots["lamp"], original)
        self.assertIn(0.3, delays)

    async def test_failed_nudge_does_not_skip_restoring_original_state(self):
        from _hue_sync_worker import restore_lights
        writes = []
        async def pause(_): pass
        async def request(method, resource, body=None):
            if method == "GET": return [{"id": "area", "status": "inactive"}]
            writes.append(body)
            if len(writes) == 1: raise RuntimeError("transient failure")
            return []
        original = {"on": {"on": False}, "dimming": {"brightness": 42}}
        await restore_lights(request, "area", {"lamp": original}, pause=pause)
        self.assertEqual(writes[1:], [original] * 3)

    async def test_all_restore_failures_are_reported(self):
        from _hue_sync_worker import restore_lights, SyncError
        async def pause(_): pass
        async def request(method, resource, body=None):
            if method == "GET": return [{"id": "area", "status": "inactive"}]
            raise RuntimeError("bridge unreachable")
        with self.assertRaisesRegex(SyncError, "could not be restored"):
            await restore_lights(request, "area", {"lamp": {"on": {"on": False}}}, pause=pause)

    async def test_stop_surfaces_worker_cleanup_errors(self):
        from unittest.mock import Mock
        worker = SyncProcess()
        worker.process = Mock()
        worker.cleanup_errors.append("Error: lamp restoration did not complete.")
        with self.assertRaisesRegex(RuntimeError, "restoration did not complete"):
            worker.stop()
        self.assertIsNone(worker.process)
