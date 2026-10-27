import os
from dataclasses import asdict, dataclass

from . import store

PLACEHOLDER = "fill in from your provider's pricing page"
DEFAULT_PRICING = {
    "claude-opus-5-5": {
        "provider": "anthropic",
        "input": 4.0,
        "output": 20.0,
        "cache_read": 0.20,
        "cache_write": 5.0,
        "source": "raidlock defaults",
    },
    "claude-sonnet-5-5": {
        "provider": "anthropic",
        "input": 2.0,
        "output": 10.0,
        "cache_read": 0.10,
        "cache_write": 2.5,
        "source": "raidlock defaults",
    },
    "claude-haiku-5-5": {
        "provider": "anthropic",
        "input": 0.10,
        "output": 0.50,
        "cache_read": 0.01,
        "cache_write": 0.125,
        "source": "raidlock defaults",
    },
    "gpt-6-astra": {
        "provider": "openai",
        "input": None,
        "output": None,
        "cache_read": None,
        "cache_write": None,
        "source": PLACEHOLDER,
    },
    "gemini-model-id": {
        "provider": "google",
        "input": None,
        "output": None,
        "cache_read": None,
        "cache_write": None,
        "source": PLACEHOLDER,
    },
}
BILLING = ("api", "plan", "unknown")


@dataclass
class Usage:
    """Token usage of one run. `input` is uncached input only, `cached_input` cache reads, `cache_write` cache writes.
    `reasoning_output` is the part of `output` spent on reasoning (informational; priced inside `output`)."""

    input: int = 0
    cached_input: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning_output: int = 0

    @property
    def total(self):
        return self.input + self.cached_input + self.cache_write + self.output

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_codex(cls, usage):
        """From Codex `turn.completed.usage` (input_tokens includes cached_input_tokens)."""
        cached = int(usage.get("cached_input_tokens", 0) or 0)
        return cls(
            input=max(0, int(usage.get("input_tokens", 0) or 0) - cached),
            cached_input=cached,
            output=int(usage.get("output_tokens", 0) or 0),
            reasoning_output=int(usage.get("reasoning_output_tokens", 0) or 0),
        )

    @classmethod
    def from_anthropic(cls, usage):
        """From an Anthropic `usage` object (input_tokens excludes cache reads and writes)."""
        return cls(
            input=int(usage.get("input_tokens", 0) or 0),
            cached_input=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_write=int(usage.get("cache_creation_input_tokens", 0) or 0),
            output=int(usage.get("output_tokens", 0) or 0),
        )


def pricing(home):
    """USD per million tokens by model, from `<home>/pricing.json` (created from defaults; edit it freely)."""
    path = os.path.join(home, "pricing.json")
    data = store.read_json(path)
    if data is None:
        store.write_doc(path, "models", DEFAULT_PRICING, unit="USD per MTok")
        return {k: dict(v) for k, v in DEFAULT_PRICING.items()}
    store.check_schema(data, path)
    return data.get("models", {})


def provider_for(model, prices, provider=""):
    """The provider to bill: the agent's own, except that a local runner (a shell command) reporting a priced model
    bills that model's provider."""
    if provider in ("", "local"):
        found = (prices or {}).get(model or "", {})
        if isinstance(found, dict) and found.get("provider"):
            return found["provider"]
    return provider


def price(usage, rates):
    """USD for `usage` at `rates`, or None when the model or a needed rate is missing."""
    if not rates:
        return None
    parts = (
        (usage.input, "input"),
        (usage.cached_input, "cache_read"),
        (usage.cache_write, "cache_write"),
        (usage.output, "output"),
    )
    total = 0.0
    for tokens, key in parts:
        rate = rates.get(key)
        if tokens and rate is None:
            return None
        total += tokens * (rate or 0)
    if rates.get("input") is None or rates.get("output") is None:
        return None
    return total / 1e6


def entry(model, usage, usd, duration_ms, prices, by, ts, billing="unknown", provider=""):
    """One run's cost record.

    api: usd = reported USD, else priced.  plan: usd = 0 (subscription), usd_equiv = API-equivalent estimate.
    unknown: usd = reported USD if any, else None; usd_equiv = priced estimate. Missing price: usd None."""
    computed = price(usage, prices.get(model))
    estimate = usd if usd is not None else computed
    if billing == "plan":
        real, source = 0.0, "plan"
    elif billing == "api":
        real, source = estimate, "reported" if usd is not None else ("priced" if computed is not None else "unpriced")
    else:
        real, source = usd, "reported" if usd is not None else ("estimate" if computed is not None else "unpriced")
    rates = prices.get(model) or {}
    return {
        "ts": ts,
        "by": by,
        "model": model,
        "provider": provider or rates.get("provider", ""),
        "billing": billing,
        "usage": usage.as_dict(),
        "tokens": usage.total,
        "usd": _round(real),
        "usd_equiv": _round(estimate),
        "usd_source": source,
        "duration_ms": int(duration_ms or 0),
    }


def _round(value):
    return round(value, 6) if value is not None else None


def card_total(card):
    rows = card.get("costs", [])
    return {
        "usd": round(sum(r["usd"] for r in rows if r.get("usd") is not None), 6),
        "usd_equiv": round(sum(r.get("usd_equiv") or 0 for r in rows), 6),
        "tokens": sum(r.get("tokens", 0) for r in rows),
        "duration_ms": sum(r.get("duration_ms", 0) for r in rows),
        "runs": len(rows),
        "unpriced_runs": sum(1 for r in rows if r.get("usd") is None),
    }


def totals(cards):
    """{cards: {id: total}, teams: {team: total}, workspace: total} from card files."""
    by_card = {c["id"]: card_total(c) for c in cards if c.get("costs")}
    teams = {}
    for c in cards:
        if c["id"] in by_card:
            _add(teams.setdefault(c.get("team") or "", _zero()), by_card[c["id"]])
    workspace = _zero()
    for t in by_card.values():
        _add(workspace, t)
    return {"cards": by_card, "teams": teams, "workspace": workspace}


def _zero():
    return {"usd": 0.0, "usd_equiv": 0.0, "tokens": 0, "duration_ms": 0, "runs": 0, "unpriced_runs": 0}


def _add(acc, t):
    for k in acc:
        acc[k] = round(acc[k] + t[k], 6) if k in ("usd", "usd_equiv") else acc[k] + t[k]
