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


def run(out, g7=True, until=UNTIL_STD, stake=0.25, lat=4085, raw=False):
    pools = json.loads((out / "pools.json").read_text())["pools"]
    sp = out / "status.json"
    sg = json.loads(sp.read_text()).get("gaps", []) if sp.exists() else []
    spec = (raw, g7, stake, lat)
    return P.stream(out, until, [spec], pools, sg)["cells"][spec]


UNTIL_CLI = BASE + 33 * 3600_000


def cli(mode, out, md, until=UNTIL_CLI, now=None, hours=(33, 34)):
    return P.main([mode, "--out-dir", str(out), "--until", str(until)], now_ms=until if now is None else now, marker_dir=md, first_hour_ms=BASE, check_hours=hours)


def mkmd(tmp_path):
    md = tmp_path / "m"
    md.mkdir()
    return md


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
    assert len(rows) == 1  # G5 as the replay: the guarded attempt blocks the pool for the phantom hold, so the drop at DEC_I + 2 is not re-entered
    assert rows[0]["filled"] is False and rows[0]["pnl"] == -F.SEND_FEE_DECIDING
    assert rows[0]["exit_ms"] == BASE + DEC_I * STEP + P.HOLD_MS
    assert P.count_set(rows) == 0
    # the guarded send is paid in the K4 leg (guard_cost enters total_incl_guard; K1 reads the filled-only mean)
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


# ------------------------------------------------------------------------------------------------------------------------ G7 window, G8, G3, G5
def test_g7_pair_straddling_t_minus_24h_does_not_count(tmp_path):
    dec = 1500  # t - 24 h is sample 60
    until = BASE + (dec + 1446) * STEP
    qa = qpath(n=dec + 1500, q0=6e9, q_drop=5.2e9, noise=0.0, dec=dec, overrides={59: 6.3e9})
    out = build(tmp_path, q=qa, sup_factor=0.5, drop={60}, name="a")  # sample 60 missing: the pair (59, 61) moves 0.3 SOL but starts before t - 24 h
    assert run(out, until=until)[0] == []                               # window sum 0.8 SOL < 1 SOL (the old code summed 1.1 and admitted it)
    qb = qpath(n=dec + 1500, q0=6e9, q_drop=5.2e9, noise=0.0, dec=dec, overrides={60: 6.3e9})
    out_b = build(tmp_path, q=qb, sup_factor=0.5, name="b")             # pair (60, 61) lies fully inside: 0.3 + 0.8 = 1.1 SOL
    assert len(run(out_b, until=until)[0]) == 1


def test_g8_uses_the_sample_six_hours_back_not_the_previous_sample(tmp_path):
    q = qpath(overrides={i: 30e9 - 0.25e9 * (i - 380) for i in range(381, 400)})  # a slow slide: each step is a ~2% fall, the 6 h ratio crosses 0.80 at sample 393
    rows, _ = run(build(tmp_path, q=q))
    assert rows and rows[0]["entry_ms"] == BASE + 393 * STEP


@pytest.mark.parametrize("age_h", [10, 300])
def test_g3_age_window(tmp_path, age_h):
    out = build(tmp_path, mod=lambda i, r: r.update({"open_time": BASE // 1000 - int((age_h - 6.67) * 3600)}))
    assert run(out)[0] == []


def test_g5_unfilled_after_300s_blocks_for_the_phantom_hold(tmp_path):
    q = qpath(overrides={DEC_I + 7: 26e9, DEC_I + 8: 26e9, DEC_I + 9: 26e9, DEC_I + 10: 25e9})  # a second qualifying drop after the late landing
    rows, cnt = run(build(tmp_path, q=q, drop=set(range(DEC_I + 1, DEC_I + 7))))
    assert rows == [] and cnt["unfilled_after_300s"] == 1


def test_g5_entry_invalid_is_stranded_and_blocks(tmp_path):
    def mod(i, r):
        if i == DEC_I + 1:
            r["quote_reserve"] = 0
    q = qpath(overrides={DEC_I + 9: 26e9, DEC_I + 10: 25e9})
    rows, cnt = run(build(tmp_path, q=q, mod=mod))
    assert len(rows) == 1 and rows[0]["stranded"] and rows[0]["filled"]
    assert rows[0]["pnl"] == -0.25e9 - F.SEND_FEE_DECIDING - 2_039_280 and cnt["stranded_other"] == 1


# ------------------------------------------------------------------------------------------------------------------------ counted set, until, markers
def test_pinned_constants():
    assert P.FIRST_HOUR_FILE == "2026-11-23T23.jsonl"
    assert P.FIRST_HOUR_MS == int(datetime(2026, 11, 23, 23, tzinfo=timezone.utc).timestamp() * 1000)
    assert P.CHECK_HOURS == (336, 480, 624, 800)
    ins = P.check_instants()
    assert ins[0] == int(datetime(2026, 12, 7, 23, tzinfo=timezone.utc).timestamp() * 1000) and len(ins) == 4
    assert [(b - a) // 3_600_000 for a, b in zip(ins, ins[1:])] == [144, 144, 176]
    assert str(P.MARKER_DIR) == "/data2/mal/floor-cpmm/pricer-marks" and "forward-1124" not in str(P.MARKER_DIR)


def test_no_marker_dir_on_the_cli(tmp_path):
    with pytest.raises(SystemExit):
        P.main(["counts", "--out-dir", str(tmp_path), "--marker-dir", str(tmp_path), "--until", "1"])


def test_counted_set_cutoff_is_24h_1_9s_300s(tmp_path):
    t = BASE + DEC_I * STEP
    out = build(tmp_path)
    assert BACK == 86_400_000 + 1_900 + 300_000
    assert P.count_set(run(out, until=t + BACK)[0]) == 1
    assert P.count_set(run(out, until=t + BACK - 1)[0]) == 0


def test_counts_mode_prints_one_integer_and_writes_only_marker(tmp_path, capsys):
    out = build(tmp_path)
    md = mkmd(tmp_path)
    before = sorted(p.name for p in out.iterdir())
    assert cli("counts", out, md) == 0
    cap = capsys.readouterr()
    assert cap.out == "1\n" and cap.err == ""
    assert sorted(p.name for p in out.iterdir()) == before
    assert [p.name for p in md.iterdir()] == [f"counts-{UNTIL_CLI}.json"]
    assert json.loads((md / f"counts-{UNTIL_CLI}.json").read_text())["count"] == 1
    assert cli("counts", out, md) == 2  # second counts run at the same instant
    assert capsys.readouterr().out == ""
    assert cli("counts", out, md, until=UNTIL_CLI + 3600_000) == 0  # the next instant has its own marker
    assert sorted(p.name for p in md.iterdir()) == [f"counts-{UNTIL_CLI}.json", f"counts-{UNTIL_CLI + 3600_000}.json"]


def test_until_must_be_a_check_instant_and_not_in_the_future(tmp_path, capsys):
    out = build(tmp_path)
    md = mkmd(tmp_path)
    assert cli("counts", out, md, now=UNTIL_CLI - 1) == 2
    assert cli("counts", out, md, until=UNTIL_CLI + 1) == 2  # not one of the registered instants
    assert list(md.iterdir()) == [] and capsys.readouterr().out == ""


def test_rows_and_files_at_or_after_until_are_never_used(tmp_path):
    out = build(tmp_path)
    until = BASE + 10 * 3600_000 + 30 * STEP
    (out / "2026-12-01T11.jsonl").write_bytes(b"\xff\xfe not utf8 \n")  # hour starts after until: must never be opened
    spec = (False, True, 0.25, 4085)
    res = P.stream(out, until, [spec], json.loads((out / "pools.json").read_text())["pools"], [])
    assert res["load"]["rows_dropped_ms_ge_until"] > 0 and res["load"]["files_read"] == 11


def test_marker_dir_inside_out_dir_refused(tmp_path):
    out = build(tmp_path)
    (out / "m").mkdir()
    assert cli("counts", out, out / "m") == 2


# --- bad input never spends a look
def _nopools(o):
    (o / "pools.json").unlink()


def _badpools(o):
    (o / "pools.json").write_text("{not json")


def _emptypools(o):
    (o / "pools.json").write_text(json.dumps({"pools": {}}))


def _nobool(o):
    (o / "pools.json").write_text(json.dumps({"pools": {POOL: {"wsol_is_token0": "yes"}}}))


def _other_pool(o):
    (o / "pools.json").write_text(json.dumps({"pools": {"OTHER": {"wsol_is_token0": True}}}))


def _nofirst(o):
    (o / "2026-12-01T00.jsonl").unlink()


def _emptyrows(o):
    for f in o.glob("*.jsonl"):
        f.write_text("")


def _badline(o):
    with open(o / "2026-12-01T05.jsonl", "a") as f:
        f.write("{torn\n")


def _unordered(o):
    with open(o / "2026-12-01T05.jsonl", "a") as f:
        f.write(json.dumps(row(1, 20e9)) + "\n")


BAD = [_nopools, _badpools, _emptypools, _nobool, _other_pool, _nofirst, _emptyrows, _badline, _unordered]


@pytest.mark.parametrize("bad", BAD)
@pytest.mark.parametrize("mode", ["counts", "kill"])
def test_bad_input_refuses_and_writes_no_marker(tmp_path, capsys, bad, mode):
    out = build(tmp_path)
    md = mkmd(tmp_path)
    if mode == "kill":
        (md / f"counts-{UNTIL_CLI}.json").write_text(json.dumps({"count": 12}))
    kept = sorted(p.name for p in md.iterdir())
    bad(out)
    assert cli(mode, out, md) == 2
    assert sorted(p.name for p in md.iterdir()) == kept  # no marker, no tmp file
    assert capsys.readouterr().out == ""


def test_empty_out_dir_refuses(tmp_path):
    out = tmp_path / "e"
    out.mkdir()
    md = mkmd(tmp_path)
    assert cli("counts", out, md) == 2 and list(md.iterdir()) == []


def test_kill_refuses_below_10_and_without_counts(tmp_path, capsys):
    out = build(tmp_path)
    md = mkmd(tmp_path)
    assert cli("kill", out, md) == 2  # no counts marker
    (md / f"counts-{UNTIL_CLI}.json").write_text(json.dumps({"count": 9}))
    assert cli("kill", out, md) == 2
    assert not (md / "kill-onelook.json").exists() and capsys.readouterr().out == ""
    (md / f"counts-{UNTIL_CLI}.json").write_text(json.dumps({"count": "12"}))
    assert cli("kill", out, md) == 2


def test_kill_crash_during_pricing_leaves_no_marker(tmp_path, monkeypatch):
    out = build(tmp_path)
    md = mkmd(tmp_path)
    (md / f"counts-{UNTIL_CLI}.json").write_text(json.dumps({"count": 10}))

    def boom(*a, **k):
        raise RuntimeError("crash")

    with monkeypatch.context() as m:
        m.setattr(P, "stream", boom)
        with pytest.raises(RuntimeError):
            cli("kill", out, md)
    assert sorted(p.name for p in md.iterdir()) == [f"counts-{UNTIL_CLI}.json"]  # no marker, no tmp file
    assert cli("kill", out, md) == 0  # the look was not spent


def test_kill_runs_once_and_marker_holds_the_result(tmp_path, capsys):
    out = build(tmp_path)
    md = mkmd(tmp_path)
    (md / f"counts-{UNTIL_CLI}.json").write_text(json.dumps({"count": 10}))
    assert cli("kill", out, md) == 0
    res = json.loads(capsys.readouterr().out)
    mk = json.loads((md / "kill-onelook.json").read_text())
    assert mk["result"]["counted_set"] == res["counted_set"] == 1 and mk["result"]["headline_cell"] == res["headline_cell"]
    assert sorted(p.name for p in md.iterdir()) == [f"counts-{UNTIL_CLI}.json", "kill-onelook.json"]
    assert res["headline_cell"] == "stake0.25_lat4085ms_G7_corrected" and res["counted_set"] == 1 and res["counted_set_matches_marker"] is False
    assert len(res["cells"]) == 16  # 4 cells x {G7, no-G7} x {corrected, raw-vault}
    assert res["cells"][res["headline_cell"]]["press_proxy"]["label"].startswith("PROXY")
    assert "fifo_k4_report_only" in res["cells"][res["headline_cell"]]
    assert res["kill"]["verdict"] in ("KILLED", "PASS_FORWARD_KILL_LINE_NOT_A_PROMOTION")
    assert res["D_days"] == pytest.approx(33 / 24)
    assert cli("kill", out, md) == 2  # one look only
    (md / f"counts-{UNTIL_CLI + 3600_000}.json").write_text(json.dumps({"count": 11}))  # a different instant cannot run kill either
    assert cli("kill", out, md, until=UNTIL_CLI + 3600_000) == 2


# ------------------------------------------------------------------------------------------------------------------------ owed fees, legs
def owed_row():
    r = row(0, 20e9)
    r.update(protocol_fees_token_0=1, fund_fees_token_0=2, creator_fees_token_0=4, protocol_fees_token_1=100, fund_fees_token_1=200, creator_fees_token_1=400)
    return r


def test_owed_fee_side_selected_by_wsol_is_token0():
    assert P.owed_split(owed_row(), True) == (7, 700)    # quote-side owed = _token_0 fields, base-side = _token_1
    assert P.owed_split(owed_row(), False) == (700, 7)   # quote-side owed = _token_1 fields


def test_owed_side_changes_the_gate_outcome(tmp_path):
    # owed 14 SOL on the token_0 fields: with wsol_is_token0 the quote reserve is cut by 14 SOL (E2 q_floor < 10 SOL, no trigger); on the other side it is the base that is cut (no effect on E2)
    def mod(i, r):
        r["protocol_fees_token_0"] = int(14e9)
    out_a = build(tmp_path, mod=mod, wsol0=True, name="a")
    out_b = build(tmp_path, mod=mod, wsol0=False, name="b")
    assert len(run(out_a)[0]) == 0 and len(run(out_b)[0]) == 1


def test_raw_vault_variant_ignores_owed_fees(tmp_path):
    def mod(i, r):
        r["protocol_fees_token_0"] = int(14e9)
    out = build(tmp_path, mod=mod, wsol0=True)
    assert len(run(out, raw=False)[0]) == 0 and len(run(out, raw=True)[0]) == 1


def test_null_owed_field_is_a_missing_sample(tmp_path):
    out = build(tmp_path, mod=lambda i, r: r.update({"creator_fees_token_0": None}))
    assert run(out)[0] == []
    out2 = build(tmp_path, mod=lambda i, r: r.update({"err": "account_missing"}), name="o2")
    assert run(out2)[0] == []


