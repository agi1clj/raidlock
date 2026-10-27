import pytest
from hypothesis import given
from hypothesis import strategies as st

from raidlock.core import paths as P
from raidlock.core import repos
from raidlock.core.errors import Denied, Precondition

segment = st.text(alphabet="abcXYZ019_-", min_size=1, max_size=6)
path_parts = st.lists(segment, min_size=1, max_size=5)


@given(path_parts)
def test_canonical_is_idempotent(parts):
    p = P.canonical("/".join(parts))
    assert P.canonical(p) == p


@given(path_parts)
def test_variants_canonicalize_equal(parts):
    plain = "/".join(parts)
    variants = ["./" + plain, plain + "/", "\\".join(parts), "/./".join(parts), "//".join(parts), "  " + plain + " "]
    for v in variants:
        assert P.canonical(v) == P.canonical(plain)


@given(path_parts, st.integers(min_value=0, max_value=4))
def test_dotdot_always_rejected(parts, at):
    parts = list(parts)
    parts.insert(min(at, len(parts)), "..")
    with pytest.raises(Precondition):
        P.canonical("/".join(parts))


@given(path_parts)
def test_absolute_rejected(parts):
    for prefix in ("/", "C:/", "c:\\", "~/", "\\\\"):
        with pytest.raises(Precondition):
            P.canonical(prefix + "/".join(parts))


@given(path_parts, path_parts)
def test_overlap_is_symmetric_and_contains_implies_overlap(a, b):
    pa, pb = "/".join(a), "/".join(b)
    assert P.overlaps(pa, pb) == P.overlaps(pb, pa)
    if P.contains(pa, pb):
        assert P.overlaps(pa, pb)


@given(path_parts, path_parts)
def test_child_is_contained(a, b):
    parent = "/".join(a)
    assert P.contains(parent, parent + "/" + "/".join(b))
    assert not P.contains(parent + "/" + "/".join(b), parent)


@given(path_parts)
def test_casefold_overlap(parts):
    p = "/".join(parts)
    assert P.overlaps(p.upper(), p.lower(), casefold=True)


def test_prefix_needs_segment_boundary():
    assert not P.overlaps("repo/mod", "repo/modules")
    assert P.overlaps("repo/modules", "repo/modules/x")


def test_control_characters_rejected():
    for bad in ("repo/a\nb", "repo/\x00", "repo/\u2028x"):
        with pytest.raises(Precondition):
            P.canonical(bad)


def test_root_rejected():
    for bad in (".", "./", "", "  "):
        with pytest.raises(Precondition):
            P.canonical(bad)


@pytest.mark.parametrize("variant", ["app/./modules/x", "app\\modules\\x", "app//modules/x", "app/modules/x/"])
def test_audit_repro_variants_conflict(world, variant):
    holder = world.agent("w-holder")
    other = world.agent("w-other")
    world.card("held", locks="app/modules")
    world.claim("held", holder)
    world.card("probe", locks=variant)
    with pytest.raises(Denied):
        world.claim("probe", other)


def test_audit_repro_dotdot_rejected(world):
    with pytest.raises(Precondition):
        world.card("probe", locks="app/other/../modules")


def test_audit_repro_case_variant_on_case_insensitive_fs(world, monkeypatch):
    monkeypatch.setattr(repos, "case_insensitive", lambda folder: True)
    holder = world.agent("w-holder")
    other = world.agent("w-other")
    world.card("held", locks="app/modules")
    world.claim("held", holder)
    world.card("probe", locks="APP/Modules/x")
    with pytest.raises(Denied):
        world.claim("probe", other)


def test_case_variant_distinct_on_case_sensitive_fs(world, monkeypatch):
    monkeypatch.setattr(repos, "case_insensitive", lambda folder: False)
    (world.root / "App").mkdir(exist_ok=True)
    holder = world.agent("w-holder")
    world.card("held", locks="app/modules")
    world.claim("held", holder)
    assert world.board.check_paths("app/Modules") == []


@pytest.mark.parametrize(
    "url,expected",
    [
        ("git@github.com:Acme/App.git", "github.com/acme/app"),
        ("https://github.com/acme/app", "github.com/acme/app"),
        ("https://user:pw@GitHub.com:443/acme/app.git/", "github.com/acme/app"),
        ("ssh://git@github.com:22/acme/app.git", "github.com/acme/app"),
        ("github.com:acme/app", "github.com/acme/app"),
    ],
)
def test_remote_normalization(url, expected):
    assert repos.normalize_remote(url) == expected


def test_whole_repo_lock_overlaps_everything(world):
    holder = world.agent("w-holder")
    world.card("held", locks="app")
    world.claim("held", holder)
    assert world.board.check_paths("app/deep/file.py")
