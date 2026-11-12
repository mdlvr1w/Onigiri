import os
import random
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from onigiri.model import Config, Playlist, Scene
from onigiri.playlist import interval_ms, move_id, next_index
from onigiri.store import save_config, load_config


class PlaylistTests(unittest.TestCase):
    def test_interval_and_order(self) -> None:
        self.assertEqual(interval_ms(2, "seconds"), 2000)
        self.assertEqual(interval_ms(3, "minutes"), 180000)
        self.assertEqual(interval_ms(1, "hours"), 3600000)
        self.assertEqual(next_index(3, 2, "repeat"), 0)
        self.assertEqual(next_index(3, 0, "repeat"), 1)
        self.assertEqual(next_index(1, 0, "random"), 0)
        self.assertIn(next_index(3, 1, "random", random.Random(0)), (0, 2))
        self.assertEqual(move_id(["a", "b", "c"], "a", 3), ["b", "c", "a"])
        self.assertEqual(move_id(["a", "b", "c"], "c", 0), ["c", "a", "b"])
        self.assertEqual(move_id(["a", "b", "c"], "b", 1), ["a", "b", "c"])

    def test_playlist_round_trips_with_scenes(self) -> None:
        config = Config(
            scenes=[Scene(id="sc1", name="Night"), Scene(id="sc2", name="Day")],
            playlist=Playlist(interval=12, unit="seconds", mode="random", playing=True),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "onigiri.json"
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.playlist.interval, 12)
            self.assertEqual(loaded.playlist.unit, "seconds")
            self.assertEqual(loaded.playlist.mode, "random")
            self.assertTrue(loaded.playlist.playing)
            self.assertEqual([scene.id for scene in loaded.scenes], ["sc1", "sc2"])
            self.assertEqual(Config.from_dict({"version": 2, "profiles": []}).playlist.unit, "minutes")
