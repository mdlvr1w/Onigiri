#!/usr/bin/env python3
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
import configparser
from typing import Dict, Any, List, Optional
from uuid import uuid4
import time

# Application config storage
CONFIG_DIR = Path.home() / ".config" / "onigiri"
TILER_CONFIG = CONFIG_DIR / "onigiri.json"

# KWin rules storage (never change this location)
KWIN_RULES = Path.home() / ".config" / "kwinrulesrc"


# ======================= Domain objects =======================


@dataclass
class MatchSpec:
    """How a tile identifies its target window(s)."""

    kind: str = "none"  # "class", "title", "regex-title", "none"
    value: str = ""

    @property
    def is_usable(self) -> bool:
        """Return True if this match should lead to a KWin rule."""
        v = (self.value or "").strip()
        return bool(self.kind) and self.kind != "none" and bool(v)

    def normalized_value(self) -> str:
        return (self.value or "").strip()


@dataclass
class Tile:
    """Single rectangular region and its window-binding configuration."""

    name: str
    x: int
    y: int
    width: int
    height: int
    match: MatchSpec = field(default_factory=MatchSpec)
    command: str = ""
    no_border: bool = False
    skip_taskbar: bool = False
    # Future fields (desktop, activity, screen, etc.) can be added here.

    def has_valid_match(self) -> bool:
        return self.match.is_usable


@dataclass
class Profile:
    """A named layout consisting of multiple tiles."""

    name: str
    monitor: str = "default"
    tiles: List[Tile] = field(default_factory=list)
    tile_gap: int = 0


# ======================= Profile storage =======================


class ProfileStore:
    """Load/save profiles from JSON and expose them as Profile/Tile objects."""

    def __init__(self, config_dir: Path, config_path: Path) -> None:
        self._config_dir = config_dir
        self._config_path = config_path

    # --- raw I/O, kept for UI compatibility ---

    def load_raw(self) -> Dict[str, Any]:
        """Return the raw JSON structure used by the UI."""
        if not self._config_path.exists():
            return {"profiles": []}
        with self._config_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save_raw(self, data: Dict[str, Any]) -> None:
        """Persist the raw JSON structure."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with self._config_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # --- typed model helpers ---

    def _match_from_dict(self, d: Any) -> MatchSpec:
        if not isinstance(d, dict):
            return MatchSpec()
        kind = (d.get("type") or "none").strip()
        value = d.get("value", "") or ""
        return MatchSpec(kind=kind, value=str(value))

    def _tile_from_dict(self, d: Dict[str, Any]) -> Tile:
        match = self._match_from_dict(d.get("match", {}))
        return Tile(
            name=str(d.get("name", "")),
            x=int(d.get("x", 0)),
            y=int(d.get("y", 0)),
            width=int(d.get("width", 800)),
            height=int(d.get("height", 600)),
            match=match,
            command=str(d.get("command", "") or ""),
            no_border=bool(d.get("no_border", False)),
            skip_taskbar=bool(d.get("skip_taskbar", False)),
        )

    def _profile_from_dict(self, d: Dict[str, Any]) -> Profile:
        name = str(d.get("name", ""))
        monitor = str(d.get("monitor", "default"))
        tile_gap = int(d.get("tile_gap", 0) or 0)
        tiles_raw = d.get("tiles", []) or []
        tiles: List[Tile] = [self._tile_from_dict(t) for t in tiles_raw]
        return Profile(name=name, monitor=monitor, tiles=tiles, tile_gap=tile_gap)

    def _find_profile_raw(self, data: Dict[str, Any], name: str) -> Dict[str, Any]:
        for p in data.get("profiles", []):
            if p.get("name") == name:
                return p
        raise RuntimeError(f"Profile '{name}' not found in {self._config_path}")

    def load_profile(self, name: str) -> Profile:
        """Load a single profile as a typed object."""
        data = self.load_raw()
        raw = self._find_profile_raw(data, name)
        return self._profile_from_dict(raw)


# ======================= KWin rule management =======================


def _trigger_kwin_reconfigure() -> None:
    """Ask KWin to reload its configuration (incl. Window Rules)."""
    qdbus_bin: Optional[str] = None
    for candidate in ("qdbus6", "qdbus"):
        if subprocess.call(
            ["which", candidate],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) == 0:
            qdbus_bin = candidate
            break

    if qdbus_bin:
        subprocess.call(
            [qdbus_bin, "org.kde.KWin", "/KWin", "reconfigure"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class KWinRulesManager:
    """Wrapper around kwinrulesrc with higher-level operations."""

    def __init__(self, rules_path: Path) -> None:
        self._rules_path = rules_path

    # --- low-level INI I/O ---

    def load_config(self) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg.optionxform = str  # keep case
        if self._rules_path.exists():
            cfg.read(self._rules_path, encoding="utf-8")
        return cfg

    def _get_rules_list(self, cfg: configparser.ConfigParser) -> List[str]:
        """Return the list of rule IDs from [General].rules (UUIDs)."""
        if not cfg.has_section("General"):
            cfg.add_section("General")
        raw = cfg.get("General", "rules", fallback="")
        raw = raw.strip()
        if not raw:
            return []
        return [r for r in raw.split(",") if r]

    def _set_rules_list(self, cfg: configparser.ConfigParser, ids: List[str]) -> None:
        """Update [General].rules and [General].count."""
        if not cfg.has_section("General"):
            cfg.add_section("General")
        # ensure uniqueness but keep order
        seen = set()
        cleaned: List[str] = []
        for r in ids:
            if r and r not in seen:
                cleaned.append(r)
                seen.add(r)
        cfg.set("General", "rules", ",".join(cleaned))
        cfg.set("General", "count", str(len(cleaned)))

    def save_config(self, cfg: configparser.ConfigParser) -> None:
        """Write KWin rules file with correct [General] metadata."""
        rules_list = self._get_rules_list(cfg)
        self._set_rules_list(cfg, rules_list)
        with self._rules_path.open("w", encoding="utf-8") as f:
            cfg.write(f)

    # --- section helpers ---

    def _find_section_by_description(
        self, cfg: configparser.ConfigParser, desc: str
    ) -> Optional[str]:
        """Return section name whose Description equals desc, or None."""
        for section in cfg.sections():
            if section == "General":
                continue
            if cfg.has_option(section, "Description"):
                if cfg.get(section, "Description") == desc:
                    return section
        return None

    def ensure_rule_section(
        self, cfg: configparser.ConfigParser, desc: str
    ) -> str:
        """
        Find or create a section for a rule with the given Description.

        Returns the section name (UUID).
        """
        existing = self._find_section_by_description(cfg, desc)
        if existing:
            return existing

        rule_id = str(uuid4())
        cfg.add_section(rule_id)
        cfg.set(rule_id, "Description", desc)

        rules_list = self._get_rules_list(cfg)
        rules_list.append(rule_id)
        self._set_rules_list(cfg, rules_list)

        return rule_id

    # --- high-level operations used by the UI ---

    def list_rules(self) -> List[Dict[str, Any]]:
        """
        Return a list of all KWin rules from kwinrulesrc.

        Each entry:
            {
              "id": <uuid>,
              "description": "...",
              "position": "x,y" or "",
              "size": "w,h" or "",
              "wmclass": "...",
              "title": "...",
              "enabled": bool,
              "from_kwintiler": bool,   # whether Description starts with "KWinTiler:"
            }
        """
        cfg = self.load_config()
        rules_ids = self._get_rules_list(cfg)
        result: List[Dict[str, Any]] = []

        for rid in rules_ids:
            if not cfg.has_section(rid):
                continue
            sec = cfg[rid]

            desc = sec.get("Description", "")
            enabled = sec.get("Enabled", "").strip().lower() != "false"

            entry = {
                "id": rid,
                "description": desc,
                "position": sec.get("position", ""),
                "size": sec.get("size", ""),
                "wmclass": sec.get("wmclass", ""),
                "title": sec.get("title", ""),
                "enabled": enabled,
                "from_kwintiler": desc.startswith("KWinTiler:"),
            }
            result.append(entry)

        return result

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        """
        Enable/disable a single KWin rule by ID.

        - enabled=True  -> remove 'Enabled' key (KWin default: enabled)
        - enabled=False -> set 'Enabled=false'
        """
        cfg = self.load_config()
        if not cfg.has_section(rule_id):
            return

        if enabled:
            if cfg.has_option(rule_id, "Enabled"):
                cfg.remove_option(rule_id, "Enabled")
        else:
            cfg.set(rule_id, "Enabled", "false")

        self.save_config(cfg)
        _trigger_kwin_reconfigure()

    def remove_profile_rules(self, profile_name: str) -> None:
        """Remove all KWin Window Rules created for a given profile."""
        cfg = self.load_config()
        prefix = f"KWinTiler:{profile_name}:"

        rules_list = self._get_rules_list(cfg)
        to_remove_ids: List[str] = []

        for section in cfg.sections():
            if section == "General":
                continue
            if cfg.has_option(section, "Description"):
                if cfg.get(section, "Description").startswith(prefix):
                    to_remove_ids.append(section)

        if to_remove_ids:
            rules_list = [r for r in rules_list if r not in to_remove_ids]
            self._set_rules_list(cfg, rules_list)
            for sec in to_remove_ids:
                cfg.remove_section(sec)
            self.save_config(cfg)
            _trigger_kwin_reconfigure()


# ======================= Engine: apply + launch =======================


class OnigiriEngine:
    """High-level operations: apply a profile, launch apps, etc."""

    def __init__(self, store: ProfileStore, rules: KWinRulesManager) -> None:
        self._store = store
        self._rules = rules

    def apply_profile(self, name: str) -> None:
        """
        Write/update KWin Window Rules for a profile.
        Does NOT launch any applications – pure rules.
        """
        #print(f"[OnigiriEngine] apply_profile called for '{name}'")
        profile = self._store.load_profile(name)
        if not profile.tiles:
            return

        cfg = self._rules.load_config()

        for tile in profile.tiles:
            if not tile.has_valid_match():
                # Avoid catch-all rules
                continue

            desc = f"KWinTiler:{profile.name}:{tile.name}"
            sec = self._rules.ensure_rule_section(cfg, desc)

            x = int(tile.x)
            y = int(tile.y)
            w = int(tile.width)
            h = int(tile.height)

            cfg.set(sec, "position", f"{x},{y}")
            cfg.set(sec, "positionrule", "2")   # apply
            cfg.set(sec, "size", f"{w},{h}")
            cfg.set(sec, "sizerule", "2")       # apply

            # --- Optional: no border / no titlebar ---
            if tile.no_border:
                cfg.set(sec, "noborder", "true")
                cfg.set(sec, "noborderrule", "2")
            else:
                if cfg.has_option(sec, "noborder"):
                    cfg.remove_option(sec, "noborder")
                if cfg.has_option(sec, "noborderrule"):
                    cfg.remove_option(sec, "noborderrule")

            # --- Optional: skip taskbar ---
            if tile.skip_taskbar:
                cfg.set(sec, "skiptaskbar", "true")
                cfg.set(sec, "skiptaskbarrule", "2")
            else:
                if cfg.has_option(sec, "skiptaskbar"):
                    cfg.remove_option(sec, "skiptaskbar")
                if cfg.has_option(sec, "skiptaskbarrule"):
                    cfg.remove_option(sec, "skiptaskbarrule")

            # --- Match fields ---
            mtype = tile.match.kind
            mvalue = tile.match.normalized_value()

            if mtype == "class":
                cfg.set(sec, "wmclass", mvalue)
                cfg.set(sec, "wmclassmatch", "1")  # exact
            elif mtype == "title":
                cfg.set(sec, "title", mvalue)
                cfg.set(sec, "titlematch", "2")    # substring
            elif mtype == "regex-title":
                cfg.set(sec, "title", mvalue)
                cfg.set(sec, "titlematch", "3")    # regex

            cfg.set(sec, "types", "1")  # normal windows

        self._rules.save_config(cfg)
        _trigger_kwin_reconfigure()

    def launch_profile_commands(self, name: str) -> None:
        """Launch all commands for a profile (open its windows) and re-trigger rules."""
        profile = self._store.load_profile(name)
        if not profile.tiles:
            return

        # Launch all windows
        for tile in profile.tiles:
            cmd = tile.command
            if cmd:
                subprocess.Popen(cmd, shell=True)

        # Give KWin a moment so the windows are mapped and titles/classes are set
        time.sleep(1.5)

        # Now poke KWin so it re-reads rules and re-applies them to existing windows
        _trigger_kwin_reconfigure()

    def remove_profile_rules(self, name: str) -> None:
        """Delete rules belonging to a given profile."""
        self._rules.remove_profile_rules(name)


# ======================= Module-level façade (public API) =======================


# Singletons used by the rest of the app
_profile_store = ProfileStore(CONFIG_DIR, TILER_CONFIG)
_kwin_rules = KWinRulesManager(KWIN_RULES)
_engine = OnigiriEngine(_profile_store, _kwin_rules)


# --- functions retained for UI + CLI compatibility ---


def load_profiles() -> Dict[str, Any]:
    """
    Backwards-compatible wrapper returning raw JSON.

    Existing UI code expects a dict like:
        {"profiles": [ { ... }, ... ]}
    """
    return _profile_store.load_raw()


def save_profiles(data: Dict[str, Any]) -> None:
    """Backwards-compatible wrapper to persist raw JSON data."""
    _profile_store.save_raw(data)


def find_profile(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Original helper kept for any external use (primarily CLI)."""
    for p in data.get("profiles", []):
        if p.get("name") == name:
            return p
    raise RuntimeError(f"Profile '{name}' not found in {TILER_CONFIG}")


def list_kwin_rules() -> List[Dict[str, Any]]:
    return _kwin_rules.list_rules()


def set_rule_enabled(rule_id: str, enabled: bool) -> None:
    _kwin_rules.set_rule_enabled(rule_id, enabled)


def apply_profile(name: str) -> None:
    _engine.apply_profile(name)


def launch_profile_commands(name: str) -> None:
    _engine.launch_profile_commands(name)


def remove_profile_rules(name: str) -> None:
    _engine.remove_profile_rules(name)


def example_config() -> None:
    """Create a sample config file for your current 3-pane dashboard."""
    data: Dict[str, Any] = {
        "profiles": [
            {
                "name": "dashboard-3pane",
                "monitor": "HDMI-1",
                "tiles": [
                    {
                        "name": "left-btop",
                        "x": 0,
                        "y": 29,
                        "width": 960,
                        "height": 1051,
                        "match": {"type": "class", "value": "btop-dash"},
                        "command": "alacritty --class btop-dash --title 'BTOP Dash' -e btop",
                        "no_border": False,
                        "skip_taskbar": False,
                    },
                    {
                        "name": "top-right",
                        "x": 960,
                        "y": 29,
                        "width": 960,
                        "height": 531,
                        "match": {"type": "class", "value": "info-dash"},
                        "command": (
                            "alacritty --class info-dash --title 'Info Dash' "
                            "-e bash -lc 'fastfetch; exec $SHELL'"
                        ),
                        "no_border": False,
                        "skip_taskbar": False,
                    },
                    {
                        "name": "bottom-right",
                        "x": 960,
                        "y": 560,
                        "width": 960,
                        "height": 520,
                        "match": {"type": "class", "value": "empty-dash"},
                        "command": (
                            "alacritty --class empty-dash --title 'Empty Dash' -e $SHELL"
                        ),
                        "no_border": False,
                        "skip_taskbar": False,
                    },
                ],
                "tile_gap": 0,
            }
        ]
    }
    save_profiles(data)
    print(f"Wrote example config to {TILER_CONFIG}")


# ======================= CLI entry point =======================


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="KWin tiler profile tool (Onigiri backend)")
    ap.add_argument(
        "--profile",
        help="Profile name from onigiri.json",
    )
    ap.add_argument(
        "--launch",
        action="store_true",
        help="Also launch the given profile's commands after applying rules",
    )
    ap.add_argument(
        "--init-example",
        action="store_true",
        help="Write example config for the current 3-pane dashboard",
    )
    args = ap.parse_args()

    if args.init_example:
        example_config()
    elif args.profile:
        apply_profile(args.profile)
        if args.launch:
            launch_profile_commands(args.profile)
    else:
        ap.print_help()
