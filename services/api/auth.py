"""Login (password + TOTP), roles and CSRF for the dashboard. No public sign-up.

2FA is checked once, at login. Actions inside a session need only the session and CSRF token.
"""
from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request

from common import db

ph = PasswordHasher()
SESSION_MAX_AGE = 8 * 3600
_failures: dict[str, deque] = defaultdict(deque)
MAX_FAILURES, FAILURE_WINDOW = 5, 600


def hash_password(pw: str) -> str:
    return ph.hash(pw)


def rate_limited(key: str) -> bool:
    q = _failures[key]
    while q and q[0] < time.time() - FAILURE_WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILURES


def note_failure(key: str):
    _failures[key].append(time.time())


def authenticate(conn, username: str, password: str, code: str) -> dict | None:
    u = db.row(conn, "SELECT * FROM users WHERE username=?", (username,))
    if not u:
        ph.hash(password)  # even out timing
        return None
    try:
        ph.verify(u["pw_hash"], password)
    except (VerifyMismatchError, VerificationError):
        return None
    if u["role"] == "owner" and not u["totp_secret"]:
        return None  # 2FA is mandatory for owner
    if u["totp_secret"] and not pyotp.TOTP(u["totp_secret"]).verify(code or "", valid_window=1):
        return None
    return u


def login(request: Request, user: dict):
    request.session.clear()
    now = time.time()
    request.session.update({"uid": user["id"], "username": user["username"], "role": user["role"],
                            "login_at": now, "csrf": secrets.token_urlsafe(24)})


def current(request: Request) -> dict | None:
    s = request.session
    if not s.get("uid"):
        return None
    if time.time() - s.get("login_at", 0) > SESSION_MAX_AGE:
        request.session.clear()
        return None
    return {"id": s["uid"], "username": s["username"], "role": s["role"], "csrf": s.get("csrf", ""),
            "login_at": s["login_at"]}


def require_owner(request: Request) -> dict:
    u = current(request)
    if not u:
        raise HTTPException(401, "login required")
    if u["role"] != "owner":
        raise HTTPException(403, "read-only account")
    return u


def check_csrf(request: Request, form_token: str | None):
    expected = request.session.get("csrf")
    got = request.headers.get("x-csrf-token") or form_token
    if not expected or not got or not secrets.compare_digest(expected, got):
        raise HTTPException(403, "bad CSRF token")
