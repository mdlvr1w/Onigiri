"""Hue helper for the hue.py scene plugin. Not an Onigiri extension.

Run this file to pair a bridge and assign lamps:

    python _hue.py

Writes ~/.config/onigiri/hue.json. hue.py reads that file when a scene applies.
Copy both files into ~/.config/onigiri/extensions/. Files that start with _
are helpers, not plugins.
"""

from __future__ import annotations

import colorsys
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

Rgb = tuple[int, int, int]

_DISCOVERY_URL = "https://discovery.meethue.com/"
_SSDP_ADDR = ("239.255.255.250", 1900)
_SSDP_BODY = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    "MAN: \"ssdp:discover\"\r\n"
    "MX: 2\r\n"
    "ST: upnp:rootdevice\r\n"
    "\r\n"
)


class LinkButtonNeeded(RuntimeError):
    """The Hue bridge is waiting for the physical link button."""


@dataclass
class HueBridge:
    ip: str
    name: str = ""
    bridge_id: str = ""


@dataclass
class HueLight:
    id: str
    name: str
    reachable: bool = True


@dataclass
class HueSettings:
    bridge_ip: str = ""
    username: str = ""
    blue: list[str] = field(default_factory=list)
    purple: list[str] = field(default_factory=list)
    clientkey: str = ""

    def paired(self) -> bool:
        return bool(self.bridge_ip and self.username)

    def to_dict(self) -> dict:
        data = {
            "bridge_ip": self.bridge_ip,
            "username": self.username,
            "clientkey": self.clientkey,
            "blue": list(self.blue),
            "purple": list(self.purple),
        }
        return data

    @staticmethod
    def from_dict(data: dict) -> HueSettings:
        source = data if isinstance(data, dict) else {}
        return HueSettings(
            bridge_ip=str(source.get("bridge_ip") or source.get("ip") or "").strip(),
            username=str(source.get("username") or "").strip(),
            clientkey=str(source.get("clientkey") or "").strip(),
            blue=_id_list(source.get("blue")),
            purple=_id_list(source.get("purple")),
        )


def _id_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def hue_config_path() -> Path:
    return Path.home() / ".config" / "onigiri" / "hue.json"


def load_hue(path: Path | None = None) -> HueSettings:
    target = path or hue_config_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return HueSettings()
    if not isinstance(data, dict):
        return HueSettings()
    return HueSettings.from_dict(data)


def save_hue(settings: HueSettings, path: Path | None = None) -> None:
    target = path or hue_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.to_dict(), indent=2) + "\n"
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                     prefix=target.name + ".", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def light_options(
    settings: HueSettings, lights: list[HueLight], slot: str
) -> list[tuple[HueLight, bool, bool]]:
    """Return (light, checked, enabled) for a color. Lights on the other color stay locked."""
    mine = set(settings.blue if slot == "blue" else settings.purple)
    other = set(settings.purple if slot == "blue" else settings.blue)
    options: list[tuple[HueLight, bool, bool]] = []
    for light in lights:
        taken = light.id in other
        options.append((light, light.id in mine and not taken, not taken))
    return options


def assigned_lights(settings: HueSettings, lights: list[HueLight], slot: str) -> list[HueLight]:
    """Lights already mapped to this Onigiri color on the main page."""
    mine = set(settings.blue if slot == "blue" else settings.purple)
    return [light for light in lights if light.id in mine]


def assign_light(settings: HueSettings, light_id: str, slot: str | None) -> bool:
    """Move a light onto blue/purple, or off both. False if the other color already owns it."""
    other = settings.purple if slot == "blue" else settings.blue if slot == "purple" else []
    if slot and light_id in other:
        return False
    settings.blue = [item for item in settings.blue if item != light_id]
    settings.purple = [item for item in settings.purple if item != light_id]
    if slot == "blue":
        settings.blue.append(light_id)
    elif slot == "purple":
        settings.purple.append(light_id)
    return True


def http_json(method: str, url: str, body=None, timeout: float = 5.0):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        if not raw:
            raise RuntimeError(f"Hue returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the Hue bridge: {exc.reason}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("Hue returned invalid JSON.") from exc


# Typical Hue gamut C (wide RGB bulbs and strips).
_GAMUT_C = ((0.6915, 0.3083), (0.17, 0.7), (0.1532, 0.0475))


def _gamma(channel: float) -> float:
    value = max(0.0, min(1.0, channel))
    if value > 0.04045:
        return ((value + 0.055) / 1.055) ** 2.4
    return value / 12.92


def _closest_point(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    span = (end[0] - start[0], end[1] - start[1])
    length = span[0] * span[0] + span[1] * span[1]
    if length == 0:
        return start
    t = ((point[0] - start[0]) * span[0] + (point[1] - start[1]) * span[1]) / length
    t = max(0.0, min(1.0, t))
    return (start[0] + t * span[0], start[1] + t * span[1])


def _clamp_xy(point: tuple[float, float]) -> tuple[float, float]:
    red, green, blue = _GAMUT_C
    v0 = (green[0] - red[0], green[1] - red[1])
    v1 = (blue[0] - red[0], blue[1] - red[1])
    v2 = (point[0] - red[0], point[1] - red[1])
    dot00 = v0[0] * v0[0] + v0[1] * v0[1]
    dot01 = v0[0] * v1[0] + v0[1] * v1[1]
    dot02 = v0[0] * v2[0] + v0[1] * v2[1]
    dot11 = v1[0] * v1[0] + v1[1] * v1[1]
    dot12 = v1[0] * v2[0] + v1[1] * v2[1]
    denom = dot00 * dot11 - dot01 * dot01
    if denom == 0:
        return point
    u = (dot11 * dot02 - dot01 * dot12) / denom
    v = (dot00 * dot12 - dot01 * dot02) / denom
    if u >= 0 and v >= 0 and u + v < 1:
        return point
    edges = (
        _closest_point(point, red, green),
        _closest_point(point, green, blue),
        _closest_point(point, blue, red),
    )
    return min(edges, key=lambda item: (item[0] - point[0]) ** 2 + (item[1] - point[1]) ** 2)


_FULL_BRIGHTNESS = 254


def rgb_to_xy(color: Rgb) -> tuple[float, float, int]:
    """Convert 8-bit RGB to Hue CIE xy. Brightness is always full (254)."""

    red, green, blue = (_gamma(channel / 255.0) for channel in color)
    x_comp = red * 0.664511 + green * 0.154324 + blue * 0.162028
    y_comp = red * 0.283881 + green * 0.668433 + blue * 0.047685
    z_comp = red * 0.000088 + green * 0.072310 + blue * 0.986039
    total = x_comp + y_comp + z_comp
    if total <= 0:
        point = (0.3127, 0.3290)
    else:
        point = _clamp_xy((x_comp / total, y_comp / total))
    return (round(point[0], 4), round(point[1], 4), _FULL_BRIGHTNESS)


def rgb_to_hs(color: Rgb) -> tuple[int, int]:
    """Hue/sat the Play bars actually apply; xy alone is often ignored."""
    hue, sat, _value = colorsys.rgb_to_hsv(color[0] / 255.0, color[1] / 255.0, color[2] / 255.0)
    return int(hue * 65535) % 65536, max(0, min(254, int(round(sat * 254))))


def _nudge_xy(x_pos: float, y_pos: float) -> list[float]:
    shift_x = -0.05 if x_pos >= 0.5 else 0.05
    shift_y = -0.05 if y_pos >= 0.5 else 0.05
    nudged = _clamp_xy((x_pos + shift_x, y_pos + shift_y))
    return [round(nudged[0], 4), round(nudged[1], 4)]


def _unique_ips(addresses: list[str]) -> list[str]:
    seen: list[str] = []
    for raw in addresses:
        ip = raw.strip()
        if ip and ip not in seen:
            seen.append(ip)
    return seen


def discover_meethue(fetch=None) -> list[str]:
    getter = fetch or (lambda: http_json("GET", _DISCOVERY_URL, timeout=4.0))
    try:
        data = getter()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    found: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ip = str(item.get("internalipaddress") or "").strip()
        if ip:
            found.append(ip)
    return found


def discover_ssdp(timeout: float = 2.0, sock=None) -> list[str]:
    owned = sock is None
    connection = sock
    if connection is None:
        connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        connection.settimeout(timeout)
    found: list[str] = []
    try:
        connection.sendto(_SSDP_BODY.encode("utf-8"), _SSDP_ADDR)
        while True:
            try:
                payload, address = connection.recvfrom(1024)
            except TimeoutError:
                break
            except socket.timeout:
                break
            text = payload.decode("utf-8", "ignore")
            if "hue-bridgeid" in text.casefold() or "philips hue" in text.casefold():
                found.append(address[0])
                continue
            for line in text.splitlines():
                if line.lower().startswith("location:"):
                    host = line.split(":", 1)[1].strip()
                    if "://" in host:
                        host = host.split("://", 1)[1]
                    host = host.split("/", 1)[0].split("]")[-1]
                    host = host.split(":")[0].strip()
                    if host:
                        found.append(host)
    except OSError:
        return []
    finally:
        if owned:
            connection.close()
    return found


def probe_bridge(ip: str, fetch=None) -> HueBridge | None:
    getter = fetch or (lambda: http_json("GET", f"http://{ip}/api/config", timeout=2.0))
    try:
        data = getter()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("bridgeid") and "Hue" not in str(data.get("name") or ""):
        return None
    return HueBridge(
        ip=ip,
        name=str(data.get("name") or "Hue Bridge"),
        bridge_id=str(data.get("bridgeid") or ""),
    )


def discover_bridges(*, meethue=None, ssdp=None, probe=None) -> list[HueBridge]:
    ips = _unique_ips((meethue or discover_meethue)() + (ssdp or discover_ssdp)())
    found: list[HueBridge] = []
    checker = probe or probe_bridge
    for ip in ips:
        bridge = checker(ip)
        if bridge is not None:
            found.append(bridge)
    return found


def pair_bridge(ip: str, *, device: str = "onigiri#onigiri", post=None) -> str:
    sender = post or (lambda: http_json("POST", f"http://{ip}/api", {"devicetype": device}, timeout=5.0))
    data = sender()
    if not isinstance(data, list) or not data:
        raise RuntimeError("Hue pairing returned an unexpected reply.")
    first = data[0] if isinstance(data[0], dict) else {}
    error = first.get("error") if isinstance(first.get("error"), dict) else None
    if error is not None:
        if int(error.get("type") or 0) == 101:
            raise LinkButtonNeeded("Press the button on the Hue bridge, then pair again.")
        raise RuntimeError(str(error.get("description") or "Hue pairing failed."))
    success = first.get("success") if isinstance(first.get("success"), dict) else None
    username = str((success or {}).get("username") or "").strip()
    if not username:
        raise RuntimeError("Hue pairing did not return a username.")
    return username


def pair_entertainment(settings: HueSettings, *, post=None) -> HueSettings:
    """Authorize streaming once, preserving this bridge's existing lamp assignments."""
    from dataclasses import replace
    sender = post or (lambda: http_json(
        "POST", f"http://{settings.bridge_ip}/api",
        {"devicetype": "onigiri#onigiri", "generateclientkey": True}, timeout=5.0))
    data = sender()
    # Reuse the existing response/error validation.
    username = pair_bridge(settings.bridge_ip, post=lambda: data)
    success = data[0].get("success", {})
    key = str(success.get("clientkey") or "").strip()
    try:
        valid = len(bytes.fromhex(key)) == 16
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError("Hue did not return a valid Entertainment client key.")
    return replace(settings, username=username, clientkey=key)


def list_lights(settings: HueSettings, fetch=None) -> list[HueLight]:
    if not settings.paired():
        return []
    getter = fetch or (
        lambda: http_json("GET", f"http://{settings.bridge_ip}/api/{settings.username}/lights", timeout=5.0)
    )
    data = getter()
    if not isinstance(data, dict):
        raise RuntimeError("Hue lights reply was not a list of lamps.")
    lights: list[HueLight] = []
    for light_id, info in data.items():
        if not isinstance(info, dict):
            continue
        state = info.get("state") if isinstance(info.get("state"), dict) else {}
        lights.append(
            HueLight(
                id=str(light_id),
                name=str(info.get("name") or f"Light {light_id}").strip() or f"Light {light_id}",
                reachable=bool(state.get("reachable", True)),
            )
        )
    lights.sort(key=lambda light: light.name.casefold())
    return lights


def color_taken(state, payload: dict, xy_tol: float = 0.04) -> bool:
    """True when a GET light state matches the color we just sent."""
    if not isinstance(state, dict):
        return False
    if payload.get("on") and not state.get("on"):
        return False
    if state.get("reachable") is False:
        return False
    wanted = payload.get("xy")
    have = state.get("xy")
    if isinstance(wanted, (list, tuple)) and len(wanted) >= 2:
        if not isinstance(have, (list, tuple)) or len(have) < 2:
            return False
        if abs(float(have[0]) - float(wanted[0])) > xy_tol:
            return False
        if abs(float(have[1]) - float(wanted[1])) > xy_tol:
            return False
    if payload.get("bri") is not None and state.get("bri") is not None:
        if abs(int(state["bri"]) - int(payload["bri"])) > 4:
            return False
    return True


def set_light_color(
    settings: HueSettings,
    light_id: str,
    color: Rgb,
    put=None,
    get=None,
    attempts: int = 3,
    wait: float = 0.2,
) -> None:
    x_pos, y_pos, brightness = rgb_to_xy(color)
    hue, sat = rgb_to_hs(color)
    wanted = {
        "on": True,
        "xy": [x_pos, y_pos],
        "bri": brightness,
        "hue": hue,
        "sat": sat,
        "transitiontime": 0,
    }
    if put is not None and get is None:
        put(
            light_id,
            {
                "on": True,
                "xy": [x_pos, y_pos],
                "bri": brightness,
                "transitiontime": 0,
            },
        )
        return

    state_url = f"http://{settings.bridge_ip}/api/{settings.username}/lights/{light_id}/state"
    light_url = f"http://{settings.bridge_ip}/api/{settings.username}/lights/{light_id}"

    def send(payload: dict) -> None:
        if put is not None:
            put(light_id, payload)
            return
        http_json("PUT", state_url, payload, timeout=2.0)

    def pause() -> None:
        if wait > 0:
            time.sleep(wait)

    def read():
        if get is not None:
            return get(light_id)
        data = http_json("GET", light_url, timeout=2.0)
        return data.get("state") if isinstance(data, dict) else None

    try:
        state = read()
    except (OSError, RuntimeError, TimeoutError, ValueError, StopIteration, IndexError):
        state = None

    # Play bars keep the old LEDs when the bridge already stored this xy.
    # Nudge first so the next command is a real change, then HS (colormode
    # switch) and xy. Sequential PUTs; GET is not proof the lamp moved.
    if color_taken(state, wanted):
        send(
            {
                "on": True,
                "xy": _nudge_xy(x_pos, y_pos),
                "hue": (hue + 4000) % 65536,
                "sat": max(80, sat),
                "bri": brightness,
                "transitiontime": 1,
            }
        )
        pause()
    send({"on": True, "hue": hue, "sat": sat, "bri": brightness, "transitiontime": 1})
    pause()
    xy_payload = {"on": True, "xy": [x_pos, y_pos], "bri": brightness, "transitiontime": 2}
    send(xy_payload)
    pause()
    tries = max(1, int(attempts))
    for _ in range(tries):
        send(xy_payload)
        pause()
        try:
            state = read()
        except (OSError, RuntimeError, TimeoutError, ValueError, StopIteration, IndexError):
            state = None
        if color_taken(state, wanted):
            return


def paint_hue(
    blue: Rgb,
    purple: Rgb,
    path: Path | None = None,
    *,
    settings: HueSettings | None = None,
    put=None,
    get=None,
    attempts: int = 3,
    wait: float = 0.2,
) -> None:
    config = settings if settings is not None else load_hue(path)
    if not config.paired():
        return
    jobs = [(light_id, blue) for light_id in config.blue] + [(light_id, purple) for light_id in config.purple]
    if not jobs:
        return
    if put is not None and get is None:
        for light_id, color in jobs:
            set_light_color(config, light_id, color, put=put)
        return
    for light_id, color in jobs:
        set_light_color(
            config,
            light_id,
            color,
            put=put,
            get=get,
            attempts=attempts,
            wait=wait,
        )


def _prompt(text: str) -> str:
    try:
        return input(text)
    except EOFError:
        return ""


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Pair Hue lamps or enable screen-sync streaming")
    parser.add_argument("--entertainment", action="store_true", help="Add streaming credentials without changing lamp assignments")
    args = parser.parse_args()
    path = hue_config_path()
    print(f"Pairing file: {path}")
    existing = load_hue(path)
    if args.entertainment and existing.bridge_ip:
        print("Press the button on your existing Hue bridge, then press Enter.")
        _prompt("")
        try:
            upgraded = pair_entertainment(existing)
        except RuntimeError as error:
            print(error)
            return 1
        save_hue(upgraded, path)
        print("Streaming authorized. Existing lamp assignments were preserved.")
        return 0
    if args.entertainment:
        print("Pair the bridge first by running this helper without --entertainment.")
        return 1
    print("Looking for Hue bridges…")
    bridges = discover_bridges()
    ip = ""
    if bridges:
        for index, bridge in enumerate(bridges, start=1):
            print(f"  {index}. {bridge.name} ({bridge.ip})")
        choice = _prompt("Bridge number, or an IP: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(bridges):
            ip = bridges[int(choice) - 1].ip
        else:
            ip = choice
    else:
        ip = _prompt("No bridge found. IP address: ").strip()
    if not ip:
        print("No bridge selected.")
        return 1
    print("Press the button on the Hue bridge, then press Enter.")
    _prompt("")
    while True:
        try:
            username = pair_bridge(ip)
            break
        except LinkButtonNeeded as error:
            print(error)
            again = _prompt("Press Enter to retry, or q to quit: ").strip().lower()
            if again == "q":
                return 1
    settings = HueSettings(bridge_ip=ip, username=username)
    try:
        lights = list_lights(settings)
    except RuntimeError as error:
        print(error)
        lights = []
    if lights:
        print("Assign each lamp: c = cool, w = warm, Enter = skip")
        for light in lights:
            label = light.name if light.reachable else f"{light.name} (offline)"
            choice = _prompt(f"  {label} [{light.id}]: ").strip().lower()
            if choice in {"c", "cool", "b", "blue"}:
                settings.blue.append(light.id)
            elif choice in {"w", "warm", "p", "purple"}:
                settings.purple.append(light.id)
    save_hue(settings, path)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
