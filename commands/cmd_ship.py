"""
commands/cmd_ship.py
--------------------
Implements the  SHIP [LOCATION]  command — live AIS vessel tracking map.
 
Rendering order (painter's algorithm):
  1. Stamp each map row character by character:
       █  → orange on orange (land)
       ·  → space on blue (water)
       other chars → inline label with matching background
  2. Overlay live ship markers (●) — colored fg, blue bg (water)
 
Each ship gets a unique color. Sidebar shows matching colored dot + white name.
 
Color pair IDs used here: 20–34 (above the 9 reserved by colors.py)
  20-26 = ship colors on BLUE background  (for map markers)
  27-33 = same ship colors on BLACK background (for sidebar dots)
"""
 
import curses
import ship_data
 
# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
 
SHIP_MARKER   = "●"
MAP_ROW_START = 4
MAP_COL_START = 2
SIDEBAR_WIDTH = 34
MAX_SIDEBAR   = 14
 
# ---------------------------------------------------------------------------
# Hardcoded label color mapping
# Keys match the trimmed label text exactly as it appears in the .map file.
# Land  labels → grey text on orange background  ("land_label"  color)
# Water labels → white text on blue background   ("water_label" color)
# To add a label for a new map, just add it to the appropriate set.
# ---------------------------------------------------------------------------

LAND_LABELS  = {"I R A N", "U A E", "OMAN"}
WATER_LABELS = {"Hormuz", "Gulf of Oman", "Persian Gulf"}


def _label_color(label_text: str, colors: dict) -> int:
    """Return the curses color attr for a map label, or water_label as default."""
    if label_text in LAND_LABELS:
        return colors["land_label"]
    if label_text in WATER_LABELS:
        return colors["water_label"]
    return colors["water_label"]   # safe default for any unknown label
 
# ---------------------------------------------------------------------------
# Per-ship palette  — (pair_id_on_blue, pair_id_on_black, fg_color)
# ---------------------------------------------------------------------------
 
_PALETTE = [
    (20, 27, curses.COLOR_CYAN),
    (21, 28, curses.COLOR_MAGENTA),
    (22, 29, curses.COLOR_YELLOW),
    (23, 30, curses.COLOR_GREEN),
    (24, 31, curses.COLOR_RED),
    (25, 32, curses.COLOR_WHITE),
    (26, 33, curses.COLOR_BLUE),
]
 
_pairs_on_blue  = []   # for map markers (blue bg)
_pairs_on_black = []   # for sidebar dots (black bg)
_pairs_ready    = False
 
 
def _ensure_ship_colors():
    global _pairs_on_blue, _pairs_on_black, _pairs_ready
    if _pairs_ready:
        return
    _pairs_on_blue  = []
    _pairs_on_black = []
    for (id_blue, id_black, fg) in _PALETTE:
        try:
            curses.init_pair(id_blue,  fg, curses.COLOR_BLUE)
            curses.init_pair(id_black, fg, curses.COLOR_BLACK)
            _pairs_on_blue.append(curses.color_pair(id_blue))
            _pairs_on_black.append(curses.color_pair(id_black))
        except Exception:
            pass
    _pairs_ready = True
 
 
def _map_color(idx):
    """Ship color on blue background — for map marker."""
    if not _pairs_on_blue:
        return 0
    return _pairs_on_blue[idx % len(_pairs_on_blue)]
 
 
def _sidebar_color(idx):
    """Ship color on black background — for sidebar dot."""
    if not _pairs_on_black:
        return 0
    return _pairs_on_black[idx % len(_pairs_on_black)]
 
 
# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
 
def fetch(parts: list) -> dict:
    loc_key = parts[1] if len(parts) > 1 else ship_data.DEFAULT_LOCATION
 
    cfg = ship_data.get_location_config(loc_key)
    if cfg is None:
        valid = ", ".join(ship_data.LOCATION_ALIASES.keys())
        return {
            "error":  f"Unknown location '{loc_key}'.  Valid: {valid}",
            "loc":    None,
            "config": None,
        }
 
    ship_data.subscribe(loc_key.upper())
    return {
        "error":  None,
        "loc":    loc_key.upper(),
        "config": cfg,
    }
 
 
# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
 
def render(stdscr, cache: dict, colors: dict) -> None:
    height, width = stdscr.getmaxyx()
 
    _ensure_ship_colors()
 
    # ── Error state ───────────────────────────────────────────────────────
    if cache.get("error"):
        try:
            stdscr.attron(colors["negative"])
            stdscr.addstr(MAP_ROW_START, MAP_COL_START,
                          f"  SHIP ERROR: {cache['error']}")
            stdscr.attroff(colors["negative"])
        except Exception:
            pass
        return
 
    cfg       = cache["config"]
    status    = ship_data.get_status()
    ships     = ship_data.get_ships_snapshot()
    msg_count = ship_data.get_msg_count()
 
    if status == "live":
        status_color = colors["positive"]
    elif status.startswith("error"):
        status_color = colors["negative"]
    else:
        status_color = colors["dim"]
 
    # Stable color index per MMSI
    sorted_mmsi     = sorted(ships.keys())
    mmsi_index      = {mmsi: i for i, mmsi in enumerate(sorted_mmsi)}
 
    # ── Title bar ─────────────────────────────────────────────────────────
    title = (f"  SHIP  |  {cfg['label']}  |  "
             f"{len(ships)} vessel(s)  |  {msg_count} msg(s) recv")
    try:
        stdscr.attron(colors["orange"])
        stdscr.addstr(MAP_ROW_START, 0, title[: width - 1])
        stdscr.attroff(colors["orange"])
    except Exception:
        pass
 
    status_label = f"[{status.upper()}]"
    try:
        stdscr.attron(status_color | curses.A_BOLD)
        stdscr.addstr(MAP_ROW_START, width - len(status_label) - 2, status_label)
        stdscr.attroff(status_color | curses.A_BOLD)
    except Exception:
        pass
 
    # ── Map geometry ──────────────────────────────────────────────────────
    map_rows  = cfg["map_rows"]
    grid_rows = cfg["grid_rows"]
    grid_cols = cfg["grid_cols"]
 
    map_top  = MAP_ROW_START + 2
    map_left = MAP_COL_START
 
    sep_col = map_left + grid_cols + 1
    sb_col  = sep_col + 2
 
    # ── Vertical separator ────────────────────────────────────────────────
    for r in range(map_top, map_top + grid_rows + 1):
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(r, sep_col, "│")
            stdscr.attroff(colors["dim"])
        except Exception:
            pass
 
    # ── Color aliases ─────────────────────────────────────────────────────
    land_color  = colors["orange"]    # orange fg, orange bg (land tiles)
    water_color = colors.get("water") # space on blue bg (water tiles)
 
    # ── Render map rows character by character ────────────────────────────
    for row_idx, row_str in enumerate(map_rows):
        term_row = map_top + row_idx
        if term_row >= height - 1:
            break
 
        col      = map_left
        char_idx = 0
        while char_idx < len(row_str):
            if col >= width - 1:
                break
 
            ch = row_str[char_idx]
 
            if ch == ship_data.LAND_CHAR:
                try:
                    stdscr.attron(land_color)
                    stdscr.addstr(term_row, col, ch)
                    stdscr.attroff(land_color)
                except Exception:
                    pass
                col      += 1
                char_idx += 1
 
            elif ch in ship_data.WATER_CHARS:
                try:
                    stdscr.attron(water_color)
                    stdscr.addstr(term_row, col, " ")
                    stdscr.attroff(water_color)
                except Exception:
                    pass
                col      += 1
                char_idx += 1
 
            else:
                # Collect entire label run.
                # Stop only on █ (land) or · (explicit water marker).
                # Plain spaces are allowed inside labels like "I R A N" and
                # "U A E" — trailing spaces are stripped after collection.
                label_start = char_idx
                while char_idx < len(row_str):
                    c = row_str[char_idx]
                    if c == ship_data.LAND_CHAR or c == "·":
                        break
                    char_idx += 1
                # Strip trailing spaces so the lookup key matches exactly
                label_text = row_str[label_start:char_idx].rstrip()
 
                lcolor = _label_color(label_text, colors) | curses.A_BOLD
 
                try:
                    stdscr.attron(lcolor)
                    stdscr.addstr(term_row, col, row_str[label_start:char_idx])
                    stdscr.attroff(lcolor)
                except Exception:
                    pass
                col += char_idx - label_start
 
    # ── Ship coordinate projection ────────────────────────────────────────
    lat_min, lat_max = cfg["lat_range"]
    lon_min, lon_max = cfg["lon_range"]
    lat_span = lat_max - lat_min
    lon_span = lon_max - lon_min
 
    def lat_to_row(lat):
        frac = (lat_max - lat) / lat_span
        return map_top + int(round(frac * (grid_rows - 1)))
 
    def lon_to_col(lon):
        frac = (lon - lon_min) / lon_span
        return map_left + int(round(frac * (grid_cols - 1)))
 
    # ── Overlay ship markers — colored fg on blue bg ──────────────────────
    for mmsi, ship in ships.items():
        r = lat_to_row(ship["lat"])
        c = lon_to_col(ship["lon"])
        if map_top <= r < map_top + grid_rows and map_left <= c < sep_col:
            idx = mmsi_index.get(mmsi, 0)
            try:
                stdscr.attron(_map_color(idx) | curses.A_BOLD)
                stdscr.addstr(r, c, SHIP_MARKER)
                stdscr.attroff(_map_color(idx) | curses.A_BOLD)
            except Exception:
                pass
 
    # ── Sidebar ───────────────────────────────────────────────────────────
    sb_row   = map_top
    sb_avail = height - map_top - 2
 
    try:
        stdscr.attron(colors["orange"])
        stdscr.addstr(sb_row, sb_col, "VESSELS IN RANGE")
        stdscr.attroff(colors["orange"])
        sb_row += 1
        stdscr.attron(colors["dim"])
        stdscr.addstr(sb_row, sb_col, "─" * (SIDEBAR_WIDTH - 3))
        stdscr.attroff(colors["dim"])
        sb_row += 1
    except Exception:
        pass
 
    if not ships:
        if status == "live":
            waiting = "Waiting for AIS data..."
        elif status == "connecting":
            waiting = "Connecting to stream..."
        else:
            waiting = status
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(sb_row, sb_col, waiting)
            stdscr.attroff(colors["dim"])
        except Exception:
            pass
    else:
        sorted_ships = sorted(ships.values(), key=lambda s: s["name"])
        for ship in sorted_ships[:MAX_SIDEBAR]:
            if sb_row >= map_top + sb_avail:
                break
 
            mmsi = ship["mmsi"]
            name = ship["name"][: SIDEBAR_WIDTH - 4]
            idx  = mmsi_index.get(mmsi, 0)
 
            # Colored dot — fg on black (sidebar background)
            try:
                stdscr.attron(_sidebar_color(idx) | curses.A_BOLD)
                stdscr.addstr(sb_row, sb_col, "● ")
                stdscr.attroff(_sidebar_color(idx) | curses.A_BOLD)
            except Exception:
                pass
 
            # White bold vessel name on black
            try:
                stdscr.attron(colors["dim"] | curses.A_BOLD)
                stdscr.addstr(sb_row, sb_col + 2, name)
                stdscr.attroff(colors["dim"] | curses.A_BOLD)
            except Exception:
                pass
 
            sb_row += 1
            if sb_row >= map_top + sb_avail:
                break
 
            # Speed / course
            try:
                stdscr.attron(colors["dim"])
                stdscr.addstr(sb_row, sb_col,
                              f"  {ship['speed']:.1f} kn  {ship['course']:.0f}°")
                stdscr.attroff(colors["dim"])
            except Exception:
                pass
            sb_row += 1
 
        overflow = len(ships) - MAX_SIDEBAR
        if overflow > 0 and sb_row < map_top + sb_avail:
            try:
                stdscr.attron(colors["dim"])
                stdscr.addstr(sb_row, sb_col, f"  … +{overflow} more")
                stdscr.attroff(colors["dim"])
            except Exception:
                pass
 
    # ── Legend ────────────────────────────────────────────────────────────
    legend_row = map_top + grid_rows + 1
    if legend_row < height - 1:
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(legend_row, map_left, "  █ land   ● vessel")
            stdscr.attroff(colors["dim"])
        except Exception:
            pass