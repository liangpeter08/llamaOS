"""Host-only: clear the kill switch after you've investigated. Not reachable from the dashboard."""
from __future__ import annotations

import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.env import load  # noqa: E402

load()
from common import audit, db  # noqa: E402


def main():
    conn = db.get()
    ks = db.kv_get(conn, "kill_switch") or {}
    if not ks.get("active"):
        print("kill switch is not active")
        return
    print(f"active since {ks.get('ts')}: {ks.get('reason')}")
    if input("type RESET to clear it: ").strip() != "RESET":
        sys.exit("aborted")
    db.kv_set(conn, "kill_switch", {"active": False, "reset_by": getpass.getuser()})
    db.kv_set(conn, "broker.suspend_trade", False)
    audit.log(conn, "kill_switch.reset", f"host:{getpass.getuser()}", {"previous": ks})
    print("cleared. Restart the broker to start a fresh session.")


if __name__ == "__main__":
    main()
