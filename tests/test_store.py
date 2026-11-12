import json
import tempfile
import unittest
from pathlib import Path

from onigiri.model import Config, iter_tiles
from onigiri.store import import_old, load_config, needs_import, save_config


OLD = {
    "profiles": [
        {
            "name": "dashboard-3pane",
            "monitor": "HDMI-1",
            "tile_gap": 4,
            "panel_position": "top",
            "panel_size": 29,
            "tiles": [
                {
                    "name": "BTOP",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                    "launch_mode": "helper",
                    "shell_command": "btop",
                    "terminal_app": "alacritty",
                    "no_border": True,
                    "match": {"type": "class", "value": "btop"},
                },
                {
                    "name": "FAST",
                    "x": 100,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                    "launch_mode": "app",
                    "command": "firefox",
                    "app_id": "firefox.desktop",
                    "app_name": "Firefox",
                    "match": {"type": "title", "value": "Firefox"},
                },
            ],
        }
    ]
}


class StoreTests(unittest.TestCase):
    def test_old_config_becomes_a_split_tree(self) -> None:
        self.assertTrue(needs_import(OLD))
        config = import_old(OLD)
        profile = config.profiles[0]
        self.assertEqual(profile.name, "dashboard-3pane")
        self.assertEqual(profile.current, "Imported")
        screen = profile.layouts["Imported"].screens["HDMI-1"]
        self.assertEqual(screen.gap, 4)
        tiles = {tile.name: tile for tile in iter_tiles(screen.root)}
        self.assertEqual(set(tiles), {"BTOP", "FAST"})
        self.assertEqual(tiles["BTOP"].shell_command, "btop")
        self.assertTrue(tiles["BTOP"].no_border)
        self.assertEqual(tiles["FAST"].app_id, "firefox.desktop")
        saved = profile.to_dict()
        self.assertNotIn("panel_position", json.dumps(saved))
        self.assertNotIn("gap", saved)
        self.assertNotIn("monitors", saved)

    def test_import_backs_up_the_old_file_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            path.write_text(json.dumps(OLD), encoding="utf-8")
            loaded = load_config(path)
            backup = Path(str(path) + ".bak")
            self.assertTrue(backup.exists())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["profiles"][0]["name"], "dashboard-3pane")
            self.assertEqual(loaded.profiles[0].name, "dashboard-3pane")
            again = load_config(path)
            self.assertEqual(again.to_dict()["version"], 2)
            self.assertFalse(needs_import(json.loads(path.read_text(encoding="utf-8"))))

    def test_placeholder_screen_is_dropped_on_load(self) -> None:
        data = {
            "version": 2,
            "profiles": [
                {
                    "name": "default",
                    "gap": 8,
                    "monitors": {
                        "TEST-PORTRAIT": {
                            "current": "Default",
                            "layouts": {
                                "Default": {
                                    "root": {
                                        "type": "leaf",
                                        "tile": {"id": "e1", "name": "Tile 1", "launch_mode": "raw"},
                                    }
                                }
                            },
                        },
                        "TEST-HDMI": {
                            "current": "Default",
                            "layouts": {
                                "Default": {
                                    "root": {
                                        "type": "leaf",
                                        "tile": {
                                            "id": "bt",
                                            "name": "btop",
                                            "launch_mode": "helper",
                                            "shell_command": "btop",
                                        },
                                    }
                                }
                            },
                        },
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = load_config(path)
            screens = loaded.profiles[0].layouts["Default"].screens
            self.assertNotIn("TEST-PORTRAIT", screens)
            self.assertIn("TEST-HDMI", screens)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("monitors", saved["profiles"][0])
            self.assertNotIn("gap", saved["profiles"][0])
            self.assertNotIn("TEST-PORTRAIT", saved["profiles"][0]["layouts"]["Default"]["screens"])

    def test_empty_leaf_screen_is_dropped_on_load(self) -> None:
        data = {
            "version": 2,
            "profiles": [
                {
                    "name": "default",
                    "current": "Default",
                    "layouts": {
                        "Default": {
                            "screens": {
                                "TEST-WIDE": {
                                    "gap": 15,
                                    "root": {"type": "leaf", "slot": "empty", "tile": None},
                                },
                                "TEST-HDMI": {
                                    "gap": 15,
                                    "root": {
                                        "type": "leaf",
                                        "tile": {
                                            "id": "bt",
                                            "name": "btop",
                                            "launch_mode": "helper",
                                            "shell_command": "btop",
                                        },
                                    },
                                },
                            }
                        }
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = load_config(path)
            screens = loaded.profiles[0].layouts["Default"].screens
            self.assertNotIn("TEST-WIDE", screens)
            self.assertIn("TEST-HDMI", screens)

    def test_round_trip(self) -> None:
        config = import_old(OLD)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.to_dict(), config.to_dict())
            self.assertEqual(loaded.hotkey, "")

    def test_scenes_round_trip_without_touching_layouts(self) -> None:
        from onigiri.model import Config, Leaf, Profile, Scene, ScreenLayout, Tile

        profile = Profile(name="default")
        profile.layouts["Default"].screens["TEST-HDMI"] = ScreenLayout(
            root=Leaf(Tile(id="saved", name="btop")),
            gap=15,
        )
        config = Config(
            profiles=[profile],
            scenes=[
                Scene(
                    id="sc1",
                    name="Night",
                    wallpaper="/tmp/night.png",
                    blue=(1, 2, 3),
                    purple=(4, 5, 6),
                    wallpaper_pan={"TEST-PORTRAIT": (0.0, 0.5)},
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.scenes[0].name, "Night")
            self.assertEqual(loaded.scenes[0].wallpaper, "/tmp/night.png")
            self.assertEqual(loaded.scenes[0].blue, (1, 2, 3))
            self.assertEqual(loaded.scenes[0].purple, (4, 5, 6))
            self.assertEqual(loaded.scenes[0].case_blue(), (1, 2, 3))
            self.assertEqual(loaded.scenes[0].case_purple(), (4, 5, 6))
            self.assertEqual(loaded.scenes[0].wallpaper_pan["TEST-PORTRAIT"], (0.0, 0.5))
            self.assertEqual(loaded.profiles[0].layouts["Default"].screens["TEST-HDMI"].root.tile.name, "btop")
            self.assertEqual(Config.from_dict({"version": 2, "profiles": [profile.to_dict()]}).scenes, [])

    def test_scene_colors_extras_and_script_round_trip(self) -> None:
        from onigiri.model import Config, Scene

        config = Config(
            scenes=[
                Scene(
                    id="sc1",
                    name="Night",
                    colors={"accent": (9, 8, 7), "glow": (1, 2, 3)},
                    extras={"mode": "picture"},
                    script="/tmp/one_off.py",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.scenes[0].colors["accent"], (9, 8, 7))
            self.assertEqual(loaded.scenes[0].colors["glow"], (1, 2, 3))
            self.assertEqual(loaded.scenes[0].extras["mode"], "picture")
            self.assertEqual(loaded.scenes[0].script, "/tmp/one_off.py")

    def test_scene_plugins_round_trip(self) -> None:
        from onigiri.model import Config, Scene

        config = Config(
            scenes=[
                Scene(
                    id="sc1",
                    name="Night",
                    plugins={"hue": {"blue": (1, 2, 3), "purple": (4, 5, 6)}, "dual_color": {}},
                ),
                Scene(id="sc2", name="Empty", plugins={}),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.scenes[0].plugins["hue"]["blue"], (1, 2, 3))
            self.assertEqual(loaded.scenes[0].plugins["gpu"], {})
            self.assertEqual(loaded.scenes[0].plugins["ram"], {})
            self.assertEqual(loaded.scenes[0].plugins["ryuo"], {})
            self.assertNotIn("dual_color", loaded.scenes[0].plugins)
            self.assertEqual(loaded.scenes[1].plugins, {})
            self.assertIsNone(Scene.from_dict({"id": "x", "name": "Legacy"}).plugins)

    def test_legacy_plugin_stems_migrate(self) -> None:
        from onigiri.model import Scene

        scene = Scene.from_dict(
            {
                "id": "sc1",
                "name": "Night",
                "plugins": {
                    "dual_color": {"blue": [1, 2, 3], "purple": [4, 5, 6]},
                    "lianli_fan_leds": {"blue": [10, 20, 30], "purple": [40, 50, 60]},
                    "lianli_fan_displays": {"blue": [11, 22, 33], "purple": [44, 55, 66]},
                },
            }
        )
        self.assertEqual(scene.plugins["gpu"]["blue"], (1, 2, 3))
        self.assertEqual(scene.plugins["ram"]["purple"], (4, 5, 6))
        self.assertEqual(scene.plugins["ryuo"]["blue"], (1, 2, 3))
        self.assertEqual(scene.plugins["lianli_fans"]["led_blue"], (10, 20, 30))
        self.assertEqual(scene.plugins["lianli_fans"]["led_purple"], (40, 50, 60))
        self.assertEqual(scene.plugins["lianli_fans"]["text"], (11, 22, 33))
        self.assertEqual(scene.plugins["lianli_fans"]["gauge"], (44, 55, 66))
        self.assertNotIn("dual_color", scene.plugins)
        self.assertNotIn("lianli_fan_leds", scene.plugins)
        self.assertNotIn("lianli_fan_displays", scene.plugins)
        again = Scene.from_dict(scene.to_dict())
        self.assertEqual(again.plugins["lianli_fans"]["gauge"], (44, 55, 66))
        self.assertEqual(again.plugins["gpu"]["blue"], (1, 2, 3))

    def test_hotkey_round_trips(self) -> None:
        data = {
            "version": 2,
            "hotkey": "Meta+Space",
            "profiles": [{"name": "default", "current": "Default", "layouts": {"Default": {"screens": {}}}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = load_config(path)
            self.assertEqual(loaded.hotkey, "Meta+Space")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved.get("hotkey"), "Meta+Space")

    def test_mouse_hotkey_round_trips(self) -> None:
        data = {
            "version": 2,
            "hotkey": "Ctrl+Middle",
            "profiles": [{"name": "default", "current": "Default", "layouts": {"Default": {"screens": {}}}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = load_config(path)
            self.assertEqual(loaded.hotkey, "Ctrl+Middle")

    def test_overlay_round_trips_without_touching_layouts(self) -> None:
        from onigiri.model import Leaf, Profile, ScreenLayout, Tile

        profile = Profile(name="default")
        profile.layouts["Default"].screens["TEST-HDMI"] = ScreenLayout(
            root=Leaf(Tile(id="saved", name="btop")),
            gap=15,
        )
        profile.overlays["TEST-HDMI"] = ScreenLayout(root=Leaf(Tile(id="over", name="Firefox")))
        data = {"version": 2, "profiles": [profile.to_dict()]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = load_config(path)
            saved = loaded.profiles[0]
            self.assertEqual(saved.layouts["Default"].screens["TEST-HDMI"].root.tile.name, "btop")
            self.assertEqual(saved.overlays["TEST-HDMI"].root.tile.name, "Firefox")

    def test_legacy_monitors_become_profile_layouts(self) -> None:
        data = {
            "version": 2,
            "profiles": [
                {
                    "name": "default",
                    "gap": 15,
                    "monitors": {
                        "TEST-HDMI": {
                            "current": "Default",
                            "x": 240,
                            "y": 0,
                            "width": 1920,
                            "height": 1080,
                            "layouts": {
                                "Default": {
                                    "root": {
                                        "type": "split",
                                        "orientation": "v",
                                        "ratio": 0.5,
                                        "first": {
                                            "type": "leaf",
                                            "tile": {
                                                "id": "4c6bf737",
                                                "name": "btop",
                                                "launch_mode": "helper",
                                                "shell_command": "btop",
                                            },
                                        },
                                        "second": {
                                            "type": "split",
                                            "orientation": "h",
                                            "ratio": 0.5,
                                            "first": {
                                                "type": "leaf",
                                                "tile": {"id": "52f72897", "name": "fastfetch"},
                                            },
                                            "second": {"type": "leaf", "slot": "5561e963", "tile": None},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            ],
        }
        profile = Config.from_dict(data).profiles[0]
        self.assertEqual(profile.current, "Default")
        self.assertEqual(list(profile.layouts), ["Default"])
        screen = profile.layouts["Default"].screens["TEST-HDMI"]
        self.assertEqual(screen.gap, 15)
        self.assertEqual(screen.x, 240)
        names = [tile.name for tile in iter_tiles(screen.root)]
        self.assertEqual(names, ["btop", "fastfetch"])
        saved = profile.to_dict()
        self.assertNotIn("gap", saved)
        self.assertNotIn("monitors", saved)
        self.assertEqual(saved["layouts"]["Default"]["screens"]["TEST-HDMI"]["gap"], 15)
        self.assertNotIn("TEST-PORTRAIT", saved["layouts"]["Default"]["screens"])
        self.assertNotIn("TEST-WIDE", saved["layouts"]["Default"]["screens"])


if __name__ == "__main__":
    unittest.main()
