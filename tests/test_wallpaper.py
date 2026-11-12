import tempfile
import unittest
from pathlib import Path

from onigiri.wallpaper import (
    assign_wallpapers,
    cover_source_rect,
    parse_screen_script,
    read_desktop_wallpapers,
    set_scene_wallpaper,
    set_wallpaper,
    wallpaper_for,
)


def _plasma(folder: Path, wide: Path, hdmi: Path, panel: Path) -> str:
    return f"""
[Containments][4]
activityId=old
formfactor=0
lastScreen=3
location=0
plugin=org.kde.plasma.folder
wallpaperplugin=org.kde.image

[Containments][4][Wallpaper][org.kde.image][General]
Image={folder / "folder.png"}

[Containments][29]
lastScreen=0
plugin=org.kde.desktopcontainment
wallpaperplugin=org.kde.image

[Containments][29][Wallpaper][org.kde.image][General]
Image=file://{wide}
FillMode=2

[Containments][30]
lastScreen=1
plugin=org.kde.desktopcontainment
wallpaperplugin=org.kde.image

[Containments][30][Wallpaper][org.kde.image][General]
Image={hdmi}
FillMode=1

[Containments][5]
lastScreen=0
plugin=org.kde.panel
wallpaperplugin=org.kde.image

[Containments][5][Wallpaper][org.kde.image][General]
Image={panel}
"""


class WallpaperTests(unittest.TestCase):
    def test_desktop_wallpapers_ignore_panels_and_keep_fill(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wide = root / "wide.png"
            hdmi = root / "hdmi.png"
            panel = root / "panel.png"
            folder_image = root / "folder.png"
            for path in (wide, hdmi, panel, folder_image):
                path.write_bytes(b"png")
            found = read_desktop_wallpapers(_plasma(root, wide, hdmi, panel))
            keyed = {(item.screen, item.path, item.fill, item.desktop) for item in found}
            self.assertIn((0, str(wide), "crop", True), keyed)
            self.assertIn((1, str(hdmi), "fit", True), keyed)
            self.assertNotIn(str(panel), {item.path for item in found})

    def test_screens_match_the_desktop_that_contains_them(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wide = root / "wide.png"
            hdmi = root / "hdmi.png"
            for path in (wide, hdmi, root / "panel.png", root / "folder.png"):
                path.write_bytes(b"png")
            wallpapers = read_desktop_wallpapers(_plasma(root, wide, hdmi, root / "panel.png"))
            geometries = {
                3: (0, 1080, 2560, 1440),
                0: (0, 1080, 2560, 1440),
                1: (240, 0, 1920, 1080),
            }
            screens = [
                ("TEST-WIDE", 0, 1080, 2560, 1440),
                ("TEST-HDMI", 240, 0, 1920, 1080),
            ]
            assigned = assign_wallpapers(screens, wallpapers, geometries)
            self.assertEqual(assigned["TEST-WIDE"], (str(wide), "crop"))
            self.assertEqual(assigned["TEST-HDMI"], (str(hdmi), "fit"))

    def test_one_wallpaper_covers_every_screen_without_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "only.png"
            image.write_bytes(b"png")
            text = f"""
[Containments][1]
lastScreen=0
plugin=org.kde.desktopcontainment
wallpaperplugin=org.kde.image

[Containments][1][Wallpaper][org.kde.image][General]
Image={image}
"""
            assigned = wallpaper_for(
                [("TEST-WIDE", 0, 0, 100, 100), ("TEST-HDMI", 100, 0, 100, 100)],
                config_text=text,
                query_screens=False,
            )
            self.assertEqual(assigned["TEST-WIDE"][0], str(image))
            self.assertEqual(assigned["TEST-HDMI"][0], str(image))

    def test_script_output_and_real_files(self) -> None:
        self.assertEqual(parse_screen_script("0\t10\t20\t30\t40\nnoise\n"), {0: (10, 20, 30, 40)})
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "slide.png"
            image.write_bytes(b"png")
            text = f"""
[Containments][2]
lastScreen=0
plugin=org.kde.desktopcontainment
wallpaperplugin=org.kde.slideshow

[Containments][2][Wallpaper][org.kde.slideshow][General]
SlidePaths={folder}
"""
            found = read_desktop_wallpapers(text)
            self.assertEqual(found[0].path, str(image))

    def test_set_wallpaper_script_uses_file_uri_and_crop(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "wall.png"
            image.write_bytes(b"png")
            scripts: list[str] = []
            set_wallpaper(str(image), run=lambda script: scripts.append(script) or True)
            self.assertEqual(len(scripts), 1)
            self.assertIn(image.resolve().as_uri(), scripts[0])
            self.assertIn('d.writeConfig("FillMode", 2)', scripts[0])
            self.assertIn("org.kde.image", scripts[0])
            with self.assertRaises(RuntimeError):
                set_wallpaper(str(Path(folder) / "missing.png"), run=lambda _script: True)

    def test_cover_source_rect_pans_a_portrait_display(self) -> None:
        left = cover_source_rect(1920, 1080, 720, 1280, 0.0, 0.5)
        right = cover_source_rect(1920, 1080, 720, 1280, 1.0, 0.5)
        center = cover_source_rect(1920, 1080, 720, 1280, 0.5, 0.5)
        self.assertEqual(left[1], 0)
        self.assertEqual(left[3], 1080)
        self.assertEqual(left[0], 0)
        self.assertEqual(right[0] + right[2], 1920)
        self.assertGreater(right[0], left[0])
        self.assertGreater(center[0], left[0])
        self.assertLess(center[0], right[0])

    def test_scene_wallpaper_writes_a_crop_per_screen(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "wall.png"
            image.write_bytes(b"png")
            cache = Path(folder) / "crops"
            written: list[tuple] = []
            scripts: list[str] = []

            def render(path, dest, width, height, pan_x, pan_y):
                written.append((path, str(dest), width, height, pan_x, pan_y))
                Path(dest).write_bytes(b"png")

            set_scene_wallpaper(
                str(image),
                [
                    ("TEST-WIDE", 0, 1080, 2560, 1440),
                    ("TEST-PORTRAIT", 2560, 480, 720, 1280),
                ],
                {"TEST-PORTRAIT": (0.0, 0.5)},
                run=lambda script: scripts.append(script) or True,
                cache_dir=cache,
                render=render,
            )
            self.assertEqual(len(written), 2)
            by_size = {(item[2], item[3], item[4], item[5]) for item in written}
            self.assertIn((2560, 1440, 0.5, 0.5), by_size)
            self.assertIn((720, 1280, 0.0, 0.5), by_size)
            self.assertEqual(len(scripts), 1)
            self.assertIn('d.writeConfig("FillMode", 0)', scripts[0])
            self.assertIn("var targets =", scripts[0])
            self.assertIn("2560", scripts[0])
            self.assertIn("720", scripts[0])


if __name__ == "__main__":
    unittest.main()
