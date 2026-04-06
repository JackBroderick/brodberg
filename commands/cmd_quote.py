"""
commands/cmd_quote.py
---------------------
Implements the  Q <TICKER>  command.

  fetch(parts)               -> cache dict
  render(stdscr, cache, colors) -> None

Usage:
  Q AAPL
  Q TSLA
"""

import curses
import market_data


# ---------------------------------------------------------------------------
# fetch — called once on Enter, result stored as cache by main.py
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    """
    parts = ["Q", "AAPL"]  (already upper-cased by the registry)
    Returns a cache dict with keys "data" and "error".
    """
    ticker = parts[1] if len(parts) > 1 else None
    data, error = market_data.fetch_quote_data(ticker)
    return {"data": data, "error": error}


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    """
    Render a previously fetched quote starting at row 4.
    Rows 0-3 are reserved for header / benchmark / news / breathing room.
    """
    error = cache.get("error")
    if error:
        try:
            stdscr.attron(colors["negative"])
            stdscr.addstr(4, 0, f"  Error fetching data: {error}")
            stdscr.attroff(colors["negative"])
        except Exception:
            pass
        return

    q = cache.get("data")
    if not q:
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(4, 0, "  Loading...")
            stdscr.attroff(colors["dim"])
        except Exception:
            pass
        return

    try:
        change_val   = float(q["change"])
        sign         = "+" if change_val >= 0 else ""
        change_color = colors["positive"] if change_val >= 0 else colors["negative"]
    except ValueError:
        sign         = ""
        change_color = colors["orange"]

    separator = f"  {'─' * 40}"

    def put(row, text, color):
        try:
            stdscr.attron(color)
            stdscr.addstr(row, 0, text)
            stdscr.attroff(color)
        except Exception:
            pass

    r = 4
    put(r,      separator,                                              colors["dim"])
    put(r + 1,  f"   {q['symbol']}   ${q['price']}",                  colors["orange"] | curses.A_BOLD)
    put(r + 2,  f"   {sign}{q['change']}  ({sign}{q['change_pct']})", change_color | curses.A_BOLD)
    put(r + 3,  separator,                                              colors["dim"])
    put(r + 4,  "",                                                     colors["orange"])
    put(r + 5,  f"   Open      : ${q['open']}",                        colors["orange"])
    put(r + 6,  f"   High      : ${q['high']}",                        colors["orange"])
    put(r + 7,  f"   Low       : ${q['low']}",                         colors["orange"])
    put(r + 8,  f"   Prev Close: ${q['prev_close']}",                  colors["orange"])
    put(r + 9,  f"   Volume    : {q['volume']}",                       colors["orange"])
    put(r + 10, "",                                                     colors["orange"])
    put(r + 11, f"   As of     : {q['latest_trading']}",               colors["dim"])
    put(r + 12, separator,                                              colors["dim"])
