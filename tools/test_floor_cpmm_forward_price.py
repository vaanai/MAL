"""Tests for floor_cpmm_forward_price on SYNTHETIC rows built in tmp dirs (DECLARE 11.6 (e)). No real row, smoke dir or out dir is opened."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.research import floor_cpmm_replay as RP
from tools import arena_stack as F
from tools import floor_cpmm_forward_price as P

BASE = int(datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)
STEP = 60_000
POOL = "POOLAAA"
K = 30e9 * 1e15
B0 = int(1e15)
BACK = P.CUTOFF_BACK_MS
DEC_I = 400                                     # decision sample index in the standard path
UNTIL_STD = BASE + 2000 * STEP                  # all standard rows are before it


def qpath(n=2000, q0=30e9, q_drop=25e9, noise=0.02e9, dec=DEC_I, overrides=None):
    q = []
    for i in range(n):
        if i < dec:
            q.append(q0 + (noise if i % 2 else -noise))
        else:
            q.append(q_drop)
    for i, v in (overrides or {}).items():
        if i < n:
            q[i] = v
    return q


def row(i, q, pool=POOL, sup_factor=1.0, **kw):
    b = int(K / q)
    r = {"ms": BASE + i * STEP, "slot": 1000 + i, "pool": pool, "token_mint": "TOK", "base_reserve": b, "quote_reserve": int(q), "token_supply": int(B0 * sup_factor), "token_supply_ms": 0,
         "err": None, "open_time": BASE // 1000 - 30 * 3600, "creator_fee_on": 0, "enable_creator_fee": False,
         "protocol_fees_token_0": 0, "protocol_fees_token_1": 0, "fund_fees_token_0": 0, "fund_fees_token_1": 0, "creator_fees_token_0": 0, "creator_fees_token_1": 0,
         "trade_fee_rate": 2500, "creator_fee_rate": 0, "lp_locked_frac": 1.0, "mint_authority_set": False, "freeze_authority_set": False, "token_program": "token",
         "token_2022_transfer_fee": False}
    r.update(kw)
    return r


def write_rows(out: Path, rows, wsol0=True, pool=POOL, status=None):
    by_hour = {}
    for r in rows:
        h = datetime.fromtimestamp(r["ms"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H")
        by_hour.setdefault(h, []).append(r)
    for h, rs in by_hour.items():
        with open(out / f"{h}.jsonl", "a", encoding="utf-8") as f:
            for r in rs:
                f.write(json.dumps(r) + "\n")
    pj = out / "pools.json"
    cur = json.loads(pj.read_text()) if pj.exists() else {"pools": {}}
    if pool is not None:
        cur["pools"][pool] = {"wsol_is_token0": wsol0}
    pj.write_text(json.dumps(cur))
    if status is not None:
        (out / "status.json").write_text(json.dumps(status))


def build(tmp_path, q=None, drop=(), mod=None, sup_factor=1.0, wsol0=True, status=None, name="o"):
    out = tmp_path / name
    out.mkdir()
    q = q or qpath()
    rows = []
    for i, v in enumerate(q):
        if i in drop:
            continue
        r = row(i, v, sup_factor=sup_factor)
        if mod:
            mod(i, r)
        rows.append(r)
    write_rows(out, rows, wsol0=wsol0, status=status)
    return out


def deciding(out, until=UNTIL_STD):
    rows, cnt, *_ = P.deciding_rows(out, until)
    return rows, cnt


def run(out, g7=True, until=UNTIL_STD, stake=0.25, lat=4085):
    by, st, pm, sg, status = P.prepare(out, until)
    pools = P.build_pools(by, pm, False, sg, [])
    return P.run_cell(pools, g7, stake, lat, until, until - BACK, count_events=True)


# ------------------------------------------------------------------------------------------------------------------------ pricing
def test_next_sample_pricing(tmp_path):
    q = qpath(overrides={DEC_I + 1: 25.3e9})
    out = build(tmp_path, q=q)
    rows, cnt = run(out)
    assert len(rows) == 1 and rows[0]["filled"]
    r = rows[0]
    assert r["entry_ms"] == BASE + DEC_I * STEP and r["land_ms"] == r["entry_ms"] + STEP  # priced on sample i+1, not on the deciding sample
    xs = DEC_I + 1441  # first sample at or after decision + 24 h + 1.9 s
    assert r["reason"] == "time_24h" and r["exit_ms"] == r["entry_ms"] + P.HOLD_MS
    b1, bx = float(int(K / 25.3e9)), float(int(K / q[xs]))
    spot = float(25e9) / int(K / 25e9)  # spot of the DECISION sample (guard reference)
    exp = RP.cpmm_round_trip(0.25e9, float(int(25.3e9)), b1, spot, float(q[xs]), bx, 0.0025)
    assert r["pnl"] == pytest.approx(exp["pnl"])
    assert cnt["unfilled_after_300s"] == 0


def test_guarded_buy_pays_one_send_and_is_not_filled(tmp_path):
    out = build(tmp_path, q=qpath(overrides={DEC_I + 1: 27.6e9}))
    rows, cnt = run(out)
    assert cnt["guarded"] == 1
    assert rows[0]["filled"] is False and rows[0]["pnl"] == -F.SEND_FEE_DECIDING
    assert P.count_set(rows[:1]) == 0
    rows = rows[:1]
    # the guarded send is paid in the legs K1/K4 read (guard_cost enters total_incl_guard)
    blk = P.leg_block(rows, 1.0)
    assert blk["flat"]["guard_cost_sol"] == pytest.approx(-F.SEND_FEE_DECIDING / 1e9)


def test_gap_refuses_trigger(tmp_path):
    out = build(tmp_path, drop={DEC_I - 2, DEC_I - 1})  # interval 180 s > 120 s before the deciding sample
    rows, cnt = run(out)
    assert rows == [] and cnt["refused_gap"] == 1


def test_status_gap_entry_marks_interval(tmp_path):
    a, c = BASE + (DEC_I - 1) * STEP, BASE + DEC_I * STEP
    out = build(tmp_path, status={"gaps": [[a - 1000, c]], "counters": {}})
    rows, cnt = run(out)
    assert rows == [] and cnt["refused_gap"] == 1


def test_unfilled_after_300s(tmp_path):
    out = build(tmp_path, drop=set(range(DEC_I + 1, DEC_I + 7)))  # next sample is 420 s after the decision
    rows, cnt = run(out)
    assert rows == [] and cnt["unfilled_after_300s"] == 1  # no send paid, not a row, not counted
    assert P.count_set(rows) == 0


def test_landing_inside_gap_under_300s_is_marked(tmp_path):
    out = build(tmp_path, drop={DEC_I + 1, DEC_I + 2})  # lands 180 s later
    rows, cnt = run(out)
    assert len(rows) == 1 and rows[0]["filled"] and rows[0]["landed_after_gap"] and cnt["landed_after_gap"] == 1


def test_stranded_when_exit_has_no_sample(tmp_path):
    n = DEC_I + 1441  # last row index 1840; the time exit needs index 1841
    out = build(tmp_path, q=qpath(n=n))
    until = BASE + (DEC_I + 1446) * STEP
    rows, cnt = run(out, until=until)
    assert len(rows) == 1 and rows[0]["stranded"] and rows[0]["stranded_item6"] and rows[0]["filled"]
    assert rows[0]["pnl"] == -0.25e9 - F.SEND_FEE_DECIDING - 2_039_280  # headline: stranded
    assert rows[0]["pnl_last_good"] is not None and rows[0]["pnl_last_good"] != rows[0]["pnl"]  # last-good-state variant beside it
    assert cnt["stranded_no_exit_sample"] == 1 and P.count_set(rows) == 1
    rep = P.cell_report(rows, 1.0)
    assert rep["stranded_item6"] == 1 and "last_good_state_variant" in rep


def test_take_profit_priced_one_sample_after_decision(tmp_path):
    q = qpath(overrides={i: 25e9 * 1.4 for i in range(DEC_I + 5, 2000)})  # price x ~1.96 >= 1.5 x mark
    out = build(tmp_path, q=q)
    rows, _ = run(out)
    assert rows[0]["reason"] == "tp_1.5x" and rows[0]["exit_ms"] == BASE + (DEC_I + 5) * STEP
    assert rows[0]["pnl"] > 0


def test_exit_delayed_by_gap_counted(tmp_path):
    out = build(tmp_path, drop=set(range(DEC_I + 1435, DEC_I + 1450)))  # the time exit falls in a gap
    rows, cnt = run(out)
    assert len(rows) == 1 and cnt["exits_delayed_by_gap"] == 1 and cnt["longest_exit_delay_s"] > 120


# ------------------------------------------------------------------------------------------------------------------------ vetoes
VETOES = [
    ("lp_locked_lt_0.95", {"lp_locked_frac": 0.9}),
    ("lp_locked_lt_0.95", {"lp_locked_frac": None}),
    ("mint_authority_set", {"mint_authority_set": True}),
    ("mint_authority_set", {"mint_authority_set": None}),
    ("freeze_authority_set", {"freeze_authority_set": True}),
    ("freeze_authority_set", {"freeze_authority_set": None}),
    ("token_program_null", {"token_program": None}),
    ("token_2022_transfer_fee", {"token_program": "token-2022", "token_2022_transfer_fee": True}),
    ("token_2022_transfer_fee", {"token_program": "token-2022", "token_2022_transfer_fee": None}),
]


@pytest.mark.parametrize("reason,patch", VETOES)
def test_each_veto(tmp_path, reason, patch):
    out = build(tmp_path, mod=lambda i, r: r.update(patch))
    rows, cnt = run(out)
    assert rows == [] and cnt["veto_triggers"] == {reason: 1}


def test_veto_uses_latest_row_at_or_before_decision(tmp_path):
    out = build(tmp_path, mod=lambda i, r: r.update({"lp_locked_frac": 0.0}) if i > DEC_I else None)  # rows after the decision are not looked at
    rows, cnt = run(out)
    assert len(rows) == 1 and cnt["veto_triggers"] == {}


def test_token_2022_without_fee_is_allowed(tmp_path):
    out = build(tmp_path, mod=lambda i, r: r.update({"token_program": "token-2022", "token_2022_transfer_fee": False}))
    assert len(run(out)[0]) == 1


def test_null_fee_rate_excluded_not_assumed(tmp_path):
    out = build(tmp_path, mod=lambda i, r: r.update({"trade_fee_rate": None}))
    rows, cnt = run(out)
    assert rows == [] and cnt["fee_null_triggers"] == 1


def test_creator_fee_counts_only_when_enabled():
    assert P.pool_fee({"trade_fee_rate": 2500, "creator_fee_rate": 500, "enable_creator_fee": False}) == pytest.approx(0.0025)
    assert P.pool_fee({"trade_fee_rate": 2500, "creator_fee_rate": 500, "enable_creator_fee": True}) == pytest.approx(0.003)
    assert P.pool_fee({"trade_fee_rate": 2500, "creator_fee_rate": None, "enable_creator_fee": True}) is None


def test_pool_missing_from_pools_json_is_not_priced(tmp_path):
    out = build(tmp_path)
    (out / "pools.json").write_text(json.dumps({"pools": {}}))
    by, st, pm, sg, status = P.prepare(out, UNTIL_STD)
    miss = []
    assert P.build_pools(by, pm, False, sg, miss) == [] and miss == [POOL]


# ------------------------------------------------------------------------------------------------------------------------ G7
def flat_path():  # q 6e9 flat, drop to 5.2e9 (0.8e9 < 1 SOL): spot ratio 0.751, q_floor = 2 q with supply = b0 / 2
    return qpath(q0=6e9, q_drop=5.2e9, noise=0.0)


def test_g7_lower_bound_blocks_and_no_g7_form_triggers(tmp_path):
    out = build(tmp_path, q=flat_path(), sup_factor=0.5)
    assert run(out, g7=True)[0] == []
    assert len(run(out, g7=False)[0]) == 1  # the no-G7 form is report-only but computed


def test_g7_lower_bound_passes_with_enough_moves(tmp_path):
    out = build(tmp_path, q=qpath(q0=6e9, q_drop=5.2e9, noise=0.01e9), sup_factor=0.5)  # 400 pairs x 0.02 SOL of |dq|
    assert len(run(out, g7=True)[0]) == 1


def test_g7_excludes_gap_pairs(tmp_path):
    q = qpath(q0=6e9, q_drop=5.2e9, noise=0.0, overrides={0: 9e9, 1: 9e9})
    out = build(tmp_path, q=q, sup_factor=0.5, drop=set(range(2, 380)))  # the pair across the gap moves 3 SOL; it is not volume, so 0.8 SOL < 1 SOL
    assert run(out, g7=True)[0] == []
    assert len(run(out, g7=False)[0]) == 1


# ------------------------------------------------------------------------------------------------------------------------ counted set, until, markers
def test_counted_set_cutoff_is_24h_1_9s_300s(tmp_path):
    t = BASE + DEC_I * STEP
    out = build(tmp_path)
    assert BACK == 86_400_000 + 1_900 + 300_000
    assert len(deciding(out, until=t + BACK)[0]) == 1
    assert deciding(out, until=t + BACK - 1)[0] == []


def test_counts_mode_prints_one_integer_and_writes_only_marker(tmp_path, capsys):
    out = build(tmp_path)
    md = tmp_path / "m"
    md.mkdir()
    before = sorted(p.name for p in out.iterdir())
    until = BASE + DEC_I * STEP + BACK
    args = ["counts", "--out-dir", str(out), "--marker-dir", str(md), "--until", str(until)]
    assert P.main(args, now_ms=until) == 0
    cap = capsys.readouterr()
    assert cap.out == "1\n" and cap.err == ""
    assert sorted(p.name for p in out.iterdir()) == before
    assert [p.name for p in md.iterdir()] == [f"counts-{until}.json"]
    assert json.loads((md / f"counts-{until}.json").read_text())["count"] == 1
    assert P.main(args, now_ms=until) == 2  # second counts run at the same instant
    assert capsys.readouterr().out == ""


def test_until_refuses_before_instant(tmp_path, capsys):
    out = build(tmp_path)
    md = tmp_path / "m"
    md.mkdir()
    assert P.main(["counts", "--out-dir", str(out), "--marker-dir", str(md), "--until", str(UNTIL_STD)], now_ms=UNTIL_STD - 1) == 2
    assert list(md.iterdir()) == [] and capsys.readouterr().out == ""


def test_rows_and_files_at_or_after_until_are_never_used(tmp_path):
    out = build(tmp_path)
    until = BASE + 10 * 3600_000 + 30 * STEP
    (out / "2026-12-01T11.jsonl").write_bytes(b"\xff\xfe not utf8 \n")  # hour starts after until: must never be opened
    by, st = P.load_rows(out, until)
    assert st["rows_dropped_ms_ge_until"] > 0 and max(r["ms"] for rs in by.values() for r in rs) < until
    assert st["files_read"] == 11


def test_marker_dir_inside_out_dir_refused(tmp_path):
    out = build(tmp_path)
    (out / "m").mkdir()
    assert P.main(["counts", "--out-dir", str(out), "--marker-dir", str(out / "m"), "--until", str(UNTIL_STD)], now_ms=UNTIL_STD) == 2


def test_kill_refuses_below_10_and_without_counts(tmp_path, capsys):
    out = build(tmp_path)
    md = tmp_path / "m"
    md.mkdir()
    until = UNTIL_STD
    args = ["kill", "--out-dir", str(out), "--marker-dir", str(md), "--until", str(until)]
    assert P.main(args, now_ms=until) == 2  # no counts marker
    (md / f"counts-{until}.json").write_text(json.dumps({"count": 9}))
    assert P.main(args, now_ms=until) == 2
    assert not (md / "kill-onelook.json").exists() and capsys.readouterr().out == ""
    (md / f"counts-{until}.json").write_text(json.dumps({"count": "12"}))
    assert P.main(args, now_ms=until) == 2


def test_kill_runs_once_and_writes_one_look_marker(tmp_path, capsys):
    out = build(tmp_path)
    md = tmp_path / "m"
    md.mkdir()
    until = UNTIL_STD
    (md / f"counts-{until}.json").write_text(json.dumps({"count": 10}))
    args = ["kill", "--out-dir", str(out), "--marker-dir", str(md), "--until", str(until)]
    assert P.main(args, now_ms=until) == 0
    res = json.loads(capsys.readouterr().out)
    assert (md / "kill-onelook.json").exists()
    assert res["headline_cell"] == "stake0.25_lat4085ms_G7_corrected" and res["counted_set"] == 1
    assert len(res["cells"]) == 16  # 4 cells x {G7, no-G7} x {corrected, raw-vault}
    assert res["cells"][res["headline_cell"]]["press_proxy"]["label"].startswith("PROXY")
    assert "fifo_k4_report_only" in res["cells"][res["headline_cell"]]
    assert res["kill"]["verdict"] in ("KILLED", "PASS_FORWARD_KILL_LINE_NOT_A_PROMOTION")
    assert res["D_days"] == pytest.approx((until - BASE) / 3.6e6 / 24)
    assert P.main(args, now_ms=until) == 2  # one look only
    (md / f"counts-{until + 1}.json").write_text(json.dumps({"count": 11}))  # a different instant cannot run kill either
    assert P.main(["kill", "--out-dir", str(out), "--marker-dir", str(md), "--until", str(until + 1)], now_ms=until + 1) == 2


# ------------------------------------------------------------------------------------------------------------------------ owed fees, legs
def owed_row():
    r = row(0, 20e9)
    r.update(protocol_fees_token_0=1, fund_fees_token_0=2, creator_fees_token_0=4, protocol_fees_token_1=100, fund_fees_token_1=200, creator_fees_token_1=400)
    return r


def test_owed_fee_side_selected_by_wsol_is_token0():
    assert P.owed_split(owed_row(), True) == (7, 700)    # quote-side owed = _token_0 fields, base-side = _token_1
    assert P.owed_split(owed_row(), False) == (700, 7)   # quote-side owed = _token_1 fields


@pytest.mark.parametrize("wsol0,qoff,boff", [(True, 7, 700), (False, 700, 7)])
def test_corrected_reserves_use_owed_side(wsol0, qoff, boff):
    r = owed_row()
    S = P.PoolS("p", [r], wsol0, False, [])
    assert S.q[0] == r["quote_reserve"] - qoff and S.b[0] == r["base_reserve"] - boff
    R = P.PoolS("p", [r], wsol0, True, [])  # raw-vault variant
    assert R.q[0] == r["quote_reserve"] and R.b[0] == r["base_reserve"]


def test_owed_side_changes_the_gate_outcome(tmp_path):
    # owed 4 SOL on the token_0 fields: with wsol_is_token0 the quote reserve is cut by 4 SOL (E2 q_floor < 10 SOL, no trigger); the other side leaves it untouched
    def mod(i, r):
        r["protocol_fees_token_0"] = int(14e9)
    out_a = build(tmp_path, mod=mod, wsol0=True, name="a")
    out_b = build(tmp_path, mod=mod, wsol0=False, name="b")
    assert len(run(out_a)[0]) == 0 and len(run(out_b)[0]) == 1


def test_null_owed_field_is_a_missing_sample():
    r = row(0, 20e9, creator_fees_token_0=None)
    assert len(P.PoolS("p", [r], True, False, []).ms) == 0
    assert len(P.PoolS("p", [row(0, 20e9, err="account_missing")], True, False, []).ms) == 0


def test_pressure_proxy_not_available_without_fills():
    blk = P.leg_block([], 1.0)
    assert blk["press_proxy"] == {"status": "NOT_AVAILABLE"}
    v = P.verdict({**blk, "flat": {**blk["flat"], "trades_per_day": 0.0}}, 0)
    assert v["legs_read"] == ["flat"] and v["K1"] is False and v["verdict"] == "KILLED"


def test_proxy_flow_rise_and_buy_flag(tmp_path):
    out = build(tmp_path, q=qpath(overrides={DEC_I + 1: 25.3e9}))
    r = run(out)[0][0]
    assert r["flow_rise"] == pytest.approx(0.3e9) and r["ssb"] == 1
    r2 = run(build(tmp_path, name="o2"))[0][0]
    assert r2["flow_rise"] == 0.0 and r2["ssb"] == 0


def test_g5_one_open_position_per_pool(tmp_path):
    q = qpath(overrides={DEC_I + 1: 25e9, DEC_I + 2: 24e9, DEC_I + 3: 23e9, DEC_I + 4: 22e9, DEC_I + 5: 21e9})
    rows, _ = run(build(tmp_path, q=q))
    assert len([r for r in rows if r["filled"]]) == 1
