"""
commands/cmd_div.py
-------------------
Implements the  DIV <TICKER>  command (Dividend History).

Displays up to 10 years of dividend history for the requested ticker.

Usage:
  DIV AAPL
  DIV KO
"""

import curses

import market_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FREQ_LABELS = {
    1: "Annual",
    2: "Semi-Annual",
    4: "Quarterly",
    12: "Monthly",
}


def _freq_label(freq) -> str:
    try:
        return _FREQ_LABELS.get(int(freq), f"{freq}x/yr")
    except (TypeError, ValueError):
        return "N/A"


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {"data": None, "error": "Usage: DIV <TICKER>   e.g. DIV AAPL"}

    try:
        raw = market_data.server_get(f"/api/dividends/{ticker.upper()}")
        if not raw or not isinstance(raw, list):
            return {"data": None, "error": f"No dividend history found for '{ticker}'."}

        # Sort newest first
        records = sorted(raw, key=lambda x: x.get("date", ""), reverse=True)

        # Derive trailing-twelve-month yield if possible
        currency = records[0].get("currency", "USD") if records else "USD"
        return {"data": {"symbol": ticker.upper(), "records": records,
                         "currency": currency}, "error": None}
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

    symbol  = d["symbol"]
    records = d["records"]
    ccy     = d["currency"]
    sep     = f"  {'─' * 60}"

    r = 4
    put(r,     0, sep,                                         colors["dim"])
    put(r + 1, 0, f"   {symbol}",                             colors["orange"], bold=True)
    put(r + 1, 8, "  Dividend History",                       colors["orange"], bold=True)
    put(r + 2, 0, f"   {len(records)} payments  |  {ccy}",   colors["dim"])
    put(r + 3, 0, sep,                                         colors["dim"])

    if not records:
        put(r + 4, 0, "  No dividend history on record.", colors["dim"])
        return

    # Column layout
    C_DATE    = 2
    C_EX      = 16
    C_PAY     = 30
    C_AMOUNT  = 44
    C_FREQ    = 56

    put(r + 4, C_DATE,   f"{'DECL DATE':<13}", colors["dim"])
    put(r + 4, C_EX,     f"{'EX-DATE':<13}",   colors["dim"])
    put(r + 4, C_PAY,    f"{'PAY DATE':<13}",   colors["dim"])
    put(r + 4, C_AMOUNT, f"{'AMOUNT':<11}",     colors["dim"])
    put(r + 4, C_FREQ,   "FREQ",                colors["dim"])
    put(r + 5, 0, sep,                           colors["dim"])

    row = r + 6
    for rec in records:
        date   = rec.get("date",    "N/A")
        ex     = rec.get("exDate",  "N/A")
        pay    = rec.get("payDate", "N/A")
        freq   = _freq_label(rec.get("freq"))
        try:
            amt = f"${float(rec.get('amount', 0)):.4f}"
        except (TypeError, ValueError):
            amt = "N/A"

        put(row, C_DATE,   f"{date:<13}", colors["orange"])
        put(row, C_EX,     f"{ex:<13}",   colors["dim"])
        put(row, C_PAY,    f"{pay:<13}",  colors["dim"])
        put(row, C_AMOUNT, f"{amt:<11}",  colors["positive"])
        put(row, C_FREQ,   freq,          colors["orange"])
        row += 1

    put(row, 0, sep, colors["dim"])
