"""Edit kwinrulesrc without rewriting rules Onigiri does not own."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from onigiri.files import atomic_write_text

DESCRIPTION_PREFIX = "Onigiri:"
OVERLAY_PREFIX = "OnigiriOverlay:"
# KWin rules.h saves these numbers: DontAffect=1, Force=2, Apply=3, Remember=4.
# Force keeps applying after the client sets its own size. Remember only applies
# while the window is first mapped, which Alacritty then overwrites.
FORCE = "2"
EXACT = "1"
_APP_ID_FLAG = re.compile(r"--app-id=([A-Za-z0-9]+)")
_CRX_CLASS = re.compile(r"^crx_([A-Za-z0-9]+)$", re.I)
_PACKAGED_DESKTOP = re.compile(
    r"^(?:chrome|brave|chromium)-([A-Za-z0-9]+)(?:-Default)?(?:\.desktop)?$",
    re.I,
)


@dataclass(frozen=True)
class RuleRecord:
    id: str
    description: str
    enabled: bool
    owned: bool
    wmclass: str
    title: str
    position: str
    size: str


@dataclass(frozen=True)
class Placement:
    tile_id: str
    description: str
    x: int
    y: int
    width: int
    height: int
    match_kind: str
    match_value: str
    no_border: bool
    skip_taskbar: bool
    command: str
    use_shell: bool
    app_id: str = ""


def packaged_app_ids(match_value: str = "", app_id: str = "", command: str = "") -> list[str]:
    """Chromium/Brave PWA ids from a desktop class, file name, or --app-id flag."""

    found: list[str] = []

    def add(value: str) -> None:
        text = value.strip()
        if text and text not in found:
            found.append(text)

    for raw in (match_value, app_id):
        name = Path(raw.strip()).name if raw.strip() else ""
        if not name:
            continue
        hit = _CRX_CLASS.match(name) or _PACKAGED_DESKTOP.match(name)
        if hit:
            add(hit.group(1))
    for hit in _APP_ID_FLAG.finditer(command or ""):
        add(hit.group(1))
    return found


def match_keys(match_value: str = "", app_id: str = "", command: str = "") -> list[str]:
    """Window class, desktop file, and PWA ids KWin can actually see."""

    keys: list[str] = []

    def add(value: str) -> None:
        text = value.strip()
        if text and text not in keys:
            keys.append(text)

    add(match_value)
    stem = Path(app_id).stem if app_id.strip() else ""
    if stem.casefold().startswith(("chrome-", "brave-", "chromium-")):
        add(stem)
    for app in packaged_app_ids(match_value, app_id, command):
        add(app)
        add(f"crx_{app}")
        add(f"chrome-{app}-Default")
        add(f"brave-{app}-Default")
        add(f"chromium-{app}-Default")
    return keys


def match_needles(placement: Placement) -> list[str]:
    return match_keys(placement.match_value, placement.app_id, placement.command)


def rule_desktop_file(placement: Placement) -> str:
    """Desktop file stem for Chromium-style app windows. Empty for normal class rules."""

    stem = Path((placement.app_id or "").strip()).stem
    lowered = stem.casefold()
    if lowered.startswith(("chrome-", "brave-", "chromium-")):
        return stem
    ids = packaged_app_ids(placement.match_value, placement.app_id, placement.command)
    if not ids:
        return ""
    command = (placement.command or "").casefold()
    app_id = (placement.app_id or "").casefold()
    if "brave" in command or app_id.startswith("brave"):
        return f"brave-{ids[0]}-Default"
    return f"chrome-{ids[0]}-Default"


def default_rules_path() -> Path:
    return Path.home() / ".config" / "kwinrulesrc"


def run_kwin_script(name: str, source: str) -> bool:
    """Load a one-shot KWin script, run it, then unload it."""

    path = Path(tempfile.gettempdir()) / f"{name}.js"
    path.write_text(source, encoding="utf-8")
    for program in ("qdbus6", "qdbus"):
        try:
            subprocess.run(
                [program, "org.kde.KWin", "/Scripting", "unloadScript", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            loaded = subprocess.run(
                [program, "org.kde.KWin", "/Scripting", "loadScript", str(path), name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            continue
        script_id = loaded.stdout.strip()
        if loaded.returncode != 0 or not script_id.isdigit():
            continue
        ran = subprocess.run(
            [program, "org.kde.KWin", f"/Scripting/Script{script_id}", "run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            [program, "org.kde.KWin", "/Scripting", "unloadScript", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return ran.returncode == 0
    return False


def close_script(match_values: set[str]) -> str:
    """KWin script that closes windows whose class, name, or title is one of the values."""

    values = sorted({value.strip().casefold() for value in match_values if value and value.strip()})
    payload = json.dumps(values)
    return f"""const wanted = new Set({payload});
for (const w of workspace.windowList()) {{
  const desk = String(w.desktopFileName || "").replace(/\\.desktop$/i, "");
  const fields = [String(w.resourceClass || ""), String(w.resourceName || ""), String(w.caption || ""), desk];
  if (fields.some((field) => wanted.has(field.toLowerCase()))) {{
    w.closeWindow();
  }}
}}
"""


def close_onigiri_windows(match_values: set[str]) -> bool:
    """Ask KWin to close windows previously launched for these tiles."""

    if not any(value and value.strip() for value in match_values):
        return False
    return run_kwin_script("onigiri-close-windows", close_script(match_values))


def close_stray_terminals_script() -> str:
    """Close stock Alacritty/Konsole windows, never Onigiri helper terminals."""

    return """function owned(w) {
  const desk = String(w.desktopFileName || "").replace(/\\.desktop$/i, "").toLowerCase();
  const fields = [
    String(w.resourceClass || "").toLowerCase(),
    String(w.resourceName || "").toLowerCase(),
    String(w.caption || "").toLowerCase(),
    desk,
  ];
  return fields.some((field) => field.indexOf("onigiri-") === 0);
}
for (const w of workspace.windowList()) {
  if (owned(w)) {
    continue;
  }
  const cls = String(w.resourceClass || "").toLowerCase();
  const name = String(w.resourceName || "").toLowerCase();
  const desk = String(w.desktopFileName || "").replace(/\\.desktop$/i, "").toLowerCase();
  const stock =
    cls === "alacritty" ||
    name === "alacritty" ||
    cls === "konsole" ||
    name === "konsole" ||
    cls === "org.kde.konsole" ||
    desk === "org.kde.konsole" ||
    desk === "konsole";
  if (stock) {
    w.closeWindow();
  }
}
"""


def close_stray_alacritty_script() -> str:
    """Back-compat name for the stock-terminal closer."""

    return close_stray_terminals_script()


def close_stray_terminals() -> bool:
    """Drop leftover generic terminals that login or Konsole restore leaves behind."""

    return run_kwin_script("onigiri-close-stray-terminals", close_stray_terminals_script())


def close_stray_alacritty_windows() -> bool:
    """Drop leftover generic terminals that login leaves behind."""

    return close_stray_terminals()


def place_script(placements: list[Placement]) -> str:
    """KWin script that raises one matching window and moves it into each tile."""

    jobs = []
    for placement in placements:
        match = match_needles(placement)
        if not match:
            continue
        jobs.append(
            {
                "match": match,
                "x": int(placement.x),
                "y": int(placement.y),
                "width": int(placement.width),
                "height": int(placement.height),
                "noBorder": bool(placement.no_border),
                "skipTaskbar": bool(placement.skip_taskbar),
            }
        )
    if not jobs:
        return ""
    token = str(uuid4())[:8]
    payload = json.dumps(jobs)
    return f"""const jobs = {payload};
const placeToken = "{token}";
const used = new Set();
const placed = [];
function windowId(w) {{
  return String(w.internalId || w.windowId || (String(w.resourceClass) + ":" + String(w.caption)));
}}
function needles(job) {{
  return job.match.map((item) => String(item).toLowerCase()).filter(Boolean);
}}
function score(w, job) {{
  const want = needles(job);
  const cls = String(w.resourceClass || "").toLowerCase();
  const name = String(w.resourceName || "").toLowerCase();
  const desk = String(w.desktopFileName || "").toLowerCase().replace(/\\.desktop$/, "");
  const cap = String(w.caption || "").toLowerCase();
  let best = 0;
  for (const needle of want) {{
    if (cls === needle || desk === needle) {{
      best = Math.max(best, 100);
    }} else if (name === needle && needle.indexOf("onigiri-") === 0) {{
      best = Math.max(best, 100);
    }} else if (cap === needle) {{
      best = Math.max(best, 90);
    }} else if (needle.indexOf("onigiri-") === 0 && cap.indexOf(needle) !== -1) {{
      best = Math.max(best, 90);
    }} else if (desk.indexOf(needle) !== -1) {{
      best = Math.max(best, 70);
    }} else if (cls.indexOf(needle) !== -1) {{
      best = Math.max(best, 60);
    }} else if (needle.length >= 4 && cap.indexOf(needle) !== -1) {{
      best = Math.max(best, 50);
    }}
  }}
  return best;
}}
function place(w, job) {{
  try {{ w.fullScreen = false; }} catch (e) {{}}
  try {{ w.setMaximize(false, false); }} catch (e) {{}}
  try {{ w.minimized = false; }} catch (e) {{}}
  try {{
    if (workspace.currentDesktop) {{
      w.desktops = [workspace.currentDesktop];
    }}
  }} catch (e) {{}}
  try {{ workspace.activeWindow = w; }} catch (e) {{}}
  try {{
    const geo = w.frameGeometry;
    geo.x = job.x;
    geo.y = job.y;
    geo.width = job.width;
    geo.height = job.height;
    w.frameGeometry = geo;
  }} catch (e) {{}}
  try {{
    w.frameGeometry = {{ x: job.x, y: job.y, width: job.width, height: job.height }};
  }} catch (e) {{}}
  try {{
    if (typeof Qt !== "undefined" && Qt.rect) {{
      w.frameGeometry = Qt.rect(job.x, job.y, job.width, job.height);
    }}
  }} catch (e) {{}}
  try {{ w.noBorder = job.noBorder; }} catch (e) {{}}
  try {{ w.skipTaskbar = job.skipTaskbar; }} catch (e) {{}}
}}
for (const job of jobs) {{
  let best = null;
  let bestScore = 0;
  for (const w of workspace.windowList()) {{
    const id = windowId(w);
    if (used.has(id) || w.deleted || w.dock || w.desktop || w.normalWindow === false) {{
      continue;
    }}
    const value = score(w, job);
    if (value >= 70 && value > bestScore) {{
      best = w;
      bestScore = value;
    }}
  }}
  if (best !== null) {{
    place(best, job);
    used.add(windowId(best));
    placed.push(String(job.match[0] || ""));
  }}
}}
console.info("ONIGIRI_PLACE_RESULT " + placeToken + " " + JSON.stringify(placed));
"""


_PLACE_MARK = "ONIGIRI_PLACE_RESULT "


def _place_token(source: str) -> str:
    marker = 'const placeToken = "'
    start = source.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = source.find('"', start)
    return source[start:end] if end != -1 else ""


def _read_place_result(token: str) -> list[str]:
    if not token:
        return []
    try:
        ran = subprocess.run(
            ["journalctl", "--user", "-n", "50", "-o", "cat", "--since", "20 seconds ago"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    needle = _PLACE_MARK + token + " "
    for line in reversed(ran.stdout.splitlines()):
        index = line.find(needle)
        if index == -1:
            continue
        try:
            payload = json.loads(line[index + len(needle) :])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            return [str(item) for item in payload if item]
    return []


def place_windows(placements: list[Placement]) -> list[str]:
    """Move already-open windows into tiles. Returns match values that were found."""

    source = place_script(placements)
    if not source:
        return []
    if not run_kwin_script("onigiri-place-windows", source):
        return []
    return _read_place_result(_place_token(source))


def reconfigure_kwin() -> bool:
    for program in ("qdbus6", "qdbus"):
        try:
            completed = subprocess.run(
                [program, "org.kde.KWin", "/KWin", "reconfigure"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except FileNotFoundError:
            continue
        return completed.returncode == 0
    return False


_HEADER = re.compile(r"(?m)^\[([^\]]+)\][^\n]*\n?")


def _parse(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(_HEADER.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1).strip(), text[match.start() : end]))
    return preamble, sections


def _value(chunk: str, key: str) -> str:
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(.*)$")
    for line in chunk.splitlines():
        found = pattern.match(line)
        if found:
            return found.group(1).strip()
    return ""


def _upsert(chunk: str, key: str, value: str | None) -> str:
    """Set or remove one key. `value` None removes the key. Other lines stay put."""

    newline = "\r\n" if "\r\n" in chunk else "\n"
    lines = chunk.splitlines(keepends=True)
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=")
    found = False
    output: list[str] = []
    for line in lines:
        if pattern.match(line):
            found = True
            if value is None:
                continue
            ending = newline if line.endswith(("\n", "\r\n")) else newline
            output.append(f"{key}={value}{ending}")
        else:
            output.append(line)
    if value is not None and not found:
        if output and not output[-1].endswith(("\n", "\r\n")):
            output[-1] = output[-1] + newline
        output.append(f"{key}={value}{newline}")
    return "".join(output)


def _rules_ids(chunk: str) -> list[str]:
    """KWin 6 lists ids in `Order`. Older files use `rules`."""

    ids = [part.strip() for part in _value(chunk, "rules").split(",") if part.strip()]
    if ids:
        return ids
    return [part.strip() for part in _value(chunk, "Order").split(",") if part.strip()]


def _render_rule(rule_id: str, placement: Placement) -> str:
    lines = [
        f"[{rule_id}]",
        f"Description={placement.description}",
        f"position={placement.x},{placement.y}",
        f"positionrule={FORCE}",
        f"size={placement.width},{placement.height}",
        f"sizerule={FORCE}",
        "ignoregeometry=true",
        f"ignoregeometryrule={FORCE}",
        "types=1",
    ]
    if placement.match_kind == "title":
        lines.append(f"title={placement.match_value}")
        lines.append(f"titlematch={EXACT}")
    else:
        # desktopfile is a SetRule in this KWin (it *writes* a desktop file name).
        # Using it as a match with no wmclass makes a catch-all Force geometry rule.
        lines.append(f"wmclass={placement.match_value.strip().casefold()}")
        lines.append(f"wmclassmatch={EXACT}")
        lines.append("wmclasscomplete=false")
    if placement.no_border:
        lines.append("noborder=true")
        lines.append(f"noborderrule={FORCE}")
    if placement.skip_taskbar:
        lines.append("skiptaskbar=true")
        lines.append(f"skiptaskbarrule={FORCE}")
    return "\n".join(lines) + "\n"


class KWinRules:
    def __init__(self, path: Path | None = None, reconfigure_fn=None) -> None:
        self.path = path or default_rules_path()
        self._reconfigure = reconfigure_fn or reconfigure_kwin
        self._backed_up = False

    def _read(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    def _backup_once(self) -> None:
        if self._backed_up or not self.path.exists():
            self._backed_up = True
            return
        backup = self.path.with_name(self.path.name + ".onigiri-bak")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, backup)
        self._backed_up = True

    def _write(self, text: str) -> None:
        self._backup_once()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, text)

    def list_rules(self) -> list[RuleRecord]:
        _preamble, sections = _parse(self._read())
        records: list[RuleRecord] = []
        for name, chunk in sections:
            if name == "General":
                continue
            description = _value(chunk, "Description")
            enabled = _value(chunk, "Enabled").lower() != "false"
            records.append(
                RuleRecord(
                    id=name,
                    description=description,
                    enabled=enabled,
                    owned=description.startswith(DESCRIPTION_PREFIX)
                    or description.startswith(OVERLAY_PREFIX),
                    wmclass=_value(chunk, "wmclass"),
                    title=_value(chunk, "title"),
                    position=_value(chunk, "position"),
                    size=_value(chunk, "size"),
                )
            )
        return records

    def set_enabled(self, rule_id: str, enabled: bool) -> None:
        preamble, sections = _parse(self._read())
        changed = False
        rewritten: list[tuple[str, str]] = []
        for name, chunk in sections:
            if name == rule_id:
                chunk = _upsert(chunk, "Enabled", None if enabled else "false")
                changed = True
            rewritten.append((name, chunk))
        if not changed:
            return
        self._write(_join(preamble, rewritten))
        self._reconfigure()

    def delete_rule(self, rule_id: str) -> None:
        preamble, sections = _parse(self._read())
        kept = [(name, chunk) for name, chunk in sections if name != rule_id]
        if len(kept) == len(sections):
            return
        kept = _update_general(kept, drop={rule_id}, add=[])
        self._write(_join(preamble, kept))
        self._reconfigure()

    def replace_onigiri_rules(self, placements: list[Placement], prefix: str = DESCRIPTION_PREFIX) -> int:
        """Drop rules with this prefix, then write Force rules for placements that match a window."""

        preamble, sections = _parse(self._read())
        removed: set[str] = set()
        kept: list[tuple[str, str]] = []
        for name, chunk in sections:
            if name != "General" and _value(chunk, "Description").startswith(prefix):
                removed.add(name)
                continue
            kept.append((name, chunk))
        fresh: list[tuple[str, Placement]] = []
        for placement in placements:
            if placement.match_kind not in {"class", "title"} or not placement.match_value:
                continue
            fresh.append((str(uuid4()), placement))
        kept = _update_general(kept, drop=removed, add=[rule_id for rule_id, _placement in fresh])
        for rule_id, placement in fresh:
            kept.append((rule_id, _render_rule(rule_id, placement)))
        self._write(_join(preamble, kept))
        self._reconfigure()
        return len(fresh)


def _update_general(
    sections: list[tuple[str, str]],
    drop: set[str],
    add: list[str],
) -> list[tuple[str, str]]:
    general_index = next((index for index, (name, _chunk) in enumerate(sections) if name == "General"), None)
    if general_index is None:
        ids = [rule_id for rule_id in add if rule_id]
        joined = ",".join(ids)
        chunk = "[General]\n" + f"count={len(ids)}\n" + f"rules={joined}\n" + f"Order={joined}\n"
        return [("General", chunk), *sections]
    name, chunk = sections[general_index]
    ids = [rule_id for rule_id in _rules_ids(chunk) if rule_id not in drop]
    for rule_id in add:
        if rule_id and rule_id not in ids:
            ids.append(rule_id)
    joined = ",".join(ids)
    chunk = _upsert(chunk, "rules", joined)
    chunk = _upsert(chunk, "Order", joined)
    chunk = _upsert(chunk, "count", str(len(ids)))
    updated = list(sections)
    updated[general_index] = (name, chunk)
    return updated


def _join(preamble: str, sections: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    if preamble:
        parts.append(preamble)
    for _name, chunk in sections:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        parts.append(chunk)
    text = "".join(parts)
    if text and not text.endswith("\n"):
        text += "\n"
    return text
