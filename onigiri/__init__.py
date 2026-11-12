"""Onigiri lays out windows on KDE Plasma."""

from pathlib import Path

__version__ = "1.0.0"


def asset_path(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / name
