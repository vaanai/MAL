"""arena_score: bootstrap determinism, gate edge cases, clock and sha refusals. Synthetic ledgers only."""

from __future__ import annotations

import json

import numpy as np
import pytest

from tools.arena_paper import Ledger, StackConfig, canon, register_bot
from tools.arena_score import Refused, cell_stats, cell_verdict, main, render, score_all, score_book
from tools.r4b_book import boot_lo, fail_legs

DAY0 = 1_789_000_000_000 - (1_789_000_000_000 % 86_400_000)  # a UTC midnight
SEND = 505_000


def mk(i, day, pnl, bot="botA", sha="abc1234", stake=0.25, ssb=0.0, near=0.0, ms=None, tid=None):
    ms = DAY0 + day * 86_400_000 + 3_600_000 + i * 1000 if ms is None else ms
    fl = {}
    calc = {}
    for lat in ("1p3", "1p9"):
        flat, press, _, _ = fail_legs(np.array([pnl]), np.array([True]), np.array([ssb]), np.array([near]), SEND)
        fl["flat_" + lat] = {"state": "filled", "net_lamports": float(flat[0])}
        fl["press_" + lat] = {"state": "filled", "net_lamports": float(press[0])}
        calc[lat] = {"pnl_nofail": float(pnl), "filled": True, "ssb": ssb, "nearby_lamports": near}
    return {"schema": "arena_trade_v1", "trade_id": tid or f"{bot}-{day}-{i}-{stake}", "bot_id": bot, "frozen_sha": sha, "decision_ms": {"entry": ms, "exit": ms + 5000},
            "stake_sol": stake, "legs": fl, "calc": calc, "stack": {"send": SEND}}


def book(pnls, days, **kw):
    return [mk(i, d, p, **kw) for i, (p, d) in enumerate(zip(pnls, days))]


def test_bootstrap_is_deterministic_and_is_the_labs_function():
    rng = np.random.default_rng(7)
    x = rng.normal(2e6, 5e6, 300)
    a, b = cell_stats(x, ["d"] * 300)["ci90_lo_sol"], cell_stats(x, ["d"] * 300)["ci90_lo_sol"]
    assert a == b == boot_lo(x / 1e9)
    # 1,000 draws, seed 1, 5th percentile of the resampled means
    g = np.random.default_rng(1)
    bm = np.array([(x / 1e9)[g.integers(0, 300, 300)].mean() for _ in range(1000)])
    assert a == float(np.percentile(bm, 5))


def test_cell_stats_numbers():
    x = [10e6, -4e6, 3e6, 2e6, 1e6, -1e6]
    d = ["a", "a", "b", "b", "c", "c"]
    s = cell_stats(x, d)
    assert s["n"] == 6 and s["days"] == 3 and s["days_pos"] == 2
    assert s["total_sol"] == pytest.approx(0.011) and s["mean_sol"] == pytest.approx(0.011 / 6)
    assert s["ex_top3_sol"] == pytest.approx(0.011 - 0.015)  # drop 10, 3, 2 (million)
    assert s["ex_best_day_sol"] == pytest.approx(0.011 - 0.006)  # best day a = +6
    assert s["daily"]["a"] == {"n": 2, "sol": pytest.approx(0.006)}


def passing_book(n=120, days=6, seed=3):
    rng = np.random.default_rng(seed)
    pnl = rng.normal(4e6, 2e6, n)  # every trade positive-ish, no single winner
    return book(pnl, [i % days for i in range(n)])


def test_a_clean_book_passes_every_cell():
    r = score_book(passing_book())
    assert r["pass"] and all(c["pass"] for c in r["cells"].values()) and r["per_latency_pass"] == {"1p3": True, "1p9": True}
    assert r["deciding_cell"] in r["cells"] and r["deciding_misses"] == []


@pytest.mark.parametrize("n,days,why", [(99, 6, "n 99 < 100"), (120, 4, "days 4 < 5")])
def test_gate_minimums(n, days, why):
    r = score_book(passing_book(n, days))
    assert not r["pass"] and any(why in m for c in r["cells"].values() for m in c["misses"])


def test_exactly_100_trades_on_5_days_can_pass_and_a_majority_of_days_is_needed():
    assert score_book(passing_book(100, 5))["pass"]
    rng = np.random.default_rng(5)
    pnl = np.concatenate([rng.normal(9e6, 1e6, 60), rng.normal(-2e5, 1e5, 40)])  # day 0 and 1 carry all the profit
    days = [0] * 30 + [1] * 30 + [2] * 14 + [3] * 13 + [4] * 13
    r = score_book(book(pnl, days))
    c = r["cells"]["flat_1p3"]
    assert c["days"] == 5 and c["days_pos"] == 2 and not c["pass"] and any("majority" in m for m in c["misses"])
    assert c["total_sol"] > 0 and c["ci90_lo_sol"] > 0 and c["ex_top3_sol"] > 0  # only the day rule fails


def test_ci_and_top3_criteria():
    rng = np.random.default_rng(11)
    noisy = rng.normal(2e5, 5e6, 120)  # mean ~ 0, wide: CI lo < 0
    r = score_book(book(noisy, [i % 6 for i in range(120)]))
    assert not r["cells"]["flat_1p3"]["pass"] and any("CI90" in m for m in r["cells"]["flat_1p3"]["misses"])
    # three huge winners carry the book: total > 0 but ex-top-3 < 0
    pnl = np.concatenate([[3e8, 2e8, 1e8], np.full(117, -1e6)])
    s = cell_stats(pnl, [str(i % 6) for i in range(120)])
    assert s["total_sol"] > 0 and s["ex_top3_sol"] < 0 and not cell_verdict(s)["pass"]


def test_must_pass_under_both_fail_models():
    # constant pnl +200,000: flat = 0.85*pnl - 0.15*send = +94,250 > 0; pressure (p = 0.289 for all) = 0.711*pnl - 0.289*send = -3,745 < 0
    r = score_book(book(np.full(120, 200_000.0), [i % 6 for i in range(120)]))
    assert r["cells"]["flat_1p3"]["pass"] and r["cells"]["flat_1p9"]["pass"]
    assert not r["cells"]["press_1p3"]["pass"] and not r["pass"]
    assert r["deciding_cell"].startswith("press") and r["per_latency_pass"] == {"1p3": False, "1p9": False}


def test_pressure_leg_is_refit_from_ingredients_not_read_from_the_ledger():
    rows = passing_book()
    for r in rows:  # corrupt the stored provisional pressure legs: the scorer must not read them
        for lat in ("1p3", "1p9"):
            r["legs"]["press_" + lat]["net_lamports"] = -1e12
    assert score_book(rows)["pass"]


def _setup(tmp_path, rows, clock=0, sha="abc1234"):
    reg = tmp_path / "bots.json"
    register_bot("botA", sha, clock, reg)
    led = Ledger(tmp_path / "ledger")
    for r in rows:
        led.append(r)
    return tmp_path / "ledger", reg


def test_only_trades_at_or_after_the_registered_clock_count(tmp_path):
    rows = passing_book(120, 6)
    clock = DAY0 + 3 * 86_400_000  # days 0-2 are before the freeze clock
    root, reg = _setup(tmp_path, rows, clock=clock)
    res = score_all(str(root), str(reg))
    v = res["bots"]["botA"]
    assert v["rows_before_clock"] == 60 and v["books"]["0.25"]["n"] == 60
    assert not v["books"]["0.25"]["pass"]  # 60 counted trades, 3 days


def test_a_changed_frozen_sha_is_refused(tmp_path):
    rows = passing_book(110, 6) + [mk(9999, 6, 5e6, sha="def5678")]
    root, reg = _setup(tmp_path, rows)
    res = score_all(str(root), str(reg))
    assert "botA" in res["bots_refused"] and "frozen_sha" in res["bots_refused"]["botA"] and "botA" not in res["bots"]
    # all rows under a sha that differs from the registry: refused as well
    root2, reg2 = _setup(tmp_path / "x", passing_book(110, 6, seed=4), sha="1111111")
    # (ledger rows carry abc1234, the registry says 1111111)
    assert "botA" in score_all(str(root2), str(reg2))["bots_refused"]


def test_duplicate_trade_id_across_files_is_refused(tmp_path):
    a = mk(1, 0, 5e6, tid="dup")
    b = mk(2, 2, 5e6, tid="dup")  # same id, another day file
    root, reg = _setup(tmp_path, [a, b])
    assert "duplicated" in score_all(str(root), str(reg))["bots_refused"]["botA"]


def test_multiplicity_disclosure_and_cli_output(tmp_path, capsys):
    rows = passing_book(120, 6) + passing_book(120, 6, seed=9)[:0]
    root, reg = _setup(tmp_path, rows)
    register_bot("botB", "bbb2222", 0, reg)
    out = tmp_path / "out.json"
    assert main(["--ledger-root", str(root), "--registry", str(reg), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "bots in the arena: 2" in text and "deciding cell" in text and "VERDICT PASS" in text
    doc = json.loads(out.read_text())
    assert doc["arena_bots_registered"] == 2 and doc["cells_scored"] == 4
    assert "daily (flat_1p3)" in render(doc)


def test_strict_json_output_has_no_nan(tmp_path):
    root, reg = _setup(tmp_path, [])
    res = score_all(str(root), str(reg))
    canon(res)  # raises on NaN / inf
    assert res["bots"]["botA"]["books"] == {}


def test_refused_is_an_exception_type():
    assert issubclass(Refused, Exception) and StackConfig().strand_rule == "sc"
