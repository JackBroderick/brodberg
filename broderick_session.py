"""
broderick_session.py
-------------------
Manages the local login session for Broderick Terminal.

Session is stored at  ~/.broderick/session.json  and contains:
  {
    "username": "alice",
    "token":    "eyJ..."
  }

The server URL is also stored here so the user only has to configure it once:
  {
    "server_url": "https://your-server.com"
  }

Public API:
  load_session()           -> dict
  save_session(data)       -> None
  clear_session()          -> None
  get_token()              -> str | None
  get_current_user()       -> str | None
  is_logged_in()           -> bool
  get_server_url()         -> str
  set_server_url(url)      -> None
"""

import json
import os

_SESSION_DIR  = os.path.join(os.path.expanduser("~"), ".broderick")
_SESSION_FILE = os.path.join(_SESSION_DIR, "session.json")

_DEFAULT_SERVER = "https://brodberg.onrender.com"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _ensure_dir() -> None:
    os.makedirs(_SESSION_DIR, exist_ok=True)


def load_session() -> dict:
    try:
        with open(_SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_session(data: dict) -> None:
    _ensure_dir()
    current = load_session()
    current.update(data)
    with open(_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)


def clear_session() -> None:
    """Remove auth credentials but keep server_url config."""
    session = load_session()
    session.pop("username", None)
    session.pop("token", None)
    _ensure_dir()
    with open(_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def get_token() -> str | None:
    return load_session().get("token")


def get_current_user() -> str | None:
    return load_session().get("username")


def is_logged_in() -> bool:
    s = load_session()
    return bool(s.get("token") and s.get("username"))


def get_is_admin() -> bool:
    return bool(load_session().get("is_admin", False))


# ---------------------------------------------------------------------------
# Server URL helpers
# ---------------------------------------------------------------------------

def get_server_url() -> str:
    return load_session().get("server_url", _DEFAULT_SERVER).rstrip("/")
