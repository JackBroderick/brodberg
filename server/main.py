"""
server/main.py
--------------
Brodberg online server — user accounts, profiles, and API proxy.

User account endpoints:
  POST /register            — create a new account
  POST /login               — authenticate, receive a JWT token
  GET  /profile/{username}  — view any user's public profile
  GET  /me                  — view your own profile (token required)
  PUT  /me                  — update bio / location  (token required)

Market data proxy endpoints (keys live here — clients send no keys):
  GET  /api/quote/{symbol}    — Finnhub stock quote
  GET  /api/news              — Finnhub market news
  GET  /api/profile/{symbol}  — Finnhub company profile
  GET  /api/yield-curve       — Finnhub US Treasury yield curve
  GET  /api/forex/rates       — Finnhub FX spot rates (base USD)
  GET  /api/forex/candles     — Finnhub FX daily candles
  GET  /api/live/benchmarks   — real-time benchmark prices (live store)
  WS   /api/ship              — AISStream WebSocket proxy

Environment variables:
  BRODBERG_SECRET       — JWT signing key  (required in production)
  DATABASE_URL          — PostgreSQL URL   (set automatically by Render)
  FINNHUB_API_KEY       — Finnhub API key
  AISSTREAM_API_KEY     — AISStream API key
  BRODBERG_DB           — SQLite path      (local dev only)
"""

import os
import sqlite3
import hashlib
import hmac
import json
import time
import base64
import re
import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SECRET_KEY    = os.environ.get("BRODBERG_SECRET", "change-me-before-deploying")
TOKEN_SECONDS = 60 * 60 * 24 * 30
_HERE         = os.path.dirname(os.path.abspath(__file__))
_DATABASE_URL = os.environ.get("DATABASE_URL")
_SQLITE_PATH  = os.environ.get("BRODBERG_DB", os.path.join(_HERE, "brodberg.db"))

FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY", "")
AISSTREAM_KEY = os.environ.get("AISSTREAM_API_KEY", "")
FH_BASE       = "https://finnhub.io/api/v1"
AISSTREAM_URI = "wss://stream.aisstream.io/v0/stream"

USERNAME_RE   = re.compile(r"^[a-zA-Z0-9_]{3,20}$")

# ---------------------------------------------------------------------------
# Shared HTTP client (created once at startup, reused across all requests)
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient = None

# ---------------------------------------------------------------------------
# Simple TTL response cache
# ---------------------------------------------------------------------------

_cache: dict        = {}
_cache_lock         = threading.Lock()

# Cache TTLs in seconds
_TTL_QUOTE          = 30
_TTL_NEWS           = 300
_TTL_COMPANY        = 3600
_TTL_YIELD_CURVE    = 3600
_TTL_FOREX_RATES    = 60
_TTL_FOREX_CANDLES  = 300
_TTL_IPO            = 3600    # 1 hour
_TTL_PEERS          = 86400   # 24 hours — peer groups rarely change
_TTL_EXECUTIVES     = 86400
_TTL_DIVIDENDS      = 3600
_TTL_INSIDER        = 1800    # 30 minutes
_TTL_SENTIMENT      = 300
_TTL_OPTIONS        = 300     # 5 minutes


# ---------------------------------------------------------------------------
# Unusual Options Activity store
# Populated by _uo_scheduler(); persisted to/from DB across restarts.
# ---------------------------------------------------------------------------

_unusual_options: dict = {"data": [], "as_of": None}

# ---------------------------------------------------------------------------
# Earnings Calendar store
# Populated by _earn_scheduler(); persisted to/from DB across restarts.
# ---------------------------------------------------------------------------

_earnings: dict = {"data": [], "as_of": None}


def _cache_get(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() < entry["exp"]:
            return entry["data"]
    return None


def _cache_set(key: str, data, ttl: int):
    with _cache_lock:
        _cache[key] = {"data": data, "exp": time.time() + ttl}


# ---------------------------------------------------------------------------
# Live benchmark price store
# All access is within the asyncio event loop — no lock needed.
# ---------------------------------------------------------------------------

_live_prices: dict = {}   # symbol -> {"price": float, "change_pct": float|None}

# Symbols streamed via Finnhub WebSocket (real-time trade feed)
_WS_BENCH   = ["BINANCE:BTCUSDT"]

# Symbols polled via REST every N seconds (indices have no trade feed)
_REST_BENCH = ["SPY", "QQQ", "DIA", "GLD", "SLV", "BNO", "UNG"]

_REST_POLL_INTERVAL = 15  # seconds — indices tick every ~15s during market hours


async def _rest_bench_poll():
    """Poll Finnhub REST for index/ETF benchmark prices every 5 seconds."""
    while True:
        for sym in _REST_BENCH:
            try:
                r = await _http_client.get(f"{FH_BASE}/quote",
                                           params={"symbol": sym, "token": FINNHUB_KEY})
                q = r.json()
                if q.get("c"):
                    _live_prices[sym] = {"price": q["c"], "change_pct": q.get("dp")}
            except Exception:
                pass
        await asyncio.sleep(_REST_POLL_INTERVAL)


async def _finnhub_ws_bench():
    """Maintain a persistent Finnhub WebSocket for real-time benchmark trade prices."""
    import websockets
    uri = f"wss://ws.finnhub.io?token={FINNHUB_KEY}"

    # Seed change_pct for WS symbols from REST before streaming starts
    for sym in _WS_BENCH:
        try:
            r = await _http_client.get(f"{FH_BASE}/quote",
                                       params={"symbol": sym, "token": FINNHUB_KEY})
            q = r.json()
            if q.get("c"):
                _live_prices[sym] = {"price": q["c"], "change_pct": q.get("dp")}
        except Exception:
            pass

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20) as ws:
                for sym in _WS_BENCH:
                    await ws.send(json.dumps({"type": "subscribe", "symbol": sym}))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "trade":
                        for trade in msg.get("data", []):
                            sym = trade["s"]
                            entry = _live_prices.get(sym, {})
                            entry["price"] = trade["p"]
                            _live_prices[sym] = entry
        except Exception:
            await asyncio.sleep(5)   # reconnect after any error


# ---------------------------------------------------------------------------
# Database — connection pool (PostgreSQL) or sqlite (local dev)
# ---------------------------------------------------------------------------

_pool = None


def _init_pool():
    global _pool
    if _DATABASE_URL:
        import psycopg2.pool
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, _DATABASE_URL)


def _q(sql: str) -> str:
    if _DATABASE_URL:
        return sql.replace("?", "%s")
    return sql


@contextmanager
def _db_conn():
    """Context manager that yields a DB connection and returns it to the pool on exit."""
    if _DATABASE_URL and _pool:
        import psycopg2.extras
        conn = _pool.getconn()
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        try:
            yield conn
        finally:
            _pool.putconn(conn)
    else:
        conn = sqlite3.connect(_SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def _execute(conn, sql: str, params: tuple = ()):
    cur = conn.cursor()
    cur.execute(_q(sql), params)
    return cur


def _init_db() -> None:
    with _db_conn() as conn:
        if _DATABASE_URL:
            _execute(conn, """
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    username      TEXT   UNIQUE NOT NULL,
                    password_hash TEXT   NOT NULL,
                    created_at    TEXT   NOT NULL,
                    bio           TEXT   NOT NULL DEFAULT '',
                    location      TEXT   NOT NULL DEFAULT ''
                )
            """)
        else:
            _execute(conn, """
                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT    UNIQUE NOT NULL COLLATE NOCASE,
                    password_hash TEXT    NOT NULL,
                    created_at    TEXT    NOT NULL,
                    bio           TEXT    NOT NULL DEFAULT '',
                    location      TEXT    NOT NULL DEFAULT ''
                )
            """)
        # Unusual options — same schema for both DBs (all TEXT)
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS unusual_options (
                trade_date TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        # Earnings calendar
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS earnings_calendar (
                trade_date TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        # Chat messages
        if _DATABASE_URL:
            _execute(conn, """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id        SERIAL PRIMARY KEY,
                    room      TEXT NOT NULL,
                    from_user TEXT NOT NULL,
                    text      TEXT NOT NULL,
                    ts        TEXT NOT NULL
                )
            """)
            _execute(conn, "CREATE INDEX IF NOT EXISTS idx_chat_room ON chat_messages (room, id)")
        else:
            _execute(conn, """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    room      TEXT NOT NULL,
                    from_user TEXT NOT NULL,
                    text      TEXT NOT NULL,
                    ts        TEXT NOT NULL
                )
            """)
            _execute(conn, "CREATE INDEX IF NOT EXISTS idx_chat_room ON chat_messages (room, id)")

        # Migrations — add admin/moderation columns if they don't exist yet
        for col in ("is_admin", "is_muted", "is_banned"):
            try:
                if _DATABASE_URL:
                    _execute(conn, f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} BOOLEAN DEFAULT FALSE")
                else:
                    _execute(conn, f"ALTER TABLE users ADD COLUMN {col} BOOLEAN DEFAULT FALSE")
            except Exception:
                pass  # column already exists (SQLite raises on duplicate)

        # Bootstrap: grant admin to the owner account
        _execute(conn,
            "UPDATE users SET is_admin = TRUE WHERE username = 'jackbroderick'")

        conn.commit()


# ---------------------------------------------------------------------------
# Unusual Options — DB helpers and background scraper
# ---------------------------------------------------------------------------

def _uo_save_db(trade_date: str, rows: list) -> None:
    try:
        with _db_conn() as conn:
            _execute(conn,
                "INSERT INTO unusual_options (trade_date, data, fetched_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (trade_date) DO UPDATE SET "
                "data = excluded.data, fetched_at = excluded.fetched_at",
                (trade_date, json.dumps(rows),
                 datetime.now(timezone.utc).isoformat()))
            conn.commit()
    except Exception as e:
        print(f"[UO] db save error: {e}")


def _uo_load_db() -> None:
    """Load the most recent UO snapshot from DB into the in-memory store."""
    try:
        with _db_conn() as conn:
            cur = _execute(conn,
                "SELECT trade_date, data FROM unusual_options "
                "ORDER BY trade_date DESC LIMIT 1")
            row = cur.fetchone()
        if row:
            _unusual_options["data"]  = json.loads(row["data"])
            _unusual_options["as_of"] = row["trade_date"]
            print(f"[UO] loaded {len(_unusual_options['data'])} rows "
                  f"for {_unusual_options['as_of']} from DB")
    except Exception as e:
        print(f"[UO] db load error: {e}")


async def _scrape_unusual_options() -> dict:
    """
    Fetch Barchart unusual options activity via their CSV download endpoint.

    Returns a diagnostic dict with keys:
      ok (bool), rows (int), error (str|None), detail (str)
    so callers can surface failures without digging through logs.
    """
    from urllib.parse import unquote
    from datetime import date as _date

    base_url = "https://www.barchart.com/options/unusual-activity/stocks"
    today    = _date.today().strftime("%Y-%m-%d")

    hdrs = {
        "User-Agent":      ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"),
        "Accept":          ("text/html,application/xhtml+xml,application/xml;"
                            "q=0.9,image/webp,*/*;q=0.8"),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    }

    try:
        # Step 1 — land on the page to collect session cookies (esp. XSRF-TOKEN)
        r1      = await _http_client.get(base_url, headers=hdrs,
                                         follow_redirects=True, timeout=30.0)
        xsrf    = unquote(r1.cookies.get("XSRF-TOKEN", ""))
        cookies = dict(r1.cookies)

        diag_step1 = {
            "status": r1.status_code,
            "cookies": list(cookies.keys()),
            "xsrf_found": bool(xsrf),
            "body_preview": r1.text[:300],
        }

        if not xsrf:
            msg = f"XSRF-TOKEN not in cookies. step1={diag_step1}"
            print(f"[UO] {msg}")
            return {"ok": False, "rows": 0, "error": "no_xsrf", "detail": msg}

        # Step 2 — call Barchart's internal JSON API using their exact filter params
        # (reverse-engineered from browser Network tab on /options/unusual-activity/stocks)
        _TARGET  = 500   # rows we want total
        _PG_SIZE = 100   # rows per API request

        base_qs = (
            "fields=symbol,baseSymbol,baseLastPrice,expirationDate,"
            "daysToExpiration,strikePrice,moneyness,bidPrice,lastPrice,"
            "askPrice,volume,openInterest,volumeOpenInterestRatio,"
            "weightedImpliedVolatility,delta,tradeTime"
            "&orderBy=volumeOpenInterestRatio&orderDir=desc"
            "&baseSymbolTypes=stock"
            "&between(volumeOpenInterestRatio,1.24,)="
            "&between(lastPrice,.10,)="
            "&between(volume,500,)="
            "&between(openInterest,100,)="
            "&in(exchange,(AMEX,NYSE,NASDAQ,INDEX-CBOE))="
            f"&limit={_PG_SIZE}"
            "&meta=field.shortName,field.type,field.description"
            "&hasOptions=true"
            "&raw=1"
        )
        api_hdrs = {
            **hdrs,
            "Referer":      base_url,
            "X-XSRF-Token": xsrf,
            "Accept":       "application/json, text/plain, */*",
        }

        import datetime as _dt

        all_raw: list = []
        page          = 1
        diag_step2    = {}

        while len(all_raw) < _TARGET:
            page_qs  = f"{base_qs}&page={page}"
            api_url  = (f"https://www.barchart.com/proxies/core-api/v1"
                        f"/options/get?{page_qs}")
            r2       = await _http_client.get(api_url, headers=api_hdrs,
                                              cookies=cookies,
                                              follow_redirects=True, timeout=30.0)
            ct   = r2.headers.get("content-type", "")
            body = r2.text
            diag_step2 = {"status": r2.status_code, "content_type": ct,
                          "page": page, "body_preview": body[:300]}

            if r2.status_code != 200:
                msg = f"API page {page} returned {r2.status_code}. {diag_step2}"
                print(f"[UO] {msg}")
                return {"ok": False, "rows": 0, "error": "bad_status", "detail": msg}

            try:
                payload = r2.json()
            except Exception:
                msg = f"Page {page} not JSON. {diag_step2}"
                print(f"[UO] {msg}")
                return {"ok": False, "rows": 0, "error": "not_json", "detail": msg}

            page_records = payload.get("data", [])
            if not page_records:
                break   # no more data

            all_raw.extend(page_records)

            # Stop early if the API returned fewer rows than requested (last page)
            if len(page_records) < _PG_SIZE:
                break

            page += 1

        if not all_raw:
            msg = f"All pages empty. keys={list(payload.keys())} {diag_step2}"
            print(f"[UO] {msg}")
            return {"ok": False, "rows": 0, "error": "empty_data", "detail": msg}

        rows = []
        for rec in all_raw[:_TARGET]:
            r = rec.get("raw", rec)   # unwrap {"raw": {...}} if present

            # Parse OCC symbol "AAPL|20260515|111.00C" as fallback source
            occ       = str(r.get("symbol", ""))
            occ_parts = occ.split("|")
            occ_tick  = occ_parts[0] if occ_parts else ""
            occ_exp   = occ_parts[1] if len(occ_parts) > 1 else ""
            occ_st    = occ_parts[2] if len(occ_parts) > 2 else ""   # e.g. "111.00C"
            occ_type  = ("Call" if occ_st.endswith("C") else
                         "Put"  if occ_st.endswith("P") else "")

            # Format expiration: YYYYMMDD → YYYY-MM-DD
            exp_raw = str(r.get("expirationDate", "") or occ_exp)
            if len(exp_raw) == 8 and exp_raw.isdigit():
                exp_fmt = f"{exp_raw[:4]}-{exp_raw[4:6]}-{exp_raw[6:]}"
            else:
                exp_fmt = exp_raw

            # Format trade time: unix ts → HH:MM
            try:
                ts     = int(r.get("tradeTime", 0) or 0)
                time_s = _dt.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""
            except Exception:
                time_s = str(r.get("tradeTime", ""))

            rows.append({
                "Symbol":          str(r.get("baseSymbol",                  occ_tick)),
                "Put/Call":        occ_type or str(r.get("symbolType",      "")),
                "Strike":          str(r.get("strikePrice",                 "")),
                "Expiration Date": exp_fmt,
                "Volume":          str(r.get("volume",                      "")),
                "Open Interest":   str(r.get("openInterest",                "")),
                "Vol/OI Ratio":    str(r.get("volumeOpenInterestRatio",     "")),
                "Bid":             str(r.get("bidPrice",                    "")),
                "Ask":             str(r.get("askPrice",                    "")),
                "Last Price":      str(r.get("lastPrice",                   "")),
                "IV":              str(r.get("weightedImpliedVolatility",   "")),
                "Time":            time_s,
                "Base Price":      str(r.get("baseLastPrice",               "")),
                "Delta":           str(r.get("delta",                       "")),
                "Moneyness":       str(r.get("moneyness",                   "")),
                "DTE":             str(r.get("daysToExpiration",            "")),
            })

        # Derive as_of from the latest tradeTime in the data (ET date),
        # so a catch-up scrape on 4/10 morning correctly shows "as of 2025-04-09".
        try:
            from zoneinfo import ZoneInfo as _ZI
            max_ts = max(
                (int(rec.get("raw", rec).get("tradeTime", 0) or 0)
                 for rec in all_raw[:_TARGET]),
                default=0,
            )
            as_of = (
                _dt.datetime.fromtimestamp(max_ts, tz=_ZI("America/New_York"))
                            .strftime("%Y-%m-%d")
                if max_ts else today
            )
        except Exception:
            as_of = today

        _unusual_options["data"]  = rows
        _unusual_options["as_of"] = as_of
        _uo_save_db(as_of, rows)
        print(f"[UO] scraped {len(rows)} rows for {as_of} (fetched {today})")
        return {"ok": True, "rows": len(rows), "error": None,
                "detail": f"scraped {len(rows)} rows for {as_of}"}

    except Exception as e:
        msg = str(e)
        print(f"[UO] scrape exception: {msg}")
        return {"ok": False, "rows": 0, "error": "exception", "detail": msg}


async def _uo_scheduler() -> None:
    """
    On startup: seed in-memory store from DB.
    Then: scrape Barchart once per weekday at or after 4:05 PM ET.
    """
    from zoneinfo import ZoneInfo

    _uo_load_db()
    last_date: str | None = _unusual_options.get("as_of")

    # Scrape on startup if data is missing OR stale (a previous day).
    # Render free tier sleeps between requests — the scheduler loop dies during
    # sleep, so the 4:05 PM scrape can be missed.  On the next wake-up we catch
    # up immediately rather than waiting until 4:05 PM again.
    et_now    = datetime.now(ZoneInfo("America/New_York"))
    today_str = et_now.strftime("%Y-%m-%d")
    if last_date is None or last_date != today_str:
        result = await _scrape_unusual_options()
        print(f"[UO] startup scrape (prev={last_date}): {result}")
        last_date = _unusual_options.get("as_of")

    while True:
        try:
            et        = datetime.now(ZoneInfo("America/New_York"))
            today_str = et.strftime("%Y-%m-%d")
            # Fire on weekdays (Mon–Fri) at 4:05 PM ET or later
            if (et.weekday() < 5
                    and (et.hour > 16 or (et.hour == 16 and et.minute >= 5))
                    and last_date != today_str):
                result = await _scrape_unusual_options()
                if result.get("ok"):
                    last_date = _unusual_options.get("as_of") or today_str
        except Exception as e:
            print(f"[UO] scheduler error: {e}")
        await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Earnings Calendar — DB helpers, scraper, and scheduler
# ---------------------------------------------------------------------------

def _earn_save_db(trade_date: str, rows: list) -> None:
    try:
        with _db_conn() as conn:
            _execute(conn,
                "INSERT INTO earnings_calendar (trade_date, data, fetched_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (trade_date) DO UPDATE SET "
                "data = excluded.data, fetched_at = excluded.fetched_at",
                (trade_date, json.dumps(rows),
                 datetime.now(timezone.utc).isoformat()))
            conn.commit()
    except Exception as e:
        print(f"[EARN] db save error: {e}")


def _earn_load_db() -> None:
    try:
        with _db_conn() as conn:
            cur = _execute(conn,
                "SELECT trade_date, data FROM earnings_calendar "
                "ORDER BY trade_date DESC LIMIT 1")
            row = cur.fetchone()
        if row:
            _earnings["data"]  = json.loads(row["data"])
            _earnings["as_of"] = row["trade_date"]
            print(f"[EARN] loaded {len(_earnings['data'])} rows "
                  f"for {_earnings['as_of']} from DB")
    except Exception as e:
        print(f"[EARN] db load error: {e}")


async def _scrape_earnings() -> dict:
    """Scrape Barchart upcoming earnings (next trading day) via their internal API."""
    from urllib.parse import unquote
    from datetime import date as _date
    import datetime as _dt

    base_url = "https://www.barchart.com/options/upcoming-earnings"
    today    = _date.today().strftime("%Y-%m-%d")

    hdrs = {
        "User-Agent":      ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"),
        "Accept":          ("text/html,application/xhtml+xml,application/xml;"
                            "q=0.9,image/webp,*/*;q=0.8"),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    }

    try:
        # Step 1 — land on the page to collect XSRF-TOKEN
        r1   = await _http_client.get(base_url, headers=hdrs,
                                      follow_redirects=True, timeout=30.0)
        xsrf = unquote(r1.cookies.get("XSRF-TOKEN", ""))
        cookies = dict(r1.cookies)

        if not xsrf:
            msg = f"XSRF-TOKEN not found. cookies={list(cookies.keys())}"
            print(f"[EARN] {msg}")
            return {"ok": False, "rows": 0, "error": "no_xsrf", "detail": msg}

        # Step 2 — call the internal API (exact params from browser Network tab)
        qs = (
            "lists=stocks.optionable.upcoming_earnings.1d.us"
            "&fields=symbol,symbolName,nextEarningsDate,timeCode,lastPrice,"
            "priceChange,percentChange,optionsImpliedVolatilityRank1y,"
            "nearestImpliedMove,nearestImpliedMovePercent,optionsTotalVolume,tradeTime"
            "&orderBy=nextEarningsDate&orderDir=asc"
            "&page=1&limit=200"
            "&hasOptions=true"
            "&raw=1"
        )
        api_url  = f"https://www.barchart.com/proxies/core-api/v1/quotes/get?{qs}"
        api_hdrs = {
            **hdrs,
            "Referer":      base_url,
            "X-XSRF-Token": xsrf,
            "Accept":       "application/json, text/plain, */*",
        }

        r2   = await _http_client.get(api_url, headers=api_hdrs, cookies=cookies,
                                      follow_redirects=True, timeout=30.0)
        body = r2.text
        diag = {"status": r2.status_code,
                "content_type": r2.headers.get("content-type", ""),
                "body_preview": body[:400]}

        if r2.status_code != 200:
            msg = f"API returned {r2.status_code}. {diag}"
            print(f"[EARN] {msg}")
            return {"ok": False, "rows": 0, "error": "bad_status", "detail": msg}

        try:
            payload = r2.json()
        except Exception:
            msg = f"Response not JSON. {diag}"
            print(f"[EARN] {msg}")
            return {"ok": False, "rows": 0, "error": "not_json", "detail": msg}

        raw_records = payload.get("data", [])
        if not raw_records:
            msg = f"data[] empty. keys={list(payload.keys())} {diag}"
            print(f"[EARN] {msg}")
            return {"ok": False, "rows": 0, "error": "empty_data", "detail": msg}

        rows = []
        for rec in raw_records:
            r = rec.get("raw", rec)

            # Format trade time: unix ts → HH:MM
            try:
                ts     = int(r.get("tradeTime", 0) or 0)
                time_s = _dt.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""
            except Exception:
                time_s = str(r.get("tradeTime", ""))

            rows.append({
                "Symbol":       str(r.get("symbol",                          "")),
                "Name":         str(r.get("symbolName",                      "")),
                "Date":         str(r.get("nextEarningsDate",                "")),
                "Time":         str(r.get("timeCode",                        "")),
                "Price":        str(r.get("lastPrice",                       "")),
                "Change %":     str(r.get("percentChange",                   "")),
                "IV Rank":      str(r.get("optionsImpliedVolatilityRank1y",  "")),
                "Impl Move":    str(r.get("nearestImpliedMove",              "")),
                "Impl Move %":  str(r.get("nearestImpliedMovePercent",       "")),
                "Opt Volume":   str(r.get("optionsTotalVolume",              "")),
                "Last Trade":   time_s,
            })

        _earnings["data"]  = rows
        _earnings["as_of"] = today
        _earn_save_db(today, rows)
        print(f"[EARN] scraped {len(rows)} rows for {today}")
        return {"ok": True, "rows": len(rows), "error": None,
                "detail": f"scraped {len(rows)} rows for {today}"}

    except Exception as e:
        msg = str(e)
        print(f"[EARN] scrape exception: {msg}")
        return {"ok": False, "rows": 0, "error": "exception", "detail": msg}


async def _earn_scheduler() -> None:
    """Load cached earnings from DB on startup, then scrape daily at 4:05 PM ET."""
    from zoneinfo import ZoneInfo

    _earn_load_db()
    last_date: str | None = _earnings.get("as_of")

    if last_date is None:
        result = await _scrape_earnings()
        print(f"[EARN] startup scrape: {result}")
        last_date = _earnings.get("as_of")

    while True:
        try:
            et        = datetime.now(ZoneInfo("America/New_York"))
            today_str = et.strftime("%Y-%m-%d")
            if (et.weekday() < 5
                    and (et.hour > 16 or (et.hour == 16 and et.minute >= 5))
                    and last_date != today_str):
                await _scrape_earnings()
                last_date = today_str
        except Exception as e:
            print(f"[EARN] scheduler error: {e}")
        await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Minimal JWT (HS256)
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))

def _make_token(username: str) -> str:
    header  = _b64url(b'{"alg":"HS256","typ":"JWT"}')
    payload = _b64url(json.dumps({"sub": username, "exp": int(time.time()) + TOKEN_SECONDS}).encode())
    sig     = _b64url(hmac.new(SECRET_KEY.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"

def _verify_token(token: str) -> str:
    try:
        header, payload_b64, sig = token.split(".")
    except ValueError:
        raise HTTPException(status_code=401, detail="Malformed token")
    expected = _b64url(hmac.new(SECRET_KEY.encode(), f"{header}.{payload_b64}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="Invalid token signature")
    data = json.loads(_b64url_decode(payload_b64))
    if data.get("exp", 0) < time.time():
        raise HTTPException(status_code=401, detail="Token expired")
    return data["sub"]

# ---------------------------------------------------------------------------
# Token verification for WebSocket handlers
# (raises ValueError instead of HTTPException — safe inside async WS context)
# ---------------------------------------------------------------------------

def _verify_token_ws(token: str) -> str:
    try:
        header, payload_b64, sig = token.split(".")
    except ValueError:
        raise ValueError("Malformed token")
    expected = _b64url(hmac.new(SECRET_KEY.encode(), f"{header}.{payload_b64}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid token")
    data = json.loads(_b64url_decode(payload_b64))
    if data.get("exp", 0) < time.time():
        raise ValueError("Token expired")
    return data["sub"]


# ---------------------------------------------------------------------------
# Chat — connection manager + DB helpers
# ---------------------------------------------------------------------------

class _ChatManager:
    """Tracks live WebSocket connections for the chat endpoint."""

    def __init__(self):
        self._conns: dict[str, "WebSocket"] = {}   # username -> ws

    def connect(self, username: str, ws) -> None:
        self._conns[username] = ws

    def disconnect(self, username: str) -> None:
        self._conns.pop(username, None)

    async def send_to(self, username: str, payload: dict) -> None:
        ws = self._conns.get(username)
        if ws:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                self.disconnect(username)

    async def broadcast(self, payload: dict) -> None:
        """Send to every connected user (used for #general messages)."""
        dead = []
        for uname, ws in list(self._conns.items()):
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(uname)
        for u in dead:
            self.disconnect(u)

    def online(self) -> list:
        return list(self._conns.keys())

    async def kick(self, username: str, reason: str = "kicked by admin") -> None:
        ws = self._conns.get(username)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "kicked", "reason": reason}))
                await ws.close()
            except Exception:
                pass
            self.disconnect(username)

    async def broadcast_room(self, room: str, payload: dict) -> None:
        """Broadcast to relevant users: DM participants only, everyone for public rooms."""
        if room.startswith("dm:"):
            for user in room[3:].split(":"):
                await self.send_to(user, payload)
        else:
            await self.broadcast(payload)


_chat = _ChatManager()


def _chat_save(room: str, from_user: str, text: str, ts: str) -> int | None:
    try:
        with _db_conn() as conn:
            cur = _execute(conn,
                "INSERT INTO chat_messages (room, from_user, text, ts) VALUES (?, ?, ?, ?)",
                (room, from_user, text, ts))
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        print(f"[CHAT] db save error: {e}")
        return None


def _get_admin_usernames() -> set:
    try:
        with _db_conn() as conn:
            cur = _execute(conn, "SELECT username FROM users WHERE is_admin = TRUE")
            return {r["username"] for r in cur.fetchall()}
    except Exception:
        return set()


def _chat_history(room: str, limit: int = 80) -> list:
    try:
        admins = _get_admin_usernames()
        with _db_conn() as conn:
            cur = _execute(conn,
                "SELECT id, from_user, text, ts FROM chat_messages "
                "WHERE room = ? ORDER BY id DESC LIMIT ?",
                (room, limit))
            rows = cur.fetchall()
        return [{"id": r["id"], "from": r["from_user"], "text": r["text"], "ts": r["ts"],
                 "admin": r["from_user"] in admins}
                for r in reversed(rows)]
    except Exception as e:
        print(f"[CHAT] db history error: {e}")
        return []


def _chat_delete_last(room: str, from_user: str | None = None) -> int | None:
    """Delete the most recent message in room (optionally filtered to from_user). Returns deleted id."""
    try:
        with _db_conn() as conn:
            if from_user:
                cur = _execute(conn,
                    "SELECT id FROM chat_messages WHERE room = ? AND from_user = ? ORDER BY id DESC LIMIT 1",
                    (room, from_user))
            else:
                cur = _execute(conn,
                    "SELECT id FROM chat_messages WHERE room = ? ORDER BY id DESC LIMIT 1",
                    (room,))
            row = cur.fetchone()
            if not row:
                return None
            msg_id = row["id"]
            _execute(conn, "DELETE FROM chat_messages WHERE id = ?", (msg_id,))
            conn.commit()
            return msg_id
    except Exception as e:
        print(f"[CHAT] db delete error: {e}")
        return None


def _chat_clear_room(room: str) -> bool:
    """Delete all messages in a room. Returns True on success."""
    try:
        with _db_conn() as conn:
            _execute(conn, "DELETE FROM chat_messages WHERE room = ?", (room,))
            conn.commit()
        return True
    except Exception as e:
        print(f"[CHAT] db clear error: {e}")
        return False


def _get_user_flags(username: str) -> tuple:
    """Returns (is_admin, is_muted, is_banned) for a user."""
    try:
        with _db_conn() as conn:
            cur = _execute(conn,
                "SELECT is_admin, is_muted, is_banned FROM users WHERE username = ?",
                (username,))
            row = cur.fetchone()
        if row:
            return bool(row["is_admin"]), bool(row["is_muted"]), bool(row["is_banned"])
    except Exception as e:
        print(f"[CHAT] get_user_flags error: {e}")
    return False, False, False


def _set_user_flag(username: str, flag: str, value: bool) -> None:
    allowed = {"is_admin", "is_muted", "is_banned"}
    if flag not in allowed:
        return
    try:
        with _db_conn() as conn:
            _execute(conn, f"UPDATE users SET {flag} = ? WHERE username = ?", (value, username))
            conn.commit()
    except Exception as e:
        print(f"[CHAT] set_user_flag error: {e}")


# ---------------------------------------------------------------------------
# FastAPI app — lifespan manages shared client and connection pool
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(timeout=10.0)
    _init_pool()
    _init_db()
    asyncio.create_task(_rest_bench_poll())
    asyncio.create_task(_finnhub_ws_bench())
    asyncio.create_task(_uo_scheduler())
    asyncio.create_task(_earn_scheduler())
    yield
    await _http_client.aclose()
    if _pool:
        _pool.closeall()


app       = FastAPI(title="Brodberg Server", version="1.0.0", lifespan=lifespan)
_security = HTTPBearer(auto_error=False)

def _current_user(creds: HTTPAuthorizationCredentials = Depends(_security)) -> str:
    if not creds:
        raise HTTPException(status_code=401, detail="Authorization header required")
    return _verify_token(creds.credentials)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class UpdateProfileRequest(BaseModel):
    bio:      str = ""
    location: str = ""

# ---------------------------------------------------------------------------
# User account routes
# ---------------------------------------------------------------------------

@app.post("/register", status_code=201)
def register(req: RegisterRequest):
    import bcrypt
    if not USERNAME_RE.match(req.username):
        raise HTTPException(status_code=400,
            detail="Username must be 3-20 chars, letters/numbers/underscore only")
    if len(req.password) < 6:
        raise HTTPException(status_code=400,
            detail="Password must be at least 6 characters")

    pw_hash  = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    created  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    username = req.username.lower()

    with _db_conn() as conn:
        try:
            _execute(conn,
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, pw_hash, created))
            conn.commit()
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(status_code=409, detail="Username already taken")
            raise HTTPException(status_code=500, detail="Database error")

    return {"message": f"Account '{req.username}' created successfully."}


@app.post("/login")
def login(req: LoginRequest):
    import bcrypt
    with _db_conn() as conn:
        cur = _execute(conn, "SELECT * FROM users WHERE username = ?", (req.username.lower(),))
        row = cur.fetchone()

    if not row or not bcrypt.checkpw(req.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = _make_token(row["username"])
    return {"token": token, "username": row["username"]}


@app.get("/profile/{username}")
def get_profile(username: str):
    with _db_conn() as conn:
        cur = _execute(conn,
            "SELECT username, created_at, bio, location, is_admin FROM users WHERE username = ?",
            (username.lower(),))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.get("/me")
def get_me(username: str = Depends(_current_user)):
    with _db_conn() as conn:
        cur = _execute(conn,
            "SELECT username, created_at, bio, location, is_admin FROM users WHERE username = ?",
            (username,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.put("/me")
def update_me(req: UpdateProfileRequest, username: str = Depends(_current_user)):
    with _db_conn() as conn:
        _execute(conn,
            "UPDATE users SET bio = ?, location = ? WHERE username = ?",
            (req.bio[:200], req.location[:100], username))
        conn.commit()
    return {"message": "Profile updated."}


@app.get("/api/chat/dm-threads")
def get_dm_threads(username: str = Depends(_current_user)):
    """Return all DM rooms the logged-in user has participated in."""
    with _db_conn() as conn:
        cur = _execute(conn,
            "SELECT DISTINCT room FROM chat_messages "
            "WHERE room LIKE ? OR room LIKE ? "
            "ORDER BY room",
            (f"dm:{username}:%", f"dm:%:{username}"))
        rows = cur.fetchall()
    return {"rooms": [row["room"] for row in rows]}

# ---------------------------------------------------------------------------
# Market data proxy routes
# Finnhub API key is injected server-side — clients send none.
# Responses are cached to reduce Finnhub API usage.
# ---------------------------------------------------------------------------

@app.get("/api/quote/{symbol}")
async def proxy_quote(symbol: str):
    key = f"quote:{symbol.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/quote",
                               params={"symbol": symbol.upper(), "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_QUOTE)
    return data


@app.get("/api/news")
async def proxy_news():
    cached = _cache_get("news")
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/news",
                               params={"category": "general", "token": FINNHUB_KEY})
    data = r.json()
    _cache_set("news", data, _TTL_NEWS)
    return data


@app.get("/api/company/{symbol}")
async def proxy_company_profile(symbol: str):
    key = f"company:{symbol.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/stock/profile2",
                               params={"symbol": symbol.upper(), "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_COMPANY)
    return data


@app.get("/api/yield-curve")
async def proxy_yield_curve():
    cached = _cache_get("yield-curve")
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/bond/yield_curve",
                               params={"code": "US", "token": FINNHUB_KEY})
    data = r.json()
    _cache_set("yield-curve", data, _TTL_YIELD_CURVE)
    return data


@app.get("/api/forex/rates")
async def proxy_forex_rates():
    cached = _cache_get("forex:rates")
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/forex/rates",
                               params={"base": "USD", "token": FINNHUB_KEY})
    data = r.json()
    _cache_set("forex:rates", data, _TTL_FOREX_RATES)
    return data


@app.get("/api/forex/candles")
async def proxy_forex_candles(symbol: str, resolution: str = "D",
                               from_ts: int = 0, to_ts: int = 0):
    key = f"forex:candles:{symbol}:{resolution}:{from_ts}:{to_ts}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/forex/candle",
                               params={"symbol": symbol, "resolution": resolution,
                                       "from": from_ts, "to": to_ts,
                                       "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_FOREX_CANDLES)
    return data


@app.get("/api/company-news/{symbol}")
async def proxy_company_news(symbol: str):
    from datetime import date, timedelta
    today     = date.today()
    from_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    to_date   = today.strftime("%Y-%m-%d")
    key       = f"company-news:{symbol.upper()}:{to_date}"
    cached    = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/company-news",
                               params={"symbol": symbol.upper(), "from": from_date,
                                       "to": to_date, "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_NEWS)
    return data


@app.get("/api/ipo")
async def proxy_ipo(frm: str = "", to: str = ""):
    from datetime import date, timedelta
    if not frm:
        frm = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not to:
        to  = (date.today() + timedelta(days=90)).strftime("%Y-%m-%d")
    key = f"ipo:{frm}:{to}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/calendar/ipo",
                               params={"from": frm, "to": to, "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_IPO)
    return data


@app.get("/api/peers/{symbol}")
async def proxy_peers(symbol: str):
    key = f"peers:{symbol.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/stock/peers",
                               params={"symbol": symbol.upper(), "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_PEERS)
    return data


@app.get("/api/executives/{symbol}")
async def proxy_executives(symbol: str):
    key = f"executives:{symbol.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/stock/executive",
                               params={"symbol": symbol.upper(), "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_EXECUTIVES)
    return data


@app.get("/api/dividends/{symbol}")
async def proxy_dividends(symbol: str):
    key = f"dividends:{symbol.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    from datetime import date, timedelta
    today     = date.today()
    from_date = (today - timedelta(days=365 * 10)).strftime("%Y-%m-%d")
    to_date   = today.strftime("%Y-%m-%d")
    r = await _http_client.get(f"{FH_BASE}/stock/dividend",
                               params={"symbol": symbol.upper(), "from": from_date,
                                       "to": to_date, "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_DIVIDENDS)
    return data


@app.get("/api/insider/{symbol}")
async def proxy_insider(symbol: str):
    key = f"insider:{symbol.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/stock/insider-transactions",
                               params={"symbol": symbol.upper(), "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_INSIDER)
    return data


@app.get("/api/sentiment/{symbol}")
async def proxy_sentiment(symbol: str):
    key = f"sentiment:{symbol.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    r = await _http_client.get(f"{FH_BASE}/news-sentiment",
                               params={"symbol": symbol.upper(), "token": FINNHUB_KEY})
    data = r.json()
    _cache_set(key, data, _TTL_SENTIMENT)
    return data


def _yf_df_to_list(df) -> list:
    """Convert a yfinance options DataFrame to a list of dicts, handling NaN safely."""
    import math
    def _sf(v):  # safe float
        try:
            f = float(v)
            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
        except Exception:
            return 0.0
    def _si(v):  # safe int
        try:
            f = float(v)
            return 0 if (math.isnan(f) or math.isinf(f)) else int(f)
        except Exception:
            return 0
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "strike":            _sf(row.get("strike")),
            "bid":               _sf(row.get("bid")),
            "ask":               _sf(row.get("ask")),
            "lastPrice":         _sf(row.get("lastPrice")),
            "volume":            _si(row.get("volume")),
            "openInterest":      _si(row.get("openInterest")),
            "impliedVolatility": _sf(row.get("impliedVolatility")),
            "inTheMoney":        bool(row.get("inTheMoney", False)),
        })
    return rows


@app.get("/api/options/{symbol}/dates")
async def proxy_option_dates(symbol: str):
    """Return the list of available expiry dates for a symbol (fast — no chain data)."""
    key = f"options-dates:{symbol.upper()}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    def _fetch():
        import yfinance as yf
        tk   = yf.Ticker(symbol.upper())
        exps = tk.options
        return {"ticker": symbol.upper(), "dates": list(exps) if exps else []}

    data = await asyncio.to_thread(_fetch)
    _cache_set(key, data, _TTL_OPTIONS)
    return data


@app.get("/api/options/{symbol}/chain/{expiry}")
async def proxy_option_chain(symbol: str, expiry: str):
    """Return calls + puts for a single expiry date."""
    key = f"options-chain:{symbol.upper()}:{expiry}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    def _fetch():
        import yfinance as yf
        tk    = yf.Ticker(symbol.upper())
        chain = tk.option_chain(expiry)
        return {
            "calls": _yf_df_to_list(chain.calls),
            "puts":  _yf_df_to_list(chain.puts),
        }

    data = await asyncio.to_thread(_fetch)
    _cache_set(key, data, _TTL_OPTIONS)
    return data


@app.get("/api/live/benchmarks")
async def live_benchmarks():
    return dict(_live_prices)


@app.get("/api/unusual-options")
async def get_unusual_options():
    return _unusual_options


@app.get("/api/earnings")
async def get_earnings():
    return _earnings


@app.get("/api/earnings/refresh")
async def refresh_earnings():
    result = await _scrape_earnings()
    sample = _earnings.get("data", [])[:3]
    return {**result, "sample": sample,
            "store": {"as_of": _earnings.get("as_of"),
                      "row_count": len(_earnings.get("data", []))}}


@app.get("/api/unusual-options/refresh")
async def refresh_unusual_options():
    """Trigger an immediate scrape and return full diagnostic info."""
    result = await _scrape_unusual_options()
    sample = _unusual_options.get("data", [])[:3]   # first 3 normalized rows
    return {**result, "sample": sample,
            "store": {"as_of": _unusual_options.get("as_of"),
                      "row_count": len(_unusual_options.get("data", []))}}



# ---------------------------------------------------------------------------
# AISStream WebSocket proxy
# The client sends the subscription (minus APIKey); server injects the key.
# ---------------------------------------------------------------------------

@app.websocket("/api/ship")
async def ship_ws_proxy(ws: WebSocket):
    await ws.accept()
    try:
        import websockets as _ws
        sub_text = await ws.receive_text()
        sub_data = json.loads(sub_text)
        sub_data["APIKey"] = AISSTREAM_KEY      # inject server-side key

        async with _ws.connect(AISSTREAM_URI) as ais:
            await ais.send(json.dumps(sub_data))
            async for raw in ais:
                msg = raw if isinstance(raw, str) else raw.decode("utf-8", errors="ignore")
                try:
                    await ws.send_text(msg)
                except WebSocketDisconnect:
                    break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Chat WebSocket endpoint
# Protocol:
#   Client → {"type": "auth",    "token": "..."}
#   Server ← {"type": "ready",   "username": "..."}
#   Client → {"type": "join",    "room": "general"}
#   Server ← {"type": "history", "room": "...", "messages": [...]}
#   Client → {"type": "message", "room": "general", "text": "..."}
#   Server ← {"type": "message", "room": "...", "from": "...", "text": "...", "ts": "HH:MM"}
#   Client → {"type": "dm",      "to": "bob", "text": "..."}
#   Server ← {"type": "dm",      "room": "dm:alice:bob", "from": "...", "to": "...",
#              "text": "...", "ts": "HH:MM"}   (sent to both parties if online)
# ---------------------------------------------------------------------------

@app.websocket("/api/chat")
async def chat_ws(ws: WebSocket):
    await ws.accept()
    username = None
    try:
        # ── Auth handshake ────────────────────────────────────────────────
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        auth = json.loads(raw)
        if auth.get("type") != "auth":
            await ws.send_text(json.dumps({"type": "error", "text": "Expected auth message"}))
            return
        try:
            username = _verify_token_ws(auth.get("token", ""))
        except ValueError as exc:
            await ws.send_text(json.dumps({"type": "error", "text": str(exc)}))
            return

        # Ban check — reject banned users immediately
        _, _, is_banned = _get_user_flags(username)
        if is_banned:
            await ws.send_text(json.dumps({"type": "error", "text": "You are banned from chat"}))
            return

        _chat.connect(username, ws)
        await ws.send_text(json.dumps({"type": "ready", "username": username}))

        # ── Message loop ──────────────────────────────────────────────────
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")

            if mtype == "join":
                room = msg.get("room", "general")
                # DM rooms: validate the requesting user is a participant
                if room.startswith("dm:"):
                    parts = room[3:].split(":")
                    if username not in parts:
                        await ws.send_text(json.dumps(
                            {"type": "error", "text": "Not a participant in this DM"}))
                        continue
                history = _chat_history(room)
                await ws.send_text(json.dumps(
                    {"type": "history", "room": room, "messages": history}))

            elif mtype == "message":
                is_admin, is_muted, _ = _get_user_flags(username)
                if is_muted:
                    await ws.send_text(json.dumps({"type": "error", "text": "You are muted"}))
                    continue
                room = msg.get("room", "general")
                text = msg.get("text", "").strip()[:500]
                if not text:
                    continue
                ts     = datetime.now(timezone.utc).isoformat(timespec="seconds")
                msg_id = _chat_save(room, username, text, ts)
                await _chat.broadcast(
                    {"type": "message", "room": room, "id": msg_id,
                     "from": username, "text": text, "ts": ts, "admin": is_admin})

            elif mtype == "dm":
                is_admin, is_muted, _ = _get_user_flags(username)
                if is_muted:
                    await ws.send_text(json.dumps({"type": "error", "text": "You are muted"}))
                    continue
                to   = msg.get("to", "").strip().lower()
                text = msg.get("text", "").strip()[:500]
                if not text or not to:
                    continue
                # Verify recipient exists
                with _db_conn() as conn:
                    cur = _execute(conn,
                        "SELECT username FROM users WHERE username = ?", (to,))
                    row = cur.fetchone()
                if not row:
                    await ws.send_text(json.dumps(
                        {"type": "error", "text": f"User '{to}' not found"}))
                    continue
                room    = "dm:" + ":".join(sorted([username, to]))
                ts      = datetime.now(timezone.utc).isoformat(timespec="seconds")
                msg_id  = _chat_save(room, username, text, ts)
                payload = {"type": "dm", "room": room, "id": msg_id,
                           "from": username, "to": to, "text": text, "ts": ts, "admin": is_admin}
                await _chat.send_to(username, payload)
                if to != username:
                    await _chat.send_to(to, payload)

            elif mtype == "admin":
                is_admin, _, _ = _get_user_flags(username)
                if not is_admin:
                    await ws.send_text(json.dumps({"type": "error", "text": "Not authorized"}))
                    continue

                action      = msg.get("action", "")
                target      = msg.get("target", "").strip().lower()
                room        = msg.get("room", "general")
                target_user = msg.get("target_user")  # optional, for delete

                if action in ("mute", "unmute") and target:
                    _set_user_flag(target, "is_muted", action == "mute")
                    label = "muted" if action == "mute" else "unmuted"
                    await ws.send_text(json.dumps(
                        {"type": "system", "room": room, "text": f"[admin] {target} {label}"}))

                elif action in ("ban", "unban") and target:
                    _set_user_flag(target, "is_banned", action == "ban")
                    if action == "ban":
                        await _chat.kick(target, reason="You have been banned")
                    label = "banned" if action == "ban" else "unbanned"
                    await ws.send_text(json.dumps(
                        {"type": "system", "room": room, "text": f"[admin] {target} {label}"}))

                elif action == "kick" and target:
                    await _chat.kick(target, reason="You have been kicked")
                    await ws.send_text(json.dumps(
                        {"type": "system", "room": room, "text": f"[admin] {target} kicked"}))

                elif action == "delete":
                    msg_id = _chat_delete_last(room, target_user or None)
                    if msg_id:
                        await _chat.broadcast_room(room,
                            {"type": "message_deleted", "room": room, "msg_id": msg_id})
                    else:
                        await ws.send_text(json.dumps(
                            {"type": "error", "text": "No message found to delete"}))

                elif action == "clear":
                    if _chat_clear_room(room):
                        await _chat.broadcast_room(room,
                            {"type": "room_cleared", "room": room})
                    else:
                        await ws.send_text(json.dumps(
                            {"type": "error", "text": "Failed to clear room"}))

    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception:
        pass
    finally:
        if username:
            _chat.disconnect(username)
