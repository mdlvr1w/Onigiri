import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "extensions"
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

from _hue import (  # noqa: E402
    HueBridge,
    HueLight,
    HueSettings,
    LinkButtonNeeded,
    assign_light,
    color_taken,
    discover_bridges,
    light_options,
    list_lights,
    load_hue,
    paint_hue,
    pair_bridge,
    rgb_to_xy,
    save_hue,
    set_light_color,
)
from onigiri.lights import apply_colors


class HueTests(unittest.TestCase):
    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hue.json"
            save_hue(HueSettings(bridge_ip="192.0.2.10", username="abc", blue=["1"], purple=["2", "3"]), path)
            loaded = load_hue(path)
            self.assertEqual(loaded.bridge_ip, "192.0.2.10")
            self.assertEqual(loaded.username, "abc")
            self.assertEqual(loaded.blue, ["1"])
            self.assertEqual(loaded.purple, ["2", "3"])
            self.assertTrue(loaded.paired())
            self.assertFalse(load_hue(Path(tmp) / "missing.json").paired())

    def test_discover_and_pair(self) -> None:
        bridges = discover_bridges(
            meethue=lambda: ["192.0.2.10"],
            ssdp=lambda: [],
            probe=lambda ip: HueBridge(ip=ip, name="Hue Bridge", bridge_id="abc"),
        )
        self.assertEqual(bridges[0].ip, "192.0.2.10")
        username = pair_bridge("192.0.2.10", post=lambda: [{"success": {"username": "token"}}])
        self.assertEqual(username, "token")
        with self.assertRaises(LinkButtonNeeded):
            pair_bridge("192.0.2.10", post=lambda: [{"error": {"type": 101, "description": "link button not pressed"}}])

    def test_list_and_paint_assigned_lights(self) -> None:
        settings = HueSettings(bridge_ip="192.0.2.10", username="token", blue=["10"], purple=["11"])
        lights = list_lights(
            settings,
            fetch=lambda: {
                "11": {"name": "Right", "state": {"reachable": True}},
                "10": {"name": "Left", "state": {"reachable": True}},
                "12": {"name": "Lamp", "state": {"reachable": False}},
            },
        )
        self.assertEqual([light.name for light in lights], ["Lamp", "Left", "Right"])
        self.assertEqual([light.id for light in lights], ["12", "10", "11"])
        painted: list[tuple[str, dict]] = []
        paint_hue((0, 123, 255), (212, 0, 255), settings=settings, put=lambda light_id, payload: painted.append((light_id, payload)))
        ids = [item[0] for item in painted]
        self.assertEqual(ids, ["10", "11"])
        self.assertTrue(all(item[1]["on"] for item in painted))
        self.assertEqual(painted[0][1]["transitiontime"], 0)
        x_pos, y_pos, bri = rgb_to_xy((0, 123, 255))
        self.assertEqual(painted[0][1]["xy"], [x_pos, y_pos])
        self.assertEqual(bri, 254)
        self.assertEqual(painted[0][1]["bri"], 254)
        self.assertEqual(painted[1][1]["bri"], 254)
        self.assertNotEqual(painted[0][1]["xy"], painted[1][1]["xy"])
        purple = rgb_to_xy((212, 0, 255))
        self.assertEqual(painted[1][1]["xy"], [purple[0], purple[1]])
        self.assertEqual(rgb_to_xy((20, 0, 40))[2], 254)

    def test_retries_until_the_lamp_takes_the_color(self) -> None:
        settings = HueSettings(bridge_ip="192.0.2.10", username="token")
        x_pos, y_pos, brightness = rgb_to_xy((0, 123, 255))
        payload = {"on": True, "xy": [x_pos, y_pos], "bri": brightness}
        self.assertFalse(color_taken({"on": True, "reachable": False, "xy": [x_pos, y_pos], "bri": brightness}, payload))
        self.assertFalse(color_taken({"on": True, "reachable": True, "xy": [0.9, 0.9], "bri": brightness}, payload))
        self.assertTrue(color_taken({"on": True, "reachable": True, "xy": [x_pos + 0.01, y_pos], "bri": brightness - 2}, payload))
        puts: list[dict] = []
        replies = [
            {"on": True, "reachable": True, "xy": [0.1, 0.2], "bri": brightness},
            {"on": True, "reachable": False, "xy": [x_pos, y_pos], "bri": brightness},
            {"on": True, "reachable": True, "xy": [x_pos, y_pos], "bri": brightness},
        ]

        def get(_light_id: str):
            return replies.pop(0)

        set_light_color(
            settings,
            "10",
            (0, 123, 255),
            put=lambda _light_id, payload: puts.append(payload),
            get=get,
            wait=0,
            attempts=2,
        )
        self.assertTrue(any("hue" in item and "xy" not in item for item in puts))
        self.assertGreaterEqual(sum(1 for item in puts if item.get("xy") == [x_pos, y_pos]), 2)
        self.assertEqual(replies, [])

    def test_kicks_a_lamp_that_already_reports_the_color(self) -> None:
        settings = HueSettings(bridge_ip="192.0.2.10", username="token")
        x_pos, y_pos, brightness = rgb_to_xy((0, 123, 255))
        matching = {"on": True, "reachable": True, "xy": [x_pos, y_pos], "bri": brightness}
        puts: list[dict] = []
        set_light_color(
            settings,
            "10",
            (0, 123, 255),
            put=lambda _light_id, payload: puts.append(payload),
            get=lambda _light_id: matching,
            wait=0,
            attempts=1,
        )
        self.assertTrue(any(item.get("xy") and item["xy"] != [x_pos, y_pos] for item in puts))
        self.assertTrue(any("hue" in item and "xy" not in item for item in puts))
        self.assertTrue(any(item.get("xy") == [x_pos, y_pos] for item in puts))

    def test_apply_colors_paints_hue(self) -> None:
        painted: list[tuple] = []
        examples = Path(__file__).resolve().parents[1] / "examples" / "extensions"
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            apply_colors(
                (1, 2, 3),
                (4, 5, 6),
                colors_file=folder / "colors.json",
                lianli_file=folder / "missing.json",
                hue_file=folder / "hue.json",
                reload_fn=lambda: None,
                hue_fn=lambda blue, purple, path: painted.append((blue, purple, path)),
                extensions_dir=examples,
            )
            self.assertEqual(painted, [((1, 2, 3), (4, 5, 6), folder / "hue.json")])

    def test_taken_lights_are_not_selectable(self) -> None:
        settings = HueSettings(blue=["10"], purple=["11"])
        lights = [HueLight("10", "Left"), HueLight("11", "Right"), HueLight("12", "Desk")]
        purple = {light.id: (checked, enabled) for light, checked, enabled in light_options(settings, lights, "purple")}
        blue = {light.id: (checked, enabled) for light, checked, enabled in light_options(settings, lights, "blue")}
        self.assertEqual(purple["11"], (True, True))
        self.assertEqual(purple["10"], (False, False))
        self.assertEqual(purple["12"], (False, True))
        self.assertEqual(blue["10"], (True, True))
        self.assertEqual(blue["11"], (False, False))
        self.assertFalse(assign_light(settings, "10", "purple"))
        self.assertEqual(settings.blue, ["10"])
        self.assertTrue(assign_light(settings, "11", None))
        self.assertEqual(settings.purple, [])
        self.assertTrue(assign_light(settings, "12", "purple"))
        self.assertEqual(settings.purple, ["12"])
