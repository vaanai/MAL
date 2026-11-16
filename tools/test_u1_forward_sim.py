"""u1_forward_sim: reputation without look-ahead, hub exclusion, cluster definition, entry/exit constants, zero entries before S, random-pick plan, runner manifest glue.
Synthetic fixtures only; no real tape, funder, outcome, fill or P&L is opened. Nothing here says any book makes money."""
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
def test_spot_after_applies_the_trades_own_delta_to_pre_trade_reserves():
    r = print_("P", 1)
    r.update(side="buy", sol_lamports=10 ** 9, token_raw=10 ** 12)
    assert U.spot_after(r) == (Q0 + 10 ** 9 + V) / (B0 - 10 ** 12)
    r["side"] = "sell"
    assert U.spot_after(r) == (Q0 - 10 ** 9 + V) / (B0 + 10 ** 12)
    r["side"] = "x"
    assert U.spot_after(r) is None


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
def test_stop_rule_first_print_after_landing_at_or_below_75_percent():
    prints = [(100, 1.0), (110, 0.76), (120, 0.75), (130, 0.5)]
    assert U.first_stop_print(prints, 1.0, 105) == (120, 139)
    assert U.first_stop_print(prints, 1.0, 130) is None
    assert U.first_stop_print([(100, 0.1)], 1.0, 100) is None  # a print AT the landing slot is not after it


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
         "tape_dir": S.KEEP_ROOT, "out_root": S.SIM_ROOT + "/u1-test", "u1": {"table_a": str(ta), "hr_exclusion": True}}
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
    assert entries == [] and st.counts["cap_open_stale"] == 1 and U.board_disclosures(st.counts) == [U.STALE_DISCLOSURE % 1]
    # exact also 10: not stale
    entries, st = flow(new_decider(open_fn=lambda: 10, exact_open_fn=lambda d_ms: 10), history_rows("A", 3), candidate("A"))
    assert st.counts["cap_open"] == 1 and st.counts["cap_open_stale"] == 0 and U.board_disclosures(st.counts) == []


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
    pda = U.hr_pda(mint)
    assert U.hr_coin({"mint": mint, "creator": pda}) is True
    assert U.hr_coin({"mint": mint, "creator": str(Pubkey.new_unique())}) is False
    assert U.hr_coin({"mint": "not-base58!", "creator": "x"}) is False
    # the default decider excludes it
    dec = new_decider()
    run(dec, sorted(history_rows("A", 3), key=lambda r: r["t_recv_ms"]), warmup=True)
    c = [create(mint, "A", S_S + 3000, creator=pda)] + [pool_create(mint, S_S + 3600), print_("P" + mint, S_S + 4000)]
    entries, st = run(dec, c)
    assert entries == [] and st.counts["hr_coin_excluded"] == 1
