"""arena_score: the outcome-blind instant, the pinned order of operations, the deciding cell, exclusions, orphans, refusals. Synthetic ledgers only."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from tools import arena_score as S
from tools.arena_paper import TEST_ENV, Ledger, LedgerCorrupt, canon, register_bot as _register_bot, utc_day
from tools.arena_score import (
    STEPS,
    Refused,
    blind_read_instant,
    cell_stats,
    cell_verdict,
    load_decisions,
    main,
    record_read,
    render,
    score_all,
    score_bot,
)
from tools.arena_paper import book_fail_legs
from tools.r4b_book import boot_lo, fail_legs



os.environ[TEST_ENV] = "1"  # the test-only registry overrides (fake clock, fake sha) are honoured in this process only


def register_bot(*a, **k):
    k.setdefault("verify_sha", False)  # tests use fake shas
    k.setdefault("deciding_stake", 0.25)
    k.setdefault("model_ids", [])
    return _register_bot(*a, **k)


DAY = 86_400_000
DAY0 = 1_789_000_000_000 - (1_789_000_000_000 % DAY)  # a UTC midnight
SEND = 505_000
STACK = {"send": SEND, "rent": 2_039_280, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "exit_lag_s": 0.55, "guard": 1.15, "portal_ppm": 0,
         "press_intercept_provisional": -1.42, "strand_rule": "both", "e1_floor_per_005": 384_022, "sps_entry": 0.4, "sps_exit": 0.4}
VSRC = {"entry": "row", "exit": "row", "span": {"row": 5}}


def _legs(pnl, ssb=0.0, near=0.0, strand=False, rule="total"):
    fn = book_fail_legs if rule == "total" else (lambda p, f, s, a, b, c: fail_legs(p, f, a, b, c))
    flat, press, _, _ = fn(np.array([pnl]), np.array([True]), np.array([strand]), np.array([ssb]), np.array([near]), SEND)
    return float(flat[0]), float(press[0])


def trade_row(pid, i, day, pnl19, pnl13=None, pnl19_sc=None, pnl13_sc=None, stake=0.25, bot="botA", prog="pumpswap", src="tape", vsrc=None, kind="trade", cfg=None):
    ms = DAY0 + day * DAY + 3_600_000 + i * 1000
    p13 = pnl19 if pnl13 is None else pnl13
    p19s = pnl19 if pnl19_sc is None else pnl19_sc
    p13s = (p13 if pnl19_sc is None else p19s) if pnl13_sc is None else pnl13_sc
    vals = {"1p3": {"total": p13, "sc": p13s}, "1p9": {"total": pnl19, "sc": p19s}}
    legs = {r: {} for r in ("total", "sc")}
    calc = {}
    for lat in ("1p3", "1p9"):
        calc[lat] = {"landing_slot": 1, "Qe": 1.0, "Be": 1.0, "spot": 1.0, "ssb": 0.0, "nearby_lamports": 0.0, "Qx": 1.0, "Bx": 1.0, "by_rule": {}}
        for rule in ("total", "sc"):
            flat, press = _legs(vals[lat][rule], rule=rule)
            legs[rule][f"flat_{lat}"] = {"state": "filled", "net_lamports": flat}
            legs[rule][f"press_{lat}"] = {"state": "filled", "net_lamports": press}
            calc[lat]["by_rule"][rule] = {"pnl_nofail": float(vals[lat][rule]), "filled": True, "state": "filled", "strand": False, "e1_extra": 0.0,
                                          "p_press_provisional": 0.2}
    return {"schema": "arena_trade_v1", "kind": kind, "trade_id": f"{pid}-s{int(stake * 1000)}", "position_id": pid, "bot_id": bot, "frozen_sha": "abc1234",
            "decision_ms": {"entry": ms, "exit": ms + 5000}, "stake_sol": stake, "prog": prog, "state_src": src, "stack": dict(cfg or STACK), "legs": legs, "calc": calc,
            "v_src": vsrc or VSRC}


def open_row(pid, i, day, bot="botA", ms=None):
    ms = DAY0 + day * DAY + 3_600_000 + i * 1000 if ms is None else ms
    return {"schema": "arena_trade_v1", "kind": "open", "trade_id": f"{pid}-open", "position_id": pid, "bot_id": bot, "frozen_sha": "abc1234",
            "decision_ms": {"entry": ms, "exit": None}, "stack": dict(STACK), "code_head": "abc1234", "code_dirty": False, "engine_id": "eng1"}


def start_row(engine_id="eng1", head="abc1234", dirty=False, bot="botA"):
    return {"schema": "arena_trade_v1", "kind": "start", "trade_id": f"start-{engine_id}-{bot}", "engine_id": engine_id, "bot_id": bot, "frozen_sha": "abc1234",
            "decision_ms": {"entry": DAY0 + 1000, "exit": None}, "code_head": head, "code_dirty": dirty, "stack": dict(STACK)}


def refused_row(i, day, bot="botA", reason="no_prints"):
    ms = DAY0 + day * DAY + 7_200_000 + i * 1000
    return {"schema": "arena_trade_v1", "kind": "refused", "trade_id": f"ref{i}-refused", "position_id": f"ref{i}", "bot_id": bot, "frozen_sha": "abc1234",
            "decision_ms": {"entry": ms, "exit": None}, "reason": reason, "stack": dict(STACK)}


def positions(pnls19, days=5, stakes=(0.25,), **kw):
    """One open row and one trade row per stake per position; positions spread over `days` UTC dates in order."""
    rows = [start_row()]
    per = int(np.ceil(len(pnls19) / days))
    for i, p in enumerate(pnls19):
        pid = f"p{i:04d}"
        day = i // per
        rows.append(open_row(pid, i, day))
        for st in stakes:
            rows.append(trade_row(pid, i, day, p, stake=st, **kw))
    return rows


def good_pnls(n=100, seed=3):
    return np.random.default_rng(seed).normal(4e6, 2e6, n)


def setup(tmp_path, rows, stake=0.25, clock=DAY0, bot="botA", models=()):
    reg = tmp_path / "bots.json"
    register_bot(bot, "abc1234", clock, reg, deciding_stake=stake, model_ids=list(models), now_ms=0)
    led = Ledger(tmp_path / "ledger")
    for r in rows:
        led.append(r)
    return str(tmp_path / "ledger"), str(reg)


def read(tmp_path, rows, now=DAY0 + 30 * DAY, **kw):
    root, reg = setup(tmp_path, rows, **kw)
    return score_all(root, reg, now_ms=now)


def status_of(res, bot="botA"):
    return res["bots"][bot]["status"] if bot in res["bots"] else "REFUSED"


# ------------------------------------------------------------------------------------------------------------------------------ the outcome-blind instant
def test_blind_instant_is_the_first_midnight_with_100_attempts_over_5_dates():
    ms = [DAY0 + d * DAY + 3_600_000 + i * 1000 for d in range(5) for i in range(20)]
    assert blind_read_instant(ms, DAY0) == DAY0 + 5 * DAY
    assert blind_read_instant(ms[:-1], DAY0) is None  # 99 attempts
    assert blind_read_instant([DAY0 + d * DAY + 1000 * i for d in range(4) for i in range(40)], DAY0) is None  # 160 attempts on 4 dates
    # 100 attempts reached on day 3 but only 4 dates: the boundary waits for the 5th date
    ms2 = [DAY0 + d * DAY + 1000 * i for d in range(4) for i in range(30)] + [DAY0 + 4 * DAY + 5000]
    assert blind_read_instant(ms2, DAY0) == DAY0 + 5 * DAY
    # attempts before the clock do not count
    assert blind_read_instant(ms, DAY0 + DAY) is None


def test_blind_instant_loads_no_fill_exit_or_pnl(tmp_path):
    rows = [open_row("a", 1, 0), refused_row(2, 0)]
    root, _ = setup(tmp_path, rows)
    p = next((tmp_path / "ledger" / "botA").glob("trades-*.jsonl"))
    with open(p, "a") as f:
        f.write('{"kind":"trade", this line would raise if it were parsed\n')
    assert len(load_decisions(root, "botA")) == 2  # the poisoned trade line was never parsed


# ------------------------------------------------------------------------------------------------------------------------------ pure statistics
def test_bootstrap_is_deterministic_and_is_the_labs_function():
    x = np.random.default_rng(7).normal(2e6, 5e6, 300)
    a, b = cell_stats(x, ["d"] * 300)["ci90_lo_sol"], cell_stats(x, ["d"] * 300)["ci90_lo_sol"]
    assert a == b == boot_lo(x / 1e9)
    g = np.random.default_rng(1)  # 1,000 draws, seed 1, 5th percentile of the resampled means
    bm = np.array([(x / 1e9)[g.integers(0, 300, 300)].mean() for _ in range(1000)])
    assert a == float(np.percentile(bm, 5))


def test_cell_stats_numbers():
    s = cell_stats([10e6, -4e6, 3e6, 2e6, 1e6, -1e6], ["a", "a", "b", "b", "c", "c"])
    assert s["n"] == 6 and s["days"] == 3 and s["days_pos"] == 2
    assert s["total_sol"] == pytest.approx(0.011) and s["mean_sol"] == pytest.approx(0.011 / 6)
    assert s["ex_top3_sol"] == pytest.approx(0.011 - 0.015)  # drop 10, 3, 2 (million)
    assert s["ex_best_day_sol"] == pytest.approx(0.011 - 0.006)
    assert s["daily"]["a"] == {"n": 2, "sol": pytest.approx(0.006)}


@pytest.mark.parametrize("n,days,why", [(99, 6, "n 99 < 100"), (100, 4, "days 4 < 5")])
def test_cell_minimums(n, days, why):
    s = cell_stats(np.full(n, 4e6), [str(i % days) for i in range(n)])
    assert not cell_verdict(s)["pass"] and any(why in m for m in cell_verdict(s)["misses"])
    assert cell_verdict(cell_stats(np.full(100, 4e6) + np.arange(100) * 1e3, [str(i % 5) for i in range(100)]))["pass"]  # exactly 100 on 5 days


def test_majority_ci_and_top3_criteria():
    rng = np.random.default_rng(5)
    pnl = np.concatenate([rng.normal(9e6, 1e6, 60), rng.normal(-2e5, 1e5, 40)])
    s = cell_stats(pnl, ["0"] * 30 + ["1"] * 30 + ["2"] * 14 + ["3"] * 13 + ["4"] * 13)
    v = cell_verdict(s)
    assert s["days_pos"] == 2 and any("majority" in m for m in v["misses"]) and s["total_sol"] > 0 and s["ci90_lo_sol"] > 0
    noisy = cell_stats(rng.normal(2e5, 5e6, 120), [str(i % 6) for i in range(120)])
    assert any("CI90" in m for m in cell_verdict(noisy)["misses"])
    win3 = cell_stats(np.concatenate([[3e8, 2e8, 1e8], np.full(117, -1e6)]), [str(i % 6) for i in range(120)])
    assert win3["total_sol"] > 0 and win3["ex_top3_sol"] < 0 and not cell_verdict(win3)["pass"]


# ------------------------------------------------------------------------------------------------------------------------------ the read
def test_a_clean_book_passes_its_deciding_cell(tmp_path):
    res = read(tmp_path, positions(good_pnls()))
    v = res["bots"]["botA"]
    assert v["status"] == "PASS" and v["deciding"]["pass"] and v["deciding"]["latency"] == "1p9" and v["deciding"]["stake"] == "0.25"
    assert len(v["deciding"]["subcells"]) == 4 and v["deciding"]["weakest_cell"] in v["books"]["0.25"]["cells"]
    assert v["read_boundary_ms"] == DAY0 + 5 * DAY and v["attempts"] == 100 and v["restart_orphans"] == 0
    assert res["arena_books_with_clock_before_read"] == 1 and res["cells_existed"] == 12 and res["cells_read"] == 4 and res["books_read"] == 1
    assert "weakest_cell" in v["deciding"] and "deciding_cell" not in v["deciding"]


def test_not_ready_before_the_boundary(tmp_path):
    assert status_of(read(tmp_path, positions(good_pnls(99)))) == "NOT_READY"
    sub = tmp_path / "b"
    sub.mkdir()
    assert status_of(read(sub, positions(good_pnls()), now=DAY0 + 4 * DAY)) == "NOT_READY"  # the boundary exists but now is before it


def test_the_deciding_cell_is_1p9_at_the_pinned_stake(tmp_path):
    # 1.3 s leg loses, 1.9 s leg wins: PASS (the 1.3 s leg is report-only)
    bad13 = positions(good_pnls(), pnl13=-3e6)
    assert status_of(read(tmp_path, bad13)) == "PASS"
    sub = tmp_path / "b"
    sub.mkdir()
    # the reverse: 1.9 s loses, 1.3 s wins
    assert status_of(read(sub, positions(np.full(100, -3e6), pnl13=4e6))) == "FAIL"
    # the pinned stake decides: stake 0.25 loses, stake 0.5 wins, pinned 0.5 -> PASS; pinned 0.25 -> FAIL
    two = [start_row()]
    for i, p in enumerate(good_pnls()):
        pid = f"p{i:04d}"
        two += [open_row(pid, i, i // 20), trade_row(pid, i, i // 20, -3e6, stake=0.25), trade_row(pid, i, i // 20, p, stake=0.5)]
    sub2, sub3 = tmp_path / "c", tmp_path / "d"
    sub2.mkdir()
    sub3.mkdir()
    assert status_of(read(sub2, two, stake=0.5)) == "PASS"
    assert status_of(read(sub3, two, stake=0.25)) == "FAIL"


def test_must_pass_under_both_fail_models_and_both_strand_rules(tmp_path):
    # constant +200,000: flat = +94,250 > 0; pressure (p = 0.289 for all) = -3,745 < 0
    res = read(tmp_path, positions(np.full(100, 200_000.0) + np.arange(100) * 10.0))
    v = res["bots"]["botA"]
    assert v["status"] == "FAIL" and "press_1p9_total" in v["deciding"]["misses"] and "flat_1p9_total" not in v["deciding"]["misses"]
    # passes under the strand class, loses under "sc"
    sub = tmp_path / "b"
    sub.mkdir()
    res = read(sub, positions(good_pnls(), pnl19_sc=-3e6))
    assert res["bots"]["botA"]["status"] == "FAIL" and any(k.endswith("_sc") for k in res["bots"]["botA"]["deciding"]["misses"])


def test_exclusions_reduce_n_and_above_five_percent_is_not_decidable(tmp_path):
    rows = positions(good_pnls())
    # 3 excluded positions (3%) -> priced n = 97 < 100: a FAIL on the n criterion, not NOT_DECIDABLE
    ex = []
    for r in rows:
        if r["kind"] == "trade" and r["position_id"] in ("p0001", "p0002", "p0003"):
            ex.append(dict(r, kind="excluded", exclusion_reason="feed_gap", legs=None, calc=None))
        else:
            ex.append(r)
    res = read(tmp_path, ex)
    v = res["bots"]["botA"]
    assert v["status"] == "FAIL" and v["books"]["0.25"]["excluded"] == {"feed_gap": 3} and any("n 97" in m for ms in v["deciding"]["misses"].values() for m in ms)
    # 6 excluded of 100 -> NOT_DECIDABLE
    sub = tmp_path / "b"
    sub.mkdir()
    ex6 = [dict(r, kind="excluded", exclusion_reason="feed_gap", legs=None, calc=None) if r["kind"] == "trade" and int(r["position_id"][1:]) < 6 else r for r in rows]
    v = read(sub, ex6)["bots"]["botA"]
    assert v["status"] == "NOT_DECIDABLE" and any("above 5%" in x for x in v["reasons"])
    # refused buys count in the excluded share: 5 excluded + 2 refused of 102 attempts = 6.9%
    sub2 = tmp_path / "c"
    sub2.mkdir()
    ex5 = [dict(r, kind="excluded", exclusion_reason="feed_gap", legs=None, calc=None) if r["kind"] == "trade" and int(r["position_id"][1:]) < 5 else r for r in rows]
    v = read(sub2, ex5 + [refused_row(1, 1), refused_row(2, 1)])["bots"]["botA"]
    assert v["status"] == "NOT_DECIDABLE" and v["refused_buys"] == {"no_prints": 2}
    # a trade whose print had no V is excluded and counted by the scorer even if the engine priced it
    sub3 = tmp_path / "d"
    sub3.mkdir()
    nov = [dict(r, v_src={"entry": "none", "exit": "row", "span": {"none": 1}}) if r["kind"] == "trade" and int(r["position_id"][1:]) < 2 else r for r in rows]
    v = read(sub3, nov)["bots"]["botA"]
    assert v["books"]["0.25"]["excluded"] == {"missing_v": 2} and v["books"]["0.25"]["n_trades"] == 98


def test_a_late_print_over_a_gap_excludes_the_trade(tmp_path):
    rows = positions(good_pnls()) + [{"schema": "arena_trade_v1", "kind": "late_print", "trade_id": "p0005-late-x", "position_id": "p0005", "bot_id": "botA",
                                       "decision_ms": {"entry": DAY0 + 1000, "exit": None}, "gap_overlap": True},
                                      {"schema": "arena_trade_v1", "kind": "late_print", "trade_id": "p0006-late-x", "position_id": "p0006", "bot_id": "botA",
                                       "decision_ms": {"entry": DAY0 + 2000, "exit": None}, "gap_overlap": False}]
    v = read(tmp_path, rows)["bots"]["botA"]
    assert v["late_prints"] == 2 and v["books"]["0.25"]["excluded"] == {"late_print_gap": 1}


def test_an_unclosed_position_is_a_restart_orphan(tmp_path):
    rows = [r for r in positions(good_pnls()) if not (r["kind"] == "trade" and r["position_id"] == "p0007")]  # opened, never closed
    v = read(tmp_path, rows)["bots"]["botA"]
    assert v["status"] == "NOT_DECIDABLE" and v["restart_orphans"] == 1 and any("orphan" in x for x in v["reasons"])


def test_a_book_with_a_bonding_row_is_report_only(tmp_path):
    rows = positions(good_pnls())
    rows = [dict(r, prog="pump_bonding") if r["kind"] == "trade" and r["position_id"] == "p0010" else r for r in rows]
    v = read(tmp_path, rows)["bots"]["botA"]
    assert v["status"] == "REPORT_ONLY" and v["has_bonding"] and v["deciding"]["pass"]  # the numbers print, the verdict does not


def test_a_pass_with_no_total_ex_best_day_is_not_proposed_for_canary(tmp_path):
    p = np.concatenate([np.full(20, 25e6), np.full(20, 1.0e6), np.full(20, 1.0e6), np.full(20, -5e6), np.full(20, -5e6)])
    p = p + np.random.default_rng(2).normal(0, 1e4, 100)
    res = read(tmp_path, positions(p))
    d = res["bots"]["botA"]["deciding"]
    assert res["bots"]["botA"]["status"] == "PASS" and d["not_proposed_for_canary"]
    assert "NOT PROPOSED FOR CANARY" in render(res)
    sub = tmp_path / "b"
    sub.mkdir()
    assert not read(sub, positions(good_pnls()))["bots"]["botA"]["deciding"]["not_proposed_for_canary"]


def test_derived_last_rows_are_counted_at_their_value_and_a_rerun_without_them_is_report_only(tmp_path):
    rows = [dict(r, state_src="derived_last") if r["kind"] == "trade" and int(r["position_id"][1:]) % 4 == 0 else r for r in positions(good_pnls())]
    v = read(tmp_path, rows)["bots"]["botA"]
    c = v["books"]["0.25"]["cells"]["flat_1p9_total"]
    assert v["status"] == "PASS" and c["n"] == 100 and c["derived_last_n"] == 25 and c["derived_last_share"] == 0.25
    assert c["without_derived_last"]["n"] == 75 and c["without_derived_last"]["report_only"]


# ------------------------------------------------------------------------------------------------------------------------------ order of operations
def test_the_order_of_operations_is_pinned(tmp_path):
    root, reg = setup(tmp_path, positions(good_pnls()))
    trace: list[str] = []
    from tools.arena_paper import load_registry

    score_bot("botA", load_registry(reg), Ledger(root).read_bot("botA"), DAY0 + 30 * DAY, trace)
    assert tuple(trace) == STEPS == ("clock_filter", "read_boundary", "exclusions", "pressure_refit", "statistics")


def test_pressure_is_refit_on_covered_non_excluded_attempts_only(tmp_path):
    """The intercept changes if (a) a row after the read boundary is added, (b) an excluded row is added: it is fit after the clock filter, the boundary and the
    exclusions, on the book that is scored."""
    pnl = good_pnls()

    def with_ssb(rows, k):
        out = []
        for r in rows:
            if r["kind"] == "trade" and int(r["position_id"][1:]) < k:
                r = dict(r, calc={lat: dict(c, ssb=5.0, nearby_lamports=3e9) for lat, c in r["calc"].items()})
            out.append(r)
        return out

    base = with_ssb(positions(pnl), 30)
    icpt = lambda res: res["bots"]["botA"]["books"]["0.25"]["cells"]["press_1p9_total"]["intercept"]  # noqa: E731
    a = icpt(read(tmp_path, base))
    # (a) rows decided after the boundary (day 5) are not in the refit, however many pressure features they carry
    sub = tmp_path / "b"
    sub.mkdir()
    after = [open_row("late0", 0, 5)] + [trade_row("late0", 0, 5, 1e6)]
    after[1] = dict(after[1], calc={lat: dict(c, ssb=9.0, nearby_lamports=9e9) for lat, c in after[1]["calc"].items()})
    assert icpt(read(sub, base + after)) == a
    # (b) rows before the clock are not in the refit
    sub2 = tmp_path / "c"
    sub2.mkdir()
    early = [open_row("early0", 0, -3), trade_row("early0", 0, -3, 1e6)]
    early[1] = dict(early[1], calc={lat: dict(c, ssb=9.0, nearby_lamports=9e9) for lat, c in early[1]["calc"].items()})
    assert icpt(read(sub2, base + early)) == a
    # (c) an excluded attempt is not in the refit: excluding the 30 high-pressure positions changes the intercept
    sub3 = tmp_path / "d"
    sub3.mkdir()
    ex = [dict(r, kind="excluded", exclusion_reason="feed_gap", legs=None, calc=None) if r["kind"] == "trade" and int(r["position_id"][1:]) < 3 else r for r in base]
    assert icpt(read(sub3, ex)) != a


# ------------------------------------------------------------------------------------------------------------------------------ refusals
def test_a_changed_frozen_sha_a_duplicate_and_mixed_configs_are_refused(tmp_path):
    rows = positions(good_pnls())
    res = read(tmp_path, rows + [dict(trade_row("zz", 1, 1, 5e6), frozen_sha="def5678")])
    assert "frozen_sha" in res["bots_refused"]["botA"] and "botA" not in res["bots"]
    sub = tmp_path / "b"
    sub.mkdir()
    dup = trade_row("p0001", 1, 3, 5e6)  # same position + stake, another day file
    assert "duplicated" in read(sub, rows + [dict(dup, decision_ms={"entry": DAY0 + 3 * DAY, "exit": None}, trade_id=rows[1]["trade_id"])])["bots_refused"]["botA"]
    sub2 = tmp_path / "c"
    sub2.mkdir()
    mixed = [dict(r, stack=dict(STACK, rent=0)) if r["kind"] == "trade" and r["position_id"] == "p0009" else r for r in rows]
    assert "mixed stack configs" in read(sub2, mixed)["bots_refused"]["botA"]


def test_a_registry_line_without_deciding_stake_or_models_is_refused(tmp_path):
    reg = tmp_path / "bots.json"
    reg.write_text(canon({"kind": "register", "bot_id": "botA", "frozen_sha": "abc1234", "clock_start_ms": DAY0, "registered_at_ms": 0}) + "\n")
    led = Ledger(tmp_path / "ledger")
    for r in positions(good_pnls()):
        led.append(r)
    assert "deciding_stake" in score_all(str(tmp_path / "ledger"), str(reg), now_ms=DAY0 + 30 * DAY)["bots_refused"]["botA"]


def test_the_one_read_per_bot_is_recorded_once(tmp_path):
    res = read(tmp_path, positions(good_pnls()))
    p = record_read(tmp_path / "reads", res["bots"]["botA"])
    assert p.exists() and json.loads(p.read_text())["status"] == "PASS"
    with pytest.raises(Refused, match="already recorded"):
        record_read(tmp_path / "reads", res["bots"]["botA"])
    sub = tmp_path / "b"
    sub.mkdir()
    nr = read(sub, positions(good_pnls(50)))["bots"]["botA"]
    with pytest.raises(Refused, match="not ready"):
        record_read(tmp_path / "reads2", nr)


def test_multiplicity_counts_stopped_books_and_the_cli_prints_it(tmp_path, capsys):
    rows = positions(good_pnls())
    root, reg = setup(tmp_path, rows)
    register_bot("botB", "bbb2222", DAY0, reg, deciding_stake=1.0, model_ids=["haiku-5.5@t0.2"], now_ms=0)
    from tools.arena_paper import stop_bot

    stop_bot("botB", DAY0 + DAY, reg)
    register_bot("botC", "ccc3333", DAY0 + 90 * DAY, reg, now_ms=0)  # a clock after the read: not counted
    out = tmp_path / "out.json"
    assert main(["--ledger-root", root, "--registry", reg, "--bot", "botA", "--json", str(out), "--now-ms", str(DAY0 + 30 * DAY)]) == 0
    text = capsys.readouterr().out
    doc = json.loads(out.read_text())
    assert doc["arena_books_with_clock_before_read"] == 2 and doc["arena_books_registered"] == 3 and doc["arena_books_stopped"] == 1
    assert doc["cells_existed"] == 24 and doc["cells_read"] == 4  # --bot A shrinks the cells read, not the cells that existed
    assert "2 books in bots.json with a clock before the read" in text and "PREVIEW" in text and "DECIDING CELL" in text and "STATUS PASS" in text
    canon(doc)  # strict JSON


def test_refused_is_an_exception_type():
    assert issubclass(Refused, Exception) and S.STEPS[0] == "clock_filter"


# ------------------------------------------------------------------------------------------------------------------------------ fixed-intercept leg, daily digest
def test_the_fixed_intercept_pressure_leg_is_printed_beside_the_refit_and_is_report_only(tmp_path):
    res = read(tmp_path, positions(good_pnls()))
    v = res["bots"]["botA"]
    for k, c in v["books"]["0.25"]["cells"].items():
        if k.startswith("press_"):
            fx = c["fixed_intercept"]
            assert fx["report_only"] is True and fx["intercept"] == STACK["press_intercept_provisional"] and fx["mean_p"] is not None
        else:
            assert c["fixed_intercept"] is None
    text = render(res)
    assert "fixed icpt -1.4200 (REPORT-ONLY, never decides)" in text
    # it never decides: corrupt the fixed-intercept figure's inputs (the provisional intercept in the stored config) and the verdict is unchanged
    sub = tmp_path / "b"
    sub.mkdir()
    rows = [dict(r, stack=dict(r["stack"], press_intercept_provisional=-30.0)) for r in positions(good_pnls())]
    assert status_of(read(sub, rows)) == "PASS"


def test_daily_digest_has_hashes_and_counts_only_and_parses_no_trade_line(tmp_path, capsys):
    import hashlib

    rows = positions(good_pnls(10), days=2) + [refused_row(1, 0)]
    root, reg = setup(tmp_path, rows)
    register_bot("botB", "bbb2222", DAY0, reg, now_ms=0)
    day = utc_day(DAY0 + 3_600_000)
    p = next((tmp_path / "ledger" / "botA").glob(f"trades-{day}.jsonl"))
    with open(p, "a") as f:
        f.write('{"kind":"trade", this line would raise if it were parsed\n')
    assert main(["--ledger-root", root, "--registry", reg, "--daily-digest", "--day", day]) == 0
    text = capsys.readouterr().out
    data = p.read_bytes()
    dec = b"".join(ln + b"\n" for ln in data.split(b"\n") if ln.startswith(b'{"kind":"open"') or ln.startswith(b'{"kind":"refused"'))
    assert hashlib.sha256(data).hexdigest() in text and hashlib.sha256(dec).hexdigest() in text
    dg = S.digest_day(root, reg, day)
    a = dg["bots"]["botA"]
    assert a["counts"] == {"open": 5, "trade": 6, "refused": 1, "start": 1} and a["lines"] == 13  # 5 positions, their 5 trades, 1 refused buy, and the poisoned line (counted by prefix, never parsed)
    assert dg["bots"]["botB"] == {"ledger_sha256": None, "decision_log_sha256": None, "lines": 0, "counts": {}}
    assert "net_lamports" not in text and "total_sol" not in text and "mean" not in text and "ci90" not in text
    assert main(["--ledger-root", root, "--registry", reg, "--daily-digest"]) == 2  # --day is required
    with pytest.raises(ValueError):
        S.digest_day(root, reg, "26-09-2026")


# ------------------------------------------------------------------------------------------------------------------------------ pending / orphans, refusals by cause, registry record
def test_pending_attempts_block_recording_and_become_orphans_after_the_settle_window(tmp_path, capsys):
    rows = [r for r in positions(good_pnls()) if not (r["kind"] == "trade" and r["position_id"] == "p0099")]  # the last position (day 4) is still open
    root, reg = setup(tmp_path, rows)
    boundary = DAY0 + 5 * DAY
    soon = boundary + 3_600_000  # 1 h after the boundary: p0099 is 24 h - 99 s old
    v = score_all(root, reg, now_ms=soon)["bots"]["botA"]
    assert v["status"] == "PENDING" and v["pending"] == 1 and v["restart_orphans"] == 0 and v["preview_status"] in ("PASS", "FAIL")
    with pytest.raises(Refused, match="pending"):
        record_read(tmp_path / "reads", v)
    args = ["--ledger-root", root, "--registry", reg, "--bot", "botA", "--record", "--reads-dir", str(tmp_path / "reads")]
    assert main(args + ["--now-ms", str(soon)]) == 3 and not (tmp_path / "reads").exists()  # refused, nothing written
    assert "PENDING" in capsys.readouterr().out
    later = boundary + 26 * 3_600_000  # past the settle window: now an orphan, the read is NOT_DECIDABLE and may be recorded
    v = score_all(root, reg, now_ms=later)["bots"]["botA"]
    assert v["status"] == "NOT_DECIDABLE" and v["restart_orphans"] == 1 and v["pending"] == 0
    assert main(args + ["--now-ms", str(later)]) == 0 and (tmp_path / "reads" / "botA.json").exists()
    assert main(args + ["--now-ms", str(later)]) == 3  # one read per bot_id
    # a custom settle window moves the line
    v = score_all(root, reg, now_ms=soon, settle_ms=3_600_000)["bots"]["botA"]
    assert v["status"] == "NOT_DECIDABLE" and v["restart_orphans"] == 1


def test_refusals_by_cause_data_issues_count_toward_the_five_percent_and_tolerance_refusals_do_not(tmp_path):
    base = positions(good_pnls())
    other = [refused_row(i, i % 3, reason="stale_decision") for i in range(8)]  # 8 of 108 attempts, 7.4%, but not a data issue
    v = read(tmp_path, base + other)["bots"]["botA"]
    assert v["status"] == "PASS" and v["refused_other"] == {"stale_decision": 8} and v["refused_data_issues"] == 0 and v["books"]["0.25"]["excluded_share"] == 0.0
    sub = tmp_path / "b"
    sub.mkdir()
    data = [refused_row(i, i % 3, reason="clock_not_ready") for i in range(6)]
    v = read(sub, base + data)["bots"]["botA"]
    assert v["status"] == "NOT_DECIDABLE" and v["refused_data_issues"] == 6 and v["books"]["0.25"]["excluded_share"] > 0.05


def test_a_registry_line_without_the_verification_record_is_refused_outside_tests(tmp_path, monkeypatch):
    root, reg = setup(tmp_path, positions(good_pnls()))
    assert status_of(score_all(root, reg, now_ms=DAY0 + 30 * DAY)) == "PASS"  # test mode: a test_override line is accepted
    monkeypatch.delenv(TEST_ENV)
    res = score_all(root, reg, now_ms=DAY0 + 30 * DAY)
    assert "verification record" in res["bots_refused"]["botA"] and "botA" not in res["bots"]


def test_the_read_carries_informational_file_hashes_and_counts_attempts_priced_trades_and_exclusions(tmp_path):
    v = read(tmp_path, positions(good_pnls()))["bots"]["botA"]
    assert len(v["ledger_files"]) == 5 and all(set(x) == {"ledger_sha256", "decision_log_sha256", "lines", "counts"} for x in v["ledger_files"].values())
    assert "informational" in v["ledger_files_note"]
    # the boundary counts attempts, the gate counts priced trades after exclusions: 100 attempts and one exclusion is n = 99, a FAIL on n
    rows = [dict(r, kind="excluded", exclusion_reason="feed_gap", legs=None, calc=None) if r["kind"] == "trade" and r["position_id"] == "p0003" else r for r in positions(good_pnls())]
    sub = tmp_path / "b"
    sub.mkdir()
    w = read(sub, rows)["bots"]["botA"]
    assert w["attempts"] == 100 and w["books"]["0.25"]["n_trades"] == 99 and w["status"] == "FAIL"
    assert any("n 99" in m for ms in w["deciding"]["misses"].values() for m in ms)


def test_a_book_failing_only_on_n_prints_fail_n_lt_100(tmp_path):
    rows = [dict(r, kind="excluded", exclusion_reason="feed_gap", legs=None, calc=None) if r["kind"] == "trade" and r["position_id"] == "p0003" else r for r in positions(good_pnls())]
    res = read(tmp_path, rows)
    v = res["bots"]["botA"]
    assert v["status"] == "FAIL" and v["fail_label"] == "FAIL (n < 100)" and "STATUS FAIL (n < 100)" in render(res)
    sub = tmp_path / "b"
    sub.mkdir()
    res = read(sub, positions(np.full(100, -3e6)))  # a real loss: a plain FAIL, no label
    assert res["bots"]["botA"]["status"] == "FAIL" and "fail_label" not in res["bots"]["botA"] and "STATUS FAIL (n" not in render(res)


def test_open_rows_must_carry_the_registered_head_and_a_clean_tree(tmp_path):
    base = positions(good_pnls())
    for name, patch in (("wrong_head", {"code_head": "deadbee"}), ("dirty", {"code_dirty": True}), ("unknown_head", {"code_head": None, "code_dirty": None}),
                        ("no_fields", None)):
        sub = tmp_path / name
        sub.mkdir()
        rows = []
        for r in base:
            if r["kind"] == "open" and r["position_id"] == "p0042":
                r = {k: v for k, v in r.items() if k not in ("code_head", "code_dirty")} if patch is None else dict(r, **patch)
            rows.append(r)
        res = read(sub, rows)
        assert "bound to the process" in res["bots_refused"]["botA"] and "botA" not in res["bots"], name
    sub = tmp_path / "before_clock"
    sub.mkdir()  # a bad row decided before the clock is not counted, so it does not refuse the book
    rows = base + [dict(open_row("early", 0, -3), code_head="deadbee", code_dirty=True)]
    assert status_of(read(sub, rows)) == "PASS"


def test_the_scorer_warns_when_it_reads_a_test_override_registry_line(tmp_path):
    res = read(tmp_path, positions(good_pnls()))
    v = res["bots"]["botA"]
    assert len(v["warnings"]) == 1 and "test_override" in v["warnings"][0]
    assert "WARNING: the registry line is a test_override" in render(res)


def test_the_engine_start_record_must_match_the_registered_sha_and_be_clean(tmp_path):
    base = positions(good_pnls())
    assert status_of(read(tmp_path, base)) == "PASS"
    cases = {"wrong_head": [start_row(head="deadbee")] , "dirty": [start_row(dirty=True)], "missing": [], "other_engine": [start_row(engine_id="eng2")]}
    for name, starts in cases.items():
        sub = tmp_path / name
        sub.mkdir()
        rows = [r for r in base if r["kind"] != "start"] + starts
        res = read(sub, rows)
        assert "engine start record" in res["bots_refused"]["botA"] and "botA" not in res["bots"], name
    # an engine that was restarted: each open row points at its own engine's start record; a bad start of an engine with no counted rows is harmless
    sub = tmp_path / "restart"
    sub.mkdir()
    rows = [dict(r, engine_id="eng2") if r["kind"] == "open" and int(r["position_id"][1:]) >= 50 else r for r in base] + [start_row(engine_id="eng2"), start_row(engine_id="eng3", dirty=True)]
    assert status_of(read(sub, rows)) == "PASS"


def test_a_forced_boundary_scores_comparators_over_the_same_attempts_and_never_a_later_one(tmp_path):
    rows = positions(good_pnls())
    natural_b = DAY0 + 5 * DAY
    root, reg = setup(tmp_path, rows)
    now = DAY0 + 30 * DAY
    default = score_all(root, reg, now_ms=now)["bots"]["botA"]
    assert default["boundary_source"] == "natural" and default["read_boundary_ms"] == default["natural_boundary_ms"] == natural_b
    assert score_all(root, reg, now_ms=now, boundary_ms=None)["bots"]["botA"] == default  # default behaviour is unchanged
    # equal to the natural instant: allowed, same read, labelled forced
    same = score_all(root, reg, now_ms=now, boundary_ms=natural_b)["bots"]["botA"]
    assert same["boundary_source"] == "forced" and same["natural_boundary_ms"] == natural_b
    assert {k: v for k, v in same.items() if k != "boundary_source"} == {k: v for k, v in default.items() if k != "boundary_source"}
    # earlier: the attempts decided before it only (days 0-3 = 80 attempts), so n < 100
    early = score_all(root, reg, now_ms=now, boundary_ms=DAY0 + 4 * DAY)["bots"]["botA"]
    assert early["boundary_source"] == "forced" and early["read_boundary_ms"] == DAY0 + 4 * DAY and early["natural_boundary_ms"] == natural_b
    assert early["attempts"] == 80 and early["status"] == "FAIL" and any("n 80 < 100" in m for ms in early["deciding"]["misses"].values() for m in ms)
    # later than the bot's own outcome-blind instant: refused, by score_bot and by score_all
    from tools.arena_paper import load_registry

    with pytest.raises(Refused, match="later than the bot's own outcome-blind instant"):
        score_bot("botA", load_registry(reg), Ledger(root).read_bot("botA"), now, None, 24 * 3_600_000, natural_b + DAY)
    assert "later than" in score_all(root, reg, now_ms=now, boundary_ms=natural_b + DAY)["bots_refused"]["botA"]
    # not a 00:00Z instant: refused
    assert "00:00Z" in score_all(root, reg, now_ms=now, boundary_ms=natural_b - 3_600_000)["bots_refused"]["botA"]
    # a bot with no outcome-blind instant yet cannot be forced
    sub = tmp_path / "b"
    sub.mkdir()
    root2, reg2 = setup(sub, positions(good_pnls(60)))
    assert "no outcome-blind instant yet" in score_all(root2, reg2, now_ms=now, boundary_ms=natural_b)["bots_refused"]["botA"]


def test_a_forced_read_is_a_comparator_score_and_cannot_be_recorded(tmp_path, capsys):
    root, reg = setup(tmp_path, positions(good_pnls()))
    now = DAY0 + 30 * DAY
    v = score_all(root, reg, now_ms=now, boundary_ms=DAY0 + 5 * DAY)["bots"]["botA"]
    with pytest.raises(Refused, match="comparator score"):
        record_read(tmp_path / "reads", v)
    args = ["--ledger-root", root, "--registry", reg, "--bot", "botA", "--now-ms", str(now), "--reads-dir", str(tmp_path / "reads")]
    assert main(args + ["--boundary-ms", str(DAY0 + 4 * DAY)]) == 0
    assert "(forced; the bot's own is" in capsys.readouterr().out
    assert main(args + ["--boundary-ms", str(DAY0 + 5 * DAY), "--record"]) == 3 and not (tmp_path / "reads").exists()
    assert main(args + ["--boundary-ms", str(DAY0 + 9 * DAY)]) == 0  # refused bots are reported, not a crash
    assert "later than" in capsys.readouterr().out
    assert main(args + ["--record"]) == 0 and (tmp_path / "reads" / "botA.json").exists()  # the natural read still records


# ------------------------------------------------------------------------------------------------------------------------------ incidents and damaged files (quant-proof ruling on #716)
def day_file(root, bot="botA", day=0):
    return Ledger(root).path_for(bot, utc_day(DAY0 + day * DAY))


def as_bot(rows, bot):
    return [dict(r, bot_id=bot) for r in rows]


def two_bots(tmp_path, seed_b=5):
    """botA and botB, each with a clean 100-attempt book that passes; returns (root, reg)."""
    root, reg = setup(tmp_path, positions(good_pnls()))
    register_bot("botB", "abc1234", DAY0, reg, deciding_stake=0.25, model_ids=[], now_ms=0)
    led = Ledger(root)
    for r in as_bot(positions(good_pnls(seed=seed_b)), "botB"):
        led.append(r)
    return root, reg


def test_an_incident_before_the_boundary_makes_the_read_not_decidable_even_after_the_file_was_repaired(tmp_path):
    root, reg = setup(tmp_path, positions(good_pnls()))
    now = DAY0 + 30 * DAY
    assert score_all(root, reg, now_ms=now)["bots"]["botA"]["status"] == "PASS"
    # the real flow: a torn tail, an append that is refused (writes the incident), a repair
    p = day_file(root)
    good = p.read_bytes()
    p.write_bytes(good + b'{"kind":"open","trade_id":"half')
    refused_at = DAY0 + 2 * DAY
    with pytest.raises(LedgerCorrupt):
        Ledger(root, now_fn=lambda: refused_at).append(open_row("lost1", 999, 0))
    p.write_bytes(good)  # repaired: the day file is clean again
    assert Ledger(root).scan_bot("botA")[1] == []
    v = score_all(root, reg, now_ms=now)["bots"]["botA"]
    assert v["status"] == "NOT_DECIDABLE" and v["incidents"]["n"] == 1 and v["incidents"]["before_boundary"] == 1 and v["incidents"]["kinds"] == {"torn_tail": 1}
    assert v["ledger_problems"] == [] and any("ledger incident" in r and "repaired" in r for r in v["reasons"])
    txt = render(score_all(root, reg, now_ms=now))
    assert "STATUS NOT_DECIDABLE" in txt and "INCIDENTS: 1 on record" in txt
    # it is a final read like any NOT_DECIDABLE: recordable at the boundary
    assert json.loads(record_read(tmp_path / "reads", v).read_text())["status"] == "NOT_DECIDABLE"


def test_only_incidents_before_the_boundary_count_and_a_lost_attempt_counts_by_its_decision_instant(tmp_path):
    boundary = DAY0 + 5 * DAY
    now = DAY0 + 30 * DAY
    for name, line, want in (("after", dict(at_ms=boundary + 1, row_decision_ms=boundary + 1), "PASS"),
                             ("at_the_boundary", dict(at_ms=boundary, decision_ms=boundary), "PASS"),
                             ("recorded_late_lost_early", dict(at_ms=boundary + DAY, row_decision_ms=DAY0 + 2 * DAY), "NOT_DECIDABLE"),
                             ("lost_buy", dict(at_ms=DAY0 + 4 * DAY, decision_ms=DAY0 + 4 * DAY, pool="P1"), "NOT_DECIDABLE"),
                             ("recorded_early", dict(at_ms=DAY0 + 1), "NOT_DECIDABLE")):
        sub = tmp_path / name
        sub.mkdir()
        root, reg = setup(sub, positions(good_pnls()))
        kind = "buy_refused" if name == "lost_buy" else "torn_tail"
        at = line.pop("at_ms")
        Ledger(root, now_fn=lambda: at).incident("botA", kind, "test", **line)
        v = score_all(root, reg, now_ms=now)["bots"]["botA"]
        assert v["status"] == want, name
        assert v["incidents"]["n"] == 1 and v["incidents"]["before_boundary"] == (0 if want == "PASS" else 1), name
        assert v["incidents"]["lost_buys"] == (1 if name == "lost_buy" else 0), name


def test_a_not_ready_book_discloses_its_incidents_and_the_read_at_the_boundary_will_be_not_decidable(tmp_path):
    root, reg = setup(tmp_path, positions(good_pnls(50)))
    Ledger(root, now_fn=lambda: DAY0 + DAY).incident("botA", "torn_tail", "test", row_decision_ms=DAY0 + DAY)
    res = score_all(root, reg, now_ms=DAY0 + 30 * DAY)
    v = res["bots"]["botA"]
    assert v["status"] == "NOT_READY" and v["incidents"]["n"] == 1 and v["incidents"]["before_boundary"] is None
    assert any("NOT_DECIDABLE" in r for r in v["reasons"]) and "INCIDENTS: 1 on record" in render(res)
    with pytest.raises(Refused, match="not ready"):
        record_read(tmp_path / "reads", v)


DAMAGE = {
    "torn_tail": lambda p: p.write_bytes(p.read_bytes() + b'{"kind":"open","trade_id":"hal'),
    "unparseable_line": lambda p: p.write_bytes(p.read_bytes() + b"not json at all\n"),
    "unterminated_but_valid_json_tail": lambda p: p.write_bytes(p.read_bytes() + b'{"kind":"refused","trade_id":"x","decision_ms":{"entry":1}}'),
    "not_a_record": lambda p: p.write_bytes(p.read_bytes() + b"[1,2,3]\n"),
    "record_without_decision_ms": lambda p: p.write_bytes(p.read_bytes() + b'{"kind":"trade","trade_id":"y"}\n'),
    "binary_garbage": lambda p: p.write_bytes(p.read_bytes() + b"\xff\xfe\x00\n"),
}


@pytest.mark.parametrize("damage", sorted(DAMAGE))
def test_a_torn_or_unparseable_line_makes_that_bot_not_decidable_and_every_other_bot_is_still_scored(tmp_path, damage, capsys):
    root, reg = two_bots(tmp_path)
    now = DAY0 + 30 * DAY
    assert {b: v["status"] for b, v in score_all(root, reg, now_ms=now)["bots"].items()} == {"botA": "PASS", "botB": "PASS"}
    DAMAGE[damage](day_file(root, "botA", 2))  # one damaged line in botA's files
    res = score_all(root, reg, now_ms=now)  # no crash
    a, b = res["bots"]["botA"], res["bots"]["botB"]
    assert a["status"] == "NOT_DECIDABLE" and a["ledger_problems"] and "fail_label" not in a
    assert any("torn or unparseable ledger line" in r for r in a["reasons"]) and "trades-" in a["ledger_problems"][0]
    assert b["status"] == "PASS" and b["ledger_problems"] == [] and "botB" not in res["bots_refused"]  # the other bot is scored as before
    txt = render(res)
    assert "BOT botA  STATUS NOT_DECIDABLE" in txt and "LEDGER PROBLEM: trades-" in txt and "BOT botB  STATUS PASS" in txt
    assert main(["--ledger-root", root, "--registry", reg, "--now-ms", str(now)]) == 0  # the CLI prints both and does not crash
    assert "LEDGER PROBLEM" in capsys.readouterr().out
    # the damaged bot's NOT_DECIDABLE is recordable at the boundary (final, like any NOT_DECIDABLE); the clean bot's read is not affected
    assert json.loads(record_read(tmp_path / "reads", a).read_text())["status"] == "NOT_DECIDABLE"


def test_a_damaged_file_before_the_boundary_prints_not_decidable_but_nothing_is_recorded(tmp_path):
    root, reg = setup(tmp_path, positions(good_pnls(50)))
    DAMAGE["torn_tail"](day_file(root))
    res = score_all(root, reg, now_ms=DAY0 + 30 * DAY)  # NOT_READY has no 'attempts' / 'books': the renderer and the digest must cope
    v = res["bots"]["botA"]
    assert v["status"] == "NOT_DECIDABLE" and v["ledger_problems"] and "LEDGER PROBLEM" in render(res)
    with pytest.raises(Refused, match="boundary is not reached"):
        record_read(tmp_path / "reads", v)


def test_a_torn_incident_line_is_a_ledger_problem_too(tmp_path):
    root, reg = two_bots(tmp_path)
    inc = Ledger(root).incidents_path("botA")
    inc.write_bytes(b'{"kind":"torn_tail","at_ms":1,"bot_id":"botA","day_fi')  # a crash while the incident was written: an incident may have been lost
    res = score_all(root, reg, now_ms=DAY0 + 30 * DAY)
    assert res["bots"]["botA"]["status"] == "NOT_DECIDABLE" and "incidents.jsonl" in res["bots"]["botA"]["ledger_problems"][0] and res["bots"]["botB"]["status"] == "PASS"


def test_the_ledger_scan_reads_under_a_shared_lock_so_a_write_in_flight_is_never_seen_half_done(tmp_path):
    import fcntl

    root, reg = setup(tmp_path, positions(good_pnls()))
    p = day_file(root)
    fd = os.open(str(p), os.O_RDWR | os.O_APPEND)
    fcntl.flock(fd, fcntl.LOCK_EX)  # an appender holds the exclusive lock from write to fsync
    try:
        import threading

        done = []
        t = threading.Thread(target=lambda: done.append(Ledger(root).scan_bot("botA")))
        t.start()
        t.join(0.3)
        assert t.is_alive() and not done  # the scan waits for the writer
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    t.join(5)
    assert done and done[0][1] == []
