"""Regression coverage for recovery, partial layouts, and scene failures."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtTest import QTest
from tests.test_ui import app
from onigiri.model import Config, Leaf, Scene, Split, Tile, iter_tiles, split_leaf
from onigiri.session import Session
from onigiri.scenes import Scenes
from onigiri.store import save_config
from onigiri.kwin import KWinRules
from onigiri.ui.jobs import SceneJobs
from onigiri.ui.main_window import MainWindow


class ReliabilityTests(unittest.TestCase):
    def test_split_with_empty_sibling(self):
        for empty_first in (True, False):
            occupied, empty = Leaf(Tile("keep", "Keep")), Leaf(None)
            root = Split("v", .5, empty if empty_first else occupied,
                         occupied if empty_first else empty)
            tree, created = split_leaf(root, "keep", "h")
            self.assertEqual({tile.id for tile in iter_tiles(tree)}, {"keep", created})

    def test_failed_startup_preserves_config_and_rules(self):
        app()
        with tempfile.TemporaryDirectory() as tmp:
            config, rules = Path(tmp)/"config.json", Path(tmp)/"kwinrulesrc"
            config.write_text('{"broken":')
            original_rules = "[General]\ncount=1\nrules=mine\n[mine]\nDescription=Onigiri:Saved:x\n"
            rules.write_text(original_rules)
            window = MainWindow(config, rules, reconfigure_fn=lambda: None)
            self.assertTrue(window.session.load_error)
            self.assertEqual(config.read_text(), '{"broken":')
            self.assertEqual(rules.read_text(), original_rules)
            # An intentional subsequent edit can save, but preserves the source.
            window.session.save()
            backups = list(Path(tmp).glob("config.json.recovery-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), '{"broken":')
            window.deleteLater()

    def test_failed_atomic_replace_leaves_both_files_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            config, rules = folder/"config.json", folder/"kwinrulesrc"
            config.write_text("original config")
            rules.write_text("original rules")
            writer = KWinRules(rules, reconfigure_fn=lambda: None)
            with patch("onigiri.files.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    save_config(Config(), config)
                with self.assertRaises(OSError):
                    writer._write("new rules")
            self.assertEqual(config.read_text(), "original config")
            self.assertEqual(rules.read_text(), "original rules")
            self.assertEqual(list(folder.glob(".*")), [])

    def test_reorder_preserves_active_scene_and_unlisted_scenes(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(Path(tmp)/"config.json", screens_fn=lambda: [])
            session.config.scenes = [Scene("a", "A"), Scene("b", "B"), Scene("c", "C")]
            scenes = Scenes(session)
            scenes.reorder(["b", "c", "b"])
            self.assertEqual([s.id for s in session.config.scenes], ["b", "c", "a"])
            self.assertEqual(session.config.scenes[scenes.playlist_index].id, "a")

    def test_extension_failures_reach_scene_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenes = Scenes(Session(Path(tmp)/"config.json"), set_wallpaper_fn=lambda *args: None)
            with patch("onigiri.scenes.apply_scene", return_value=["Hue offline"]):
                self.assertEqual(scenes.apply(Scene("a", "A")), ["Hue offline"])

    def test_jobs_keep_ui_responsive_and_deliver_errors_on_ui_thread(self):
        app()
        jobs = SceneJobs()
        release = threading.Event()
        started = threading.Event()
        results = []
        caller = threading.get_ident()

        def slow():
            started.set()
            release.wait(2)
            return ["offline"]

        jobs.submit(slow, lambda errors: results.append((errors, threading.get_ident())))
        try:
            self.assertTrue(started.wait(1))
            QTest.qWait(10)
            self.assertEqual(results, [])
            jobs.submit(lambda: (_ for _ in ()).throw(RuntimeError("failed")),
                        lambda errors: results.append((errors, threading.get_ident())))
        finally:
            release.set()
        for _ in range(100):
            QTest.qWait(10)
            if len(results) == 2:
                break
        self.assertEqual(results, [(["offline"], caller), (["failed"], caller)])
        jobs.pool.waitForDone()
