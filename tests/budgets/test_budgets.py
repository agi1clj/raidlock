import argparse
import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from raidlock import budgets, cli
from raidlock.core import Board, Usage, clock, cost, db
from raidlock.core.errors import Precondition


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(db.close_all)
        self.env = patch.dict(os.environ, {"RAIDLOCK_HOME": self.tmp.name + "/home"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.time = patch.object(clock, "now", return_value=datetime.datetime(2026, 12, 15, 12, tzinfo=datetime.UTC))
        self.time.start()
        self.addCleanup(self.time.stop)
        self.board, token = Board.init(self.tmp.name + "/ws", env={})
        self.lead = self.board.caller(lead_secret=token, env={})
        self.board.new_card(self.lead, "First", card_id="first")
        self.board.new_card(self.lead, "Second", card_id="second")
        self.settings = {}
        self.reader = patch.object(self.board, "get_setting", side_effect=lambda: dict(self.settings))
        self.reader.start()
        self.addCleanup(self.reader.stop)

    def spend(self, usd, tokens=0, billing="api", card="first", at=None):
        make_entry = cost.entry

        def dated_entry(*args, **kwargs):
            row = make_entry(*args, **kwargs)
            row["ts"] = at
            return row

        with patch.object(cost, "entry", side_effect=dated_entry) if at else contextlib.nullcontext():
            self.board.record_cost(card, self.lead, usd=usd, usage=Usage(input=tokens), billing=billing)

    def check(self, card="first"):
        return budgets.check_before_delegate(self.board, card)

    def test_unlimited_and_zero(self):
        result = self.check()
        self.assertTrue(result["allowed"])
        self.assertTrue(all(v is None for v in result["remaining"].values()))
        self.settings[budgets.DAILY] = 0
        self.assertFalse(self.check()["allowed"])

    def test_card_usd_and_tokens(self):
        self.settings["budget.card.first"] = {"usd": 3, "tokens": 100}
        self.spend(2, 40)
        self.spend(99, 999, card="second")
        result = self.check({"id": "first", "costs": []})
        self.assertEqual(result["remaining"]["card_usd"], 1)
        self.assertEqual(result["remaining"]["card_tokens"], 60)
        self.spend(1, 60)
        result = self.check()
        self.assertFalse(result["allowed"])
        self.assertEqual(len(result["reasons"]), 2)
        self.spend(1, 1)
        self.assertEqual(self.check()["remaining"]["card_usd"], 0)

    def test_token_default_and_override(self):
        self.settings[budgets.TOKENS] = 100
        self.spend(0, 100, billing="plan")
        self.assertFalse(self.check()["allowed"])
        self.settings["budget.card.first"] = {"tokens": 200}
        self.assertTrue(self.check()["allowed"])
        self.settings["budget.card.first"] = {"tokens": None}
        self.assertIsNone(self.check()["remaining"]["card_tokens"])

    def test_plan_equivalent_without_api_double_counting(self):
        self.settings.update({"budget.card.first": {"usd": 10}, budgets.DAILY: 10, budgets.MONTHLY: 10})
        self.spend(3, 30)
        self.spend(7, 70, billing="plan")
        result = self.check()
        self.assertFalse(result["allowed"])
        self.assertEqual(result["used"], {"card_usd": 10, "card_tokens": 100, "daily_usd": 10, "monthly_usd": 10})
        self.settings[budgets.COUNT_PLAN] = False
        result = self.check()
        self.assertTrue(result["allowed"])
        self.assertEqual(result["used"], {"card_usd": 3, "card_tokens": 100, "daily_usd": 3, "monthly_usd": 3})

    def test_calendar_boundaries_and_other_cards(self):
        self.settings.update({budgets.DAILY: 10, budgets.MONTHLY: 50})
        for usd, ts in (
            (100, "2026-11-30T23:59:59Z"),
            (20, "2026-12-01T00:00:00Z"),
            (4, "2026-12-15T00:00:00Z"),
            (2, "2026-12-15T23:59:59Z"),
            (8, "2026-12-16T00:00:00Z"),
            (100, "2027-01-01T00:00:00Z"),
        ):
            self.spend(usd, card="second", at=ts)
        result = self.check()
        self.assertEqual(result["remaining"]["daily_usd"], 4)
        self.assertEqual(result["remaining"]["monthly_usd"], 16)
        self.assertEqual(result["used"]["card_usd"], 0)

    def test_workspace_isolation(self):
        other, token = Board.init(self.tmp.name + "/other", env={})
        lead = other.caller(lead_secret=token, env={})
        other.new_card(lead, "Other", card_id="first")
        other.record_cost("first", lead, usd=100, billing="plan")
        self.settings[budgets.DAILY] = 1
        self.assertEqual(self.check()["used"]["daily_usd"], 0)

    def test_decimal_boundary(self):
        self.settings["budget.card.first"] = {"usd": 0.3}
        self.spend(0.1)
        self.spend(0.2, billing="plan")
        self.assertFalse(self.check()["allowed"])
        self.assertEqual(self.check()["remaining"]["card_usd"], 0)

    def test_validation(self):
        cases = [(budgets.DAILY, x) for x in (-1, True, "NaN", "Infinity", "no", 1e309)]
        cases += [
            (budgets.TOKENS, 1.5),
            (budgets.COUNT_PLAN, "yes"),
            ("budget.no", 1),
            ("budget.card.first", {}),
            ("budget.card.first", {"usd": -1}),
            ("budget.card.first", {"tokens": 1.5}),
            ("budget.card.first", {"other": 1}),
        ]
        with patch.object(self.board, "set_setting") as setter:
            for key, value in cases:
                with self.subTest(key=key, value=value), self.assertRaises(Precondition):
                    budgets.set_budget(self.board, self.lead, key, value)
            setter.assert_not_called()

    def test_config_api_forwarding(self):
        with patch.object(self.board, "set_setting") as setter:
            budgets.set_budget(self.board, self.lead, "budget.card.first", {"usd": 2, "tokens": 3})
            setter.assert_called_once_with(self.lead, "budget.card.first", {"usd": 2.0, "tokens": 3})
            budgets.set_budget(self.board, self.lead, budgets.DAILY, None)
            setter.assert_called_with(self.lead, budgets.DAILY, None)

    def test_core_persists_and_clears_validated_budget_config(self):
        budgets.set_budget(self.board, self.lead, budgets.DAILY, 10)
        self.assertEqual(self.board.settings[budgets.DAILY], 10)
        budgets.set_budget(self.board, self.lead, budgets.DAILY, None)
        self.assertNotIn(budgets.DAILY, self.board.settings)

    def test_invalid_stored_setting_and_missing_card(self):
        self.settings[budgets.DAILY] = -1
        with self.assertRaises(Precondition):
            self.check()
        self.settings.clear()
        with self.assertRaises(Precondition):
            self.check("missing")

    def test_cli_registration_and_handlers(self):
        commands = {}

        def add(name, help_text, setup, handler, **kwargs):
            commands[name] = (setup, handler)

        budgets.register_cli(add)
        setup, handler = commands["budget"]
        parser = argparse.ArgumentParser()
        setup(parser)
        ctx = argparse.Namespace(board=self.board, caller=lambda: self.lead)
        args = parser.parse_args(["set", "budget.card.first", '{"usd":2,"tokens":30}'])
        with patch.object(self.board, "set_setting") as setter:
            result, text = handler(ctx, args)
            setter.assert_called_once()
            self.assertEqual(json.loads(text), result)
        result, _ = handler(ctx, parser.parse_args(["show"]))
        self.assertTrue(result["settings"][budgets.COUNT_PLAN])
        result, _ = handler(ctx, parser.parse_args(["show", "first"]))
        self.assertTrue(result["allowed"])
        with self.assertRaises(Precondition):
            handler(ctx, parser.parse_args(["set", budgets.DAILY, "invalid"]))

    def test_registered_cli_json(self):
        with patch.dict(cli.COMMANDS), patch.object(cli, "load_extensions"):
            budgets.register_cli(cli.add_command)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cli.main(["budget", "show", "first", "--board", self.board.dir, "--json"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["allowed"])


if __name__ == "__main__":
    unittest.main()
