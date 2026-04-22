"""
ui/loading.py
-------------
Radar sweep loading screen for Brodberg.

A circular radar dish rotates a sweep arm and picks up random signal blips.
When data arrives (start_crush called), the arm freezes, crosshairs lock onto
the ticker, then the radar dissolves inward to reveal the chart.

Visual anatomy:
  ──────── circle boundary drawn with ╱  ╲  ─  │  per angle
  ········ range rings at 1/3 and 2/3 radius
  · trail of past sweep arm positions (dim)
  · current sweep arm (bright orange, bold)
  ◉ detected signal blips (brighten on detect, fade over time)
  ── crosshairs + [ TICKER ] during lock-on
  ◈ SIGNAL ACQUIRED ◈ status line

Public API (unchanged from Brian's Brain version):
  render_loading(stdscr, cache, colors, start_row, label) -> bool
  start_crush(cache)
"""

import math
import random
import time
import curses


# ── Tuning ─────────────────────────────────────────────────────────────────
SWEEP_SPEED    = 0.22    # radians advanced per render frame  (~10fps → ~3s/rev)
TRAIL_LEN      = 18      # past arm positions kept in trail
BLIP_COUNT     = 9       # number of random signal targets seeded on init
BLIP_LIFETIME  = 4.0     # seconds a blip glows after being swept
LOCK_HOLD      = 0.75    # seconds crosshairs hold before dissolve begins
DISSOLVE_RATE  = 1.8     # radius units erased per frame during dissolve

# ── Internal state machine ─────────────────────────────────────────────────
_SCAN     = "scan"       # sweep rotating, fetch in flight
_LOCK     = "lock"       # data arrived, crosshairs visible
_DISSOLVE = "dissolve"   # radar shrinking inward, then done


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_ch(stdscr, r, c, ch, attr, h, w):
    """addch with bounds guard — never raises."""
    if 0 <= r < h and 0 <= c < w - 1:
        try:
            stdscr.addch(r, c, ch, attr)
        except Exception:
            pass


def _safe_str(stdscr, r, c, s, attr, h, w):
    """addstr with bounds guard — never raises."""
    if 0 <= r < h and 0 <= c < w - 1:
        try:
            stdscr.addstr(r, c, s[: w - c - 1], attr)
        except Exception:
            pass


def _circle_char(theta: float) -> str:
    """
    Pick the best box-drawing character for the circle boundary at angle theta.

    The radar circle is rendered as a visual ellipse in character space
    (r_col = 2 * r_row) so that it looks like a true circle on screen given
    the ~2:1 tall character aspect ratio.

    Slope of the circle boundary in character space = -cos(θ) / (2·sin(θ)).
    """
    s = math.sin(theta)
    c = math.cos(theta)
    if abs(s) < 0.16:          # near 0° / 180°  → boundary is vertical
        return "│"
    if abs(c) < 0.32:          # near 90° / 270° → boundary is horizontal
        return "─"
    slope = -c / (2.0 * s)
    return "╲" if slope > 0 else "╱"


def _in_radius(dr: int, dc: int, r_rows: int, r_cols: int,
               limit_r: float | None) -> bool:
    """
    True if the character at offset (dr, dc) from radar centre lies within
    limit_r rows of the centre (in visual / ellipse-normalised distance).
    Pass None to always return True (no dissolve limit).
    """
    if limit_r is None:
        return True
    if r_rows == 0 or r_cols == 0:
        return False
    # Normalise to a unit circle in visual space
    fr = dr / r_rows
    fc = dc / r_cols
    fl = limit_r / r_rows
    return fr * fr + fc * fc <= fl * fl + 0.01   # small epsilon for boundaries


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def _init_radar(cache: dict, win_h: int, win_w: int, start_row: int) -> None:
    """Seed all radar state into cache._rdr_* keys."""
    avail_rows = win_h - start_row
    cy         = start_row + avail_rows // 2
    cx         = win_w // 2

    # Radius: must fit vertically and horizontally (accounting for 2:1 aspect)
    r_rows = max(4, min(avail_rows // 2 - 1, win_w // 4 - 2))
    r_cols = r_rows * 2

    # Generate blips at random visual positions inside the circle
    blips = []
    for _ in range(BLIP_COUNT):
        vis_angle = random.uniform(0, 2 * math.pi)
        dist_frac = random.uniform(0.30, 0.88)
        dr = int(round(dist_frac * r_rows * math.sin(vis_angle)))
        dc = int(round(dist_frac * r_cols * math.cos(vis_angle)))
        # reveal_angle: the sweep angle at which this blip gets detected
        # atan2(visual_y, visual_x) = atan2(2·dr/r_rows, dc/r_cols) normalised
        reveal = math.atan2(2.0 * dr, dc) % (2 * math.pi)
        blips.append({
            "dr": dr, "dc": dc,
            "reveal": reveal,
            "detected": False,
            "detect_time": None,
        })

    cache.update({
        "_rdr_init":       True,
        "_rdr_win_h":      win_h,
        "_rdr_win_w":      win_w,
        "_rdr_start":      start_row,
        "_rdr_cy":         cy,
        "_rdr_cx":         cx,
        "_rdr_r_rows":     r_rows,
        "_rdr_r_cols":     r_cols,
        "_rdr_angle":      -math.pi / 2,  # start at 12 o'clock
        "_rdr_trail":      [],
        "_rdr_blips":      blips,
        "_rdr_state":      _SCAN,
        "_rdr_lock_time":  None,
        "_rdr_dissolve_r": None,
    })


# ---------------------------------------------------------------------------
# Drawing sub-routines
# ---------------------------------------------------------------------------

def _draw_rings(stdscr, cy, cx, r_rows, r_cols, limit_r, h, w, colors):
    """Faint dotted range rings at 1/3 and 2/3 of full radius."""
    dim = colors["dim"]
    for frac in (0.33, 0.67):
        rr    = r_rows * frac
        rc    = r_cols * frac
        steps = max(40, int(2 * math.pi * max(rr, rc) * 1.2))
        for i in range(steps):
            theta = 2 * math.pi * i / steps
            dr    = round(rr * math.sin(theta))
            dc    = round(rc * math.cos(theta))
            if _in_radius(dr, dc, r_rows, r_cols, limit_r):
                _safe_ch(stdscr, cy + dr, cx + dc, "·", dim, h, w)


def _draw_circle(stdscr, cy, cx, r_rows, r_cols, limit_r, h, w, colors):
    """Draw the outer radar circle boundary."""
    attr  = colors["orange"]
    steps = max(80, int(2 * math.pi * max(r_rows, r_cols) * 1.6))
    for i in range(steps):
        theta = 2 * math.pi * i / steps
        dr    = round(r_rows * math.sin(theta))
        dc    = round(r_cols * math.cos(theta))
        if _in_radius(dr, dc, r_rows, r_cols, limit_r):
            _safe_ch(stdscr, cy + dr, cx + dc, _circle_char(theta), attr, h, w)


def _draw_sweep(stdscr, cy, cx, r_rows, r_cols, angle, trail,
                limit_r, h, w, colors):
    """Draw the rotating sweep arm and its dimming trail."""
    dim    = colors["dim"]
    bright = colors["orange"] | curses.A_BOLD

    # Trail — past arm positions, dim
    for ta in trail:
        for t in range(1, r_rows + 1):
            dr = round(t * math.sin(ta))
            dc = round(t * 2 * math.cos(ta))
            if _in_radius(dr, dc, r_rows, r_cols, limit_r):
                _safe_ch(stdscr, cy + dr, cx + dc, "·", dim, h, w)

    # Current arm — bright, overwrites any trail overlap
    for t in range(1, r_rows + 1):
        dr = round(t * math.sin(angle))
        dc = round(t * 2 * math.cos(angle))
        if _in_radius(dr, dc, r_rows, r_cols, limit_r):
            _safe_ch(stdscr, cy + dr, cx + dc, "·", bright, h, w)

    # Centre dot
    if _in_radius(0, 0, r_rows, r_cols, limit_r):
        _safe_ch(stdscr, cy, cx, "·", bright, h, w)


def _draw_blips(stdscr, cy, cx, r_rows, r_cols, blips, limit_r, h, w, colors):
    """Render detected blips; fade them as they age."""
    now    = time.monotonic()
    bright = colors["orange"] | curses.A_BOLD
    dim    = colors["dim"]
    for b in blips:
        if not b["detected"]:
            continue
        age = now - b["detect_time"]
        if age > BLIP_LIFETIME:
            continue
        dr, dc = b["dr"], b["dc"]
        if _in_radius(dr, dc, r_rows, r_cols, limit_r):
            attr = bright if age < BLIP_LIFETIME * 0.55 else dim
            _safe_ch(stdscr, cy + dr, cx + dc, "◉", attr, h, w)


def _draw_crosshairs(stdscr, cy, cx, r_rows, r_cols, limit_r, h, w, colors):
    """Targeting crosshairs during lock-on / dissolve."""
    attr = colors["orange"] | curses.A_BOLD

    # Horizontal bar
    for dc in range(-r_cols, r_cols + 1):
        if not _in_radius(0, dc, r_rows, r_cols, limit_r):
            continue
        if dc == 0:
            continue   # centre drawn last
        _safe_ch(stdscr, cy, cx + dc, "─", attr, h, w)

    # Vertical bar
    for dr in range(-r_rows, r_rows + 1):
        if not _in_radius(dr, 0, r_rows, r_cols, limit_r):
            continue
        if dr == 0:
            continue
        _safe_ch(stdscr, cy + dr, cx, "│", attr, h, w)

    # Corner tick marks at ~60 % radius
    tick_r = max(1, int(r_rows * 0.60))
    tick_c = max(2, int(r_cols * 0.60))
    corners = [
        (-tick_r, -tick_c, "┌"), (-tick_r, tick_c, "┐"),
        ( tick_r, -tick_c, "└"), ( tick_r, tick_c, "┘"),
    ]
    for dr, dc, ch in corners:
        if _in_radius(dr, dc, r_rows, r_cols, limit_r):
            _safe_ch(stdscr, cy + dr, cx + dc, ch, attr, h, w)

    # Centre crosshair intersection
    if _in_radius(0, 0, r_rows, r_cols, limit_r):
        _safe_ch(stdscr, cy, cx, "┼", attr, h, w)


def _draw_lock_text(stdscr, cy, cx, r_rows, r_cols, limit_r,
                    label: str, h, w, colors):
    """Ticker name above centre + ACQUIRED banner below circle."""
    bright = colors["orange"] | curses.A_BOLD

    # Ticker centred one row above crosshair centre
    ticker = label.replace("FETCHING", "").strip()
    if ticker and _in_radius(-1, 0, r_rows, r_cols, limit_r):
        ts   = f"[ {ticker} ]"
        tcol = max(0, cx - len(ts) // 2)
        _safe_str(stdscr, cy - 1, tcol, ts, bright, h, w)

    # Status banner below the circle
    banner  = "  ◈  SIGNAL ACQUIRED  ◈  "
    ban_row = cy + r_rows + 1
    ban_col = max(0, cx - len(banner) // 2)
    _safe_str(stdscr, ban_row, ban_col, banner, bright, h, w)


def _detect_blips(blips: list, prev_angle: float, curr_angle: float) -> None:
    """Mark any blip whose reveal angle falls between prev and curr sweep angle."""
    p   = prev_angle % (2 * math.pi)
    c   = curr_angle % (2 * math.pi)
    now = time.monotonic()
    for b in blips:
        if b["detected"]:
            continue
        ra = b["reveal"]
        hit = (p <= ra <= c) if p <= c else (ra >= p or ra <= c)
        if hit:
            b["detected"]    = True
            b["detect_time"] = now


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_crush(cache: dict) -> None:
    """
    Signal that background data has arrived — arm the lock-on sequence.
    Safe to call multiple times; only transitions once from SCAN → LOCK.
    """
    if cache.get("_rdr_state") == _SCAN:
        cache["_rdr_state"]    = _LOCK
        cache["_rdr_lock_time"] = time.monotonic()


def render_loading(stdscr, cache: dict, colors: dict,
                   start_row: int = 5,
                   label: str = "FETCHING DATA") -> bool:
    """
    Render the radar sweep and advance its state machine.

    Parameters
    ----------
    stdscr    : curses subwindow passed to the command's render()
    cache     : mutable pane cache (radar state stored in _rdr_* keys)
    colors    : color dict from init_colors()
    start_row : first subwindow row available (rows 0..start_row-1 = tab bar)
    label     : "FETCHING AAPL" style string; ticker extracted for display

    Returns True when the dissolve completes — caller should flip to chart.
    """
    h, w = stdscr.getmaxyx()

    # (Re-)initialise if first call or window resized
    if (not cache.get("_rdr_init")
            or cache.get("_rdr_win_h") != h
            or cache.get("_rdr_win_w") != w):
        _init_radar(cache, h, w, start_row)

    cy       = cache["_rdr_cy"]
    cx       = cache["_rdr_cx"]
    r_rows   = cache["_rdr_r_rows"]
    r_cols   = cache["_rdr_r_cols"]
    angle    = cache["_rdr_angle"]
    trail    = cache["_rdr_trail"]
    blips    = cache["_rdr_blips"]
    state    = cache["_rdr_state"]
    limit_r  = cache["_rdr_dissolve_r"]   # None until dissolve begins

    # ── Always draw: rings + circle ────────────────────────────────────────
    _draw_rings(stdscr,  cy, cx, r_rows, r_cols, limit_r, h, w, colors)
    _draw_circle(stdscr, cy, cx, r_rows, r_cols, limit_r, h, w, colors)

    # ── State-specific content ─────────────────────────────────────────────
    if state == _SCAN:
        _draw_sweep(stdscr, cy, cx, r_rows, r_cols,
                    angle, trail, limit_r, h, w, colors)
        _draw_blips(stdscr, cy, cx, r_rows, r_cols,
                    blips, limit_r, h, w, colors)

        # Pulsing status below circle
        phase  = int(time.monotonic() * 3) % 3
        dots   = "." * (phase + 1)
        status = f"  SCANNING{dots:<3}"
        srow   = cy + r_rows + 1
        scol   = max(0, cx - len(status) // 2)
        _safe_str(stdscr, srow, scol, status, colors["dim"], h, w)

        # Advance sweep angle and detect blips
        prev    = angle
        angle  += SWEEP_SPEED
        cache["_rdr_angle"] = angle
        cache["_rdr_trail"] = (trail + [prev])[-TRAIL_LEN:]
        _detect_blips(blips, prev, angle)

    elif state == _LOCK:
        # Sweep frozen — show crosshairs + lock text
        _draw_blips(stdscr, cy, cx, r_rows, r_cols,
                    blips, limit_r, h, w, colors)
        _draw_crosshairs(stdscr, cy, cx, r_rows, r_cols,
                         limit_r, h, w, colors)
        _draw_lock_text(stdscr, cy, cx, r_rows, r_cols,
                        limit_r, label, h, w, colors)

        elapsed = time.monotonic() - cache["_rdr_lock_time"]
        if elapsed >= LOCK_HOLD:
            cache["_rdr_state"]     = _DISSOLVE
            cache["_rdr_dissolve_r"] = float(r_rows)

    elif state == _DISSOLVE:
        # Radar contracts inward; only draw what's within limit_r
        _draw_blips(stdscr, cy, cx, r_rows, r_cols,
                    blips, limit_r, h, w, colors)
        _draw_crosshairs(stdscr, cy, cx, r_rows, r_cols,
                         limit_r, h, w, colors)
        _draw_lock_text(stdscr, cy, cx, r_rows, r_cols,
                        limit_r, label, h, w, colors)

        cache["_rdr_dissolve_r"] = limit_r - DISSOLVE_RATE
        if cache["_rdr_dissolve_r"] <= 0:
            return True   # ← dissolve complete; caller switches to chart

    return False
