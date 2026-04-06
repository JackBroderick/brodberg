"""
commands/cmd_auth.py
--------------------
Implements user account commands for the Brodberg online server.

Commands:
  REGISTER <username> <password>   — create a new account
  LOGIN <username> <password>      — sign in and save session
  LOGOUT                           — clear local session
  PROFILE                          — show your own profile
  PROFILE <username>               — show another user's profile
  SERVER <url>                     — set the server URL

fetch(parts)                  -> cache dict
render(stdscr, cache, colors) -> None
"""

import curses
import requests
import brodberg_session

TIMEOUT = 8     # seconds per HTTP request


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _url(path: str) -> str:
    return f"{brodberg_session.get_server_url()}{path}"


def _auth_headers() -> dict:
    token = brodberg_session.get_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _ok(message: str) -> dict:
    return {"status": "ok", "message": message, "user": None}


def _err(message: str) -> dict:
    return {"status": "error", "message": message, "user": None}


def _user_result(message: str, user: dict) -> dict:
    return {"status": "ok", "message": message, "user": user}


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    """
    parts[0] is always the uppercased command name.
    parts[1:] preserve original casing (so passwords work).
    """
    action = parts[0]   # REGISTER | LOGIN | LOGOUT | PROFILE | SERVER

    # ── SERVER <url> ─────────────────────────────────────────────────────────
    if action == "SERVER":
        if len(parts) < 2:
            return _err("Usage:  SERVER <url>   e.g.  SERVER https://myapp.com")
        url = parts[1]
        brodberg_session.set_server_url(url)
        return _ok(f"Server URL set to  {url}")

    # ── LOGOUT ────────────────────────────────────────────────────────────────
    if action == "LOGOUT":
        if not brodberg_session.is_logged_in():
            return _err("You are not logged in.")
        user = brodberg_session.get_current_user()
        brodberg_session.clear_session()
        return _ok(f"Logged out.  Goodbye, {user}.")

    # ── REGISTER <username> <password> ────────────────────────────────────────
    if action == "REGISTER":
        if len(parts) < 3:
            return _err("Usage:  REGISTER <username> <password>")
        username, password = parts[1], parts[2]
        try:
            r = requests.post(
                _url("/register"),
                json={"username": username, "password": password},
                timeout=TIMEOUT,
            )
            data = r.json()
            if r.status_code == 201:
                return _ok(data.get("message", "Account created."))
            return _err(data.get("detail", "Registration failed."))
        except requests.exceptions.ConnectionError:
            return _err(f"Could not connect to server.  Is it running?  ({brodberg_session.get_server_url()})")
        except Exception as e:
            return _err(f"Error: {e}")

    # ── LOGIN <username> <password> ───────────────────────────────────────────
    if action == "LOGIN":
        if len(parts) < 3:
            return _err("Usage:  LOGIN <username> <password>")
        username, password = parts[1], parts[2]
        try:
            r = requests.post(
                _url("/login"),
                json={"username": username, "password": password},
                timeout=TIMEOUT,
            )
            data = r.json()
            if r.status_code == 200:
                brodberg_session.save_session({
                    "username": data["username"],
                    "token":    data["token"],
                })
                return _ok(f"Logged in as  {data['username']}.")
            return _err(data.get("detail", "Login failed."))
        except requests.exceptions.ConnectionError:
            return _err(f"Could not connect to server.  ({brodberg_session.get_server_url()})")
        except Exception as e:
            return _err(f"Error: {e}")

    # ── PROFILE [username] ────────────────────────────────────────────────────
    if action == "PROFILE":
        # PROFILE → show own profile (requires login)
        if len(parts) == 1:
            if not brodberg_session.is_logged_in():
                return _err("Not logged in.  Use  LOGIN <username> <password>.")
            try:
                r = requests.get(_url("/me"), headers=_auth_headers(), timeout=TIMEOUT)
                data = r.json()
                if r.status_code == 200:
                    return _user_result("", data)
                return _err(data.get("detail", "Could not fetch profile."))
            except requests.exceptions.ConnectionError:
                return _err(f"Could not connect to server.  ({brodberg_session.get_server_url()})")
            except Exception as e:
                return _err(f"Error: {e}")

        # PROFILE <username> → look up someone else
        target = parts[1]
        try:
            r = requests.get(_url(f"/profile/{target}"), timeout=TIMEOUT)
            data = r.json()
            if r.status_code == 200:
                return _user_result("", data)
            return _err(data.get("detail", "User not found."))
        except requests.exceptions.ConnectionError:
            return _err(f"Could not connect to server.  ({brodberg_session.get_server_url()})")
        except Exception as e:
            return _err(f"Error: {e}")

    return _err(f"Unknown auth action: {action}")


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    status  = cache.get("status", "error")
    message = cache.get("message", "")
    user    = cache.get("user")

    def put(row, text, color, bold=False):
        attr = color | (curses.A_BOLD if bold else 0)
        try:
            stdscr.attron(attr)
            stdscr.addstr(row, 0, text)
            stdscr.attroff(attr)
        except Exception:
            pass

    sep = f"  {'─' * 40}"
    r   = 4

    put(r, sep, colors["dim"])

    if status == "error":
        put(r + 1, f"  {message}", colors["negative"])
        put(r + 2, sep, colors["dim"])
        return

    # ── Profile view ─────────────────────────────────────────────────────────
    if user:
        is_me = (user.get("username") == brodberg_session.get_current_user())
        label = "YOUR PROFILE" if is_me else f"PROFILE  —  {user['username'].upper()}"

        put(r + 1, f"  {label}",                                colors["orange"],  bold=True)
        put(r + 2, sep,                                          colors["dim"])
        put(r + 3, f"   Username  : {user.get('username','')}",  colors["orange"])
        put(r + 4, f"   Member since : {user.get('created_at','')}", colors["orange"])
        put(r + 5, f"   Location   : {user.get('location') or '—'}", colors["orange"])
        put(r + 6, f"   Bio        : {user.get('bio') or '—'}",  colors["orange"])
        put(r + 7, sep,                                          colors["dim"])
        return

    # ── Simple message (LOGIN OK, REGISTER OK, LOGOUT, SERVER set) ───────────
    put(r + 1, f"  {message}", colors["positive"], bold=True)

    # Show who's currently logged in as a footer hint
    if brodberg_session.is_logged_in():
        put(r + 2, sep, colors["dim"])
        put(r + 3, f"  Logged in as  {brodberg_session.get_current_user()}", colors["dim"])

    put(r + 4 if brodberg_session.is_logged_in() else r + 2, sep, colors["dim"])
