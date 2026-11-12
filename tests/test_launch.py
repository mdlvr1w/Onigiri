import shlex
import unittest

from onigiri.launch import (
    DesktopApp,
    activate_app,
    assign_app,
    clean_exec,
    command_is_running,
    desktop_path,
    duplicate_classes,
    ensure_konsole_color_scheme,
    exec_basename,
    resolve_tile,
    running_applications,
    spawn,
    terminal_command,
    unique_class,
    uses_private_window,
)
from onigiri.model import Tile


class LaunchTests(unittest.TestCase):
    def test_desktop_field_codes_are_removed(self) -> None:
        self.assertEqual(clean_exec("firefox %u"), "firefox")
        self.assertEqual(clean_exec("app --flag %F"), "app --flag")
        self.assertEqual(clean_exec("quoted %%u"), "quoted %u")
        self.assertEqual(exec_basename("env FOO=bar /usr/bin/kitty"), "kitty")

    def test_terminals_get_a_stable_class(self) -> None:
        cls = unique_class("Dash Board", "abc")
        self.assertEqual(cls, "onigiri-dash-board-abc")
        command, kind, value = terminal_command("alacritty", cls, "BTOP", "btop")
        parts = shlex.split(command)
        self.assertEqual(parts[0], "alacritty")
        self.assertEqual(parts[parts.index("--class") + 1], f"{cls},{cls}")
        self.assertEqual(parts[parts.index("-o") + 1], "ipc_socket=false")
        self.assertEqual(kind, "class")
        self.assertEqual(value, cls)
        self.assertIn("btop; exec $SHELL", parts)

        kitty, kitty_kind, _value = terminal_command("kitty", cls, "BTOP", "btop")
        kparts = shlex.split(kitty)
        self.assertEqual(kparts[0], "kitty")
        self.assertEqual(kparts[kparts.index("--class") + 1], cls)
        self.assertEqual(kparts[kparts.index("--name") + 1], cls)
        self.assertEqual(kitty_kind, "class")

        xterm, xterm_kind, _value = terminal_command("xterm", cls, "BTOP", "")
        xparts = shlex.split(xterm)
        self.assertEqual(xparts[xparts.index("-class") + 1], cls)
        self.assertEqual(xterm_kind, "class")
        self.assertNotIn("-e", xparts)

        konsole, konsole_kind, konsole_value = terminal_command("konsole", cls, "BTOP", "btop")
        kparts = shlex.split(konsole)
        self.assertEqual(kparts[0], "konsole")
        self.assertEqual(kparts[kparts.index("--qwindowtitle") + 1], cls)
        self.assertEqual(kparts[kparts.index("--desktopfile") + 1], cls)
        self.assertIn(f"LocalTabTitleFormat={cls}", kparts)
        self.assertIn(f"tabtitle={cls}", kparts)
        self.assertEqual(konsole_kind, "title")
        self.assertEqual(konsole_value, cls)
        self.assertNotIn("--hide-toolbars", kparts)

        hidden, _kind, _value = terminal_command("konsole", cls, "BTOP", "btop", hide_toolbar=True)
        self.assertIn("--hide-toolbars", shlex.split(hidden))
        alacritty, _kind, _value = terminal_command("alacritty", cls, "BTOP", "btop", hide_toolbar=True)
        self.assertNotIn("--hide-toolbars", shlex.split(alacritty))

    def test_opacity_and_title_are_optional(self) -> None:
        cls = "onigiri-dash-tile"
        solid, _kind, _value = terminal_command("alacritty", cls, "BTOP", "btop", opacity=1, show_title=True)
        parts = shlex.split(solid)
        self.assertEqual(parts[parts.index("--title") + 1], "BTOP")
        solid_opts = [parts[index + 1] for index, part in enumerate(parts) if part == "-o"]
        self.assertEqual(solid_opts, ["ipc_socket=false"])

        clear, _kind, _value = terminal_command("alacritty", cls, "BTOP", "", opacity=0.4, show_title=False)
        clear_parts = shlex.split(clear)
        self.assertNotIn("--title", clear_parts)
        clear_opts = [clear_parts[index + 1] for index, part in enumerate(clear_parts) if part == "-o"]
        self.assertEqual(clear_opts, ["ipc_socket=false", "window.opacity=0.40"])

        konsole, _kind, _value = terminal_command("konsole", cls, "BTOP", "", opacity=0.4, show_title=False)
        konsole_parts = shlex.split(konsole)
        self.assertEqual(konsole_parts[konsole_parts.index("--qwindowtitle") + 1], cls)
        konsole_props = [konsole_parts[index + 1] for index, part in enumerate(konsole_parts) if part == "-p"]
        self.assertIn("ColorScheme=onigiri-40", konsole_props)
        self.assertFalse(any(prop.startswith("Opacity=") for prop in konsole_props))
        self.assertIn(f"LocalTabTitleFormat={cls}", konsole_props)

        solid_konsole, _kind, _value = terminal_command("konsole", cls, "BTOP", "", opacity=1)
        solid_props = [shlex.split(solid_konsole)[index + 1] for index, part in enumerate(shlex.split(solid_konsole)) if part == "-p"]
        self.assertFalse(any(prop.startswith("ColorScheme=") for prop in solid_props))

        xterm, _kind, _value = terminal_command("xterm", cls, "BTOP", "", opacity=0.4, show_title=False)
        xparts = shlex.split(xterm)
        self.assertNotIn("-T", xparts)
        self.assertNotIn("-o", xparts)

    def test_font_size_is_passed_to_every_terminal(self) -> None:
        cls = "onigiri-dash-tile"
        unchanged, _kind, _value = terminal_command("alacritty", cls, "BTOP", "", font_size=0)
        self.assertNotIn("font.size", unchanged)

        alacritty, _kind, _value = terminal_command("alacritty", cls, "BTOP", "", font_size=14)
        self.assertIn("font.size=14", shlex.split(alacritty))

        kitty, _kind, _value = terminal_command("kitty", cls, "BTOP", "", font_size=14)
        self.assertIn("font_size=14", shlex.split(kitty))

        konsole, _kind, _value = terminal_command("konsole", cls, "BTOP", "", font_size=14)
        konsole_props = [shlex.split(konsole)[index + 1] for index, part in enumerate(shlex.split(konsole)) if part == "-p"]
        self.assertTrue(any(prop.startswith("Font=Monospace,14,") for prop in konsole_props))

        xterm, _kind, _value = terminal_command("xterm", cls, "BTOP", "", font_size=14)
        xparts = shlex.split(xterm)
        self.assertEqual(xparts[xparts.index("-fs") + 1], "14")
        self.assertEqual(xparts[xparts.index("-fa") + 1], "Monospace")

    def test_duplicate_application_classes_are_reported(self) -> None:
        first = Tile(id="a", name="One", launch_mode="app", command="firefox", match_kind="class", match_value="firefox")
        second = Tile(id="b", name="Two", launch_mode="app", command="firefox", match_kind="class", match_value="firefox")
        third = Tile(id="c", name="Three", launch_mode="helper", terminal="alacritty", shell_command="btop")
        dupes = duplicate_classes([first, second, third], "Dash")
        self.assertEqual(dupes, {"firefox"})
        resolved = resolve_tile(third, "Dash")
        self.assertEqual(resolved.match_kind, "class")
        self.assertFalse(resolved.use_shell)
        raw = resolve_tile(Tile(id="d", name="Raw", command="echo hi"), "Dash")
        self.assertEqual(raw.match_kind, "")
        self.assertTrue(raw.use_shell)

    def test_terminal_apps_get_a_private_class(self) -> None:
        tile = Tile(
            id="over",
            name="Alacritty",
            launch_mode="app",
            command="alacritty",
            match_kind="class",
            match_value="Alacritty",
        )
        layout = resolve_tile(tile, "Default")
        overlay = resolve_tile(tile, "Default", overlay=True)
        self.assertEqual(layout.match_value, unique_class("Default", "over"))
        self.assertEqual(overlay.match_value, unique_class("Default", "over", overlay=True))
        self.assertNotEqual(layout.match_value, overlay.match_value)
        self.assertTrue(layout.match_value.startswith("onigiri-"))
        self.assertTrue(overlay.match_value.startswith("onigiri-ov-"))
        self.assertIn(f"--class {layout.match_value},{layout.match_value}", layout.command)
        self.assertNotEqual(layout.match_value.casefold(), "alacritty")
        assigned = assign_app(Tile(id="t", name="Tile"), DesktopApp("Alacritty.desktop", "Alacritty", "alacritty", "Alacritty"))
        self.assertEqual(assigned.launch_mode, "helper")
        self.assertEqual(assigned.terminal, "alacritty")
        self.assertEqual(assigned.match_value, "")
        firefox = assign_app(Tile(id="f", name="Tile"), DesktopApp("firefox.desktop", "Firefox", "firefox", "firefox"))
        self.assertEqual(firefox.launch_mode, "app")
        self.assertEqual(firefox.match_value, "firefox")

    def test_activate_app_does_not_gio_launch_private_windows(self) -> None:
        self.assertTrue(uses_private_window("alacritty --class onigiri-ov-x,onigiri-ov-x"))
        self.assertFalse(uses_private_window("discord"))
        from pathlib import Path
        from unittest.mock import patch

        command = "alacritty --class onigiri-ov-x,onigiri-ov-x"
        with patch("onigiri.launch.subprocess.Popen") as popen:
            with patch("onigiri.launch.desktop_path", return_value=Path("/usr/share/applications/Alacritty.desktop")):
                activate_app(command, False, "Alacritty.desktop")
        args = popen.call_args[0][0]
        self.assertEqual(args[0], "alacritty")
        self.assertNotIn("gio", args)

    def test_spawn_clears_alacritty_socket(self) -> None:
        from unittest.mock import patch

        with patch("onigiri.launch.subprocess.Popen") as popen:
            with patch.dict("os.environ", {"ALACRITTY_SOCKET": "/tmp/alacritty.sock"}, clear=False):
                spawn("alacritty --class foo,foo", False)
        env = popen.call_args.kwargs["env"]
        self.assertIsNotNone(env)
        self.assertNotIn("ALACRITTY_SOCKET", env)

    def test_running_applications_match_process_names(self) -> None:
        firefox = DesktopApp("firefox.desktop", "Firefox", "firefox", "firefox")
        code = DesktopApp("code.desktop", "VS Code", "code", "Code")
        running = running_applications([firefox, code], names={"firefox", "bash"})
        self.assertEqual([app.name for app in running], ["Firefox"])

    def test_command_is_running_uses_class_or_executable(self) -> None:
        self.assertTrue(command_is_running("firefox", "firefox", names={"firefox"}))
        self.assertTrue(command_is_running("/usr/bin/firefox", "Firefox", names={"firefox"}))
        self.assertFalse(command_is_running("firefox", "firefox", names={"bash"}))
        self.assertFalse(command_is_running("flatpak run org.mozilla.firefox", "Firefox", names={"flatpak"}))

    def test_konsole_color_scheme_carries_background_opacity(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch("onigiri.launch._konsole_data_dir", return_value=folder):
                name = ensure_konsole_color_scheme(0.6)
            path = folder / "onigiri-60.colorscheme"
            self.assertEqual(name, "onigiri-60")
            text = path.read_text(encoding="utf-8")
            self.assertIn("Opacity=0.60", text)
            self.assertIn("[Background]", text)

    def test_desktop_path_finds_discord(self) -> None:
        path = desktop_path("discord.desktop")
        if path is None:
            self.skipTest("discord.desktop is not installed")
        self.assertEqual(path.name, "discord.desktop")
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
