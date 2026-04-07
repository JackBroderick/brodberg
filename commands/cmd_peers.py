"""
commands/cmd_peers.py
---------------------
Implements the  PEERS <TICKER>  command.

Displays a list of peer/competitor companies for the requested ticker,
sourced from Finnhub's peer-group endpoint.

Usage:
  PEERS AAPL
  PEERS TSLA
"""

import curses

import market_data


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {"data": None, "error": "Usage: PEERS <TICKER>   e.g. PEERS AAPL"}

    try:
        raw = market_data.server_get(f"/api/peers/{ticker.upper()}")
        if not raw or not isinstance(raw, list):
            return {"data": None, "error": f"No peer data found for '{ticker}'."}
        return {"data": {"symbol": ticker.upper(), "peers": raw}, "error": None}
    except Exception as e:
        return {"data": None, "error": str(e)}


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    error = cache.get("error")
    if error:
        try:
            stdscr.attron(colors["negative"])
            stdscr.addstr(4, 0, f"  Error: {error}")
            stdscr.attroff(colors["negative"])
        except Exception:
            pass
        return

    d = cache.get("data")
    if not d:
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(4, 0, "  Loading...")
            stdscr.attroff(colors["dim"])
        except Exception:
            pass
        return

    def put(row, col, text, color, bold=False):
        attr = color | (curses.A_BOLD if bold else 0)
        try:
            stdscr.attron(attr)
            stdscr.addstr(row, col, text)
            stdscr.attroff(attr)
        except Exception:
            pass

    symbol = d["symbol"]
    peers  = d["peers"]
    sep    = f"  {'─' * 50}"

    r = 4
    put(r,     0, sep,                                    colors["dim"])
    put(r + 1, 0, f"   {symbol}",                        colors["orange"], bold=True)
    put(r + 1, 8, "  Peer Companies",                    colors["header"], bold=True)
    put(r + 2, 0, f"   {len(peers)} peers identified",   colors["dim"])
    put(r + 3, 0, sep,                                    colors["dim"])

    if not peers:
        put(r + 4, 0, "  No peers found.", colors["dim"])
        return

    # Render peers in a 4-column grid
    COL_W   = 12
    COLS    = 4
    COL_X   = [2, 2 + COL_W, 2 + COL_W * 2, 2 + COL_W * 3]
    row     = r + 4
    col_idx = 0

    for i, peer in enumerate(peers):
        color = colors["orange"] if peer == symbol else colors["header"]
        put(row, COL_X[col_idx], f"{peer:<{COL_W - 1}}", color)
        col_idx += 1
        if col_idx >= COLS:
            col_idx = 0
            row += 1

    row += (1 if col_idx == 0 else 2)
    put(row, 0, sep, colors["dim"])
