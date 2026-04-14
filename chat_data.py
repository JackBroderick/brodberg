"""
chat_data.py
------------
Background WebSocket thread for the CHAT command.

Maintains a persistent connection to wss://.../api/chat and stores
received messages in a per-room list.  The curses render loop reads
from these lists via get_messages(); messages are sent via send().

Public API
----------
  connect(initial_rooms)  — start the WS thread (idempotent)
  disconnect()            — stop the WS thread
  send(payload)           — send a JSON message to the server
  join_room(room)         — request history for a room (send join)
  get_messages(room)      — return a snapshot list for a room
  get_status()            — "idle" | "connecting" | "live" | "error: ..."

Room name conventions
---------------------
  "general"          — public chat room
  "dm:alice:bob"     — DM between alice and bob (names sorted alphabetically)
"""

import json
import threading
import asyncio

import brodberg_session

# ---------------------------------------------------------------------------
# Per-room message store
# ---------------------------------------------------------------------------

_messages: dict[str, list] = {}   # room -> [{"from": str, "text": str, "ts": str}]
_messages_lock = threading.Lock()

HISTORY_LIMIT = 200   # max messages kept in memory per room


def _append(room: str, entry: dict) -> None:
    with _messages_lock:
        if room not in _messages:
            _messages[room] = []
        _messages[room].append(entry)
        if len(_messages[room]) > HISTORY_LIMIT:
            _messages[room] = _messages[room][-HISTORY_LIMIT:]


def get_messages(room: str) -> list:
    with _messages_lock:
        return list(_messages.get(room, []))


def clear_messages(room: str) -> None:
    with _messages_lock:
        _messages.pop(room, None)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

_status      = "idle"
_status_lock = threading.Lock()


def _set_status(s: str) -> None:
    global _status
    with _status_lock:
        _status = s


def get_status() -> str:
    with _status_lock:
        return _status


# ---------------------------------------------------------------------------
# WebSocket thread internals
# ---------------------------------------------------------------------------

_loop:   asyncio.AbstractEventLoop | None = None
_ws_ref                                   = None   # websockets.ClientConnection
_thread: threading.Thread | None          = None
_stop:   threading.Event                  = threading.Event()


def send(payload: dict) -> None:
    """Send a JSON payload to the server.  No-op if not connected."""
    global _loop, _ws_ref
    if _loop and _ws_ref:
        asyncio.run_coroutine_threadsafe(_ws_ref.send(json.dumps(payload)), _loop)
    else:
        # Connection not ready — surface this as a visible system message
        room = payload.get("room", "general")
        _append(room, {
            "from": "system",
            "text": f"[debug] send skipped — loop={bool(_loop)} ws={bool(_ws_ref)}",
            "ts":   "",
        })


def join_room(room: str) -> None:
    send({"type": "join", "room": room})


# ---------------------------------------------------------------------------
# Async WS runner
# ---------------------------------------------------------------------------

async def _run(token: str, initial_rooms: list, stop_event: threading.Event) -> None:
    global _ws_ref

    try:
        import websockets
    except ImportError:
        _set_status("error: pip install websockets")
        return

    server_url = brodberg_session.get_server_url()
    uri = (server_url
           .replace("https://", "wss://")
           .replace("http://",  "ws://")) + "/api/chat"

    _set_status("connecting")

    try:
        async with websockets.connect(uri, ping_interval=20) as ws:
            _ws_ref = ws

            # ── Auth ──────────────────────────────────────────────────────
            await ws.send(json.dumps({"type": "auth", "token": token}))
            resp = json.loads(await ws.recv())
            if resp.get("type") == "error":
                _set_status(f"error: {resp.get('text', 'auth failed')}")
                return

            _set_status("live")

            # ── Join initial rooms ─────────────────────────────────────────
            for room in initial_rooms:
                await ws.send(json.dumps({"type": "join", "room": room}))

            # ── Receive loop ──────────────────────────────────────────────
            async for raw in ws:
                if stop_event.is_set():
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type")

                if mtype == "history":
                    room  = msg.get("room", "general")
                    msgs  = msg.get("messages", [])
                    with _messages_lock:
                        _messages[room] = msgs[-HISTORY_LIMIT:]

                elif mtype in ("message", "dm"):
                    room = msg.get("room", "general")
                    _append(room, {
                        "from": msg.get("from", "?"),
                        "text": msg.get("text", ""),
                        "ts":   msg.get("ts",   ""),
                    })

                elif mtype == "error":
                    # Surface server errors as a system message in general
                    _append("general", {
                        "from": "system",
                        "text": f"[error] {msg.get('text', '')}",
                        "ts":   "",
                    })

    except Exception as exc:
        _set_status(f"error: {exc}")
    finally:
        _ws_ref = None


# ---------------------------------------------------------------------------
# Public lifecycle API
# ---------------------------------------------------------------------------

def connect(initial_rooms: list | None = None) -> None:
    """Start the background WS thread.  Idempotent — safe to call repeatedly."""
    global _loop, _thread, _stop

    token = brodberg_session.get_token()
    if not token:
        _set_status("error: not logged in")
        return

    # Already running
    if _thread and _thread.is_alive():
        return

    if initial_rooms is None:
        initial_rooms = ["general"]

    _stop  = threading.Event()
    _loop  = asyncio.new_event_loop()

    def _thread_main():
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(_run(token, initial_rooms, _stop))

    _thread = threading.Thread(target=_thread_main, daemon=True, name="chat-ws")
    _thread.start()


def disconnect() -> None:
    global _loop, _thread
    _stop.set()
    if _loop:
        _loop.call_soon_threadsafe(_loop.stop)
    _thread = None
    _loop   = None
    _set_status("idle")
