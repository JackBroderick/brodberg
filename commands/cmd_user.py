"""
commands/cmd_user.py
--------------------
Implements the USER command — unified account management with interactive forms.

  USER                           — show own profile (or prompt if not logged in)
  USER login                     — interactive login form
  USER register                  — interactive registration form
  USER logout                    — sign out
  USER edit                      — interactive profile editor
  USER <username>                — view another user's profile

Interactive forms (login / register / edit) use the pane itself as a UI:
  ↑ ↓   navigate between fields
  Enter  submit
  Any printable key types into the focused field
  Backspace deletes

main.py routes all keystrokes to on_keypress() when cache["form_mode"] is True.

fetch(parts)                  -> cache dict
render(stdscr, cache, colors) -> None
on_keypress(key, cache)       -> cache dict
"""

import curses
import requests
import brodberg_session

TIMEOUT = 35

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _url(path: str) -> str:
    return f"{brodberg_session.get_server_url()}{path}"

def _auth_headers() -> dict:
    token = brodberg_session.get_token()
    return {"Authorization": f"Bearer {token}"} if token else {}

def _form(action: str, fields: list, message: str = "", status=None) -> dict:
    """Return a cache dict that puts the pane into form input mode."""
    return {
        "action":        action,
        "form_mode":     True,
        "focused_field": 0,
        "fields":        fields,
        "status":        status,
        "message":       message,
        "user":          None,
    }

def _result(action: str, message: str, status: str, user=None) -> dict:
    """Return a cache dict showing a result (no form)."""
    return {
        "action":        action,
        "form_mode":     False,
        "focused_field": 0,
        "fields":        [],
        "status":        status,
        "message":       message,
        "user":          user,
    }

# ---------------------------------------------------------------------------
# fetch — called once when the user presses Enter in the command bar
# ---------------------------------------------------------------------------

_DIR_VISIBLE = 18   # rows shown at once in USER DIR list


def fetch(parts: list) -> dict:
    action = parts[1].upper() if len(parts) > 1 else ""

    # ── USER dir ──────────────────────────────────────────────────────────
    if action == "DIR":
        if not brodberg_session.is_logged_in():
            return _result("DIR", "Not logged in.  Use  USER login  first.", "error")
        try:
            r = requests.get(_url("/api/users"), headers=_auth_headers(), timeout=TIMEOUT)
            if r.status_code == 200:
                users = r.json().get("users", [])
                return {
                    "action":      "DIR",
                    "form_mode":   False,
                    "users":       users,
                    "selected":    0,
                    "page_offset": 0,
                    "view":        "list",
                    "detail_user": None,
                    "error":       None,
                }
            return _result("DIR", r.json().get("detail", "Could not fetch directory."), "error")
        except Exception as e:
            return _result("DIR", str(e), "error")

    # ── USER login ────────────────────────────────────────────────────────
    if action == "LOGIN":
        return _form("LOGIN", [
            {"label": "Username", "value": "", "masked": False},
            {"label": "Password", "value": "", "masked": True},
        ])

    # ── USER register ─────────────────────────────────────────────────────
    if action == "REGISTER":
        return _form("REGISTER", [
            {"label": "Username", "value": "", "masked": False},
            {"label": "Password", "value": "", "masked": True},
        ])

    # ── USER logout ───────────────────────────────────────────────────────
    if action == "LOGOUT":
        if not brodberg_session.is_logged_in():
            return _result("LOGOUT", "You are not logged in.", "error")
        who = brodberg_session.get_current_user()
        brodberg_session.clear_session()
        return _result("LOGOUT", f"Logged out.  Goodbye, {who}.", "ok")

    # ── USER edit ─────────────────────────────────────────────────────────
    if action == "EDIT":
        if not brodberg_session.is_logged_in():
            return _result("EDIT", "Not logged in.  Use  USER login.", "error")
        try:
            r    = requests.get(_url("/me"), headers=_auth_headers(), timeout=TIMEOUT)
            data = r.json()
            if r.status_code != 200:
                return _result("EDIT", data.get("detail", "Could not fetch profile."), "error")
            return _form("EDIT", [
                {"label": "Bio     ", "value": data.get("bio",      ""), "masked": False},
                {"label": "Location", "value": data.get("location", ""), "masked": False},
            ])
        except Exception as e:
            return _result("EDIT", str(e), "error")

    # ── USER <username> — profile lookup ──────────────────────────────────
    if action and action not in ("LOGIN", "REGISTER", "LOGOUT", "EDIT"):
        try:
            r    = requests.get(_url(f"/profile/{action.lower()}"), timeout=TIMEOUT)
            data = r.json()
            if r.status_code == 200:
                return _result("PROFILE", "", "ok", user=data)
            return _result("PROFILE", data.get("detail", "User not found."), "error")
        except Exception as e:
            return _result("PROFILE", str(e), "error")

    # ── USER (no args) — show own profile or prompt ───────────────────────
    if not brodberg_session.is_logged_in():
        return _result("HOME", "Not logged in.  Use  USER login  or  USER register.", "error")
    try:
        r    = requests.get(_url("/me"), headers=_auth_headers(), timeout=TIMEOUT)
        data = r.json()
        if r.status_code == 200:
            return _result("PROFILE", "", "ok", user=data)
        return _result("HOME", data.get("detail", "Could not fetch profile."), "error")
    except Exception as e:
        return _result("HOME", str(e), "error")


# ---------------------------------------------------------------------------
# Form submission — called from on_keypress when Enter is pressed
# ---------------------------------------------------------------------------

def _submit(cache: dict) -> dict:
    action = cache.get("action")
    fields = cache.get("fields", [])

    if action == "LOGIN":
        username = fields[0]["value"].strip()
        password = fields[1]["value"]
        if not username or not password:
            return {**cache, "status": "error", "message": "Both fields are required."}
        try:
            r = requests.post(_url("/login"),
                              json={"username": username, "password": password},
                              timeout=TIMEOUT)
            data = r.json()
            if r.status_code == 200:
                brodberg_session.save_session({"username": data["username"],
                                               "token":    data["token"]})
                pr   = requests.get(_url("/me"),
                                    headers={"Authorization": f"Bearer {data['token']}"},
                                    timeout=TIMEOUT)
                user = pr.json() if pr.status_code == 200 else None
                if user:
                    brodberg_session.save_session({"is_admin": bool(user.get("is_admin", False))})
                return _result("LOGIN", f"Welcome back, {data['username']}.", "ok", user=user)
            return {**cache, "status": "error", "message": data.get("detail", "Login failed.")}
        except Exception as e:
            return {**cache, "status": "error", "message": str(e)}

    if action == "REGISTER":
        username = fields[0]["value"].strip()
        password = fields[1]["value"]
        if not username or not password:
            return {**cache, "status": "error", "message": "Both fields are required."}
        try:
            r = requests.post(_url("/register"),
                              json={"username": username, "password": password},
                              timeout=TIMEOUT)
            data = r.json()
            if r.status_code == 201:
                return {**cache, "status": "ok",
                        "message": "Account created!  Now use  USER login  to sign in."}
            return {**cache, "status": "error", "message": data.get("detail", "Registration failed.")}
        except Exception as e:
            return {**cache, "status": "error", "message": str(e)}

    if action == "EDIT":
        bio      = fields[0]["value"].strip()
        location = fields[1]["value"].strip()
        try:
            r = requests.put(_url("/me"),
                             json={"bio": bio, "location": location},
                             headers=_auth_headers(),
                             timeout=TIMEOUT)
            if r.status_code == 200:
                return _result("EDIT", "Profile updated.", "ok",
                               user={"username": brodberg_session.get_current_user(),
                                     "created_at": "",
                                     "bio":      bio,
                                     "location": location})
            return {**cache, "status": "error",
                    "message": r.json().get("detail", "Update failed.")}
        except Exception as e:
            return {**cache, "status": "error", "message": str(e)}

    return cache


# ---------------------------------------------------------------------------
# on_keypress — handles ALL keys when form_mode is True
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    # ── DIR navigation ────────────────────────────────────────────────────
    if cache.get("action") == "DIR":
        view = cache.get("view", "list")

        if view == "detail":
            return {**cache, "view": "list"}   # any key returns to list

        # list view
        users = cache.get("users", [])
        n     = len(users)
        sel   = cache.get("selected", 0)
        off   = cache.get("page_offset", 0)

        if key == curses.KEY_UP:
            new_sel = max(0, sel - 1)
            return {**cache, "selected": new_sel,
                    "page_offset": min(off, new_sel)}

        if key == curses.KEY_DOWN:
            if n == 0:
                return cache
            new_sel = min(n - 1, sel + 1)
            new_off = off if new_sel < off + _DIR_VISIBLE else new_sel - _DIR_VISIBLE + 1
            return {**cache, "selected": new_sel, "page_offset": new_off}

        if key in (curses.KEY_ENTER, 10, 13):
            if 0 <= sel < n:
                return {**cache, "view": "detail", "detail_user": users[sel]}

        return cache

    # ── Form-mode handling (login / register / edit) ──────────────────────
    if not cache.get("form_mode"):
        return cache

    fields  = [dict(f) for f in cache.get("fields", [])]
    focused = cache.get("focused_field", 0)
    n       = len(fields)

    if not fields:
        return cache

    # Navigate between fields
    if key in (curses.KEY_DOWN, curses.KEY_RIGHT):
        return {**cache, "focused_field": (focused + 1) % n}

    if key in (curses.KEY_UP, curses.KEY_LEFT):
        return {**cache, "focused_field": (focused - 1) % n}

    # Backspace
    if key in (curses.KEY_BACKSPACE, 127, 8):
        fields[focused]["value"] = fields[focused]["value"][:-1]
        return {**cache, "fields": fields, "status": None, "message": ""}

    # Submit on Enter
    if key in (curses.KEY_ENTER, 10, 13):
        return _submit(cache)

    # Printable character
    if 32 <= key <= 126:
        fields[focused]["value"] += chr(key)
        return {**cache, "fields": fields, "status": None, "message": ""}

    return cache


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


def render(stdscr, cache: dict, colors: dict) -> None:
    _, width  = stdscr.getmaxyx()
    action    = cache.get("action", "HOME")
    form_mode = cache.get("form_mode", False)
    status    = cache.get("status")
    message   = cache.get("message", "")
    user      = cache.get("user")

    sep   = f"  {'─' * min(50, width - 4)}"
    box_w = max(24, min(52, width - 8))   # total box width  ┌──...──┐
    inner = box_w - 4                      # usable chars inside  │ ... │

    # ── USER DIR ──────────────────────────────────────────────────────────
    if action == "DIR":
        r = 4
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1

        if cache.get("error"):
            _put(stdscr, r, 2, "USER DIRECTORY", colors["orange"], bold=True); r += 1
            _put(stdscr, r, 0, sep, colors["dim"]); r += 1
            _put(stdscr, r, 2, cache["error"], colors["negative"])
            return

        view = cache.get("view", "list")

        if view == "detail":
            du = cache.get("detail_user") or {}
            _put(stdscr, r, 2, "USER DIRECTORY  ›  PROFILE", colors["orange"], bold=True); r += 1
            _put(stdscr, r, 0, sep, colors["dim"]); r += 1

            is_me    = (du.get("username") == brodberg_session.get_current_user())
            is_admin = bool(du.get("is_admin", False))
            label    = "YOUR PROFILE" if is_me else du.get("username", "").upper()
            if is_admin:
                label += "  ♠"
            _put(stdscr, r, 2, label, colors["orange"], bold=True); r += 1
            _put(stdscr, r, 0, sep, colors["dim"]); r += 1

            def dv(lbl, val):
                nonlocal r
                _put(stdscr, r, 4, f"{lbl:<16}", colors["dim"])
                _put(stdscr, r, 20, val or "—", colors["orange"])
                r += 1

            uname_display = du.get("username", "")
            if is_admin:
                uname_display += "  ♠  Admin"
            dv("Username",     uname_display)
            dv("Member since", du.get("created_at", ""))
            dv("Location",     du.get("location",   ""))
            dv("Bio",          du.get("bio",        ""))
            r += 1
            _put(stdscr, r, 0, sep, colors["dim"]); r += 1
            _put(stdscr, r, 2, "Any key  back to directory", colors["dim"])
            return

        # list view
        users = cache.get("users", [])
        total = len(users)
        sel   = cache.get("selected", 0)
        off   = cache.get("page_offset", 0)

        count_str = f"{total} user{'s' if total != 1 else ''}"
        _put(stdscr, r, 2, "USER DIRECTORY", colors["orange"], bold=True)
        _put(stdscr, r, 18, count_str, colors["dim"])
        r += 1
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1

        # column headers
        C_USER   = 2
        C_JOINED = 20
        C_LOC    = 33
        C_BIO    = 55
        _put(stdscr, r, C_USER,   f"{'USERNAME':<17}", colors["dim"], bold=True)
        _put(stdscr, r, C_JOINED, f"{'JOINED':<12}",   colors["dim"], bold=True)
        _put(stdscr, r, C_LOC,    f"{'LOCATION':<21}", colors["dim"], bold=True)
        if width > C_BIO + 4:
            _put(stdscr, r, C_BIO, "BIO", colors["dim"], bold=True)
        r += 1
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1

        if not users:
            _put(stdscr, r, 2, "No users found.", colors["dim"])
            return

        visible = users[off: off + _DIR_VISIBLE]
        for i, u in enumerate(visible):
            abs_idx  = off + i
            is_sel   = abs_idx == sel
            is_admin = bool(u.get("is_admin", False))
            uname    = u.get("username", "")
            if is_admin:
                uname += " ♠"
            joined = (u.get("created_at") or "")[:10]
            loc    = (u.get("location") or "—")[:20]
            bio    = (u.get("bio") or "—")[: max(1, width - C_BIO - 2)]

            col = colors["orange"] if is_sel else colors["dim"]
            bld = is_sel
            _put(stdscr, r, C_USER,   f"{uname:<17}", col, bold=bld)
            _put(stdscr, r, C_JOINED, f"{joined:<12}", col, bold=bld)
            _put(stdscr, r, C_LOC,    f"{loc:<21}",   col, bold=bld)
            if width > C_BIO + 4:
                _put(stdscr, r, C_BIO, bio, col, bold=bld)
            r += 1

        _put(stdscr, r, 0, sep, colors["dim"]); r += 1
        if total > _DIR_VISIBLE:
            shown_end = min(off + _DIR_VISIBLE, total)
            _put(stdscr, r, 2,
                 f"Showing {off + 1}–{shown_end} of {total}   "
                 f"↑ ↓ navigate   Enter view profile",
                 colors["dim"])
        else:
            _put(stdscr, r, 2, "↑ ↓ navigate   Enter view profile", colors["dim"])
        return

    TITLES = {
        "LOGIN":    "LOGIN TO BRODBERG",
        "REGISTER": "CREATE AN ACCOUNT",
        "EDIT":     "EDIT PROFILE",
        "LOGOUT":   "SIGN OUT",
        "PROFILE":  "USER PROFILE",
        "HOME":     "USER",
    }

    r = 4
    _put(stdscr, r, 0, sep, colors["dim"]);                             r += 1
    _put(stdscr, r, 2, TITLES.get(action, "USER"), colors["orange"], bold=True); r += 1
    _put(stdscr, r, 0, sep, colors["dim"]);                             r += 2

    # ── Interactive form ──────────────────────────────────────────────────
    if form_mode:
        fields  = cache.get("fields", [])
        focused = cache.get("focused_field", 0)

        if status and message:
            sc = colors["positive"] if status == "ok" else colors["negative"]
            _put(stdscr, r, 2, message, sc, bold=True)
            r += 2

        for i, field in enumerate(fields):
            is_focused  = (i == focused)
            label_color = colors["orange"] if is_focused else colors["dim"]
            box_color   = colors["orange"] if is_focused else colors["dim"]

            _put(stdscr, r, 2, field["label"], label_color, bold=is_focused)
            r += 1

            raw     = field["value"]
            display = "•" * len(raw) if field.get("masked") else raw
            display = display[-(inner - 1):]              # keep cursor visible on long input
            cursor  = "▌" if is_focused else " "
            content = (display + cursor).ljust(inner)

            _put(stdscr, r, 2, "┌" + "─" * (box_w - 2) + "┐", box_color); r += 1
            _put(stdscr, r, 2, "│ " + content + " │",           box_color, bold=is_focused); r += 1
            _put(stdscr, r, 2, "└" + "─" * (box_w - 2) + "┘",  box_color); r += 2

        _put(stdscr, r, 2, "↑ ↓  navigate   Enter  submit", colors["dim"]); r += 1
        _put(stdscr, r, 0, sep, colors["dim"])
        return

    # ── Result / profile display ──────────────────────────────────────────
    if status and message:
        sc = colors["positive"] if status == "ok" else colors["negative"]
        _put(stdscr, r, 2, message, sc, bold=True)
        r += 2

    if user:
        is_me    = (user.get("username") == brodberg_session.get_current_user())
        is_admin = bool(user.get("is_admin", False))
        label    = "YOUR PROFILE" if is_me else user.get("username", "").upper()
        if is_admin:
            label += "  ♠"
        _put(stdscr, r, 2, label, colors["orange"], bold=True); r += 1
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1

        def info_row(lbl, val):
            nonlocal r
            _put(stdscr, r, 4, f"{lbl:<16}", colors["dim"])
            _put(stdscr, r, 20, val or "—", colors["orange"])
            r += 1

        username_display = user.get("username", "")
        if is_admin:
            username_display += "  ♠  Admin"
        info_row("Username",      username_display)
        info_row("Member since",  user.get("created_at", ""))
        info_row("Location",      user.get("location",   ""))
        info_row("Bio",           user.get("bio",        ""))
        r += 1
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1
        if is_me:
            _put(stdscr, r, 2, "USER edit  to update your profile", colors["dim"])

    elif not message:
        _put(stdscr, r, 2,
             "USER login   USER register   USER <username>   USER edit",
             colors["dim"])
