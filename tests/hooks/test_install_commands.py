import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import SRC

from raidlock import commands_install as ci
from raidlock.core.errors import Precondition


def env(tmp_path):
    return {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}


def test_install_is_idempotent_and_user_only(tmp_path):
    e = env(tmp_path)
    assert ci.install(e)["action"] == "installed"
    assert ci.install(e)["action"] == "unchanged"
    text = Path(ci.target(e)).read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in text
    assert ci.MARKER in text.split("\n---", 1)[0]
    assert "!`raidlock $ARGUMENTS`" in text


def test_install_updates_own_older_file(tmp_path):
    e = env(tmp_path)
    ci.install(e)
    with open(ci.target(e), "a", encoding="utf-8") as f:
        f.write("old\n")
    assert ci.install(e)["action"] == "updated"
    assert Path(ci.target(e)).read_text(encoding="utf-8") == ci.CONTENT


def test_foreign_file_is_never_touched(tmp_path):
    e = env(tmp_path)
    os.makedirs(ci.commands_dir(e))
    foreign = "---\ndescription: mine\n---\nraidlock-managed: install-commands in the body does not count\n"
    with open(ci.target(e), "w", encoding="utf-8") as f:
        f.write(foreign)
    with pytest.raises(Precondition):
        ci.install(e)
    with pytest.raises(Precondition):
        ci.uninstall(e)
    assert Path(ci.target(e)).read_text(encoding="utf-8") == foreign


def test_uninstall_removes_only_its_file(tmp_path):
    e = env(tmp_path)
    assert ci.uninstall(e)["action"] == "absent"
    ci.install(e)
    other = os.path.join(ci.commands_dir(e), "other.md")
    open(other, "w").close()
    assert ci.uninstall(e)["action"] == "removed"
    assert not os.path.exists(ci.target(e)) and os.path.exists(other)


def test_cli_command(tmp_path):
    full = {**os.environ, "PYTHONPATH": SRC, **env(tmp_path)}

    def run(*a):
        return subprocess.run(
            [sys.executable, "-m", "raidlock", *a],
            capture_output=True,
            text=True,
            env=full,
            cwd=str(tmp_path),
            timeout=60,
        )

    first = run("install-commands")
    assert first.returncode == 0 and "Installed /raid" in first.stdout
    assert run("install-commands").returncode == 0
    gone = run("install-commands", "--uninstall")
    assert gone.returncode == 0 and "Removed /raid" in gone.stdout
    os.makedirs(ci.commands_dir(env(tmp_path)), exist_ok=True)
    with open(ci.target(env(tmp_path)), "w", encoding="utf-8") as f:
        f.write("mine\n")
    refused = run("install-commands")
    assert refused.returncode == 3 and "not written by raidlock" in refused.stderr


def test_install_replaces_its_legacy_command_file_only(tmp_path):
    e = env(tmp_path)
    legacy = ci.legacy_target(e)
    os.makedirs(os.path.dirname(legacy))
    with open(legacy, "w", encoding="utf-8") as f:
        f.write(ci.CONTENT)
    ci.install(e)
    assert not os.path.exists(legacy) and ci.target(e).endswith(os.path.join("skills", "raid", "SKILL.md"))
    with open(legacy, "w", encoding="utf-8") as f:
        f.write("---\ndescription: mine\n---\n")
    ci.uninstall(e)
    assert os.path.exists(legacy) and not os.path.exists(ci.target(e))


CLAUDE_CODE_FIELDS = {
    "name",
    "description",
    "when_to_use",
    "argument-hint",
    "arguments",
    "disable-model-invocation",
    "user-invocable",
    "allowed-tools",
    "disallowed-tools",
    "model",
    "effort",
    "context",
    "agent",
    "background",
    "hooks",
    "paths",
    "shell",
    "metadata",
    "license",
    "compatibility",
}


def test_personal_skill_frontmatter_is_flat_and_only_adds_the_marker():
    lines = ci.CONTENT.splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    keys = []
    for line in lines[1:end]:
        key, sep, value = line.partition(": ")
        assert sep and value.strip() and not line.startswith((" ", "\t")), line
        keys.append(key)
    assert len(keys) == len(set(keys))
    assert set(keys) - CLAUDE_CODE_FIELDS == {ci.MARKER.split(":")[0]}
    assert "!`raidlock $ARGUMENTS`" in "\n".join(lines[end + 1 :])
