"""
chat_data.py
------------
Background WebSocket thread for the CHAT command.

Maintains a persistent connection to wss://.../api/chat and stores
received messages in a per-room list.  The curses render loop reads
from these lists via get_messages(); messages are sent via send().

Send mechanism
--------------
Instead of asyncio.run_coroutine_threadsafe (which can silently drop
messages if the loop is blocked), we use an asyncio.Queue bridged with
loop.call_soon_threadsafe.  A dedicated sender task inside the event
loop drains the queue and calls ws.send() — keeping all WebSocket I/O
inside the event loop thread.

Public API
----------
  connect(initial_rooms)  — start the WS thread (idempotent)
  disconnect()            — stop the WS thread
  send(payload)           — enqueue a JSON payload to send
  join_room(room)         — request history for a room
  get_messages(room)      — return a snapshot list for a room
  get_status()            — "idle" | "connecting" | "live" | "error: ..."

Room name conventions
---------------------
  "general"          — public chat room
  "dm:alice:bob"     — DM between alice and bob (names sorted alphabetically)
"""

import json
import os
import threading
import asyncio
import time

import brodberg_session

# ---------------------------------------------------------------------------
# Debug log  — writes to ~/brodberg_chat.log so we can trace without curses
# ---------------------------------------------------------------------------

_LOG = os.path.expanduser("~/brodberg_chat.log")


def _dbg(msg: str) -> None:
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')}  {msg}\n")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Per-room message store
# ---------------------------------------------------------------------------

_messages: dict[str, list] = {}   # room -> [{"from": str, "text": str, "ts": str}]
_messages_lock = threading.Lock()

HISTORY_LIMIT = 200   # max messages kept in memory per room

_STOP_SENTINEL = object()   # signals the sender task to exit


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
# Outbound queue
# Populated from the main (curses) thread via send().
# Drained by the _sender task inside the event loop.
# ---------------------------------------------------------------------------

_send_queue: asyncio.Queue | None = None
_loop:  asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_stop:   threading.Event = threading.Event()


def send(payload: dict) -> None:
    """Enqueue a payload for sending.  Thread-safe; call from any thread."""
    global _loop, _send_queue
    _dbg(f"send() called: loop={bool(_loop)} queue={bool(_send_queue)} payload={payload}")
    if _loop and _send_queue:
        _loop.call_soon_threadsafe(_send_queue.put_nowait, payload)
        _dbg("send() → call_soon_threadsafe queued")
    else:
        room = payload.get("room", "general")
        _dbg(f"send() SKIPPED — loop={bool(_loop)} queue={bool(_send_queue)}")
        _append(room, {
            "from": "system",
            "text": f"[debug] send skipped — loop={bool(_loop)} queue={bool(_send_queue)}",
            "ts":   "",
        })


def join_room(room: str) -> None:
    send({"type": "join", "room": room})


# ---------------------------------------------------------------------------
# Async internals
# ---------------------------------------------------------------------------

async def _sender(ws, q: asyncio.Queue) -> None:
    """Drain the outbound queue and write each payload to the WebSocket."""
    _dbg("_sender task started")
    while True:
        item = await q.get()
        if item is _STOP_SENTINEL:
            _dbg("_sender received STOP sentinel — exiting")
            break
        _dbg(f"_sender dequeued item: {item}")
        try:
            await ws.send(json.dumps(item))
            _dbg("_sender ws.send() completed OK")
        except Exception as exc:
            _dbg(f"_sender ws.send() RAISED: {exc}")
            break   # connection lost — let the receiver detect and exit


async def _run(token: str, initial_rooms: list, stop_event: threading.Event) -> None:
    global _send_queue

    try:
        import websockets
    except ImportError:
        _set_status("error: pip install websockets")
        return

    server_url = brodberg_session.get_server_url()
    uri = (server_url
           .replace("https://", "wss://")
           .replace("http://",  "ws://")) + "/api/chat"

    _dbg(f"_run() connecting to {uri}")
    _set_status("connecting")

    try:
        async with websockets.connect(uri, ping_interval=20) as ws:

            # ── Auth ──────────────────────────────────────────────────────
            await ws.send(json.dumps({"type": "auth", "token": token}))
            resp = json.loads(await ws.recv())
            _dbg(f"_run() auth response: {resp}")
            if resp.get("type") == "error":
                _set_status(f"error: {resp.get('text', 'auth failed')}")
                return

            _set_status("live")
            _dbg("_run() status=live, creating send queue + sender task")

            # ── Create outbound queue and start sender task ────────────────
            _send_queue = asyncio.Queue()
            sender_task = asyncio.create_task(_sender(ws, _send_queue))

            # ── Join initial rooms ─────────────────────────────────────────
            for room in initial_rooms:
                await ws.send(json.dumps({"type": "join", "room": room}))

            # ── Receive loop ──────────────────────────────────────────────
            try:
                async for raw in ws:
                    if stop_event.is_set():
                        break

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    mtype = msg.get("type")

                    if mtype == "history":
                        room = msg.get("room", "general")
                        msgs = msg.get("messages", [])
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
                        _append("general", {
                            "from": "system",
                            "text": f"[error] {msg.get('text', '')}",
                            "ts":   "",
                        })

            finally:
                # Stop the sender task cleanly
                _send_queue.put_nowait(_STOP_SENTINEL)
                await sender_task

    except Exception as exc:
        _set_status(f"error: {exc}")
    finally:
        _send_queue = None


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

    if _thread and _thread.is_alive():
        return   # already running

    if initial_rooms is None:
        initial_rooms = ["general"]

    _stop = threading.Event()
    _loop = asyncio.new_event_loop()

    def _thread_main():
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(_run(token, list(initial_rooms), _stop))

    _thread = threading.Thread(target=_thread_main, daemon=True, name="chat-ws")
    _thread.start()


def disconnect() -> None:
    global _loop, _thread
    _stop.set()
    if _loop and _send_queue:
        _loop.call_soon_threadsafe(_send_queue.put_nowait, _STOP_SENTINEL)
    _thread = None
    _loop   = None
    _set_status("idle")
