"""Run the Onigiri window."""

from __future__ import annotations

import argparse
import sys

from PyQt6.QtWidgets import QApplication

from onigiri.ui.main_window import MainWindow
from onigiri.ui.theme import apply_theme


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onigiri", description="KWin tiling layouts")
    parser.add_argument("--profile", help="Apply this profile, launch its tiles, and stay in the tray")
    parser.add_argument("--hidden", action="store_true", help="Start in the tray without showing the window")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv)
    app.setApplicationName("Onigiri")
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)

    window = MainWindow()
    if not window.windowIcon().isNull():
        app.setWindowIcon(window.windowIcon())
    started = False
    if args.profile:
        started = window.launch_named(args.profile, delay_ms=2000)
    if started or args.hidden:
        window.start_hidden()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
