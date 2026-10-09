"""Create a dashboard account and print its TOTP enrollment URI.

    python -m services.api.create_owner --username admin            # prompts for a password
    python -m services.api.create_owner --username bob --role viewer
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time

import pyotp

from common import audit, db

from .auth import hash_password


def create(conn, username: str, password: str, role: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    secret = pyotp.random_base32()
    conn.execute("INSERT INTO users(username, role, pw_hash, totp_secret, created_at) VALUES (?,?,?,?,?)",
                 (username, role, hash_password(password), secret, time.time()))
    audit.log(conn, "user.created", "host", {"username": username, "role": role})
    return secret


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    ap.add_argument("--role", choices=["owner", "viewer"], default="owner")
    ap.add_argument("--password-env", help="read the password from this environment variable instead of prompting")
    a = ap.parse_args()
    conn = db.get()
    db.init(conn)
    if db.row(conn, "SELECT 1 FROM users WHERE username=?", (a.username,)):
        sys.exit(f"user {a.username!r} already exists")
    if a.password_env:
        pw = os.environ.get(a.password_env) or sys.exit(f"${a.password_env} is empty")
    else:
        pw = getpass.getpass("password (min 12 chars): ")
        if pw != getpass.getpass("again: "):
            sys.exit("passwords differ")
    secret = create(conn, a.username, pw, a.role)
    uri = pyotp.TOTP(secret).provisioning_uri(name=a.username, issuer_name="llamaOS")
    print(f"created {a.role} {a.username!r}")
    print("Add this to your authenticator app (2FA is required to log in):")
    print(f"  secret: {secret}")
    print(f"  uri:    {uri}")


if __name__ == "__main__":
    main()
