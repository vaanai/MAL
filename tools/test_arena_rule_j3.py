"""J3 rule family (QP R9(d) exploration): trigger counting at k = 1 / 5 / 20, the canonical-pool filter, R9(b) at the 360 s edges, one position per parent, one attempt per
(parent, child), the fixed hold, no child pricing, determinism and restart. SYNTHETIC tapes only (tools/arena_j3_testkit.py); nothing here opens the real arena tape,
a ledger or bots.json, and nothing here says any J3 cell makes money."""

from __future__ import annotations

import ast
import collections
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import arena_rule_j3 as J
from tools.arena_j3_testkit import B, MINT, OTHER, POOL, SEC, V, V_OUT, WSOL, RuleSim, World, at, scenario
from tools.arena_rule_j3 import J3Rule, make_rule, make_rules
from tools.arena_rules import Buy, PositionView, Sell, stack_config
from tools.arena_rules_testkit import decision_files, decision_rows, ledger_rows, make_host
from tools.arena_state import ArenaState

REPO = Path(__file__).resolve().parent.parent
T_RUN_END = B + 3_590 * SEC
WORLD, BUYS = scenario()
ROWS = WORLD.rows
PARENT_POOLS = {POOL["PA"], POOL["PD"], POOL["PE"]}
CHILD_POOLS = {POOL[k] for k in ("CA1", "CA2", "CA3", "CB", "CC", "CD", "CE", "CE2", "CS", "CF")}


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")  # register_bot's now_ms / verify_sha overrides (the test kit registers the scratch books)
        yield


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def tick_at_or_after(ms: int) -> int:
    return B + -(-(ms - B) // 1000) * 1000


def hosted(tmp: Path, rules=None, rows=None):
    h = make_host(tmp, rules or make_rules(), rows=rows if rows is not None else ROWS, clock=B)
    h.resume()
    return h


def full_run(tmp: Path):
    h = hosted(tmp)
    h.run(B, T_RUN_END)
    h.engine.flush()
    return h


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        mp.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
        yield full_run(tmp_path_factory.mktemp("j3run"))


def entries(h, bot):
    return [r for r in decision_rows(h, bot) if r["kind"] == "entry"]


# -------------------------------------------------------------------------------------------------------------------------- identity and purity
def test_twelve_cells_with_their_bot_ids_and_the_fixed_stake():
    ids = [J.bot_id(k, h) for k, h in J.cells()]
    assert ids == [f"j3-k{k}-h{h}" for k in (1, 5, 20) for h in (30, 120, 300, 900)] and len(set(ids)) == 12
    rules = make_rules()
    assert [r.bot_id for r in rules] == ids and {(r.deciding_stake, r.model_ids, r.universe, r.uses_ticks) for r in rules} == {(0.25, (), "pumpswap_wsol", True)}
    assert (J.V_LO, J.V_HI, J.R9B_MS, J.LEG_MS) == (17_500_000_000, 17_700_000_000, 360_000, 1_900)
    assert make_rule("j3-k5-h120").hold_ms == 120_000 + 1_900
    for bad in ("j3-k2-h30", "j3-k1-h31", "h2ps-a1", "j3-k1", ""):
        with pytest.raises(ValueError):
            make_rule(bad)
    for k, h in ((2, 30), (1, 60), (True, 30), (1, "30")):
        with pytest.raises(ValueError):
            J3Rule(k, h)  # type: ignore[arg-type]


def test_events_keep_constant_matches_the_state(tmp_path):
    """The rule's bound on s0 and its 'complete history' test rest on ArenaState keeping a pool's trade events for 60 min and dropping strictly older ones."""
    w = World()
    w.trade(B, POOL["PA"], MINT["PA"], "buy")
    w.trade(B + J.EVENTS_KEEP_MS, POOL["PA"], MINT["PA"], "buy")  # exactly 60 min later: the first event is kept
    w.trade(B + J.EVENTS_KEEP_MS + 1, POOL["PA"], MINT["PA"], "buy")  # one ms more: the print at B is now more than 60 min old and gone
    sim = RuleSim(tmp_path)
    seen = []
    for r in w.ordered:
        sim.apply(r)
        p = sim.state.pool(POOL["PA"])
        seen.append((p.n_trades, len(p.events)))
    assert seen == [(1, 1), (2, 2), (3, 2)]


def test_module_is_pure_by_construction():
    tree = ast.parse((REPO / "tools" / "arena_rule_j3.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"__future__", "typing", "tools.arena_rules", "tools.arena_state"}  # no time, os, random, json, socket, no open() of a feed
    banned = {"open", "print", "input", "exec", "eval", "__import__", "setattr", "globals"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert node.value.id not in {"time", "os", "random", "datetime"}


def test_the_rule_keeps_no_memory(tmp_path):
    rule = make_rule("j3-k1-h30")
    before = dict(vars(rule))
    sim = RuleSim(tmp_path)
    for r in ROWS[:600]:
        sim.apply(r)
        rule.evaluate(sim.ctx(), r)
        rule.on_tick(sim.ctx(), r["t_recv_ms"])
    assert dict(vars(rule)) == before


# -------------------------------------------------------------------------------------------------------------------------- the trigger: k-th BUY of the child
def mini_rows(parent_buys_from=B + 10 * SEC):
    w = World()
    w.pool_create(B + 5 * SEC, POOL["PA"], MINT["PA"])
    w.flow(POOL["PA"], MINT["PA"], parent_buys_from, B + 900 * SEC, every=10 * SEC)
    w.pool_create(B + 100 * SEC, POOL["CA1"], MINT["CA1"], MINT["PA"])
    buys = w.child_buys(POOL["CA1"], MINT["CA1"], MINT["PA"], at(500), 25)
    return w.rows, buys


@pytest.mark.parametrize("k", [1, 5, 20])
def test_trigger_is_the_kth_buy_only_and_fires_once(tmp_path, k):
    rows, buys = mini_rows()
    rule = make_rule(f"j3-k{k}-h30")
    sim = RuleSim(tmp_path)
    fired = [(r["t_recv_ms"], v) for r in rows for v in [sim.feed(rule, r)] if v.trigger]
    assert [t for t, _ in fired] == [buys[k - 1]]  # sells never count: the k-th buy, not the k-th print
    (t, v), = fired
    assert v.child == POOL["CA1"] and v.reason is None and len(v.orders) == 1
    o = v.orders[0]
    assert isinstance(o, Buy) and o.pool == POOL["PA"] and o.attempt_key == f"{MINT['PA']}|{POOL['CA1']}"
    assert o.meta["k"] == k and o.meta["child_pool"] == POOL["CA1"] and o.meta["trigger_t_recv_ms"] == t and o.meta["parent_v"] == V
    assert sum(1 for r in rows if r["pool"] == POOL["CA1"] and r.get("side") == "buy" and r["t_recv_ms"] <= t) == k


def test_sells_and_non_children_never_trigger(tmp_path):
    rule = make_rule("j3-k1-h30")
    sim = RuleSim(tmp_path)
    w = World()
    w.pool_create(B, POOL["PA"], MINT["PA"])
    w.flow(POOL["PA"], MINT["PA"], B + SEC, B + 800 * SEC, every=10 * SEC)  # buys on a WSOL pool, which is not a child
    w.pool_create(B + 50 * SEC, POOL["CA1"], MINT["CA1"], MINT["PA"])
    w.trade(at(500), POOL["CA1"], MINT["CA1"], "sell", quote=MINT["PA"])  # a sell first: it does not count
    w.pool_create(B + 60 * SEC, POOL["CC"], MINT["CC"], None)  # quote null: not a child
    w.trade(at(501), POOL["CC"], MINT["CC"], "buy", quote=None)
    verdicts = [(r, sim.feed(rule, r)) for r in w.rows]
    assert not any(v.trigger or v.orders for _, v in verdicts)
    first_buy = World()
    first_buy.trade(at(502), POOL["CA1"], MINT["CA1"], "buy", quote=MINT["PA"])
    (r,) = first_buy.rows
    assert sim.feed(rule, r).trigger  # the first BUY is the trigger even though the child's first PRINT was a sell


def test_child_without_a_create_on_the_tape_is_not_a_trigger(tmp_path):
    w = World()
    w.pool_create(B, POOL["PA"], MINT["PA"])
    w.flow(POOL["PA"], MINT["PA"], B + SEC, B + 800 * SEC, every=10 * SEC)
    w.child_buys(POOL["CF"], MINT["CF"], MINT["PA"], at(500), 3)  # no pool_create row for CF: its earlier buys, if any, are unknown
    rule, sim = make_rule("j3-k1-h30"), RuleSim(tmp_path)
    vs = [sim.feed(rule, r) for r in w.rows]
    assert {v.reason for v in vs if v.reason} == {J.R_CHILD_NO_CREATE} and not any(v.trigger or v.orders for v in vs)


def test_child_history_that_aged_out_of_the_state_is_not_counted(tmp_path):
    """60 min of silence prunes the child's first event: n_trades != len(events), the buy count is unknown, and the rule does not guess."""
    w = World()
    w.pool_create(B, POOL["PA"], MINT["PA"])
    w.pool_create(B + SEC, POOL["CA1"], MINT["CA1"], MINT["PA"])
    w.trade(B + 10 * SEC, POOL["CA1"], MINT["CA1"], "buy", quote=MINT["PA"])
    w.flow(POOL["PA"], MINT["PA"], B + 20 * SEC, B + 3 * J.EVENTS_KEEP_MS, every=10 * SEC)
    late = B + J.EVENTS_KEEP_MS + 11 * SEC
    w.trade(late, POOL["CA1"], MINT["CA1"], "buy", quote=MINT["PA"])  # really the 2nd buy
    rule = make_rule("j3-k1-h30")
    sim = RuleSim(tmp_path)
    verdicts = {r["t_recv_ms"]: sim.feed(rule, r) for r in w.ordered if r["pool"] in (POOL["CA1"], POOL["PA"]) and r["t_recv_ms"] <= late}
    assert verdicts[B + 10 * SEC].trigger  # on time, the 1st buy is a trigger
    v = verdicts[late]
    assert (v.trigger, v.reason, v.orders) == (False, J.R_CHILD_PRUNED, ())
    late_row = next(r for r in w.rows if r["t_recv_ms"] == late)
    p = sim.state.pool(POOL["CA1"])
    assert (p.n_trades, len(p.events)) == (2, 1)
    assert not make_rule("j3-k5-h30").evaluate(sim.ctx(), late_row).trigger


# -------------------------------------------------------------------------------------------------------------------------- the canonical-pool filter
OFF_MS = 137


def mini(tmp, *, parent_v=V, parent_quote=WSOL, s0_off=-1_000_000, last_off=-10, create=True, rule=None, ctx_kw=None, tape_start=None, second_pool=None):
    """One parent (prints at s0 and last), one child whose first buy is the decision at T. Offsets are relative to T. Returns (verdict, sim, T)."""
    T = B + 1_200 * SEC + OFF_MS
    w = World()
    s0, last = T + s0_off, T + last_off
    if create:
        w.pool_create(s0 - 5 * SEC, POOL["PA"], MINT["PA"], parent_quote)
    w.trade(s0, POOL["PA"], MINT["PA"], "buy", quote=parent_quote, v=parent_v)
    if last != s0:
        w.trade(last, POOL["PA"], MINT["PA"], "sell", quote=parent_quote, v=parent_v)
    if second_pool:
        w.pool_create(s0 - 4 * SEC, POOL["PB"], MINT["PA"], second_pool["quote"])
        w.trade(s0 + 1, POOL["PB"], MINT["PA"], "buy", quote=second_pool["quote"], v=second_pool["v"])
    w.pool_create(T - 10 * SEC, POOL["CA1"], MINT["CA1"], MINT["PA"])
    w.trade(T, POOL["CA1"], MINT["CA1"], "buy", quote=MINT["PA"])
    rule = rule or make_rule("j3-k1-h30", tape_start)
    sim = RuleSim(tmp)
    out = None
    for r in w.ordered:  # same-ms rows keep their order: a parent print at T is applied before the child's buy at T
        out = sim.feed(rule, r, **(ctx_kw or {}))
    return out, sim, T


@pytest.mark.parametrize("v,ok", [(17_500_000_000, True), (17_700_000_000, True), (V, True), (17_499_999_999, False), (17_499_900_000, False),
                                  (17_700_000_001, False), (0, False), (25_000_000_000, False)])
def test_canonical_band_edges_are_inclusive(tmp_path, v, ok):
    verdict, _, _ = mini(tmp_path, parent_v=v)
    assert bool(verdict.orders) is ok
    assert verdict.reason == (None if ok else J.R_PARENT_V)
    if ok:
        assert verdict.orders[0].meta["parent_v"] == v  # the V the check used is on the decision row


def test_a_parent_pool_that_is_not_wsol_is_never_tested_for_v(tmp_path):
    """WSOL is read first: a non-WSOL pool with a perfectly in-band V is not canonical, and the reason says it is the quote, not the V."""
    verdict, _, _ = mini(tmp_path, parent_quote=OTHER, parent_v=V)
    assert (verdict.orders, verdict.reason) == ((), J.R_PARENT_NO_WSOL)
    # a WSOL pool with an out-of-band V next to a non-WSOL pool with an in-band V: still out of band (the in-band one is not WSOL)
    verdict, _, _ = mini(tmp_path / "x", parent_v=V_OUT, second_pool={"quote": OTHER, "v": V})
    assert (verdict.orders, verdict.reason) == ((), J.R_PARENT_V)
    # and a WSOL in-band pool wins over a non-WSOL pool of the same mint, whatever the latter's V
    verdict, _, _ = mini(tmp_path / "y", parent_v=V, second_pool={"quote": OTHER, "v": V_OUT})
    assert len(verdict.orders) == 1 and verdict.orders[0].pool == POOL["PA"]


def test_canonical_pool_picks_the_latest_pool_and_refuses_unknown_quotes():
    def p(id_, quote, v, last_ms, prog="pumpswap"):
        return SimpleNamespace(id=id_, prog=prog, quote_mint=quote, v=v, last_ms=last_ms)

    assert J.canonical_pool([]) == (None, J.R_PARENT_NO_POOL)
    assert J.canonical_pool([p("c", WSOL, V, 1, prog="pumpfun")]) == (None, J.R_PARENT_NO_POOL)  # a bonding curve is never a parent
    assert J.canonical_pool([p("a", None, V, 1)]) == (None, J.R_PARENT_NO_WSOL)  # quote not verified
    assert J.canonical_pool([p("a", OTHER, V, 1), p("b", WSOL, V_OUT, 2)])[1] == J.R_PARENT_V
    assert J.canonical_pool([p("a", WSOL, V, 5), p("b", WSOL, V, 9)])[0].id == "b"  # the later print
    assert J.canonical_pool([p("b", WSOL, V, 9), p("a", WSOL, V, 9)])[0].id == "a"  # tie: the smaller address
    assert J.canonical_pool([p("a", WSOL, True, 9)])[1] == J.R_PARENT_V  # a bool is not a V


# -------------------------------------------------------------------------------------------------------------------------- R9(b) at the 360 s edges
@pytest.mark.parametrize("s0_off,last_off,reason", [
    (-360_000, 0, None),  # s0 exactly 360 s before the decision and a last print exactly at s0 + 360 s (the decision instant): both inclusive
    (-359_999, 0, f"{J.R_PARENT_YOUNG}_first_print"),
    (-360_001, 0, None),
    (-400_000, -40_000, None),  # last print exactly at s0 + 360 s
    (-400_000, -40_001, f"{J.R_PARENT_SILENT}_first_print"),  # one ms short
    (-3_000_000, -10, None),
    (-1_000_000, -1_000_000, f"{J.R_PARENT_SILENT}_first_print"),  # a single print, long ago
])
def test_r9b_edges_when_the_parent_create_is_on_the_tape(tmp_path, s0_off, last_off, reason):
    verdict, _, _ = mini(tmp_path, s0_off=s0_off, last_off=last_off)
    assert verdict.reason == reason and bool(verdict.orders) is (reason is None) and verdict.trigger
    if reason is None:
        assert verdict.orders[0].meta["s0_basis"] == "first_print"


TS = B  # the tape start the rule is given (the explorer's W_e start, 2026-10-19T08:05:20Z)


def fake_parent(first, last=None, created_kind=None):
    """A parent pool as the state shows it: events (t, sol, is_buy, trader), n_trades, last_trade, created_kind. No tape needed."""
    ev = collections.deque([(first, 1, True, "t")] + ([(last, 1, False, "t")] if last not in (None, first) else []))
    return SimpleNamespace(events=ev, n_trades=len(ev), last_trade={"t_ms": last if last is not None else first}, created_kind=created_kind)


@pytest.mark.parametrize("created_kind", [None, "pool_create"])
@pytest.mark.parametrize("first_off,t_off,last_off,reason", [
    (40_000, 40_000 + 357_000, 40_000 + 357_000, f"{J.R_PARENT_YOUNG}_first_print"),  # QP: first print 08:06:00, tape start 08:05:20: 357 s after the print is too early
    (40_000, 40_000 + 359_999, 40_000 + 359_999, f"{J.R_PARENT_YOUNG}_first_print"),
    (40_000, 40_000 + 360_000, 40_000 + 360_000, None),  # exactly 360 s after the first print, last print at the decision instant: both inclusive
    (5_000, 5_000 + 65_000, 5_000 + 65_000, f"{J.R_PARENT_YOUNG}_first_print"),  # QP (a): first print 08:05:25 must not pass at 08:06:30
    (5_000, 5_000 + 357_000, 5_000 + 357_000, f"{J.R_PARENT_YOUNG}_first_print"),  # QP (b): first print 08:05:25, decision 08:11:22
    (5_000, 5_000 + 360_000, 5_000 + 360_000, None),
    (40_000, 40_000 + 400_000, 40_000 + 360_000, None),  # last print exactly at s0 + 360 s
    (40_000, 40_000 + 400_000, 40_000 + 359_999, f"{J.R_PARENT_SILENT}_first_print"),
    (-100_000, 359_999, 359_999, f"{J.R_PARENT_YOUNG}_first_print"),  # a first print before the tape start: the start is a floor on s0 ...
    (-100_000, 360_000, 360_000, None),  # ... so it ages from the tape start, not from the earlier print
])
def test_r9b_s0_is_the_first_print_seen_whatever_the_create_and_the_tape_start_is_only_a_floor(first_off, t_off, last_off, reason, created_kind):
    ok, why, s0, basis = J.r9b(fake_parent(TS + first_off, TS + last_off, created_kind), TS + t_off, TS)
    assert (ok, why) == (reason is None, reason) and basis == J.BASIS_FIRST_PRINT
    assert s0 == max(TS + first_off, TS)  # never the tape start in place of a later first print


def test_r9b_aged_out_parent_keeps_the_bound_and_the_floor():
    p = fake_parent(TS + 3_700_000)
    p.n_trades = 2  # one event aged out of the deque
    t = TS + 3_700_000 + 10_000
    ok, why, s0, basis = J.r9b(p, t, TS)
    assert (ok, why, basis) == (True, None, J.BASIS_BOUND) and s0 == t - J.EVENTS_KEEP_MS  # earlier than the earliest retained print and than now - 60 min
    assert J.s0_bound(p, TS + 100_000, TS) == (TS, J.BASIS_BOUND)  # and never below the floor


@pytest.mark.parametrize("t_off,reason", [(357_000, f"{J.R_PARENT_YOUNG}_first_print"), (359_999, f"{J.R_PARENT_YOUNG}_first_print"), (360_000, None)])
@pytest.mark.parametrize("with_create", [False, True])
def test_a_parent_first_printed_after_the_tape_start_is_aged_from_its_first_print_in_the_real_state(tmp_path, t_off, reason, with_create):
    """No create on the tape (or a create long before the first print): the parent first prints 5 s after the tape start and may not pass 357 s after that print."""
    first = TS + 5 * SEC
    T = first + t_off
    w = World()
    if with_create:
        w.pool_create(TS - 5 * SEC, POOL["PA"], MINT["PA"])
    w.trade(first, POOL["PA"], MINT["PA"], "buy")
    w.trade(T, POOL["PA"], MINT["PA"], "sell")  # the parent's last print is the decision instant
    w.pool_create(T - 5 * SEC, POOL["CA1"], MINT["CA1"], MINT["PA"])
    w.trade(T, POOL["CA1"], MINT["CA1"], "buy", quote=MINT["PA"])
    rule, sim = make_rule("j3-k1-h30", tape_start_ms=TS), RuleSim(tmp_path)
    for r in w.ordered:
        v = sim.feed(rule, r)
    assert v.reason == reason and bool(v.orders) is (reason is None)
    if reason is None:
        assert v.orders[0].meta["s0_basis"] == "first_print" and v.orders[0].meta["parent_s0_ms"] == first


def test_the_tape_start_defaults_to_the_host_clock_and_a_parent_with_no_print_is_refused(tmp_path):
    w = World()
    w.pool_create(B, POOL["PA"], MINT["PA"])
    w.pool_create(B + 400 * SEC, POOL["CA1"], MINT["CA1"], MINT["PA"])
    w.trade(B + 410 * SEC, POOL["CA1"], MINT["CA1"], "buy", quote=MINT["PA"])
    rule, sim = make_rule("j3-k1-h30"), RuleSim(tmp_path, clock_start_ms=B)
    v = [sim.feed(rule, r) for r in w.ordered][-1]
    assert v.orders == () and v.reason == J.R_PARENT_V  # the pool exists and nobody traded it, so it has no V: not canonical
    assert J.r9b(SimpleNamespace(last_trade=None, events=()), B + 400 * SEC, B)[:2] == (False, J.R_PARENT_NO_PRINTS)  # and R9(b) has nothing to anchor on
    assert make_rule("j3-k1-h30", tape_start_ms=B + 100 * SEC).tape_start_ms == B + 100 * SEC and make_rule("j3-k1-h30").tape_start_ms is None


def test_parent_whose_events_aged_out_uses_a_sufficient_bound(tmp_path):
    """The parent traded 61 min ago and again 10 s ago: its first print is no longer in the state. s0 is bounded above by (now - 60 min), which is enough for
    R9(b); the entry row says the s0 is a bound."""
    T = B + 7_300 * SEC + OFF_MS
    w = World()
    w.pool_create(B, POOL["PA"], MINT["PA"])
    w.trade(B + SEC, POOL["PA"], MINT["PA"], "buy")
    w.trade(T - 10 * SEC, POOL["PA"], MINT["PA"], "sell")  # prunes the first print: more than 60 min older
    w.pool_create(T - 5 * SEC, POOL["CA1"], MINT["CA1"], MINT["PA"])
    w.trade(T, POOL["CA1"], MINT["CA1"], "buy", quote=MINT["PA"])
    rule, sim = make_rule("j3-k1-h30"), RuleSim(tmp_path)
    for r in w.ordered:
        v = sim.feed(rule, r)
    p = sim.state.pool(POOL["PA"])
    assert p.n_trades == 2 and len(p.events) == 1
    assert len(v.orders) == 1 and v.orders[0].meta["s0_basis"] == J.BASIS_BOUND and v.orders[0].meta["parent_s0_ms"] == T - J.EVENTS_KEEP_MS


# -------------------------------------------------------------------------------------------------------------------------- one position per parent, one attempt per (parent, child)
def test_one_open_position_per_parent_and_one_attempt_per_parent_child(tmp_path):
    rule = make_rule("j3-k1-h30")
    rows, _ = mini_rows()
    trig = next(r for r in rows if r["pool"] == POOL["CA1"] and r.get("side") == "buy")
    sim = RuleSim(tmp_path)
    for r in rows:
        sim.apply(r)
        if r is trig:
            break
    pos = lambda parent, exiting=False: PositionView("p1", POOL["PA"], {"parent_mint": parent}, B, 1, exiting)  # noqa: E731
    base = rule.evaluate(sim.ctx(), trig)
    assert len(base.orders) == 1
    assert rule.evaluate(sim.ctx(positions=[pos(MINT["PA"])]), trig).reason == J.R_PARENT_OPEN  # the parent's position is open
    assert rule.evaluate(sim.ctx(positions=[pos(MINT["PA"], exiting=True)]), trig).reason == J.R_PARENT_OPEN  # and still open while its sell is being sent
    assert len(rule.evaluate(sim.ctx(positions=[pos(MINT["PB"])]), trig).orders) == 1  # another parent's position does not block
    key = f"{MINT['PA']}|{POOL['CA1']}"
    assert base.orders[0].attempt_key == key
    assert rule.evaluate(sim.ctx(attempted=[key]), trig).reason == J.R_ATTEMPTED  # the log says this (parent, child) was attempted
    assert len(rule.evaluate(sim.ctx(attempted=[f"{MINT['PA']}|{POOL['CA2']}", f"{MINT['PB']}|{POOL['CA1']}"]), trig).orders) == 1  # other pairs do not


def test_hosted_one_position_per_parent_and_a_new_attempt_after_the_exit(run):
    for h_s in (30, 120, 300, 900):
        e = entries(run, f"j3-k1-h{h_s}")
        # CA1 and CE at +400 s; CA2's first buy (+420 s, same parent PA, position open) makes no attempt; CA3 at +2,000 s, after every hold has ended
        assert sorted((x["meta"]["child_pool"], x["ms"]) for x in e) == sorted([(POOL["CA1"], at(400)), (POOL["CE"], at(400)), (POOL["CA3"], at(2000))])
        assert POOL["CA2"] not in {x["meta"]["child_pool"] for x in e}
        assert all(x["attempt_key"] == f"{x['meta']['parent_mint']}|{x['meta']['child_pool']}" for x in e)
        assert len({x["attempt_key"] for x in e}) == len(e)


# -------------------------------------------------------------------------------------------------------------------------- hosted: the 12 cells on the synthetic tape
EXPECT = {  # k -> [(child, parent pool, entry instant)]
    1: [("CA1", "PA", at(400)), ("CE", "PE", at(400)), ("CA3", "PA", at(2000))],
    5: [("CA1", "PA", at(440)), ("CE", "PE", at(440)), ("CA3", "PA", at(2040))],
    20: [("CA1", "PA", at(590)), ("CE", "PE", at(590)), ("CD", "PD", at(990)), ("CA3", "PA", at(2190))],
}


@pytest.mark.parametrize("k,h_s", [(k, h) for k in (1, 5, 20) for h in (30, 120, 300, 900)])
def test_hosted_cell_entries_and_exits(run, k, h_s):
    bot = f"j3-k{k}-h{h_s}"
    e = entries(run, bot)
    assert sorted((x["meta"]["child_pool"], x["pool"], x["ms"]) for x in e) == sorted((POOL[c], POOL[p], t) for c, p, t in EXPECT[k])  # same-ms entries: no order promised
    assert all(x["stake_sol"] == 0.25 and x["meta"]["k"] == k and x["meta"]["hold_s"] == h_s and x["meta"]["parent_v"] == V for x in e)
    ex = {r["order_id"]: r for r in decision_rows(run, bot) if r["kind"] == "exit"}
    assert len(ex) == len(e) and all(r["attempt"] == 1 and r["reason"] == f"hold_{h_s}s" for r in ex.values())
    for x in e:  # sold at the first 1 s tick at or after (decision + 1.9 s + H)
        assert ex[x["order_id"]]["ms"] == tick_at_or_after(x["ms"] + 1_900 + h_s * 1000)
        assert ex[x["order_id"]]["ms"] - (x["ms"] + 1_900 + h_s * 1000) < 1000
    led = ledger_rows(run, bot)
    assert collections.Counter(r["kind"] for r in led)["open"] == len(e) and not run.books[bot].open


def test_hosted_every_s0_basis_and_skip_is_as_scripted(run):
    e1 = {x["meta"]["child_pool"]: x["meta"] for x in entries(run, "j3-k1-h30")}
    assert e1[POOL["CA1"]]["s0_basis"] == "first_print" and e1[POOL["CE"]]["s0_basis"] == "first_print" and e1[POOL["CE"]]["parent_s0_ms"] == B + SEC
    assert e1[POOL["CA1"]]["parent_pool"] == POOL["PA"] and e1[POOL["CA1"]]["parent_mint"] == MINT["PA"] and e1[POOL["CA1"]]["parent_s0_ms"] == B + 10 * SEC


def test_no_child_is_ever_priced_or_ordered(run):
    """The children are trigger events only: no decision row, no ledger row (open, trade, excluded, refused) and no order is ever on a child pool, and every order
    is on a pool whose state says PumpSwap + WSOL."""
    seen = set()
    for bot in (r.bot_id for r in make_rules()):
        for r in run.log.rows(bot):
            if "pool" in r:
                seen.add(r["pool"])
        for r in run.engine.ledger.read_bot(bot):
            if "pool" in r:
                seen.add(r["pool"])
    assert seen and seen <= PARENT_POOLS and not seen & CHILD_POOLS and not seen & {POOL["PB"], POOL["PC"], POOL["PS"], POOL["PG"]}
    for pool in seen:
        p = run.state.pool(pool)
        assert p.prog == "pumpswap" and p.quote_mint == WSOL
    assert run.counters.get("blocked_universe", 0) == 0 and run.counters.get("blocked_duplicate_attempt", 0) == 0 and not run.hook_errors


def test_scan_of_the_whole_tape_never_orders_on_a_non_wsol_pool(tmp_path):
    """The same assertion without the host: every Buy any of the 12 rules returns for any row of the scenario targets a pool the state shows as WSOL-quoted."""
    sim = RuleSim(tmp_path)
    rules = make_rules()
    pools = collections.Counter()
    for r in ROWS:
        sim.apply(r)
        c = sim.ctx()
        for rule in rules:
            for o in rule.on_row(c, r):
                assert isinstance(o, Buy) and sim.state.pool(o.pool).quote_mint == WSOL and sim.state.pool(o.pool).prog == "pumpswap"
                pools[o.pool] += 1
    assert set(pools) == PARENT_POOLS


def test_hosted_decisions_match_with_and_without_the_recording_subclass(tmp_path, run):
    """What the explorer hosts (a recording subclass) decides exactly what the plain rule decides."""
    class Recording(J3Rule):
        seen = 0

        def on_row(self, ctx, row):
            v = self.evaluate(ctx, row)
            Recording.seen += bool(v.trigger)
            return list(v.orders)

    h = hosted(tmp_path, make_rules(rule_cls=Recording))
    h.run(B, T_RUN_END)
    h.engine.flush()
    for bot in (r.bot_id for r in make_rules()):
        assert decision_files(tmp_path / "rules", bot) == decision_files(Path(run.cfg.root), bot)
    assert Recording.seen > 0


# -------------------------------------------------------------------------------------------------------------------------- hold exit as a pure function
@pytest.mark.parametrize("h_s", [30, 120, 300, 900])
def test_on_tick_sells_exactly_at_decision_plus_leg_plus_hold(h_s):
    rule = make_rule(f"j3-k1-h{h_s}")
    pos = PositionView("p1", POOL["PA"], {}, B + 5_000, 1, False)
    due = B + 5_000 + 1_900 + h_s * 1000
    c = SimpleNamespace(positions=(pos,), state=None, attempted=frozenset(), now_ms=0, clock_start_ms=B)
    assert rule.on_tick(c, due - 1) == [] and rule.on_tick(c, due) == [Sell("p1", f"hold_{h_s}s")] and rule.on_tick(c, due + 60_000) == [Sell("p1", f"hold_{h_s}s")]
    done = SimpleNamespace(positions=(PositionView("p1", POOL["PA"], {}, B + 5_000, 1, True),), state=None, attempted=frozenset(), now_ms=0, clock_start_ms=B)
    assert rule.on_tick(done, due + 10_000) == []  # a sell is already being sent


# -------------------------------------------------------------------------------------------------------------------------- determinism and restart
def test_two_replays_give_identical_decision_logs_and_ledger_rows(tmp_path, run):
    other = full_run(tmp_path)
    for bot in (r.bot_id for r in make_rules()):
        assert decision_files(tmp_path / "rules", bot) == decision_files(Path(run.cfg.root), bot)
        assert ledger_rows(other, bot) == ledger_rows(run, bot)
        other.log.verify(bot)
    assert sum(len(entries(other, r.bot_id)) for r in make_rules()) == 3 * 4 + 3 * 4 + 4 * 4


@pytest.mark.parametrize("kill_s", [450, 1_000])
def test_kill_and_resume_rebuilds_the_rule_memory_from_the_log(tmp_path, run, kill_s):
    """The rule keeps no memory: after a restart the attempts and the open positions come from the decision log, so nothing is entered twice and the open
    positions are sold at the same instants."""
    split = tmp_path / "split"
    first = hosted(split)
    first.run(B, B + kill_s * SEC)
    del first  # killed
    second = make_host(split, make_rules(), rows=ROWS, clock=B)
    n = second.resume()
    assert n["open"] > 0 and n["dropped"] == 0
    second.run(B + kill_s * SEC, T_RUN_END)
    for bot in (r.bot_id for r in make_rules()):
        assert decision_files(split / "rules", bot) == decision_files(Path(run.cfg.root), bot)  # byte for byte, no marker row in a sim resume
        assert len(entries(second, bot)) == len(entries(run, bot))
    excl = [r for bot in ("j3-k1-h900",) for r in ledger_rows(second, bot) if r["kind"] == "excluded"]
    assert excl and {r["exclusion_reason"] for r in excl} == {"engine_restart"}  # a position open at the kill is booked by the engine as excluded, as in hv4


def test_stack_the_rule_runs_under_is_the_hosts_pinned_stack(run):
    assert run.engine.cfg == stack_config(7) and run.engine.cfg.rent == 0 and run.engine.cfg.gap_slots == 150 and run.engine.cfg.gap_silence_ms == 60_000
    assert isinstance(run.state, ArenaState)
