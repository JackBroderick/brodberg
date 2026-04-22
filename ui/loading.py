"""
ui/loading.py
-------------
Brian's Brain cellular automaton — reusable loading screen for Brodberg.

Brian's Brain rules (3-state automaton):
  FIRING     (1) → REFRACTORY (2)  unconditionally
  REFRACTORY (2) → OFF        (0)  unconditionally
  OFF        (0) → FIRING     (1)  iff exactly 2 of 8 neighbors are FIRING

Visual:
  FIRING     → ●  bright orange (bold)
  REFRACTORY → ·  dim
  OFF        → (space / background)

Crush animation:
  When data arrives, call start_crush(cache).  The automaton grid freezes
  and its rows are erased top-down at _CRUSH_SPEED rows/frame, compressing
  the chaos downward until the pane is blank.  render_loading() returns True
  the frame the crush completes — caller switches to chart rendering.

Usage (inside a command's render() function):
    from ui.loading import render_loading, start_crush

    if cache.get("loading"):
        if fetch_is_done(cache):
            start_crush(cache)
        done = render_loading(stdscr, cache, colors,
                              start_row=5, label="FETCHING AAPL")
        if done:
            cache["loading"] = False
            cache["data"]    = ...
        return
"""

import random
import time
import curses

# ── Cell states ────────────────────────────────────────────────────────────
_OFF  = 0   # dead
_FIRE = 1   # firing    (→ REFRACTORY next step)
_REFR = 2   # refractory (→ OFF next step)

_FIRE_CHAR = "●"
_REFR_CHAR = "·"

# Rows cleared per frame once crush begins.
# 3 = snappy; reduce to 1-2 for a slower, more dramatic collapse.
_CRUSH_SPEED = 3


# ---------------------------------------------------------------------------
# Grid engine
# ---------------------------------------------------------------------------

def _init_grid(rows: int, cols: int, density: float = 0.30) -> list:
    """
    Return a randomly seeded Brian's Brain grid (list of row-lists).
    `density` controls what fraction of cells start alive (FIRE or REFR).
    """
    half = density * 0.45       # slightly more REFR than FIRE at start
    grid = []
    for _ in range(rows):
        row = []
        r_val = random.random   # local ref — tiny speed-up in inner loop
        for _ in range(cols):
            v = r_val()
            if v < half:
                row.append(_FIRE)
            elif v < density:
                row.append(_REFR)
            else:
                row.append(_OFF)
        grid.append(row)
    return grid


def _step(grid: list, rows: int, cols: int) -> list:
    """
    Advance Brian's Brain one generation with toroidal (wrapping) edges.
    Optimised: pre-fetch row references and avoid repeated modulo in hot path.
    """
    new = [[_OFF] * cols for _ in range(rows)]
    for r in range(rows):
        ra      = (r - 1) % rows
        rb      = (r + 1) % rows
        row_a   = grid[ra]
        row_c   = grid[r]
        row_b   = grid[rb]
        new_row = new[r]
        for c in range(cols):
            state = row_c[c]
            if state == _FIRE:
                new_row[c] = _REFR
            elif state == _REFR:
                pass            # already _OFF
            else:
                ca = (c - 1) % cols
                cb = (c + 1) % cols
                n = (
                    (row_a[ca] == _FIRE) +
                    (row_a[c]  == _FIRE) +
                    (row_a[cb] == _FIRE) +
                    (row_c[ca] == _FIRE) +
                    (row_c[cb] == _FIRE) +
                    (row_b[ca] == _FIRE) +
                    (row_b[c]  == _FIRE) +
                    (row_b[cb] == _FIRE)
                )
                if n == 2:
                    new_row[c] = _FIRE
    return new


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_crush(cache: dict) -> None:
    """
    Signal that background data has arrived — begin the crush animation.
    Safe to call multiple times; only arms the crush on the first call.
    """
    if cache.get("_bb_crush_frame", -1) < 0:
        cache["_bb_crush_frame"] = 0


def render_loading(stdscr, cache: dict, colors: dict,
                   start_row: int = 5,
                   label: str = "FETCHING DATA") -> bool:
    """
    Draw Brian's Brain in the pane and advance its state each frame.

    Parameters
    ----------
    stdscr    : curses subwindow (the `stdscr` received by render())
    cache     : mutable pane cache — automaton state lives in _bb_* keys
    colors    : color dict from init_colors()
    start_row : first subwindow row used by the automaton
    label     : text to pulse at the very bottom of the pane

    Returns
    -------
    True  — crush animation just completed; caller should flip to content.
    False — still loading or still crushing.
    """
    win_h, win_w = stdscr.getmaxyx()

    # One row at the bottom is reserved for the pulsing label.
    rows = max(2, win_h - start_row - 1)
    cols = max(4, win_w - 1)

    # ── (Re-)initialise when dimensions change ─────────────────────────────
    if (cache.get("_bb_grid") is None
            or cache.get("_bb_rows") != rows
            or cache.get("_bb_cols") != cols):
        cache["_bb_grid"]        = _init_grid(rows, cols)
        cache["_bb_rows"]        = rows
        cache["_bb_cols"]        = cols
        if "_bb_crush_frame" not in cache:
            cache["_bb_crush_frame"] = -1

    grid      = cache["_bb_grid"]
    crush     = cache["_bb_crush_frame"]
    fire_attr = colors["orange"] | curses.A_BOLD
    refr_attr = colors["dim"]

    # ── Draw automaton rows ────────────────────────────────────────────────
    for r in range(rows):
        scr_row = start_row + r
        if scr_row >= win_h - 1:
            break

        if crush >= 0 and r < crush:
            # This row has been crushed — blank it out
            try:
                stdscr.addstr(scr_row, 0, " " * min(cols, win_w - 1))
            except Exception:
                pass
            continue

        row_data = grid[r]
        limit    = min(cols, win_w - 1)
        for c in range(limit):
            state = row_data[c]
            if state == _FIRE:
                try:
                    stdscr.addch(scr_row, c, _FIRE_CHAR, fire_attr)
                except Exception:
                    pass
            elif state == _REFR:
                try:
                    stdscr.addch(scr_row, c, _REFR_CHAR, refr_attr)
                except Exception:
                    pass
            # OFF cells → leave background (blank)

    # ── Pulsing label at the bottom ────────────────────────────────────────
    label_row = win_h - 1
    if 0 <= label_row < win_h:
        phase = int(time.monotonic() * 4) % 4          # 0-3, cycles ~4×/sec
        dots  = "." * (phase % 3 + 1)                  # 1, 2, or 3 dots
        text  = f"  {label}{dots:<3}"
        lattr = (colors["orange"] | curses.A_BOLD) if phase < 2 else colors["dim"]
        try:
            stdscr.addstr(label_row, 0, text[: win_w - 1], lattr)
        except Exception:
            pass

    # ── Step the automaton (freeze once crush begins) ──────────────────────
    if crush < 0:
        cache["_bb_grid"] = _step(grid, rows, cols)

    # ── Advance crush ──────────────────────────────────────────────────────
    if crush >= 0:
        cache["_bb_crush_frame"] = crush + _CRUSH_SPEED
        if cache["_bb_crush_frame"] >= rows:
            return True     # crush complete — caller should render content

    return False
