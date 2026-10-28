import io
import json
import time
import types

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
    assert updates.notice() == f"raidlock 99.0.0 is available (you have {__version__}): raid upgrade"
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
    assert json.loads((home / "update-check.json").read_text())["checked"] > time.time() - 5


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


def test_checks_again_after_ten_minutes(home, monkeypatch):
    assert updates.TTL == 600
    write(home, "0.0.1", age=updates.TTL + 1)
    body = b'{"info": {"version": "99.0.0"}}'
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    assert updates.refresh() == "99.0.0"


def test_running_ui_keeps_checking(monkeypatch):
    calls = []

    def stop(seconds):
        assert seconds == updates.TTL
        if len(calls) == 3:
            raise SystemExit

    monkeypatch.setattr(updates, "refresh", lambda: calls.append(1))
    monkeypatch.setattr(updates.time, "sleep", stop)
    with pytest.raises(SystemExit):
        updates.watch()
    assert len(calls) == 3


def test_new_release_changes_the_overview_etag(home):
    from raidlock.web import data

    catalog = data.Catalog(home=str(home))
    write(home, "0.0.1")
    before = catalog.overview_key()
    write(home, "99.0.0")
    assert catalog.overview_key() != before


def test_upgrade_uses_the_installing_tool_and_the_plugin(monkeypatch):
    calls = []
    monkeypatch.setattr(updates.sys, "prefix", "/home/me/.local/share/uv/tools/raidlock")
    monkeypatch.setattr(updates.shutil, "which", lambda name: f"/bin/{name}")

    def run(argv, timeout=300):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="raidlock@raidlock\n", stderr="")

    monkeypatch.setattr(updates, "_run", run)
    assert updates.upgrade_steps() == [
        ["/bin/uv", "tool", "install", "--force", "raidlock@latest"],
        ["/bin/claude", "plugin", "marketplace", "update", "raidlock"],
        ["/bin/claude", "plugin", "update", "raidlock@raidlock"],
    ]


def test_upgrade_skips_what_is_not_installed(monkeypatch):
    monkeypatch.setattr(updates.sys, "prefix", "/usr/lib/python3")
    monkeypatch.setattr(updates.shutil, "which", lambda name: "/bin/pipx" if name == "pipx" else None)
    assert updates.upgrade_steps() == []
    monkeypatch.setattr(updates.sys, "prefix", "/home/me/.local/pipx/venvs/raidlock")
    assert updates.upgrade_steps() == [["/bin/pipx", "upgrade", "raidlock"]]


def test_upgrade_finds_a_uv_install_from_another_python(monkeypatch, tmp_path):
    (tmp_path / "raidlock").mkdir()
    monkeypatch.setattr(updates.sys, "prefix", "/usr/lib/python3")
    monkeypatch.setattr(updates.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(
        updates,
        "_run",
        lambda argv, timeout=300: types.SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr=""),
    )
    assert updates.upgrade_steps() == [["/bin/uv", "tool", "install", "--force", "raidlock@latest"]]
    (tmp_path / "raidlock").rmdir()
    assert updates.upgrade_steps() == []


def test_upgrade_command_stops_on_a_failed_step(monkeypatch, capsys):
    from raidlock import cli

    monkeypatch.setattr(
        updates, "upgrade_steps", lambda: [["/bin/uv", "tool", "install", "--force", "raidlock@latest"]]
    )
    monkeypatch.setattr(
        updates, "_run", lambda argv, timeout=300: types.SimpleNamespace(returncode=2, stdout="", stderr="offline")
    )
    assert cli.main(["upgrade"]) == 3
    assert "uv tool install --force raidlock@latest failed: offline" in capsys.readouterr().err


@pytest.mark.parametrize(("got", "says"), [("0.2.5", "raidlock is now 0.2.5"), ("0.2.4", "PyPI is still catching up")])
def test_upgrade_reports_the_version_it_installed(monkeypatch, capsys, got, says):
    from raidlock import cli

    monkeypatch.setattr(
        updates, "upgrade_steps", lambda: [["/bin/uv", "tool", "install", "--force", "raidlock@latest"]]
    )
    monkeypatch.setattr(
        updates, "_run", lambda argv, timeout=300: types.SimpleNamespace(returncode=0, stdout="", stderr="")
    )
    monkeypatch.setattr(updates, "installed_version", lambda: got)
    monkeypatch.setattr(updates, "refresh", lambda force=False, persist=True: "0.2.5")
    assert cli.main(["upgrade"]) == 0
    assert says in capsys.readouterr().out


def test_installed_version_asks_the_running_executable_not_path(monkeypatch, tmp_path):
    exe = tmp_path / "raid"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(updates.sys, "argv", [str(exe), "upgrade"])
    monkeypatch.setattr(updates.shutil, "which", lambda name: "/plugin/bin/raidlock")
    monkeypatch.setattr(
        updates, "_run", lambda argv, timeout=300: calls.append(argv) or types.SimpleNamespace(stdout="raid 0.2.6\n")
    )
    assert updates.installed_version() == "0.2.6"
    assert calls == [[str(exe), "--version"]]
