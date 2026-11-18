"""floor_decay_monitor tests: synthetic ledgers only (no real ledger, no host).

  /data/mal/venv/bin/python -m pytest tools/test_floor_decay_monitor.py -q
"""

from __future__ import annotations

import json

from tools import floor_decay_monitor as M
from tools import lane_daily_report as R

BOT = "floor-lane-1"
S10, S25 = 100_000_000, 250_000_000


class Fx:
    """Closed trades with an exact realized r: intent, ok fill (cash out stake + 1000), exit (cash in stake + 1000 + r)."""

    def __init__(self, tmp):
        self.p = tmp / "lane-ledger.jsonl"
        self.rows, self.t, self.i = [], 1_000_000, 0

    def row(self, ev, mint, **kw):
        self.t += 1000
        self.rows.append({"schema": "x", "bot_id": BOT, "row_id": f"r{len(self.rows)}", "ev": ev, "ts_ms": self.t, "mint": mint,
                          "sol_in_lamports": 0, "sol_out_lamports": 0, **kw})

    def start(self, stake):
        self.row("start", "", stake_lamports=stake)
        return self

    def trade(self, r, stake=S25, intent=True):
        self.i += 1
        m = f"M{self.i}"
        if intent:
            self.row("send_intent", m, stake_lamports=stake)
        self.row("fill", m, ok=True, sol_out_lamports=stake + 1000)
        self.row("exit", m, sol_in_lamports=stake + 1000 + r)
        return self

    def many(self, rs, stake=S25):
        for r in rs:
            self.trade(r, stake)
        return self

    def open(self, stake):
        """open a position (intent + ok fill) and return its mint; close it later with close(). Used for carried-over positions."""
        self.i += 1
        m = f"M{self.i}"
        self.row("send_intent", m, stake_lamports=stake)
        self.row("fill", m, ok=True, sol_out_lamports=stake + 1000)
        return m, stake

    def close(self, pos, r):
        m, stake = pos
        self.row("exit", m, sol_in_lamports=stake + 1000 + r)
        return self

    def write(self):
        self.p.write_text("".join(json.dumps(x) + "\n" for x in self.rows))
        return self.p


def run(fx, **kw):
    return M.run(fx.write(), **kw)


def sub(tmp, name):
    d = tmp / name
    d.mkdir()
    return d


def test_total_equals_lane_report_and_per_trade_r(tmp_path):
    fx = Fx(tmp_path).many([5_000_000, -7_000_000, 1_234_567])
    fx.row("fee", "ZZ", sol_out_lamports=5000)  # unmatched fee: realized, not a trade
    res = run(fx)
    assert res["realized_lifetime_lamports"] == 5_000_000 - 7_000_000 + 1_234_567 - 5000
    day = R.time.strftime("%Y-%m-%d", R.time.gmtime(fx.t / 1000))
    assert res["realized_lifetime_lamports"] == R.build(fx.p, day)["bots"][BOT]["lifetime_realized_lamports"]
    assert res["n_closed_lane"] == 3
    assert [e["r"] for e in M.closed_events(fx.rows)] == [5_000_000, -7_000_000, 1_234_567, -5000]


def test_failed_landing_and_writeoff_are_trades_at_real_cost(tmp_path):
    fx = Fx(tmp_path)
    fx.row("send_intent", "A", stake_lamports=S25)
    fx.row("fill", "A", ok=False, sol_out_lamports=180_000)
    fx.row("send_intent", "B", stake_lamports=S25)
    fx.row("writeoff", "B", reason="buy_expired")
    res = run(fx)
    assert res["n_closed_lane"] == 2 and res["realized_lifetime_lamports"] == -180_000


def test_fewer_than_20_not_armed_exit_0(tmp_path, capsys):
    fx = Fx(tmp_path).many([-S25 // 10] * 5)  # -0.125 SOL: under the drawdown lines; X1/Y1 must not apply at n < 20
    res = run(fx)
    assert not res["armed"] and res["exit_code"] == 0 and not any(res["flags"].values())
    assert M.main(["--ledger-file", str(fx.p)]) == 0
    assert "n < 20: monitor not armed" in capsys.readouterr().out


def test_not_armed_but_drawdown_rules_still_trip(tmp_path):
    # X2 at exactly 3 s = 0.75 SOL (stake 0.25), n < 20 -> step-down, code 10; one lamport less -> none
    r = run(Fx(tmp_path).many([-250_000_000] * 3))
    assert r["flags"]["X2"] and not r["flags"]["Y2"] and r["exit_code"] == 10
    assert not run(Fx(sub(tmp_path, "b")).many([-250_000_000, -250_000_000, -249_999_999]))["flags"]["X2"]
    # Y2 at exactly 1.50 SOL lifetime drawdown (stake 0.10, so X2 fires too) -> STOP, code 20; one lamport less -> no Y2
    r = run(Fx(sub(tmp_path, "c")).many([-750_000_000, -750_000_000], stake=S10))
    assert r["flags"]["Y2"] and r["exit_code"] == 20
    assert not run(Fx(sub(tmp_path, "d")).many([-750_000_000, -749_999_999], stake=S10))["flags"]["Y2"]


def test_x1_boundary_exactly_minus_0_05_s(tmp_path):
    r = run(Fx(tmp_path).many([0] * 19 + [-S25]))
    assert r["armed"] and r["flags"]["X1"] and not r["flags"]["Y1"] and not r["flags"]["X2"] and r["exit_code"] == 10
    assert r["last20_mean_over_stake"] == -0.05
    r = run(Fx(sub(tmp_path, "b")).many([0] * 19 + [-S25 + 1]))
    assert not r["flags"]["X1"] and r["exit_code"] == 0 and r["last20_mean_over_stake"] > -0.05


def test_y1_boundary_exactly_minus_0_15_s(tmp_path):
    # stake 0.10: a -0.30 SOL window stays under the 1.50 SOL Y2 line, and 3 s = 0.30 SOL so X2 fires alongside
    r = run(Fx(tmp_path).many([0] * 19 + [-3 * S10], stake=S10))
    assert r["flags"]["Y1"] and r["exit_code"] == 20 and not r["flags"]["Y2"]
    r = run(Fx(sub(tmp_path, "b")).many([0] * 19 + [-3 * S10 + 1], stake=S10))
    assert not r["flags"]["Y1"] and r["flags"]["X1"] and r["x_at_0_10_is_stop"] and r["exit_code"] == 20  # X at 0.10 is STOP (A4 row 3)


def test_window_is_last_20_only(tmp_path):
    r = run(Fx(tmp_path).many([-S25] + [0] * 20))  # the old loss has left the window
    assert r["armed"] and not r["flags"]["X1"] and r["last20_sum_lamports"] == 0


def test_hwm_drawdown_x2_boundary_after_a_gain(tmp_path):
    r = run(Fx(tmp_path).many([1_000_000_000, -750_000_000]))
    assert r["population_hwm_lamports"] == 1_000_000_000 and r["population_drawdown_lamports"] == 750_000_000
    assert r["flags"]["X2"] and not r["flags"]["Y2"]
    assert not run(Fx(sub(tmp_path, "b")).many([1_000_000_000, -749_999_999]))["flags"]["X2"]


def test_mixed_stakes_population_is_the_current_stake_run(tmp_path):
    # 25 trades at 0.10 (-0.02 SOL each) then 3 at 0.25: s = 0.25 (most recent trade), population = the 3, not armed;
    # X2 sees only the 0.25 trades; Y2 is lane-wide and not reached (0.50 SOL drawdown)
    r = run(Fx(tmp_path).many([-20_000_000] * 25, stake=S10).many([1, 2, 3], stake=S25))
    assert r["stake_lamports"] == S25 and r["n_closed_population"] == 3 and r["n_closed_lane"] == 28 and not r["armed"]
    assert r["realized_population_lamports"] == 6 and r["population_drawdown_lamports"] == 0
    assert r["realized_lifetime_lamports"] == -500_000_000 + 6 and r["lifetime_drawdown_lamports"] == 499_999_994
    assert not r["flags"]["X2"] and not r["flags"]["Y2"]


def test_missing_intent_inherits_previous_stake_or_flag(tmp_path):
    assert run(Fx(tmp_path).trade(10, S25).trade(10, intent=False))["stake_lamports"] == S25
    assert run(Fx(sub(tmp_path, "b")).trade(10, S25, intent=False), stake_default=S10)["stake_lamports"] == S10


def test_json_cli_and_error_exit_and_read_only(tmp_path, capsys):
    p = Fx(tmp_path).many([1, 2]).write()
    before = sorted(x.name for x in tmp_path.iterdir())
    assert M.main(["--ledger-file", str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["n_closed_lane"] == 2
    assert sorted(x.name for x in tmp_path.iterdir()) == before
    assert M.main(["--ledger-file", str(tmp_path / "nope.jsonl")]) == 2


def test_qp_case_a_carried_over_010_close_does_not_empty_the_window(tmp_path):
    # 25 losing 0.25 closes (each -0.06 s), a carried-over 0.10 position opened before, one 0.10 close after; start row says 0.25
    fx = Fx(tmp_path).start(S25)
    old = fx.open(S10)
    fx.many([-S25 * 6 // 100] * 25, stake=S25).close(old, -1)
    r = run(fx)
    assert r["stake_lamports"] == S25 and r["n_closed_population"] == 25 and r["n_closed_other_stake_or_before_start"] == 1
    assert r["flags"]["X1"] and r["exit_code"] == 10 and "X1" in r["fired_ms"]


def test_qp_case_b_after_step_down_22_losing_010_then_one_025_close(tmp_path):
    # stake 0.25 start, a 0.25 position carried over, step-down start at 0.10, 22 trades at -0.06 s, then the 0.25 close
    fx = Fx(tmp_path).start(S25)
    old = fx.open(S25)
    fx.start(S10).many([-S10 * 6 // 100] * 22, stake=S10).close(old, 5)
    r = run(fx)
    assert r["stake_lamports"] == S10 and r["n_closed_population"] == 22 and r["n_closed_other_stake_or_before_start"] == 1
    assert r["flags"]["X1"] and r["x_at_0_10_is_stop"] and r["exit_code"] == 20  # ruling A4 row 3
    assert "STOP (X at 0.10" in M.render(r, BOT)


def test_reviewer_case_old_stake_close_does_not_reset_hwm_x2_still_fires(tmp_path):
    fx = Fx(tmp_path).start(S25)
    old = fx.open(S10)
    fx.many([1_000_000_000, -750_000_000], stake=S25).close(old, 900_000_000)  # an old-stake gain must not enter the 0.25 population HWM
    r = run(fx)
    assert r["population_hwm_lamports"] == 1_000_000_000 and r["population_drawdown_lamports"] == 750_000_000 and r["flags"]["X2"]
    assert r["realized_lifetime_lamports"] == 1_000_000_000 - 750_000_000 + 900_000_000  # the old-stake close counts toward lifetime only


def test_restart_at_same_stake_continues_population_and_start_row_changes_it(tmp_path):
    fx = Fx(tmp_path).start(S25).many([1, 1], stake=S25).start(S25).many([1], stake=S25)
    assert run(fx)["n_closed_population"] == 3
    (tmp_path / "b").mkdir()
    fx = Fx(tmp_path / "b").start(S25).many([1, 1], stake=S25).start(S10).many([1], stake=S10)
    r = run(fx)
    assert r["n_closed_population"] == 1 and r["stake_lamports"] == S10


def test_no_stake_known_is_exit_2(tmp_path, capsys):
    p = tmp_path / "lane-ledger.jsonl"
    p.write_text("")
    assert M.main(["--ledger-file", str(p), "--json"]) == 2
    assert M.main(["--ledger-file", str(p)]) == 2
    assert "no stake known" in capsys.readouterr().err


def test_a5_fields_are_printed(tmp_path):
    fx = Fx(tmp_path).start(S25).many([0] * 19 + [-S25], stake=S25)
    fx.open(S25)
    out = M.render(run(fx), BOT)
    for need in ("median r", "take-profit exits in the last 20: n/a", "open positions: 1", "margin to threshold", "FIRED", "population start", "live vs book A: not printed (no matched list in the lane ledger)"):
        assert need in out


# --- --since-ms: per-close evaluation (ruling A4) ---

def _close_ts(fx, n):
    """ts of the n-th (1-based) exit row."""
    return [r["ts_ms"] for r in fx.rows if r["ev"] == "exit"][n - 1]


def test_since_ms_step_down_at_a_past_close_recovered_by_latest(tmp_path, capsys):
    fx = Fx(tmp_path).start(S25).many([-13_000_000] * 20).many([20_000_000] * 20)
    p = fx.write()
    assert M.main(["--ledger-file", str(p)]) == 0
    assert "fired_at_close" not in capsys.readouterr().out
    assert M.main(["--ledger-file", str(p), "--since-ms", "0"]) == 10
    out = capsys.readouterr().out
    assert f"fired_at_close ts={_close_ts(fx, 20)} flags=X1" in out
    assert len([ln for ln in out.splitlines() if ln.startswith("fired_at_close ts=")]) == 1
    # a since after that close does not see it
    assert M.main(["--ledger-file", str(p), "--since-ms", str(_close_ts(fx, 21))]) == 0


def test_since_ms_stop_fires_mid_window_exit_20(tmp_path, capsys):
    fx = Fx(tmp_path).start(S25).many([-40_000_000] * 20).many([100_000_000] * 20)
    p = fx.write()
    assert M.main(["--ledger-file", str(p)]) == 0
    capsys.readouterr()
    assert M.main(["--ledger-file", str(p), "--since-ms", "0"]) == 20
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("fired_at_close ts=")]
    assert any("Y1" in ln and f"ts={_close_ts(fx, 20)} " in ln for ln in lines)
    assert any(f"ts={_close_ts(fx, 19)} flags=X2 exit=10" in ln for ln in lines)


def test_since_ms_x_at_010_past_close_is_stop(tmp_path, capsys):
    fx = Fx(tmp_path).start(S10).many([-6_000_000] * 20, stake=S10).many([10_000_000] * 20, stake=S10)
    p = fx.write()
    assert M.main(["--ledger-file", str(p)]) == 0
    capsys.readouterr()
    assert M.main(["--ledger-file", str(p), "--since-ms", "0"]) == 20
    assert "flags=X1 exit=20" in capsys.readouterr().out


def test_since_ms_no_closes_since_equals_latest(tmp_path, capsys):
    ok = Fx(tmp_path).start(S25).many([1_000_000] * 5)
    p = ok.write()
    assert M.main(["--ledger-file", str(p), "--since-ms", str(ok.t + 1)]) == 0
    capsys.readouterr()
    bad = Fx(sub(tmp_path, "b")).start(S25).many([-13_000_000] * 20)
    p = bad.write()
    base = M.main(["--ledger-file", str(p)])
    capsys.readouterr()
    assert base == 10 and M.main(["--ledger-file", str(p), "--since-ms", str(bad.t + 1)]) == base
    assert "closes with a flag 0" in capsys.readouterr().out


def test_since_ms_json_and_window_start_inclusive(tmp_path, capsys):
    fx = Fx(tmp_path).start(S25).many([-13_000_000] * 20).many([20_000_000] * 20)
    p = fx.write()
    capsys.readouterr()
    assert M.main(["--ledger-file", str(p), "--since-ms", str(_close_ts(fx, 20)), "--json"]) == 10
    d = json.loads(capsys.readouterr().out)
    assert d["exit_code_latest"] == 0 and d["exit_code"] == 10 and d["fired_at_close"][0]["ts_ms"] == _close_ts(fx, 20)


DEFAULT_MD5 = "f225b57f1bf2a077325ac734c5198d63"  # recorded on main before --since-ms was added


def test_default_output_md5_unchanged(tmp_path, capsys):
    import hashlib
    fx = Fx(tmp_path).start(S25).many([-30_000_000] * 8 + [5_000_000] * 14 + [-40_000_000] * 3)
    p = fx.write()
    out = []
    for extra in ([], ["--json"]):
        rc = M.main(["--ledger-file", str(p)] + extra)
        out.append((rc, capsys.readouterr().out))
    assert hashlib.md5(repr(out).encode()).hexdigest() == DEFAULT_MD5


def test_since_ms_ignores_start_rows_after_the_close(tmp_path, capsys):
    """Stake-change seam: a 0.25 step-down at a past close, then a LATER 0.10 start row. Per-close evaluation must cut start rows at the close (exit 10);
    with all starts the past 0.25 close would be judged at 0.10 and be missed (exit 0)."""
    fx = Fx(tmp_path).start(S25).many([-13_000_000] * 20).start(S10).many([1_000_000] * 3, stake=S10)
    p = fx.write()
    assert M.main(["--ledger-file", str(p)]) == 0
    capsys.readouterr()
    assert M.main(["--ledger-file", str(p), "--since-ms", "0"]) == 10
    assert f"fired_at_close ts={_close_ts(fx, 20)} flags=X1 exit=10" in capsys.readouterr().out
