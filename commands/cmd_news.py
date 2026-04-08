"""
commands/cmd_news.py
--------------------
Implements the  N [TICKER]  command — News Feed.

  N              <- live general market news feed
  N AAPL         <- company-specific news for AAPL

Navigation (PANE mode):
  ↑ / ↓     move cursor through articles
  Enter      expand selected article (full headline + summary + URL) / collapse
"""

import curses
import datetime

import market_data

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VISIBLE_ROWS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_datetime(unix_ts) -> tuple[str, str]:
    """Return (date_str, time_str) from a UNIX timestamp."""
    try:
        dt = datetime.datetime.fromtimestamp(int(unix_ts))
        return dt.strftime("%-m/%-d/%y"), dt.strftime("%H:%M:%S")
    except Exception:
        return "N/A", "N/A"


def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1].upper() if len(parts) > 1 else None

    try:
        if ticker:
            raw      = market_data.server_get(f"/api/company-news/{ticker}")
            articles = raw if isinstance(raw, list) else []
        else:
            articles = market_data.server_get("/api/news")
            if not isinstance(articles, list):
                articles = []

        # Sort newest first
        articles = sorted(articles, key=lambda x: x.get("datetime", 0), reverse=True)

        return {
            "error":       None,
            "articles":    articles,
            "ticker":      ticker,
            "selected":    0,
            "expanded":    False,
            "page_offset": 0,
        }
    except Exception as e:
        return {
            "error": str(e), "articles": [], "ticker": ticker,
            "selected": 0, "expanded": False, "page_offset": 0,
        }


# ---------------------------------------------------------------------------
# on_keypress — ↑↓ navigate, Enter expand/collapse
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    articles    = cache.get("articles", [])
    n           = len(articles)
    selected    = cache.get("selected", 0)
    expanded    = cache.get("expanded", False)
    page_offset = cache.get("page_offset", 0)

    if key == curses.KEY_UP:
        if n == 0:
            return cache
        new_sel = max(0, selected - 1)
        new_off = min(page_offset, new_sel)
        return {**cache, "selected": new_sel, "page_offset": new_off, "expanded": False}

    if key == curses.KEY_DOWN:
        if n == 0:
            return cache
        new_sel = min(n - 1, selected + 1)
        new_off = page_offset
        if new_sel >= page_offset + _VISIBLE_ROWS:
            new_off = new_sel - _VISIBLE_ROWS + 1
        return {**cache, "selected": new_sel, "page_offset": new_off, "expanded": False}

    if key in (curses.KEY_ENTER, 10, 13):
        return {**cache, "expanded": not expanded}

    return cache


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    _, width = stdscr.getmaxyx()

    error = cache.get("error")
    if error:
        _put(stdscr, 4, 0, f"  Error: {error}", colors["negative"])
        return

    articles    = cache.get("articles", [])
    ticker      = cache.get("ticker")
    selected    = cache.get("selected", 0)
    expanded    = cache.get("expanded", False)
    page_offset = cache.get("page_offset", 0)

    if not articles:
        _put(stdscr, 4, 0, "  Loading...", colors["dim"])
        return

    sep = f"  {'─' * min(90, width - 3)}"
    r   = 4

    # ── Header ───────────────────────────────────────────────────────────
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    title = f"NEWS  —  {ticker}" if ticker else "NEWS  —  Market Feed"
    _put(stdscr, r, 2, title, colors["orange"], bold=True)
    _put(stdscr, r, len(title) + 4, f"  {len(articles)} articles", colors["dim"])
    r += 1
    _put(stdscr, r, 2, "↑ ↓ navigate   Enter expand/collapse", colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── If expanded, show detail panel ───────────────────────────────────
    if expanded and 0 <= selected < len(articles):
        _render_detail(stdscr, r, articles[selected], colors, sep, width)
        return

    # ── Column headers ────────────────────────────────────────────────────
    # Dynamically size headline column based on terminal width
    C_HEADLINE = 2
    C_DATE     = max(54, width - 40)
    C_TIME     = C_DATE + 9
    C_TICKER   = C_TIME + 9
    C_SOURCE   = C_TICKER + 8

    _put(stdscr, r, C_HEADLINE, f"{'Headline':<{C_DATE - C_HEADLINE - 1}}", colors["dim"], bold=True)
    _put(stdscr, r, C_DATE,     f"{'Date':<8}",   colors["dim"], bold=True)
    _put(stdscr, r, C_TIME,     f"{'Time':<8}",   colors["dim"], bold=True)
    _put(stdscr, r, C_TICKER,   f"{'Ticker':<7}", colors["dim"], bold=True)
    if C_SOURCE < width - 4:
        _put(stdscr, r, C_SOURCE, "Source", colors["dim"], bold=True)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── Article rows ──────────────────────────────────────────────────────
    headline_w = C_DATE - C_HEADLINE - 2
    visible    = articles[page_offset: page_offset + _VISIBLE_ROWS]

    for i, article in enumerate(visible):
        abs_idx  = page_offset + i
        is_sel   = abs_idx == selected

        headline = (article.get("headline") or "")[:headline_w]
        date_s, time_s = _fmt_datetime(article.get("datetime", 0))
        sym      = (article.get("related") or article.get("symbol") or "--")[:6]
        source   = (article.get("source") or "")[:14]

        if is_sel:
            try:
                stdscr.attron(colors["highlight"] | curses.A_BOLD)
                stdscr.addstr(r, 0, " " * min(width - 1, 100))
                stdscr.attroff(colors["highlight"] | curses.A_BOLD)
            except Exception:
                pass
            hl_attr = colors["highlight"] | curses.A_BOLD

            def _row_put(col, text):
                try:
                    stdscr.attron(hl_attr)
                    stdscr.addstr(r, col, text)
                    stdscr.attroff(hl_attr)
                except Exception:
                    pass

            _row_put(C_HEADLINE, f"{headline:<{headline_w}}")
            _row_put(C_DATE,     f"{date_s:<8}")
            _row_put(C_TIME,     f"{time_s:<8}")
            _row_put(C_TICKER,   f"{sym:<7}")
            if C_SOURCE < width - 4:
                _row_put(C_SOURCE, f"{source:<14}")
        else:
            _put(stdscr, r, C_HEADLINE, f"{headline:<{headline_w}}", colors["dim"])
            _put(stdscr, r, C_DATE,     f"{date_s:<8}",              colors["dim"])
            _put(stdscr, r, C_TIME,     f"{time_s:<8}",              colors["dim"])
            _put(stdscr, r, C_TICKER,   f"{sym:<7}",                 colors["orange"])
            if C_SOURCE < width - 4:
                _put(stdscr, r, C_SOURCE, f"{source:<14}",           colors["dim"])

        r += 1

    # Scroll indicator
    total = len(articles)
    if total > _VISIBLE_ROWS:
        shown_end = min(page_offset + _VISIBLE_ROWS, total)
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1
        _put(stdscr, r, 2,
             f"Showing {page_offset + 1}–{shown_end} of {total}   ↑ ↓ to scroll",
             colors["dim"])
    else:
        _put(stdscr, r, 0, sep, colors["dim"])


def _render_detail(stdscr, r: int, article: dict, colors: dict, sep: str, width: int) -> None:
    """Expanded view for a single article."""

    def lv(row, label, value, val_color=None):
        _put(stdscr, row, 2,  f"{label:<14}", colors["dim"])
        _put(stdscr, row, 16, str(value),     val_color or colors["orange"])

    date_s, time_s = _fmt_datetime(article.get("datetime", 0))
    headline = article.get("headline", "N/A")
    source   = article.get("source", "N/A")
    sym      = article.get("related") or article.get("symbol") or "--"
    summary  = article.get("summary", "")
    url      = article.get("url", "N/A")

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # Headline — wrap across up to 3 lines
    wrap_w = max(40, width - 18)
    words  = headline.split()
    lines  = []
    line   = ""
    for w in words:
        if len(line) + len(w) + 1 <= wrap_w:
            line = (line + " " + w).strip()
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)

    for idx, hl in enumerate(lines[:3]):
        color = colors["header"] if idx == 0 else colors["dim"]
        _put(stdscr, r, 2, hl, color, bold=(idx == 0))
        r += 1
    r += 1

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    lv(r, "Date:",   f"{date_s}  {time_s}"); r += 1
    lv(r, "Source:", source);                r += 1
    lv(r, "Ticker:", sym, colors["orange"]); r += 1
    r += 1

    # Summary — wrap across up to 4 lines
    if summary:
        _put(stdscr, r, 2, "Summary:", colors["dim"], bold=True); r += 1
        sum_words = summary.split()
        s_line    = ""
        s_lines   = []
        for w in sum_words:
            if len(s_line) + len(w) + 1 <= wrap_w:
                s_line = (s_line + " " + w).strip()
            else:
                if s_line:
                    s_lines.append(s_line)
                s_line = w
        if s_line:
            s_lines.append(s_line)
        for sl in s_lines[:4]:
            _put(stdscr, r, 2, sl, colors["dim"])
            r += 1
        r += 1

    lv(r, "URL:", url[:width - 20], colors["dim"]); r += 1
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "Press Enter to collapse", colors["dim"])
