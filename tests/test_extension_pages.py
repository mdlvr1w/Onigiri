"""Standalone plugin pages: custom UI, isolated controls and targeted lifecycle."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
EXAMPLES = Path(__file__).resolve().parents[1] / "examples/extensions"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from PyQt6.QtTest import QTest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget
from onigiri.extensions import apply_extension, create_extension_page, extension_running, list_extensions, stop_extensions
from onigiri.ui.extension_page import ExtensionControlPage, ExtensionPageContext
from onigiri.ui.main_window import MainWindow
from tests.test_ui import app
from _hue import HueSettings, HueLight


class ImmediateJobs:
    def submit(self, work, done):
        try:
            result = work()
        except Exception as error:
            result = [str(error)]
        done(result)


def context(info, folder, jobs=None):
    return ExtensionPageContext(info, jobs or ImmediateJobs(), lambda _: None,
                                settings_path=folder / "manual.json")


class ExtensionPageTests(unittest.TestCase):
    def setUp(self):
        app()

    def tearDown(self):
        stop_extensions()

    def test_custom_page_opens_from_list_without_applying_a_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "custom.py").write_text(
                "from PyQt6.QtWidgets import QLabel\n"
                "title = 'Custom controls'\n"
                "def create_page(context): return QLabel('An extension-owned page')\n"
                "def on_scene(scene): raise RuntimeError('must not apply on open')\n")
            with patch.dict(os.environ, {"ONIGIRI_EXTENSIONS": str(folder)}):
                window = MainWindow(folder / "config.json", folder / "rules", folder / "autostart",
                                    reconfigure_fn=lambda: None)
                before = window.config.to_dict()
                window.nav_list.setCurrentRow(2)
                item = window.plugin_list.item(0)
                window.plugin_list.itemClicked.emit(item)
                scroll, page = window._extension_pages['custom']
                self.assertEqual(page.text(), 'An extension-owned page')
                self.assertEqual(window.extension_stack.currentIndex(), 1)
                self.assertEqual(window.page_title.text(), 'Extensions · Custom controls')
                self.assertEqual(window.config.to_dict(), before)
                window._show_extensions()
                self.assertEqual(window.extension_stack.currentIndex(), 0)
                window._quitting = True
                window.close()

    def test_generic_page_applies_only_manual_values_and_saves_them_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sink = folder / 'device.json'
            (folder / 'device.py').write_text(
                "import json\nfrom pathlib import Path\n"
                "def fields(): return [{'id':'power','type':'number','default':30}]\n"
                f"def on_scene(scene): Path({str(sink)!r}).write_text(json.dumps(scene.settings))\n")
            info = list_extensions(folder)[0]
            ctx = context(info, folder)
            page = create_extension_page(info, ctx)
            self.assertIsInstance(page, ExtensionControlPage)
            self.assertFalse(sink.exists())
            page.editor.set_values({'power': 75})
            page.apply_button.click()
            self.assertEqual(json.loads(sink.read_text()), {'power': 75})
            self.assertEqual(json.loads((folder/'manual.json').read_text()), {'device': {'power': 75}})
            reopened = create_extension_page(info, context(info, folder))
            self.assertEqual(reopened.editor.values()['power'], 75)
            page.deleteLater(); reopened.deleteLater()

    def test_direct_apply_leaves_unrelated_continuous_effect_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'running.py').write_text("def on_scene(scene): pass\ndef on_stop(): pass\n")
            (folder/'static.py').write_text("def on_scene(scene): pass\n")
            infos = {i.stem: i for i in list_extensions(folder)}
            self.assertEqual(apply_extension(infos['running'], {}), [])
            self.assertEqual(apply_extension(infos['static'], {}), [])
            self.assertTrue(extension_running(infos['running']))

    def test_same_hardware_group_stops_its_continuous_effect_before_manual_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            events = folder/'events'
            (folder/'stream.py').write_text(
                "from pathlib import Path\nexclusive_group='same-device'\n"
                "def on_scene(scene): pass\n"
                f"def on_stop(): Path({str(events)!r}).write_text('stopped')\n")
            (folder/'static.py').write_text(
                "from pathlib import Path\nexclusive_group='same-device'\n"
                f"def on_scene(scene):\n    assert Path({str(events)!r}).read_text() == 'stopped'\n")
            infos = {i.stem:i for i in list_extensions(folder)}
            self.assertEqual(apply_extension(infos['stream'], {}), [])
            self.assertEqual(apply_extension(infos['static'], {}), [])
            self.assertFalse(extension_running(infos['stream']))

    def test_bundled_extensions_all_supply_their_own_control_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch('_colors.default_path', return_value=folder/'missing'), \
                 patch('_lianli.lianli_path', return_value=folder/'missing'), \
                 patch('_hue_page.load_hue', return_value=HueSettings()):
                infos = list_extensions(EXAMPLES)
                self.assertEqual(len(infos), 6)
                for info in infos:
                    with self.subTest(extension=info.stem):
                        ctx = context(info, folder)
                        page = create_extension_page(info, ctx)
                        self.assertIsInstance(page, QWidget)
                        self.assertIsInstance(page, ExtensionControlPage)
                        self.assertTrue(page.editor.is_checked())
                        if info.continuous:
                            self.assertTrue(page.stop_button.text().startswith('Stop'))
                        page.deleteLater()

    def test_hue_lamp_assignments_are_saved_without_painting_lamps(self):
        from _hue_page import HuePage
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            info = next(i for i in list_extensions(EXAMPLES) if i.stem == 'hue')
            settings = HueSettings(bridge_ip='bridge', username='private', blue=['1'], clientkey='ab'*16)
            with patch('_hue_page.load_hue', return_value=settings), patch('_hue_page.save_hue') as save, patch('_hue.paint_hue') as paint:
                page = HuePage(context(info, folder))
                page.setup._show_lamps((settings, [HueLight('1','Left'),HueLight('2','Right')]))
                page.setup.rows['2'].setCurrentIndex(2)
                page.setup.save_button.click()
                saved = save.call_args.args[0]
                self.assertEqual(saved.blue, ['1'])
                self.assertEqual(saved.purple, ['2'])
                self.assertEqual(saved.username, 'private')
                self.assertEqual(saved.clientkey, 'ab'*16)
                paint.assert_not_called()
                page.deleteLater()

    def test_sync_page_starts_and_stops_using_the_shared_extension_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            info = next(i for i in list_extensions(EXAMPLES) if i.stem == 'hue_sync')
            with patch('_hue_page.load_hue', return_value=HueSettings()), patch('_hue_sync.SyncProcess') as worker:
                ctx = context(info, folder)
                page = create_extension_page(info, ctx)
                page.editor.set_values({'area':'Workstation','brightness':85})
                page.apply_button.click()
                self.assertTrue(ctx.is_running())
                self.assertEqual(worker.return_value.start.call_args.args[0]['brightness'], 85)
                self.assertEqual(worker.return_value.start.call_args.args[0]['area'], 'Workstation')
                page.stop_button.click()
                worker.return_value.stop.assert_called_once()
                self.assertFalse(ctx.is_running())
                page.deleteLater()

    def test_lianli_manual_page_updates_colors_and_reloads_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder/'fans.json'
            path.write_text(json.dumps({'rgb': {'fans': {'mode':'TailChasing','colors':[[1,2,3],[4,5,6]]}},
                                        'lcds':[{'type':'sensor','sensor':{'text_color':[1,1,1],'gauge_ranges':[{'color':[2,2,2]}]}}]}))
            info = next(i for i in list_extensions(EXAMPLES) if i.stem=='lianli_fans')
            with patch('_lianli.lianli_path', return_value=path), patch('_lianli.reload_fans') as reload:
                page = create_extension_page(info, context(info, folder))
                page.editor.set_values({'led_blue':(9,8,7),'led_purple':(6,5,4),'text':(3,2,1),'gauge':(2,3,4)})
                page.apply_button.click()
                result = json.loads(path.read_text())
                self.assertEqual(result['rgb']['fans']['colors'], [[9,8,7],[6,5,4]])
                self.assertEqual(result['lcds'][0]['sensor']['text_color'], [3,2,1])
                reload.assert_called_once()
                page.deleteLater()

    def test_unreadable_manual_settings_are_preserved_before_hardware_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'device.py').write_text('def on_scene(scene): pass\n')
            ctx = context(list_extensions(folder)[0], folder)
            ctx.settings_path.write_text('broken json')
            page = create_extension_page(ctx.info, ctx)
            with patch('onigiri.ui.extension_page.apply_extension') as apply:
                page.apply_button.click()
                apply.assert_not_called()
            self.assertEqual(ctx.settings_path.read_text(), 'broken json')
            self.assertIn('could not be read', page.status.text())
            page.deleteLater()


    def test_page_background_actions_run_off_the_ui_thread(self):
        from onigiri.ui.jobs import SceneJobs
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'device.py').write_text('def on_scene(scene): pass\n')
            jobs = SceneJobs()
            ctx = context(list_extensions(folder)[0], folder, jobs=jobs)
            started, release = threading.Event(), threading.Event()
            threads, values = [], []
            def work():
                threads.append(threading.get_ident())
                started.set()
                release.wait(timeout=3)
                return 42
            ctx.run(work, lambda value: values.append((value, threading.get_ident())))
            try:
                for _ in range(100):
                    QTest.qWait(5)
                    if started.is_set(): break
                self.assertTrue(started.is_set())
                self.assertEqual(values, [])
                self.assertEqual(ctx.pending, 1)
            finally:
                release.set()
            for _ in range(100):
                QTest.qWait(5)
                if values: break
            self.assertNotEqual(threads[0], threading.get_ident())
            self.assertEqual(values, [(42, threading.get_ident())])
            self.assertEqual(ctx.pending, 0)
            jobs.shutdown()

    def test_hue_page_pairing_preserves_assignments_and_never_displays_credentials(self):
        from dataclasses import replace
        from _hue_page import HuePage
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            info = next(i for i in list_extensions(EXAMPLES) if i.stem=='hue')
            current = [HueSettings(bridge_ip='bridge', username='old-secret', blue=['1'], purple=['2'])]
            upgraded = replace(current[0], username='new-secret', clientkey='ab'*16)
            with patch('_hue_page.load_hue', side_effect=lambda: current[0]), \
                 patch('_hue_page.save_hue', side_effect=lambda settings: current.__setitem__(0, settings)) as save, \
                 patch('_hue_page.pair_entertainment', return_value=upgraded) as pair:
                page = HuePage(context(info, folder))
                page.setup.pair_button.click()
                pair.assert_called_once()
                save.assert_called_once_with(upgraded)
                self.assertEqual(current[0].blue, ['1'])
                self.assertEqual(current[0].purple, ['2'])
                labels = ' '.join(label.text() for label in page.findChildren(QLabel))
                self.assertNotIn('new-secret', labels)
                self.assertNotIn('ab'*16, labels)
                page.deleteLater()

    def test_custom_page_failure_is_contained_and_can_return_to_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'broken.py').write_text('def create_page(context): return 42\n')
            with patch.dict(os.environ, {'ONIGIRI_EXTENSIONS':str(folder)}):
                window = MainWindow(folder/'config.json',folder/'rules',folder/'autostart',reconfigure_fn=lambda:None)
                window._open_extension('broken')
                page = window._extension_pages['broken'][1]
                self.assertIn('must return a QWidget',page.text())
                window._show_extensions()
                self.assertEqual(window.extension_stack.currentIndex(),0)
                window._quitting=True
                window.close()


    def test_sync_area_refresh_selects_matching_area_and_accepts_manual_names(self):
        import importlib.util
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        if importlib.util.find_spec('hue_entertainment') is None:
            self.skipTest('optional sync dependency')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            info = next(i for i in list_extensions(EXAMPLES) if i.stem=='hue_sync')
            settings = HueSettings(bridge_ip='bridge', username='secret', clientkey='ab'*16)
            api = Mock()
            api.get_entertainment_areas = AsyncMock(return_value=[
                SimpleNamespace(id='desk-id', name='Desk'), SimpleNamespace(id='room-id', name='Room')])
            api.close = AsyncMock()
            with patch('_hue_page.load_hue', return_value=settings), \
                 patch('hue_entertainment.HueEntertainmentAPI', return_value=api):
                page = create_extension_page(info, context(info, folder))
                page.editor.set_values({'area':'Room'})
                page.refresh_areas()
                self.assertEqual(page.areas.currentText(), 'Room')
                self.assertEqual(page.editor.values()['area'], 'room-id')
                page.areas.setCurrentIndex(0)
                page.choose_area(0)
                self.assertEqual(page.editor.values()['area'], 'desk-id')
                page.areas.lineEdit().textEdited.emit('Another area')
                self.assertEqual(page.editor.values()['area'], 'Another area')
                api.close.assert_awaited_once()
                page.deleteLater()
