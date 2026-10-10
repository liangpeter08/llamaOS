import os
from scripts.env import load, ROOT

load(ROOT / "secrets" / "broker.env")
if os.environ.get("BROKER_BACKEND", "simulator") == "alpaca":
    from .paper import main
elif os.environ.get("BROKER_BACKEND", "simulator") == "simulator":
    from .main import main
else:
    raise ValueError("BROKER_BACKEND must be alpaca or simulator")

main()
