import json
import os

import pytest

from raidlock.core import Usage, cost
from raidlock.core.errors import Precondition


def test_pricing_file_created_from_defaults(world):
    prices = cost.pricing(world.board.home)
    son = prices["claude-sonnet-5-5"]
    assert (son["input"], son["output"], son["cache_read"], son["cache_write"]) == (2.0, 10.0, 0.10, 2.5)
    assert prices["gpt-6-astra"]["input"] is None and "pricing page" in prices["gpt-6-astra"]["source"]
    assert prices["claude-opus-5-5"]["cache_write"] == 5.0
    assert os.path.exists(os.path.join(world.board.home, "pricing.json"))


@pytest.mark.parametrize(
    "model,usage,expected",
    [
        ("claude-opus-5-5", Usage(1_000_000, 0, 0, 0), 4.0),
        ("claude-opus-5-5", Usage(0, 1_000_000, 1_000_000, 1_000_000), 0.20 + 5.0 + 20.0),
        ("claude-sonnet-5-5", Usage(2_000_000, 1_000_000, 0, 500_000), 4.0 + 0.10 + 5.0),
        ("claude-haiku-5-5", Usage(1_000_000, 1_000_000, 1_000_000, 1_000_000), 0.10 + 0.01 + 0.125 + 0.50),
    ],
)
def test_formulas(model, usage, expected):
    assert cost.price(usage, cost.DEFAULT_PRICING[model]) == pytest.approx(expected)


def test_reported_usd_wins_and_unknown_model_unpriced(world):
    w = world.agent("w-one", model_id="claude-opus-5-5", billing="api")
    world.card("c1")
    r = world.board.record_cost("c1", w, usage=Usage(1000, 0, 0, 0), usd=1.5)
    assert r["entry"]["usd"] == 1.5 and r["entry"]["usd_source"] == "reported"
    r = world.board.record_cost("c1", w, model_id="mystery-model", usage=Usage(500, 0, 0, 500))
    assert r["entry"]["usd"] is None and r["entry"]["tokens"] == 1000
    assert r["total"]["usd"] == 1.5 and r["total"]["tokens"] == 2000 and r["total"]["unpriced_runs"] == 1


def test_default_model_from_agent(world):
    w = world.agent("w-one", model_id="claude-haiku-5-5", billing="api")
    world.card("c1")
    r = world.board.record_cost("c1", w, usage=Usage(0, 0, 0, 1_000_000))
    assert r["entry"]["model"] == "claude-haiku-5-5" and r["entry"]["usd"] == pytest.approx(0.5)


def test_totals_per_card_team_workspace(world):
    world.board.add_team(world.lead, "other", 2)
    w = world.agent("w-one", billing="api")
    world.card("c1")
    world.card("c2", team="other", locks="app/x")
    world.board.record_cost("c1", w, model_id="claude-opus-5-5", usage=Usage(1_000_000, 0, 0, 0))
    world.board.record_cost("c2", w, model_id="claude-opus-5-5", usage=Usage(0, 0, 0, 1_000_000))
    t = world.board.costs()
    assert t["cards"]["c1"]["usd"] == pytest.approx(4.0)
    assert t["teams"]["other"]["usd"] == pytest.approx(20.0)
    assert t["workspace"]["usd"] == pytest.approx(24.0) and t["workspace"]["runs"] == 2


def test_negative_rejected(world):
    w = world.agent("w-one")
    world.card("c1")
    with pytest.raises(Precondition):
        world.board.record_cost("c1", w, usage=Usage(-1, 0, 0, 0))


def test_cost_transcribed(world):
    w = world.agent("w-one")
    world.card("c1")
    world.board.record_cost("c1", w, model_id="claude-opus-5-5", usage=Usage(10, 0, 0, 10))
    rec = world.board.transcript.read(kind="cost")[-1]
    assert rec["parts"][0]["data"]["tokens"] == 20


def test_plan_billing_is_zero_with_api_equivalent(world):
    w = world.agent("w-plan", model_id="claude-opus-5-5", billing="plan")
    world.card("c1")
    e = world.board.record_cost("c1", w, usage=Usage(1_000_000, 0, 0, 0))["entry"]
    assert e["usd"] == 0.0 and e["usd_equiv"] == pytest.approx(4.0) and e["usd_source"] == "plan"


def test_missing_price_shows_tokens_only(world):
    w = world.agent("w-gpt", runtime="codex-cli", model_id="gpt-6-astra", billing="api")
    world.card("c1")
    e = world.board.record_cost(
        "c1",
        w,
        usage=Usage.from_codex(
            {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50, "reasoning_output_tokens": 20}
        ),
    )["entry"]
    assert e["usd"] is None and e["usage"] == {
        "input": 600,
        "cached_input": 400,
        "cache_write": 0,
        "output": 50,
        "reasoning_output": 20,
    }


def test_from_anthropic():
    u = Usage.from_anthropic(
        {"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 30, "output_tokens": 40}
    )
    assert (u.input, u.cached_input, u.cache_write, u.output, u.total) == (10, 20, 30, 40, 100)


def test_spend_grouping_and_global(tmp_path):
    from corehelpers import World

    a, b = World(tmp_path, "ws-a"), World(tmp_path, "ws-b", remote="git@github.com:acme/other.git")
    wa = a.agent("w-claude", model_id="claude-opus-5-5", billing="api")
    wb = b.agent("w-codex", runtime="codex-cli", model_id="gpt-6-astra", billing="plan")
    a.card("c1")
    b.card("c2")
    a.board.record_cost("c1", wa, usage=Usage(1_000_000, 0, 0, 0))
    a.board.record_cost("c1", wa, usage=Usage(0, 0, 0, 1_000_000))
    b.board.record_cost("c2", wb, usage=Usage(500, 0, 0, 500))
    local = a.board.spend(by="model")
    assert local["total"]["usd"] == pytest.approx(24.0) and local["total"]["runs"] == 2
    everywhere = a.board.spend(by="provider", everywhere=True)
    keys = {r["key"]: r for r in everywhere["rows"]}
    assert keys["anthropic"]["usd"] == pytest.approx(24.0) and keys["openai"]["tokens"] == 1000
    assert {r["key"] for r in a.board.spend(by="workspace", everywhere=True)["rows"]} == {"ws-a", "ws-b"}
    assert a.board.spend(by="day")["rows"][0]["key"] == a.board.card("c1")["costs"][0]["ts"][:10]
    assert a.board.spend(by="week")["rows"][0]["key"].count("-W") == 1
    assert a.board.spend(since="2999-01-01")["total"]["runs"] == 0


def test_billing_detection(tmp_path):
    from raidlock.core import billing

    (tmp_path / "codex").mkdir()
    (tmp_path / "codex" / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}), encoding="utf-8")
    assert billing.detect("codex-cli", env={"CODEX_HOME": str(tmp_path / "codex")})["billing"] == "plan"
    assert billing.detect("codex-cli", env={"OPENAI_API_KEY": "x"})["billing"] == "api"
    assert billing.detect("claude-code", env={"ANTHROPIC_API_KEY": "x"})["billing"] == "api"
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / ".claude.json").write_text(json.dumps({"oauthAccount": {"x": 1}}), encoding="utf-8")
    assert billing.detect("claude-code", env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")})["billing"] == "plan"
    assert billing.detect("shell", env={})["billing"] == "unknown"
    assert billing.detect("shell", declared="api", env={})["billing_source"] == "declared"


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -float("inf")])
def test_nonfinite_money_rejected(world, value):
    worker = world.agent("worker")
    world.card("c1")
    with pytest.raises(Precondition, match="finite"):
        world.board.record_cost("c1", worker, usd=value)


def test_existing_nonfinite_rows_rejected_before_sum(world):
    worker = world.agent("worker")
    world.card("c1")
    world.board.record_cost("c1", worker, usd=1)
    world.board.index.conn.execute("UPDATE costs SET usd = ?", (float("inf"),))
    with pytest.raises(Precondition, match="invalid-data"):
        world.board.spend()


def test_existing_nan_card_cost_is_invalid(world):
    worker = world.agent("worker")
    world.card("c1")
    world.board.record_cost("c1", worker, usd=1)
    card = world.board.card("c1")
    card["costs"][0]["usd"] = float("nan")
    world.board._save_card(card)
    with pytest.raises(Precondition, match="invalid-data"):
        world.board.spend()


def test_local_runner_reporting_a_priced_model_bills_its_provider(world):
    from raidlock.core import cost as C

    assert C.provider_for("claude-sonnet-5-5", C.DEFAULT_PRICING, "local") == "anthropic"
    assert C.provider_for("claude-sonnet-5-5", C.DEFAULT_PRICING, "openai") == "openai"
    assert C.provider_for("unknown-model", C.DEFAULT_PRICING, "local") == "local"
    world.card("c1")
    runner = world.agent("shell-runner", runtime="shell")
    r = world.board.record_cost("c1", runner, model_id="claude-sonnet-5-5")
    assert r["entry"]["provider"] == "anthropic"
