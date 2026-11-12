# Dependency licensing

The MIT license in this repository applies to Onigiri's original source and
included assets. It does not change the licenses of separately installed libraries,
Plasma components, device daemons, firmware, or other third-party software.
No third-party Python packages or shared libraries are vendored in this repository.

## PyQt6 and Qt

PyQt6 is available under GPLv3 or Riverbank's commercial license, not LGPL. The
MIT license is GPL-compatible; it does not remove GPL obligations when distributing
an application together with the GPL version of PyQt. Riverbank documents these
requirements in its [licensing FAQ](https://www.riverbankcomputing.com/commercial/license-faq)
and [PyQt overview](https://www.riverbankcomputing.com/software/pyqt).

The GPL PyQt wheels include an LGPL version of Qt. Qt itself and its modules retain
their own license terms. Review the licenses of the actual components used when
packaging a binary or bundled application; this source repository's MIT license
is not a license for those dependencies.

## Optional libraries and integrations

The optional `hue-entertainment` dependency is distributed separately under
Apache-2.0; see its [project license](https://github.com/music-assistant/hue-entertainment/blob/main/LICENSE).
Its dependencies retain their respective licenses.

KDE KWindowSystem, KWin, Plasma, PipeWire, desktop portals and optional hardware
services are system components installed separately. Consult their upstream
licenses when redistributing them. Onigiri requests KDE's public window-effects
API and does not bundle KDE libraries.
