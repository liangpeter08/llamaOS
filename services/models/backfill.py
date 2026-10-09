"""Record signals for historical news with the offline model (no API spend).

    python -m services.models.backfill
"""
from __future__ import annotations

from common import audit, db

from .triage import backfill


def main():
    conn = db.get()
    db.init(conn)
    n = backfill(conn)
    counts = {r["triage"]: r["n"] for r in conn.execute("SELECT triage, COUNT(*) n FROM news GROUP BY triage")}
    audit.log(conn, "signals.backfilled", "models", {"items": n, "triage": counts})
    print(f"triaged {n} historical items: {counts}")


if __name__ == "__main__":
    main()
