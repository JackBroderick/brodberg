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
# Database
# ---------------------------------------------------------------------------

def _q(sql: str) -> str:
    if _DATABASE_URL:
        return sql.replace("?", "%s")
    return sql

def _get_conn():
    if _DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(_DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _execute(conn, sql: str, params: tuple = ()):
    cur = conn.cursor()
    cur.execute(_q(sql), params)
    return cur

def _init_db() -> None:
    conn = _get_conn()
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
    conn.close()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app       = FastAPI(title="Brodberg Server", version="1.0.0")
_security = HTTPBearer(auto_error=False)

def _current_user(creds: HTTPAuthorizationCredentials = Depends(_security)) -> str:
    if not creds:
        raise HTTPException(status_code=401, detail="Authorization header required")
    return _verify_token(creds.credentials)

@app.on_event("startup")
def startup():
    _init_db()

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

    conn = _get_conn()
    try:
        _execute(conn,
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, pw_hash, created))
        conn.commit()
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(status_code=409, detail="Username already taken")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        conn.close()

    return {"message": f"Account '{req.username}' created successfully."}


@app.post("/login")
def login(req: LoginRequest):
    import bcrypt
    conn = _get_conn()
    cur  = _execute(conn, "SELECT * FROM users WHERE username = ?", (req.username.lower(),))
    row  = cur.fetchone()
    conn.close()

    if not row or not bcrypt.checkpw(req.password.encode(), row["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = _make_token(row["username"])
    return {"token": token, "username": row["username"]}


@app.get("/profile/{username}")
def get_profile(username: str):
    conn = _get_conn()
    cur  = _execute(conn,
        "SELECT username, created_at, bio, location FROM users WHERE username = ?",
        (username.lower(),))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.get("/me")
def get_me(username: str = Depends(_current_user)):
    conn = _get_conn()
    cur  = _execute(conn,
        "SELECT username, created_at, bio, location FROM users WHERE username = ?",
        (username,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.put("/me")
def update_me(req: UpdateProfileRequest, username: str = Depends(_current_user)):
    conn = _get_conn()
    _execute(conn,
        "UPDATE users SET bio = ?, location = ? WHERE username = ?",
        (req.bio[:200], req.location[:100], username))
    conn.commit()
    conn.close()
    return {"message": "Profile updated."}

# ---------------------------------------------------------------------------
# Market data proxy routes
# Finnhub API key is injected server-side — clients send none.
# ---------------------------------------------------------------------------

@app.get("/api/quote/{symbol}")
async def proxy_quote(symbol: str):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{FH_BASE}/quote",
                             params={"symbol": symbol.upper(), "token": FINNHUB_KEY},
                             timeout=8)
    return r.json()


@app.get("/api/news")
async def proxy_news():
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{FH_BASE}/news",
                             params={"category": "general", "token": FINNHUB_KEY},
                             timeout=8)
    return r.json()


@app.get("/api/company/{symbol}")
async def proxy_company_profile(symbol: str):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{FH_BASE}/stock/profile2",
                             params={"symbol": symbol.upper(), "token": FINNHUB_KEY},
                             timeout=8)
    return r.json()


@app.get("/api/yield-curve")
async def proxy_yield_curve():
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{FH_BASE}/bond/yield_curve",
                             params={"code": "US", "token": FINNHUB_KEY},
                             timeout=10)
    return r.json()


@app.get("/api/forex/rates")
async def proxy_forex_rates():
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{FH_BASE}/forex/rates",
                             params={"base": "USD", "token": FINNHUB_KEY},
                             timeout=8)
    return r.json()


@app.get("/api/forex/candles")
async def proxy_forex_candles(symbol: str, resolution: str = "D",
                               from_ts: int = 0, to_ts: int = 0):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{FH_BASE}/forex/candle",
                             params={"symbol": symbol, "resolution": resolution,
                                     "from": from_ts, "to": to_ts,
                                     "token": FINNHUB_KEY},
                             timeout=10)
    return r.json()


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
