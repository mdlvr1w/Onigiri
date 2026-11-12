import tempfile
import unittest
from pathlib import Path

from onigiri.autostart import autostart_text, write_autostart
from onigiri.kwin import (
    OVERLAY_PREFIX,
    KWinRules,
    Placement,
    _parse,
    close_script,
    close_stray_alacritty_script,
    close_stray_terminals_script,
    match_keys,
    place_script,
    rule_desktop_file,
)


ORIGINAL = """# keep me
[General]
count=1
rules=foreign-id
SomeOther=keep

[foreign-id]
Description=User rule with 100% and percent
wmclass=firefox
"""


def _placement(**kwargs) -> Placement:
    data = dict(
        tile_id="tile",
        description="Onigiri:Dash:tile",
        x=10,
        y=20,
        width=300,
        height=400,
        match_kind="class",
        match_value="onigiri-dash-tile",
        no_border=True,
        skip_taskbar=False,
        command="true",
        use_shell=False,
    )
    data.update(kwargs)
    return Placement(**data)


class KWinTests(unittest.TestCase):
    def test_rules_round_trip_keeps_foreign_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kwinrulesrc"
            path.write_text(ORIGINAL, encoding="utf-8")
            calls: list[str] = []
            rules = KWinRules(path, reconfigure_fn=lambda: calls.append("re"))
            count = rules.replace_onigiri_rules([_placement()])
            self.assertEqual(count, 1)
            text = path.read_text(encoding="utf-8")
            _preamble, sections = _parse(text)
            foreign = dict(sections)["foreign-id"]
            self.assertEqual(
                foreign,
                "[foreign-id]\nDescription=User rule with 100% and percent\nwmclass=firefox\n",
            )
            self.assertTrue(text.startswith("# keep me\n"))
            self.assertIn("SomeOther=keep", text)
            self.assertIn("positionrule=2", text)
            self.assertIn("sizerule=2", text)
            self.assertIn("ignoregeometry=true", text)
            self.assertIn("ignoregeometryrule=2", text)
            self.assertIn("wmclass=onigiri-dash-tile", text)
            self.assertIn("noborder=true", text)
            self.assertNotIn("skiptaskbar=", text)
            self.assertEqual(calls, ["re"])
            backup = Path(str(path) + ".onigiri-bak")
            self.assertEqual(backup.read_text(encoding="utf-8"), ORIGINAL)

            listed = {rule.description: rule for rule in rules.list_rules()}
            onigiri_id = next(rule.id for rule in listed.values() if rule.owned)
            rules.set_enabled(onigiri_id, False)
            disabled = next(rule for rule in rules.list_rules() if rule.id == onigiri_id)
            self.assertFalse(disabled.enabled)
            foreign_after = dict(_parse(path.read_text(encoding="utf-8"))[1])["foreign-id"]
            self.assertEqual(foreign_after, foreign)

            rules.delete_rule(onigiri_id)
            remaining = rules.list_rules()
            self.assertEqual([rule.id for rule in remaining], ["foreign-id"])
            self.assertIn("rules=foreign-id", path.read_text(encoding="utf-8"))

    def test_close_script_targets_only_named_windows(self) -> None:
        script = close_script({"onigiri-dash-tile", ""})
        self.assertIn('["onigiri-dash-tile"]', script)
        self.assertIn("toLowerCase()", script)
        self.assertIn("closeWindow", script)

    def test_stray_alacritty_script_skips_onigiri_classes(self) -> None:
        script = close_stray_alacritty_script()
        self.assertEqual(script, close_stray_terminals_script())
        self.assertIn('cls === "alacritty"', script)
        self.assertIn('cls === "konsole"', script)
        self.assertIn("onigiri-", script)
        self.assertIn("closeWindow", script)
        self.assertIn("desktopFileName", script)
        self.assertIn('owned(w)', script)

    def test_place_script_moves_matching_windows(self) -> None:
        script = place_script(
            [
                _placement(
                    match_value="firefox",
                    command="firefox",
                    x=100,
                    y=200,
                    width=640,
                    height=480,
                    no_border=True,
                    skip_taskbar=True,
                )
            ]
        )
        self.assertIn("firefox", script)
        self.assertIn("frameGeometry", script)
        self.assertIn("minimized = false", script)
        self.assertIn("activeWindow", script)
        self.assertIn('"x": 100', script)
        self.assertIn('"y": 200', script)
        self.assertIn('"width": 640', script)
        self.assertIn('"height": 480', script)
        self.assertIn('"noBorder": true', script)
        self.assertIn('"skipTaskbar": true', script)
        self.assertIn("ONIGIRI_PLACE_RESULT", script)
        self.assertEqual(place_script([_placement(match_kind="", match_value="", command="")]), "")

    def test_place_script_does_not_match_generic_terminal_names(self) -> None:
        script = place_script(
            [
                _placement(
                    match_value="onigiri-ov-default-over",
                    command="alacritty --class onigiri-ov-default-over,onigiri-ov-default-over",
                )
            ]
        )
        self.assertIn("onigiri-ov-default-over", script)
        self.assertNotIn("alacritty", script)
        self.assertIn('needle.indexOf("onigiri-") === 0', script)
        self.assertIn("cap.indexOf(needle)", script)

    def test_chromium_pwas_match_desktop_file_not_x11_class(self) -> None:
        youtube = _placement(
            match_value="crx_agimnkijcaahngcdmfeangaknmldooml",
            app_id="chrome-agimnkijcaahngcdmfeangaknmldooml-Default.desktop",
            command="/usr/bin/chromium --profile-directory=Default --app-id=agimnkijcaahngcdmfeangaknmldooml",
        )
        music = _placement(
            match_value="crx_abcdefghijklmnopabcdefghijklmnop",
            app_id="chrome-abcdefghijklmnopabcdefghijklmnop-Default.desktop",
            command="/usr/bin/chromium --profile-directory=Default --app-id=abcdefghijklmnopabcdefghijklmnop",
        )
        keys = match_keys(youtube.match_value, youtube.app_id, youtube.command)
        self.assertIn("chrome-agimnkijcaahngcdmfeangaknmldooml-Default", keys)
        self.assertIn("agimnkijcaahngcdmfeangaknmldooml", keys)
        self.assertNotIn("chromium", keys)
        self.assertNotEqual(
            match_keys(youtube.match_value, youtube.app_id, youtube.command),
            match_keys(music.match_value, music.app_id, music.command),
        )
        self.assertEqual(
            rule_desktop_file(youtube),
            "chrome-agimnkijcaahngcdmfeangaknmldooml-Default",
        )
        script = place_script([youtube, music])
        self.assertIn("chrome-agimnkijcaahngcdmfeangaknmldooml-Default", script)
        self.assertIn("chrome-abcdefghijklmnopabcdefghijklmnop-Default", script)
        self.assertIn("value >= 70", script)
        self.assertNotIn('"chromium"', script)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kwinrulesrc"
            rules = KWinRules(path, reconfigure_fn=lambda: None)
            rules.replace_onigiri_rules([youtube, music])
            text = path.read_text(encoding="utf-8")
            self.assertIn("wmclass=crx_agimnkijcaahngcdmfeangaknmldooml", text)
            self.assertIn("wmclass=crx_abcdefghijklmnopabcdefghijklmnop", text)
            self.assertNotIn("desktopfile=", text)
            self.assertNotIn("wmclass=chromium", text)

    def test_empty_match_does_not_write_a_catch_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kwinrulesrc"
            rules = KWinRules(path, reconfigure_fn=lambda: None)
            count = rules.replace_onigiri_rules([_placement(match_kind="", match_value="")])
            self.assertEqual(count, 0)
            self.assertNotIn("wmclass=", path.read_text(encoding="utf-8"))

    def test_overlay_rules_do_not_replace_layout_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kwinrulesrc"
            rules = KWinRules(path, reconfigure_fn=lambda: None)
            rules.replace_onigiri_rules([_placement()])
            rules.replace_onigiri_rules(
                [_placement(description="OnigiriOverlay:Dash:over", match_value="firefox")],
                prefix=OVERLAY_PREFIX,
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("Onigiri:Dash:tile", text)
            self.assertIn("OnigiriOverlay:Dash:over", text)
            self.assertIn("wmclass=onigiri-dash-tile", text)
            self.assertIn("wmclass=firefox", text)
            rules.replace_onigiri_rules([_placement(match_value="other")])
            text = path.read_text(encoding="utf-8")
            self.assertIn("OnigiriOverlay:Dash:over", text)
            self.assertIn("wmclass=other", text)
            self.assertNotIn("onigiri-dash-tile", text)


class AutostartTests(unittest.TestCase):
    def test_desktop_entry_is_not_indented(self) -> None:
        text = autostart_text("My Profile", 'python -m onigiri --profile "My Profile"')
        self.assertTrue(text.startswith("[Desktop Entry]\n"))
        for line in text.splitlines():
            self.assertFalse(line.startswith(" ") or line.startswith("\t"))
            if not line.startswith("["):
                self.assertIn("=", line)
        self.assertIn('--profile "My Profile"', text)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_autostart("My Profile", Path(tmp) / "onigiri.desktop")
            written = path.read_text(encoding="utf-8")
            self.assertIn("--profile", written)
            self.assertIn("--hidden", written)
            self.assertIn("X-KDE-autostart-after=panel", written)
            self.assertFalse(any(line.startswith(" ") for line in written.splitlines()))


if __name__ == "__main__":
    unittest.main()
