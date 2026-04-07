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
        conn.commit()


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
            "SELECT username, created_at, bio, location FROM users WHERE username = ?",
            (username.lower(),))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.get("/me")
def get_me(username: str = Depends(_current_user)):
    with _db_conn() as conn:
        cur = _execute(conn,
            "SELECT username, created_at, bio, location FROM users WHERE username = ?",
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


@app.get("/api/live/benchmarks")
async def live_benchmarks():
    return dict(_live_prices)


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
