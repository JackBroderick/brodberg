"""
commands/cmd_exec.py
--------------------
Implements the  EXEC <TICKER>  command (Company Executives).

Displays the executive team and board members for the requested ticker,
including title, age, and total compensation.

Usage:
  EXEC AAPL
  EXEC MSFT
"""

import curses

import market_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_comp(raw) -> str:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return "N/A"
    if val <= 0:
        return "N/A"
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:.0f}K"
    return f"${val:,.0f}"


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {"data": None, "error": "Usage: EXEC <TICKER>   e.g. EXEC AAPL"}

    try:
        raw  = market_data.server_get(f"/api/executives/{ticker.upper()}")
        execs = raw.get("executive", []) if isinstance(raw, dict) else []
        if not execs:
            return {"data": None, "error": f"No executive data found for '{ticker}'."}
        return {"data": {"symbol": ticker.upper(), "executives": execs}, "error": None}
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
    execs  = d["executives"]
    sep    = f"  {'─' * 70}"

    r = 4
    put(r,     0, sep,                                           colors["dim"])
    put(r + 1, 0, f"   {symbol}",                               colors["orange"], bold=True)
    put(r + 1, 8, "  Company Executives",                       colors["header"], bold=True)
    put(r + 2, 0, f"   {len(execs)} executives on record",      colors["dim"])
    put(r + 3, 0, sep,                                           colors["dim"])

    # Column headers
    C_NAME  = 2
    C_TITLE = 30
    C_AGE   = 58
    C_COMP  = 64

    put(r + 4, C_NAME,  f"{'NAME':<27}", colors["dim"])
    put(r + 4, C_TITLE, f"{'TITLE':<27}", colors["dim"])
    put(r + 4, C_AGE,   f"{'AGE':<5}", colors["dim"])
    put(r + 4, C_COMP,  "COMPENSATION", colors["dim"])
    put(r + 5, 0, sep, colors["dim"])

    row = r + 6
    for ex in execs:
        name  = (ex.get("name") or "")[:26]
        title = (ex.get("title") or "")[:26]
        age   = str(ex.get("age") or "")
        comp  = _fmt_comp(ex.get("totalPay") or ex.get("compensation"))

        put(row, C_NAME,  f"{name:<27}", colors["header"])
        put(row, C_TITLE, f"{title:<27}", colors["orange"])
        put(row, C_AGE,   f"{age:<5}",   colors["dim"])
        put(row, C_COMP,  comp,           colors["positive"] if comp != "N/A" else colors["dim"])
        row += 1

    put(row, 0, sep, colors["dim"])
