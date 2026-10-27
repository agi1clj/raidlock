# Enforcement contracts

Run `python -m pytest -q -p no:cacheprovider tests/enforce` with Python 3.11+ and pytest.
All test names below live in `test_must_not.py`. Covered means the assertion passes;
xfail means a known enforcement gap, marked `strict=True` with a reason. No rules
are skipped. Unexpected passes fail CI, and setup/runtime errors are not swallowed
by xfail (only assertion failures qualify).

| Rule from the Must-not table or P-rules | Test (after `test_`) | Status |
|---|---|---|
| P1/P2: hooks must add no output on allow, exit 0 | `p1_p2_hooks_must_not_add_output_on_allow` (all seven handlers) | covered |
| P1: refuse overhead over budget | `p1_token_budget_must_not_accept_over_budget_overhead` | xfail |
| P2: do not publish unreproduced competitor numbers | `p2_overhead_bench_must_not_publish_unreproduced_competitor_number` | xfail |
| P3: done must reject a receipt from another HEAD | `p3_verify_receipt_must_not_trust_sha_different_from_head_done` | covered |
| P3: pr must reject a receipt from another HEAD | `p3_verify_receipt_must_not_trust_sha_different_from_head_pr` | covered |
| P3: dirty checkouts must be refused without writing a receipt | `p3_verify_must_not_run_in_dirty_worker_checkout` | covered |
| P4: native-bridge spike must report go/no-go before publish | `p4_native_bridge_must_not_publish_without_spike_decision` | xfail |
| P5: do not change defaults for single-provider boards | `p5_gate_defaults_must_not_change_single_provider_boards` | covered |
| P5: second provider must enable cross-provider review | `p5_must_not_allow_done_without_cross_provider_review` | covered |
| P5: second provider must require verification | `p5_must_not_allow_done_without_verify` | covered |
| P6: reputation must not score self-reports | `p6_reputation_must_not_score_agent_self_reports` | covered |
| P6: duel must require verified evidence | `p6_duel_must_not_select_winner_from_self_reports` | covered |
| P7: foresight must warn at claim time, never block the claim | `p7_foresight_must_not_block_claim_and_must_warn_before_work` | covered |
| P8: never read undocumented Claude internals | `p8_limits_must_not_read_undocumented_claude_internals` | covered |
| P8: no assignment at 90% weekly usage or unknown headroom | `p8_next_routing_must_not_assign_at_90_percent_or_unknown_headroom` | covered |
| P9: never resurrect past max attempts or budget | `p9_resurrect_must_not_exceed_max_attempts_or_budget` | covered |
| P9: hand off at 80% context fill | `p9_must_not_continue_at_80_percent_context_fill` | covered |
| P10: never apply suggested locks without confirmation | `p10_lock_infer_must_not_apply_locks_without_confirmation` | covered |
| P11: never inject unapproved lessons | `p11_lore_must_not_inject_unapproved_lessons` | covered |
| P11: quote lessons, top three, 60 tokens each, within brief cap | `p11_lore_must_not_inject_instructions_or_exceed_top_three_and_brief_cap` | covered |
| P11: required fields must not bypass the brief cap | `p11_lore_must_not_exceed_brief_cap_with_oversized_required_fields` | xfail |

Missing commands use proposed CLI contracts because their implementations and
interfaces do not exist yet. Align their arguments with the implementation when
those cards land; an unknown command or argument never counts as enforcement.
P4's publish prerequisite is currently a planning rule, represented as a future
executable refusal. The tests do not execute a release or call external services.

Fixtures isolate board state and credentials in pytest temporary directories and
supply a deterministic process table for lead authentication. Git scenarios use
local clones and two existing source commits; they create no commits and require
no network. Git history must be available. Lock inference stubs Git discovery;
foresight supplies a merge-tree conflict for two open checkouts. Neither stubs the
product's decision about whether to apply locks or grant a claim.
