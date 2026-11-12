"""Request compositor background blur through KDE's KWindowEffects API.

PyQt6 has no KF6 binding. This small Linux ABI adapter passes the existing Qt
objects to the installed KWindowSystem library; it never renders/blur-filters
Onigiri's own widgets. KDE manages Wayland surface recreation and X11 hints.
"""
from __future__ import annotations

import ctypes
from ctypes.util import find_library
from functools import lru_cache
import logging
from pathlib import Path
import sysconfig

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtGui import QGuiApplication, QRegion

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _backend():
    try:
        library = ctypes.CDLL(find_library("KF6WindowSystem") or "libKF6WindowSystem.so.6")
        # Linux/Itanium C++ ABI: enableBlurBehind(QWindow*, bool, const QRegion&).
        enable = getattr(library, "_ZN14KWindowEffects16enableBlurBehindEP7QWindowbRK7QRegion")
        enable.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p]
        enable.restype = None
    except (OSError, AttributeError) as error:
        log.warning("KDE background blur unavailable: %s", error)
        return None

    # A pip Qt installation doesn't normally search the distro's KF6 plugin path.
    # Append it so bundled Qt plugins keep priority over system Qt plugins.
    paths = QCoreApplication.libraryPaths()
    candidates = [Path("/usr/lib/qt6/plugins"), Path("/usr/lib64/qt6/plugins")]
    multiarch = sysconfig.get_config_var("MULTIARCH")
    if multiarch:
        candidates.append(Path("/usr/lib") / multiarch / "qt6/plugins")
    for candidate in candidates:
        if (candidate / "kf6/kwindowsystem").is_dir() and str(candidate) not in paths:
            paths.append(str(candidate))
    QCoreApplication.setLibraryPaths(paths)
    return library, enable  # Retain the library for the lifetime of its Qt plugin objects.


def enable_background_blur(widget) -> bool:
    """Request full-window blur after the native window exists; leave alpha alone."""
    if QGuiApplication.platformName() not in {"wayland", "wayland-egl", "xcb"}:
        return False
    window = widget.windowHandle()
    if window is None:
        return False
    backend = _backend()
    if backend is None:
        return False
    _library, enable = backend
    # KWindowEffects defines an empty region as the whole window. Its plugin tracks
    # surface changes, so resizing and returning from the tray retain the request.
    region = QRegion()
    enable(sip.unwrapinstance(window), True, sip.unwrapinstance(region))
    return True
