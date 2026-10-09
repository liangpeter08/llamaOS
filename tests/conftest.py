import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://llamaos:llamaos@127.0.0.1:5432/llamaos_test")
os.environ.setdefault("RISK_FILE", str(ROOT / "config" / "risk.example.yaml"))

from common import db  # noqa: E402
from common.config import load_risk  # noqa: E402


@pytest.fixture(scope="session")
def risk():
    return load_risk()


@pytest.fixture()
def conn():
    c = db.get()
    db.reset(c)
    db.init(c)
    yield c


@pytest.fixture(scope="session")
def orb_text():
    return (ROOT / "strategies" / "orb_news_long.v1.yaml").read_text()
