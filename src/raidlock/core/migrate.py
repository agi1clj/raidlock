import hashlib
import json
import os
import re
import shlex
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import clock, model, safeio, store
from . import paths as P
from .errors import Precondition
from .portable import remove_tree
from .transcript import _clean

V1_FIELDS = (
    "id",
    "title",
    "lane",
    "owner",
    "team",
    "ticket",
    "scope",
    "locks",
    "depends",
    "verify",
    "updated",
    "model",
    "tokens",
    "duration_ms",
    "cost_usd",
)
_NOTE = re.compile(r"^- (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) (.*)$")
_BY = re.compile(r"^([A-Za-z0-9_.-]+): (.*)$")
V1_FILES = ("agents.json", "teams.json", "leases.json", "LOG.md", "settings.json", "config.json")


def needs_migration(board_dir):
    if os.path.exists(os.path.join(board_dir, "board.json")):
        return False
    cards = os.path.join(board_dir, "cards")
    has_cards = os.path.isdir(cards) and any(n.endswith(".md") for n in os.listdir(cards))
    return (
        has_cards
        or any(os.path.exists(os.path.join(board_dir, f)) for f in V1_FILES)
        or os.path.isdir(os.path.join(board_dir, "v1"))
    )


def reject_legacy(board_dir):
    if needs_migration(board_dir):
        raise Precondition(
            "legacy v1 board found (not migrated); run raid migrate to upgrade",
            board=str(board_dir),
            hint="raid migrate --board " + str(board_dir),
        )


def _write_json(path, data):
    safeio.write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _write_doc(path, key, data):
    _write_json(path, {"schema": store.SCHEMA, key: data})


def _write_jsonl(path, rows):
    safeio.write_text(path, "".join(json.dumps(_clean(row)) + "\n" for row in rows))


def _state_path(board_dir, home):
    if Path(os.path.abspath(home)).is_relative_to(Path(os.path.abspath(board_dir))):
        raise Precondition("migration home must be outside the untrusted board")
    key = hashlib.sha256(os.path.abspath(board_dir).encode()).hexdigest()
    return safeio.check(Path(home) / "migrations" / (key + ".json"))


def _state(board_dir, home):
    path = _state_path(board_dir, home)
    state = json.loads(safeio.read(path)) if path.exists() else {}
    if state and state.get("board_identity") != _board_identity(board_dir):
        raise Precondition("migration state belongs to a different board")
    return state


def _board_identity(board_dir):
    info = safeio.check(board_dir).stat()
    return [info.st_dev, info.st_ino]


def _manifest(root):
    root = safeio.check(root, tree=True)
    return {
        str(p.relative_to(root)): None if p.is_dir() else hashlib.sha256(safeio.read(p)).hexdigest()
        for p in sorted(root.rglob("*"))
    }


def backups(board_dir, home=None):
    from . import registry

    state = _state(board_dir, home or registry.home())
    backup = state.get("backup")
    return [Path(backup)] if backup else []


def _read_json(path, default=None):
    return json.loads(safeio.read(path)) if Path(path).exists() else default


def _reject_existing_outputs(board_dir, home):
    if _state(board_dir, home):
        return
    root = Path(board_dir)
    if list((root / "cards").glob("*.json")) or list((root / "transcript").glob("*")):
        raise Precondition("untrusted board contains v2 output without trusted migration state")


def preflight(board_dir, allow_auth_downgrade=False):
    root = safeio.check(board_dir, tree=True)
    source = root / "v1" if (root / "v1").is_dir() else root
    settings = _settings(source)[0]
    if settings["auth"] == "off" and not allow_auth_downgrade:
        raise Precondition("authentication downgrade requires --allow-auth-downgrade")
    seen = set()
    for path in (source / "cards").glob("*.md"):
        meta, body = _read_v1_card(path)
        cid = model.card_id(meta["id"])
        if cid in seen:
            raise Precondition(f"duplicate card id: {cid}")
        seen.add(cid)
        for cid in _split(meta["depends"]):
            model.card_id(cid)
        if meta["owner"]:
            model.agent_name(meta["owner"])
        for note in _notes(body):
            if note["by"]:
                model.agent_name(note["by"])
    for name, agent in _read_json(source / "agents.json", {}).items():
        model.agent_name(name)
        if agent.get("card"):
            model.card_id(agent["card"])
    for cid, lease in _read_json(source / "leases.json", {}).items():
        model.card_id(cid)
        if lease.get("agent"):
            model.agent_name(lease["agent"])
    for path in (source / "inbox").glob("*.jsonl"):
        model.agent_name(path.stem)
        for line in safeio.read(path).decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for key in ("from", "to", "agent"):
                if row.get(key) and row[key] != "all":
                    model.agent_name(row[key])
            for key in ("card", "task"):
                if row.get(key):
                    model.card_id(row[key])
    return source


def upgrade(board_dir, home, allow_auth_downgrade=False, lead=None):
    from . import identity
    from .db import Index

    preflight(board_dir, allow_auth_downgrade)
    lead = _lead(_agents(_read_json(os.path.join(board_dir, "agents.json"), {})), lead)
    safeio.check(home, tree=True)
    _reject_existing_outputs(board_dir, home)
    _state_path(board_dir, home)
    safeio.check(Path(board_dir).parent / ".gitignore")
    if not needs_migration(board_dir):
        raise Precondition("no legacy v1 board found")
    state = _state(board_dir, home)
    if state.get("backup"):
        backup = state["backup"]
        if _manifest(backup) != state.get("manifest"):
            raise Precondition("migration backup integrity check failed")
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = str(Path(board_dir).absolute()) + ".backup-" + stamp
        safeio.copytree(board_dir, backup + ".partial")
        safeio.replace(backup + ".partial", backup)
        state = {
            "uid": state.get("uid") or uuid.uuid4().hex,
            "backup": backup,
            "manifest": _manifest(backup),
            "board_identity": _board_identity(board_dir),
        }
        _write_json(_state_path(board_dir, home), state)
    workspace = Path(board_dir).resolve().parent
    if any((parent / ".git").exists() for parent in (workspace, *workspace.parents)):
        ignore = workspace / ".gitignore"
        content = safeio.read(ignore).decode() if ignore.exists() else ""
        pattern = "/" + Path(board_dir).name + ".backup-*"
        if pattern not in content.splitlines():
            safeio.write_text(
                str(ignore), content + ("\n" if content and not content.endswith("\n") else "") + pattern + "\n"
            )
    result = migrate(board_dir, Index(home), allow_auth_downgrade=allow_auth_downgrade, lead=lead)
    meta = _read_json(os.path.join(board_dir, "board.json"))
    from .lead import mint

    secret_path = identity.token_path(home, meta["uid"], meta["lead"])
    safeio.write_text(secret_path, mint(1) + "\n")
    result["secret_file"] = secret_path
    result["takeover"] = shlex.join(
        [
            "raid",
            "--board",
            str(Path(board_dir).resolve()),
            "lead",
            "takeover",
            "--lead-secret",
            identity.token_path(home, meta["uid"], meta["lead"]),
        ]
    )
    result["backup"] = backup
    return result


def rollback(board_dir, home, caller=None, board=None):
    from .board import Board
    from .lock import FileLock

    board_dir = safeio.check(board_dir, tree=True)
    if board and (Path(board.dir).resolve() != board_dir or Path(board.home).resolve() != Path(home).resolve()):
        raise Precondition("rollback authorization belongs to a different board")
    with FileLock(os.path.join(board_dir, ".lock")):
        board = board or (Board.open(board_dir) if Path(board_dir, "board.json").exists() else None)
        if board and caller is None:
            raise Precondition("rollback requires current lead authorization")
        return _rollback(board_dir, home, board, caller)


def _rollback(board_dir, home, board=None, caller=None):
    from .db import Index, purge

    safeio.check(board_dir, tree=True)
    state = _state(board_dir, home)
    choices = backups(board_dir, home)
    if not choices:
        raise Precondition("no recorded migration backup found")
    backup = Path(state["backup"])
    if _manifest(backup) != state.get("manifest"):
        raise Precondition("migration backup integrity check failed")
    if board:
        board._require_lead(caller, "roll back migration")
    uid = state.get("uid")
    staging = str(board_dir) + ".restore-" + uuid.uuid4().hex
    safeio.copytree(backup, staging)
    if _manifest(staging) != state.get("manifest"):
        remove_tree(staging)
        raise Precondition("migration backup changed during restore")
    root = Path(board_dir)
    for child in root.iterdir():
        if child.name == ".lock":
            continue
        if child.is_dir():
            remove_tree(child)
        else:
            safeio.unlink(child)
    for child in Path(staging).iterdir():
        if child.name != ".lock":
            safeio.replace(child, root / child.name)
    remove_tree(staging)
    if uid:
        index = Index(home)
        with index.write() as conn:
            purge(conn, uid)
    safeio.unlink(_state_path(board_dir, home))
    return {"restored": str(board_dir), "backup": str(backup)}


def _backup(board_dir):
    target = os.path.join(board_dir, "v1")
    if os.path.isdir(target):
        return target
    staging = target + ".partial"
    remove_tree(staging, ignore_errors=True)
    safeio.mkdir(staging)
    for name in V1_FILES:
        src = os.path.join(board_dir, name)
        if os.path.exists(src):
            safeio.write_text(Path(staging, name), safeio.read(src))
    for folder in ("cards", "inbox"):
        src = os.path.join(board_dir, folder)
        if os.path.isdir(src):
            safeio.copytree(src, os.path.join(staging, folder))
    safeio.replace(staging, target)
    return target


def _read_v1_card(path):
    text = safeio.read(path).decode("utf-8", errors="replace")
    head, _, body = text.partition("\n---\n")
    meta = {k: "" for k in V1_FIELDS}
    for line in head.splitlines()[1:]:
        k, _, v = line.partition(": ")
        meta[k.strip()] = v.strip()
    return meta, body


def _notes(body):
    out = []
    for line in body.splitlines():
        m = _NOTE.match(line.strip())
        if not m:
            continue
        by = _BY.match(m.group(2))
        out.append(
            {"ts": m.group(1), "by": by.group(1) if by else "", "model": "", "text": by.group(2) if by else m.group(2)}
        )
    return out


def _split(value):
    return [x for x in re.split(r"[,\s]+", value or "") if x]


def _locks(value):
    out = []
    for raw in _split(value):
        try:
            out.append(P.canonical(raw))
        except Precondition:
            continue
    return out


def _num(value, cast):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return cast(0)


def _card(meta, body):
    lane = meta["lane"] if meta["lane"] in ("todo", "doing", "review", "blocked", "done") else "todo"
    card = {
        "id": meta["id"],
        "title": meta["title"],
        "lane": lane,
        "owner": meta["owner"],
        "team": meta["team"],
        "ticket": meta["ticket"],
        "scope": meta["scope"],
        "output": "",
        "description": "".join(line for line in body.splitlines(keepends=True) if not _NOTE.match(line.strip())),
        "verify": meta["verify"],
        "verify_cmd": "",
        "locks": _locks(meta["locks"]),
        "depends": _split(meta["depends"]),
        "reviewers": [],
        "fence": 0,
        "released": False,
        "created": meta["updated"] or clock.iso(),
        "updated": meta["updated"] or clock.iso(),
        "notes": _notes(body),
        "reviews": [],
        "verifications": [],
        "costs": [],
    }
    if meta.get("cost_usd") or meta.get("tokens"):
        card["costs"].append(
            {
                "ts": card["updated"],
                "by": meta["owner"],
                "model": meta.get("model", ""),
                "usage": {"input": 0, "cached": 0, "cache_write": 0, "output": 0},
                "tokens": _num(meta.get("tokens"), int),
                "usd": _num(meta.get("cost_usd"), float),
                "usd_source": "migrated",
                "duration_ms": _num(meta.get("duration_ms"), int),
            }
        )
    return card


def _agents(data):
    out = {}
    for name, a in (data or {}).items():
        out[name] = {
            "team": a.get("team", ""),
            "role": a.get("role", "worker"),
            "joined": a.get("joined", ""),
            "last_seen": a.get("last_seen", ""),
            "card": a.get("card", ""),
            "provider": "",
            "runtime": "",
            "model": "",
            "model_version": "",
            "effort": "",
            "model_source": "unknown",
            "session": "",
        }
    return out


def _teams(data):
    return {
        n: {"max": t.get("max", 2), "mission": t.get("mission", ""), "repos": t.get("repos", [])}
        for n, t in (data or {}).items()
    }


def _inbox(backup, board_dir):
    src = os.path.join(backup, "inbox")
    if not os.path.isdir(src):
        return
    for name in os.listdir(src):
        if name.endswith(".jsonl"):
            rows = [
                json.loads(line) for line in safeio.read(os.path.join(src, name)).decode().splitlines() if line.strip()
            ]
            for r in rows:
                r["task"] = r.pop("card", r.get("task", ""))
                r.setdefault("urgent", False)
                r.setdefault("model", "")
            _write_jsonl(os.path.join(board_dir, "inbox", name), rows)


def migrate(board_dir, index, allow_auth_downgrade=False, lead=None):
    """Migrate a team-board v1 `.board` in place. v1 files are copied to `.board/v1/` first; board.json is written
    last."""
    preflight(board_dir, allow_auth_downgrade)
    home = os.path.dirname(index.path)
    safeio.check(home, tree=True)
    _reject_existing_outputs(board_dir, home)
    state = _state(board_dir, home)
    if not state:
        state = {"uid": uuid.uuid4().hex, "board_identity": _board_identity(board_dir)}
        _write_json(_state_path(board_dir, home), state)
    provenance = Path(board_dir, "checkout-provenance.json")
    if provenance.exists():
        safeio.unlink(provenance)
    lore = Path(board_dir, "lore")
    dropped_lore = lore.exists()
    if dropped_lore:
        remove_tree(lore)
    backup = _backup(board_dir)
    settings, unmapped, explicit = _settings(backup)
    uid = state["uid"]
    cards = {}
    cards_dir = os.path.join(backup, "cards")
    for name in sorted(os.listdir(cards_dir)) if os.path.isdir(cards_dir) else []:
        if name.endswith(".md"):
            card = _card(*_read_v1_card(os.path.join(cards_dir, name)))
            if card["id"]:
                cards[card["id"]] = card
    agents = _agents(_read_json(os.path.join(backup, "agents.json"), {}))
    history = _read_json(os.path.join(backup, "leases.json"), {})
    leases = len(history)
    for cid, lease in history.items():
        if cid in cards:
            cards[cid]["imported_lease"] = {"active": False, "untrusted": True, "claimed": lease}
    for existing in Path(board_dir, "cards").glob("*.json"):
        safeio.unlink(existing)
    for card in cards.values():
        _write_doc(os.path.join(board_dir, "cards", card["id"] + ".json"), "card", card)
    _write_doc(os.path.join(board_dir, "agents.json"), "agents", agents)
    _write_doc(
        os.path.join(board_dir, "teams.json"), "teams", _teams(_read_json(os.path.join(backup, "teams.json"), {}))
    )
    for stale in ("leases.json",):
        if os.path.exists(os.path.join(board_dir, stale)):
            safeio.unlink(os.path.join(board_dir, stale))
    _inbox(backup, board_dir)
    transcript_count = _transcript(backup, board_dir, index, uid)
    lead = _lead(agents, lead)
    _write_json(
        os.path.join(board_dir, "board.json"),
        {
            "schema": store.SCHEMA,
            "uid": uid,
            "workspace": os.path.basename(os.path.dirname(os.path.abspath(board_dir))),
            "created": clock.iso(),
            "lead": lead,
            "migrated_from": 1,
            "settings": settings,
            "explicit_settings": explicit,
        },
    )
    for name in os.listdir(os.path.join(board_dir, "cards")) if os.path.isdir(os.path.join(board_dir, "cards")) else []:
        if name.endswith(".md"):
            safeio.unlink(os.path.join(board_dir, "cards", name))
    return {
        "cards": len(cards),
        "agents": len(agents),
        "leases": leases,
        "backup": backup,
        "lead": lead,
        "unmapped_settings": unmapped,
        "dropped_untrusted_lore": dropped_lore,
        "transcript": transcript_count,
    }


def _lead(agents, selected=None):
    candidates = sorted(n for n, a in agents.items() if a.get("role") == "lead")
    if selected:
        if selected not in candidates and not (selected == "lead" and ("lead" in agents or not candidates)):
            raise Precondition("--lead must name a v1 lead-role agent")
        return selected
    if "lead" in agents:
        return "lead"
    if len(candidates) > 1:
        raise Precondition("multiple v1 leads; select the confirmed lead with --lead <name>")
    return candidates[0] if candidates else "lead"


def _settings(backup):
    from .board import DEFAULT_SETTINGS, _apply_setting, normalize_review_setting

    settings = dict(DEFAULT_SETTINGS, require_review=normalize_review_setting(0))
    unmapped, explicit = {}, []
    aliases = {"ttl": "ttl_minutes", "default_ttl": "ttl_minutes", "default_ttl_minutes": "ttl_minutes"}
    for filename in ("config.json", "settings.json"):
        raw = _read_json(os.path.join(backup, filename), {})
        for key, value in raw.get("settings", raw).items():
            target = aliases.get(key, key)
            try:
                if target == "require_review" and isinstance(value, dict):
                    for subkey, item in value.items():
                        _apply_setting(settings, target + "." + subkey, item)
                        explicit.append(target + "." + subkey)
                else:
                    _apply_setting(settings, target, value)
            except (Precondition, TypeError, ValueError):
                if target in DEFAULT_SETTINGS or target.startswith("require_review."):
                    raise Precondition(f"invalid migration setting {filename}:{key}") from None
                unmapped[f"{filename}:{key}"] = value
            else:
                explicit.append(target)
    return settings, unmapped, sorted(set(explicit))


def _transcript(backup, board_dir, index, uid):
    from . import transcript as T

    path = Path(backup) / "LOG.md"
    for existing in Path(board_dir, "transcript").glob("*.jsonl"):
        safeio.unlink(existing)
    if not path.exists():
        T.Transcript(board_dir, index, uid).reindex()
        return 0
    records = []
    for number, line in enumerate(safeio.read(path).decode().splitlines()):
        match = _NOTE.match(line)
        ts, event = (match.group(1), match.group(2)) if match else ("", line)
        words = event.split()
        verb = words[0] if words else ""
        card, actor = "", ""
        if verb in {"new", "claim", "lock", "move", "release", "reap", "cost", "note", "heartbeat"}:
            card = words[1].rstrip(":") if len(words) > 1 else ""
        by = re.search(r"\bby ([A-Za-z0-9_.-]+)", event)
        if by:
            actor = by.group(1)
        elif verb in {"join", "inbox"} and len(words) > 1:
            actor = words[1]
        elif verb == "msg":
            message = re.match(r"msg \S+ (\S+) -> \S+(?: on (\S+))?", event)
            if message:
                actor, card = message.group(1), message.group(2) or ""
        record = T.make(
            "status",
            {"agent": ""},
            "[untrusted v1 import] " + line,
            task=card,
            context=uid,
            src="v1-log",
            message_id=f"v1-log:{uid}:{number}",
            data={"claimed_action": verb, "claimed_actor": actor, "trust": "untrusted v1 import"},
        )
        record.update(ts=ts, trust="untrusted v1 import", claimed_action=verb, claimed_actor=actor, claimed_text=line)
        records.append(record)
    _write_jsonl(os.path.join(board_dir, "transcript", "000000.jsonl"), records)
    T.Transcript(board_dir, index, uid).reindex()
    return len(records)
