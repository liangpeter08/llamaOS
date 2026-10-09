"""Development only: create an owner account with generated credentials.

Writes secrets/dev-owner.txt (git-ignored). `--code` prints the current 2FA code.
For a real deployment use `python -m services.api.create_owner` instead.
"""
from __future__ import annotations

import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.env import ROOT, load  # noqa: E402

load()
import pyotp  # noqa: E402

from common import db  # noqa: E402
from services.api.create_owner import create  # noqa: E402

PATH = ROOT / "secrets" / "dev-owner.txt"


def main():
    if "--code" in sys.argv:
        d = json.loads(PATH.read_text())
        print(pyotp.TOTP(d["totp_secret"]).now())
        return
    conn = db.get()
    db.init(conn)
    if db.row(conn, "SELECT 1 FROM users WHERE username='admin'"):
        print(f"admin already exists; credentials in {PATH}")
        return
    pw = secrets.token_urlsafe(18)
    secret = create(conn, "admin", pw, "owner")
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps({"username": "admin", "password": pw, "totp_secret": secret}, indent=2))
    os.chmod(PATH, 0o600)
    print(f"created dev owner 'admin'; credentials in {PATH} (current code: make dev-code)")


if __name__ == "__main__":
    main()
