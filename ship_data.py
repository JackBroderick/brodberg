"""
ship_data.py
------------
AIS vessel tracking via aisstream.io WebSocket API.

Map data is loaded from a plain-text file in data/ (e.g. data/hormuz.txt).
Edit that file to change the map — no code changes needed.

MAP FILE FORMAT
━━━━━━━━━━━━━━
Lines beginning with a lat label followed by '|' are map rows:
    24.00|████·····...
The characters after '|' are the 80-column map grid.

  █  = land cell   (orange in terminal)
  ·  = water cell  (blue in terminal)
  space = also water
  Any other printable char = drawn as-is (used for inline labels like
        "I R A N", "OMAN", "U A E", "Hormuz", "Gulf of Oman")

Header lines (before the first data row) supply viewport metadata:
    Viewport: lat 24.00 (bottom) → 28.00 (top) | lon 54.00 (left) → 62.00 (right)
    Grid: 80 cols × 30 rows

HOW TO ADD A NEW LOCATION
━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Create a new file in data/ following the format above.
  2. Add an entry to LOCATIONS pointing at the file (relative to project root).
  3. Add an alias to LOCATION_ALIASES.
  4. cmd_ship.py needs no changes.

Dependency: pip install websockets
"""

import json
import re
import threading
import asyncio
import os
import sys

from api_keys import AISSTREAM_API_KEY


# ---------------------------------------------------------------------------
# PyInstaller-safe resource path
# ---------------------------------------------------------------------------
# ship_data.py lives at the PROJECT ROOT, so __file__ already points there.
# We still handle the frozen (.exe) case via sys._MEIPASS.
# ---------------------------------------------------------------------------

def resource_path(relative: str) -> str:
    """
    Resolve a path relative to the PROJECT ROOT.
    Works both as a raw script and as a PyInstaller --onefile .exe.
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


# ---------------------------------------------------------------------------
# Map file parser
# ---------------------------------------------------------------------------

# Characters treated as water (rendered blue).
WATER_CHARS = set("·. ")

# Character treated as land (rendered orange)
LAND_CHAR = "█"


def load_map_file(path: str) -> dict:
    """
    Parse a plain-text map file and return a config dict.

    `path` is relative to the project root (e.g. "data/hormuz.txt").
    It is resolved via resource_path() so it works as a script or .exe.

    Returns dict with keys:
        label       str
        lat_range   (float, float)
        lon_range   (float, float)
        map_rows    list[str]
        grid_rows   int
        grid_cols   int
    """
    resolved = resource_path(path)
    with open(resolved, encoding="utf-8") as f:
        raw_lines = f.readlines()

    label     = ""
    lat_min   = lat_max = lon_min = lon_max = None
    grid_cols = 80
    grid_rows = 30
    data_rows = []

    for line in raw_lines:
        line_stripped = line.rstrip("\n")

        # ── Location label (first non-blank line) ────────────────────────
        if not label and line_stripped.strip() and not line_stripped.startswith("Viewport"):
            candidate = line_stripped.split("—")[0].strip()
            if candidate:
                label = candidate

        # ── Viewport metadata ────────────────────────────────────────────
        vp_match = re.search(
            r"lat\s+([\d.]+)\s*\(bottom\).*?([\d.]+)\s*\(top\).*?lon\s+([\d.]+)\s*\(left\).*?([\d.]+)\s*\(right\)",
            line_stripped,
        )
        if vp_match:
            lat_min = float(vp_match.group(1))
            lat_max = float(vp_match.group(2))
            lon_min = float(vp_match.group(3))
            lon_max = float(vp_match.group(4))

        # ── Grid size ────────────────────────────────────────────────────
        grid_match = re.search(r"(\d+)\s*cols.*?(\d+)\s*rows", line_stripped)
        if grid_match:
            grid_cols = int(grid_match.group(1))
            grid_rows = int(grid_match.group(2))

        # ── Data rows  (format:  "24.55|████·····...")  ──────────────────
        data_match = re.match(r"^\s*([\d.]+)\|(.*)$", line_stripped)
        if data_match:
            lat_val  = float(data_match.group(1))
            row_data = data_match.group(2)
            row_data = row_data.ljust(grid_cols)[:grid_cols]
            data_rows.append((lat_val, row_data))

    # Sort top→bottom (highest lat first)
    data_rows.sort(key=lambda x: x[0], reverse=True)
    map_rows = [row for (_, row) in data_rows]

    if lat_min is None:
        lat_min, lat_max = 24.0, 28.0
    if lon_min is None:
        lon_min, lon_max = 54.0, 62.0

    return {
        "label":     label or path,
        "lat_range": (lat_min, lat_max),
        "lon_range": (lon_min, lon_max),
        "map_rows":  map_rows,
        "grid_rows": len(map_rows) or grid_rows,
        "grid_cols": grid_cols,
    }


# ---------------------------------------------------------------------------
# Location definitions
# ---------------------------------------------------------------------------

LOCATIONS = {
    "HORMUZ": {
        "map_file": os.path.join("data", "hormuz.txt"),   # relative to project root
    },
}

DEFAULT_LOCATION = "HORMUZ"

LOCATION_ALIASES = {
    "HORMUZ": "HORMUZ",
    "STRAIT": "HORMUZ",
    "GULF":   "HORMUZ",
}

_map_cache: dict = {}


def get_location_config(location_key: str) -> dict | None:
    resolved = LOCATION_ALIASES.get(location_key.upper())
    if resolved is None:
        return None
    if resolved not in _map_cache:
        entry = LOCATIONS.get(resolved)
        if entry is None:
            return None
        cfg = load_map_file(entry["map_file"])
        _map_cache[resolved] = cfg
    return _map_cache[resolved]


# ---------------------------------------------------------------------------
# Ship data store
# ---------------------------------------------------------------------------

_ships       = {}
_ships_lock  = threading.Lock()
_active_loc  = None
_ws_thread   = None
_stop_event  = threading.Event()
_status      = "idle"
_status_lock = threading.Lock()
_msg_count   = 0
_msg_lock    = threading.Lock()


def _set_status(msg: str):
    global _status
    with _status_lock:
        _status = msg


def get_status() -> str:
    with _status_lock:
        return _status


def get_ships_snapshot() -> dict:
    with _ships_lock:
        return dict(_ships)


def get_active_location() -> str | None:
    return _active_loc


def get_msg_count() -> int:
    with _msg_lock:
        return _msg_count


# ---------------------------------------------------------------------------
# WebSocket thread
# ---------------------------------------------------------------------------

def _ws_loop(location_key: str, stop_event: threading.Event):
    try:
        import websockets
    except ImportError:
        _set_status("error: run 'pip install websockets'")
        return

    cfg = get_location_config(location_key)
    lat_min, lat_max = cfg["lat_range"]
    lon_min, lon_max = cfg["lon_range"]
    bbox = [[lat_min, lon_min], [lat_max, lon_max]]

    async def _run():
        uri = "wss://stream.aisstream.io/v0/stream"
        subscribe_msg = json.dumps({
            "APIKey":             AISSTREAM_API_KEY,
            "BoundingBoxes":      [bbox],
            "FilterMessageTypes": ["PositionReport"],
        })
        _set_status("connecting")

        try:
            async with websockets.connect(uri) as ws:
                await ws.send(subscribe_msg)
                _set_status("live")

                async for raw in ws:
                    if stop_event.is_set():
                        break

                    global _msg_count
                    with _msg_lock:
                        _msg_count += 1

                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="ignore")

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("MessageType") != "PositionReport":
                        continue

                    meta   = msg.get("MetaData", {})
                    report = msg.get("Message", {}).get("PositionReport", {})

                    mmsi   = str(report.get("UserID", ""))
                    lat    = report.get("Latitude",  None)
                    lon    = report.get("Longitude", None)
                    speed  = float(report.get("Sog", 0.0))
                    course = float(report.get("Cog", 0.0))
                    name   = meta.get("ShipName", "").strip() or mmsi

                    if mmsi and lat is not None and lon is not None:
                        with _ships_lock:
                            _ships[mmsi] = {
                                "lat":    lat,
                                "lon":    lon,
                                "name":   name,
                                "speed":  speed,
                                "course": course,
                                "mmsi":   mmsi,
                            }

        except Exception as exc:
            _set_status(f"error: {exc}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def subscribe(location_key: str):
    global _ws_thread, _stop_event, _active_loc, _ships

    location_key = location_key.upper()
    resolved     = LOCATION_ALIASES.get(location_key)

    if resolved is None:
        _set_status(f"error: unknown location '{location_key}'")
        return
    location_key = resolved

    if _active_loc == location_key and _ws_thread and _ws_thread.is_alive():
        return

    if _ws_thread and _ws_thread.is_alive():
        _stop_event.set()
        _ws_thread.join(timeout=3)

    _stop_event = threading.Event()
    with _ships_lock:
        _ships = {}
    _active_loc = location_key
    _set_status("connecting")

    _ws_thread = threading.Thread(
        target=_ws_loop,
        args=(location_key, _stop_event),
        daemon=True,
    )
    _ws_thread.start()