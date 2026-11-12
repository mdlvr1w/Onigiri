# Onigiri

Onigiri is a KDE Plasma ricing helper: tiled layouts, on-the-fly overlay placement, wallpaper, and RGB that follows the picture. Split a monitor into tiles, bind each tile to an application or a terminal command, and Onigiri writes KWin window rules that force those windows into place.

It needs a Plasma session with KWin. Closing the window leaves Onigiri in the system tray.
The main window requests KDE background blur through KWindowSystem, preserving its
panel and window transparency levels. KWin's Blur effect must be enabled; without
KWindowSystem or compositor blur support, the window retains ordinary transparency.

## Requirements

- Linux with KDE Plasma 6 and KWin. Desktop placement and global shortcuts need a
  running Plasma session; this is not a Windows or macOS application.
- Python 3.11 or newer, `venv`, and `pip`.
- PyQt6 6.7 or newer (installed from `requirements.txt`).
- `qdbus6` or `qdbus` for Plasma/KWin integration.
- Optional: KDE KWindowSystem and KWin's Blur effect for background blur.
- Optional: PipeWire and the KDE desktop portal for Wayland screen sync; a Hue
  Bridge and compatible color lights configured in an Entertainment area.

GPU, RAM, Ryuo and Lian Li extensions are optional integrations with separately
configured device software/daemons. Installing Onigiri does not install those
hardware services. Start with the core app, then enable only integrations you use.

## Download and run

Clone this repository or download its source ZIP from GitHub, then open a terminal
in the project directory:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m onigiri
```

Apply the current layout and launch its tiles for one profile:

```bash
.venv/bin/python -m onigiri --profile "Default"
```

## Install

Run the installer as your normal desktop user, without `sudo`:

```bash
./install.sh                 # Core app
./install.sh --extensions    # Also install bundled extension controls
./install.sh --hue-sync      # Also install Hue sync and its dependencies
```

`install.sh` creates a virtualenv under `~/.local/share/onigiri2`, launchers at `~/.local/bin/onigiri2` and `~/.local/bin/onigiri`, a desktop entry, and installs the icon into the hicolor theme.

## Tiles editor

The **Tiles** page combines profiles, layouts, and tile assignments. Choose a
profile and layout from the right-hand selectors; each **Manage** menu offers
create, rename, and delete actions. Edit tiles on the canvas and use **Apply
layout** to place their windows. Changes save automatically. The scene strip and
playback controls stay on **Scenes**; ongoing playback continues while editing tiles.

## Config

Layouts are stored in `~/.config/onigiri/onigiri.json`. An older Onigiri config at that path is copied to `onigiri.json.bak` and imported once.

Window rules are written to `~/.config/kwinrulesrc`. Onigiri only adds, updates, or removes rules whose description starts with `Onigiri:`. The first write in a session copies the file to `kwinrulesrc.onigiri-bak`.

## Scenes and extensions

Scenes combine an optional desktop background and extension settings. Choose
**Keep current background** for lighting-only scenes, **Picture** for a wallpaper
with a crop position per monitor, or **Solid color** for a color across all displays.
The creator and sidebar editor support all three choices. Existing image scenes
remain compatible; scenes without a background leave the desktop unchanged.
The scene strip supports reordering and timed playback
in a continuous loop or random order until stopped. Each interval begins after
the previous scene finishes applying; a failed scene does not stop playback.
Scene application runs in a background queue so
hardware and network updates leave the editor responsive. Failures appear
in the status bar; an individual extension can also be tested from its panel.

Click any item on **Extensions** to open its own standalone control page. All
bundled extensions support direct control without creating or editing a scene,
including Hue bridge setup and screen-sync Start/Stop. Manual settings are kept
separate from scenes. Install/update the bundled pages with `./install.sh --extensions`,
or use `--hue-sync` to include sync dependencies. See the
[extension controls and page API](examples/extensions/README.md).

The extension installer copies the bundled plugins and their helpers into
`~/.config/onigiri/extensions/`. You can also copy individual plugins and their
underscore-prefixed helpers from `examples/extensions/`. These are trusted Python
scripts executed with your user's permissions. An empty extension folder means no
hardware is changed. The Hue lamps page supports bridge pairing and lamp assignments.

## Recovery and troubleshooting

If configuration cannot be read, Onigiri shows the error and opens a temporary
default profile without replacing your configuration or existing KWin rules.
Closing the app leaves the unreadable file untouched. If you choose to save or
edit afterward, its original bytes are preserved beside it in a uniquely named
`onigiri.json.recovery-*` file before the new configuration is saved.
Configuration and KWin rule updates use atomic file replacement.

Window placement and global shortcuts require a live Plasma/KWin session.
If a scene only partially applies, check the status bar and use the extension
panel's test action to identify a failing hardware integration.

Run the test suite with the same environment used for the app:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -v
```

## Optional Hue screen sync

`./install.sh --hue-sync` installs the optional continuous screen-sync extension.
It shares the static Hue setup, adds one-time Entertainment authorization, and
uses KDE's screen-sharing picker to capture a monitor. Enable it explicitly in a
scene; installing it does not start capture. See
[Hue screen-sync setup and behavior](examples/extensions/HUE_SYNC.md).


## Local data and privacy

This repository contains application source, generic assets and synthetic tests.
It does not include desktop layouts, wallpapers, paired bridges, credentials,
light assignments, local editor state or hardware configuration from a development
machine. Onigiri discovers screens and applications at runtime and stores user
settings outside the source tree.

The runtime locations documented above are per-user defaults, not files bundled
with the project. Do not attach `hue.json`, private configuration files, recovery
backups or unsanitized screenshots to public issues.

## License

Onigiri's original source and included assets are provided under the [MIT License](LICENSE).
Dependencies retain their own licenses; in particular, PyQt6 is GPLv3/commercial
licensed. See [dependency licensing notes](THIRD_PARTY_NOTICES.md) before distributing
bundled application builds.
