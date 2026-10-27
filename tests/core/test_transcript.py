import json

import pytest

from raidlock.core import transcript as T
from raidlock.core.db import Index


def _t(tmp_path, ws="ws1"):
    return T.Transcript(str(tmp_path / "board"), Index(str(tmp_path / "home")), ws)


@pytest.mark.parametrize(
    "secret",
    [
        "AKIAABCDEFGHIJKLMNOP",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "xoxb-1234567890-abcdefghij",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redacts_secrets(secret):
    out = T.redact(f"before {secret} after")
    assert secret not in out and T.REDACTED in out and out.startswith("before")


def test_redacts_assignments_keeps_key_name():
    out = T.redact("password=hunter2xyz and API_KEY: 'abcd1234'")
    assert "hunter2xyz" not in out and "abcd1234" not in out
    assert "password=" in out and "API_KEY" in out


def test_plain_text_untouched():
    text = "ran pytest -q: 42 passed; see https://example.com/run/1"
    assert T.redact(text) == text


def test_append_only_and_dedupe(tmp_path):
    t = _t(tmp_path)
    rec = T.make(
        "note",
        {"agent": "a", "provider": "anthropic", "runtime": "claude-code", "model": "m"},
        "hi",
        task="c1",
        context="ws/c1",
    )
    assert t.append(rec) is not None
    assert t.append(rec) is None
    t.append(T.make("msg", {"agent": "b"}, "second"))
    lines = (tmp_path / "board" / "transcript" / "000001.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["messageId"] == rec["messageId"]


def test_a2a_shape_and_redaction_on_append(tmp_path):
    t = _t(tmp_path)
    rec = t.append(
        T.make(
            "handback",
            {"agent": "a"},
            "token=supersecretvalue",
            task="c1",
            context="ws/c1",
            artifacts=[T.artifact("evidence", "ghp_abcdefghijklmnopqrstuvwxyz0123456789")],
        )
    )
    for key in ("messageId", "ts", "kind", "role", "taskId", "contextId", "from", "to", "parts", "artifacts", "src"):
        assert key in rec
    assert rec["parts"][0] == {"kind": "text", "text": "token=" + T.REDACTED}
    assert "ghp_" not in json.dumps(rec)


def test_filters(tmp_path):
    t = _t(tmp_path)
    t.append(T.make("note", {"agent": "a"}, "1", task="c1"))
    t.append(T.make("msg", {"agent": "b"}, "2", task="c2", to="a"))
    t.append(T.make("note", {"agent": "c"}, "3", task="c1"))
    assert [r["parts"][0]["text"] for r in t.read(task="c1")] == ["1", "3"]
    assert [r["parts"][0]["text"] for r in t.read(agent="a")] == ["1", "2"]
    assert [r["parts"][0]["text"] for r in t.read(limit=1)] == ["3"]


def test_unknown_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        _t(tmp_path).append(T.make("bogus", {"agent": "a"}, "x"))


def test_board_notes_are_redacted(world):
    w = world.agent("w-one")
    world.card("c1")
    world.claim("c1", w)
    world.board.note("c1", w, "used key sk-proj-abcdefghijklmnopqrstuvwx")
    assert "sk-proj" not in json.dumps(world.board.card("c1"))


def test_segments_rotate_and_cursor_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "SEGMENT_BYTES", 600)
    t = _t(tmp_path)
    for i in range(20):
        t.append(T.make("note", {"agent": "a"}, f"record {i}", task="c1" if i % 2 else "c2"))
    assert len(list((tmp_path / "board" / "transcript").glob("*.jsonl"))) > 3
    first = t.read(since=0, limit=7)
    second = t.read(since=first[-1]["seq"], limit=100)
    texts = [r["parts"][0]["text"] for r in first + second]
    assert texts == [f"record {i}" for i in range(20)]
    assert [r["parts"][0]["text"] for r in t.read(task="c1", limit=2)] == ["record 17", "record 19"]


def test_reindex_rebuilds_from_segments(tmp_path):
    t = _t(tmp_path)
    for i in range(5):
        t.append(T.make("note", {"agent": "a"}, f"r{i}", task="c1"))
    t.index.clear_transcript("ws1")
    again = _t(tmp_path)
    assert [r["parts"][0]["text"] for r in again.read(task="c1")] == [f"r{i}" for i in range(5)]


def test_dedupe_survives_reopen(tmp_path):
    rec = T.make("note", {"agent": "a"}, "once")
    assert _t(tmp_path).append(rec) is not None
    assert _t(tmp_path).append(rec) is None


def test_gitleaks_allowlist_requires_exact_fixture_and_dummy_secret():
    import re
    from pathlib import Path

    config = (Path(__file__).resolve().parents[2] / ".gitleaks.toml").read_text()
    entries = config.split("[[allowlists]]")[1:]
    assert entries

    def allowed(path, secret):
        for entry in entries:
            assert 'condition = "AND"' in entry
            assert 'regexTarget = "secret"' in entry
            path_pattern = re.search(r"paths = \['''(.*?)'''\]", entry).group(1)
            secret_pattern = re.search(r"regexes = \['''(.*?)'''\]", entry).group(1)
            assert path_pattern.startswith("^") and path_pattern.endswith("$")
            assert secret_pattern.startswith("^") and secret_pattern.endswith("$")
            if re.search(path_pattern, path) and re.search(secret_pattern, secret):
                return True
        return False

    assert allowed("tests/core/test_transcript.py", "AKIAABCDEFGHIJKLMNOP")
    assert allowed("tests/core/test_model.py", "abcd1234secret")
    assert not allowed("src/production.py", "AKIAABCDEFGHIJKLMNOP")
    assert not allowed("tests/core/test_transcript.py.bak", "AKIAABCDEFGHIJKLMNOP")
    assert not allowed("tests/core/test_transcript.py", "AKIAREALCREDENTIALXYZ")
    assert not allowed("tests/core/test_model.py", "AKIAABCDEFGHIJKLMNOP")
