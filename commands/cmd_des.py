"""
commands/cmd_des.py
-------------------
DES <TICKER> — Company profile with 1Y chart and news.

Layout (two-panel):
  Left  — company profile (exchange, industry, market cap, etc.)
  Right — 1Y price chart loaded in background + top news headlines
"""

import curses
import threading

import market_data
import chart as chart_mod


CHART_HEIGHT  = chart_mod.CHART_HEIGHT   # 12
Y_LABEL_WIDTH = chart_mod.Y_LABEL_WIDTH  # 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, str(text))
        stdscr.attroff(attr)
    except Exception:
        pass


def _fmt_market_cap(raw) -> str:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return "N/A"
    if val >= 1_000:
        return f"${val / 1_000:.2f}B"
    return f"${val:.2f}M"


def _fmt_shares(raw) -> str:
    try:
        val = float(raw)
        return f"{val:,.2f}M"
    except (TypeError, ValueError):
        return "N/A"


# ---------------------------------------------------------------------------
# Background fetchers
# ---------------------------------------------------------------------------

def _start_chart_fetch(ticker: str, chart_result: dict) -> None:
    def _worker():
        data, error = market_data.fetch_gip_data(ticker, "1Y")
        chart_result["data"]  = data
        chart_result["error"] = error
        chart_result["done"]  = True
    threading.Thread(target=_worker, daemon=True).start()


def _start_news_fetch(ticker: str, news_result: dict) -> None:
    def _worker():
        try:
            articles = market_data.server_get(f"/api/company-news/{ticker}")
            news_result["articles"] = articles if isinstance(articles, list) else []
            news_result["error"]    = None
        except Exception as e:
            news_result["articles"] = []
            news_result["error"]    = str(e)
        news_result["done"] = True
    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {"data": None, "error": "Usage: DES <TICKER>   e.g. DES AAPL"}

    try:
        raw = market_data.server_get(f"/api/company/{ticker.upper()}")
        if not raw or not raw.get("name"):
            return {"data": None, "error": f"No profile data found for '{ticker}'. Check the ticker symbol."}

        data = {
            "symbol":     raw.get("ticker",           ticker.upper()),
            "name":       raw.get("name",              "N/A"),
            "exchange":   raw.get("exchange",          "N/A"),
            "industry":   raw.get("finnhubIndustry",   "N/A"),
            "country":    raw.get("country",           "N/A"),
            "currency":   raw.get("currency",          "N/A"),
            "ipo":        raw.get("ipo",               "N/A"),
            "market_cap": _fmt_market_cap(raw.get("marketCapitalization")),
            "shares_out": _fmt_shares(raw.get("shareOutstanding")),
            "phone":      raw.get("phone",             "N/A"),
            "website":    raw.get("weburl",            "N/A"),
        }

        chart_result = {"data": None, "error": None, "done": False}
        news_result  = {"articles": [], "error": None, "done": False}

        _start_chart_fetch(ticker.upper(), chart_result)
        _start_news_fetch(ticker.upper(), news_result)

        return {
            "data":         data,
            "error":        None,
            "chart_result": chart_result,
            "news_result":  news_result,
        }

    except Exception as e:
        return {"data": None, "error": str(e)}


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    height, width = stdscr.getmaxyx()

    error = cache.get("error")
    if error:
        _put(stdscr, 4, 2, f"Error: {error}", colors["negative"])
        return

    d = cache.get("data")
    if not d:
        _put(stdscr, 4, 2, "Loading...", colors["dim"])
        return

    left_w  = min(48, width // 2)
    right_c = left_w + 1           # absolute col where right panel starts
    right_w = width - right_c      # usable cols in right panel

    def lput(row, col, text, color, bold=False):
        """Draw text clipped to the left panel."""
        if row < 0 or row >= height or col >= left_w:
            return
        _put(stdscr, row, col, str(text)[:max(0, left_w - col)], color, bold)

    def rput(row, col, text, color, bold=False):
        """Draw text in the right panel; col is relative to right_c."""
        abs_col = right_c + col
        if row < 0 or row >= height or abs_col >= width:
            return
        _put(stdscr, row, abs_col, str(text)[:max(0, width - abs_col - 1)], color, bold)

    # ── Vertical separator ────────────────────────────────────────────────────
    for row in range(4, height - 1):
        _put(stdscr, row, left_w, "│", colors["dim"])

    sep_l = "  " + "─" * max(0, left_w - 4)
    r = 4

    # ── Left panel: header ────────────────────────────────────────────────────
    lput(r, 0, sep_l, colors["dim"]); r += 1
    lput(r, 2, d["symbol"], colors["orange"], bold=True)
    lput(r, 2 + len(d["symbol"]) + 2, d["name"], colors["orange"], bold=True)
    r += 1
    lput(r, 2, f"{d['exchange']}  ·  {d['country']}  ·  {d['currency']}", colors["dim"])
    r += 1
    lput(r, 0, sep_l, colors["dim"]); r += 1
    r += 1  # breathing room

    # ── Left panel: data rows ─────────────────────────────────────────────────
    def lv(row, label, value, vc=None):
        lbl = f"  {label:<18}"
        lput(row, 0, lbl, colors["dim"])
        lput(row, len(lbl), str(value), vc or colors["orange"])

    lv(r, "Industry:",   d["industry"]);              r += 1
    lv(r, "Market Cap:", d["market_cap"]);             r += 1
    lv(r, "IPO Date:",   d["ipo"]);                   r += 1
    lv(r, "Shares Out:", d["shares_out"]);             r += 1
    r += 1
    lput(r, 0, sep_l, colors["dim"]);                 r += 1
    lv(r, "Phone:",   d["phone"]);                    r += 1
    lv(r, "Website:", d["website"], vc=colors["dim"]); r += 1
    lput(r, 0, sep_l, colors["dim"])

    # ── Right panel ───────────────────────────────────────────────────────────
    if right_w < 24:
        return

    chart_result = cache.get("chart_result", {})
    news_result  = cache.get("news_result",  {})
    rr = 4  # right panel current row

    # ── Chart ─────────────────────────────────────────────────────────────────
    if not chart_result.get("done"):
        rput(rr + 1, 2, f"Fetching {d['symbol']} chart...", colors["dim"])
        rr += 2
    else:
        chart_data  = chart_result.get("data")
        chart_error = chart_result.get("error")

        if chart_error or not chart_data:
            msg = f"Chart unavailable: {chart_error or 'no data'}"
            rput(rr + 1, 2, msg, colors["negative"])
            rr += 2
        else:
            prices = chart_data.get("prices", [])
            dates  = chart_data.get("dates",  [])

            if not prices:
                rput(rr + 1, 2, "No price data.", colors["dim"])
                rr += 2
            else:
                trend_color  = chart_mod._trend_color(prices, colors)
                price_change = prices[-1] - prices[0]
                pct_change   = (price_change / prices[0]) * 100 if prices[0] else 0
                sign         = "+" if price_change >= 0 else ""

                # Chart header
                rput(rr, 0, "─" * max(0, right_w - 1), colors["dim"]); rr += 1
                rput(rr, 2, f"{d['symbol']}  —  1-Year Price",
                     colors["orange"], bold=True);                       rr += 1
                rput(rr, 2,
                     f"${prices[-1]:.2f}   {sign}{price_change:.2f} ({sign}{pct_change:.2f}%)",
                     trend_color, bold=True);                            rr += 1
                if dates:
                    rput(rr, 2, f"{dates[0]}  →  {dates[-1]}", colors["dim"]); rr += 1
                rput(rr, 0, "─" * max(0, right_w - 1), colors["dim"]); rr += 1

                # Chart body
                chart_cols    = max(10, right_w - Y_LABEL_WIDTH - 5)
                lines, y_labels, _, _ = chart_mod.build_block_chart(
                    prices, chart_cols, CHART_HEIGHT
                )
                y_label_len   = len(y_labels[0]) if y_labels else 8
                chart_body_c  = 1 + y_label_len + 2   # relative col where bars start

                chart_body_top = rr
                for i, (line, label) in enumerate(zip(lines, y_labels)):
                    row = chart_body_top + i
                    if row >= height - 1:
                        break
                    rput(row, 1,               label,  colors["dim"])
                    rput(row, 1 + y_label_len, " │",   colors["dim"])
                    rput(row, chart_body_c,    line,   trend_color)
                rr = chart_body_top + len(lines)

                # X-axis tick line
                if rr < height - 1:
                    rput(rr, chart_body_c - 2, "─" * (chart_cols + 2), colors["dim"])
                    rr += 1

                # X-axis date labels
                if dates and rr < height - 1:
                    mid_idx  = len(dates) // 2
                    mid_date = dates[mid_idx]
                    mid_col  = chart_body_c + (chart_cols // 2) - len(mid_date) // 2
                    end_col  = chart_body_c + chart_cols - len(dates[-1])
                    rput(rr, chart_body_c, dates[0],  colors["dim"])
                    rput(rr, mid_col,      mid_date,  colors["dim"])
                    rput(rr, end_col,      dates[-1], colors["dim"])
                    rr += 2

    # ── News headlines ────────────────────────────────────────────────────────
    if rr < height - 2:
        rput(rr, 0, "─" * max(0, right_w - 1), colors["dim"]); rr += 1
    if rr < height - 2:
        rput(rr, 2, "NEWS", colors["orange"], bold=True); rr += 1

    if not news_result.get("done"):
        if rr < height - 2:
            rput(rr, 2, "Fetching news...", colors["dim"])
    else:
        articles = news_result.get("articles", [])
        if not articles and rr < height - 2:
            rput(rr, 2, "No recent news.", colors["dim"])
        else:
            max_hl_w = max(0, right_w - 6)
            for art in articles[:5]:
                if rr >= height - 2:
                    break
                headline = art.get("headline", "")
                source   = art.get("source", "")
                if source:
                    src_tag  = f" [{source}]"
                    headline = headline[:max(0, max_hl_w - len(src_tag))] + src_tag
                else:
                    headline = headline[:max_hl_w]
                rput(rr, 2, f"· {headline}", colors["dim"])
                rr += 1
