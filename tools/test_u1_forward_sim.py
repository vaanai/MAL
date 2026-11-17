"""u1_forward_sim: reputation without look-ahead, hub exclusion, cluster definition, entry/exit constants, zero entries before S, random-pick plan, runner manifest glue.
Synthetic fixtures only; no real tape, funder, outcome, fill or P&L is opened. Nothing here says any book makes money."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import c1nf_forward_sim as S  # noqa: E402
import u1_forward_sim as U  # noqa: E402

START = "2026-11-20T00"
S_MS = S.parse_hour(START)
S_S = S_MS // 1000
V = 17_584_505_288
Q0, B0 = 60 * 10 ** 9, 10 ** 15
DAY = 86_400


def row(typ, t_s, **kw):
    r = {"v": 1, "type": typ, "t_recv_ms": t_s * 1000, "block_time": t_s, "slot": t_s * 2}
    r.update(kw)
    return r


def create(mint, launcher, t_s, creator=None, trader="__same__"):
    tr = launcher if trader == "__same__" else trader
    return row("create", t_s, prog="pumpfun", mint=mint, trader=tr, creator=creator or launcher)


def pool_create(mint, t_s, pool=None, quote=S.WSOL):
    return row("pool_create", t_s, prog="pumpswap", mint=mint, pool=pool or "P" + mint, quote_mint=quote)


def print_(pool, t_s, base=B0):
    return row("trade", t_s, prog="pumpswap", pool=pool, mint="M" + pool, side="buy", sol_lamports=1, token_raw=1, quote_reserve=Q0, base_reserve=base, virtual_quote_reserves=V)


def graduate(mint, launcher, pc_s, alive=True, **kw):
    """create, pool_create, first print at pc+1, last print at pc+86,000 (alive: same price; dead: base x3 = spot / 3)"""
    pool = "P" + mint
    return [create(mint, launcher, pc_s - 600, **kw), pool_create(mint, pc_s), print_(pool, pc_s + 1), print_(pool, pc_s + 86_000, B0 if alive else 3 * B0)]


def stream(*groups):
    return sorted([r for g in groups for r in g], key=lambda r: r["t_recv_ms"])


def new_decider(table=None, **kw):
    return U.U1Decider(S_MS, table or U.FunderTable(), **kw)


def run(dec, rows, warmup=False):
    st = S.RunState(hour="h", warmup=warmup, day="d")
    return dec.decide(rows, st), st


def history_rows(launcher, n_alive, n_dead=0, base_mint="h", t0=S_S - 5 * DAY):
    rows = []
    for i in range(n_alive + n_dead):
        rows += graduate("%s%s%d" % (base_mint, launcher, i), launcher, t0 + i * 1000, alive=i < n_alive)
    return rows


def candidate(launcher, mint="NEW", pc_s=S_S + 3600):
    """the decided graduate plus the crossing row after D = pc + 360 s"""
    return graduate(mint, launcher, pc_s)[:2] + [print_("P" + mint, pc_s + 100), print_("P" + mint, pc_s + 359), print_("P" + mint, pc_s + 361)]


def flow(dec, hist, cand):
    run(dec, sorted(hist, key=lambda r: r["t_recv_ms"]), warmup=True)
    # one forward batch holding the candidate; rows before S inside the history have warm-up status
    return run(dec, stream(cand))


# ---------------------------------------------------------------------------------------------------------------------------- spot, scoring, no look-ahead
def test_spot_and_hr_pda_are_the_u4_u2_functions_not_copies():
    import u4_u2_deciders as UD
    assert U.post_spot is UD.post_spot and U.hr_pda_default is UD.hr_pda_default
    assert not hasattr(U, "spot_after") and not hasattr(U, "hr_pda")
    r = print_("P", 1)
    r.update(side="buy", sol_lamports=10 ** 9, token_raw=10 ** 12)
    assert U.post_spot(r) == (Q0 + 0.99 * 10 ** 9 + V) / (B0 - 10 ** 12)  # the engine's mint_states convention
    assert U.post_spot(dict(r, virtual_quote_reserves=None)) is None


def test_alive_is_p24_at_least_half_of_p0():
    reg = U.Registry()
    for r in graduate("A", "L", S_S - 5 * DAY, alive=True) + graduate("B", "L", S_S - 5 * DAY, alive=False):
        reg.feed(r)
    assert reg.scored(reg.grads["A"], S_S, 0) is True and reg.scored(reg.grads["B"], S_S, 0) is False


def test_no_look_ahead_graduate_whose_24h_falls_after_D_is_not_scored():
    reg = U.Registry()
    pc = S_S - DAY + 1000  # pc + 86,400 s = S + 1,000 s
    for r in graduate("A", "L", pc, alive=True):
        reg.feed(r)
    assert reg.scored(reg.grads["A"], S_S + 999, 0) is None   # D is before the 24 h mark: out
    assert reg.scored(reg.grads["A"], S_S + 1000, 0) is True  # exactly at the mark: in
    # a print after pc + 86,400 s never becomes P24
    reg.feed(print_("PA", pc + DAY + 5, 100 * B0))
    assert reg.grads["A"].p24[0] == pc + 86_000


def test_decider_does_not_use_priors_whose_24h_is_after_D():
    dec = new_decider()
    pc_late = S_S + 3600 - DAY + 100  # +24 h lands at D - 260 s... then one that lands after D:
    hist = history_rows("A", 2) + graduate("late", "A", S_S + 3600 - DAY + 400, alive=True)  # 24 h mark at D + 40 s: not scored at D
    entries, st = flow(dec, hist, candidate("A"))
    assert entries == [] and st.counts["gate_fail"] == 1  # only 2 scored priors
    # the same history plus a third prior whose 24 h mark is before D passes
    dec2 = new_decider()
    entries, _ = flow(dec2, history_rows("A", 2) + graduate("ok", "A", S_S + 3600 - DAY - 400, alive=True), candidate("A"))
    assert len(entries) == 1
    del pc_late


def test_window_start_and_gap_make_priors_unscored():
    reg = U.Registry()
    for r in graduate("A", "L", S_S - 5 * DAY):
        reg.feed(r)
    g = reg.grads["A"]
    assert reg.scored(g, S_S, S_S - 4 * DAY) is None  # before W start
    gap = lambda t: {"type": "gap", "prog": "pumpswap", "kind": "tip_gap_x", "data_loss": True, "t_recv_ms": t * 1000, "block_time": t, "slot": 1}
    for t, expect in ((g.pc_s + 50_000, True), (g.pc_s + 86_200, None), (g.pc_s + 1, None)):  # between P0 and P24: harmless; after P24's print or before P0's: unscored
        rg = U.Registry()
        for r in graduate("A", "L", S_S - 5 * DAY):
            rg.feed(r)
        rg.feed(gap(t))
        assert rg.scored(rg.grads["A"], S_S, 0) is expect
    reg2 = U.Registry()
    for r in graduate("A", "L", S_S - 5 * DAY):
        reg2.feed(r)
    reg2.feed({"type": "gap", "prog": "pumpfun", "data_loss": True, "block_time": 5, "slot": 1, "t_recv_ms": 5000})  # other program: ignored
    assert reg2.scored(reg2.grads["A"], S_S, 0) is True


def test_launcher_is_trader_else_creator_and_counters():
    reg = U.Registry()
    reg.feed(create("A", "T", 1, creator="C", trader="T"))
    reg.feed(create("B", "C", 2, creator="C", trader=None))
    assert reg.creates["A"]["launcher"] == "T" and reg.creates["B"]["launcher"] == "C"
    assert reg.c["trader_ne_creator"] == 1 and reg.c["launcher_from_creator"] == 1


# ---------------------------------------------------------------------------------------------------------------------------- entry rules
def test_entry_gate_three_priors_and_half_alive_enters_at_the_crossing_row():
    dec = new_decider()
    entries, st = flow(dec, history_rows("A", 2, 2), candidate("A"))  # 4 priors, 2 alive = 0.50
    assert len(entries) == 1
    e = entries[0]
    pc = S_S + 3600
    cand = candidate("A")
    assert e.pool == "PNEW" and e.mint == "NEW" and e.decision_ms == (pc + 361) * 1000 and cand[e.at_row]["t_recv_ms"] == e.decision_ms  # the crossing row
    assert e.decision_slot == (pc + 359) * 2  # SD = slot of the last row at or before D
    assert e.exit_after_s == pytest.approx(21_600 - S.LAT_DECIDING_S)  # the sell is decided at D + 21,600 s: land + exit = 6 h
    assert e.meta["n_hist"] == 4 and e.meta["n_alive"] == 2 and e.meta["hist_kind"] == "own" and st.counts["entries"] == 1
    assert "A" not in json.dumps(e.meta)  # cluster is an opaque hash


def test_gate_fail_below_three_priors_or_below_half():
    for hist in (history_rows("A", 2), history_rows("A", 1, 3)):
        entries, st = flow(new_decider(), hist, candidate("A"))
        assert entries == [] and st.counts["gate_fail"] == 1


def test_low_reputation_line_is_the_complement():
    entries, _ = flow(new_decider(mode="low_rep"), history_rows("A", 1, 3), candidate("A"))
    assert len(entries) == 1 and entries[0].meta["mode"] == "low_rep"
    entries, _ = flow(new_decider(mode="low_rep"), history_rows("A", 3), candidate("A"))
    assert entries == []


def test_one_entry_per_mint_and_cap_open():
    dec = new_decider(open_fn=lambda: 10)
    entries, st = flow(dec, history_rows("A", 3), candidate("A"))
    assert entries == [] and st.counts["cap_open"] == 1 and st.counts["gate_pass"] == 1
    assert dec.gate_log == [{"pool": "PNEW", "mint": "NEW", "pc_s": S_S + 3600, "entered": False}]
    dec = new_decider(open_fn=lambda: 9)
    entries, _ = flow(dec, history_rows("A", 3), candidate("A"))
    assert len(entries) == 1 and "NEW" in dec.entered


def test_no_create_row_and_non_wsol_are_counted_not_entered():
    dec = new_decider()
    run(dec, sorted(history_rows("A", 3), key=lambda r: r["t_recv_ms"]), warmup=True)
    cand = [pool_create("NEW", S_S + 3600), print_("PNEW", S_S + 4000)]  # no create row
    entries, st = run(dec, cand)
    assert entries == [] and st.counts["no_create_row"] == 1
    dec = new_decider()
    run(dec, sorted(history_rows("A", 3), key=lambda r: r["t_recv_ms"]), warmup=True)
    entries, st = run(dec, [create("NEW", "A", S_S + 3000), pool_create("NEW", S_S + 3600, quote="OtherQuote"), print_("PNEW", S_S + 4000)])
    assert entries == [] and st.counts["non_wsol"] == 1


def test_holder_rewards_predicate_excludes():
    entries, st = flow(new_decider(hr_fn=lambda r: True), history_rows("A", 3), candidate("A"))
    assert entries == [] and st.counts["hr_coin_excluded"] == 1


def test_hub_launcher_over_50_graduates_is_excluded():
    hist = []
    for i in range(51):
        hist += [create("g%d" % i, "A", S_S - 6 * DAY + i), pool_create("g%d" % i, S_S - 6 * DAY + i + 1)]
    entries, st = flow(new_decider(), hist, candidate("A"))
    assert entries == [] and st.counts["hub_launcher"] == 1


# ---------------------------------------------------------------------------------------------------------------------------- zero entries before S
def test_no_decision_before_S_and_none_in_warmup():
    dec = new_decider()
    hist = history_rows("A", 3)
    # a qualifying candidate whose D falls before S, fed as a normal (non-warm-up) hour: no pending decision is made
    early = [create("E", "A", S_S - 2000)] + [pool_create("E", S_S - 1000), print_("PE", S_S - 500), print_("PE", S_S + 100)]
    run(dec, sorted(hist, key=lambda r: r["t_recv_ms"]), warmup=True)
    entries, st = run(dec, stream(early))
    assert entries == [] and st.counts["entries"] == 0 and st.counts["graduates_seen"] >= 1
    # the same candidate shape inside a warm-up hour
    dec2 = new_decider()
    entries, st = run(dec2, stream(hist, candidate("A")), warmup=True)
    assert entries == [] and dec2.pending == []


def test_warmup_registry_still_accumulates_history():
    dec = new_decider()
    run(dec, sorted(history_rows("A", 3), key=lambda r: r["t_recv_ms"]), warmup=True)
    assert len(dec.reg.by_launcher["A"]) == 3 and dec.c["entries"] == 0


# ---------------------------------------------------------------------------------------------------------------------------- clusters and hubs
def funded_table(**launcher_slots):
    """launchers funded by 'F': kwargs launcher=slot"""
    t = U.FunderTable()
    for l, s in launcher_slots.items():
        t.add_row({"launcher": l, "funder": "F", "funding_slot": s, "status": "resolved"})
    return t


def test_funder_cluster_lends_history_to_a_new_launcher():
    t = funded_table(A=10, B=11)
    entries, _ = flow(new_decider(t), history_rows("A", 3), candidate("B"))  # B has no history of its own
    assert len(entries) == 1 and entries[0].meta["hist_kind"] == "funder" and entries[0].meta["n_hist"] == 3
    entries, _ = flow(new_decider(U.FunderTable()), history_rows("A", 3), candidate("B"))  # unlinked: own history only
    assert entries == []


def test_funding_after_SD_is_not_in_the_cluster_and_unresolved_rows_never_link():
    t = funded_table(A=10, B=11, C=10 ** 12)  # C funded after D's slot
    cl, kind, hub = new_decider(t).cluster("B", 10 ** 6)
    assert cl == {"A", "B"} and kind == "funder" and hub is None
    t2 = U.FunderTable()
    t2.add_row({"launcher": "A", "funder": None, "funding_slot": None, "status": "no_sol_inflow"})
    assert new_decider(t2).cluster("A", 10) == ({"A"}, "own", None)


def test_no_transitive_closure():
    t = U.FunderTable()
    t.add_row({"launcher": "A", "funder": "F", "funding_slot": 1, "status": "resolved"})
    t.add_row({"launcher": "F", "funder": "G", "funding_slot": 1, "status": "resolved"})  # the funder was itself funded; a launcher that funded another launcher: not followed
    t.add_row({"launcher": "Z", "funder": "G", "funding_slot": 1, "status": "resolved"})
    assert new_decider(t).cluster("A", 100)[0] == {"A"}


@pytest.mark.parametrize("rule", ["H1", "H2", "H3", "H4"])
def test_each_hub_rule_removes_the_cluster(rule):
    funder = "F"
    t = U.FunderTable()
    kw = {}
    if rule == "H1":
        t.set_hub("F", True)
    elif rule == "H3":
        kw["labels"] = {"F"}
    elif rule == "H4":
        funder = sorted(U.H4_ACCOUNTS)[0]
    n = 51 if rule == "H2" else 2
    for i, l in enumerate(["A", "B"] + ["x%d" % i for i in range(n)]):
        t.add_row({"launcher": l, "funder": funder, "funding_slot": 10, "status": "resolved"})
    dec = new_decider(t, **kw)
    assert dec.hub_reason(funder, 100) == rule
    entries, st = flow(dec, history_rows("A", 3), candidate("B"))
    assert entries == [] and st.counts["hub_" + rule] == 1  # B falls back to its own (empty) history
    entries, _ = flow(new_decider(t, **kw), history_rows("A", 3), candidate("A"))  # A keeps its own history
    assert len(entries) == 1 and entries[0].meta["hist_kind"] == "own"


def test_h2_counts_children_funded_by_the_decision_slot_only():
    t = U.FunderTable()
    for i in range(51):
        t.add_row({"launcher": "x%d" % i, "funder": "F", "funding_slot": 10 + i, "status": "resolved"})
    dec = new_decider(t)
    assert dec.hub_reason("F", 10 + 49) is None and dec.hub_reason("F", 10 + 50) == "H2"  # 50 children at slot <= 59: not a hub; 51 at <= 60: hub


def test_h1_error_flag_is_not_a_hub():
    t = U.FunderTable()
    t.set_hub("F", "error")
    assert new_decider(t).hub_reason("F", 1) is None


def test_forward_fetch_hook_traces_an_untraced_launcher():
    calls = []

    def fetch(launcher, slot):
        calls.append((launcher, slot))
        return {"launcher": launcher, "funder": "F", "funding_slot": 5, "status": "resolved"}

    t = funded_table(A=10)
    entries, _ = flow(new_decider(t, fetch_fn=fetch), history_rows("A", 3), candidate("B"))
    assert [c[0] for c in calls] == ["B"] and len(entries) == 1 and entries[0].meta["hist_kind"] == "funder"


# ---------------------------------------------------------------------------------------------------------------------------- stop, placebo
def stop_flow(mode="book", **kw):
    dec = new_decider(mode=mode, **kw) if mode != "placebo" else new_decider(mode="placebo", plan={"PNEW": {"for": "x", "pc_s": 1}})
    hist = history_rows("A", 0 if mode == "placebo" else (1 if mode == "low_rep" else 3), 3 if mode == "low_rep" else 0)
    entries, _ = flow(dec, hist, candidate("A"))
    assert len(entries) == 1
    return dec


@pytest.mark.parametrize("mode", ["book", "low_rep", "placebo"])
def test_stop_fires_on_first_print_after_landing_at_or_below_75_percent_of_the_entry_print(mode):
    dec = stop_flow(mode)
    pc = S_S + 3600
    # landing slot = SD + 19; SD = (pc + 359) * 2; the entry print = last print at or before landing = the crossing row (pc + 361)
    assert dec.pos["PNEW"]["land_slot"] == (pc + 359) * 2 + 19
    run(dec, [print_("PNEW", pc + 365, B0)])   # slot (pc+365)*2 <= landing: this print is the entry print (the reference), never a trigger
    assert dec.take_sells() == []
    ok, _ = run(dec, [print_("PNEW", pc + 400, int(1.2 * B0))])   # spot / 1.2 = 0.83: no stop
    assert dec.take_sells() == []
    run(dec, [print_("PNEW", pc + 500, int(1.5 * B0)), print_("PNEW", pc + 600, 5 * B0)])   # spot / 1.5 = 0.667 <= 0.75: stop on THIS print; later ones add nothing
    sells = dec.take_sells()
    assert len(sells) == 1 and sells[0].reason == "stop" and sells[0].decision_slot == (pc + 500) * 2 and sells[0].decision_ms == (pc + 500) * 1000 and sells[0].at_row == 1 and sells[0].bot == "main"
    assert "PNEW" not in dec.pos


def test_stop_boundary_is_inclusive_and_independent_of_stake():
    dec = stop_flow()
    pc = S_S + 3600
    ref = U.post_spot(print_("PNEW", 1))
    # base chosen so that spot = 0.75 x ref (within float error): find the base by inverting post_spot
    row = print_("PNEW", pc + 500)
    tgt = (Q0 + 0.99 + V) / (0.75 * ref)
    row["base_reserve"] = int(tgt) + 1 + 1   # + the buy's token_raw=1 removed from base
    assert U.post_spot(row) <= 0.75 * ref * (1 + 1e-12)
    run(dec, [row])
    assert len(dec.take_sells()) == 1
    import inspect
    assert list(inspect.signature(U.U1Decider._check_stop).parameters) == ["self", "row", "i"]   # no stake input: stop decisions never depend on stake


def test_no_stop_after_the_timed_exit():
    dec = stop_flow()
    pc = S_S + 3600
    run(dec, [print_("PNEW", pc + 400, B0)])
    run(dec, [print_("PNEW", pc + 361 + 21_600 + 5, 9 * B0)])
    assert dec.take_sells() == [] and "PNEW" not in dec.pos


class StopWrap:
    """the real U1 stop code behind the runner: one main-bot entry on the tape's pool P1 at row 100 of the first forward hour, then the decider's own SellReqs"""

    def __init__(self):
        self.u = new_decider()
        self.done = False

    def decide(self, rows, state):
        from tools import test_c1nf_forward_sim as T1m
        if state.warmup or state.hour != T1m.HOUR0:
            return []
        rows = list(rows)
        prev = rows[99]
        self.u.pos["P1"] = {"land_slot": prev["slot"] + 19, "expiry_s": 10 ** 12, "ref": None}
        for i, r in enumerate(rows):
            r = dict(r)  # the runner streams the file twice: the decider no longer shares row objects with the pricing engine, so the fixture's price lives in the tape itself (below)
            r["quote_reserve"], r["base_reserve"] = Q0, B0 * (3 if i >= 400 else 1)   # flat price, falling to a third from row 400 (the fixture's own reserves are not a price)
            self.u._check_stop(r, i)
        return [S.Entry(pool="P1", mint="MP1", at_row=100, decision_ms=prev["t_recv_ms"], decision_slot=prev["slot"], exit_after_s=21_600 - S.LAT_DECIDING_S)]

    def take_sells(self):
        return self.u.take_sells()


def test_stop_is_priced_by_the_runner_book_through_the_price_exit_mechanism(tmp_path, monkeypatch):
    import collections as C
    from tools import test_c1nf_forward_sim as T1
    monkeypatch.setattr(S, "MIN_WARMUP_H", 1)
    monkeypatch.setattr(S, "WARMUP_MIN_BY_DECIDER", {})
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: C.namedtuple("Usage", "total used free")(1000, 500, 500))
    orig_hour_rows = T1.hour_rows

    def flat_then_falling(ms):  # the same reserves the old stub wrote into the shared row objects, now in the tape file of the first forward hour (price sees them too, as before)
        rows = orig_hour_rows(ms)
        if ms == T1.H0_MS:
            for i, r in enumerate(rows):
                r["quote_reserve"], r["base_reserve"] = Q0, B0 * (3 if i >= 400 else 1)
        return rows
    monkeypatch.setattr(T1, "hour_rows", flat_then_falling)
    T1.write_keep(tmp_path, 3)
    ta = tmp_path / "a.jsonl"
    ta.write_text("")
    m = T1.manifest(tmp_path, decider="u1", warmup_hours=1, u1={"table_a": str(ta), "table_a_sha256": hashlib.sha256(ta.read_bytes()).hexdigest(), "hr_exclusion": True})
    m.pop("wallet_ledger_root", None)
    assert T1.run(tmp_path, m, StopWrap()) == 0
    st = json.loads((Path(m["out_root"]) / "status.json").read_text())
    assert st["book_counts"].get("buys") == 1 and st["book_counts"].get("sell_req_stop") == 1, st["book_counts"]
    tr = [r for r in T1.ledger_rows(m["out_root"]) if r["kind"] == "trade"]
    assert tr and tr[0]["decision_ms"]["exit"] < tr[0]["decision_ms"]["entry"] + 21_600_000   # closed by the stop, not the 6 h timed exit


def test_placebo_plan_draw_is_seeded_excludes_and_is_uniform_over_the_window():
    uni = [{"pool": "p%d" % i, "mint": "m%d" % i, "pc_s": 10_000 + i * 100} for i in range(40)]
    entries = [uni[20]]
    plan, c = U.build_placebo_plan(uni, entries, gate_passed_mints={"m20", "m21"})
    assert c["placebo_drawn"] == 1 and len(plan) == 1
    pool = next(iter(plan))
    assert pool not in ("p20", "p21") and abs(plan[pool]["pc_s"] - 12_000) <= 1800 and plan[pool]["for"] == "p20"
    assert U.build_placebo_plan(uni, entries, {"m20", "m21"})[0] == plan  # deterministic: seed 1 + P epoch seconds
    import random
    cands = [u for u in uni if abs(u["pc_s"] - 12_000) <= 1800 and u["mint"] not in ("m20", "m21")]
    assert pool == random.Random(1 + 12_000).choice(sorted(cands, key=lambda u: (u["pc_s"], u["pool"])))["pool"]


def test_placebo_no_candidate_and_held_pool_excluded():
    plan, c = U.build_placebo_plan([{"pool": "p", "mint": "m", "pc_s": 1000}], [{"pool": "p", "mint": "m", "pc_s": 1000}], {"m"})
    assert plan == {} and c["placebo_no_candidate"] == 1
    uni = [{"pool": "p%d" % i, "mint": "m%d" % i, "pc_s": 1000 + i} for i in range(3)]
    plan, c = U.build_placebo_plan(uni, [uni[0], uni[1]], {"m0", "m1"})
    assert len(plan) == 1 and c["placebo_no_candidate"] == 1  # the only candidate (p2) is held for the second draw


def test_placebo_decider_enters_only_planned_pool_at_its_own_age_360s():
    plan = {"PNEW": {"for": "Pother", "pc_s": S_S + 3600}}
    dec = new_decider(mode="placebo", plan=plan)
    entries, _ = flow(dec, history_rows("A", 0), candidate("A"))  # no gate needed
    assert len(entries) == 1 and entries[0].meta["mode"] == "placebo" and entries[0].decision_ms == (S_S + 3600 + 361) * 1000
    entries, _ = flow(new_decider(mode="placebo", plan={}), history_rows("A", 3), candidate("A"))
    assert entries == []
    with pytest.raises(ValueError):
        new_decider(mode="placebo")


# ---------------------------------------------------------------------------------------------------------------------------- runner glue
def u1_manifest(tmp_path, decider="u1", **extra):
    ta = tmp_path / "a.jsonl"
    ta.write_text(json.dumps({"launcher": "A", "funder": "F", "funding_slot": 1, "status": "resolved"}) + "\n")
    m = {"book_id": "u1-test", "title": "t", "decider": decider, "stakes_sol": [0.1, 0.25, 0.5], "send_lamports": 505_000, "start_hour": START,
         "tape_dir": S.KEEP_ROOT, "out_root": S.SIM_ROOT + "/u1-test", "u1": {"table_a": str(ta), "table_a_sha256": hashlib.sha256(ta.read_bytes()).hexdigest(), "hr_exclusion": True}}
    m.update(extra)
    return m


def test_manifest_accepts_u1_deciders_and_builds_them(tmp_path):
    m = S.check_manifest(u1_manifest(tmp_path))
    d = S.default_decider(m)
    assert isinstance(d, U.U1Decider) and d.mode == "book" and d.table.known("A")
    assert S.default_decider(S.check_manifest(u1_manifest(tmp_path, "u1_lowrep"))).mode == "low_rep"


def test_manifest_refuses_u1_without_its_key_or_placebo_without_plan(tmp_path):
    m = u1_manifest(tmp_path)
    del m["u1"]
    with pytest.raises(S.Refused):
        S.check_manifest(m)
    with pytest.raises(S.Refused):
        S.check_manifest(u1_manifest(tmp_path, "u1_placebo"))
    with pytest.raises(S.Refused):
        S.check_manifest(u1_manifest(tmp_path, "nope"))


def test_runner_refuses_an_entry_decided_before_S_for_u1_too(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda p: __import__("collections").namedtuple("U", "total used free")(1000, 500, 500))
    e = S.Entry(pool="P", mint="M", at_row=0, decision_ms=S_MS - 1, decision_slot=1)
    book = S.Book(S.check_manifest(u1_manifest(tmp_path, out_root=str(tmp_path / "o"))), tmp_path / "run", "0" * 64)
    with pytest.raises(S.Refused):
        book.buy(e)


# ---------------------------------------------------------------------------------------------------------------------------- QP ADD1
def test_cap_open_open_count_never_below_exact_and_refuses_at_ten():
    # exact count at D is 10 (open_fn reports 10): refusal
    entries, st = flow(new_decider(open_fn=lambda: 10), history_rows("A", 3), candidate("A"))
    assert entries == [] and st.counts["cap_open"] == 1
    # the hour-start count over-counts positions that close inside the hour: exact 9, reported 10 -> refused (over-count only), flagged stale by the exact hook
    dec = new_decider(open_fn=lambda: 10, exact_open_fn=lambda d_ms: 9)
    entries, st = flow(dec, history_rows("A", 3), candidate("A"))
    assert entries == [] and st.counts["cap_open_stale"] == 1 and U.board_disclosures(st.counts, True) == [U.STALE_DISCLOSURE % 1]
    # exact also 10: not stale
    entries, st = flow(new_decider(open_fn=lambda: 10, exact_open_fn=lambda d_ms: 10), history_rows("A", 3), candidate("A"))
    assert st.counts["cap_open"] == 1 and st.counts["cap_open_stale"] == 0 and U.board_disclosures(st.counts, True) == []


def test_own_count_without_open_fn_is_an_upper_bound_of_the_true_count():
    dec = new_decider()
    dec.own_entries = [(S_S + 3600 + 361) * 1000 - 3 * 3600 * 1000] * 10  # 10 own entries, timed end (6 h) still ahead: counted even if a stop closed them
    entries, st = flow(dec, history_rows("A", 3), candidate("A"))
    assert entries == [] and st.counts["cap_open"] == 1
    dec = new_decider()
    dec.own_entries = [0] * 10  # all past their 6 h timed end: none open
    assert len(flow(dec, history_rows("A", 3), candidate("A"))[0]) == 1


def test_placebo_one_entry_per_pool_ever_and_summary_not_evaluable_below_five():
    uni = [{"pool": "p%d" % i, "mint": "m%d" % i, "pc_s": 1000 + i} for i in range(4)]
    plan, c = U.build_placebo_plan(uni, [uni[0], uni[1], uni[2]], {"m0", "m1", "m2"})
    assert list(plan) == ["p3"] and c["placebo_no_candidate"] == 2
    r = U.placebo_summary(30, 4)
    assert r["condition_6_evaluable"] is False and r["verdict"].startswith("NO-GO") and r["placebo_n"] == 4 and r["book_n"] == 30
    assert U.placebo_summary(30, 5)["condition_6_evaluable"] is True and U.placebo_summary(30, 5)["verdict"] is None


def test_placebo_plan_written_hashed_before_pricing_and_never_rebuilt(tmp_path):
    plan = {"PNEW": {"for": "Pother", "pc_s": 5}}
    path = tmp_path / "plan.json"
    sha = U.write_placebo_plan(plan, {"placebo_drawn": 1}, path)
    assert oct(path.stat().st_mode & 0o777) == "0o440"
    assert U.load_placebo_plan(path, sha) == plan
    with pytest.raises(FileExistsError):
        U.write_placebo_plan({}, {}, path)  # never rebuilt
    with pytest.raises(ValueError):
        U.load_placebo_plan(path, "0" * 64)  # a differing plan: the second pass refuses
    with pytest.raises(ValueError):
        U.load_placebo_plan(path, None)  # no pinned hash: refused
    m = u1_manifest(tmp_path, "u1_placebo")
    m["u1"].update(placebo_plan=str(path), placebo_plan_sha256=sha)
    assert S.default_decider(S.check_manifest(m)).plan == plan
    m["u1"]["placebo_plan_sha256"] = "1" * 64
    with pytest.raises(ValueError):
        S.default_decider(S.check_manifest(m))
    m2 = u1_manifest(tmp_path, "u1_placebo")
    m2["u1"]["placebo_plan"] = str(path)
    with pytest.raises(S.Refused):
        S.check_manifest(m2)  # no sha key


@pytest.mark.parametrize("val", [False, None, "yes", 1])
def test_runner_refuses_without_hr_exclusion_on(tmp_path, val):
    m = u1_manifest(tmp_path)
    m["u1"]["hr_exclusion"] = val
    with pytest.raises(S.Refused):
        S.check_manifest(m)
    m = u1_manifest(tmp_path)
    del m["u1"]["hr_exclusion"]
    with pytest.raises(S.Refused):
        S.check_manifest(m)


def test_default_hr_predicate_matches_the_pda_creator():
    from solders.pubkey import Pubkey
    mint = str(Pubkey.new_unique())
    import u4_u2_deciders as UD
    pda = UD.hr_pda_default(mint)
    assert U.hr_coin({"mint": mint, "creator": pda}) is True
    assert U.hr_coin({"mint": mint, "creator": str(Pubkey.new_unique())}) is False
    assert U.hr_coin({"mint": "not-base58!", "creator": "x"}) is False
    # the default decider excludes it
    dec = new_decider()
    run(dec, sorted(history_rows("A", 3), key=lambda r: r["t_recv_ms"]), warmup=True)
    c = [create(mint, "A", S_S + 3000, creator=pda)] + [pool_create(mint, S_S + 3600), print_("P" + mint, S_S + 4000)]
    entries, st = run(dec, c)
    assert entries == [] and st.counts["hr_coin_excluded"] == 1


# ============================================================================================================================ u1-start-prep part 1a
import hashlib  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402


def mode_of(p):
    return stat.S_IMODE(os.stat(p).st_mode)


# ---------------------------------------------------------------------------------------------------------------------------- B1 launcher list
def warm_rows_for_list():
    """launcher A: a NON-graduate create at slot 100 (earlier), a graduate whose create is at slot 2000; launcher B: one graduate; launcher C: a create with no graduate"""
    t = S_S - 3 * DAY
    a_nongrad = row("create", t - 5000, prog="pumpfun", mint="NG", trader="A", creator="A")
    a_nongrad["slot"] = 100
    rows = [a_nongrad] + graduate("GA", "A", t) + graduate("GB", "B", t + 500) + [create("NC", "C", t + 700)]
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def test_launcher_list_min_slot_over_all_creates_sorted_atomic_0440_with_counts(tmp_path):
    dec = new_decider(out_root=tmp_path)
    _, st = run(dec, warm_rows_for_list(), warmup=True)
    path = tmp_path / U.LAUNCHER_LIST
    data = path.read_bytes()
    got = [json.loads(x) for x in data.decode().splitlines()]
    ga_slot = (S_S - 3 * DAY - 600) * 2
    assert [g["launcher"] for g in got] == ["A", "B"]                       # sorted; C has no graduate
    assert got[0] == {"launcher": "A", "first_create_slot": 100}            # min over ALL create rows, not the graduate's own create slot
    assert got[1]["first_create_slot"] == (S_S - 3 * DAY + 500 - 600) * 2 and ga_slot > 100
    assert mode_of(path) == 0o440 and not (tmp_path / (U.LAUNCHER_LIST + ".tmp")).exists()
    assert st.counts["u1_launchers_listed"] == 2 and st.counts["u1_first_slot_below_graduate_create"] == 1   # A only: its chosen slot predates its graduate's create slot
    by_hour = [json.loads(x) for x in (tmp_path / U.LAUNCHER_LIST_BY_HOUR).read_text().splitlines()]
    assert by_hour == [{"hour": "h", "launchers": 2, "sha256": hashlib.sha256(data).hexdigest()}] and mode_of(tmp_path / U.LAUNCHER_LIST_BY_HOUR) == 0o440
    # a second warm-up hour rewrites (never appends) the list and adds one by-hour record
    st2 = S.RunState(hour="h2", warmup=True, day="d")
    dec.decide(sorted(graduate("GC", "D", S_S - 2 * DAY), key=lambda r: r["t_recv_ms"]), st2)
    assert [json.loads(x)["launcher"] for x in path.read_text().splitlines()] == ["A", "B", "D"] and st2.counts["u1_launchers_listed"] == 1   # counts are per-hour deltas
    assert len((tmp_path / U.LAUNCHER_LIST_BY_HOUR).read_text().splitlines()) == 2


def test_launcher_list_slot_has_one_definition_and_the_u1_fetcher_reads_the_list(tmp_path):
    dec = new_decider(out_root=tmp_path)
    run(dec, warm_rows_for_list(), warmup=True)
    assert U.first_create_slot(dec.reg, "A") == 100 and not hasattr(U, "FIRST_CREATE_SLOT_RULE")   # one definition, no switch (QP verdict 5)
    rows, below = U.launcher_list_rows(dec.reg)
    assert rows[0] == {"launcher": "A", "first_create_slot": 100} and below == 1
    import u1_funder_fetch as F
    n, sha, _ = U.write_launcher_list(dec.reg, tmp_path / "again.jsonl")
    got = F.read_launcher_list(str(tmp_path / "again.jsonl"))
    assert len(list(got)) == n == 2


def test_launcher_list_is_not_written_without_out_root_or_in_forward_hours(tmp_path):
    dec = new_decider()
    run(dec, warm_rows_for_list(), warmup=True)
    assert not list(tmp_path.iterdir())
    dec = new_decider(out_root=tmp_path)
    run(dec, stream(candidate("A")))
    assert not (tmp_path / U.LAUNCHER_LIST).exists()


# ---------------------------------------------------------------------------------------------------------------------------- B2 plan input and the plan subcommand
def forward_with_entry(out_root):
    dec = new_decider(out_root=out_root)
    entries, st = flow(dec, history_rows("A", 3), candidate("A"))
    assert len(entries) == 1
    return dec


def test_plan_input_is_persisted_append_only_0440_and_a_restart_replay_does_not_duplicate(tmp_path):
    dec = forward_with_entry(tmp_path)
    path = tmp_path / U.PLAN_INPUT
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert {r["kind"] for r in lines} == {"universe", "gate", "hour"} and mode_of(path) == 0o440 and lines[-1] == {"kind": "hour", "hour": "h"}
    assert [r for r in lines if r["kind"] == "gate"] == [{"kind": "gate", "pool": "PNEW", "mint": "NEW", "pc_s": S_S + 3600, "entered": True}]
    assert set(lines[0]) == {"kind", "pool", "mint", "pc_s"}
    sha1 = hashlib.sha256(path.read_bytes()).hexdigest()
    forward_with_entry(tmp_path)                      # restart = full replay from W with a new decider on the same out_root
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha1
    # a second forward hour appends and leaves the earlier lines as they were
    before = path.read_bytes()
    dec.universe_log.append({"pool": "PX", "mint": "X", "pc_s": S_S + 7200})
    dec.decide([], S.RunState(hour="h3", warmup=False, day="d"))
    assert path.read_bytes().startswith(before) and len(path.read_text().splitlines()) == len(lines) + 2 and mode_of(path) == 0o440   # the new universe row + the hour marker
    assert dec.universe_log == [] and dec.gate_log == []                                                                           # flushed rows leave memory


def test_low_rep_and_placebo_modes_persist_no_plan_input(tmp_path):
    dec = new_decider(out_root=tmp_path, mode="low_rep")
    flow(dec, history_rows("A", 1, 2), candidate("A"))
    assert not (tmp_path / U.PLAN_INPUT).exists()


def markers(a_s, b_s):
    """one hour-complete marker per whole UTC hour holding a_s .. b_s"""
    h = S.parse_hour(S.hour_key(a_s * 1000))
    out = []
    while h <= b_s * 1000:
        out.append({"kind": "hour", "hour": S.hour_key(h)})
        h += S.HOUR_MS
    return out


def write_rows(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_plan_subcommand_builds_the_plan_prints_sha_and_counters_only_and_never_rebuilds(tmp_path, capsys):
    pi = tmp_path / "in.jsonl"
    rows = [{"kind": "gate", "pool": "PE", "mint": "E", "pc_s": 10_000, "entered": True}, {"kind": "gate", "pool": "PF", "mint": "F", "pc_s": 10_100, "entered": False}]
    rows += [{"kind": "universe", "pool": "P%d" % i, "mint": "M%d" % i, "pc_s": 10_000 + 50 * i} for i in range(-5, 6)] + [{"kind": "universe", "pool": "PE", "mint": "E", "pc_s": 10_000}]
    rows += [{"kind": "universe", "pool": "PF", "mint": "F", "pc_s": 10_100}] + markers(10_000, 10_000 + 2 * 3600)
    write_rows(pi, rows)
    out = tmp_path / "plan.json"
    assert U.main(["plan", "--in", str(pi), "--out", str(out), "--through-hour", S.hour_key((10_000 + 7200) * 1000)]) == 0
    printed = json.loads(capsys.readouterr().out)
    plan = json.loads(out.read_text())["plan"]
    assert set(printed) == {"plan_sha256", "plan_pools", "universe", "book_entries", "counters"} and printed["plan_pools"] == len(plan) == 1
    assert printed["plan_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest() and mode_of(out) == 0o440
    assert next(iter(plan.values()))["for"] == "PE" and "PE" not in plan and "PF" not in plan
    U.load_placebo_plan(out, printed["plan_sha256"])
    with pytest.raises(FileExistsError):
        U.main(["plan", "--in", str(pi), "--out", str(out), "--through-hour", S.hour_key((10_000 + 7200) * 1000)])
    with pytest.raises(SystemExit):                                                  # --through-hour is required
        U.main(["plan", "--in", str(pi), "--out", str(tmp_path / "n.json")])


H1 = "2026-11-20T01"
END1 = S.parse_hour(H1) // 1000 + 3600      # end of hour H1 (s)


def cut_input(entry_pc, cand_pc):
    """one book entry and one candidate; markers through hour H1 (D of the candidate = cand_pc + 360 s is in the file when it is before END1)"""
    return [{"kind": "gate", "pool": "PL", "mint": "L", "pc_s": entry_pc, "entered": True}, {"kind": "universe", "pool": "PL", "mint": "L", "pc_s": entry_pc},
            {"kind": "universe", "pool": "PC", "mint": "C", "pc_s": cand_pc}] + markers(S_S, END1 - 1)


def test_through_hour_keeps_a_candidate_near_the_cut_for_a_ripe_entry(tmp_path):
    # reviewer 1: entry P = END1 - 2500 (P + 2,160 s < END1: ripe), candidate at P + 1,800 s = END1 - 700 whose D = END1 - 340 is inside the closed hour: it is in the universe and drawn
    P = END1 - 2500
    pi = tmp_path / "in.jsonl"
    write_rows(pi, cut_input(P, P + 1800))
    assert U.plan_main(pi, tmp_path / "p.json", H1)["plan_pools"] == 1
    # an entry whose P + 2,160 s is after the end of H1 is not ripe (and nothing else is left): refused, counted
    pi2 = tmp_path / "in2.jsonl"
    write_rows(pi2, cut_input(END1 - 2000, END1 - 1500))
    with pytest.raises(ValueError, match="no ripe"):
        U.plan_main(pi2, tmp_path / "p2.json", H1)
    assert not (tmp_path / "p2.json").exists()
    # boundary: P + 2,160 s == END1 is NOT ripe (the hour containing it is not closed), one second earlier is
    for dp, n in ((0, 0), (-1, 1)):
        ent = [{"kind": "gate", "pool": "PL", "mint": "L", "pc_s": END1 - 2160 + dp, "entered": True}, {"kind": "gate", "pool": "PK", "mint": "K", "pc_s": END1 - 5000, "entered": True}]
        pj = tmp_path / ("b%d.jsonl" % n)
        write_rows(pj, ent + [{"kind": "universe", "pool": "PK", "mint": "K", "pc_s": END1 - 5000}] + markers(S_S, END1 - 1))
        assert U.read_plan_input(pj, H1)[3] == 1 - n


def test_plan_refuses_an_incomplete_plan_input(tmp_path):
    P = END1 - 2500
    full = cut_input(P, P + 1800)
    pi = tmp_path / "in.jsonl"
    write_rows(pi, [])
    with pytest.raises(ValueError, match="no hour marker"):                       # empty file
        U.plan_main(pi, tmp_path / "a.json", H1)
    write_rows(pi, [r for r in full if r["kind"] != "hour"])
    with pytest.raises(ValueError, match="no hour marker"):                       # rows without markers (an old / foreign file)
        U.plan_main(pi, tmp_path / "b.json", H1)
    # a replay stopped before H1: markers end earlier than H1
    stopped = [r for r in full if r["kind"] != "hour"] + markers(S_S, END1 - 3600 - 1)
    write_rows(pi, stopped)
    with pytest.raises(ValueError, match="not complete through"):
        U.plan_main(pi, tmp_path / "c.json", H1)
    # a marker gap
    gap = [r for r in full if r["kind"] != "hour"] + [m for m in markers(S_S - 3600, END1 - 1) if m["hour"] != "2026-11-20T00"]
    write_rows(pi, gap)
    with pytest.raises(ValueError, match="consecutive"):
        U.plan_main(pi, tmp_path / "d.json", H1)
    assert not [f for f in tmp_path.iterdir() if f.suffix == ".json"]            # no plan file was written by any refusal
    write_rows(pi, full)
    assert U.plan_main(pi, tmp_path / "e.json", H1)["plan_pools"] == 1


def test_decider_restart_replay_leaves_a_file_a_plan_refuses_until_the_hour_is_replayed(tmp_path):
    dec = new_decider(out_root=tmp_path)
    run(dec, stream(candidate("A", "N1", S_S + 3600)))                              # forward hour "h": marker written, even though the hour held only a candidate
    path = tmp_path / U.PLAN_INPUT
    assert json.loads(path.read_text().splitlines()[-1]) == {"kind": "hour", "hour": "h"}
    new_decider(out_root=tmp_path)                                                    # a restart: the file is rebuilt by the replay, empty until then
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        U.plan_main(path, tmp_path / "x.json", "h")


# ---------------------------------------------------------------------------------------------------------------------------- B4 strict gap exclusion for U1
class GapStub:
    """a U1-shaped entry (no exit_from_decision) at row 100 of the first forward hour with a timed exit 3,000 s later (the 6 h hold does not fit the fixture)"""

    def decide(self, rows, state):
        rows = list(rows)
        if state.warmup or state.hour != __import__("tools.test_c1nf_forward_sim", fromlist=["x"]).HOUR0:
            return []
        prev = rows[99]
        return [S.Entry(pool="P1", mint="MP1", at_row=100, decision_ms=prev["t_recv_ms"], decision_slot=prev["slot"], exit_after_s=3000.0)]


@pytest.mark.parametrize("decider", ["u1", "u1_lowrep", "u1_placebo"])
def test_u1_gap_between_decision_and_exit_removes_the_trade_and_the_disclosure_is_verbatim(tmp_path, monkeypatch, decider):
    import collections as C
    from tools import test_c1nf_forward_sim as T1
    monkeypatch.setattr(S, "MIN_WARMUP_H", 1)
    monkeypatch.setattr(S, "WARMUP_MIN_BY_DECIDER", {})
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: C.namedtuple("Usage", "total used free")(1000, 500, 500))
    T1.write_keep(tmp_path, 3)
    f = tmp_path / "keep" / ("tape-%s.jsonl" % S.hour_key(T1.H0_MS))
    rows = [json.loads(x) for x in f.read_text().splitlines()]
    t = rows[500]["t_recv_ms"]
    rows.insert(500, {"v": 1, "type": "gap", "prog": "all", "kind": "tip_gap_backlog_jump", "data_loss": True, "from_slot": rows[499]["slot"], "to_slot": rows[500]["slot"], "t_recv_ms": t, "block_time": t // 1000})
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    ta = tmp_path / "a.jsonl"
    ta.write_text("")
    u1 = {"table_a": str(ta), "table_a_sha256": hashlib.sha256(ta.read_bytes()).hexdigest(), "hr_exclusion": True}
    if decider == "u1_placebo":
        plan = tmp_path / "plan.json"
        u1.update(placebo_plan=str(plan), placebo_plan_sha256=U.write_placebo_plan({}, {}, plan))
    m = T1.manifest(tmp_path, decider=decider, warmup_hours=1, u1=u1)
    m.pop("wallet_ledger_root", None)
    assert T1.run(tmp_path, m, GapStub()) == 0
    g = json.loads((Path(m["out_root"]) / "gap_exclusions.json").read_text())
    assert list(k for k in g if k != "disclosure") == ["main"]                            # no placebo bot for U1: the placebo is its own book run
    assert g["main"]["total"] == {"entries": 1, "feed_gap_timed": 1, "feed_gap_requested": 0}
    assert g["disclosure"] == S.GAP_DISCLOSURE.format(n=1, N=1, timed=1, requested=0) and g["disclosure"].startswith("Strict feed-gap exclusion: any data_loss gap between decision and exit")
    assert not [r for r in T1.ledger_rows(m["out_root"]) if r["kind"] == "trade"]       # the trade is removed, not priced
    assert any(r["kind"] == "excluded" and str(r["exclusion_reason"]).startswith("feed_gap") for r in T1.ledger_rows(m["out_root"]))


def test_u1_has_no_placebo_bot_and_the_engine_default_exclusion_is_not_gap_in_hold(tmp_path):
    from tools import test_c1nf_forward_sim as T1
    m = S.check_manifest(u1_manifest(tmp_path, out_root=str(tmp_path / "o")))
    assert "u1" not in S.PLACEBO_DECIDERS and set(S.U1_DECIDERS).isdisjoint(S.PLACEBO_DECIDERS)
    bk = S.Book(m, tmp_path / "run", "0" * 64)
    assert set(bk.bots) == {"main"} and "hold" not in json.dumps(m)


# ---------------------------------------------------------------------------------------------------------------------------- B5 cap_open_stale wording
def test_cap_open_stale_is_never_shown_as_zero_without_an_exact_count():
    for counts in ({}, {"cap_open_stale": 0}, {"cap_open_stale": 7, "cap_open": 7}):
        assert U.board_disclosures(counts) == [U.CAP_OPEN_NOT_COMPUTED]
    assert U.CAP_OPEN_NOT_COMPUTED == ("cap_open_stale not computed; cap_open counts the book's own entries for a full 6 h (stops ignored), so it may refuse entries an exact count would have taken.")
    dec = new_decider(open_fn=lambda: 10)
    entries, st = flow(dec, history_rows("A", 3), candidate("A"))
    assert entries == [] and st.counts["cap_open"] == 1 and "cap_open_stale" not in st.counts and dec.exact_open_fn is None
    assert U.board_disclosures(st.counts, dec.exact_open_fn is not None) == [U.CAP_OPEN_NOT_COMPUTED]


# ---------------------------------------------------------------------------------------------------------------------------- B6 last warm-up hour graduates
def test_graduate_in_the_last_warmup_hour_with_D_after_S_is_queued_counted_and_decided():
    dec = new_decider()
    last = S_S - 100                                           # pool_create 100 s before S: D = S + 260 s
    wu = sorted(history_rows("A", 3), key=lambda r: r["t_recv_ms"]) + [create("LW", "A", last - 600), pool_create("LW", last)]
    run(dec, sorted(wu, key=lambda r: r["t_recv_ms"]), warmup=True)
    assert dec.c["queued_in_last_warmup_hour"] == 1 and len(dec.pending) == 1 and dec.pending[0][0] == (last + 360) * 1000
    early = dec.c["queued_in_last_warmup_hour"]
    entries, st = run(dec, [print_("PLW", S_S + 100), print_("PLW", S_S + 259), print_("PLW", S_S + 261)])
    assert len(entries) == 1 and entries[0].mint == "LW" and st.counts["universe_graduates"] == 1 and early == 1


def test_graduate_with_D_before_S_is_not_queued_in_warmup_and_tracing_stays_forward_only():
    calls = []

    def fetch(launcher, slot):
        calls.append(launcher)
        return None
    dec = new_decider(fetch_fn=fetch)
    run(dec, sorted(history_rows("A", 3) + graduate("OLD", "A", S_S - 3600)[:2] + [create("LW", "A", S_S - 700), pool_create("LW", S_S - 100)], key=lambda r: r["t_recv_ms"]), warmup=True)
    assert dec.c["queued_in_last_warmup_hour"] == 1 and calls == [] and len(dec.pending) == 1


# ---------------------------------------------------------------------------------------------------------------------------- G10 exit_s is ignored for U1
def test_u1_exit_s_in_the_manifest_is_ignored_and_the_hold_is_the_ruling_6h(tmp_path):
    outs = []
    for kw in ({}, {"exit_s": 300}, {"exit_s": 3600}, {"exit_s": 1}):
        m = S.check_manifest(u1_manifest(tmp_path, out_root=str(tmp_path / "o"), **kw))
        dec = S.default_decider(m)
        entries, _ = flow(dec, history_rows("A", 3), candidate("A"))
        assert len(entries) == 1
        outs.append(entries[0].as_dict())
    assert outs[0] == outs[1] == outs[2] == outs[3]
    assert outs[0]["exit_after_s"] == U.HOLD_S - S.LAT_DECIDING_S == 21_600 - 4.085 and U.HOLD_S == 21_600
    assert S.EXIT_S_MAX["u1"] == 3600 < U.HOLD_S         # the manifest bound is irrelevant to U1: the decider sets the hold


# ---------------------------------------------------------------------------------------------------------------------------- C1-NF / U4 / U2 stay md5-equal
PINNED_MANIFEST_SHA = {
    "c1nf": "66c455eaab8a350b8a4ea79a34b5691dd9fa43509a5bb9898d193f932b15bf55",
    "u4": "efad2907b973df7ebecdbb8d009201d43137f7aa4659a50e54f7615a04ad4931",
    "u2": "a5b8bddfe9fd87a81b14ef3a2933656d3ba8c36926a155a38887a6e66b9e7b7b",
}
PINNED_DECISION_MD5 = {"u4": "09ce2974b4e3fb5f4f41af799a0ea422", "u2": "9a06b45b5c6e33825a0557b0e4e2697d"}   # taken on origin/main d29de639 before this PR; 408 per-hour md5s joined


@pytest.mark.parametrize("decider,kw", [("c1nf", {}), ("u4", {"exit_s": 14_400}), ("u2", {"exit_s": 43_200})])
def test_canonical_manifest_sha256_of_c1nf_u4_u2_is_unchanged(decider, kw):
    m = {"book_id": "pin-" + decider, "title": "t", "decider": decider, "stakes_sol": [0.1, 0.25, 0.5, 1.0] if decider == "c1nf" else [0.1, 0.25, 0.5], "send_lamports": 505_000,
         "start_hour": START, "tape_dir": S.KEEP_ROOT, "out_root": S.SIM_ROOT + "/pin-" + decider}
    m.update(kw)
    assert S.manifest_sha256(S.check_manifest(m)) == PINNED_MANIFEST_SHA[decider]


@pytest.mark.parametrize("decider", ["u4", "u2"])
def test_u4_u2_fixture_decision_md5_is_unchanged(decider):
    import u4_u2_deciders as UD
    from tools import test_u4_u2_forward_sim as T2
    per_hour = T2.run_long(UD.U4Decider if decider == "u4" else UD.U2Decider, T2.long_rows())[0]
    assert len(per_hour) == 408 and hashlib.md5("".join(per_hour).encode()).hexdigest() == PINNED_DECISION_MD5[decider]


# ============================================================================================================================ u1-start-prep part 1b (QP-RULING-1117-u1-start)
from tools import test_u1_funder_fetch as TF  # noqa: E402
import u1_funder_fetch as F  # noqa: E402

DAYK = "2026-11-20"
FX = "FunderX"
BACKFILL_A = [{"launcher": "OLD", "funder": FX, "funding_slot": 1, "status": "resolved"}]


def fx_mock(hub=True, **kw):
    full = [{"signature": "f%d" % i, "slot": i, "blockTime": 10_000 - i // 100, "err": None} for i in range(1000)]
    sigs = {"NEWL": [[TF.sig(1)]], "L3": [[TF.sig(3)]], "L4": [[TF.sig(4)]], FX: [full if hub else full[:10]]}
    txs = {s: TF.tx([TF.sys_ins("transfer", source=FX, destination=d, lamports=2_000_000)], slot=5, bt=10_000) for s, d in (("s1", "NEWL"), ("s3", "L3"), ("s4", "L4"))}
    return TF.Mock(sigs=sigs, txs=txs, **kw)


def mk_fwd(root, mock=None, cap=F.CAPS["forward_day"], sleeps=None, factory_calls=None):
    def factory():
        if factory_calls is not None:
            factory_calls.append(1)
        if mock is None:
            raise AssertionError("transport built: a key would have been read")
        return mock
    return U.ForwardFetch(root, BACKFILL_A, [], transport_factory=factory, cap_per_day=cap, sleep_fn=(sleeps.append if sleeps is not None else (lambda s: None)), clock=lambda: 0.0, day_fn=lambda: DAYK)


def test_forward_fetch_journal_keys_order_mode_and_rps_constant(tmp_path):
    sl = []
    ff = mk_fwd(tmp_path, fx_mock(), sleeps=sl)
    ff.set_hour("h1")
    row = ff("NEWL", 50)
    assert row == {"launcher": "NEWL", "funder": FX, "funding_slot": 5, "status": "resolved"} and ff.hub_of(FX) is True    # the backfill rows seeded by_funder: the hub test ran
    lines = [json.loads(x) for x in (tmp_path / U.FWD_JOURNAL).read_text().splitlines()]
    assert lines == [row, {"funder": FX, "hub_activity": True}] and set(lines[0]) == set(F.TABLE_A_KEYS) and set(lines[1]) == set(F.TABLE_B_KEYS)   # B directly after its A row
    assert mode_of(tmp_path / U.FWD_JOURNAL) == 0o640
    assert F.FORWARD_RPS == 2.0 and ff.fetcher.rps == 2.0 and ff.rps == 2.0 and sl and all(abs(x - 0.5) < 1e-9 for x in sl)   # fixed <= 2 rps, spacing 0.5 s
    assert json.loads((tmp_path / U.FWD_CREDITS).read_text()) == {DAYK: ff.total_calls()} and ff.total_calls() == 3


def test_replay_of_a_full_journal_makes_zero_rpc_and_hub_of_follows_the_journal(tmp_path):
    ff = mk_fwd(tmp_path, fx_mock())
    ff.set_hour("h1")
    ff("NEWL", 50)
    ff("L3", 50)
    assert ff.hub_of(FX) is None                       # L3's own call produced no B line: no hub fact from an earlier launcher is handed back
    (tmp_path / "md5-by-hour.jsonl").write_text(json.dumps({"hour": "h1"}) + "\n")
    made = []
    ff2 = mk_fwd(tmp_path, None, factory_calls=made)   # restart: the transport factory raises if it is ever used
    ff2.set_hour("h1")
    assert ff2("NEWL", 50)["status"] == "resolved" and ff2.hub_of(FX) is True
    assert ff2("L3", 50)["launcher"] == "L3" and ff2.hub_of(FX) is None
    assert ff2.total_calls() == 0 and made == []
    # the journal was not loaded at start: a launcher out of order is refused
    ff3 = mk_fwd(tmp_path, None)
    ff3.set_hour("h1")
    with pytest.raises(S.Refused, match="out of order"):
        ff3("L4", 50)


def test_stored_hour_needing_a_launcher_the_journal_lacks_is_refused_with_no_rpc(tmp_path):
    (tmp_path / "md5-by-hour.jsonl").write_text(json.dumps({"hour": "h1"}) + "\n")
    mock = fx_mock()
    ff = mk_fwd(tmp_path, mock)
    ff.set_hour("h1")
    with pytest.raises(S.Refused, match="stored hour"):
        ff("NEWL", 50)
    assert mock.log == []
    ff.set_hour("h2")                                   # an hour not yet stored may fetch live
    assert ff("NEWL", 50)["status"] == "resolved" and mock.log


def test_persisted_day_meter_survives_a_restart_and_the_cap_still_stops_calls(tmp_path):
    ff = mk_fwd(tmp_path, fx_mock(), cap=5)
    ff.set_hour("h1")
    ff("NEWL", 50)                                      # 3 credits (2 for the launcher + the hub test)
    assert json.loads((tmp_path / U.FWD_CREDITS).read_text()) == {DAYK: 3}
    (tmp_path / U.FWD_JOURNAL).unlink()                 # only the credit file survives the "restart" (a fresh journal)
    mock = fx_mock()
    ff2 = mk_fwd(tmp_path, mock, cap=5)
    ff2.set_hour("h2")
    assert ff2.meters.used_by_day() == {DAYK: 3}
    assert ff2("L3", 50)["status"] == "resolved"        # 2 more credits: 5
    r4 = ff2("L4", 50)
    assert r4["status"] == "fetch_capped" and ff2.meters.used_by_day() == {DAYK: 5} and len(mock.log) == 2   # no call past the cap
    assert json.loads((tmp_path / U.FWD_CREDITS).read_text()) == {DAYK: 5}


def test_by_hour_chain_has_counts_and_the_journal_sha_and_is_not_duplicated_by_a_replay(tmp_path):
    ff = mk_fwd(tmp_path, fx_mock())
    ff.set_hour("h1")
    ff("NEWL", 50)
    ff.end_hour("h1")
    ff.end_hour("h1")
    rec = [json.loads(x) for x in (tmp_path / U.FWD_BY_HOUR).read_text().splitlines()]
    assert len(rec) == 1 and rec[0]["fwd_rows_total"] == 2 and rec[0]["rpc_calls_hour"] == 3 and rec[0]["credits_day"] == 3
    assert rec[0]["journal_sha256"] == hashlib.sha256((tmp_path / U.FWD_JOURNAL).read_bytes()).hexdigest()


def fwd_decider(root, mock, stored=()):
    if stored:
        (Path(root) / "md5-by-hour.jsonl").write_text("".join(json.dumps({"hour": h}) + "\n" for h in stored))
    ff = mk_fwd(root, mock)
    t = U.FunderTable.from_rows(BACKFILL_A)
    return new_decider(table=t, fetch_fn=ff, out_root=root), ff


def test_decider_forward_fetch_links_the_new_launcher_and_a_restart_replays_with_zero_rpc(tmp_path):
    hist = history_rows("OLD", 3)
    cand = candidate("NEWL")
    cand.insert(0, row("create", S_S + 100, prog="pumpfun", mint="EARLY", trader="NEWL", creator="NEWL"))     # a non-graduated create BEFORE the graduate's create
    cand = sorted(cand, key=lambda r: r["t_recv_ms"])
    mock = fx_mock(hub=False)
    dec, ff = fwd_decider(tmp_path, mock)
    entries, st = flow(dec, hist, cand)
    assert len(entries) == 1 and entries[0].meta["hist_kind"] == "funder" and st.counts["u1_rpc_calls"] == 3 and st.counts["u1_fwd_rows"] == 2
    first_slot = U.first_create_slot(dec.reg, "NEWL")
    assert first_slot == (S_S + 100) * 2 < dec.reg.creates["NEW"]["slot"]          # Q1: the fetch got the min slot over all creates, not the graduate's create slot
    assert dec.reg.grads["NEW"].create_slot > first_slot
    assert (tmp_path / U.FWD_BY_HOUR).exists()
    dec2, ff2 = fwd_decider(tmp_path / "r", None, stored=())                   # a different dir has no journal: control for the fixture
    (tmp_path / "r").mkdir(exist_ok=True)
    # restart on the same out_root with the hour stored: zero RPC (the factory raises), identical decisions
    dec3, ff3 = fwd_decider(tmp_path, None, stored=["h"])
    entries3, st3 = flow(dec3, hist, cand)
    assert [e.as_dict() for e in entries3] == [e.as_dict() for e in entries] and ff3.total_calls() == 0 and st3.counts["u1_rpc_calls"] == 0


def test_second_graduate_of_a_fetch_error_launcher_makes_zero_calls():
    calls = []

    def fetch(launcher, slot):
        calls.append(launcher)
        return {"launcher": launcher, "funder": None, "funding_slot": None, "status": "fetch_error"}
    dec = new_decider(fetch_fn=fetch)
    run(dec, sorted(history_rows("A", 3), key=lambda r: r["t_recv_ms"]), warmup=True)
    run(dec, stream(candidate("A", "N1", S_S + 3600), candidate("A", "N2", S_S + 5400)))
    assert calls == ["A"] and dec.table.known("A") and dec.table.funder_of("A") is None


def test_dry_run_all_warmup_hours_make_zero_rpc_and_never_touch_the_key(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("key or transport touched")
    monkeypatch.setattr(F, "load_key", boom)
    monkeypatch.setattr(F, "make_transport", boom)
    m = u1_manifest(tmp_path, out_root=str(tmp_path / "o"))
    dec = S.default_decider(S.check_manifest(m))
    assert isinstance(dec.fetch_fn, U.ForwardFetch)
    _, st = run(dec, sorted(history_rows("A", 3) + candidate("A"), key=lambda r: r["t_recv_ms"]), warmup=True)
    assert st.counts["u1_rpc_calls"] == 0 and dec.fetch_fn.total_calls() == 0 and not (tmp_path / "o" / U.FWD_JOURNAL).exists() and not (tmp_path / "o" / U.FWD_CREDITS).exists()


# ---------------------------------------------------------------------------------------------------------------------------- B7 checked manifest keys
def sha_of(b):
    return hashlib.sha256(b).hexdigest()


def test_manifest_sha_keys_are_checked_and_refuse_on_mismatch(tmp_path):
    m = u1_manifest(tmp_path)
    ta = Path(m["u1"]["table_a"])
    assert m["u1"]["table_a_sha256"] == sha_of(ta.read_bytes())
    S.default_decider(S.check_manifest(dict(m)))
    bad = u1_manifest(tmp_path)
    ta.write_text(ta.read_text() + "\n")                                         # the file changes under the pinned sha256
    with pytest.raises(S.Refused, match="sha256 differs"):
        S.default_decider(S.check_manifest(bad))
    for drop in ("table_a_sha256",):
        m2 = u1_manifest(tmp_path)
        del m2["u1"][drop]
        with pytest.raises(S.Refused):
            S.check_manifest(m2)
    m3 = u1_manifest(tmp_path)
    m3["u1"]["table_b_sha256"] = "0" * 64                                         # a sha key without its file
    with pytest.raises(S.Refused):
        S.check_manifest(m3)
    m4 = u1_manifest(tmp_path)
    m4["u1"]["table_b"] = str(tmp_path / "b.jsonl")                              # a file key without its sha key
    with pytest.raises(S.Refused):
        S.check_manifest(m4)
    m5 = u1_manifest(tmp_path)
    m5["u1"]["table_a_sha256"] = "XYZ"
    with pytest.raises(S.Refused):
        S.check_manifest(m5)


def test_table_b_and_labels_are_loaded_only_when_their_sha_matches_and_no_labels_means_h3_empty(tmp_path):
    m = u1_manifest(tmp_path, out_root=str(tmp_path / "o"))
    assert S.default_decider(S.check_manifest(dict(m))).labels == frozenset()    # H3 empty: no label list pinned
    tb, lb = tmp_path / "b.jsonl", tmp_path / "labels.txt"
    tb.write_text(json.dumps({"funder": "F", "hub_activity": True}) + "\n")
    lb.write_text("EXCH1 EXCH2\n")
    m["u1"].update(table_b=str(tb), table_b_sha256=sha_of(tb.read_bytes()), labels=str(lb), labels_sha256=sha_of(lb.read_bytes()))
    d = S.default_decider(S.check_manifest(json.loads(json.dumps(m))))
    assert d.labels == {"EXCH1", "EXCH2"} and d.table.hub_activity("F") is True
    lb.write_text("EXCH1\n")
    with pytest.raises(S.Refused):
        S.default_decider(S.check_manifest(json.loads(json.dumps(m))))


# ---------------------------------------------------------------------------------------------------------------------------- Q7 extension plan and disclosure
def test_plan_input_rows_carry_no_price_or_outcome_keys(tmp_path):
    forward_with_entry(tmp_path)
    keys = set()
    for ln in (tmp_path / U.PLAN_INPUT).read_text().splitlines():
        keys |= set(json.loads(ln))
    assert keys == {"kind", "pool", "mint", "pc_s", "entered", "hour"}


def test_extension_plan_keeps_the_earlier_rows_unchanged_or_is_refused(tmp_path):
    base = 10_000
    rows = []
    for i in range(6):                                                           # six entries 100 s apart, each with a crowd of candidates around it
        rows.append({"kind": "gate", "pool": "PE%d" % i, "mint": "E%d" % i, "pc_s": base + 100 * i, "entered": True})
    rows += [{"kind": "universe", "pool": "PU%d" % i, "mint": "U%d" % i, "pc_s": base - 1500 + 25 * i} for i in range(160)]
    pi = tmp_path / "in.jsonl"
    write_rows(pi, rows + markers(base - 3600, base + 100 * 6 + 3 * 3600))
    h_all = S.hour_key((base + 100 * 6 + 3 * 3600) * 1000)
    h_mid = S.hour_key((base + 250 + 2160 + 3600) * 1000)                         # ripe for the first entries only
    assert S.hour_key(base * 1000) <= h_mid <= h_all
    p1, p2, p3 = tmp_path / "p1.json", tmp_path / "p2.json", tmp_path / "p3.json"
    r1 = U.plan_main(pi, p1, h_mid)
    r2 = U.plan_main(pi, p2, h_all, extends=p1)
    assert r2["plan_pools"] >= r1["plan_pools"] >= 1 and r1["counters"].get("placebo_entry_not_ripe", 0) >= 0
    old, new = json.loads(p1.read_text())["plan"], json.loads(p2.read_text())["plan"]
    assert all(new[k] == v for k, v in old.items())
    # an extension that changes an earlier row is refused and writes nothing
    tampered = tmp_path / "t.json"
    plan = dict(old)
    k0 = sorted(plan)[0]
    plan[k0] = dict(plan[k0], **{"for": "PX"}) if plan else plan
    if old:
        tampered.write_text(json.dumps({"plan": plan, "counters": {}}))
        with pytest.raises(ValueError, match="refused"):
            U.plan_main(pi, p3, h_all, extends=tampered)
        assert not p3.exists()


def test_a_gate_pass_with_D_in_the_next_hour_is_not_ripe_and_new_disclosure_text():
    uni_ok = U.PLACEBO_RIPE_S == 2160 and U.PLACEBO_WINDOW_S == 1800 and U.DECISION_DELAY_S == 360 and U.PLACEBO_WINDOW_S + U.DECISION_DELAY_S == U.PLACEBO_RIPE_S
    assert uni_ok
    s = U.placebo_summary(10, 7, plan_sha="abc", built="2026-11-22T00:00:00Z", through_hour="2026-11-21T23")
    assert s["condition_6_evaluable"] and s["disclosure"] == ("Placebo built once at the look by a deterministic plan pass (plan sha256 abc, built 2026-11-22T00:00:00Z, through hour 2026-11-21T23) from the book's "
                                                             "graduation and gate lists; priced afterwards by a separate full-replay run of the same engine; it was not run alongside the book.")
    assert not U.placebo_summary(10, 4)["condition_6_evaluable"]
    assert not U.placebo_summary(10, 9, placebo_excluded_share=0.21)["condition_6_evaluable"] and U.placebo_summary(10, 9, placebo_excluded_share=0.20)["condition_6_evaluable"]


def test_a_gap_mode_key_in_a_u1_manifest_is_refused(tmp_path):
    for k in ("gap_mode", "gap_in_hold", "gap_hold"):
        with pytest.raises(S.Refused):
            S.check_manifest(u1_manifest(tmp_path, **{k: "hold"}))


# ============================================================================================================================ reviewer round 2 (3b1443b4)
def test_torn_last_line_of_the_by_hour_sidecar_is_dropped_counted_and_recomputed(tmp_path):
    ff = mk_fwd(tmp_path, fx_mock())
    ff.set_hour("h1")
    ff("NEWL", 50)
    ff.end_hour("h1")
    good = (tmp_path / U.FWD_BY_HOUR).read_text()
    (tmp_path / U.FWD_BY_HOUR).write_text(good + '{"hour": "h2", "fwd_rows')                    # a crash in the middle of the next hour's write
    ff2 = mk_fwd(tmp_path, None)
    ff2.set_hour("h2")
    ff2.end_hour("h2")                                                                            # no exception: the torn line is dropped and h2 is recomputed
    recs = [json.loads(x) for x in (tmp_path / U.FWD_BY_HOUR).read_text().splitlines()]
    assert [r["hour"] for r in recs] == ["h1", "h2"] and ff2.counters["u1_by_hour_torn_dropped"] == 1
    # the torn line is the hour being recomputed or an earlier one: both recover; a torn line that is not last is corruption
    (tmp_path / U.FWD_BY_HOUR).write_text('{"hour": "h1", "x\n' + good)
    with pytest.raises(S.Refused, match="corrupt"):
        mk_fwd(tmp_path, None).end_hour("h3")


def test_sidecar_and_credits_writes_are_fsynced_and_atomic(tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    ff = mk_fwd(tmp_path, fx_mock())
    ff.set_hour("h1")
    n0 = len(calls)
    ff._persist_credits()
    assert len(calls) - n0 >= 2 and not list(tmp_path.glob("*.tmp"))                                # file + directory
    n1 = len(calls)
    ff.end_hour("h1")
    assert len(calls) - n1 >= 2 and not list(tmp_path.glob("*.tmp"))


def test_a_torn_hub_row_after_a_complete_a_row_drops_the_pair_and_refetches_live(tmp_path):
    ff = mk_fwd(tmp_path, fx_mock())
    ff.set_hour("h1")
    ff("NEWL", 50)
    j = tmp_path / U.FWD_JOURNAL
    data = j.read_bytes()
    j.write_bytes(data[:-10])                                                                       # the B line is cut in the middle
    mock = fx_mock()
    ff2 = mk_fwd(tmp_path, mock)
    ff2.set_hour("h2")
    row = ff2("NEWL", 50)
    assert row["status"] == "resolved" and ff2.hub_of(FX) is True and ff2.counters["u1_fwd_torn_pair_dropped"] == 1 and mock.log      # fetched again, hub test included
    lines = [json.loads(x) for x in j.read_text().splitlines()]
    assert len(lines) == 2 and set(lines[0]) == set(F.TABLE_A_KEYS) and set(lines[1]) == set(F.TABLE_B_KEYS)        # exactly one A and one B, no torn tail
    # a torn A line alone is cut and refetched the same way (as before)
    j.write_bytes(data[:5])
    ff3 = mk_fwd(tmp_path, fx_mock())
    ff3.set_hour("h3")
    assert ff3("NEWL", 50)["status"] == "resolved" and len(j.read_text().splitlines()) == 2


def test_no_rpc_guard_refuses_any_live_fetch_in_code(tmp_path):
    mock = fx_mock()
    ff = U.ForwardFetch(tmp_path, BACKFILL_A, [], transport_factory=lambda: mock, allow_rpc=False, day_fn=lambda: DAYK)
    ff.set_hour("h1")
    with pytest.raises(S.Refused, match="RPC refused"):
        ff("NEWL", 50)
    assert mock.log == [] and not (tmp_path / U.FWD_JOURNAL).exists()
    with pytest.raises(S.Refused):
        ff._rpc("getTransaction", [])
    # through the manifest: u1.no_rpc true builds a guarded fetcher; any other value is refused
    m = u1_manifest(tmp_path, out_root=str(tmp_path / "o"))
    m["u1"]["no_rpc"] = True
    d = S.default_decider(S.check_manifest(m))
    assert d.fetch_fn.allow_rpc is False
    m2 = u1_manifest(tmp_path, out_root=str(tmp_path / "o"))
    m2["u1"]["no_rpc"] = False
    with pytest.raises(S.Refused):
        S.check_manifest(m2)
    # the book's own manifest (no key) is live-capable
    assert S.default_decider(S.check_manifest(u1_manifest(tmp_path, out_root=str(tmp_path / "o")))).fetch_fn.allow_rpc is True


@pytest.mark.parametrize("decider", ["u1_lowrep", "u1_placebo"])
def test_only_the_book_builds_a_live_fetcher(tmp_path, monkeypatch, decider):
    def boom(*a, **k):
        raise AssertionError("key or transport touched by a non-book run")
    monkeypatch.setattr(F, "load_key", boom)
    monkeypatch.setattr(F, "make_transport", boom)
    m = u1_manifest(tmp_path, decider, out_root=str(tmp_path / "o"))
    if decider == "u1_placebo":
        plan = tmp_path / "plan.json"
        m["u1"].update(placebo_plan=str(plan), placebo_plan_sha256=U.write_placebo_plan({}, {}, plan))
    dec = S.default_decider(S.check_manifest(m))
    assert dec.fetch_fn is None
    run(dec, sorted(history_rows("A", 3), key=lambda r: r["t_recv_ms"]), warmup=True)
    run(dec, stream(candidate("A")))
    assert not (tmp_path / "o" / U.FWD_JOURNAL).exists() and not (tmp_path / "o" / U.FWD_CREDITS).exists()
