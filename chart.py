"""
chart.py
--------
Block-based line chart renderer for curses terminals.

Used by commands/cmd_gip.py via render_gip().
Uses vertical block characters instead of braille for better compatibility.
"""

import curses

# ---------------------------------------------------------------------------
# Block characters (low → high fill)
# ---------------------------------------------------------------------------

BLOCKS = [" ", "█", "█", "█", "█", "█", "█", "█", "█"]


# ---------------------------------------------------------------------------
# Core chart builder
# ---------------------------------------------------------------------------

def build_block_chart(prices, chart_cols, chart_rows):
    """
    Convert price data into a vertical block chart grid.

    Returns:
        lines     — list of strings (one per row, top → bottom)
        y_labels  — list of price label strings (one per row)
        price_min — float
        price_max — float
    """
    if not prices or len(prices) < 2:
        return ["  Not enough data to draw chart."], [], 0, 0

    price_min   = min(prices)
    price_max   = max(prices)
    price_range = price_max - price_min or 1

    n = len(prices)

    def price_at_col(c):
        """Interpolate the price value at column c."""
        t     = c / max(chart_cols - 1, 1)
        idx_f = t * (n - 1)
        lo    = int(idx_f)
        hi    = min(lo + 1, n - 1)
        frac  = idx_f - lo
        return prices[lo] * (1 - frac) + prices[hi] * frac

    # Compute normalised heights (in row units) for each column
    heights = []
    for c in range(chart_cols):
        price = price_at_col(c)
        norm  = (price - price_min) / price_range
        heights.append(norm * chart_rows)

    # Build grid top-down
    lines = []
    for row in range(chart_rows):
        row_str = ""
        for col in range(chart_cols):
            h     = heights[col]
            level = chart_rows - row - 1   # invert: top row = highest value

            if h >= level + 1:
                char = "█"
            elif h > level:
                frac = h - level
                idx  = int(frac * (len(BLOCKS) - 1))
                char = BLOCKS[idx]
            else:
                char = " "

            row_str += char
        lines.append(row_str)

    # Y-axis labels (top → bottom, matching row order)
    y_labels = []
    for r in range(chart_rows):
        t           = r / max(chart_rows - 1, 1)
        label_price = price_max - t * price_range
        y_labels.append(f"{label_price:>8.2f}")

    return lines, y_labels, price_min, price_max


# ---------------------------------------------------------------------------
# Curses renderer
# ---------------------------------------------------------------------------

CHART_HEIGHT  = 12
Y_LABEL_WIDTH = 10


def _trend_color(prices, colors):
    """Return green if uptrend, red if downtrend, orange if flat."""
    if prices[-1] > prices[0]:
        return colors["positive"]
    elif prices[-1] < prices[0]:
        return colors["negative"]
    return colors["orange"]


def render_gip(stdscr, gip_cache: dict, colors: dict) -> None:
    """
    Render the 30-day price chart from a previously fetched cache dict.
    Drawing starts at row 4 (rows 0-3 are reserved chrome).
    Safe to call every frame — never hits the API.
    """
    error = gip_cache.get("error")
    if error:
        try:
            stdscr.attron(colors["negative"])
            stdscr.addstr(4, 0, f"  Error: {error}")
            stdscr.attroff(colors["negative"])
        except Exception:
            pass
        return

    d = gip_cache.get("data")
    if not d:
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(4, 0, "  Loading chart...")
            stdscr.attroff(colors["dim"])
        except Exception:
            pass
        return

    symbol = d["symbol"]
    prices = d["prices"]
    dates  = d["dates"]

    if not prices:
        try:
            stdscr.attron(colors["negative"])
            stdscr.addstr(4, 0, "  No price data available.")
            stdscr.attroff(colors["negative"])
        except Exception:
            pass
        return

    _, width = stdscr.getmaxyx()

    chart_color  = _trend_color(prices, colors)
    separator    = f"  {'─' * 40}"
    price_change = prices[-1] - prices[0]
    pct_change   = (price_change / prices[0]) * 100 if prices[0] else 0
    sign         = "+" if price_change >= 0 else ""

    def put(row, col, text, color, bold=False):
        attr = color | (curses.A_BOLD if bold else 0)
        try:
            stdscr.attron(attr)
            stdscr.addstr(row, col, text)
            stdscr.attroff(attr)
        except Exception:
            pass

    r = 4
    put(r,     0, separator,                                                     colors["dim"])
    timeframe = d.get("timeframe", "")
    tf_label  = {
        "1W":  "1-Week",
        "1M":  "1-Month",
        "3M":  "3-Month",
        "1Y":  "1-Year",
        "YTD": "Year-to-Date",
    }.get(timeframe, timeframe)
    put(r + 1, 0, f"   {symbol}  —  {tf_label} Price Trend",                       colors["orange"], bold=True)
    put(r + 2, 0, f"   ${prices[-1]:.2f}   {sign}{price_change:.2f} ({sign}{pct_change:.2f}%)",
        chart_color, bold=True)
    put(r + 3, 0, f"   {dates[0]}  →  {dates[-1]}",                            colors["dim"])
    put(r + 4, 0, separator,                                                     colors["dim"])

    # Chart body
    chart_start_row = r + 5
    chart_cols      = max(10, width - Y_LABEL_WIDTH - 4)
    lines, y_labels, _, _ = build_block_chart(prices, chart_cols, CHART_HEIGHT)

    for i, (line, label) in enumerate(zip(lines, y_labels)):
        row = chart_start_row + i
        put(row, 2,                    label,       colors["dim"])
        put(row, 2 + len(label),       " │",        colors["dim"])
        put(row, 2 + len(label) + 2,   line,        chart_color)

    # X-axis
    x_axis_row = chart_start_row + CHART_HEIGHT
    axis_left  = 2 + Y_LABEL_WIDTH

    put(x_axis_row, axis_left - 2, "  " + "─" * (chart_cols + 1), colors["dim"])

    if dates:
        mid_idx  = len(dates) // 2
        mid_date = dates[mid_idx]
        mid_col  = axis_left + (chart_cols // 2) - len(mid_date) // 2
        end_col  = axis_left + chart_cols - len(dates[-1])

        put(x_axis_row + 1, axis_left, dates[0],  colors["dim"])
        put(x_axis_row + 1, mid_col,   mid_date,  colors["dim"])
        put(x_axis_row + 1, end_col,   dates[-1], colors["dim"])

    put(x_axis_row + 2, 0, separator, colors["dim"])