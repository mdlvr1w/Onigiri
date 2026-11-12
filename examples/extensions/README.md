# Onigiri extensions

Extensions can participate in scenes and also provide a standalone page. Click an
extension under **Extensions** to open it; use **All extensions** to return to the
list. Opening or closing a page does not apply settings or stop an effect.

## Bundled controls

- **GPU / RAM:** set two lighting colors, read the currently saved device colors,
  and apply changes to the existing lighting configuration.
- **Ryuo:** set display title/content colors and read current colors.
- **Lian Li fans:** control both LED colors, LCD text and gauge colors. Apply writes
  the configured fan settings and reloads the existing user fan daemon.
- **Hue lamps:** discover and pair a bridge, authorize streaming, refresh lamps,
  save their Color 1 / Color 2 assignments, and apply static colors.
- **Hue screen sync:** refresh Entertainment areas, choose one or enter its name/ID,
  adjust brightness/smoothing, and start, update or stop capture and streaming.

Install/update the bundled pages using `./install.sh --extensions` from the repo.
Use `./install.sh --hue-sync` to also install screen-sync dependencies. Existing
extension files are backed up before replacement. Hardware setup and daemon
installation remain device-specific; these pages use the same integrations as
scenes.

Manual field values are saved separately in
`~/.config/onigiri/extension-controls.json` (under `XDG_CONFIG_HOME` when set).
They do not edit any saved scene. Pairing credentials and lamp assignments remain
in `~/.config/onigiri/hue.json`; credentials are not shown on the pages or copied
into manual field settings.

Manual Apply targets only its extension. Static Hue and screen sync declare the
same hardware group, so applying static Hue colors stops its active sync stream
first. Unrelated continuous extensions are left alone. Applying a scene later
still applies that scene's settings and reconciles its active effects; an active
scene playlist can therefore supersede manual controls.

## Page API

Existing scene-only extensions continue to work and get a generic standalone
fields page. To supply an individual page, optionally define:

```python
def create_page(context):
    from onigiri.ui.extension_page import ExtensionControlPage
    return ExtensionControlPage(
        context,
        description="Adjust the two colors for this device.",
    )
```

The hook runs on the UI thread and must return a PyQt6 `QWidget`. It may return a
completely custom interface. Keep imports lazy if a scene extension should not
otherwise depend on the UI. Page factories must not change hardware, start an
effect, or perform blocking network requests merely because the user opened them.
Pages are cached while Onigiri runs; an optional `on_show()` method is called when
the page is opened again.

The context provides:

- `info`: this extension's metadata and file path.
- `saved_settings()`: a dictionary of its last manual field values.
- `apply(settings)`: queue this extension's `on_scene` hook directly, without
  applying a wallpaper or changing a scene; save field values after success.
- `stop()`: queue cleanup for this continuous extension only.
- `is_running()`: whether the shared extension lifecycle currently retains it.
- `run(work, on_success=None, message="Working…", on_error=None)`: execute blocking
  work in Onigiri's serialized background queue. `work` must not touch Qt widgets;
  callbacks run on the UI thread. Exceptions appear in the page/status bar.
- `set_status(message)`: update the status bar and this page's status listener.
- `status_listener`: an optional UI-thread callback for page-specific status text.
- `open_extension(stem)`: navigate to another installed extension's page.

`ExtensionControlPage` provides `editor.values()`, `editor.set_values(...)`,
`apply_button`, `stop_button`, `status`, and a `column` layout for additional
controls. Its field editor hides the scene enable checkbox and Test button.

`on_scene(scene)` receives `scene.extras["standalone"] == True` during manual
Apply. Continuous extensions retain their module and use the same `on_stop()` and
`on_status()` hooks as scene-driven effects. An optional `exclusive_group` string
lets manual Apply stop other active continuous extensions in the same hardware
group before applying; declare it on both relevant extensions. This does not
replace an extension's own scene conflict handling.

Only trusted Python belongs in the extensions directory: these scripts run with
your user's permissions.
