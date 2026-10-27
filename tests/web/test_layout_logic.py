import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "src/raidlock/web/static/app.js"


def run_js(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for board layout logic tests")
    source = APP.read_text().replace("init();\n})();", body + "\n})();")
    prelude = """
const assert = require('node:assert/strict');
global.window = {};
global.matchMedia = () => ({matches: false});
const elements = {};
global.document = {getElementById: id => elements[id]};
const storage = new Map();
global.localStorage = {getItem: k => storage.has(k) ? storage.get(k) : null,
  setItem: (k, v) => storage.set(k, String(v))};
"""
    result = subprocess.run([node, "-e", prelude + source], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_needs_merges_blocked_review_and_lead_questions_once():
    run_js("""
state.view = 'sample';
state.board = {lead: 'lead', approvals: {items: [
  {id: 'close-r', card: 'r', kind: 'close', commands: ['close r']}
]}};
state.projects = [{name: 'sample', chat: [
  {id: 'q', kind: 'msg', ts: '2026-01-01', to: 'lead', task: 't', text: 'Please choose?', from: {agent: 'worker'}},
  {id: 'q2', kind: 'msg', ts: '2026-01-02', to: 'lead', task: 't', text: 'Please choose?', from: {agent: 'worker'}}
]}];
const cards = [{id: 'r', lane: 'review'}, {id: 'b', lane: 'blocked'},
  {id: 'r2', lane: 'review'}, {id: 't', lane: 'todo'}];
const merged = groups(cards).needs;
assert.deepEqual(merged.map(x => x.card), ['r', 'r2', 't', 't']);
assert.deepEqual(groups(cards).blocked.map(c => c.id), ['b']);
assert.equal(merged.at(-1).kind, 'question');
assert.ok(merged.every(x => x.commands.length));
assert.equal(groups(cards.slice(0, 1)).needs.length, 1);
""")


def test_todo_oldest_first_with_deterministic_ties():
    run_js("""
const cards = [{id: 'new', lane: 'todo', created: '2026-02-01'},
 {id: 'z', lane: 'todo', created: '2026-01-01'},
 {id: 'a', lane: 'todo', created: '2026-01-01'}, {id: 'done', lane: 'done'}];
assert.deepEqual(todoOrder(cards).map(c => c.id), ['a', 'z', 'new']);
assert.equal(cards[0].id, 'new');
assert.equal(TODO_CAP, 5);
""")


def test_fold_defaults_and_viewer_override_with_unavailable_storage():
    run_js("""
elements.todo = {open: false, dataset: {}};
foldDefault('todo', true);
assert.equal(elements.todo.open, true);
store('rl.open.todo', false);
foldDefault('todo', true);
assert.equal(elements.todo.open, false);
store('rl.open.todo', true);
foldDefault('todo', false);
assert.equal(elements.todo.open, true);
localStorage.getItem = () => { throw Error('blocked'); };
localStorage.setItem = () => { throw Error('quota'); };
store('rl.open.todo', true);
foldDefault('todo', false);
assert.equal(elements.todo.open, false);
""")


def test_activity_merge_order_and_filters_exclude_chatter():
    run_js("""
state.log = [{id: 'record', kind: 'verify', ts: '2026-01-01', task: 'a'},
 {id: 'message', kind: 'msg', ts: '2026-01-03'},
 {id: 'note', kind: 'note', ts: '2026-01-04'}];
state.board = {cards: [{id: 'a', lane: 'done', updated: '2026-01-02', title: 'Done'}]};
assert.deepEqual(logRows().map(r => r.id), ['record', 'done-a']);
activityFilter = 'done';
assert.deepEqual(logRows().map(r => r.kind), ['done']);
activityFilter = 'log';
assert.deepEqual(logRows().map(r => r.id), ['record']);
state.logFilter = 'other';
assert.deepEqual(logRows(), []);
""")


def test_chat_is_workspace_scoped_deduplicated_and_oldest_first():
    run_js("""
state.view = 'a';
state.projects = [{name: 'a', chat: [
 {id: 'new', kind: 'msg', ts: '2026-02-01'}, {id: 'old', kind: 'msg', ts: '2026-01-01'}]},
 {name: 'b', chat: [{id: 'foreign', kind: 'msg', ts: '2026-03-01'}]}];
state.log = [{id: 'new', kind: 'msg', ts: '2026-02-01'}];
assert.deepEqual(workspaceChat().map(r => r.id), ['old', 'new']);
store('rl.chat.read.a', '2026-01-01');
assert.deepEqual(unreadChat().map(r => r.id), ['new']);
chatRead.set('a', {ts: '2026-02-01', ids: ['a:new']});
state.log.push({id: 'same-second', kind: 'msg', ts: '2026-02-01'});
assert.deepEqual(unreadChat().map(r => r.id), ['same-second']);
state.view = 'b';
state.log = [];
assert.deepEqual(unreadChat().map(r => r.id), ['foreign']);
""")


def test_layout_has_no_nested_reference_folds():
    source = APP.read_text()
    assert 'h("details", {class: "model-trust"}' not in source
    assert 'h("details", {id: "idleFold"' not in source
    assert '<details id="scoreBox"' not in APP.with_name("index.html").read_text()


def test_lore_approval_is_outside_needs_you():
    run_js("""
state.view = 'sample';
state.board = {lead: 'lead', approvals: {items: [{kind: 'lore', id: 'lesson'}]}};
state.projects = [{name: 'sample', chat: []}];
assert.equal(groups([]).needs.length, 0);
""")
    html = (APP.parent / "index.html").read_text()
    assert 'id="loreInbox"' in html and 'id="loreList"' in html


def test_realm_and_guild_levels_follow_closed_versus_open():
    run_js("""
assert.deepEqual(levelOf({}), {level: 1, closed: 0, open: 0, total: 0, rank: 'Recruit'});
assert.equal(levelOf({done: 10, cancelled: 50}).level, 60);
assert.equal(levelOf({done: 10, cancelled: 50}).rank, 'Mythic');
assert.equal(levelOf({todo: 1, doing: 1, review: 1, blocked: 1}).level, 1);
const half = levelOf({done: 5, todo: 5});
assert.equal(half.level, 30);
assert.equal(half.rank, 'Champion');
assert.deepEqual(sumCounts([{counts: {done: 2, todo: 1}}, {counts: {done: 1}}, {}]), {done: 3, todo: 1});
state.theme = 'plain';
assert.equal(levelBadge('Guild', {done: 1}), null);
""")
