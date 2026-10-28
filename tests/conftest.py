import pytest

from raidlock.core import db
from raidlock.core import lead as LD


@pytest.fixture(autouse=True)
def close_index_connections():
    yield
    db.close_all()


@pytest.fixture(autouse=True)
def one_process_per_session(monkeypatch):
    """Tests run every simulated session in this one process, so the renamed-host-session rule would merge them."""
    monkeypatch.setattr(LD, "SAME_HOST_SESSIONS", False)
    monkeypatch.setenv("RAIDLOCK_SAME_HOST_SESSIONS", "0")
