"""Provider routing: `raid next` suggests the next claimable card and the provider and model to run it on.

Rules live in `.board/routing.json` and are written only by the lead. Routing reads plan headroom from
`raidlock.limits`; it never claims, delegates or changes a card, so every board gate still applies."""

import copy
import fnmatch
import json
import re

from . import limits as LM
from .core import paths as P
from .core import store
from .core.errors import Precondition
from .core.hints import lead_cmd, lead_secret_src
from .core.identity import RUNTIME_SHORT, short_runtime
from .core.model import card_id, line_text

PROVIDERS = ("claude", "codex", "gemini", "shell")
QUOTAS = ("plan", "none")
FILE = "routing.json"
RULE_KEYS = ("name", "label", "path", "team", "provider", "model", "fallback")
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/@-]{1,100}$")
LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
WEEKLY_MINUTES = 10080
FIVE_HOUR_MINUTES = 300
DEFAULTS = {
    "rules": [],
    "labels": {},
    "default": {"provider": "", "model": "", "fallback": []},
    "weekly_max": 90,
    "five_hour_max": 95,
    "quota": {"shell": "none"},
}


def config(board):
    """The routing config merged over the defaults."""
    found = store.read_doc(board._p(FILE), "routing")
    out = copy.deepcopy(DEFAULTS)
    if not isinstance(found, dict):
        raise Precondition(f"corrupt {FILE}: expected an object")
    if not all(isinstance(found.get(k) or {}, dict) for k in ("quota", "labels")):
        raise Precondition(f"corrupt {FILE}: quota and labels must be objects")
    for key in ("rules", "default", "weekly_max", "five_hour_max"):
        if key in found:
            out[key] = validate(key, found[key])
    for name, value in (found.get("quota") or {}).items():
        out["quota"][name] = validate("quota." + name, value)
    for cid, value in (found.get("labels") or {}).items():
        out["labels"][cid] = validate("label." + cid, value)
    return out


def _provider(value, key):
    value = short_runtime(value) if isinstance(value, str) else value
    if value not in PROVIDERS:
        raise Precondition(f"{key} must be one of {', '.join(PROVIDERS)}", key=key)
    return value


def _providers(value, key):
    if not isinstance(value, list):
        raise Precondition(f"{key} must be a list of providers", key=key)
    out = []
    for item in value:
        item = _provider(item, key)
        if item not in out:
            out.append(item)
    return out


def _model(value, key):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not MODEL_ID.match(value):
        raise Precondition(f"{key} is not a valid model id", key=key)
    return value


def _percent(value, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 100:
        raise Precondition(f"{key} must be a number in (0, 100]", key=key)
    return value


def _labels(value, key):
    if not isinstance(value, list):
        raise Precondition(f"{key} must be a list of labels", key=key)
    for item in value:
        if not isinstance(item, str) or not LABEL.match(item):
            raise Precondition(f"{key}: invalid label {item!r}; use [a-z0-9_-], max 40", key=key)
    return sorted(set(value))


def check_rule(rule, index=0):
    """Validate one rule: a match (label, path or team), a provider, an optional model and fallbacks."""
    key = f"rules[{index}]"
    if not isinstance(rule, dict) or set(rule) - set(RULE_KEYS):
        raise Precondition(f"{key} must be an object with keys from {', '.join(RULE_KEYS)}", key=key)
    if not any(rule.get(k) for k in ("label", "path", "team")):
        raise Precondition(f"{key} needs label, path or team to match", key=key)
    out = {"name": line_text(rule.get("name") or f"rule-{index + 1}", key + ".name", 80)}
    if rule.get("label"):
        out["label"] = _labels([rule["label"]], key + ".label")[0]
    if rule.get("path"):
        raw = str(rule["path"]).strip()
        P.canonical(re.sub(r"[*?\[\]]", "x", raw))
        out["path"] = raw.rstrip("/")
    if rule.get("team"):
        out["team"] = line_text(rule["team"], key + ".team", 40)
    out["provider"] = _provider(rule.get("provider"), key + ".provider")
    out["model"] = _model(rule.get("model"), key + ".model")
    out["fallback"] = [p for p in _providers(rule.get("fallback") or [], key + ".fallback") if p != out["provider"]]
    return out


def validate(key, value):
    """Validate one config key and return the value to store; None clears it."""
    if key == "rules":
        if value is None:
            return []
        if not isinstance(value, list):
            raise Precondition("rules must be a list")
        return [check_rule(rule, i) for i, rule in enumerate(value)]
    if key == "default":
        if value is None:
            return copy.deepcopy(DEFAULTS["default"])
        if not isinstance(value, dict) or set(value) - {"provider", "model", "fallback"}:
            raise Precondition("default must be an object with provider, model and fallback")
        provider = _provider(value["provider"], "default.provider") if value.get("provider") else ""
        return {
            "provider": provider,
            "model": _model(value.get("model"), "default.model"),
            "fallback": _providers(value.get("fallback") or [], "default.fallback"),
        }
    if key in ("weekly_max", "five_hour_max"):
        return DEFAULTS[key] if value is None else _percent(value, key)
    if key.startswith("quota."):
        _provider(key[len("quota.") :], key)
        if value is not None and value not in QUOTAS:
            raise Precondition(f"{key} must be one of {', '.join(QUOTAS)}", key=key)
        return value
    if key.startswith("label."):
        card_id(key[len("label.") :])
        return None if value is None else _labels(value, key)
    raise Precondition(
        f"unknown routing key {key}; known: rules, default, weekly_max, five_hour_max, quota.<provider>, label.<card>",
        key=key,
    )


def set_config(board, caller, key, value):
    """Lead-only: validate and store one routing key under the board lock."""
    value = validate(key, value)
    if key.startswith("label."):
        board.card(key[len("label.") :])
    with board.lead_write(caller, f"change routing {key}"):
        cfg = config(board)
        if key.startswith("quota."):
            cfg["quota"] = dict(cfg.get("quota") or {})
            if value is None:
                cfg["quota"].pop(key[len("quota.") :], None)
            else:
                cfg["quota"][key[len("quota.") :]] = value
        elif key.startswith("label."):
            cfg["labels"] = dict(cfg.get("labels") or {})
            if value:
                cfg["labels"][key[len("label.") :]] = value
            else:
                cfg["labels"].pop(key[len("label.") :], None)
        else:
            cfg[key] = value
        store.write_doc(board._p(FILE), "routing", cfg)
        board.index.bump(board.uid)
    return {"key": key, "value": value, "routing": cfg}


def labels_of(cfg, card):
    return list((cfg.get("labels") or {}).get(card["id"], []))


def _path_match(pattern, lock):
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(lock, pattern) or fnmatch.fnmatchcase(lock + "/", pattern)
    return P.overlaps(pattern, lock)


def matches(rule, card, cfg):
    if rule.get("label") and rule["label"] not in labels_of(cfg, card):
        return False
    if rule.get("team") and rule["team"] != card.get("team"):
        return False
    return not (rule.get("path") and not any(_path_match(rule["path"], lock) for lock in card.get("locks", [])))


def rule_for(cfg, card):
    return next((r for r in cfg.get("rules") or [] if matches(r, card, cfg)), None)


def _window_kind(window):
    minutes = window.get("window_minutes") or 0
    if minutes >= WEEKLY_MINUTES:
        return "weekly"
    if 0 < minutes <= FIVE_HOUR_MINUTES:
        return "5h"
    return "other"


def headroom(provider, cfg, collected):
    """{provider, ok, left, reason, exhausted, retry_after}. Unknown or stale limits count as no headroom."""
    if (cfg.get("quota") or {}).get(provider) == "none":
        return {"provider": provider, "ok": True, "left": 100.0, "reason": "no plan quota", "exhausted": False}
    found = next((p for p in (collected or {}).get("providers", []) if p.get("provider") == provider), None)
    status = (found or {}).get("status", "unavailable")
    windows = (found or {}).get("windows") or []
    base = {"provider": provider, "ok": False, "left": 0.0, "exhausted": False}
    if status != "ok" or not windows:
        detail = status if status != "ok" else "no windows"
        return {**base, "reason": f"{provider} headroom unknown ({detail}); treated as no headroom"}
    resets = [w.get("resets_in_seconds") for w in windows if isinstance(w.get("resets_in_seconds"), int)]
    if found.get("limit_reached"):
        return {
            **base,
            "exhausted": True,
            "retry_after": max(resets) if resets else None,
            "reason": f"{provider} quota exhausted ({found['limit_reached']})",
        }
    five = None
    for w in windows:
        kind, used = _window_kind(w), w.get("used_percent")
        if not isinstance(used, (int, float)):
            continue
        if kind == "weekly" and used >= cfg["weekly_max"]:
            return {
                **base,
                "exhausted": used >= 100,
                "retry_after": w.get("resets_in_seconds"),
                "reason": f"{provider} weekly window {used:g}% used (no new work at {cfg['weekly_max']:g}% or more)",
            }
        if kind == "5h":
            five = used
            if used >= cfg["five_hour_max"]:
                return {
                    **base,
                    "retry_after": w.get("resets_in_seconds"),
                    "reason": f"{provider} 5h window {used:g}% used; wait for the reset",
                }
    peak = max((w["used_percent"] for w in windows if isinstance(w.get("used_percent"), (int, float))), default=0)
    left = 100.0 - (five if five is not None else peak)
    return {**base, "ok": True, "left": left, "reason": f"{provider} has {left:g}% 5h headroom"}


def route(board, card, provider=None, exclude=(), collected=None, cfg=None):
    """Pick a provider and model for `card`. Returns {provider, model, rule, reason, checked}; provider is "" when
    nothing has headroom. An explicit `provider` is checked but never swapped."""
    cfg = cfg or config(board)
    collected = LM.collect() if collected is None else collected
    rule = rule_for(cfg, card)
    base = rule or cfg.get("default") or {}
    if provider:
        candidates = [_provider(provider, "provider")]
        ranked = False
    elif base.get("provider"):
        candidates = [base["provider"], *base.get("fallback", [])]
        ranked = False
    else:
        candidates = list(base.get("fallback") or []) or [p for p in PROVIDERS if p != "shell"]
        ranked = True
    candidates = [p for i, p in enumerate(candidates) if p not in exclude and p not in candidates[:i]]
    checked = [headroom(p, cfg, collected) for p in candidates]
    usable = [c for c in checked if c["ok"]]
    if ranked:
        usable.sort(key=lambda c: -c["left"])
    pick = usable[0] if usable else None
    model = ""
    if pick and pick["provider"] == base.get("provider"):
        model = base.get("model", "")
    return {
        "card": card["id"],
        "provider": pick["provider"] if pick else "",
        "model": model,
        "rule": (rule or {}).get("name", "default"),
        "labels": labels_of(cfg, card),
        "reason": pick["reason"] if pick else "; ".join(c["reason"] for c in checked) or "no candidate provider",
        "checked": checked,
        "fallback": bool(pick and checked and pick["provider"] != checked[0]["provider"]),
    }


def allowed_for(cfg, card, provider):
    """True when the card's rule (if any) lets `provider` run it."""
    rule = rule_for(cfg, card)
    return rule is None or provider in [rule["provider"], *rule.get("fallback", [])]


def caller_provider(board, caller):
    record = board.agents().get(caller.name, {}) if caller.name and not caller.is_lead else {}
    short = RUNTIME_SHORT.get(record.get("runtime", ""), "")
    return short if short in PROVIDERS else ""


def suggest(board, caller, provider=None, for_review=False, collected=None):
    """The next card for the caller with its route. Read-only: no claim, no lease, no card change."""
    cfg = config(board)
    collected = LM.collect() if collected is None else collected
    agent = caller.name if caller.name and not caller.is_lead else None
    provider = provider or caller_provider(board, caller)
    if provider:
        _provider(provider, "provider")
        room = headroom(provider, cfg, collected)
        if not room["ok"]:
            raise Precondition(
                f"no new work routed to {provider}: {room['reason']}",
                provider=provider,
                retry_after=room.get("retry_after"),
                hint=_headroom_hint(board) if "headroom unknown" in room["reason"] else "raidlock limits",
            )
    queue = board.claimable(agent=agent, for_review=for_review)
    cards = {c["id"]: c for c in board.cards()}
    open_ids = [cid for cid, v in queue.items() if v["ok"] and cid in cards]
    open_ids.sort(key=lambda cid: (cards[cid]["lane"] != "todo", cards[cid].get("created", ""), cid))
    skipped = []
    agents = board.agents()
    for cid in open_ids:
        card = cards[cid]
        exclude = ()
        if for_review:
            owner_short = RUNTIME_SHORT.get(agents.get(card.get("owner"), {}).get("runtime", ""), "")
            exclude = (owner_short,) if owner_short in PROVIDERS and owner_short != "shell" else ()
            if provider and provider in exclude:
                skipped.append(f"{cid}: review needs a provider other than {provider}")
                continue
        elif provider and not allowed_for(cfg, card, provider):
            skipped.append(f"{cid}: rule {rule_for(cfg, card)['name']} routes it elsewhere")
            continue
        r = route(board, card, provider=provider, exclude=exclude, collected=collected, cfg=cfg)
        if not r["provider"]:
            skipped.append(f"{cid}: {r['reason']}")
            continue
        return {
            **r,
            "title": card["title"],
            "lane": card["lane"],
            "command": _command(r, agent, for_review, board if caller.is_lead else None),
        }
    reason = "; ".join(skipped[:5]) if skipped else ("nothing waiting for review" if for_review else "nothing open")
    unknown = any("headroom unknown" in item for item in skipped)
    raise Precondition(f"no card to route: {reason}", hint=_headroom_hint(board) if unknown else "raidlock claimable")


def _headroom_hint(board):
    return (
        "raidlock limits shows what is known; run raidlock limits --install-statusline once to record Claude Code "
        "plan usage, or "
        "mark a provider as having no plan quota: " + lead_cmd(board, "routing set quota.<provider> '\"none\"'")
    )


def _command(r, agent, for_review, lead_board=None):
    if agent:
        return f"raidlock review {r['card']} --verdict approve" if for_review else f"raidlock claim {r['card']}"
    parts = ["raidlock", "delegate", r["card"], "--to", r["provider"]]
    if lead_board is not None:
        parts[1:1] = ["--lead-secret", lead_secret_src(lead_board)]
    if for_review:
        parts += ["--as", "reviewer"]
    if r.get("model"):
        parts += ["--model", r["model"]]
    return " ".join(parts)


def _next_setup(p):
    p.add_argument(
        "--provider", default=None, type=short_runtime, choices=PROVIDERS, help="route only to this provider"
    )
    p.add_argument("--for-review", action="store_true", help="suggest a card waiting for review instead")


def _next_handler(ctx, a):
    r = suggest(ctx.board, ctx.caller(), provider=a.provider, for_review=a.for_review)
    model = f" model {r['model']}" if r["model"] else ""
    via = " (fallback)" if r["fallback"] else ""
    text = f"next: {r['card']}: {r['title']}\nroute: {r['provider']}{model}{via}, rule {r['rule']}; {r['reason']}"
    return r, text + f"\nNext: {r['command']}"


def _routing_setup(p):
    p.add_argument("routing_action", nargs="?", default="show", choices=("show", "set", "explain"))
    p.add_argument("key", nargs="?", help="config key for set, card id for explain")
    p.add_argument("value", nargs="?", help="JSON value for set; null clears")


def _routing_handler(ctx, a):
    if a.routing_action == "set":
        if a.key is None or a.value is None:
            raise Precondition("routing set needs a key and a JSON value")
        try:
            value = json.loads(a.value)
        except ValueError:
            raise Precondition("routing value must be valid JSON") from None
        r = set_config(ctx.board, ctx.caller(), a.key, value)
        return r, f"routing {a.key} set"
    if a.routing_action == "explain":
        if not a.key:
            raise Precondition("routing explain needs a card id")
        r = route(ctx.board, ctx.board.card(a.key))
        lines = [f"{a.key}: rule {r['rule']} -> {r['provider'] or 'no provider'}"]
        lines += [f"  {c['provider']}: {'ok' if c['ok'] else 'no'}, {c['reason']}" for c in r["checked"]]
        return r, "\n".join(lines)
    cfg = config(ctx.board)
    return {"routing": cfg}, json.dumps(cfg, indent=2, sort_keys=True)


def register_cli(add_command):
    add_command("next", "suggest the next card for you and the provider to run it on", _next_setup, _next_handler)
    add_command(
        "routing", "show, set or explain provider routing rules", _routing_setup, _routing_handler, mutating=True
    )
