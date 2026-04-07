"""
commands/cmd_rates.py
---------------------
Implements the  RATES  command — U.S. Treasury yield curve.

Data priority:
  1. Finnhub  /bond/yield_curve?code=US  (1 call — full time-series)
  2. yfinance  ^IRX / ^TWO / ^FVX / ^TNX / ^TYX  (5 calls — fallback)

Finnhub response shape:
  {
    "t":     [unix_ts, ...],        # one entry per trading day
    "rates": [                      # parallel list of curve snapshots
      {"1M": 5.23, "2Y": 4.85, "5Y": 4.55, "10Y": 4.40, "30Y": 4.50},
      ...
    ]
  }
We take rates[-1] for today, rates[-21] for the 1-month-ago overlay.
"""

import curses
import datetime

import market_data

# ---------------------------------------------------------------------------
# Curve node definitions
# (display_label, finnhub_key, yfinance_ticker, yfinance_scale)
# ---------------------------------------------------------------------------

CURVE_NODES = [
    ("1M",  "1M",  "^IRX", 0.1),
    ("2Y",  "2Y",  "^TWO", 1.0),
    ("5Y",  "5Y",  "^FVX", 1.0),
    ("10Y", "10Y", "^TNX", 1.0),
    ("30Y", "30Y", "^TYX", 1.0),
]

CHART_HEIGHT  = 10
Y_LABEL_WIDTH = 8


# ---------------------------------------------------------------------------
# Finnhub fetch  (primary — 1 API call)
# ---------------------------------------------------------------------------

def _fetch_finnhub_curve() -> dict:
    raw = market_data.server_get("/api/yield-curve")

    timestamps = raw.get("t", [])
    rates_list = raw.get("rates", [])
    if not timestamps or not rates_list:
        raise ValueError("Empty Finnhub yield curve response")

    def _extract(rates_dict):
        return [
            round(float(rates_dict[fh_key]), 4)
            if rates_dict.get(fh_key) is not None else None
            for (_, fh_key, _, _) in CURVE_NODES
        ]

    today_rates     = _extract(rates_list[-1])
    month_idx       = max(0, len(rates_list) - 21)
    month_ago_rates = _extract(rates_list[month_idx])
    as_of = datetime.datetime.utcfromtimestamp(
        timestamps[-1]).strftime("%Y-%m-%d")

    return {"today": today_rates, "month_ago": month_ago_rates,
            "as_of": as_of, "source": "finnhub"}


# ---------------------------------------------------------------------------
# yfinance fetch  (fallback — 5 calls)
# ---------------------------------------------------------------------------

def _yf_yield(ticker, scale, period="5d"):
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period=period, interval="1d")
        return round(float(h["Close"].iloc[-1]) * scale, 4) if not h.empty else None
    except Exception:
        return None


def _fetch_yfinance_curve() -> dict:
    today, month_ago = [], []
    for (_, _, ticker, scale) in CURVE_NODES:
        today.append(_yf_yield(ticker, scale, "5d"))
        try:
            import yfinance as yf
            h = yf.Ticker(ticker).history(period="40d", interval="1d")
            month_ago.append(
                round(float(h["Close"].iloc[-21]) * scale, 4)
                if len(h) >= 21 else None
            )
        except Exception:
            month_ago.append(None)

    if all(v is None for v in today):
        raise ValueError("yfinance returned no yield data")

    return {"today": today, "month_ago": month_ago,
            "as_of": datetime.date.today().isoformat(), "source": "yfinance"}


# ---------------------------------------------------------------------------
# Spread + status helpers
# ---------------------------------------------------------------------------

def _compute_meta(labels, today):
    idx = {l: i for i, l in enumerate(labels)}

    def _g(l):
        i = idx.get(l)
        return today[i] if i is not None else None

    def _sp(a, b):
        return round(b - a, 4) if a is not None and b is not None else None

    spreads = {"2s10s": _sp(_g("2Y"), _g("10Y")),
               "2s30s": _sp(_g("2Y"), _g("30Y")),
               "5s30s": _sp(_g("5Y"), _g("30Y"))}

    sp = spreads["2s10s"]
    if sp is None:     status = "Unknown"
    elif sp >  0.50:   status = "Normal (steep)"
    elif sp >  0.10:   status = "Normal"
    elif sp >= -0.10:  status = "Flat"
    elif sp >= -0.50:  status = "Inverted"
    else:              status = "Deeply Inverted"

    return spreads, status


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    labels = [n[0] for n in CURVE_NODES]
    try:
        d = _fetch_finnhub_curve()
    except Exception as fh_err:
        try:
            d = _fetch_yfinance_curve()
        except Exception as yf_err:
            return {"error": f"finnhub: {fh_err}  |  yfinance: {yf_err}",
                    "labels": [], "today": [], "month_ago": [],
                    "spreads": {}, "status": "", "as_of": "", "source": ""}

    spreads, status = _compute_meta(labels, d["today"])
    return {"error": None, "labels": labels, "today": d["today"],
            "month_ago": d["month_ago"], "spreads": spreads,
            "status": status, "as_of": d["as_of"], "source": d["source"]}


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr); stdscr.addstr(row, col, text); stdscr.attroff(attr)
    except Exception:
        pass


def _build_curve_lines(yields, chart_cols, chart_rows):
    valid = [(i, v) for i, v in enumerate(yields) if v is not None]
    if len(valid) < 2:
        return [" " * chart_cols] * chart_rows
    n = len(yields)
    y_min = min(v for _, v in valid)
    y_max = max(v for _, v in valid)
    y_rng = y_max - y_min or 0.01

    def interp(col_frac):
        x = col_frac * (n - 1)
        left = right = None
        for (i, v) in valid:
            if i <= x: left = (i, v)
            if i >= x and right is None: right = (i, v)
        if left is None: return right[1]
        if right is None: return left[1]
        if left[0] == right[0]: return left[1]
        t = (x - left[0]) / (right[0] - left[0])
        return left[1] * (1 - t) + right[1] * t

    heights = [((interp(c / max(chart_cols - 1, 1)) - y_min) / y_rng) * chart_rows
               for c in range(chart_cols)]
    lines = []
    for row in range(chart_rows):
        level = chart_rows - row - 1
        lines.append("".join("█" if h >= level + 1 or h > level else " "
                              for h in heights))
    return lines


def _overlay(base, overlay):
    result = []
    for b_row, o_row in zip(base, overlay):
        result.append("".join(
            "█" if b != " " else "·" if o != " " else " "
            for b, o in zip(b_row, o_row)
        ))
    return result


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    _, width = stdscr.getmaxyx()

    if cache.get("error"):
        _put(stdscr, 4, 0, f"  Error: {cache['error']}", colors["negative"])
        return

    labels    = cache.get("labels", [])
    today     = cache.get("today", [])
    month_ago = cache.get("month_ago", [])
    spreads   = cache.get("spreads", {})
    status    = cache.get("status", "")
    as_of     = cache.get("as_of", "")
    source    = cache.get("source", "")

    if not labels or all(v is None for v in today):
        _put(stdscr, 4, 0, "  Loading yield data...", colors["dim"]); return

    sep  = f"  {'─' * 50}"
    r    = 4
    AL   = 2 + Y_LABEL_WIDTH + 2   # axis left col

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "U.S. TREASURY YIELD CURVE", colors["orange"], bold=True)
    _put(stdscr, r, 30, f"As of {as_of}  [{source}]", colors["dim"]); r += 1

    sc = (colors["negative"] if "Inverted" in status
          else colors["orange"] if "Flat" in status else colors["positive"])
    _put(stdscr, r, 2, "Status: ", colors["dim"])
    _put(stdscr, r, 10, status, sc, bold=True); r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "█ Today    ", colors["orange"])
    _put(stdscr, r, 13, "· 1 Month Ago", colors["dim"]); r += 1

    chart_cols = max(10, width - Y_LABEL_WIDTH - 6)
    node_cols  = chart_cols // max(len(labels), 1)

    for i, (label, y_now, y_prev) in enumerate(zip(labels, today, month_ago)):
        col = AL + i * node_cols
        if col >= width - 10: break
        _put(stdscr, r,     col, label.center(node_cols), colors["dim"], bold=True)
        _put(stdscr, r + 1, col,
             (f"{y_now:.2f}%" if y_now is not None else "N/A").center(node_cols),
             colors["orange"], bold=True)
        _put(stdscr, r + 2, col,
             (f"{y_prev:.2f}%" if y_prev is not None else "").center(node_cols),
             colors["dim"])
    r += 3

    tl = _build_curve_lines(today,     chart_cols, CHART_HEIGHT)
    ml = _build_curve_lines(month_ago, chart_cols, CHART_HEIGHT)
    mg = _overlay(tl, ml)

    all_y = [v for v in today + month_ago if v is not None]
    y_min = min(all_y) if all_y else 0
    y_max = max(all_y) if all_y else 5

    cs = r
    for i, line in enumerate(mg):
        tr = cs + i
        t  = i / max(CHART_HEIGHT - 1, 1)
        lbl = f"{y_max - t * (y_max - y_min):>5.2f}%"
        _put(stdscr, tr, 2, lbl, colors["dim"])
        _put(stdscr, tr, 2 + len(lbl), " │", colors["dim"])
        for ci, ch in enumerate(line):
            col = AL + ci
            if col >= width - 1: break
            if ch == "█": _put(stdscr, tr, col, ch, colors["orange"])
            elif ch == "·": _put(stdscr, tr, col, ch, colors["dim"])

    r = cs + CHART_HEIGHT
    _put(stdscr, r, AL - 2, "  " + "─" * (chart_cols + 1), colors["dim"]); r += 1

    n = len(labels)
    for i, label in enumerate(labels):
        frac = i / max(n - 1, 1)
        col  = AL + int(frac * (chart_cols - 1)) - len(label) // 2
        _put(stdscr, r, max(AL, min(col, width - len(label) - 2)),
             label, colors["dim"])
    r += 2

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "SPREADS", colors["orange"], bold=True); r += 1

    sc2 = 2
    for name, val in spreads.items():
        if val is None:
            s, c = "N/A", colors["dim"]
        else:
            sign = "+" if val >= 0 else ""
            s    = f"{sign}{val * 100:.1f} bps"
            c    = colors["positive"] if val >= 0 else colors["negative"]
        _put(stdscr, r, sc2, f"{name}  ", colors["dim"])
        _put(stdscr, r, sc2 + 6, s, c, bold=True)
        sc2 += 22

    r += 1
    _put(stdscr, r, 0, sep, colors["dim"])