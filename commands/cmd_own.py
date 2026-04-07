"""
commands/cmd_own.py
-------------------
Implements the  OWN <TICKER>  command (Insider / Ownership Transactions).

Displays recent insider transactions (purchases and sales) filed with the SEC
for the requested ticker.

Usage:
  OWN AAPL
  OWN NVDA
"""

import curses

import market_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRANSACTION_CODES = {
    "P": "Purchase",
    "S": "Sale",
    "S-Sale": "Sale",
    "A": "Award",
    "D": "Disposition",
    "F": "Tax Withhold",
    "M": "Exercise",
    "G": "Gift",
    "I": "Discretionary",
    "L": "Small Acq.",
    "U": "Tender",
    "W": "Will/Inherit",
    "X": "Option Exp.",
    "Z": "Trust",
}


def _txn_label(code: str) -> str:
    return _TRANSACTION_CODES.get(str(code).strip(), code or "N/A")


def _fmt_shares(val) -> str:
    try:
        n = int(val)
        return f"{n:,}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_price(val) -> str:
    try:
        p = float(val)
        if p <= 0:
            return "N/A"
        return f"${p:,.2f}"
    except (TypeError, ValueError):
        return "N/A"


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {"data": None, "error": "Usage: OWN <TICKER>   e.g. OWN AAPL"}

    try:
        raw  = market_data.server_get(f"/api/insider/{ticker.upper()}")
        txns = raw.get("data", []) if isinstance(raw, dict) else []
        if not txns:
            return {"data": None, "error": f"No insider transactions found for '{ticker}'."}

        # Sort by transaction date, newest first
        txns = sorted(txns, key=lambda x: x.get("transactionDate", ""), reverse=True)
        return {"data": {"symbol": ticker.upper(), "transactions": txns}, "error": None}
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
    txns   = d["transactions"]
    sep    = f"  {'─' * 72}"

    r = 4
    put(r,     0, sep,                                              colors["dim"])
    put(r + 1, 0, f"   {symbol}",                                  colors["orange"], bold=True)
    put(r + 1, 8, "  Insider Transactions",                        colors["header"], bold=True)
    put(r + 2, 0, f"   {len(txns)} transactions on record",        colors["dim"])
    put(r + 3, 0, sep,                                              colors["dim"])

    # Column layout
    C_DATE   = 2
    C_NAME   = 14
    C_TYPE   = 36
    C_SHARES = 48
    C_PRICE  = 60

    put(r + 4, C_DATE,   f"{'DATE':<11}",      colors["dim"])
    put(r + 4, C_NAME,   f"{'INSIDER':<21}",   colors["dim"])
    put(r + 4, C_TYPE,   f"{'TYPE':<11}",      colors["dim"])
    put(r + 4, C_SHARES, f"{'SHARES':<11}",    colors["dim"])
    put(r + 4, C_PRICE,  "PRICE",              colors["dim"])
    put(r + 5, 0, sep,                          colors["dim"])

    row = r + 6
    for txn in txns:
        date   = txn.get("transactionDate", txn.get("filingDate", "N/A"))
        name   = (txn.get("name") or "")[:20]
        code   = txn.get("transactionCode", "")
        label  = _txn_label(code)[:10]
        shares = _fmt_shares(abs(txn.get("change", 0) or txn.get("share", 0)))
        price  = _fmt_price(txn.get("transactionPrice"))

        is_buy = str(code).strip().upper() == "P"
        txn_color = colors["positive"] if is_buy else colors["negative"]

        put(row, C_DATE,   f"{date:<11}",   colors["header"])
        put(row, C_NAME,   f"{name:<21}",   colors["dim"])
        put(row, C_TYPE,   f"{label:<11}",  txn_color)
        put(row, C_SHARES, f"{shares:<11}", colors["orange"])
        put(row, C_PRICE,  price,           colors["header"])
        row += 1

    put(row, 0, sep, colors["dim"])
