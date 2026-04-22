"""
commands/cmd_gip.py
-------------------
GIP <TICKER> [TIMEFRAME]  --  Graph In Period

Fetch runs in a background thread so the UI never freezes.
A plain status line is shown while data loads; the chart renders
immediately once the fetch completes.

Usage:
  GIP AAPL          <- defaults to 1Y
  GIP AAPL 1W
  GIP AAPL 1M
  GIP AAPL 3M
  GIP AAPL 1Y
  GIP AAPL YTD

Arrow keys (pane mode):
  <- / ->   cycle timeframes
"""

import threading
import curses

import chart
import market_data


TIMEFRAME_CYCLE = ["1W", "1M", "3M", "1Y", "YTD", "ALL"]


# ---------------------------------------------------------------------------
# Internal: start background fetch, return initial cache
# ---------------------------------------------------------------------------

def _start_fetch(ticker: str, timeframe: str) -> dict:
    result = {"data": None, "error": None, "done": False}
    cache  = {
        "data":          None,
        "error":         None,
        "ticker":        ticker,
        "timeframe":     timeframe,
        "loading":       True,
        "_fetch_result": result,
    }

    def _worker():
        data, error       = market_data.fetch_gip_data(ticker, timeframe)
        result["data"]    = data
        result["error"]   = error
        result["done"]    = True

    threading.Thread(target=_worker, daemon=True).start()
    return cache


# ---------------------------------------------------------------------------
# fetch  -- returns immediately
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker    = parts[1] if len(parts) > 1 else None
    timeframe = parts[2] if len(parts) > 2 else market_data.DEFAULT_TIMEFRAME
    if timeframe:
        timeframe = timeframe.upper()
    return _start_fetch(ticker, timeframe)


# ---------------------------------------------------------------------------
# on_keypress  -- timeframe cycling, also non-blocking
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
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
# render  -- called every frame
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    _, width = stdscr.getmaxyx()

    timeframe = cache.get("timeframe", market_data.DEFAULT_TIMEFRAME)
    _render_tf_bar(stdscr, 4, timeframe, colors, width)

    if cache.get("loading"):
        result = cache.get("_fetch_result", {})
        if result.get("done"):
            cache["data"]    = result.get("data")
            cache["error"]   = result.get("error")
            cache["loading"] = False
        else:
            ticker = cache.get("ticker") or ""
            try:
                stdscr.attron(colors["dim"])
                stdscr.addstr(6, 2, f"Fetching {ticker}...")
                stdscr.attroff(colors["dim"])
            except Exception:
                pass
            return

    chart.render_gip(stdscr, cache, colors)


# ---------------------------------------------------------------------------
# Tab bar
# ---------------------------------------------------------------------------

def _render_tf_bar(stdscr, row: int, active: str, colors: dict, width: int):
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
