"""Optional and solid-color backgrounds across scene storage, editing and apply."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QDialog

from onigiri.extensions import payload_from_scene
from onigiri.model import Scene, Config
from onigiri.store import load_config, save_config
from onigiri.wallpaper import read_desktop_wallpapers, set_scene_wallpaper, wallpaper_for
from onigiri.ui.scene_dialog import SceneDialog
from onigiri.ui.overlay import ScenePanel
from onigiri.ui.main_window import MainWindow
from tests.test_ui import app, _png


class SceneBackgroundTests(unittest.TestCase):
    def test_new_scene_can_be_created_without_a_picture(self):
        app()
        dialog = SceneDialog()
        self.assertTrue(dialog.next_button.isEnabled())
        dialog._next()
        dialog._next()
        dialog.name_edit.setText("Lighting only")
        dialog._next()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        scene = dialog.built()
        self.assertEqual(scene.wallpaper, "")
        self.assertIsNone(scene.background_color)
        self.assertEqual(scene.name, "Lighting only")
        dialog.deleteLater()

    def test_picture_mode_still_requires_a_valid_file(self):
        app()
        dialog = SceneDialog()
        dialog.background_selector.mode.setCurrentIndex(1)
        self.assertFalse(dialog.next_button.isEnabled())
        dialog._finish()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "picture.png")
            dialog._set_picture(str(image))
            self.assertTrue(dialog.next_button.isEnabled())
            self.assertEqual(dialog.built().wallpaper, str(image))
            self.assertIsNone(dialog.built().background_color)
        dialog.deleteLater()

    def test_solid_color_creator_editor_and_storage_round_trip(self):
        app()
        dialog = SceneDialog()
        dialog.background_selector.mode.setCurrentIndex(2)
        dialog.background_selector.color_swatch.set_color(QColor(12, 34, 56))
        scene = dialog.built()
        self.assertEqual(scene.background_color, (12, 34, 56))
        self.assertEqual(scene.wallpaper, "")
        self.assertEqual(dialog.preview._pixmap.toImage().pixelColor(0, 0), QColor(12, 34, 56))
        editor = ScenePanel()
        editor.load(scene)
        self.assertEqual(editor.built().background_color, (12, 34, 56))
        reopened = SceneDialog(scene)
        self.assertEqual(reopened.background_selector.mode.currentData(), "color")
        self.assertEqual(reopened.built().background_color, (12, 34, 56))
        reopened.deleteLater()
        editor.background_selector.mode.setCurrentIndex(0)
        self.assertIsNone(editor.built().background_color)
        self.assertEqual(editor.built().wallpaper, "")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            save_config(Config(scenes=[scene]), path)
            self.assertEqual(load_config(path).scenes[0].background_color, (12, 34, 56))
        self.assertEqual(payload_from_scene(scene).background_color, (12, 34, 56))
        self.assertIsNone(Scene.from_dict({"name": "Old scene"}).background_color)
        self.assertIsNone(Scene.from_dict({"background_color": [999, 0, 0]}).background_color)
        dialog.deleteLater()
        editor.deleteLater()

    def test_solid_color_uses_plasma_color_plugin_without_rendering_an_image(self):
        scripts = []
        with patch("onigiri.wallpaper.write_cover_crop") as render:
            set_scene_wallpaper("", [("screen", 0, 0, 1920, 1080)], background_color=(12, 34, 56),
                                run=lambda script: scripts.append(script) or True)
            render.assert_not_called()
        self.assertEqual(len(scripts), 1)
        self.assertIn('d.wallpaperPlugin = "org.kde.color"', scripts[0])
        self.assertIn('d.writeConfig("Color", "#0c2238")', scripts[0])
        with self.assertRaisesRegex(RuntimeError, "background color"):
            set_scene_wallpaper("", [], background_color=(0, 0, 0), run=lambda _: False)

    def test_keep_background_is_a_noop_and_still_runs_extensions(self):
        app()
        with patch("onigiri.wallpaper.evaluate_plasma") as plasma:
            set_scene_wallpaper("", [])
            plasma.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            wallpapers, lights = [], []
            window = MainWindow(folder / "config.json", folder / "rules", folder / "autostart",
                                reconfigure_fn=lambda: None,
                                set_wallpaper_fn=lambda *args: wallpapers.append(args),
                                apply_colors_fn=lambda *args, **kwargs: lights.append(args))
            scene = Scene(id="lights", name="Lights", plugins={})
            window.config.scenes = [scene]
            window._apply_scene(scene)
            self.assertEqual(wallpapers, [])
            self.assertEqual(len(lights), 1)
            self.assertEqual(window.scene_state.text(), "Applied scene: Lights")
            scene.background_color = (12, 34, 56)
            window._apply_scene(scene)
            self.assertEqual(wallpapers[-1][0], "#0c2238")
            self.assertTrue(any(v.background == "#0c2238" for v in window.canvas.views))
            window._quitting = True
            window.close()

    def test_creator_result_without_image_is_kept_by_main_window(self):
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(folder / "config.json", folder / "rules", folder / "autostart",
                                reconfigure_fn=lambda: None, apply_colors_fn=lambda *a, **k: None)
            scene = Scene(id="no-wallpaper", name="Only lighting", plugins={})
            with patch("onigiri.ui.main_window.SceneDialog") as constructor:
                constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
                constructor.return_value.deleted = False
                constructor.return_value.built.return_value = scene
                window._create_scene()
            self.assertIn(scene, window.config.scenes)
            self.assertEqual(window.scene_state.text(), "Applied scene: Only lighting")
            window._quitting = True
            window.close()

    def test_existing_plasma_solid_background_is_read_for_the_canvas(self):
        text = """[Containments][1]
plugin=org.kde.plasma.folder
lastScreen=0
wallpaperplugin=org.kde.color
[Containments][1][Wallpaper][org.kde.color][General]
Color=12,34,56
"""
        wallpapers = read_desktop_wallpapers(text)
        self.assertEqual(wallpapers[0].path, "#0c2238")
        self.assertEqual(wallpaper_for([("screen", 0, 0, 1920, 1080)], config_text=text, query_screens=False),
                         {"screen": ("#0c2238", "stretch")})
        self.assertEqual(read_desktop_wallpapers(text.replace("12,34,56", "#0c2238"))[0].path, "#0c2238")
