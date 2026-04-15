"""
market_data.py
--------------
Handles all Finnhub API calls, background refresh threads,
and the persistent banner drawing functions used in the main loop.

Supported by commands:
  cmd_quote.py  — Q <TICKER>
  cmd_gip.py    — GIP <TICKER>

Header rows owned by this module:
  Row 1 — benchmark banner : S&P 500 / NASDAQ / DOW / …
  Row 2 — scrolling news ticker : top N market headlines
"""

import curses
import datetime
import threading
import time


def server_get(path: str, params: dict = None) -> dict:
    """GET request to the Brodberg server. Used by all commands that need market data."""
    import requests
    import brodberg_session
    url = f"{brodberg_session.get_server_url()}{path}"
    r   = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

# ---------------------------------------------------------------------------
# Benchmark configuration
# ---------------------------------------------------------------------------

BENCHMARKS = [
    {"symbol": "SPY",             "label": "S&P 500"},
    {"symbol": "QQQ",             "label": "NASDAQ"},
    {"symbol": "DIA",             "label": "DOW"},
    {"symbol": "GLD",             "label": "GOLD"},
    {"symbol": "SLV",             "label": "SILVER"},
    {"symbol": "BNO",             "label": "BRENT"},
    {"symbol": "UNG",             "label": "NAT GAS"},
    {"symbol": "BINANCE:BTCUSDT", "label": "BTC"},
]

BENCHMARK_REFRESH_INTERVAL = 3     # seconds — server keeps live prices, client just polls
NEWS_REFRESH_INTERVAL      = 600   # seconds (10 minutes)
NEWS_HEADLINE_COUNT        = 3     # how many headlines to show in the ticker
NEWS_SCROLL_SPEED          = 2     # columns to advance per 100 ms tick


# ---------------------------------------------------------------------------
# Low-level API helpers
# ---------------------------------------------------------------------------

def _fetch_raw_quote(symbol):
    """Fetch a quote via the Brodberg server proxy. Returns raw JSON dict."""
    return server_get(f"/api/quote/{symbol.upper()}")


def _fetch_raw_news():
    """Fetch market news via the Brodberg server proxy. Returns list of article dicts."""
    return server_get("/api/news")


# ---------------------------------------------------------------------------
# Quote API
# ---------------------------------------------------------------------------

def get_quote(ticker):
    """
    Fetch a real-time quote for `ticker` from Finnhub.
    Returns a dict with clean field names, or raises on failure.
    """
    raw = _fetch_raw_quote(ticker.upper())

    if not raw or raw.get("c", 0) == 0:
        raise ValueError(f"No data returned for '{ticker}'. Check the ticker symbol.")

    current    = raw.get("c",  0)
    prev_close = raw.get("pc", 0)
    change     = raw.get("d",  0) or 0
    change_pct = raw.get("dp", 0) or 0
    today      = datetime.date.today().isoformat()

    return {
        "symbol":         ticker.upper(),
        "price":          f"{current:.2f}",
        "open":           f"{raw.get('o', 0):.2f}",
        "high":           f"{raw.get('h', 0):.2f}",
        "low":            f"{raw.get('l', 0):.2f}",
        "volume":         "N/A",
        "prev_close":     f"{prev_close:.2f}",
        "change":         f"{change:.2f}",
        "change_pct":     f"{change_pct:.2f}%",
        "latest_trading": today,
    }


# ---------------------------------------------------------------------------
# Timeframe configuration (used by GIP command)
# ---------------------------------------------------------------------------

# Maps user-facing period tokens to (yfinance period, yfinance interval) tuples.
TIMEFRAME_MAP = {
    "1W":  ("5d",   "1d"),
    "1M":  ("1mo",  "1d"),
    "3M":  ("3mo",  "1d"),
    "1Y":  ("1y",   "1wk"),
    "YTD": ("ytd",  "1d"),
}
DEFAULT_TIMEFRAME = "1Y"
VALID_TIMEFRAMES  = list(TIMEFRAME_MAP.keys())


def get_candles(ticker, timeframe="1Y"):
    """
    Fetch historical closing prices via yfinance.

    Parameters
    ----------
    ticker    : str  — e.g. "AAPL"
    timeframe : str  — one of VALID_TIMEFRAMES (default: DEFAULT_TIMEFRAME)

    Returns a dict with keys "symbol", "prices", "dates", "timeframe",
    or raises on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed. Run: pip install yfinance")

    tf = timeframe.upper()
    if tf not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unknown timeframe '{timeframe}'. "
            f"Valid options: {', '.join(VALID_TIMEFRAMES)}"
        )

    period, interval = TIMEFRAME_MAP[tf]
    hist = yf.Ticker(ticker.upper()).history(period=period, interval=interval)

    if hist.empty:
        raise ValueError(f"No data returned for '{ticker}'. Check the ticker symbol.")

    import math
    closes, dates = [], []
    for c, d in zip(hist["Close"].tolist(), hist.index.to_pydatetime()):
        try:
            f = float(c)
            if not math.isnan(f):
                closes.append(round(f, 2))
                dates.append(d.strftime("%Y-%m-%d"))
        except (TypeError, ValueError):
            pass

    if not closes:
        raise ValueError(f"No valid price data returned for '{ticker}'.")

    return {
        "symbol":    ticker.upper(),
        "prices":    closes,
        "dates":     dates,
        "timeframe": tf,
    }


def fetch_gip_data(ticker, timeframe=DEFAULT_TIMEFRAME):
    """
    Fetch candle data for `ticker` over `timeframe`.
    Returns (data_dict, error_string).
    Call once on command entry — never in the render loop.
    """
    if not ticker:
        valid = ', '.join(VALID_TIMEFRAMES)
        return None, f"Usage: GIP <TICKER> [TIMEFRAME]   e.g. GIP AAPL 1Y   Timeframes: {valid}"
    try:
        d = get_candles(ticker.upper(), timeframe)
        return d, None
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Benchmark banner — background refresh
# ---------------------------------------------------------------------------

_benchmark_data   = [{"label": b["label"], "symbol": b["symbol"],
                       "price": "...", "change": None} for b in BENCHMARKS]
_benchmark_lock   = threading.Lock()
_benchmark_thread = None


def _refresh_benchmarks():
    try:
        live = server_get("/api/live/benchmarks")
    except Exception:
        live = {}

    new_data = []
    for b in BENCHMARKS:
        entry = {"label": b["label"], "symbol": b["symbol"],
                 "price": "...", "change": None}
        data = live.get(b["symbol"])
        if data and data.get("price"):
            entry["price"]  = f"{data['price']:.2f}"
            entry["change"] = data.get("change_pct")
        new_data.append(entry)

    with _benchmark_lock:
        _benchmark_data[:] = new_data


def _benchmark_loop():
    while True:
        try:
            _refresh_benchmarks()
            time.sleep(BENCHMARK_REFRESH_INTERVAL)
        except Exception:
            time.sleep(10)


def start_benchmark_thread():
    global _benchmark_thread
    if _benchmark_thread is not None:
        return
    _refresh_benchmarks()   # immediate first fetch
    _benchmark_thread = threading.Thread(target=_benchmark_loop, daemon=True)
    _benchmark_thread.start()


def get_benchmark_snapshot():
    with _benchmark_lock:
        return list(_benchmark_data)


# ---------------------------------------------------------------------------
# News ticker — background refresh
# ---------------------------------------------------------------------------

_news_ticker_text = "  Fetching headlines...  "
_news_lock        = threading.Lock()
_news_thread      = None


def _build_ticker_text(articles):
    """
    Turn a list of Finnhub article dicts into a single scrolling string.
    Format:  | Headline one   | Headline two   | Headline three
    """
    headlines = []
    for a in articles[:NEWS_HEADLINE_COUNT]:
        h = a.get("headline", "").strip()
        if h:
            headlines.append(h)
    if not headlines:
        return "  No headlines available.  "
    sep = "     |     "
    return "     |  " + sep.join(headlines) + "     "


def _refresh_news():
    global _news_ticker_text
    try:
        articles = _fetch_raw_news()
        text     = _build_ticker_text(articles)
    except Exception:
        text = "  Unable to fetch headlines.  "
    with _news_lock:
        _news_ticker_text = text


def _news_loop():
    while True:
        try:
            _refresh_news()
            time.sleep(NEWS_REFRESH_INTERVAL)
        except Exception:
            time.sleep(10)


def start_news_thread():
    global _news_thread
    if _news_thread is not None:
        return
    _refresh_news()   # immediate first fetch
    _news_thread = threading.Thread(target=_news_loop, daemon=True)
    _news_thread.start()


def get_news_ticker_text():
    with _news_lock:
        return _news_ticker_text


# ---------------------------------------------------------------------------
# Drawing: benchmark banner (row 1)
# ---------------------------------------------------------------------------

def draw_benchmark_banner(stdscr, width, colors):
    """
    Draw benchmark prices on row 1.
    Each item: LABEL  $price  +/-pct%   |
    """
    snapshot = get_benchmark_snapshot()
    col = 2

    for item in snapshot:
        label  = item["label"]
        price  = item["price"]
        change = item["change"]

        if change is not None:
            sign         = "+" if change >= 0 else ""
            c_str        = f"{sign}{change:.2f}%"
            change_color = colors["positive"] if change >= 0 else colors["negative"]
        else:
            c_str        = ""
            change_color = colors["orange"]

        text_label = f"{label}  ${price} "

        if col + len(text_label) + len(c_str) + 6 > width:
            break

        try:
            stdscr.attron(colors["orange"])
            stdscr.addstr(1, col, text_label)
            stdscr.attroff(colors["orange"])
        except Exception:
            pass

        col += len(text_label)

        if c_str:
            try:
                stdscr.attron(change_color)
                stdscr.addstr(1, col, c_str)
                stdscr.attroff(change_color)
            except Exception:
                pass
            col += len(c_str)

        sep = "  |  "
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(1, col, sep)
            stdscr.attroff(colors["dim"])
        except Exception:
            pass
        col += len(sep)


# ---------------------------------------------------------------------------
# Drawing: news ticker (row 2)
# ---------------------------------------------------------------------------

def draw_news_ticker(stdscr, width, scroll_offset, colors):
    """
    Draw the scrolling news ticker on row 2.

    scroll_offset is a non-negative integer that advances each frame,
    managed entirely by main.py. The ticker text is looped so it wraps
    seamlessly — the caller just increments the offset forever.
    """
    text = get_news_ticker_text()
    if not text:
        return

    looped  = text * ((width // len(text)) + 3)
    start   = scroll_offset % len(text)
    visible = looped[start: start + width - 1]

    try:
        stdscr.attron(colors["dim"])
        stdscr.addstr(2, 0, visible.ljust(width - 1))
        stdscr.attroff(colors["dim"])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Convenience wrapper used by cmd_quote.py
# ---------------------------------------------------------------------------

def fetch_quote_data(ticker):
    """
    Fetch quote data for `ticker`. Returns (data_dict, error_string).
    Call once on command entry — never in the render loop.
    """
    if not ticker:
        return None, "Usage: Q <TICKER>   e.g. Q AAPL"
    try:
        q = get_quote(ticker.upper())
        return q, None
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Chat inline ticker cache
# ---------------------------------------------------------------------------
# Tickers mentioned in chat messages (e.g. $NVDA) are fetched once in the
# background and cached here.  The render loop reads from this cache — it
# never blocks or makes network calls.

_CHAT_TICKER_TTL     = 300   # seconds before a cached quote is considered stale
_chat_ticker_cache: dict = {}   # symbol → {"change_pct": float|None, "loading": bool, "fetched_at": float}
_chat_ticker_lock    = threading.Lock()


def request_chat_quote(symbol: str) -> None:
    """
    Ensure a background fetch is running for `symbol`.
    Safe to call from the render loop — returns immediately.
    If already loading or recently fetched, does nothing.
    """
    sym = symbol.upper()
    with _chat_ticker_lock:
        entry = _chat_ticker_cache.get(sym)
        if entry:
            if entry.get("loading"):
                return
            if time.time() - entry.get("fetched_at", 0) < _CHAT_TICKER_TTL:
                return
        # Mark as loading so concurrent renders don't double-fetch
        _chat_ticker_cache[sym] = {"change_pct": None, "loading": True, "fetched_at": time.time()}

    def _fetch():
        try:
            raw        = _fetch_raw_quote(sym)
            change_pct = raw.get("dp") if raw else None
        except Exception:
            change_pct = None
        with _chat_ticker_lock:
            _chat_ticker_cache[sym] = {
                "change_pct": change_pct,
                "loading":    False,
                "fetched_at": time.time(),
            }

    threading.Thread(target=_fetch, daemon=True, name=f"chat-ticker-{sym}").start()


def get_chat_quote(symbol: str):
    """
    Return the cached daily change % for `symbol`, or None if not yet loaded.
    Never blocks.
    """
    with _chat_ticker_lock:
        entry = _chat_ticker_cache.get(symbol.upper())
        if entry and not entry.get("loading"):
            return entry.get("change_pct")
        return None