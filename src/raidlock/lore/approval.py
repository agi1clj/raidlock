import contextlib
import hashlib

from ..core import clock
from ..core import identity as I
from ..core.errors import Precondition
from .capture import _canon
from .vault import PENDING, Vault, one_line


def _authenticated(board, caller):
    I.require_auth(caller, board.settings, board.env, board.lead, board.agents())


@contextlib.contextmanager
def _lead_write(board, caller, action):
    guarded = getattr(board, "lead_write", None)
    if guarded is not None:
        with guarded(caller, action):
            yield
        return
    with board.lock():
        _authenticated(board, caller)
        I.require_lead(caller, action)
        yield


def propose(board, caller, text, card_id="", paths=None):
    """Any authenticated agent proposes a lesson. It stays pending until the lead approves it."""
    _authenticated(board, caller)
    text = one_line(text)
    if not text:
        raise Precondition("lesson text is empty")
    card = board.card(card_id) if card_id else {}
    locks = _canon(list(paths or []) or card.get("locks") or [])
    now = clock.iso()
    agent = board.agents().get(caller.name, {})
    key = f"{board.uid}:{caller.name}:{card_id}:{text}".encode()
    lesson = {
        "id": hashlib.sha1(key, usedforsecurity=False).hexdigest()[:12],
        "created": now,
        "workspace": board.name,
        "workspace_uid": board.uid,
        "repo": locks[0].split("/", 1)[0] if locks else "",
        "paths": locks,
        "card": card_id,
        "agent": caller.name,
        "provider": agent.get("provider", ""),
        "model": agent.get("model", ""),
        "source": "proposal",
        "source_id": "",
        "tags": ["lore", "proposal"],
        "hits": 0,
        "last_used": "",
        "status": "pending",
        "proposed_by": caller.name,
        "approved_by": "",
        "approved_at": "",
        "text": text,
    }
    vault = Vault.for_board(board)
    with vault.locked():
        if vault.has(lesson["id"]):
            raise Precondition(f"lesson {lesson['id']} is already approved", lesson=lesson["id"])
        vault.put(lesson, PENDING)
    return lesson


def _resolve(vault, ids):
    pending = {x["id"]: x for x in vault.pending()}
    out = []
    for raw in ids:
        match = [i for i in pending if i.startswith(raw)] if raw else []
        if len(match) != 1:
            raise Precondition(f"{len(match)} pending lessons match {raw!r}", hint="raidlock lore pending")
        out.append(pending[match[0]])
    return out


def approve(board, caller, ids):
    """Lead only. Moves pending lessons into the vault (workspace and global) and makes them injectable."""
    with _lead_write(board, caller, "approve lessons"):
        return _approve(board, caller, ids)


def _approve(board, caller, ids):
    vault, gv = Vault.for_board(board), Vault.global_(board.home)
    with vault.locked():
        lessons = _resolve(vault, ids)
        now = clock.iso()
        for lesson in lessons:
            lesson.update(status="approved", approved_by=caller.name, approved_at=now)
            vault.put(lesson)
            vault.drop(lesson["id"], PENDING)
        if lessons:
            vault.rebuild_hubs()
    if lessons:
        with gv.locked():
            for lesson in lessons:
                gv.put(lesson)
            gv.rebuild_hubs()
    return [x["id"] for x in lessons]


def reject(board, caller, ids):
    """Lead only. Drops pending lessons; capture will not propose them again."""
    with _lead_write(board, caller, "reject lessons"):
        return _reject(board, caller, ids)


def _reject(board, caller, ids):
    vault = Vault.for_board(board)
    with vault.locked():
        lessons = _resolve(vault, ids)
        state = vault.state()
        rejected = list(state.get("rejected") or [])
        for lesson in lessons:
            vault.drop(lesson["id"], PENDING)
            if lesson["id"] not in rejected:
                rejected.append(lesson["id"])
        vault.save_state({**state, "rejected": rejected})
    return [x["id"] for x in lessons]


def retire(board, caller, ids):
    with _lead_write(board, caller, "retire lessons"):
        vault = Vault.for_board(board)
        with vault.locked():
            approved = {x["id"]: x for x in vault.lessons() if x.get("status") == "approved"}
            lessons = []
            for raw in ids:
                matches = [x for key, x in approved.items() if raw and key.startswith(raw)]
                if len(matches) != 1:
                    raise Precondition(f"{len(matches)} approved lessons match {raw!r}", hint="raidlock lore list")
                lessons.append(matches[0])
            for lesson in lessons:
                lesson.update(status="retired")
                vault.put(lesson)
            vault.rebuild_hubs([x for x in vault.lessons() if x.get("status") == "approved"])
        global_vault = Vault.global_(board.home)
        with global_vault.locked():
            for lesson in lessons:
                global_vault.put(lesson)
            global_vault.rebuild_hubs([x for x in global_vault.lessons() if x.get("status") == "approved"])
        return [x["id"] for x in lessons]
