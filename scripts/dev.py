"""Run the whole stack locally: broker, models, api (Python) and the Next.js dashboard.

    python scripts/dev.py            # Ctrl-C stops everything
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.env import ROOT, load  # noqa: E402

load()
os.environ.setdefault("BROKER_BACKEND", "simulator")
PY = sys.executable
RESTART = 75  # common.broker_accounts.RESTART
PROCS = [
    ("models", [PY, "-m", "services.models"], ROOT),
    ("api", [PY, "-m", "services.api"], ROOT),
    ("broker", [PY, "-m", "services.broker"], ROOT),
    ("dashboard", ["npm", "run", "dev"], ROOT / "services" / "dashboard"),
]
if os.environ.get("BROKER_BACKEND") == "alpaca":
    PROCS.insert(3,("watchdog",[PY,"-m","services.broker.watchdog"],ROOT))
    PROCS.insert(4,("operations",[PY,"-m","services.operations"],ROOT))


def pump(name: str, stream):
    for line in iter(stream.readline, ""):
        sys.stdout.write(f"{name:>9} | {line}")
        sys.stdout.flush()


def main():
    running = []
    for name, cmd, cwd in PROCS:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
        threading.Thread(target=pump, args=(name, p.stdout), daemon=True).start()
        running.append((name, p))
        time.sleep(1.0)  # let the schema/init settle before the next service starts
    print("dashboard: http://localhost:3000   (api :8080, models :8081 are internal)")

    def stop(*_):
        for _, p in running:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for _, p in running:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while True:
        for i, (name, p) in enumerate(running):
            if p.poll() == RESTART:  # broker/watchdog after an Alpaca account switch
                print(f"{name} restarting on the new Alpaca account")
                cmd, cwd = next((c, d) for n, c, d in PROCS if n == name)
                p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
                threading.Thread(target=pump, args=(name, p.stdout), daemon=True).start()
                running[i] = (name, p)
            elif p.poll() is not None:
                print(f"{name} exited with {p.returncode}; stopping the stack")
                stop()
        time.sleep(1)


if __name__ == "__main__":
    main()
