import sqlite3

import pytest

from raidlock.core import db


@pytest.mark.parametrize("failure", ["damaged", "migration"])
def test_failed_database_initialization_closes_connection(tmp_path, monkeypatch, failure):
    path = str(tmp_path / "damaged.sqlite")
    if failure == "damaged":
        (tmp_path / "damaged.sqlite").write_bytes(b"not a sqlite database" * 100)
    connections = []
    connect = sqlite3.connect

    def tracked(*args, **kwargs):
        conn = connect(*args, **kwargs)
        connections.append(conn)
        return conn

    def broken_migration(conn):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(db.sqlite3, "connect", tracked)
    if failure == "migration":
        monkeypatch.setattr(db, "_migrate", broken_migration)
    for _ in range(3):
        with pytest.raises((sqlite3.DatabaseError, RuntimeError)):
            db._connect(path)
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[-1].execute("SELECT 1")
        assert path not in db._local.conns
