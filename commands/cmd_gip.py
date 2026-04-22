"""
commands/cmd_gip.py
-------------------
Implements the  GIP <TICKER> [TIMEFRAME]  command (Graph In Period).

  fetch(parts)                  -> cache dict   (returns immediately)
  render(stdscr, cache, colors) -> None         (state machine)
  on_keypress(key, cache)       -> cache dict   (arrow key navigation)

Fetch runs in a background thread — the UI never freezes.
While data loads, a Brian's Brain cellular automaton fills the pane.
When the fetch completes, the automaton is crushed flat (rows clear top-down)
before the price chart slides in.

Usage:
  GIP AAPL          <- defaults to 1Y
  GIP AAPL 1W       <- 1 week
  GIP AAPL 1M       <- 1 month
  GIP AAPL 3M       <- 3 months
  GIP AAPL 1Y       <- 1 year
  GIP AAPL YTD      <- year to date

Arrow key navigation (when GIP is the active command, in pane mode):
  ← / →   cycle through timeframes: 1W → 1M → 3M → 1Y → YTD
"""

import threading
import curses

import chart
import market_data
from ui.loading import render_loading, start_crush


# Ordered cycle of timeframes for ← / → navigation.
TIMEFRAME_CYCLE = ["1W", "1M", "3M", "1Y", "YTD"]

# First subwindow row available for the automaton (rows 0-4 are tab bar / chrome).
_CONTENT_START = 5


# ---------------------------------------------------------------------------
# Internal: kick off a background fetch and return the initial cache
# ---------------------------------------------------------------------------

def _start_fetch(ticker: str, timeframe: str) -> dict:
    """
    Return an initial loading cache and immediately spawn a daemon thread
    that writes its result into cache["_fetch_result"] when done.
    """
    result = {"data": None, "error": None, "done": False}
    cache  = {
        # Public keys (used by render / chart)
        "data":            None,
        "error":           None,
        "ticker":          ticker,
        "timeframe":       timeframe,
        "loading":         True,
        # Shared result dict — worker thread writes here
        "_fetch_result":   result,
        # Brian's Brain state — initialised on first render
        "_bb_grid":        None,
        "_bb_crush_frame": -1,
    }

    def _worker():
        data, error       = market_data.fetch_gip_data(ticker, timeframe)
        result["data"]    = data
        result["error"]   = error
        result["done"]    = True   # ← render() polls this

    threading.Thread(target=_worker, daemon=True).start()
    return cache


# ---------------------------------------------------------------------------
# fetch  — called once on Enter; returns immediately
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    """
    parts = ["GIP", "AAPL"]        → uses DEFAULT_TIMEFRAME
    parts = ["GIP", "AAPL", "3M"]  → uses specified timeframe

    Already upper-cased by the registry.
    """
    ticker    = parts[1] if len(parts) > 1 else None
    timeframe = parts[2] if len(parts) > 2 else market_data.DEFAULT_TIMEFRAME
    if timeframe:
        timeframe = timeframe.upper()
    return _start_fetch(ticker, timeframe)


# ---------------------------------------------------------------------------
# on_keypress  — timeframe cycling via ← / →  (also non-blocking)
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    """
    ← / →  step backward / forward through TIMEFRAME_CYCLE and re-fetch.

    Ignored while a fetch is already in flight.
    Returns a fresh cache dict (new background fetch started).
    """
    # Don't queue a second fetch while one is running
    if cache.get("loading"):
        return cache

    ticker    = cache.get("ticker") or (cache.get("data") or {}).get("symbol")
    timeframe = cache.get("timeframe", market_data.DEFAULT_TIMEFRAME)

    if not ticker:
        return cache

    if timeframe not in TIMEFRAME_CYCLE:
        timeframe = market_data.DEFAULT_TIMEFRAME
    if timeframe not in TIMEFRAME_CYCLE:
        timeframe = TIMEFRAME_CYCLE[0]

    idx = TIMEFRAME_CYCLE.index(timeframe)

    if key == curses.KEY_RIGHT:
        new_tf = TIMEFRAME_CYCLE[(idx + 1) % len(TIMEFRAME_CYCLE)]
    elif key == curses.KEY_LEFT:
        new_tf = TIMEFRAME_CYCLE[(idx - 1) % len(TIMEFRAME_CYCLE)]
    else:
        return cache

    return _start_fetch(ticker, new_tf)


# ---------------------------------------------------------------------------
# render  — called every frame; state machine: loading → crush → chart
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    """
    Three states drive the display:

    1. loading=True, crush_frame=-1  →  automaton running, data still in flight
    2. loading=True, crush_frame≥0   →  data arrived, crush animation in progress
    3. loading=False                 →  chart rendered normally
    """
    win_h, win_w = stdscr.getmaxyx()

    # ── Timeframe tab bar (always shown) ──────────────────────────────────
    timeframe = cache.get("timeframe", market_data.DEFAULT_TIMEFRAME)
    _render_tf_bar(stdscr, 4, timeframe, colors, win_w)

    # ── Loading / crush phase ──────────────────────────────────────────────
    if cache.get("loading"):
        result = cache.get("_fetch_result", {})

        # Data just landed — arm the crush (only fires once)
        if result.get("done") and cache.get("_bb_crush_frame", -1) < 0:
            start_crush(cache)

        ticker     = cache.get("ticker") or ""
        crush_done = render_loading(
            stdscr, cache, colors,
            start_row = _CONTENT_START,
            label     = f"FETCHING  {ticker}",
        )

        if crush_done:
            # Promote fetched result → normal cache keys
            cache["data"]    = result.get("data")
            cache["error"]   = result.get("error")
            cache["loading"] = False
        return

    # ── Chart ─────────────────────────────────────────────────────────────
    chart.render_gip(stdscr, cache, colors)


# ---------------------------------------------------------------------------
# Tab bar
# ---------------------------------------------------------------------------

def _render_tf_bar(stdscr, row: int, active: str, colors: dict, width: int):
    """
    Draw the timeframe selector.  Active timeframe is highlighted orange;
    others are dim.
    """
    def _put(r, c, text, color, bold=False):
        attr = color | (curses.A_BOLD if bold else 0)
        try:
            stdscr.attron(attr)
            stdscr.addstr(r, c, text)
            stdscr.attroff(attr)
        except Exception:
            pass

    col = 2
    for tf in TIMEFRAME_CYCLE:
        if tf == active:
            label = f"[ {tf} ]"
            _put(row, col, label, colors["orange"], bold=True)
        else:
            label = f"  {tf}  "
            _put(row, col, label, colors["dim"])
        col += len(label)
