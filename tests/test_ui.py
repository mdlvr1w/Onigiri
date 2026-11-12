import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QContextMenuEvent, QGuiApplication, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication, QDialog, QMenu, QMessageBox, QWidget

from onigiri.model import Leaf, Scene, ScreenLayout, Split, Tile, find_leaf, iter_tiles
from onigiri.ui.canvas import LayoutCanvas, MonitorView, _SCREEN_GUTTER, monitor_at, preview_size
from onigiri.ui.filmstrip import Filmstrip
from onigiri.ui.hotkey_edit import HotkeyBuilder, token_from_key, token_from_mouse
from onigiri.ui.inspector import FontSizeSpin, Inspector
from onigiri.extensions import ExtensionField, ExtensionInfo
from onigiri.lights import DEFAULT_BLUE, DEFAULT_PURPLE
from onigiri.ui.scene_dialog import PluginPanel, SceneDialog
from onigiri.ui import main_window as main_window_module
from onigiri.ui.main_window import MainWindow
from onigiri.ui.theme import PANEL_ALPHA, PANEL_BORDER, WINDOW_ALPHA, apply_theme
from onigiri.hotkey import combined_keys, parse_hotkey, sequence_from_text, sequence_to_text
from onigiri.launch import DesktopApp
from onigiri.ui.popup import POPUP_SIZE, CursorPopup, popup_position
from onigiri.ui.popup_tiler import AppPicker, PopupTiler, apply_app, overlay_tile
from onigiri.ui.rules_dialog import RulesDialog

_APP = None


def app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        apply_theme(_APP)
    return _APP


class UiTests(unittest.TestCase):
    def test_click_split_edit_and_apply(self) -> None:
        spawned: list[tuple[str, bool]] = []
        order: list[str] = []
        closed: list[set[str]] = []

        def fake_spawn(command: str, use_shell: bool) -> None:
            order.append("spawn")
            spawned.append((command, use_shell))

        def fake_close(match_values: set[str]) -> bool:
            order.append("close")
            closed.append(set(match_values))
            return True

        original = main_window_module.spawn
        main_window_module.spawn = fake_spawn
        app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                calls: list[int] = []
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: calls.append(1),
                    close_windows_fn=fake_close,
                )
                window.resize(1180, 760)
                window.show()
                app().processEvents()
                self.assertEqual(window.profile_combo.count(), 1)
                self.assertTrue(window.filmstrip.isVisible())
                self.assertTrue(window.filmstrip.plus.isVisible())
                self.assertEqual(window.current_profile().current_layout().screens, {})
                screen = window.current_screen()
                self.assertTrue(screen is None or screen.root is None)
                window.add_first_tile(window.monitor_name)
                app().processEvents()
                self.assertIsNotNone(window.current_screen().root)
                self.assertEqual(
                    [window.layout_combo.itemText(i) for i in range(window.layout_combo.count())],
                    ["Default"],
                )
                tiled = window.monitor_name
                window._activate_monitor("DP-9")
                window._on_tiles_selected([])
                app().processEvents()
                self.assertTrue(window.overlay.isVisible())
                self.assertEqual(window.overlay.current_page(), "hint")
                self.assertIsNone(window.current_screen() and window.current_screen().root)
                self.assertEqual(
                    [window.layout_combo.itemText(i) for i in range(window.layout_combo.count())],
                    ["Default"],
                )
                window._activate_monitor(tiled)
                app().processEvents()
                self.assertIsNotNone(window.current_screen().root)
                window.save_layout()
                app().processEvents()
                self.assertIn("Saved layout", window.statusBar().currentMessage())
                self.assertTrue((folder / "onigiri.json").exists())
                self.assertGreater(window.canvas.width(), 100)

                QTest.mouseClick(
                    window.canvas,
                    Qt.MouseButton.LeftButton,
                    pos=QPoint(window.canvas.width() // 2, window.canvas.height() // 2),
                )
                app().processEvents()
                self.assertIsNotNone(window.selected_tile_id)

                window.inspector.name_edit.setText("Left")
                app().processEvents()
                layout = window.current_screen()
                self.assertIsNotNone(layout)
                assert layout is not None
                selected = find_leaf(layout.root, window.selected_tile_id or "")
                self.assertIsNotNone(selected)
                assert selected is not None
                self.assertEqual(selected.tile.name, "Left")

                window.split_current("v")
                layout = window.current_screen()
                assert layout is not None
                self.assertEqual(len(list(iter_tiles(layout.root))), 2)
                window.undo()
                layout = window.current_screen()
                assert layout is not None
                self.assertEqual(len(list(iter_tiles(layout.root))), 1)
                window.redo()
                layout = window.current_screen()
                assert layout is not None
                tiles = list(iter_tiles(layout.root))
                self.assertEqual(len(tiles), 2)
                window.selected_tile_id = tiles[0].id

                leaf = find_leaf(layout.root, window.selected_tile_id or "")
                assert leaf is not None
                leaf.tile.launch_mode = "raw"
                leaf.tile.command = "echo hi"
                leaf.tile.match_kind = "class"
                leaf.tile.match_value = "demo-class"
                leaf.tile.no_border = True
                window._sync_inspector()

                count = window.apply_rules()
                self.assertEqual(count, 1)
                text = (folder / "kwinrulesrc").read_text(encoding="utf-8")
                self.assertIn("Onigiri:", text)
                self.assertIn("positionrule=2", text)
                self.assertIn("wmclass=demo-class", text)
                self.assertIn("noborder=true", text)
                self.assertTrue(calls)

                window.launch_tiles()
                QTest.qWait(450)
                self.assertEqual(order[:2], ["close", "spawn"])
                self.assertEqual(closed, [{"demo-class"}])
                self.assertTrue(any(command == "echo hi" and use_shell for command, use_shell in spawned))

                window.create_profile("Work")
                self.assertEqual(window.profile_combo.count(), 2)
                self.assertEqual(window.current_profile().name, "Work")

                preview = folder / "preview.png"
                window.grab().save(str(preview))
                self.assertGreater(preview.stat().st_size, 1000)

                dialog = RulesDialog(window.kwin, window)
                self.assertGreaterEqual(dialog.list.count(), 1)
                dialog.close()

                window.create_autostart(notify=False)
                desktop = (folder / "onigiri.desktop").read_text(encoding="utf-8")
                self.assertIn("--profile", desktop)
                self.assertIn("--hidden", desktop)
                self.assertFalse(any(line.startswith(" ") for line in desktop.splitlines()))

                window._quitting = True
                window.close()
        finally:
            main_window_module.spawn = original

    def test_launch_includes_a_screen_that_is_not_selected(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
            )
            window.resize(1180, 760)
            window.show()
            app().processEvents()
            window.add_first_tile(window.monitor_name)
            layout = window.current_screen()
            assert layout is not None and isinstance(layout.root, Leaf)
            layout.root.tile.launch_mode = "raw"
            layout.root.tile.command = "echo here"
            layout.root.tile.match_kind = "class"
            layout.root.tile.match_value = "here-class"
            profile = window.current_profile()
            assert profile is not None
            profile.current_layout().screens["saved-screen"] = ScreenLayout(
                root=Leaf(
                    Tile(
                        id="other",
                        name="Other",
                        launch_mode="raw",
                        command="echo there",
                        match_kind="class",
                        match_value="there-class",
                    )
                ),
                x=10,
                y=20,
                width=640,
                height=480,
            )
            profile.overlays[window.monitor_name] = ScreenLayout(
                root=Leaf(
                    Tile(
                        id="over",
                        name="Firefox",
                        launch_mode="app",
                        command="firefox",
                        match_kind="class",
                        match_value="firefox",
                    )
                )
            )
            placed = {item.match_value for item in window.collect_placements()}
            overlay = {item.match_value for item in window.collect_overlay_placements()}
            self.assertEqual(placed, {"here-class", "there-class"})
            self.assertEqual(overlay, {"firefox"})
            window._activate_monitor("DP-9")
            self.assertNotIn("DP-9", profile.current_layout().screens)
            window._quitting = True
            window.close()

    def test_right_click_offers_split(self) -> None:
        app()
        opened: list[list[str]] = []
        original_exec = QMenu.exec

        def fake_exec(menu, *args, **kwargs):
            opened.append([action.text() for action in menu.actions()])
            return None

        QMenu.exec = fake_exec
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: None,
                )
                window.resize(1180, 760)
                window.show()
                app().processEvents()
                center = QPoint(window.canvas.width() // 2, window.canvas.height() // 2)
                window.canvas.contextMenuEvent(
                    QContextMenuEvent(QContextMenuEvent.Reason.Mouse, center, center)
                )
                window.add_first_tile(window.monitor_name)
                app().processEvents()
                window.canvas.contextMenuEvent(
                    QContextMenuEvent(QContextMenuEvent.Reason.Mouse, center, center)
                )
                self.assertEqual(
                    opened,
                    [
                        ["Add tile"],
                        ["Top / bottom", "Left / right", "Delete tile"],
                    ],
                )
                window._quitting = True
                window.close()
        finally:
            QMenu.exec = original_exec

    def test_shift_select_shows_shared_settings(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
            )
            window.resize(1180, 760)
            window.show()
            app().processEvents()
            window.add_first_tile(window.monitor_name)
            window.split_current("v")
            app().processEvents()
            tiles = list(iter_tiles(window.current_screen().root))
            self.assertEqual(len(tiles), 2)
            first, second = tiles[0], tiles[1]
            first.launch_mode = "helper"
            second.launch_mode = "helper"
            window._sync_inspector()
            _click_canvas_tile(window, first.id)
            app().processEvents()
            self.assertEqual(window.selected_ids, [first.id])
            self.assertTrue(window.inspector.name_edit.isVisible())
            self.assertTrue(window.inspector.gap_spin.isVisible())
            _click_canvas_tile(window, second.id, Qt.KeyboardModifier.ShiftModifier)
            app().processEvents()
            self.assertEqual(set(window.selected_ids), {first.id, second.id})
            self.assertFalse(window.inspector.name_edit.isVisible())
            self.assertTrue(window.inspector.opacity_slider.isVisible())
            self.assertTrue(window.inspector.no_border.isVisible())
            self.assertTrue(window.inspector.skip_taskbar.isVisible())
            self.assertTrue(window.inspector.font_spin.isVisible())
            self.assertTrue(window.inspector.font_value.isVisible())
            self.assertTrue(window.inspector.show_title.isVisible())
            self.assertTrue(window.inspector.gap_spin.isVisible())
            self.assertEqual(window.inspector.launch_button.text(), "Launch these tiles")
            second.launch_mode = "raw"
            window._sync_inspector()
            app().processEvents()
            self.assertFalse(window.inspector.font_spin.isVisible())
            self.assertFalse(window.inspector.show_title.isVisible())
            self.assertTrue(window.inspector.opacity_slider.isVisible())
            self.assertEqual(window.inspector.launch_button.text(), "Launch these tiles")
            _click_canvas_tile(window, first.id)
            app().processEvents()
            self.assertEqual(window.selected_ids, [first.id])
            self.assertTrue(window.inspector.name_edit.isVisible())
            self.assertTrue(window.inspector.gap_spin.isVisible())
            QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
            app().processEvents()
            self.assertEqual(window.selected_ids, [])
            self.assertTrue(window.overlay.isVisible())
            self.assertEqual(window.overlay.current_page(), "hint")
            self.assertFalse(window.inspector.gap_spin.isVisible())
            window._quitting = True
            window.close()

    def test_layouts_cover_every_screen_and_gap_stays_local(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
            )
            window.resize(1180, 760)
            window.show()
            app().processEvents()
            window.add_first_tile(window.monitor_name)
            profile = window.current_profile()
            assert profile is not None
            hdmi = window.monitor_name
            window.create_layout("Work")
            self.assertEqual(list(profile.layouts), ["Default", "Work"])
            self.assertEqual(profile.current, "Work")
            self.assertIsNone(profile.screen_layout(hdmi))
            self.assertIsNotNone(profile.layouts["Default"].screens[hdmi].root)
            window.add_first_tile(hdmi)
            work = profile.layouts["Work"].screens[hdmi]
            work.gap = 8
            other = profile.ensure_screen("TEST-WIDE")
            other.root = Leaf(Tile(id="wide", name="Wide"))
            other.gap = 8
            profile.layouts["Default"].screens[hdmi].gap = 15
            window.refresh()
            app().processEvents()
            self.assertEqual(window.gap_spin.value(), 8)
            window._activate_monitor("TEST-WIDE")
            app().processEvents()
            self.assertEqual(window.gap_spin.value(), 8)
            window.gap_spin.setValue(12)
            app().processEvents()
            self.assertEqual(profile.layouts["Work"].screens["TEST-WIDE"].gap, 12)
            self.assertEqual(profile.layouts["Work"].screens[hdmi].gap, 8)
            self.assertEqual(profile.layouts["Default"].screens[hdmi].gap, 15)
            window._on_layout_row(0)
            app().processEvents()
            self.assertEqual(profile.current, "Default")
            window._activate_monitor(hdmi)
            app().processEvents()
            self.assertEqual(profile.layouts["Default"].screens[hdmi].gap, 15)
            self.assertFalse(window.inspector.gap_spin.isVisible())
            window._quitting = True
            window.close()


def _color_distance(first: QColor, second: QColor) -> int:
    return abs(first.red() - second.red()) + abs(first.green() - second.green()) + abs(first.blue() - second.blue())


def _click_canvas_tile(window, tile_id: str, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    for screen in window.canvas._screens:
        for tile, rect in screen.tiles:
            if tile.id != tile_id:
                continue
            point = window.canvas._to_widget(rect.x + rect.width / 2, rect.y + rect.height / 2)
            QTest.mouseClick(
                window.canvas,
                Qt.MouseButton.LeftButton,
                modifiers,
                QPoint(int(point.x()), int(point.y())),
            )
            return
    raise AssertionError(f"tile {tile_id} is not on the canvas")


class CanvasDesktopTests(unittest.TestCase):
    def test_screens_keep_their_desktop_positions(self) -> None:
        app()
        canvas = LayoutCanvas()
        canvas.resize(900, 600)
        canvas.show()
        app().processEvents()
        left = Tile(id="left", name="Left")
        right = Tile(id="right", name="Right")
        canvas.set_monitors(
            [
                MonitorView("TEST-HDMI", 240, 0, 1920, 1080, Leaf(left), "", True),
                MonitorView("TEST-WIDE", 0, 1080, 2560, 1440, Leaf(Tile(id="wide", name="Wide")), "", False),
                MonitorView("TEST-PORTRAIT", 2560, 240, 720, 1280, Leaf(right), "", False),
            ],
            0,
            None,
        )
        self.assertEqual(canvas._tile_at(800, 100).id, "left")
        self.assertEqual(canvas._tile_at(100, 1200 + _SCREEN_GUTTER).id, "wide")
        self.assertEqual(canvas._tile_at(2600 + _SCREEN_GUTTER, 400).id, "right")
        self.assertIsNone(canvas._tile_at(100, 100))
        widget = canvas._to_widget(2600 + _SCREEN_GUTTER, 400)
        back_x, back_y = canvas._local(widget)
        self.assertAlmostEqual(back_x, 2600 + _SCREEN_GUTTER, delta=1)
        self.assertAlmostEqual(back_y, 400, delta=1)
        panels = [canvas._panel_rect(screen) for screen in canvas._screens]
        for index, first in enumerate(panels):
            for second in panels[index + 1 :]:
                self.assertFalse(first.intersects(second))

    def test_app_shell_nav_dock_and_strip(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
                set_wallpaper_fn=lambda path, fill="crop": None,
                apply_colors_fn=lambda blue, purple: None,
            )
            window.resize(1180, 760)
            window.show()
            app().processEvents()
            self.assertEqual(
                [window.nav_list.item(i).text() for i in range(window.nav_list.count())],
                ["Scenes", "Tiles", "Extensions", "Settings"],
            )
            self.assertTrue(window.overlay.isVisible())
            self.assertEqual(window.overlay.current_page(), "hint")
            self.assertTrue(window.overlay.hint.isVisible())
            self.assertGreater(window.filmstrip.mapTo(window, QPoint(0, 0)).x(), 180)
            self.assertLess(window.filmstrip.width(), window.width() - 150)
            self.assertFalse(hasattr(window, "save_button"))
            self.assertFalse(hasattr(window, "discard_button"))
            for button in (window.apply_button, window.undo_button, window.redo_button):
                self.assertFalse(button.isVisible())
            self.assertTrue(window.new_scene_button.isVisible())
            self.assertTrue(window.apply_scene_button.isVisible())
            self.assertFalse(window.apply_scene_button.isEnabled())
            self.assertFalse(window._undo_shortcut.isEnabled())
            self.assertFalse(window._save_shortcut.isEnabled())
            self.assertFalse(window.nav_list.item(0).icon().isNull())

            window.add_first_tile(window.monitor_name)
            app().processEvents()
            if window.selected_tile_id:
                self.assertEqual(window.overlay.current_page(), "tile")
                self.assertTrue(window.inspector.name_edit.isVisible())
            else:
                _click_canvas_tile(window, window.current_screen().root.tile.id)
                app().processEvents()
                self.assertEqual(window.overlay.current_page(), "tile")
                self.assertTrue(window.inspector.name_edit.isVisible())

            image = _png(folder / "wall.png")
            scene = Scene(id="sc1", name="Night", wallpaper=str(image), blue=(1, 2, 3), purple=(4, 5, 6))
            window.config.scenes = [scene]
            window._sync_scenes()
            window._on_scene_clicked("sc1")
            app().processEvents()
            self.assertEqual(window.overlay.current_page(), "scene")
            self.assertEqual(window.scene_state.text(), "No scene applied this session")
            self.assertTrue(window.apply_scene_button.isEnabled())
            with patch.object(window, "_apply_scene") as apply_scene:
                window.apply_scene_button.click()
                apply_scene.assert_called_once_with(scene)
            self.assertTrue(window.overlay.scene_panel.name_edit.isVisible())

            day = Scene(id="sc2", name="Day", wallpaper=str(image), blue=(7, 8, 9), purple=(10, 11, 12))
            window.config.scenes = [scene, day]
            window._sync_scenes()
            app().processEvents()
            self.assertTrue(window.playlist_bar.isVisible())
            self.assertLess(
                window.playlist_bar.mapTo(window, QPoint(0, 0)).y(),
                window.filmstrip.mapTo(window, QPoint(0, 0)).y(),
            )
            self.assertGreater(window.playlist_bar.mapTo(window, QPoint(0, 0)).y(), window.canvas.mapTo(window, QPoint(0, 0)).y())
            play_right = window.playlist_bar.play_button.mapTo(
                window, QPoint(window.playlist_bar.play_button.width(), 0)
            ).x()
            mode_left = window.playlist_bar.mode_combo.mapTo(window, QPoint(0, 0)).x()
            mode_right = window.playlist_bar.mode_combo.mapTo(
                window, QPoint(window.playlist_bar.mode_combo.width(), 0)
            ).x()
            side_panel = window.right_stack.parentWidget()
            side_left = side_panel.mapTo(window, QPoint(0, 0)).x()
            side_bottom = side_panel.mapTo(window, QPoint(0, side_panel.height())).y()
            strip_top = window.strip_panel.mapTo(window, QPoint(0, 0)).y()
            nav = window.nav_list.parentWidget()
            nav_right = nav.mapTo(window, QPoint(nav.width(), 0)).x()
            canvas_panel = window.canvas.parentWidget()
            canvas_left = canvas_panel.mapTo(window, QPoint(0, 0)).x()
            self.assertGreater(mode_left, play_right)
            self.assertLess(side_left - mode_right, 24)
            self.assertGreaterEqual(strip_top - side_bottom, window.scene_state.height())
            grab = window.grab().toImage()
            origin = canvas_panel.mapTo(window, QPoint(0, 0))
            inside = QColor(grab.pixel(origin.x() + 18, origin.y() + 18))
            fill = QColor("#141414")
            self.assertEqual(canvas_panel._fill.alpha(), PANEL_ALPHA)
            self.assertEqual(nav._fill.alpha(), PANEL_ALPHA)
            self.assertEqual(side_panel._fill.alpha(), PANEL_ALPHA)
            self.assertEqual(window.strip_panel._fill.alpha(), PANEL_ALPHA)
            self.assertEqual(canvas_panel._fill, nav._fill)
            self.assertGreater(WINDOW_ALPHA, PANEL_ALPHA)
            self.assertGreater(_color_distance(inside, fill), 8)
            side_origin = side_panel.mapTo(window, QPoint(0, 0))
            side_border = QColor(grab.pixel(side_origin.x() + 20, side_origin.y() + 1))
            edge = QColor("#2a2a2a")
            self.assertLess(_color_distance(side_border, edge), _color_distance(side_border, fill))
            self.assertIn(f"border: {int(PANEL_BORDER)}px solid #2c2c2c", app().styleSheet())

            window.nav_list.setCurrentRow(1)
            app().processEvents()
            self.assertTrue(window.layout_combo.isVisible())
            self.assertTrue(window.profile_combo.isVisible())
            self.assertFalse(window.filmstrip.isVisible())
            self.assertFalse(window.playlist_bar.isVisible())
            self.assertFalse(window.scene_state.isVisible())
            self.assertTrue(window.overlay.isVisible())
            self.assertEqual(window.apply_button.text(), "Apply layout")
            self.assertTrue(window.apply_button.isVisible())
            self.assertTrue(window.undo_button.isVisible())
            self.assertTrue(window.redo_button.isVisible())
            self.assertFalse(window.new_scene_button.isVisible())
            self.assertFalse(window.apply_scene_button.isVisible())
            self.assertTrue(window._undo_shortcut.isEnabled())
            self.assertIn(window.current_profile().name, window.context_label.text())
            self.assertIn(window.current_profile().current, window.context_label.text())
            tile_id = next(iter(window._tiles_by_id()))
            window._on_tiles_selected([tile_id])
            app().processEvents()
            self.assertEqual(window.overlay.current_page(), "tile")
            self.assertTrue(window.inspector.name_edit.isVisible())

            window.nav_list.setCurrentRow(2)
            app().processEvents()
            self.assertTrue(window.plugin_list.isVisible())
            self.assertFalse(window.canvas.isVisible())
            self.assertGreater(window.right_panel.width(), 300)

            window.nav_list.setCurrentRow(3)
            app().processEvents()
            self.assertFalse(window.canvas.isVisible())
            self.assertFalse(window.page_actions.isVisible())
            self.assertFalse(window.undo_button.isVisible())
            self.assertFalse(window._undo_shortcut.isEnabled())
            self.assertTrue(window.hotkey_edit.isVisible())
            self.assertTrue(window.autostart_button.isVisible())
            self.assertTrue(window.rules_button.isVisible())
            self.assertFalse(window.playlist_bar.isVisible())

            window.nav_list.setCurrentRow(0)
            app().processEvents()
            self.assertTrue(window.undo_stack)
            window.discard_edits()
            app().processEvents()
            screen = window.current_screen()
            self.assertTrue(screen is None or screen.root is None)
            window._quitting = True
            window.close()

    def test_tiles_page_manages_profiles_and_layouts_together(self) -> None:
        from onigiri.store import load_config
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(folder / "config.json", folder / "rules", folder / "autostart",
                                reconfigure_fn=lambda: None)
            window.show()
            window.nav_list.setCurrentRow(1)
            app().processEvents()
            self.assertEqual(window.page_title.text(), "Tiles")
            self.assertTrue(window.profile_combo.isVisible())
            self.assertTrue(window.layout_combo.isVisible())
            profile_actions = window.profile_actions.menu().actions()
            layout_actions = window.layout_actions.menu().actions()
            with patch.object(window, "_ask_name", return_value="Work"):
                profile_actions[0].trigger()
            self.assertEqual(window.current_profile().name, "Work")
            with patch.object(window, "_ask_name", return_value="Desk"):
                profile_actions[1].trigger()
            self.assertEqual(window.profile_combo.currentText(), "Desk")
            with patch.object(window, "_ask_name", return_value="Coding"):
                layout_actions[0].trigger()
            with patch.object(window, "_ask_name", return_value="Development"):
                layout_actions[1].trigger()
            self.assertEqual(window.layout_combo.currentText(), "Development")
            window.add_first_tile(window.monitor_name)
            self.assertTrue(window.current_screen().root)
            window.profile_combo.setCurrentIndex(0)
            self.assertEqual(window.current_profile().name, "Default")
            window.profile_combo.setCurrentIndex(1)
            self.assertEqual(window.layout_combo.currentText(), "Development")
            self.assertTrue(window.current_screen().root)
            loaded = load_config(folder / "config.json")
            self.assertIn("Development", loaded.profiles[1].layouts)
            with patch("onigiri.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                layout_actions[2].trigger()
                self.assertNotIn("Development", window.current_profile().layouts)
                profile_actions[2].trigger()
            self.assertEqual(window.profile_combo.count(), 1)
            self.assertEqual(window.current_profile().name, "Default")
            self.assertFalse(window.filmstrip.isVisible())
            self.assertFalse(window.playlist_bar.isVisible())
            window._quitting = True
            window.close()

    def test_empty_leaf_is_an_empty_screen(self) -> None:
        app()
        canvas = LayoutCanvas()
        canvas.resize(400, 300)
        canvas.set_monitors(
            [MonitorView("TEST-WIDE", 0, 0, 800, 600, Leaf(), active=True)],
            selected=None,
        )
        self.assertIsNone(canvas._screens[0].root)
        self.assertEqual(canvas._screens[0].tiles, [])
        self.assertEqual(canvas._screens[0].slots, [])


class FontSizeTests(unittest.TestCase):
    def test_arrows_skip_the_gap_below_six(self) -> None:
        app()
        spin = FontSizeSpin()
        spin.setRange(0, 48)
        spin.setValue(0)
        spin.stepBy(1)
        self.assertEqual(spin.value(), 6)
        spin.stepBy(-1)
        self.assertEqual(spin.value(), 0)

    def test_inspector_shows_the_point_size(self) -> None:
        app()
        inspector = Inspector()
        tile = Tile(id="a", name="A", launch_mode="helper", font_size=8)
        inspector.set_selection([tile], "default", set())
        self.assertEqual(inspector.font_spin.value(), 8)
        self.assertEqual(inspector.font_spin.buttonSymbols(), QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.assertEqual(inspector.gap_spin.buttonSymbols(), QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.assertEqual(inspector.font_value.text(), "8 pt")
        inspector.font_spin.setValue(12)
        self.assertEqual(inspector.font_value.text(), "12 pt")
        self.assertEqual(tile.font_size, 12)
        inspector.show()
        app().processEvents()
        raw = Tile(id="raw", name="Raw", launch_mode="raw", command="echo hi")
        inspector.set_selection([raw], "default", set())
        self.assertFalse(inspector.match_kind.isVisible())
        self.assertFalse(inspector.match_value.isVisible())
        inspector.deleteLater()

    def test_hide_toolbar_is_only_shown_for_konsole(self) -> None:
        app()
        inspector = Inspector()
        alacritty = Tile(id="a", name="A", launch_mode="helper", terminal="alacritty")
        inspector.set_selection([alacritty], "default", set())
        inspector.show()
        app().processEvents()
        self.assertFalse(inspector.hide_toolbar.isVisible())
        self.assertFalse(alacritty.hide_toolbar)

        konsole = Tile(id="k", name="K", launch_mode="helper", terminal="konsole")
        inspector.set_selection([konsole], "default", set())
        app().processEvents()
        self.assertTrue(inspector.hide_toolbar.isVisible())
        self.assertFalse(inspector.hide_toolbar.isChecked())
        inspector.hide_toolbar.setChecked(True)
        app().processEvents()
        self.assertTrue(konsole.hide_toolbar)
        inspector.deleteLater()


def _hue_stub(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "hue.py"
    path.write_text(
        "title = 'Hue lamps'\n"
        "def fields():\n"
        "    return [\n"
        "        {'id': 'blue', 'type': 'color', 'label': 'Cool'},\n"
        "        {'id': 'purple', 'type': 'color', 'label': 'Warm'},\n"
        "    ]\n"
        "def on_scene(scene):\n"
        "    fn = scene.extras.get('hue_fn')\n"
        "    if fn:\n"
        "        fn(scene.settings.get('blue'), scene.settings.get('purple'), scene.extras.get('hue_file'))\n",
        encoding="utf-8",
    )
    return folder


def _png(path: Path) -> Path:
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    )
    return path


class SceneTests(unittest.TestCase):
    def test_scene_dialog_accepts_a_qt_parent(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            host = QWidget()
            dialog = SceneDialog(parent=host, extensions_dir=Path(tmp) / "empty")
            self.assertIs(dialog.parent(), host)
            dialog.deleteLater()
            host.deleteLater()

    def test_dialog_builds_a_scene_without_extensions(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "wall.png")
            dialog = SceneDialog(extensions_dir=Path(tmp) / "empty")
            dialog.name_edit.setText("Night")
            dialog._set_picture(str(image))
            scene = dialog.built()
            self.assertEqual(scene.name, "Night")
            self.assertEqual(scene.wallpaper, str(image))
            self.assertEqual(scene.plugins, {})
            self.assertFalse(dialog.deleted)
            dialog.deleteLater()

    def test_lianli_sidebar_follows_scene_pair_unless_stored(self) -> None:
        app()
        panel = PluginPanel(
            ExtensionInfo(
                stem="lianli_fans",
                path=Path("lianli_fans.py"),
                title="Lian Li fans",
                fields=[
                    ExtensionField("led_blue", "color", "LED color 1"),
                    ExtensionField("led_purple", "color", "LED color 2"),
                    ExtensionField("text", "color", "LCD text"),
                    ExtensionField("gauge", "color", "LCD gauge"),
                ],
            )
        )
        cat = Scene(id="cat", name="Cat", blue=(9, 84, 71), purple=(72, 161, 116))
        panel.fill_from_scene(cat)
        self.assertEqual(panel.values()["led_blue"], (9, 84, 71))
        self.assertEqual(panel.values()["led_purple"], (72, 161, 116))
        self.assertEqual(panel.values()["text"], DEFAULT_BLUE)
        self.assertEqual(panel.values()["gauge"], DEFAULT_BLUE)

        stored = Scene(
            id="girl",
            name="Anime_Girl",
            blue=(136, 149, 165),
            purple=(211, 218, 232),
            plugins={
                "lianli_fans": {
                    "led_blue": (0, 123, 255),
                    "led_purple": (212, 0, 255),
                    "text": (1, 2, 3),
                    "gauge": (4, 5, 6),
                }
            },
        )
        panel.fill_from_scene(stored)
        self.assertEqual(panel.values()["led_blue"], (0, 123, 255))
        self.assertEqual(panel.values()["led_purple"], (212, 0, 255))
        self.assertEqual(panel.values()["text"], (1, 2, 3))
        self.assertEqual(panel.values()["gauge"], (4, 5, 6))

        panel.fill_from_scene(cat)
        self.assertEqual(panel.values()["led_blue"], (9, 84, 71))
        self.assertEqual(panel.values()["led_purple"], (72, 161, 116))
        self.assertEqual(panel.values()["text"], DEFAULT_BLUE)
        self.assertEqual(panel.values()["gauge"], DEFAULT_BLUE)
        panel.deleteLater()

    def test_plugin_pick_uses_system_color_dialog(self) -> None:
        from onigiri.ui import scene_dialog as sd

        app()
        panel = PluginPanel(
            ExtensionInfo(
                stem="gpu",
                path=Path("gpu.py"),
                title="GPU",
                fields=[ExtensionField("blue", "color", "Color 1")],
            )
        )
        seen: list[str] = []

        def fake_pick(parent, color, title="Color"):
            seen.append(title)
            return QColor(10, 20, 30)

        original = sd.pick_color
        sd.pick_color = fake_pick
        try:
            panel._pick(panel._swatches["blue"], "blue")
            self.assertEqual(seen, ["Color 1"])
            self.assertEqual(panel.values()["blue"], (10, 20, 30))
        finally:
            sd.pick_color = original
            panel.deleteLater()

    def test_color_dialog_uses_gray_surface_and_theme_has_arrows(self) -> None:
        from onigiri.ui.colors import color_dialog
        from onigiri.ui.theme import apply_theme

        app()
        apply_theme(app())
        dialog = color_dialog(QColor(255, 76, 70), title="Color 1")
        dialog.resize(540, 380)
        dialog.show()
        app().processEvents()
        self.assertEqual(dialog.windowTitle(), "Color 1")
        self.assertEqual(dialog.palette().color(QPalette.ColorRole.Window).name(), "#181818")
        sample = QColor(dialog.grab().toImage().pixel(16, 16))
        self.assertLess(_color_distance(sample, QColor("#181818")), _color_distance(sample, QColor("#101010")))
        sheet = app().styleSheet()
        self.assertIn("spin_up.svg", sheet)
        self.assertIn("spin_down.svg", sheet)
        self.assertIn("QSpinBox::up-arrow", sheet)
        dialog.deleteLater()

    def test_new_scene_starts_with_extensions_off(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = _png(folder / "wall.png")
            ext = _hue_stub(folder / "ext")
            (ext / "skip.py").write_text("title = 'Skip'\ndef on_scene(scene):\n    pass\n", encoding="utf-8")
            dialog = SceneDialog(extensions_dir=ext)
            dialog._set_picture(str(image), pick_colors=True)
            self.assertFalse(dialog.panels["hue"].is_checked())
            self.assertFalse(dialog.panels["skip"].is_checked())
            self.assertEqual(dialog.built().plugins, {})
            dialog.panels["hue"].set_checked(True)
            self.assertIn("hue", dialog.built().plugins)
            self.assertNotIn("skip", dialog.built().plugins)
            dialog.deleteLater()

    def test_legacy_scene_dialog_keeps_extensions_on(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = _png(folder / "wall.png")
            ext = _hue_stub(folder / "ext")
            (ext / "skip.py").write_text("title = 'Skip'\ndef on_scene(scene):\n    pass\n", encoding="utf-8")
            dialog = SceneDialog(
                scene=Scene(id="sc1", name="Night", wallpaper=str(image), plugins=None),
                extensions_dir=ext,
            )
            self.assertTrue(dialog.panels["hue"].is_checked())
            self.assertTrue(dialog.panels["skip"].is_checked())
            dialog.deleteLater()

    def test_create_scene_saves_only_chosen_extensions(self) -> None:
        from onigiri.ui import main_window as mw

        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = _png(folder / "wall.png")
            built = Scene(id="new", name="Night", wallpaper=str(image), plugins={})

            class StubDialog:
                deleted = False

                def __init__(self, *args, **kwargs) -> None:
                    pass

                def exec(self):
                    return QDialog.DialogCode.Accepted

                def built(self) -> Scene:
                    return built

            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
                set_wallpaper_fn=lambda path, fill="crop": None,
                apply_colors_fn=lambda blue, purple: True,
            )
            original = mw.SceneDialog
            mw.SceneDialog = StubDialog
            try:
                window._create_scene()
                app().processEvents()
                self.assertEqual(window.config.scenes[-1].name, "Night")
                self.assertEqual(window.config.scenes[-1].plugins, {})
            finally:
                mw.SceneDialog = original
                window._quitting = True
                window.close()

    def test_choosing_a_picture_fills_plugin_color_fields(self) -> None:
        from PyQt6.QtGui import QImage
        from onigiri.palette import dominant_colors, slot_colors

        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = QImage(10, 8, QImage.Format.Format_RGB32)
            for y in range(8):
                for x in range(10):
                    color = QColor(0, 80, 220) if x < 7 else QColor(200, 0, 180)
                    image.setPixelColor(x, y, color)
            path = folder / "wall.png"
            image.save(str(path), "PNG")
            expected_blue, expected_purple = slot_colors(dominant_colors(path))
            dialog = SceneDialog(extensions_dir=_hue_stub(folder / "ext"))
            dialog._set_picture(str(path), pick_colors=True)
            self.assertEqual(dialog.panels["hue"].values()["blue"], expected_blue)
            self.assertEqual(dialog.panels["hue"].values()["purple"], expected_purple)
            dialog.deleteLater()

    def test_wizard_walks_picture_plugins_and_save(self) -> None:
        app()
        painted: list[tuple] = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = _png(folder / "wall.png")
            ext = _hue_stub(folder / "ext")
            (ext / "skip.py").write_text("title = 'Skip'\ndef on_scene(scene):\n    pass\n", encoding="utf-8")
            script = folder / "one_off.py"
            script.write_text("def on_scene(scene):\n    pass\n", encoding="utf-8")
            dialog = SceneDialog(
                hue_file=folder / "hue.json",
                hue_fn=lambda blue, purple, path: painted.append((blue, purple, path)),
                extensions_dir=ext,
            )
            dialog.show()
            app().processEvents()
            self.assertFalse(dialog.plugins_section.isVisible())
            self.assertFalse(dialog.save_section.isVisible())
            self.assertTrue(dialog.next_button.isEnabled())
            dialog._set_picture(str(image), pick_colors=True)
            self.assertTrue(dialog.next_button.isEnabled())
            self.assertTrue(dialog.preview.isVisible())
            self.assertTrue(dialog.plugins_section.isVisible())
            self.assertFalse(dialog.panels["hue"].is_checked())
            self.assertFalse(dialog.panels["skip"].is_checked())
            dialog.panels["hue"].set_checked(True)
            dialog.panels["hue"].set_values({"blue": (10, 20, 30), "purple": (40, 50, 60)})
            dialog._test_plugin("hue")
            self.assertEqual(painted, [((10, 20, 30), (40, 50, 60), folder / "hue.json")])
            self.assertEqual(dialog.panels["hue"].status.text(), "Updated.")
            self.assertEqual(dialog.next_button.text(), "Continue")
            scrolled = dialog.scroll.verticalScrollBar().value()
            dialog._next()
            app().processEvents()
            self.assertTrue(dialog.save_section.isVisible())
            self.assertGreaterEqual(dialog.scroll.verticalScrollBar().value(), scrolled)
            self.assertEqual(dialog.next_button.text(), "Save")
            dialog.name_edit.setText("Night")
            dialog.script_edit.setText(str(script))
            scene = dialog.built()
            self.assertEqual(scene.name, "Night")
            self.assertEqual(scene.wallpaper, str(image))
            self.assertEqual(scene.script, str(script))
            self.assertEqual(scene.plugins["hue"]["blue"], (10, 20, 30))
            self.assertEqual(scene.plugins["hue"]["purple"], (40, 50, 60))
            self.assertNotIn("skip", scene.plugins)
            self.assertEqual(scene.blue, (10, 20, 30))
            self.assertEqual(scene.purple, (40, 50, 60))
            dialog.deleteLater()

    def test_picture_stage_shows_screens_and_keeps_pan(self) -> None:
        from PyQt6.QtGui import QImage
        from onigiri.ui.screens import ScreenInfo

        app()
        screens = [
            ScreenInfo("TEST-HDMI", 320, 0, 1920, 1080),
            ScreenInfo("TEST-WIDE", 0, 1080, 2560, 1440),
            ScreenInfo("TEST-PORTRAIT", 2560, 480, 720, 1280, primary=True),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            image = QImage(1920, 1080, QImage.Format.Format_RGB32)
            image.fill(QColor(20, 40, 80))
            path = Path(tmp) / "wall.png"
            image.save(str(path), "PNG")
            dialog = SceneDialog(screens=screens)
            dialog._set_picture(str(path))
            dialog.preview.set_pans({"TEST-PORTRAIT": (0.0, 0.5)})
            dialog.show()
            dialog.resize(720, 760)
            app().processEvents()
            names = {screen.name for screen, _area in dialog.preview._areas}
            self.assertEqual(names, {"TEST-HDMI", "TEST-WIDE", "TEST-PORTRAIT"})
            self.assertEqual(dialog.preview.pan_for("TEST-PORTRAIT"), (0.0, 0.5))
            panel = None
            for screen, area in dialog.preview._areas:
                if screen.name == "TEST-PORTRAIT":
                    panel = dialog.preview._panel(area)
                    break
            self.assertIsNotNone(panel)
            start = panel.center().toPoint()
            QTest.mousePress(dialog.preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
            moved = start + QPoint(-50, 0)
            QTest.mouseMove(dialog.preview, moved)
            QTest.mouseRelease(dialog.preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, moved)
            app().processEvents()
            pan_x, pan_y = dialog.preview.pan_for("TEST-PORTRAIT")
            self.assertGreater(pan_x, 0.0)
            self.assertAlmostEqual(pan_y, 0.5, places=2)
            self.assertEqual(dialog.built().wallpaper_pan["TEST-PORTRAIT"][0], pan_x)
            dialog.deleteLater()

    def test_scene_click_selects_and_apply_activates_it(self) -> None:
        app()
        walls: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = _png(folder / "wall.png")
            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
                set_wallpaper_fn=lambda path, fill="crop": walls.append(path),
                apply_colors_fn=lambda blue, purple: None,
            )
            window.show()
            app().processEvents()
            scene = Scene(id="sc1", name="Night", wallpaper=str(image), blue=(1, 2, 3), purple=(4, 5, 6))
            window.config.scenes = [scene]
            window._sync_scenes()
            app().processEvents()
            window._on_scene_clicked("sc1")
            app().processEvents()
            self.assertEqual(walls, [])
            self.assertEqual(window._scene_id, "sc1")
            self.assertTrue(window.apply_scene_button.isEnabled())
            self.assertEqual(window.apply_scene_button.text(), "Apply")
            window.apply_scene_button.click()
            app().processEvents()
            self.assertEqual(walls, [str(image)])
            self.assertEqual(window.scene_state.text(), "Applied scene: Night")
            self.assertTrue(window.overlay.isVisible())
            self.assertEqual(window.overlay.current_page(), "scene")
            window._quitting = True
            window.close()

    def test_filmstrip_emits_create_and_scene_clicks(self) -> None:
        app()
        created: list[int] = []
        clicked: list[str] = []
        strip = Filmstrip()
        strip.createRequested.connect(lambda: created.append(1))
        strip.sceneClicked.connect(clicked.append)
        strip.show()
        app().processEvents()
        QTest.mouseClick(strip.plus, Qt.MouseButton.LeftButton)
        app().processEvents()
        self.assertEqual(created, [1])
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "wall.png")
            strip.set_scenes([Scene(id="sc1", name="Night", wallpaper=str(image), blue=(1, 2, 3), purple=(4, 5, 6))])
            app().processEvents()
            self.assertEqual(len(strip._cards), 1)
            QTest.mouseClick(strip._cards[0], Qt.MouseButton.LeftButton)
            app().processEvents()
            self.assertEqual(clicked, ["sc1"])
        strip.deleteLater()

    def test_playlist_bar_and_reorder_and_tick(self) -> None:
        from onigiri.model import Playlist

        app()
        walls: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = _png(folder / "one.png")
            second = _png(folder / "two.png")
            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
                set_wallpaper_fn=lambda path, fill="crop": walls.append(path),
                apply_colors_fn=lambda blue, purple: None,
            )
            window.show()
            app().processEvents()
            night = Scene(id="sc1", name="Night", wallpaper=str(first), blue=(1, 2, 3), purple=(4, 5, 6))
            day = Scene(id="sc2", name="Day", wallpaper=str(second), blue=(7, 8, 9), purple=(10, 11, 12))
            window.config.scenes = [night]
            window._sync_scenes()
            app().processEvents()
            self.assertFalse(window.playlist_bar.isVisible())
            window.config.scenes = [night, day]
            window._sync_scenes()
            app().processEvents()
            self.assertTrue(window.playlist_bar.isVisible())
            window._on_scenes_reordered(["sc2", "sc1"])
            self.assertEqual([scene.id for scene in window.config.scenes], ["sc2", "sc1"])
            window._playlist_index = 0
            window.config.playlist = Playlist(interval=1, unit="seconds", mode="repeat", playing=True)
            self.assertEqual(
                window.playlist_bar.interval_spin.buttonSymbols(),
                QAbstractSpinBox.ButtonSymbols.UpDownArrows,
            )
            window._select_scene(day)
            self.assertEqual(window.overlay.scene_panel.name_edit.text(), "Day")
            window._playlist_tick()
            self.assertEqual(walls[-1], str(first))
            self.assertEqual(window._playlist_index, 1)
            self.assertEqual(window.overlay.scene_panel.name_edit.text(), "Night")
            window._quitting = True
            window.close()

    def test_playlist_timer_loops_repeatedly_in_both_modes_and_survives_errors(self) -> None:
        app()
        for mode in ("repeat", "random"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                walls = []
                def paint(blue, purple):
                    if blue[0] == 2:
                        raise RuntimeError("one extension failed")
                window = MainWindow(
                    folder / "config.json", folder / "rules", folder / "autostart",
                    reconfigure_fn=lambda: None,
                    set_wallpaper_fn=lambda path, pan=None: walls.append(path),
                    apply_colors_fn=paint,
                )
                window.config.scenes = [
                    Scene(id=str(i), name=str(i), wallpaper=str(i), blue=(i, 0, 0))
                    for i in range(3)
                ]
                window.config.playlist.mode = mode
                with patch("onigiri.ui.main_window.interval_ms", return_value=15):
                    window._toggle_playlist()
                    window.nav_list.setCurrentRow(1)
                    self.assertTrue(window.config.playlist.playing)
                    self.assertFalse(window.playlist_bar.isVisible())
                    for _ in range(150):
                        QTest.qWait(10)
                        if len(walls) >= 10:
                            break
                    self.assertGreaterEqual(len(walls), 10)
                    self.assertTrue(window.config.playlist.playing)
                    self.assertTrue(window._scene_timer.isActive())
                    if mode == "repeat":
                        self.assertEqual(walls[:10], [str(i % 3) for i in range(10)])
                    else:
                        self.assertTrue(all(a != b for a, b in zip(walls, walls[1:])))
                    window._toggle_playlist()
                    count = len(walls)
                    QTest.qWait(60)
                    self.assertEqual(len(walls), count)
                    self.assertFalse(window._scene_timer.isActive())
                window._quitting = True
                window.close()

    def test_playlist_waits_for_background_scene_completion_before_next_interval(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(folder / "config.json", folder / "rules", folder / "autostart",
                                reconfigure_fn=lambda: None)
            window.config.scenes = [Scene(id="a", name="A"), Scene(id="b", name="B")]
            callbacks = []
            with patch.object(window._scene_jobs, "submit", side_effect=lambda work, done: callbacks.append(done)), \
                 patch("onigiri.ui.main_window.interval_ms", return_value=15):
                window._toggle_playlist()
                self.assertEqual(len(callbacks), 1)
                self.assertFalse(window._scene_timer.isActive())
                QTest.qWait(60)
                self.assertEqual(len(callbacks), 1)
                callbacks[0]([])
                self.assertTrue(window._scene_timer.isActive())
                QTest.qWait(40)
                self.assertEqual(len(callbacks), 2)
                self.assertFalse(window._scene_timer.isActive())
                window._toggle_playlist()
                callbacks[1](["extension failure"])
                self.assertFalse(window._scene_timer.isActive())
            window._quitting = True
            window.close()

    def test_apply_scene_sets_wallpaper_and_colors(self) -> None:
        app()
        walls: list[str] = []
        pairs: list[tuple] = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = _png(folder / "wall.png")
            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
                set_wallpaper_fn=lambda path, fill="crop": walls.append(path),
                apply_colors_fn=lambda blue, purple: pairs.append((blue, purple)),
            )
            scene = Scene(id="sc1", name="Night", wallpaper=str(image), blue=(9, 8, 7), purple=(6, 5, 4))
            window.config.scenes = [scene]
            window._apply_scene(scene)
            app().processEvents()
            self.assertEqual(walls, [str(image)])
            self.assertEqual(pairs, [((9, 8, 7), (6, 5, 4))])
            window._quitting = True
            window.close()


class HotkeyTests(unittest.TestCase):
    def test_sequence_round_trip(self) -> None:
        sequence = sequence_from_text("Meta+Space")
        self.assertEqual(sequence_to_text(sequence), "Meta+Space")
        self.assertEqual(combined_keys(sequence), [268435488])
        mouse = parse_hotkey("Ctrl+Middle")
        self.assertEqual(mouse.modifiers, ("Ctrl",))
        self.assertEqual(mouse.trigger, "Middle")
        self.assertTrue(mouse.mouse)
        self.assertEqual(mouse.to_text(), "Ctrl+Middle")
        self.assertTrue(parse_hotkey("Ctrl+Middle").to_sequence().isEmpty())

    def test_builder_adds_and_removes_keys(self) -> None:
        app()
        builder = HotkeyBuilder()
        builder.set_text("Ctrl+Middle")
        self.assertEqual(builder.text(), "Ctrl+Middle")
        builder._commit_token("Shift")
        self.assertEqual(builder.text(), "Ctrl+Shift+Middle")
        builder._remove_token("Ctrl")
        self.assertEqual(builder.text(), "Shift+Middle")
        builder.deleteLater()

    def test_add_opens_picker_escape_aborts(self) -> None:
        app()
        self.assertEqual(token_from_key(int(Qt.Key.Key_Control)), "Ctrl")
        self.assertEqual(token_from_key(int(Qt.Key.Key_Alt)), "Alt")
        self.assertIsNone(token_from_key(int(Qt.Key.Key_Escape)))
        self.assertEqual(token_from_mouse(Qt.MouseButton.MiddleButton), "Middle")
        self.assertIsNone(token_from_mouse(Qt.MouseButton.LeftButton))
        builder = HotkeyBuilder()
        self.assertIsNone(builder._picker)
        builder._start_add()
        picker = builder._picker
        self.assertIsNotNone(picker)
        assert picker is not None
        self.assertNotEqual(picker.findData("Ctrl"), -1)
        self.assertNotEqual(picker.findData("Alt"), -1)
        self.assertEqual(picker.findData("Left"), -1)
        self.assertEqual(picker.findData("Escape"), -1)
        self.assertEqual(picker.findData(""), -1)
        builder._abort_add()
        self.assertIsNone(builder._picker)
        self.assertEqual(builder.text(), "")
        builder._start_add()
        builder._commit_token("Ctrl")
        builder._commit_token("Middle")
        self.assertEqual(builder.text(), "Ctrl+Middle")
        self.assertIsNone(builder._picker)
        builder.deleteLater()

    def test_popup_stays_on_the_screen(self) -> None:
        area = QRect(0, 1080, 2560, 1440)
        pinned = popup_position(QPoint(4000, 2500), POPUP_SIZE, area)
        self.assertEqual(pinned, QPoint(2560 - POPUP_SIZE.width(), 1080 + 1440 - POPUP_SIZE.height()))
        origin = popup_position(QPoint(10, 1090), POPUP_SIZE, area)
        self.assertEqual(origin, QPoint(0, 1080))
        mid = popup_position(QPoint(1720, 1800), POPUP_SIZE, area)
        self.assertEqual(mid, QPoint(1720 - POPUP_SIZE.width() // 2, 1800 - POPUP_SIZE.height() // 2))

    def test_preview_uses_only_the_screen_under_the_cursor(self) -> None:
        hdmi = MonitorView("TEST-HDMI", 240, 0, 1920, 1080, Leaf(Tile(id="a", name="btop")))
        wide = MonitorView("TEST-WIDE", 0, 1080, 2560, 1440, None)
        self.assertEqual(monitor_at([hdmi, wide], QPoint(800, 200)).name, "TEST-HDMI")
        self.assertEqual(monitor_at([hdmi, wide], QPoint(100, 1200)).name, "TEST-WIDE")
        self.assertIsNone(monitor_at([hdmi, wide], QPoint(-10, -10)))
        size = preview_size(1920, 1080)
        self.assertEqual(size, QSize(360, 202))
        wide = preview_size(2560, 1440)
        self.assertGreater(wide.width(), size.width())
        self.assertAlmostEqual(wide.width() / wide.height(), 2560 / 1440, places=2)

    def test_fullscreen_overlay_tiles_stop_at_the_panel(self) -> None:
        app()
        screen = QGuiApplication.primaryScreen()
        self.assertIsNotNone(screen)
        geo = screen.geometry()
        usable = screen.availableGeometry()
        view = MonitorView("TEST-HDMI", geo.x(), geo.y(), geo.width(), geo.height(), None)
        popup = CursorPopup()
        popup.show_at(QPoint(usable.x() + 10, usable.y() + 10), view, None, [], fill_screen=True)
        self.assertEqual(popup.size(), usable.size())
        area = popup.tiler._area()
        self.assertEqual(area.width, usable.width())
        self.assertEqual(area.height, usable.height())
        self.assertTrue(popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        popup.hide()
        popup.deleteLater()

    def test_popup_tiler_does_not_edit_the_saved_layout(self) -> None:
        app()
        saved = Leaf(Tile(id="saved", name="btop"))
        view = MonitorView("TEST-HDMI", 240, 0, 1920, 1080, saved)
        tiler = PopupTiler()
        tiler.set_scene(view, None, None)
        self.assertIsNone(tiler.overlay_root())
        tile = tiler.add_first()
        self.assertFalse(tile.skip_taskbar)
        self.assertFalse(tile.no_border)
        self.assertTrue(tile.show_title)
        apply_app(tile, DesktopApp("firefox.desktop", "Firefox", "firefox", "firefox"))
        self.assertFalse(tile.skip_taskbar)
        self.assertFalse(tile.no_border)
        self.assertTrue(tile.show_title)
        self.assertEqual(tile.launch_mode, "app")
        self.assertEqual(view.root.tile.name, "btop")
        self.assertEqual(tiler.overlay_root().tile.name, "Firefox")
        apply_app(tile, DesktopApp("Alacritty.desktop", "Alacritty", "alacritty", "Alacritty"))
        self.assertEqual(tile.launch_mode, "helper")
        self.assertEqual(tile.terminal, "alacritty")
        self.assertEqual(tile.match_value, "")
        self.assertEqual(tiler.overlay_root().tile.name, "Alacritty")
        tiler._remove(tile.id)
        self.assertIsNone(tiler.overlay_root())
        self.assertEqual(view.root.tile.name, "btop")
        first = tiler.add_first()
        tiler._split(first.id, "v")
        self.assertEqual(len(list(iter_tiles(tiler.overlay_root()))), 2)
        for leftover in list(iter_tiles(tiler.overlay_root())):
            tiler._remove(leftover.id)
        self.assertIsNone(tiler.overlay_root())
        self.assertIsNone(tiler.overlay.root)
        self.assertEqual(view.root.tile.name, "btop")
        tiler.deleteLater()

    def test_app_picker_starts_with_running_apps_and_search(self) -> None:
        app()
        firefox = DesktopApp("firefox.desktop", "Firefox", "firefox", "firefox")
        code = DesktopApp("code.desktop", "VS Code", "code", "Code")
        picker = AppPicker()
        picker.set_apps([firefox, code])
        picker.prepare(running=[firefox])
        self.assertIs(picker.focusProxy(), picker.search)
        texts = [picker.results.item(i).text() for i in range(picker.results.count())]
        self.assertEqual(texts, ["Firefox"])
        self.assertEqual(picker.results.currentRow(), 0)
        picker.search.setText("code")
        self.assertEqual(picker.results.count(), 1)
        self.assertEqual(picker.results.item(0).text(), "VS Code")
        picker.search.clear()
        self.assertEqual(picker.results.item(0).text(), "Firefox")
        chosen: list[object] = []
        picker.appChosen.connect(chosen.append)
        picker.search.setText("fire")
        picker._accept_current()
        self.assertEqual([item.name for item in chosen], ["Firefox"])
        picker.deleteLater()

    def test_app_picker_enter_selects_the_highlighted_app(self) -> None:
        app()
        firefox = DesktopApp("firefox.desktop", "Firefox", "firefox", "firefox")
        code = DesktopApp("code.desktop", "VS Code", "code", "Code")
        picker = AppPicker()
        picker.set_apps([firefox, code])
        picker.prepare(running=[firefox, code])
        chosen: list[object] = []
        picker.appChosen.connect(chosen.append)
        QTest.keyClick(picker.search, Qt.Key.Key_Return)
        self.assertEqual([item.name for item in chosen], ["Firefox"])
        chosen.clear()
        picker.prepare(running=[firefox, code])
        QTest.keyClick(picker.search, Qt.Key.Key_Down)
        self.assertEqual(picker.results.currentRow(), 1)
        QTest.keyClick(picker.search, Qt.Key.Key_Return)
        self.assertEqual([item.name for item in chosen], ["VS Code"])
        picker.deleteLater()

    def test_app_picker_opens_as_a_small_popup(self) -> None:
        app()
        view = MonitorView("TEST-HDMI", 0, 0, 1920, 1080, None)
        popup = CursorPopup()
        popup.show_at(QPoint(80, 90), view, None, [], fill_screen=True)
        tile = popup.tiler.add_first()
        popup.tiler.pickApp.emit(tile.id)
        app().processEvents()
        self.assertTrue(popup.picker.isVisible())
        self.assertLess(popup.picker.width(), popup.width())
        self.assertLess(popup.picker.height(), popup.height())
        firefox = DesktopApp("firefox.desktop", "Firefox", "firefox", "firefox")
        popup.picker.set_apps([firefox])
        popup.picker.prepare(running=[firefox])
        popup._pick_tile = tile.id
        popup._place_picker()
        popup.picker.show()
        popup.picker.raise_()
        popup._arm()
        self.assertIs(popup.picker.focusProxy(), popup.picker.search)
        item = popup.picker.results.item(0)
        self.assertEqual(item.text(), "Firefox")
        rect = popup.picker.results.visualItemRect(item)
        QTest.mouseClick(popup.picker.results.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        app().processEvents()
        self.assertEqual(popup.tiler.overlay_root().tile.name, "Firefox")
        self.assertFalse(popup.picker.isVisible())
        popup.hide()
        popup.deleteLater()

    def test_popup_tiler_opens_app_list_only_on_tile_click(self) -> None:
        app()
        view = MonitorView("TEST-HDMI", 0, 0, 1920, 1080, None)
        tiler = PopupTiler()
        tiler.resize(360, 202)
        tiler.show()
        picked: list[str] = []
        tiler.pickApp.connect(picked.append)
        tiler.set_scene(view, None, None)
        QTest.mouseClick(tiler, Qt.MouseButton.LeftButton, pos=QPoint(180, 100))
        app().processEvents()
        self.assertIsNotNone(tiler.overlay_root())
        self.assertEqual(picked, [])
        QTest.mouseClick(tiler, Qt.MouseButton.LeftButton, pos=QPoint(180, 100))
        app().processEvents()
        tile_id = tiler.overlay_root().tile.id
        self.assertEqual(picked, [tile_id])
        QTest.mouseClick(tiler, Qt.MouseButton.MiddleButton, pos=QPoint(180, 100))
        app().processEvents()
        self.assertIsNone(tiler.overlay_root())
        self.assertEqual(picked, [tile_id])
        tiler.deleteLater()

    def test_popup_split_keeps_the_popup_open(self) -> None:
        app()
        view = MonitorView("TEST-HDMI", 0, 0, 1920, 1080, None)
        popup = CursorPopup()
        popup.show_at(QPoint(80, 90), view, None, [])
        popup._arm()
        popup.tiler.add_first()
        original_exec = QMenu.exec

        def fake_exec(menu, *args, **kwargs):
            for action in menu.actions():
                if action.text() == "Left / right":
                    action.trigger()
                    break
            return None

        QMenu.exec = fake_exec
        try:
            popup.tiler.contextMenuEvent(
                QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(180, 100), QPoint(180, 100))
            )
            popup._on_app_state(Qt.ApplicationState.ApplicationInactive)
            app().processEvents()
            self.assertTrue(popup.isVisible())
            self.assertEqual(len(list(iter_tiles(popup.tiler.overlay_root()))), 2)
        finally:
            QMenu.exec = original_exec
            popup.hide()
            popup.deleteLater()

    def test_overlay_tile_keeps_normal_chrome(self) -> None:
        tile = overlay_tile("Firefox")
        self.assertFalse(tile.skip_taskbar)
        self.assertFalse(tile.no_border)
        self.assertTrue(tile.show_title)

    def test_remove_tile_closes_the_assigned_window(self) -> None:
        closed: list[set[str]] = []
        original = main_window_module.spawn
        main_window_module.spawn = lambda command, use_shell: None
        app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: None,
                    close_windows_fn=lambda matches: closed.append(set(matches)) or True,
                )
                window.show()
                app().processEvents()
                profile = window.current_profile()
                assert profile is not None
                view = window.canvas.views[0]
                overlay = ScreenLayout(
                    root=Split(
                        orientation="v",
                        ratio=0.5,
                        first=Leaf(
                            Tile(
                                id="left",
                                name="Dolphin",
                                launch_mode="app",
                                command="dolphin",
                                match_kind="class",
                                match_value="dolphin",
                            )
                        ),
                        second=Leaf(
                            Tile(
                                id="right",
                                name="Alacritty",
                                launch_mode="helper",
                                command="",
                                terminal="alacritty",
                                app_id="Alacritty.desktop",
                            )
                        ),
                    ),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                profile.overlays[view.name] = overlay
                window._popup.show_at(QPoint(80, 90), view, overlay, [])
                window._popup._arm()
                window._popup.tiler._remove("right")
                app().processEvents()
                self.assertEqual(len(closed), 1)
                closed_value = next(iter(closed[0]))
                self.assertTrue(closed_value.startswith("onigiri-ov-"))
                self.assertNotIn("dolphin", closed[0])
                remaining = list(iter_tiles(window._popup.tiler.overlay_root()))
                self.assertEqual([tile.name for tile in remaining], ["Dolphin"])
                window._popup.tiler._remove(remaining[0].id)
                app().processEvents()
                self.assertEqual(closed[1], {"dolphin"})
                self.assertNotIn(view.name, profile.overlays)
                window._popup.hide()
                app().processEvents()
                text = (folder / "kwinrulesrc").read_text(encoding="utf-8")
                self.assertNotIn("OnigiriOverlay:", text)
                window._quitting = True
                window.close()
        finally:
            main_window_module.spawn = original

    def test_popup_close_spawns_overlay_apps(self) -> None:
        spawned: list[tuple[str, bool]] = []
        placed: list[list[str]] = []
        closed: list[set[str]] = []
        original_spawn = main_window_module.spawn
        original_activate = main_window_module.activate_app
        main_window_module.spawn = lambda command, use_shell: spawned.append((command, use_shell))
        main_window_module.activate_app = lambda command, use_shell, app_id="": spawned.append((command, use_shell))
        app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: None,
                    close_windows_fn=lambda matches: closed.append(set(matches)) or True,
                    place_windows_fn=lambda items: placed.append([item.match_value for item in items]) or [],
                    running_names_fn=lambda: set(),
                )
                window.show()
                app().processEvents()
                profile = window.current_profile()
                assert profile is not None
                view = window.canvas.views[0]
                profile.current_layout().screens[view.name] = ScreenLayout(
                    root=Leaf(
                        Tile(
                            id="saved",
                            name="btop",
                            launch_mode="raw",
                            command="echo saved",
                            match_kind="class",
                            match_value="saved-class",
                        )
                    ),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                overlay = ScreenLayout(
                    root=Leaf(
                        Tile(
                            id="over",
                            name="Firefox",
                            launch_mode="app",
                            command="firefox",
                            match_kind="class",
                            match_value="firefox",
                        )
                    ),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                profile.overlays[view.name] = overlay
                window.launch_tiles()
                self.assertEqual(spawned, [("echo saved", True)])
                spawned.clear()
                window._popup.show_at(QPoint(80, 90), view, overlay, [])
                window._popup._arm()
                window._popup.hide()
                app().processEvents()
                self.assertEqual(spawned, [("firefox", False)])
                self.assertEqual(closed, [{"saved-class"}])
                self.assertIn(["firefox"], placed)
                text = (folder / "kwinrulesrc").read_text(encoding="utf-8")
                self.assertIn("Onigiri:Default:saved", text)
                self.assertIn("OnigiriOverlay:", text)
                self.assertIn("wmclass=saved-class", text)
                self.assertIn("wmclass=firefox", text)
                window._quitting = True
                window.close()
        finally:
            main_window_module.spawn = original_spawn
            main_window_module.activate_app = original_activate

    def test_popup_close_spawns_overlay_terminals_without_a_stored_command(self) -> None:
        spawned: list[tuple[str, bool]] = []
        original_activate = main_window_module.activate_app
        main_window_module.activate_app = lambda command, use_shell, app_id="": spawned.append((command, use_shell))
        app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: None,
                    close_windows_fn=lambda _matches: True,
                    place_windows_fn=lambda _items: [],
                    running_names_fn=lambda: set(),
                )
                window.show()
                app().processEvents()
                profile = window.current_profile()
                assert profile is not None
                view = window.canvas.views[0]
                overlay = ScreenLayout(
                    root=Split(
                        orientation="v",
                        ratio=0.5,
                        first=Leaf(
                            Tile(
                                id="left",
                                name="Dolphin",
                                launch_mode="app",
                                command="dolphin",
                                app_id="org.kde.dolphin.desktop",
                                match_kind="class",
                                match_value="dolphin",
                            )
                        ),
                        second=Leaf(
                            Tile(
                                id="right",
                                name="Alacritty",
                                launch_mode="helper",
                                command="",
                                terminal="alacritty",
                                app_id="Alacritty.desktop",
                            )
                        ),
                    ),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                profile.overlays[view.name] = overlay
                window._popup.show_at(QPoint(80, 90), view, overlay, [])
                window._popup._arm()
                window._popup.hide()
                app().processEvents()
                commands = [command for command, _use_shell in spawned]
                self.assertIn("dolphin", commands)
                terminal = next((command for command in commands if "alacritty" in command), "")
                self.assertIn("--class", terminal)
                self.assertIn("onigiri-ov-", terminal)
                window._quitting = True
                window.close()
        finally:
            main_window_module.activate_app = original_activate

    def test_popup_close_moves_a_running_app_instead_of_spawning(self) -> None:
        spawned: list[tuple[str, bool]] = []
        placed: list[list[str]] = []
        original_activate = main_window_module.activate_app
        main_window_module.activate_app = lambda command, use_shell, app_id="": spawned.append((command, use_shell))
        app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: None,
                    close_windows_fn=lambda _matches: True,
                    place_windows_fn=lambda items: placed.append([item.match_value for item in items])
                    or [item.match_value for item in items],
                    running_names_fn=lambda: {"firefox"},
                )
                window.show()
                app().processEvents()
                profile = window.current_profile()
                assert profile is not None
                view = window.canvas.views[0]
                overlay = ScreenLayout(
                    root=Leaf(
                        Tile(
                            id="over",
                            name="Firefox",
                            launch_mode="app",
                            command="firefox",
                            match_kind="class",
                            match_value="firefox",
                            skip_taskbar=True,
                            no_border=True,
                        )
                    ),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                profile.overlays[view.name] = overlay
                window._popup.show_at(QPoint(80, 90), view, overlay, [])
                window._popup._arm()
                window._popup.hide()
                app().processEvents()
                self.assertEqual(spawned, [])
                self.assertEqual(placed, [["firefox"]])
                text = (folder / "kwinrulesrc").read_text(encoding="utf-8")
                self.assertIn("OnigiriOverlay:", text)
                self.assertIn("wmclass=firefox", text)
                self.assertNotIn("skiptaskbar=", text)
                window._quitting = True
                window.close()
        finally:
            main_window_module.activate_app = original_activate

    def test_popup_close_activates_a_running_app_with_no_window(self) -> None:
        spawned: list[tuple[str, bool]] = []
        original_activate = main_window_module.activate_app
        main_window_module.activate_app = lambda command, use_shell, app_id="": spawned.append((command, use_shell))
        app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: None,
                    close_windows_fn=lambda _matches: True,
                    place_windows_fn=lambda _items: [],
                    running_names_fn=lambda: {"discord"},
                )
                window.show()
                app().processEvents()
                profile = window.current_profile()
                assert profile is not None
                view = window.canvas.views[0]
                profile.overlays[view.name] = ScreenLayout(
                    root=Leaf(
                        Tile(
                            id="over",
                            name="Discord",
                            launch_mode="app",
                            command="/usr/bin/discord --url --",
                            app_id="discord.desktop",
                            match_kind="class",
                            match_value="discord",
                        )
                    ),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                window._popup.show_at(QPoint(80, 90), view, profile.overlays[view.name], [])
                window._popup._arm()
                window._popup.hide()
                app().processEvents()
                self.assertEqual(spawned, [("/usr/bin/discord --url --", False)])
                window._quitting = True
                window.close()
        finally:
            main_window_module.activate_app = original_activate

    def test_removing_overlay_tiles_rewrites_kwin_rules(self) -> None:
        original = main_window_module.spawn
        main_window_module.spawn = lambda command, use_shell: None
        app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: None,
                    close_windows_fn=lambda _matches: True,
                )
                window.show()
                app().processEvents()
                profile = window.current_profile()
                assert profile is not None
                view = window.canvas.views[0]
                profile.current_layout().screens[view.name] = ScreenLayout(
                    root=Leaf(
                        Tile(
                            id="saved",
                            name="btop",
                            launch_mode="raw",
                            command="echo saved",
                            match_kind="class",
                            match_value="saved-class",
                        )
                    ),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                overlay = ScreenLayout(
                    root=Leaf(
                        Tile(
                            id="over",
                            name="Alacritty",
                            launch_mode="app",
                            command="alacritty",
                            match_kind="class",
                            match_value="Alacritty",
                            skip_taskbar=True,
                            no_border=True,
                            show_title=False,
                        )
                    ),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                profile.overlays[view.name] = overlay
                window.apply_rules()
                window.apply_overlay_rules()
                text = (folder / "kwinrulesrc").read_text(encoding="utf-8")
                layout_values = {item.match_value for item in window.collect_placements()}
                overlay_values = {item.match_value for item in window.collect_overlay_placements()}
                self.assertEqual(layout_values, {"saved-class"})
                self.assertEqual(len(overlay_values), 1)
                overlay_class = next(iter(overlay_values))
                self.assertTrue(overlay_class.startswith("onigiri-ov-"))
                self.assertNotEqual(overlay_class.casefold(), "alacritty")
                self.assertIn("OnigiriOverlay:", text)
                self.assertIn(f"wmclass={overlay_class}", text)
                self.assertNotIn("wmclass=alacritty", text)
                self.assertIn("wmclass=saved-class", text)
                window._popup.show_at(QPoint(80, 90), view, overlay, [])
                window._popup._arm()
                window._popup.tiler._remove("over")
                app().processEvents()
                window._popup.hide()
                app().processEvents()
                text = (folder / "kwinrulesrc").read_text(encoding="utf-8")
                self.assertNotIn("OnigiriOverlay:", text)
                self.assertNotIn(overlay_class, text)
                self.assertNotIn("wmclass=alacritty", text)
                self.assertIn("wmclass=saved-class", text)
                window._quitting = True
                window.close()
        finally:
            main_window_module.spawn = original

    def test_launch_spawns_each_helper_tile_once(self) -> None:
        spawned: list[str] = []
        original = main_window_module.spawn
        main_window_module.spawn = lambda command, use_shell: spawned.append(command)
        app()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                window = MainWindow(
                    folder / "onigiri.json",
                    folder / "kwinrulesrc",
                    folder / "onigiri.desktop",
                    reconfigure_fn=lambda: None,
                    close_windows_fn=lambda _matches: True,
                )
                profile = window.current_profile()
                assert profile is not None
                view = window.canvas.views[0]
                left = Tile(id="a", name="btop", launch_mode="helper", terminal="konsole", shell_command="btop")
                mid = Tile(id="b", name="fastfetch", launch_mode="helper", terminal="konsole", shell_command="fastfetch")
                right = Tile(id="c", name="empty", launch_mode="helper", terminal="konsole", shell_command="clear")
                profile.current_layout().screens[view.name] = ScreenLayout(
                    root=Split("v", 0.5, Leaf(left), Split("h", 0.5, Leaf(mid), Leaf(right))),
                    x=view.x,
                    y=view.y,
                    width=view.width,
                    height=view.height,
                )
                window.launch_tiles()
                self.assertEqual(len(spawned), 3)
                self.assertTrue(all("konsole" in command for command in spawned))
                window._quitting = True
                window.close()
        finally:
            main_window_module.spawn = original

    def test_hotkey_field_opens_the_popup(self) -> None:
        app()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(
                folder / "onigiri.json",
                folder / "kwinrulesrc",
                folder / "onigiri.desktop",
                reconfigure_fn=lambda: None,
            )
            window.show()
            app().processEvents()
            window.hotkey_edit.set_text("Ctrl+Middle")
            window.hotkey_edit.changed.emit()
            app().processEvents()
            self.assertEqual(window.config.hotkey, "Ctrl+Middle")
            self.assertEqual(window.hotkey_edit.spec().trigger, "Middle")
            window.show_cursor_popup(QPoint(80, 90))
            self.assertTrue(window._popup.isVisible())
            if window.canvas.views:
                view = window.canvas.views[0]
                self.assertEqual(window._popup.monitor_name, view.name)
            screen = QGuiApplication.screenAt(QPoint(80, 90)) or QGuiApplication.primaryScreen()
            if screen is not None:
                self.assertEqual(window._popup.size(), screen.availableGeometry().size())
            mini = CursorPopup()
            mini.show_at(QPoint(80, 90), window.canvas.views[0] if window.canvas.views else None, None, [])
            if window.canvas.views:
                view = window.canvas.views[0]
                self.assertEqual(mini.size(), preview_size(view.width, view.height))
            mini.hide()
            mini.deleteLater()
            window._popup._arm()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
            app().processEvents()
            self.assertFalse(window._popup.isVisible())
            window._quitting = True
            window.close()


if __name__ == "__main__":
    unittest.main()
