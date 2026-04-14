"""
chat_data.py
------------
Background WebSocket thread for the CHAT command.

Maintains a persistent connection to wss://.../api/chat, auto-reconnects
on drop, and stores received messages in a per-room list.

Public API
----------
  connect(initial_rooms)  — start / reuse the WS thread (detects user change)
  disconnect()            — stop the WS thread
  send(payload)           — enqueue a JSON payload to send
  join_room(room)         — request history for a room
  get_messages(room)      — return a snapshot list for a room
  get_status()            — "idle" | "connecting" | "live" | "reconnecting" | "error: ..."

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

_messages: dict[str, list] = {}
_messages_lock = threading.Lock()

HISTORY_LIMIT = 200
_STOP_SENTINEL = object()


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
# Thread / loop state
# ---------------------------------------------------------------------------

_send_queue: asyncio.Queue | None = None
_loop:       asyncio.AbstractEventLoop | None = None
_thread:     threading.Thread | None = None
_stop:       threading.Event = threading.Event()
_connected_user: str | None = None   # username of the active WS session


# ---------------------------------------------------------------------------
# Public send API
# ---------------------------------------------------------------------------

def send(payload: dict) -> None:
    """Enqueue a payload for sending.  Thread-safe; call from any thread."""
    global _loop, _send_queue
    if _loop and _send_queue:
        _loop.call_soon_threadsafe(_send_queue.put_nowait, payload)
    else:
        room = payload.get("room", "general")
        _append(room, {
            "from": "system",
            "text": "[not connected — try reconnecting]",
            "ts":   "",
        })


def join_room(room: str) -> None:
    send({"type": "join", "room": room})


# ---------------------------------------------------------------------------
# Async internals
# ---------------------------------------------------------------------------

async def _sender(ws, q: asyncio.Queue) -> None:
    while True:
        item = await q.get()
        if item is _STOP_SENTINEL:
            break
        try:
            await ws.send(json.dumps(item))
        except Exception:
            break


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

    # ── Auto-reconnect loop ───────────────────────────────────────────────
    while not stop_event.is_set():
        _set_status("connecting")
        try:
            async with websockets.connect(uri, ping_interval=20) as ws:

                # Auth
                await ws.send(json.dumps({"type": "auth", "token": token}))
                resp = json.loads(await ws.recv())
                if resp.get("type") == "error":
                    _set_status(f"error: {resp.get('text', 'auth failed')}")
                    return   # auth failure is permanent — don't retry

                _set_status("live")

                # Outbound queue + sender task (fresh each connection)
                _send_queue = asyncio.Queue()
                sender_task = asyncio.create_task(_sender(ws, _send_queue))

                # Join rooms
                for room in initial_rooms:
                    await ws.send(json.dumps({"type": "join", "room": room}))

                # Receive loop
                try:
                    async for raw in ws:
                        if stop_event.is_set():
                            return

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
                                "id":    msg.get("id"),
                                "from":  msg.get("from", "?"),
                                "text":  msg.get("text", ""),
                                "ts":    msg.get("ts",   ""),
                                "admin": msg.get("admin", False),
                            })

                        elif mtype == "message_deleted":
                            room   = msg.get("room", "general")
                            msg_id = msg.get("msg_id")
                            if msg_id is not None:
                                with _messages_lock:
                                    if room in _messages:
                                        _messages[room] = [
                                            m for m in _messages[room]
                                            if m.get("id") != msg_id
                                        ]

                        elif mtype == "kicked":
                            reason = msg.get("reason", "kicked")
                            _append("general", {
                                "from": "system",
                                "text": f"[{reason}]",
                                "ts":   "",
                            })
                            _set_status(f"error: {reason}")
                            return   # stop receiving

                        elif mtype == "system":
                            room = msg.get("room", "general")
                            _append(room, {
                                "from": "system",
                                "text": msg.get("text", ""),
                                "ts":   "",
                            })

                        elif mtype == "error":
                            _append("general", {
                                "from": "system",
                                "text": f"[error] {msg.get('text', '')}",
                                "ts":   "",
                            })

                finally:
                    _send_queue.put_nowait(_STOP_SENTINEL)
                    await sender_task

        except Exception:
            pass   # fall through to reconnect delay

        finally:
            _send_queue = None

        if stop_event.is_set():
            break

        # Brief pause before reconnecting — signals the user
        _set_status("reconnecting")
        await asyncio.sleep(3)


# ---------------------------------------------------------------------------
# Public lifecycle API
# ---------------------------------------------------------------------------

def connect(initial_rooms: list | None = None) -> None:
    """
    Start the WS thread.  If already connected as the same user, reuses the
    existing connection.  If the logged-in user has changed, disconnects first
    so the new session is authenticated correctly.
    """
    global _loop, _thread, _stop, _connected_user

    token = brodberg_session.get_token()
    me    = brodberg_session.get_current_user()

    if not token or not me:
        _set_status("error: not logged in")
        return

    # Reuse existing connection only if it's the same user
    if _thread and _thread.is_alive():
        if _connected_user == me:
            return
        # Different user — tear down the old session first
        disconnect()
        _thread.join(timeout=3)

    _connected_user = me
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
    global _loop, _thread, _connected_user
    _stop.set()
    if _loop and _send_queue:
        _loop.call_soon_threadsafe(_send_queue.put_nowait, _STOP_SENTINEL)
    _connected_user = None
    _thread = None
    _loop   = None
    _set_status("idle")
