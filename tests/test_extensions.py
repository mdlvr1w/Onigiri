import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from onigiri.extensions import ScenePayload, apply_scene, list_extensions, payload_from_scene
from onigiri.lights import apply_colors, load_colors
from onigiri.model import Scene

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "extensions"


def _write(folder: Path, name: str, source: str) -> Path:
    path = folder / name
    path.write_text(source, encoding="utf-8")
    return path


class ExtensionHostTests(unittest.TestCase):
    def test_empty_folder_paints_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            colors = folder / "colors.json"
            apply_colors((1, 2, 3), (4, 5, 6), colors_file=colors, extensions_dir=folder / "empty")
            self.assertFalse(colors.exists())

    def test_one_and_four_colors_reach_the_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sink = folder / "seen.json"
            _write(
                folder,
                "probe.py",
                "from pathlib import Path\n"
                f"SINK = Path({str(sink)!r})\n"
                "def on_scene(scene):\n"
                "    import json\n"
                "    rows = []\n"
                "    if SINK.exists():\n"
                "        rows = json.loads(SINK.read_text())\n"
                "    rows.append({k: list(v) for k, v in scene.colors.items()})\n"
                "    SINK.write_text(json.dumps(rows))\n",
            )
            apply_scene(ScenePayload(colors={"accent": (9, 8, 7)}), directory=folder)
            apply_scene(
                ScenePayload(colors={"a": (1, 0, 0), "b": (0, 1, 0), "c": (0, 0, 1), "d": (1, 1, 0)}),
                directory=folder,
            )
            rows = json.loads(sink.read_text(encoding="utf-8"))
            self.assertEqual(rows[0], {"accent": [9, 8, 7]})
            self.assertEqual(len(rows[1]), 4)

    def test_missing_color_names_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write(folder, "need_blue.py", "def on_scene(scene):\n    scene.colors.get('blue')\n")
            errors = apply_scene(ScenePayload(colors={"accent": (1, 2, 3)}), directory=folder)
            self.assertEqual(errors, [])

    def test_crashing_piece_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sink = folder / "ok.txt"
            _write(folder, "aa_boom.py", "def on_scene(scene):\n    raise RuntimeError('nope')\n")
            _write(folder, "zz_ok.py", f"def on_scene(scene):\n    open({str(sink)!r}, 'w').write('ok')\n")
            errors = apply_scene(ScenePayload(colors={"blue": (1, 2, 3)}), directory=folder)
            self.assertTrue(any("nope" in item for item in errors))
            self.assertEqual(sink.read_text(encoding="utf-8"), "ok")

    def test_per_scene_script_runs_after_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "ext"
            folder.mkdir()
            order = root / "order.txt"
            _write(
                folder,
                "ext.py",
                f"def on_scene(scene):\n    open({str(order)!r}, 'a').write('ext ')\n",
            )
            script = _write(
                root,
                "one_off.py",
                f"def on_scene(scene):\n    open({str(order)!r}, 'a').write('script')\n",
            )
            apply_scene(ScenePayload(script=str(script)), directory=folder)
            self.assertEqual(order.read_text(encoding="utf-8"), "ext script")

    def test_payload_from_scene_merges_aliases_and_extra_names(self) -> None:
        scene = Scene(
            id="s",
            name="Night",
            blue=(9, 8, 7),
            purple=(6, 5, 4),
            colors={"accent": (1, 2, 3)},
            extras={"colors": {"glow": [4, 5, 6]}},
            wallpaper="/tmp/w.png",
        )
        payload = payload_from_scene(scene)
        self.assertEqual(payload.colors["accent"], (1, 2, 3))
        self.assertEqual(payload.colors["glow"], (4, 5, 6))
        self.assertEqual(payload.colors["blue"], (9, 8, 7))
        self.assertEqual(payload.colors["purple"], (6, 5, 4))
        self.assertEqual(payload.wallpaper, "/tmp/w.png")
        defaulted = payload_from_scene(Scene(id="s", name="Day", blue=(9, 8, 7), purple=(6, 5, 4)))
        self.assertEqual(defaulted.colors["blue"], (9, 8, 7))
        self.assertEqual(defaulted.colors["purple"], (6, 5, 4))
        self.assertEqual(defaulted.colors["hardware_blue"], (9, 8, 7))
        with_plugins = payload_from_scene(
            Scene(id="s", name="P", plugins={"hue": {"blue": (1, 2, 3), "purple": (4, 5, 6)}})
        )
        self.assertEqual(with_plugins.plugins["hue"]["blue"], (1, 2, 3))

    def test_examples_write_independent_files_and_one_lianli_reload(self) -> None:
        reloads: list[int] = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            gpu = folder / "gpu.json"
            ram = folder / "ram.json"
            ryuo = folder / "ryuo.json"
            lianli = folder / "lianli.json"
            lianli.write_text(
                json.dumps(
                    {
                        "lcds": [
                            {
                                "type": "sensor",
                                "sensor": {
                                    "text_color": [0, 123, 255],
                                    "gauge_ranges": [{"max": 100, "color": [212, 0, 255]}],
                                },
                            },
                            {
                                "type": "image",
                                "sensor": {
                                    "text_color": [1, 2, 3],
                                    "gauge_ranges": [{"max": 100, "color": [4, 5, 6]}],
                                },
                            },
                        ],
                        "rgb": {
                            "devices": [
                                {
                                    "zones": [
                                        {
                                            "effect": {
                                                "mode": "TailChasing",
                                                "colors": [[0, 123, 255], [212, 0, 255]],
                                            }
                                        }
                                    ]
                                }
                            ]
                        },
                        "fans": [
                            {"mode": "TailChasing", "colors": [[0, 123, 255], [212, 0, 255]]},
                            {"mode": "Static", "colors": [[9, 9, 9], [8, 8, 8]]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            errors = apply_scene(
                ScenePayload(
                    plugins={
                        "gpu": {"blue": (1, 2, 3), "purple": (4, 5, 6)},
                        "ram": {"blue": (7, 8, 9), "purple": (10, 11, 12)},
                        "ryuo": {"blue": (13, 14, 15), "purple": (16, 17, 18)},
                        "lianli_fans": {
                            "led_blue": (10, 20, 30),
                            "led_purple": (40, 50, 60),
                            "text": (11, 22, 33),
                            "gauge": (44, 55, 66),
                        },
                    },
                    extras={
                        "gpu_file": gpu,
                        "ram_file": ram,
                        "ryuo_file": ryuo,
                        "lianli_file": lianli,
                        "reload_fn": lambda: reloads.append(1),
                        "paint_hue": False,
                    },
                ),
                directory=EXAMPLES,
            )
            self.assertEqual(errors, [])
            self.assertEqual(load_colors(gpu), ((1, 2, 3), (4, 5, 6)))
            self.assertEqual(load_colors(ram), ((7, 8, 9), (10, 11, 12)))
            self.assertEqual(load_colors(ryuo), ((13, 14, 15), (16, 17, 18)))
            data = json.loads(lianli.read_text(encoding="utf-8"))
            self.assertEqual(data["lcds"][0]["sensor"]["text_color"], [11, 22, 33])
            self.assertEqual(data["lcds"][0]["sensor"]["gauge_ranges"][0]["color"], [44, 55, 66])
            self.assertEqual(data["lcds"][1]["sensor"]["text_color"], [1, 2, 3])
            self.assertEqual(data["lcds"][1]["sensor"]["gauge_ranges"][0]["color"], [4, 5, 6])
            self.assertEqual(
                data["rgb"]["devices"][0]["zones"][0]["effect"]["colors"],
                [[10, 20, 30], [40, 50, 60]],
            )
            self.assertEqual(data["fans"][0]["colors"], [[0, 123, 255], [212, 0, 255]])
            self.assertEqual(data["fans"][1]["colors"], [[9, 9, 9], [8, 8, 8]])
            self.assertEqual(reloads, [1])

    def test_gpu_also_writes_the_shared_colors_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("pathlib.Path.home", return_value=home):
                errors = apply_scene(
                    ScenePayload(plugins={"gpu": {"blue": (1, 2, 3), "purple": (4, 5, 6)}}),
                    directory=EXAMPLES,
                )
            self.assertEqual(errors, [])
            pair = ((1, 2, 3), (4, 5, 6))
            folder = home / ".config" / "dual-color"
            self.assertEqual(load_colors(folder / "gpu.json"), pair)
            self.assertEqual(load_colors(folder / "colors.json"), pair)

    def test_example_helpers_are_not_listed(self) -> None:
        stems = [item.stem for item in list_extensions(EXAMPLES)]
        self.assertEqual(
            stems,
            ["gpu", "hue", "hue_sync", "lianli_fans", "ram", "ryuo"],
        )

    def test_missing_plugins_key_runs_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sink = folder / "ran.txt"
            _write(folder, "one.py", f"def on_scene(scene):\n    open({str(sink)!r}, 'a').write('one')\n")
            _write(folder, "two.py", f"def on_scene(scene):\n    open({str(sink)!r}, 'a').write('two')\n")
            apply_scene(ScenePayload(), directory=folder)
            self.assertEqual(sink.read_text(encoding="utf-8"), "onetwo")

    def test_empty_plugins_runs_nothing_in_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sink = folder / "ran.txt"
            _write(folder, "one.py", f"def on_scene(scene):\n    open({str(sink)!r}, 'w').write('one')\n")
            apply_scene(ScenePayload(plugins={}), directory=folder)
            self.assertFalse(sink.exists())

    def test_selected_stem_is_the_only_one_that_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sink = folder / "ran.txt"
            _write(
                folder,
                "hue.py",
                f"def on_scene(scene):\n    open({str(sink)!r}, 'a').write(str(scene.settings.get('blue')))\n",
            )
            _write(folder, "skip.py", f"def on_scene(scene):\n    open({str(sink)!r}, 'a').write('skip')\n")
            apply_scene(ScenePayload(plugins={"hue": {"blue": (1, 2, 3)}}), directory=folder)
            self.assertEqual(sink.read_text(encoding="utf-8"), "(1, 2, 3)")

    def test_fields_are_listed_and_a_crash_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write(
                folder,
                "hue.py",
                "title = 'Hue lamps'\n"
                "def fields():\n"
                "    return [{'id': 'blue', 'type': 'color', 'label': 'Cool'}]\n"
                "def on_scene(scene):\n"
                "    pass\n",
            )
            _write(
                folder,
                "aa_boom.py",
                "def fields():\n    raise RuntimeError('nope')\ndef on_scene(scene):\n    pass\n",
            )
            infos = {item.stem: item for item in list_extensions(folder)}
            self.assertEqual(infos["hue"].title, "Hue lamps")
            self.assertEqual(infos["hue"].fields[0].id, "blue")
            self.assertTrue(infos["aa_boom"].error)
            self.assertEqual(infos["aa_boom"].fields, [])

    def test_underscore_helpers_are_not_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write(folder, "_hue.py", "title = 'Helper'\ndef on_scene(scene):\n    raise RuntimeError('nope')\n")
            _write(folder, "hue.py", "title = 'Hue lamps'\ndef on_scene(scene):\n    pass\n")
            infos = list_extensions(folder)
            self.assertEqual([item.stem for item in infos], ["hue"])
            apply_scene(ScenePayload(), directory=folder)

    def test_wallpaper_is_on_the_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sink = folder / "wall.txt"
            _write(
                folder,
                "probe.py",
                f"def on_scene(scene):\n    open({str(sink)!r}, 'w').write(scene.wallpaper)\n",
            )
            apply_scene(payload_from_scene(Scene(id="s", name="N", wallpaper="/tmp/night.png")), directory=folder)
            self.assertEqual(sink.read_text(encoding="utf-8"), "/tmp/night.png")
