"""
commands/cmd_fa.py
------------------
Implements the  FA <TICKER> [STATEMENT] [ANNUAL]  command (Fundamental Analysis).

  fetch(parts)                  -> cache dict
  render(stdscr, cache, colors) -> None
  on_keypress(key, cache)       -> cache dict   ← arrow key navigation

Displays financial statements for the requested ticker across trailing 4 quarters
(or annual periods if ANNUAL is specified).

Usage:
  FA AAPL           <- Income Statement, quarterly (default)
  FA AAPL BS        <- Balance Sheet
  FA AAPL CF        <- Cash Flow Statement
  FA AAPL IS        <- Income Statement (explicit)
  FA AAPL ANNUAL    <- Income Statement, annual periods
  FA AAPL BS ANNUAL <- Balance Sheet, annual periods
  FA AAPL ANNUAL CF <- argument order doesn't matter

Arrow key navigation (when FA is the active command):
  ← / →   cycle through IS → BS → CF statements  (no re-fetch)
  ↑ / ↓   toggle quarterly ↔ annual              (triggers re-fetch)

Data source: yfinance (primary), Finnhub /financials-reported (fallback).

Statement tabs navigate by retyping the command with a different arg:
  [ IS ]  BS   CF      ← active tab shown in brackets
"""

import curses
import market_data  # reuses API_KEY and BASE_URL

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATEMENTS = {"IS", "BS", "CF"}
DEFAULT_STATEMENT = "IS"
STATEMENT_ORDER   = ["IS", "BS", "CF"]   # cycle order for ← / →

STATEMENT_LABELS = {
    "IS": "Income Statement",
    "BS": "Balance Sheet",
    "CF": "Cash Flow",
}

# Line items to extract per statement — (display_label, yfinance_key)
# Keys must match yfinance DataFrame index labels exactly.
IS_ROWS = [
    ("Revenue",          "Total Revenue"),
    ("Gross Profit",     "Gross Profit"),
    ("Operating Income", "Operating Income"),
    ("EBITDA",           "EBITDA"),
    ("Net Income",       "Net Income"),
    ("EPS (diluted)",    "Diluted EPS"),
]

BS_ROWS = [
    ("Cash & Equiv.",    "Cash And Cash Equivalents"),
    ("Total Assets",     "Total Assets"),
    ("Total Liabilities","Total Liabilities Net Minority Interest"),
    ("Total Equity",     "Stockholders Equity"),
    ("Total Debt",       "Total Debt"),
    ("Book Value/Share", "Book Value"),
]

CF_ROWS = [
    ("Operating CF",     "Operating Cash Flow"),
    ("Capital Expenditure", "Capital Expenditure"),
    ("Free Cash Flow",   "Free Cash Flow"),
    ("Investing CF",     "Investing Cash Flow"),
    ("Financing CF",     "Financing Cash Flow"),
    ("Net Change Cash",  "Changes In Cash"),
]


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def _fmt(val) -> str:
    """
    Format a raw value (usually in dollars, not millions) into abbreviated form.
    yfinance returns raw dollar values.
    Returns e.g. $94.9B, $1.2M, $340.0K, or N/A.
    """
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "N/A"

    neg = v < 0
    v = abs(v)

    if v >= 1_000_000_000:
        s = f"${v / 1_000_000_000:.1f}B"
    elif v >= 1_000_000:
        s = f"${v / 1_000_000:.1f}M"
    elif v >= 1_000:
        s = f"${v / 1_000:.1f}K"
    else:
        s = f"${v:.0f}"

    return f"-{s}" if neg else s


# ---------------------------------------------------------------------------
# yfinance fetch helpers
# ---------------------------------------------------------------------------

def _get_yf_ticker(ticker: str):
    try:
        import yfinance as yf
        return yf.Ticker(ticker.upper())
    except ImportError:
        raise ImportError("yfinance not installed. Run: pip install yfinance")


def _extract_rows(df, row_specs: list) -> tuple[list[str], list[list[str]]]:
    """
    Given a yfinance DataFrame and a list of (label, key) specs,
    return (period_headers, [[values per period] per row]).

    Periods are columns in the DataFrame; we take the 4 most recent.
    Headers are formatted as 'Q2 2025' or '2024' depending on frequency.
    """
    if df is None or df.empty:
        return [], []

    # Most recent 4 columns (left = most recent in yfinance)
    cols = df.columns[:4]

    # Format period headers
    headers = []
    for col in cols:
        try:
            dt = col.to_pydatetime() if hasattr(col, "to_pydatetime") else col
            # Quarterly: show "Q2 2025"; Annual: show "FY2024"
            month = dt.month
            year  = dt.year
            quarter = (month - 1) // 3 + 1
            headers.append(f"Q{quarter} {year}")
        except Exception:
            headers.append(str(col)[:9])

    rows = []
    for (label, key) in row_specs:
        vals = []
        for col in cols:
            try:
                raw = df.loc[key, col] if key in df.index else None
                vals.append(_fmt(raw))
            except Exception:
                vals.append("N/A")
        rows.append(vals)

    return headers, rows


def _fetch_yfinance(ticker: str, annual: bool) -> dict:
    """
    Fetch all three statements from yfinance.
    Returns a dict with keys: is_data, bs_data, cf_data, currency, name.
    Each *_data is {"headers": [...], "rows": [(label, [val, ...]), ...]}.
    Raises on hard failure.
    """
    tk = _get_yf_ticker(ticker)

    if annual:
        inc_df = tk.financials
        bal_df = tk.balance_sheet
        cf_df  = tk.cashflow
    else:
        inc_df = tk.quarterly_financials
        bal_df = tk.quarterly_balance_sheet
        cf_df  = tk.quarterly_cashflow

    info     = tk.info or {}
    name     = info.get("longName") or info.get("shortName") or ticker.upper()
    currency = info.get("financialCurrency", "USD")
    exchange = info.get("exchange", "")

    def build(df, specs):
        headers, row_vals = _extract_rows(df, specs)
        labeled = [(specs[i][0], row_vals[i]) for i in range(len(specs))]
        return {"headers": headers, "rows": labeled}

    return {
        "name":     name,
        "currency": currency,
        "exchange": exchange,
        "is_data":  build(inc_df, IS_ROWS),
        "bs_data":  build(bal_df, BS_ROWS),
        "cf_data":  build(cf_df,  CF_ROWS),
        "source":   "yfinance",
    }


# ---------------------------------------------------------------------------
# Finnhub fallback
# ---------------------------------------------------------------------------

def _fetch_finnhub(ticker: str, annual: bool) -> dict:
    """
    Fallback: pull from Finnhub /financials-reported (XBRL).
    Returns a minimal structure matching the yfinance output shape.
    Only IS is populated from Finnhub free tier; BS/CF left as empty.
    """
    import urllib.request
    import json

    freq = "annual" if annual else "quarterly"
    url  = (
        f"{market_data.BASE_URL}/stock/financials-reported"
        f"?symbol={ticker.upper()}&freq={freq}&token={market_data.API_KEY}"
    )
    with urllib.request.urlopen(url, timeout=8) as resp:
        raw = json.loads(resp.read().decode())

    reports = raw.get("data", [])[:4]  # most recent 4

    if not reports:
        raise ValueError("No Finnhub financial data available.")

    # Build headers from period end dates
    headers = []
    for r in reports:
        dt = r.get("endDate", "")[:7]  # "2025-03"
        headers.append(dt)

    # Map XBRL concept names → row labels for IS
    XBRL_IS = [
        ("Revenue",          "us-gaap_Revenues"),
        ("Gross Profit",     "us-gaap_GrossProfit"),
        ("Operating Income", "us-gaap_OperatingIncomeLoss"),
        ("Net Income",       "us-gaap_NetIncomeLoss"),
    ]

    def _get_xbrl(report, concept):
        for item in report.get("report", {}).get("ic", []):
            if item.get("concept") == concept:
                return item.get("value")
        return None

    is_rows = []
    for (label, concept) in XBRL_IS:
        vals = [_fmt(_get_xbrl(r, concept)) for r in reports]
        is_rows.append((label, vals))

    empty = {"headers": headers, "rows": []}

    return {
        "name":     ticker.upper(),
        "currency": "USD",
        "exchange": "",
        "is_data":  {"headers": headers, "rows": is_rows},
        "bs_data":  empty,
        "cf_data":  empty,
        "source":   "finnhub",
    }


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    """
    parts = ["FA", "AAPL"]
    parts = ["FA", "AAPL", "BS"]
    parts = ["FA", "AAPL", "BS", "ANNUAL"]
    parts = ["FA", "AAPL", "ANNUAL", "CF"]   ← order-independent

    Returns cache dict with keys:
        ticker, statement, annual, data (or None), error (or None)
    """
    ticker    = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {
            "ticker": None, "statement": DEFAULT_STATEMENT,
            "annual": False, "data": None,
            "error": "Usage: FA <TICKER> [IS|BS|CF] [ANNUAL]   e.g. FA AAPL BS",
        }

    # Parse remaining args — order-independent
    statement = DEFAULT_STATEMENT
    annual    = False
    for arg in parts[2:]:
        if arg in VALID_STATEMENTS:
            statement = arg
        elif arg == "ANNUAL":
            annual = True

    # Try yfinance, fall back to Finnhub
    try:
        data = _fetch_yfinance(ticker, annual)
    except Exception as yf_err:
        try:
            data = _fetch_finnhub(ticker, annual)
        except Exception as fh_err:
            return {
                "ticker":    ticker,
                "statement": statement,
                "annual":    annual,
                "data":      None,
                "error":     f"yfinance: {yf_err}  |  finnhub: {fh_err}",
            }

    return {
        "ticker":    ticker,
        "statement": statement,
        "annual":    annual,
        "data":      data,
        "error":     None,
    }


# ---------------------------------------------------------------------------
# on_keypress — arrow key navigation (no re-fetch for tab switch)
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    """
    ← / →  cycle through IS → BS → CF without re-fetching.
    ↑ / ↓  toggle quarterly ↔ annual  (triggers a full re-fetch).

    Returns updated cache dict.
    """
    # Don't navigate if there was a fetch error or no ticker
    if cache.get("error") or not cache.get("ticker"):
        return cache

    current_stmt   = cache.get("statement", DEFAULT_STATEMENT)
    current_annual = cache.get("annual", False)

    if key == curses.KEY_RIGHT:
        idx = STATEMENT_ORDER.index(current_stmt) if current_stmt in STATEMENT_ORDER else 0
        new_stmt = STATEMENT_ORDER[(idx + 1) % len(STATEMENT_ORDER)]
        return {**cache, "statement": new_stmt}

    if key == curses.KEY_LEFT:
        idx = STATEMENT_ORDER.index(current_stmt) if current_stmt in STATEMENT_ORDER else 0
        new_stmt = STATEMENT_ORDER[(idx - 1) % len(STATEMENT_ORDER)]
        return {**cache, "statement": new_stmt}

    if key in (curses.KEY_UP, curses.KEY_DOWN):
        new_annual = not current_annual
        ticker     = cache["ticker"]
        # Re-fetch with the new frequency
        try:
            data = _fetch_yfinance(ticker, new_annual)
        except Exception as yf_err:
            try:
                data = _fetch_finnhub(ticker, new_annual)
            except Exception as fh_err:
                # Keep existing data; show error
                return {**cache, "error": f"yfinance: {yf_err}  |  finnhub: {fh_err}"}
        return {**cache, "annual": new_annual, "data": data, "error": None}

    return cache


# ---------------------------------------------------------------------------
# render helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


def _render_tab_bar(stdscr, row, active: str, colors: dict):
    """
    Draw  [ IS ]  BS   CF  tab bar on `row`.
    Active tab is bracketed; inactive tabs are dim.
    Hints for arrow-key navigation are shown on the right.
    """
    col  = 2
    tabs = STATEMENT_ORDER
    for tab in tabs:
        if tab == active:
            label = f"[ {tab} ]"
            _put(stdscr, row, col, label, colors["orange"], bold=True)
        else:
            label = f"  {tab}  "
            _put(stdscr, row, col, label, colors["dim"])
        col += len(label)

    # Arrow key hint
    _put(stdscr, row, col + 2, "← → switch tab   ↑ ↓ toggle annual/quarterly", colors["dim"])


def _render_statement(stdscr, start_row, data_block: dict,
                      colors: dict, width: int):
    """
    Render a single statement grid.

    Layout:
        row 0 : column headers (period labels)
        rows 1+: label  |  val  val  val  val
    """
    headers = data_block.get("headers", [])
    rows    = data_block.get("rows", [])

    if not headers or not rows:
        _put(stdscr, start_row, 2, "  No data available for this statement.",
             colors["dim"])
        return

    # Column layout
    LABEL_W  = 22    # left label column width
    VAL_W    = 12    # each value column width
    COL_START = 2

    # Header row
    _put(stdscr, start_row, COL_START, " " * LABEL_W, colors["dim"])
    for i, hdr in enumerate(headers):
        col = COL_START + LABEL_W + i * VAL_W
        _put(stdscr, start_row, col, hdr.rjust(VAL_W - 1), colors["dim"], bold=True)

    # Separator
    sep_row = start_row + 1
    _put(stdscr, sep_row, COL_START,
         "─" * (LABEL_W + VAL_W * len(headers)), colors["dim"])

    # Data rows
    for ri, (label, vals) in enumerate(rows):
        r = sep_row + 1 + ri
        # Label
        _put(stdscr, r, COL_START, f"{label:<{LABEL_W}}", colors["dim"])
        # Values — color negative red, positive/zero orange
        for ci, val in enumerate(vals):
            col = COL_START + LABEL_W + ci * VAL_W
            is_neg = val.startswith("-")
            color  = colors["negative"] if is_neg else colors["orange"]
            _put(stdscr, r, col, val.rjust(VAL_W - 1), color)


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    """
    Render the FA panel starting at row 4.
    Rows 0–3 are reserved chrome.
    """
    _, width = stdscr.getmaxyx()

    # ── Error state ───────────────────────────────────────────────────────
    error = cache.get("error")
    if error:
        _put(stdscr, 4, 0, f"  Error: {error}", colors["negative"])
        return

    data      = cache.get("data")
    statement = cache.get("statement", DEFAULT_STATEMENT)
    annual    = cache.get("annual", False)
    ticker    = cache.get("ticker", "")

    if not data:
        _put(stdscr, 4, 0, "  Loading...", colors["dim"])
        return

    sep = f"  {'─' * 50}"

    # ── Header block ──────────────────────────────────────────────────────
    r = 4
    freq_label = "Annual" if annual else "Quarterly"
    src_label  = f"[{data['source']}]"

    _put(stdscr, r, 0, sep, colors["dim"])
    r += 1

    # Ticker + name
    _put(stdscr, r, 2,  ticker.upper(),           colors["orange"], bold=True)
    _put(stdscr, r, 2 + len(ticker) + 2,
         f"{data['name']}",                        colors["header"],  bold=True)
    r += 1

    # Exchange / currency / frequency / source
    meta = (f"  {data['exchange']}  |  {data['currency']}  |  "
            f"{freq_label}  |  {STATEMENT_LABELS[statement]}  {src_label}")
    _put(stdscr, r, 0, meta, colors["dim"])
    r += 1

    _put(stdscr, r, 0, sep, colors["dim"])
    r += 1

    # ── Tab bar ───────────────────────────────────────────────────────────
    _render_tab_bar(stdscr, r, statement, colors)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"])
    r += 1

    # ── Statement grid ────────────────────────────────────────────────────
    stmt_map = {
        "IS": data.get("is_data", {}),
        "BS": data.get("bs_data", {}),
        "CF": data.get("cf_data", {}),
    }
    _render_statement(stdscr, r, stmt_map[statement], colors, width)

    # Row count for the active statement
    active_rows = stmt_map[statement].get("rows", [])
    r += len(active_rows) + 3    # header + separator + rows

    # ── Footer separator + nav hint ───────────────────────────────────────
    _put(stdscr, r, 0, sep, colors["dim"])
    r += 1
    hint = (f"  Switch:  FA {ticker} IS  |  FA {ticker} BS  |  FA {ticker} CF"
            f"   {'  FA ' + ticker + ' ANNUAL' if not annual else '  FA ' + ticker + ' (quarterly)'}")
    _put(stdscr, r, 0, hint, colors["dim"])