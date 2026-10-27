import json
import os

import pytest

from raidlock import cli
from raidlock.lore import Vault
from raidlock.lore import cli as lore_cli
from raidlock.lore import frontmatter as F
from raidlock.lore.vault import FIELDS


def _populate(world):
    w, b = world, world.board
    worker, reviewer = w.agent("w-one"), w.agent("r-one")
    w.card("auth", locks="app/src/auth,app/tests/auth")
    w.changes("auth", worker, reviewer, 'needs [[brackets]] and "quotes": handled')
    b.note("auth", worker, "lesson: keep fixtures small")
    w.rejected_handback("auth", "w-one", ["infra/main.tf"])
    w.approve_all()
    return b


@pytest.mark.parametrize("which", ["workspace", "global"])
def test_vault_opens_every_note_parses_and_links_resolve(world, which):
    b = _populate(world)
    vault = Vault.for_board(b) if which == "workspace" else Vault.global_(b.home)
    notes = vault.notes()
    names = {os.path.splitext(os.path.basename(rel))[0] for rel, _ in notes}
    lessons = [n for n in notes if n[0].startswith("lessons")]
    assert len(lessons) == 3
    for rel, text in notes:
        meta, body = F.parse(text)
        assert meta["type"] in ("lesson", "hub")
        for target in F.links(body):
            assert target in names, f"{rel} links to missing {target}"
        if meta["type"] == "lesson":
            assert list(meta) == list(FIELDS)
            assert isinstance(meta["paths"], list) and isinstance(meta["tags"], list)
            assert isinstance(meta["hits"], int)
            assert len(body.split("\n")) <= 3
            assert {"card-auth", "repo-app"} <= set(F.links(body))
    for hub in ("card-auth", "agent-r-one", "agent-w-one", "repo-app", "path-app-src-auth"):
        assert hub in names


def test_frontmatter_is_a_yaml_compatible_subset():
    text = F.dump({"a": "x: y", "b": ["p", "q"], "c": 3, "d": True}, "body")
    meta, body = F.parse(text)
    assert meta == {"a": "x: y", "b": ["p", "q"], "c": 3, "d": True} and body == "body"
    for line in text.split("\n")[1:5]:
        json.loads(line.split(": ", 1)[1])
    with pytest.raises(F.FrontmatterError):
        F.parse("---\nbad\n---\n")
    with pytest.raises(F.FrontmatterError):
        F.parse("no frontmatter")


def test_hubs_are_rebuilt_and_stale_hubs_removed(world):
    b = _populate(world)
    vault = Vault.for_board(b)
    for x in vault.lessons():
        os.remove(vault.lesson_path(x["id"]))
    assert vault.rebuild_hubs() == 0
    assert [rel for rel, _ in vault.notes()] == []


@pytest.fixture(autouse=True)
def _isolated_registries(monkeypatch):
    from raidlock.core import tokens

    monkeypatch.setattr(cli, "COMMANDS", dict(cli.COMMANDS))
    monkeypatch.setattr(tokens, "CONTRIBUTORS", dict(tokens.CONTRIBUTORS))


def _run(argv, capsys):
    cli.load_extensions()
    lore_cli.register_cli(cli.add_command)
    code = cli.main(argv)
    return code, capsys.readouterr().out


def test_cli_list_show_search_distill_for(world, capsys, monkeypatch):
    b = _populate(world)
    world.card("next", locks="app/src/auth/x.py")
    monkeypatch.setenv("RAIDLOCK_BOARD", b.dir)
    code, out = _run(["lore", "list", "--json"], capsys)
    data = json.loads(out)
    assert code == 0 and data["count"] == 3
    lid = data["lessons"][0]["id"]
    code, out = _run(["lore", "show", lid[:6]], capsys)
    assert code == 0 and lid in out
    code, out = _run(["lore", "search", "fixtures"], capsys)
    assert "keep fixtures small" in out
    code, out = _run(["lore", "distill"], capsys)
    assert "lesson:" in out and "keep fixtures small" in out
    code, out = _run(["lore", "for", "next", "--json"], capsys)
    r = json.loads(out)
    assert 1 <= len(r["lessons"]) <= 3 and r["tokens"] <= 200


def test_cli_propose_pending_approve(world, capsys, monkeypatch):
    b = world.board
    worker = world.agent("w-one")
    world.card("auth", locks="app/src/auth")
    monkeypatch.setenv("RAIDLOCK_BOARD", b.dir)
    code, out = _run(
        [
            "lore",
            "propose",
            "check",
            "the",
            "cache",
            "--card",
            "auth",
            "--agent",
            "w-one",
            "--token",
            worker.token,
            "--json",
        ],
        capsys,
    )
    lid = json.loads(out)["lesson"]["id"]
    code, out = _run(["lore", "pending"], capsys)
    assert lid in out
    code, _ = _run(["lore", "approve", lid, "--agent", "w-one", "--token", worker.token], capsys)
    assert code == 5
    code, out = _run(["lore", "approve", lid, "--lead-secret", b.leadlease().store.file.path], capsys)
    assert code == 0 and lid in out
    code, out = _run(["lore", "for", "auth"], capsys)
    assert "check the cache" in out


def test_register_tokens_reports_lore(world):
    from raidlock.core import tokens
    from raidlock.lore import register_tokens

    _populate(world)
    world.card("next", locks="app/src/auth")
    register_tokens(tokens.register)
    row = [i for i in tokens.report(world.board)["items"] if i["name"] == "lore lessons"][0]
    assert row["tokens"] > 0


@pytest.mark.parametrize("bad", ["x/../../../../victim", "../victim", "ABCDEF012345", "abcdef01234", "abcdef012345\n"])
def test_vault_rejects_traversal_at_every_lesson_boundary(tmp_path, bad):
    from raidlock.core.errors import Precondition

    vault = Vault(str(tmp_path / "lore"))
    victim = tmp_path / "victim.md"
    victim.write_text("untouched")
    for operation in (vault.lesson_path, vault.has, vault.get, vault.drop):
        with pytest.raises(Precondition, match="lesson id"):
            operation(bad)
    with pytest.raises(Precondition, match="lesson id"):
        vault.put({"id": bad, "text": "injected", "source": "note"})
    folder = tmp_path / "lore" / ".pending"
    folder.mkdir(parents=True)
    (folder / "lesson-abcdef012345.md").write_text(F.dump({"type": "lesson", "id": bad}, "injected"))
    assert vault.pending() == []
    with pytest.raises(Precondition, match="lesson id"):
        vault.get("abcdef012345", ".pending")
    with pytest.raises(Precondition, match="vault path|lesson id"):
        vault.write(f"lessons/lesson-{bad}.md", "injected")
    with pytest.raises(Precondition, match="vault path|lesson id"):
        vault._read(f"lessons/lesson-{bad}.md")
    assert victim.read_text() == "untouched"
