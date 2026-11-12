# Hue screen sync

Optional Ambilight-style screen colors for Hue lamps. Runs as `hue_sync.py`,
using the same `~/.config/onigiri/hue.json` bridge setup as `hue.py`.
The screen capture and network loop run in a separate process.

## Install

From the Onigiri2 source directory:

```bash
./install.sh --hue-sync
```

This installs optional dependencies into the existing v2 environment and updates
the bundled extensions and their helpers, including `_hue_page.py`, in your
extensions directory. Replaced extension files are backed up under
`extensions/.backups/`. Your Hue pairing file and lamp assignments are unchanged.
Normal reinstalls preserve the environment and its optional dependencies.
Restart Onigiri after installation so the scene editor discovers the new extension.

For a source-only setup, install `requirements-hue-sync.txt` into the environment
used to run Onigiri, then copy `hue.py`, `_hue.py`, `hue_sync.py`, `_hue_sync.py`,
`_hue_sync_worker.py` and `_hue_page.py` to the extensions folder.

## Standalone controls

Open **Extensions → Hue screen sync** to start or stop sync without a scene.
Refresh Entertainment areas and select one, or enter its name/ID directly.
**Bridge and lamp setup** opens the Hue lamps page for discovery, pairing,
streaming authorization and lamp assignments. Opening a page does not change lights.

## One-time streaming authorization

The **Hue lamps** extension page can authorize streaming after you press the
bridge button. Alternatively, if you already paired Hue for static scenes, run:

```bash
~/.local/share/onigiri2/venv/bin/python ~/.config/onigiri/extensions/_hue.py --entertainment
```

The helper prompts before requesting authorization. It saves a new application
username and Entertainment client key together, preserving your existing lamp
assignments. Both Hue extensions use these credentials. Failed authorization
leaves the file untouched. Newly saved credential files are readable only by you.
If Hue has never been paired, run the helper without `--entertainment` first.

In the Hue mobile app, create an **Entertainment area** with compatible color
lights and position the lights around the display. Sync uses those positions:
left lights sample the left third, right lights the right third, and centered
lights sample the full screen. Gradient channels follow their area positions.
Existing blue/purple assignments remain available for static Hue scenes.

## Use

1. Create or select a scene and enable **Hue screen sync**.
2. Enter the Entertainment area's name or ID. Leave blank only if there is one area.
3. Set brightness and smoothing. Optionally use one average color for every light.
4. Select the scene’s card, then click **Apply** above the sidebar.
5. On Wayland, KDE asks which screen to share. Select one monitor and allow capture.

Qt's Wayland capture uses the screen-sharing portal and PipeWire; its support is
experimental. The FFmpeg Qt Multimedia backend is required and selected by the
worker. Screen selection is handled by KDE, not by a stored monitor number.
No frames are recorded or sent to the bridge: only sampled colors are streamed
locally. Protected content and HDR color fidelity are not guaranteed.

The extension samples at up to 25 frames per second, trims near-black outer
borders, and smooths color changes. This is a target rate, not a latency guarantee.
Status and runtime failures appear in the main status bar and extension panel.
Other active Hue sync sessions must be stopped first; Onigiri does not deliberately
take them over. While both Hue extensions are selected, screen sync takes precedence.

To stop, choose **Stop continuous effects** from Onigiri's tray menu, disable the
extension in the current scene, select a scene without it and click **Apply**, or quit Onigiri.
Closing the editor to the tray keeps sync running. A worker also watches its
parent's control pipe so it can clean up after an unexpected parent exit.

On a normal stop, cleanup waits for the Entertainment area to become inactive,
allows the lamps to settle, then nudges each lamp away from its cached state and
re-sends the exact saved state three times. This uses the existing Play-bar color
nudge workaround: a bridge accepting an unchanged color does not prove the LEDs
have left their streamed color. The nudge can briefly change the color; a lamp
that was off receives a minimal-brightness pulse before being turned off again.
The saved on/off, brightness, color/temperature and gradient points are preserved.
Cleanup failures and forced worker shutdown are reported to Onigiri rather than
silently treated as success. Bridge acknowledgements cannot verify physical output. Animated Hue
effects are not captured. If the bridge is unreachable during cleanup or the
worker is forcibly killed, restoration cannot be guaranteed; apply a static Hue
scene once connectivity returns.

## Extension lifecycle

Existing `on_scene(scene)` plugins continue to work. A continuous plugin can add:

- `on_stop()`: required for retained module state; called on deselection or shutdown.
- `on_status()`: optional nonblocking status hook, returning a new message or `""`.
- `explicit_only = True`: prevents automatic activation for legacy scenes.

Continuous modules are retained between scene updates, so `on_scene` must handle
updates idempotently. Scene work is serialized. Deselecting an extension stops it
before the new scene's hooks run. All continuous plugins must release resources
in `on_stop`; background processes should also monitor parent death.

Plugin fields also support `text` and bounded integer `number` controls with
`default`, `min`, and `max` metadata, alongside color, bool, and file fields.
