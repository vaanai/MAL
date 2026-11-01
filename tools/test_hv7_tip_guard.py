"""Rule U2 and the tip READ_ROOT for HV7 on the fast-0 tip source (QP-RULING-1101-arena-tip-reader sections 2 and 4; DEC-031 Amendment 23 items 3, 7, 8).

SYNTHETIC tape and snapshots only (built here, or by tools/test_hv7_runner.py's fixture), StubModel only. No API call, no key, no network, no sealed path: the
path-guard tests call `check_path` on names only and never open anything. Nothing here says HV7 makes money.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

import tools.haiku_caller as hcall
from tools import hv4_cards as C
from tools import hv4_model as m4
from tools import hv6_cards as H
from tools import hv7_runner as R
from tools import hv7_tip_guard as U2
from tools.test_hv4_runner import MIN, T0  # noqa: F401
from tools.test_hv7_runner import HOUR, POOLS, T_E1, make, rows_of, tape_rows  # noqa: F401

AGE_MIN, AGE_MAX = float(C.AGE_MIN_S), float(C.AGE_MAX_S)


# ------------------------------------------------------------------------------------------------------------------------ the guard itself
def snap(**kw):
    return {"created_known": False, "age_s": None, "age_since_first_seen_s": None, **kw}


def test_unknown_create_pool_seen_between_2h_and_7d_is_refused():
    for first_seen in (AGE_MIN, AGE_MIN + 1, 3 * 86_400.0, AGE_MAX - 1, AGE_MAX):
        s = snap(age_since_first_seen_s=first_seen)
        age, known = C._age(s)
        assert age == first_seen and known is False          # HV4's own function would take the lower bound as the age ...
        assert AGE_MIN <= age <= AGE_MAX                      # ... which sits inside the band, so HV4 alone would admit the pool
        assert U2.admit(s) is False                           # U2 refuses it
        assert U2.created_known(s) is False


def test_known_create_pools_at_the_2h_and_7d_edges_are_admitted():
    assert AGE_MIN == 7200.0 and AGE_MAX == 7 * 86_400.0
    for age_s in (AGE_MIN, AGE_MIN + 0.001, 86_400.0, AGE_MAX):
        s = snap(created_known=True, age_s=age_s, age_since_first_seen_s=30.0)
        assert C._age(s) == (age_s, True)
        assert U2.admit(s) is True


def test_known_flag_without_a_numeric_age_is_refused_and_junk_fails_closed():
    assert U2.admit(snap(created_known=True, age_s=None)) is False
    assert U2.admit(snap(created_known=True, age_s="old")) is False
    assert U2.admit({}) is False
    assert U2.admit(None) is False                            # type: ignore[arg-type]
    assert U2.admit([("x", 1)]) is False                      # type: ignore[arg-type]


def test_u2_is_hv4s_known_branch_exactly():
    for ck in (True, False, None, 0, 1):
        for a in (None, 5.0, 7200.0, 9e9):
            s = snap(created_known=ck, age_s=a, age_since_first_seen_s=100.0)
            assert U2.admit(s) is bool(C._age(s)[1])


def test_guard_passes_admitted_pairs_in_order_and_counts_refused():
    pairs = [(snap(created_known=True, age_s=8000.0, id="a"), [1]), (snap(age_since_first_seen_s=9000.0, id="b"), [2]),
             (snap(created_known=True, age_s=9000.0, id="c"), [3]), (snap(id="d"), [4])]
    refused, seen = [0], []
    out = list(U2.guard(iter(pairs), refused, seen.append))
    assert [s["id"] for s, _ in out] == ["a", "c"] and [p for _, p in out] == [[1], [3]]
    assert refused == [2] and [s["id"] for s in seen] == ["b", "d"]
    assert list(U2.guard([])) == []                           # refused/on_refuse are optional


def test_the_guard_module_opens_no_file_and_edits_no_hv4_function():
    src = Path(U2.__file__).read_text()
    for tok in ("open(", "import os", "pathlib", "subprocess", "import socket", "requests"):
        assert tok not in src, tok
    assert "C._age(snap)" in src and "def _age" not in src   # HV4's function is called, not copied


# ------------------------------------------------------------------------------------------------------------------------ READ_ROOTS and the path guard
def test_read_roots_hold_the_tip_receiver_copy_and_the_old_roots():
    assert R.TIP_FEED_ROOT == "/data2/mal/arena-tip-feed/hv7"
    assert R.READ_ROOTS == (H.TAPE_ROOT, R.HV7_ROOT, R.TIP_FEED_ROOT)
    assert R.WRITE_ROOTS == (R.HV7_ROOT,)                     # the derived copy is never a write root of the runner
    for tok in H.DENY_TOKENS:
        assert tok not in R.TIP_FEED_ROOT                     # the path names no probe, shadow or canary (QP B1)


def test_check_path_accepts_the_receiver_copy_and_refuses_state_writes_and_neighbours():
    assert R.check_path("/data2/mal/arena-tip-feed/hv7") == "/data2/mal/arena-tip-feed/hv7"
    assert R.check_path("/data2/mal/arena-tip-feed/hv7/status.json").endswith("status.json")
    assert R.check_path(R.HV7_ROOT + "/x") == R.HV7_ROOT + "/x"
    for p in ("/data2/mal/arena-tip-feed/hv7/.writer/state.json", "/data2/mal/arena-tip-feed/hv7/.writer", "/data2/mal/arena-tip-feed/hv7/a/.writer/b"):
        with pytest.raises(H.PathRefused, match="tip_writer_state"):
            R.check_path(p)
    for p in ("/data2/mal/arena-tip-feed/hv7x/status.json", "/data2/mal/arena-tip-feed", "/data2/mal/arena-tip-feed/hv7/../hv8/tape",
              "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/tip-tape-archive", "/home/claude/mal-data/arena-tipfeed/tape"):
        with pytest.raises(H.PathRefused):
            R.check_path(p)
    with pytest.raises(H.PathRefused, match="not_allowlisted"):
        R.check_path("/data2/mal/arena-tip-feed/hv7/status.json", "w")         # read-only for the runner
    with pytest.raises(H.PathRefused):
        R.check_path("relative/hv7")


# ------------------------------------------------------------------------------------------------------------------------ the runner screen
@pytest.fixture()
def _env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(R, "READ_ROOTS", (base,))
    monkeypatch.setattr(R, "WRITE_ROOTS", (base,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")

    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)

    def no_api(*a, **k):
        raise AssertionError("HaikuCaller used in a test")

    monkeypatch.setattr(hcall.HaikuCaller, "__init__", no_api)
    monkeypatch.setattr(m4, "HaikuCaller", hcall.HaikuCaller)


def _screen_row(tmp_path, rows):
    run = make(tmp_path, rows=rows)
    run.run(T0 + HOUR, T_E1 + MIN)
    return run, rows_of(run, "SCAN", "period")[0]


@pytest.mark.usefixtures("_env")
def test_runner_screen_refuses_the_pool_without_a_create_row_and_counts_it(tmp_path):
    leader = POOLS[3]                                          # the highest-volume pool: the screen's first pick when it is admitted
    base_run, base = _screen_row(tmp_path / "base", tape_rows())
    assert leader in base["screen"] and base["counts"]["age_unknown"] == 0
    assert "u2_age_unknown" not in base_run.counters

    no_create = [r for r in tape_rows() if not (r["type"] == "pool_create" and r["pool"] == leader)]
    assert len(no_create) == len(tape_rows()) - 1
    run, row = _screen_row(tmp_path / "u2", no_create)
    snap_ = run.state.snapshot(leader)
    age, known = C._age(snap_)
    assert known is False and age is not None and AGE_MIN <= age <= AGE_MAX    # HV4 alone would have admitted it on the first-seen lower bound
    assert leader not in row["screen"] and leader not in row["vetoed"]
    assert row["counts"]["age_unknown"] == 1 and run.counters["u2_age_unknown"] == 1
    assert len(row["screen"]) == 3 and not row["no_trade"]     # the three known pools still screen
    for k in (0, 1, 2):
        assert POOLS[k] in row["screen"]


@pytest.mark.usefixtures("_env")
def test_runner_screen_with_two_unknown_pools_is_a_counted_no_trade(tmp_path):
    drop = {POOLS[2], POOLS[3]}
    rows = [r for r in tape_rows() if not (r["type"] == "pool_create" and r["pool"] in drop)]
    run, row = _screen_row(tmp_path, rows)
    assert row["no_trade"] and len(row["screen"]) == 2 and row["counts"]["age_unknown"] == 2
    assert not run.model.calls                                 # no model call, no entry
    for arm in R.ARM_ORDER:
        assert rows_of(run, arm, "entry") == []
