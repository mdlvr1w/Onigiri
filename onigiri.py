#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path
import configparser
from typing import Dict, Any, List
from uuid import uuid4
import time

# Application config storage
CONFIG_DIR = Path.home() / ".config" / "onigiri"
TILER_CONFIG = CONFIG_DIR / "onigiri.json"

# KWin rules storage (never change this location)
KWIN_RULES = Path.home() / ".config" / "kwinrulesrc"


# ---------------------- helpers: profiles ----------------------


def load_profiles() -> Dict[str, Any]:
    if not TILER_CONFIG.exists():
        return {"profiles": []}
    with TILER_CONFIG.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_profiles(data: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with TILER_CONFIG.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def find_profile(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    for p in data.get("profiles", []):
        if p.get("name") == name:
            return p
    raise RuntimeError(f"Profile '{name}' not found in {TILER_CONFIG}")


# ---------------------- helpers: kwin + dbus ----------------------


def _trigger_kwin_reconfigure() -> None:
    """Ask KWin to reload its configuration (incl. Window Rules)."""
    qdbus_bin = None
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


def load_kwin_rules() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.optionxform = str  # keep case
    if KWIN_RULES.exists():
        cfg.read(KWIN_RULES, encoding="utf-8")
    return cfg


def _get_rules_list(cfg: configparser.ConfigParser) -> List[str]:
    """Return the list of rule IDs from [General].rules (UUIDs)."""
    if not cfg.has_section("General"):
        cfg.add_section("General")
    raw = cfg.get("General", "rules", fallback="")
    raw = raw.strip()
    if not raw:
        return []
    return [r for r in raw.split(",") if r]


def _set_rules_list(cfg: configparser.ConfigParser, ids: List[str]) -> None:
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


def save_kwin_rules(cfg: configparser.ConfigParser) -> None:
    """Write KWin rules file with correct [General] metadata."""
    rules_list = _get_rules_list(cfg)
    _set_rules_list(cfg, rules_list)
    with KWIN_RULES.open("w", encoding="utf-8") as f:
        cfg.write(f)


def _find_section_by_description(cfg: configparser.ConfigParser, desc: str) -> str | None:
    """Return section name whose Description equals desc, or None."""
    for section in cfg.sections():
        if section == "General":
            continue
        if cfg.has_option(section, "Description"):
            if cfg.get(section, "Description") == desc:
                return section
    return None


def _ensure_rule_section(cfg: configparser.ConfigParser, desc: str) -> str:
    """
    Find or create a section for a rule with the given Description.
    Uses UUID-style IDs and registers them in [General].rules.
    """
    rules_list = _get_rules_list(cfg)

    existing = _find_section_by_description(cfg, desc)
    if existing:
        # make sure it's also in General.rules
        if existing not in rules_list:
            rules_list.append(existing)
            _set_rules_list(cfg, rules_list)
        return existing

    # Create a new UUID-style ID like KDE does
    section_id = str(uuid4())
    cfg.add_section(section_id)
    cfg.set(section_id, "Description", desc)

    rules_list.append(section_id)
    _set_rules_list(cfg, rules_list)
    return section_id


# ---------------------- list + enable/disable rules ----------------------


def list_kwin_rules() -> List[Dict[str, Any]]:
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
          "enabled": True/False,
          "from_kwintiler": True/False
        }
    """
    cfg = load_kwin_rules()
    result: List[Dict[str, Any]] = []
    rules_ids = _get_rules_list(cfg)

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


def set_rule_enabled(rule_id: str, enabled: bool) -> None:
    """
    Enable/disable a single KWin rule by ID.

    - enabled=True  -> remove 'Enabled' key (KWin default: enabled)
    - enabled=False -> set 'Enabled=false'
    """
    cfg = load_kwin_rules()
    rules_ids = _get_rules_list(cfg)

    if rule_id not in rules_ids:
        # Don't touch random sections that aren't registered as rules
        return

    if not cfg.has_section(rule_id):
        return

    if enabled:
        if cfg.has_option(rule_id, "Enabled"):
            cfg.remove_option(rule_id, "Enabled")
    else:
        cfg.set(rule_id, "Enabled", "false")

    save_kwin_rules(cfg)
    _trigger_kwin_reconfigure()


# ---------------------- public API: profiles -> rules ----------------------


def apply_profile(name: str) -> None:
    """
    Write/update KWin Window Rules for a profile.
    Does NOT launch any applications – pure rules.
    """
    data = load_profiles()
    profile = find_profile(data, name)

    tiles: List[Dict[str, Any]] = profile.get("tiles", [])
    if not tiles:
        # No tiles defined, nothing to apply.
        return

    cfg = load_kwin_rules()

    for tile in tiles:
        tile_name = tile["name"]

        # ---- MATCH VALIDATION ----
        match = tile.get("match", {})
        mtype = match.get("type")
        mvalue = match.get("value", "")
        if mvalue is None:
            mvalue = ""
        mvalue = mvalue.strip()

        # Skip tiles without a usable match (avoid catch-all rules)
        if not mtype or mtype == "none" or not mvalue:
            continue

        desc = f"KWinTiler:{name}:{tile_name}"
        sec = _ensure_rule_section(cfg, desc)

        x = int(tile.get("x", 0))
        y = int(tile.get("y", 0))
        w = int(tile.get("width", 800))
        h = int(tile.get("height", 600))

        cfg.set(sec, "position", f"{x},{y}")
        cfg.set(sec, "positionrule", "2")   # apply
        cfg.set(sec, "size", f"{w},{h}")
        cfg.set(sec, "sizerule", "2")       # apply

        # --- Optional: no border / no titlebar ---
        if tile.get("no_border"):
            cfg.set(sec, "noborder", "true")
            cfg.set(sec, "noborderrule", "2")
        else:
            # If previously set, clean it up when user unticks
            if cfg.has_option(sec, "noborder"):
                cfg.remove_option(sec, "noborder")
            if cfg.has_option(sec, "noborderrule"):
                cfg.remove_option(sec, "noborderrule")

                # --- Optional: skip taskbar ---
        if tile.get("skip_taskbar"):
            cfg.set(sec, "skiptaskbar", "true")
            cfg.set(sec, "skiptaskbarrule", "2")
        else:
            if cfg.has_option(sec, "skiptaskbar"):
                cfg.remove_option(sec, "skiptaskbar")
            if cfg.has_option(sec, "skiptaskbarrule"):
                cfg.remove_option(sec, "skiptaskbarrule")

        # --- Match fields ---
        if mtype == "class":
            cfg.set(sec, "wmclass", mvalue)
            cfg.set(sec, "wmclassmatch", "1")  # exact
        elif mtype == "title":
            cfg.set(sec, "title", mvalue)
            cfg.set(sec, "titlematch", "2")    # substring (matches your manual rules)
        elif mtype == "regex-title":
            cfg.set(sec, "title", mvalue)
            cfg.set(sec, "titlematch", "3")    # regex

        cfg.set(sec, "types", "1")  # normal windows

        # --- Flags: no border / skip taskbar ---
        if tile.get("no_border"):
            cfg.set(sec, "noborder", "true")
            cfg.set(sec, "noborderrule", "2")  # force apply

        if tile.get("skip_taskbar"):
            cfg.set(sec, "skiptaskbar", "true")
            cfg.set(sec, "skiptaskbarrule", "2")  # force apply

    save_kwin_rules(cfg)
    _trigger_kwin_reconfigure()


def launch_profile_commands(name: str) -> None:
    """Launch all commands for a profile (open its windows) and re-trigger rules."""
    data = load_profiles()
    profile = find_profile(data, name)
    tiles: List[Dict[str, Any]] = profile.get("tiles", [])

    # Launch all windows
    for tile in tiles:
        cmd = tile.get("command")
        if cmd:
            subprocess.Popen(cmd, shell=True)

    # Give KWin a moment so the windows are mapped and titles/classes are set
    time.sleep(1.5)

    # Now poke KWin so it re-reads rules and re-applies them to existing windows
    _trigger_kwin_reconfigure()


def remove_profile_rules(name: str) -> None:
    """Remove all KWin Window Rules created for a given profile."""
    cfg = load_kwin_rules()
    prefix = f"KWinTiler:{name}:"

    rules_list = _get_rules_list(cfg)
    to_remove_sections: List[str] = []
    to_remove_ids: List[str] = []

    for section in cfg.sections():
        if section == "General":
            continue
        if cfg.has_option(section, "Description"):
            if cfg.get(section, "Description").startswith(prefix):
                to_remove_sections.append(section)
                to_remove_ids.append(section)

    for sec in to_remove_sections:
        cfg.remove_section(sec)

    if to_remove_ids:
        rules_list = [r for r in rules_list if r not in to_remove_ids]
        _set_rules_list(cfg, rules_list)
        save_kwin_rules(cfg)
        _trigger_kwin_reconfigure()


def example_config():
    """Create a sample config file for your current 3-pane dashboard."""
    data = {
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
                    },
                    {
                        "name": "top-right",
                        "x": 960,
                        "y": 29,
                        "width": 960,
                        "height": 531,
                        "match": {"type": "class", "value": "info-dash"},
                        "command": "alacritty --class info-dash --title 'Info Dash' -e bash -lc 'fastfetch; exec $SHELL'",
                    },
                    {
                        "name": "bottom-right",
                        "x": 960,
                        "y": 560,
                        "width": 960,
                        "height": 520,
                        "match": {"type": "class", "value": "empty-dash"},
                        "command": "alacritty --class empty-dash --title 'Empty Dash' -e $SHELL",
                    },
                ],
            }
        ]
    }
    save_profiles(data)
    print(f"Wrote example config to {TILER_CONFIG}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Onigiri profile applier")
    ap.add_argument(
        "--profile",
        help="Profile name to apply (must exist in onigiri.json)",
    )
    ap.add_argument(
        "--launch",
        action="store_true",
        help="Also launch profile commands after applying rules",
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
