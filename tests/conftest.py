import pytest

from raidlock.core import db


@pytest.fixture(autouse=True)
def close_index_connections():
    yield
    db.close_all()
