"""
commands/cmd_gip.py
-------------------
Implements the  GIP <TICKER> [TIMEFRAME]  command (Graph In Period).

  fetch(parts)                  -> cache dict
  render(stdscr, cache, colors) -> None
  on_keypress(key, cache)       -> cache dict   ← arrow key navigation

Displays a block-character price chart for the requested ticker and timeframe.

Usage:
  GIP AAPL          <- defaults to 1Y
  GIP AAPL 1W       <- 1 week
  GIP AAPL 1M       <- 1 month
  GIP AAPL 3M       <- 3 months
  GIP AAPL 1Y       <- 1 year
  GIP AAPL YTD      <- year to date

Arrow key navigation (when GIP is the active command):
  ← / →   cycle through timeframes: 1W → 1M → 3M → 1Y → YTD
           (triggers a re-fetch for the new period)

Supported timeframes are defined in market_data.TIMEFRAME_MAP.
"""

import curses
import chart
import market_data


# Ordered cycle of timeframes for ← / → navigation.
# Must be valid keys in market_data.TIMEFRAME_MAP.
TIMEFRAME_CYCLE = ["RT", "1W", "1M", "3M", "1Y", "YTD"]


# ---------------------------------------------------------------------------
# fetch — called once on Enter (and again by on_keypress on timeframe change)
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    """
    parts = ["GIP", "AAPL"]        — uses default timeframe
    parts = ["GIP", "AAPL", "3M"]  — uses specified timeframe

    Already upper-cased by the registry.
    Returns a cache dict with keys "data" and "error".
    """
    ticker    = parts[1] if len(parts) > 1 else None
    timeframe = parts[2] if len(parts) > 2 else market_data.DEFAULT_TIMEFRAME

    data, error = market_data.fetch_gip_data(ticker, timeframe)
    cache = {"data": data, "error": error, "ticker": ticker, "timeframe": timeframe}
    if timeframe == "RT" and data and not error:
        market_data.start_rt_refresh(cache)
    return cache


# ---------------------------------------------------------------------------
# on_keypress — timeframe cycling via ← / →
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    """
    ← / →  step backward / forward through TIMEFRAME_CYCLE and re-fetch.

    Returns the new cache dict (re-fetched for the new timeframe).
    """
    ticker    = cache.get("ticker") or (cache.get("data") or {}).get("ticker")
    timeframe = cache.get("timeframe", market_data.DEFAULT_TIMEFRAME)

    if not ticker:
        return cache

    # Normalise timeframe to something in TIMEFRAME_CYCLE
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

    # Stop any running RT thread before switching timeframe
    market_data.stop_rt_refresh(cache)

    data, error = market_data.fetch_gip_data(ticker, new_tf)
    new_cache = {"data": data, "error": error, "ticker": ticker, "timeframe": new_tf}
    if new_tf == "RT" and data and not error:
        market_data.start_rt_refresh(new_cache)
    return new_cache


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    """
    Render the GIP chart from the previously fetched cache.
    Delegates all drawing to chart.render_gip().
    Adds a timeframe tab bar above the chart for navigation context.
    """
    _, width = stdscr.getmaxyx()

    # ── Timeframe tab bar ─────────────────────────────────────────────────
    timeframe = cache.get("timeframe", market_data.DEFAULT_TIMEFRAME)
    _render_tf_bar(stdscr, 4, timeframe, colors, width)

    # ── Live indicator (RT mode only) ─────────────────────────────────────
    if timeframe == "RT" and cache.get("data"):
        label = " ● LIVE "
        try:
            stdscr.attron(colors["positive"] | curses.A_BOLD)
            stdscr.addstr(4, width - len(label) - 2, label)
            stdscr.attroff(colors["positive"] | curses.A_BOLD)
        except Exception:
            pass

    chart.render_gip(stdscr, cache, colors)


def _render_tf_bar(stdscr, row: int, active: str, colors: dict, width: int):
    """
    Draw the timeframe selector tab bar.
    Active timeframe is highlighted; others are dim.
    Arrow key hint is shown on the right.
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

    hint = ""
    _put(row, col + 2, hint, colors["dim"])