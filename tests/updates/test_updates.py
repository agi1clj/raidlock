import json
import time

import pytest

from raidlock import __version__, updates


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIDLOCK_HOME", str(tmp_path))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("RAIDLOCK_NO_UPDATE_CHECK", raising=False)
    return tmp_path


def write(home, latest, age=0):
    (home / "update-check.json").write_text(json.dumps({"checked": time.time() - age, "latest": latest}))


def test_newer_release_produces_a_notice(home):
    write(home, "99.0.0")
    assert updates.notice() == f"raidlock 99.0.0 is available (you have {__version__}): uv tool upgrade raidlock"
    assert updates.available()["latest"] == "99.0.0"


def test_same_or_older_release_is_silent(home):
    write(home, __version__)
    assert updates.notice() == ""
    write(home, "0.0.1")
    assert updates.available() is None


def test_fresh_cache_skips_the_network(home, monkeypatch):
    write(home, "99.0.0")
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network used"))
    assert updates.refresh() == "99.0.0"


def test_stale_cache_refetches_and_offline_keeps_the_old_answer(home, monkeypatch):
    write(home, "99.0.0", age=updates.TTL + 1)

    def offline(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    assert updates.refresh() == "99.0.0"
    assert (
        json.loads((home / "update-check.json").read_text())["checked"] < time.time() - updates.TTL + updates.RETRY + 5
    )


def test_opt_out_and_ci_disable_the_check(home, monkeypatch):
    write(home, "99.0.0")
    monkeypatch.setenv("RAIDLOCK_NO_UPDATE_CHECK", "1")
    assert updates.refresh() is None and updates.notice() == ""
    monkeypatch.delenv("RAIDLOCK_NO_UPDATE_CHECK")
    monkeypatch.setenv("CI", "true")
    assert updates.notice() == ""


def test_hooks_never_import_the_update_check():
    from pathlib import Path

    import raidlock.hooks as hooks

    assert "updates" not in Path(hooks.__file__).read_text(encoding="utf-8")


def test_read_only_refresh_leaves_home_untouched(home, monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return b'{"info": {"version": "99.0.0"}}'

    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert updates.refresh(persist=False) == "99.0.0"
    assert not (home / "update-check.json").exists()


def test_corrupt_cache_does_not_crash(home, monkeypatch):
    (home / "update-check.json").write_text('{"checked": "x", "latest": "99.0.0"}')

    def offline(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    assert updates.refresh() == "99.0.0"


def test_untrusted_version_strings_are_ignored(home):
    for bad in ("0.2.0rc1", "../../evil/repo", "1.0.0<script>", "", None, 3):
        write(home, bad)
        assert updates.available() is None and updates.notice() == ""


def test_failed_fetch_retries_within_the_hour_and_keeps_the_last_answer(home, monkeypatch):
    write(home, "99.0.0", age=updates.TTL + 1)

    def offline(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    assert updates.refresh() == "99.0.0"
    checked = json.loads((home / "update-check.json").read_text())["checked"]
    retry_at = checked + updates.TTL
    assert time.time() + updates.RETRY - 5 <= retry_at <= time.time() + updates.RETRY + 5


def test_failed_first_fetch_does_not_silence_checks_for_a_day(home, monkeypatch):
    def offline(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    assert updates.refresh() is None
    checked = json.loads((home / "update-check.json").read_text())["checked"]
    assert time.time() - checked > updates.TTL - updates.RETRY - 5
