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


# ------------------------------------------------------------------------------------------------------------------------ eviction (reviewer B1 on PR #1170)
def test_creation_index_notes_only_pool_create_rows_and_patches_only_a_forgotten_creation():
    ix = U2.CreationIndex()
    for row in ({"type": "trade", "pool": "p1", "t_recv_ms": 5}, {"type": "pool_create", "pool": "", "t_recv_ms": 5}, {"type": "pool_create", "pool": "p1", "t_recv_ms": True},
                {"type": "pool_create", "pool": 7, "t_recv_ms": 5}, {"type": "pool_create", "t_recv_ms": 5}, None, "x"):
        ix.note(row)                                         # type: ignore[arg-type]
    assert len(ix) == 0
    ix.note({"type": "pool_create", "pool": "p1", "t_recv_ms": 1_000})
    ix.note({"type": "pool_create", "pool": "p1", "t_recv_ms": 2_000})      # the state overwrites the same way: the newest create row wins
    assert ix.created_ms("p1") == 2_000 and ix.created_ms("nope") is None
    forgot = {"id": "p1", "now_ms": 2_000 + 7_200_000, "created_known": False, "created_ms": None, "age_s": None, "age_since_first_seen_s": 60.0}
    got = ix.patch(forgot)
    assert got is not forgot and forgot["created_known"] is False and forgot["age_s"] is None        # a copy; the input is untouched
    assert got["created_known"] is True and got["created_ms"] == 2_000 and got["age_s"] == 7200.0 and got["created_kind"] == "pool_create"
    assert U2.admit(got) and C._age(got) == (7200.0, True)
    known = {**forgot, "created_known": True, "created_ms": 1, "age_s": 9.0}
    assert ix.patch(known) is known                          # the state still knows: returned as is
    other = {**forgot, "id": "p2"}
    assert ix.patch(other) is other and not U2.admit(other)  # no create row anywhere in the history: still refused
    assert ix.patch(None) is None
    nonow = {**forgot, "now_ms": None}
    assert ix.patch(nonow) is nonow


@pytest.mark.usefixtures("_env")
@pytest.mark.parametrize("shift_min,state_forgets", [(240, False), (290, True)])
def test_a_pool_created_before_the_6h_idle_eviction_is_still_admitted(tmp_path, shift_min, state_forgets):
    """The reviewer's synthetic case: the create row lies 5h50m (state keeps it) or 6h40m (the state evicts the idle pool and a later trade makes a new one with created_ms None)
    before the pool's first trade. In both the pool is migrated 2 h to 7 d before the screen, so U2 must admit it and the screen must equal the control's."""
    # A heartbeat row at T0 - 90 min makes the state run its periodic sweep (any applied row can) while the four pools are idle past 6 h; on the bare synthetic tape nothing applies
    # a row between the create rows and the first trades, so no sweep would run and the state would never forget anything.
    hb = {"v": 1, "type": "hb", "prog": "pumpswap", "t_recv_ms": T0 - 90 * MIN, "signature": "hb0"}
    control_run, control = _screen_row(tmp_path / "control", sorted([*tape_rows(), hb], key=lambda r: (r["t_recv_ms"], r["signature"])))
    rows = sorted(({**r, "t_recv_ms": r["t_recv_ms"] - shift_min * 60_000} if r["type"] == "pool_create" else r for r in [*tape_rows(), hb]), key=lambda r: (r["t_recv_ms"], r["signature"]))
    run, row = _screen_row(tmp_path / "shifted", rows)
    for k in range(4):
        raw = run.state.snapshot(POOLS[k])
        assert raw["created_known"] is (not state_forgets)                       # the shifted case really reproduces the eviction in the state itself
        patched = run._snap(POOLS[k])
        assert patched["created_known"] is True and C._age(patched)[1] is True
        assert patched["age_s"] == pytest.approx((raw["now_ms"] - run.creation.created_ms(POOLS[k])) / 1000.0)
        assert AGE_MIN <= patched["age_s"] <= AGE_MAX
    assert row["counts"]["age_unknown"] == 0 and "u2_age_unknown" not in run.counters
    assert row["counts"]["creation_restored"] == (4 if state_forgets else 0)                 # QP condition (a): the report-only count of admissions that depended on the restore
    assert run.counters.get("u2_creation_restored", 0) == (4 if state_forgets else 0) and control["counts"]["creation_restored"] == 0
    assert row["screen"] == control["screen"] and row["card_shas"] and not row["no_trade"]
    assert row["n_candidates"] == control["n_candidates"]


@pytest.mark.usefixtures("_env")
def test_watch_tick_tells_the_prefetcher_only_pools_u2_admits(tmp_path):
    seen = []

    class WatchChain:
        calls = 0

        def watch(self, items, hot, now_ms, vols=None):
            seen.append([q for q, _mint in items])

    leader = POOLS[3]
    no_create = [r for r in tape_rows() if not (r["type"] == "pool_create" and r["pool"] == leader)]
    run = make(tmp_path, rows=no_create)
    run.chain = WatchChain()
    T = T0 + HOUR + 60_000                                   # a minute tick after an hour of tape
    run.poll_to(T)
    run.watch_tick(T)
    assert seen and leader not in seen[-1] and set(seen[-1]) == {POOLS[0], POOLS[1], POOLS[2]}


# ------------------------------------------------------------------------------------------------------------------------ the config switch (QP C1) and the bot-id prefix (QP R1)
def test_u2_is_on_by_default_and_hv8_dump_turns_it_off_explicitly_and_says_so():
    assert R.Hv7Config().u2 is True
    src = (Path(__file__).resolve().parent / "hv8_dump.py").read_text()
    assert "phantom_filter=True, u2=False)" in src and "RULE U2 IS OFF HERE" in src


@pytest.mark.usefixtures("_env")
def test_u2_off_admits_a_pool_without_a_create_row_and_leaves_the_scan_row_as_before(tmp_path):
    leader = POOLS[3]
    rows = [r for r in tape_rows() if not (r["type"] == "pool_create" and r["pool"] == leader)]
    run = make(tmp_path / "off", rows=rows, cfg_kw={"u2": False})
    run.run(T0 + HOUR, T_E1 + MIN)
    row = rows_of(run, "SCAN", "period")[0]
    assert leader in row["screen"] and "age_unknown" not in row["counts"] and "creation_restored" not in row["counts"]       # HV8's view: the SCAN row carries no U2 key
    assert not [k for k in run.counters if k.startswith("u2_")]
    on = make(tmp_path / "on", rows=rows)
    on.run(T0 + HOUR, T_E1 + MIN)
    assert leader not in rows_of(on, "SCAN", "period")[0]["screen"]                                                          # default: fail closed


@pytest.mark.usefixtures("_env")
def test_the_runner_refuses_u2_off_for_a_tape_under_the_tip_receiver_copy(tmp_path, monkeypatch):
    feed = tmp_path / "feed"
    monkeypatch.setattr(R, "TIP_FEED_ROOT", str(feed))
    with pytest.raises(R.Hv7Error, match="u2_off_on_tip_source"):
        make(feed / "x", cfg_kw={"u2": False})                                             # make() writes its tape to <tmp>/tape, i.e. under the (patched) receiver root
    run = make(feed / "y")                                                                 # U2 on: allowed
    assert run.cfg.u2 is True
    assert make(tmp_path / "elsewhere", cfg_kw={"u2": False}).cfg.u2 is False              # off is allowed only away from the receiver copy


def test_the_four_tip_source_ids_are_exactly_hv7_0t_L_R_S_U_and_disjoint_from_the_public_tape_prefix():
    from tools import hv7_register as G

    want = ["hv7.0t-L", "hv7.0t-R", "hv7.0t-S", "hv7.0t-U"]
    assert R.BOT_PREFIX == "hv7.0t-" and [R.bot_id(a) for a in R.ARM_ORDER] == want and list(G.HV7_BOTS) == want
    assert not "hv7.0-L".startswith(R.BOT_PREFIX) and not "hv7.0t-L".startswith("hv7.0-")
    assert all(b.startswith("hv7.") for b in G.HV7_BOTS)


def test_the_register_docstring_does_not_rest_h_on_the_retired_public_tape_p0():
    from tools import hv7_register as G

    doc = G.__doc__ or ""
    assert "NOT the basis" in doc and "150/14" in doc and "at P0's 23.33" not in doc


def test_the_pack_state_field_listing_equals_hv4_snapshot_keys():
    import re

    pack = (Path(__file__).resolve().parents[1] / "ARTIFACTS" / "lab" / "hv7-registration-tip-1101.md").read_text()
    sec = pack[pack.index("## 6. State fields"):pack.index("## 6b.")]
    listed = re.search(r"contract list \(checked against the real snapshot by its contract test\): (.*?)\. `tools/test_hv7_tip_guard", sec, re.S).group(1)
    names = re.findall(r"`([a-z_0-9]+)`", listed)
    assert sorted(names) == sorted(C.SNAPSHOT_KEYS) and len(names) == 26
    row = pack.split("| bot ids |")[1].split("\n")[0]
    assert "`hv7.0t-L/R/S/U`" in row and "REQUIRED" in row
