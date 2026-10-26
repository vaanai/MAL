"""Tests for tools/dbc_p18_score.py (P18 B2). Synthetic only: a synthetic hash-chained decision log, synthetic per-trade pnl, fake price functions. No tape, no network,
no real hour, nothing from the m10 window."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from tools import arena_stack as AS
from tools import dbc_fwd as F
from tools import dbc_fwd_read as R
from tools import dbc_fwd_sec3 as X
from tools import dbc_p18_score as P
from tools import dbc_price as L
from tools import dbc_screen as S
from tools.arena_declog import DecisionLog
from tools.test_dbc_fwd import _raises

D = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
CLOCK = D(2026, 11, 2, 0)
SEND = float(L.SEND_LAMPORTS)
CFG_NAMES = {"CfgOut1": "Cfga" + "a" * 30, "CfgIn1": "Cfgb" + "b" * 30}  # base58-shaped (32..44 chars) so scan_configs sees them


def write_log(root, per_day: dict[str, int], clock=CLOCK, extra=None):
    """decision rows: per_day {'2026-11-02': n} spread over the day; plus a start row and one exit row (which the scorer must ignore)."""
    log = DecisionLog(str(root))
    log.append(F.BOOK, int(clock.timestamp() * 1000), {"type": "start", "book": F.BOOK})
    rows = []
    for day, n in per_day.items():
        t0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        for i in range(n):
            rows.append((t0 + 60 + i * (80000 / max(n, 1)), f"{day}-k{i}"))
    for bt, key in sorted(rows):
        log.append(F.BOOK, int(bt * 1000), {"type": "decision", "key": key, "block_time": bt, "pool": "p"})
    log.append(F.BOOK, int(rows[-1][0] * 1000) + 1, {"type": "exit", "decision_seq": 1, "pnl": 12345.0})
    for body in extra or []:
        log.append(F.BOOK, int(body.pop("ms")), body)
    return log


def models_from_pnl(pnl, filled, ssb, nearby, dates, send=SEND):
    flat, press, c, p = AS.fail_legs(np.asarray(pnl, float), np.asarray(filled, bool), ssb, nearby, send=send)
    return {"flat": S.stat(flat / 1e9, dates), "press": S.stat(press / 1e9, dates), "press_intercept": c}


def tmp():
    return tempfile.TemporaryDirectory()


def days(n, per, start=2):
    return {f"2026-11-{start + i:02d}": per for i in range(n)}


def fake_price(pnl_of, date_of=lambda k: k[:10], strands=0, not_found=0):
    """price_fn from a per-key pnl (lamports), through the real fail models; fills = all."""
    def fn(items, lat_in=1.9, lat_out=None, fixed_k=None):
        pnl = np.array([pnl_of(k, lat_in) for k, _ in items], float)
        dates = [date_of(k) for k, _ in items]
        m = models_from_pnl(pnl, np.ones(len(pnl), bool), np.zeros(len(pnl)), np.zeros(len(pnl)), dates)
        return {"counts": {"key_not_found_full_loss": not_found}, "n_priced": len(items), "fixed_items": 0, "fills": len(items), "guard_unfilled": 0, "strands": strands,
                "flat": m["flat"], "press": m["press"]}
    return fn


# ------------------------------------------------------------------------------------------------------------------------------ bootstrap, fail models, top-3
def test_bootstrap_known_answers():
    assert AS.BOOT_DRAWS == 1000 and AS.BOOT_SEED == 1 and AS.BOOT_PCT == 5
    assert abs(AS.boot_lo(np.full(50, 0.25)) - 0.25) < 1e-12  # constant series: every resample mean is the constant
    x = np.array([1.0, -2.0, 3.0, 0.5, -0.25, 2.0, 1.5, -1.0])
    rng = np.random.default_rng(1)
    ref = np.percentile([x[rng.integers(0, len(x), len(x))].mean() for _ in range(1000)], 5)  # independent re-statement of the rule
    assert AS.boot_lo(x) == ref and S.stat(x, ["a"] * 4 + ["b"] * 4)["ci90_lo_trade"] == ref
    assert ref < x.mean()


def test_both_fail_models_known_answers():
    pnl = np.array([1e7, 2e7, -5e6, 4e6])
    m = models_from_pnl(pnl, np.ones(4, bool), np.zeros(4), np.zeros(4), ["d"] * 4)
    flat_ref = (0.85 * pnl - 0.15 * SEND) / 1e9
    assert abs(m["flat"]["total"] - flat_ref.sum()) < 1e-15 and m["flat"]["n"] == 4
    # pressure at slope scale 1 with identical rows: every p equals the refit intercept's sigmoid = mean p target 0.289
    press_ref = ((1 - 0.289) * pnl - 0.289 * SEND) / 1e9
    assert abs(m["press"]["total"] - press_ref.sum()) < 1e-9
    assert AS.PRESS_P_TARGET == 0.289 and AS.FAIL_FLAT == 0.15
    # an unfilled (guarded) row already carries its own loss and is not failed again
    m2 = models_from_pnl(np.array([-SEND, 1e7]), np.array([False, True]), np.zeros(2), np.zeros(2), ["d", "d"])
    assert abs(m2["flat"]["total"] - ((-SEND) + 0.85 * 1e7 - 0.15 * SEND) / 1e9) < 1e-15


def test_top3_removal_decides():
    x = np.array([5.0, 4.0, 3.0] + [0.01] * 100 + [-0.001] * 20)
    assert S.stat(x, ["d"] * len(x))["ex_top3"] == pytest.approx(0.98)
    y = np.array([5.0, 4.0, 3.0] + [-0.01] * 120)
    s = S.stat(y, ["d"] * len(y))
    assert s["total"] > 0 and s["ex_top3"] < 0


# ------------------------------------------------------------------------------------------------------------------------------ boundary
def test_boundary_first_midnight_with_100_on_5_dates():
    t = lambda day, i: D(2026, 11, day, 1).timestamp() + i  # noqa: E731
    bts = [t(d, i) for d in (2, 3, 4, 5, 6) for i in range(20)]
    b = P.boundary(bts, CLOCK)
    assert b["B_utc"] == "2026-11-07T00:00:00Z" and b["n"] == 100 and len(b["dates"]) == 5
    # 120 decisions on 4 dates: count is met but dates are not -> no boundary
    assert P.boundary([t(d, i) for d in (2, 3, 4, 5) for i in range(30)], CLOCK) is None
    # 5 dates but only 99 decisions -> none; one more decision on a later date gives B on that date's end
    bts99 = [t(d, i) for d in (2, 3, 4, 5, 6) for i in range(20)][:-1]
    assert P.boundary(bts99, CLOCK) is None
    assert P.boundary(bts99 + [t(7, 0)], CLOCK)["B_utc"] == "2026-11-08T00:00:00Z"
    assert P.boundary([], CLOCK) is None


# ------------------------------------------------------------------------------------------------------------------------------ refusals
def test_refuses_clock_before_earliest_and_decision_before_clock():
    with tmp() as td:
        write_log(td, days(1, 5, 2))
        with _raises(P.ScoreRefused):
            P.load_decisions(td, D(2026, 11, 1, 0, 9))
        r = P.load_decisions(td, D(2026, 11, 3))  # Plan B: the decisions on 11-02 precede the clock; they are excluded by timestamp and counted, not refused
        assert r["decisions"] == [] and r["excluded_pre_clock"] == 5
        r = P.load_decisions(td, CLOCK)
        assert len(r["decisions"]) == 5 and r["excluded_pre_clock"] == 0


def test_refuses_broken_chain_and_duplicates():
    with tmp() as td:
        log = write_log(td, days(1, 3))
        f = sorted(Path(td).rglob("*.jsonl"))[0]
        lines = f.read_text().splitlines()
        lines[1] = lines[1].replace('"pool":"p"', '"pool":"q"')
        f.write_text("\n".join(lines) + "\n")
        with _raises(P.ScoreRefused):
            P.load_decisions(td, CLOCK)
    with tmp() as td:
        log = DecisionLog(td)
        for i in range(2):
            log.append(F.BOOK, int(CLOCK.timestamp() * 1000) + i, {"type": "decision", "key": "same", "block_time": CLOCK.timestamp() + 5 + i})
        with _raises(P.ScoreRefused):
            P.load_decisions(td, CLOCK)


def test_refuses_before_B_and_on_rows_after_drop():
    with tmp() as td:
        write_log(td, days(5, 20))
        dec = P.load_decisions(td, CLOCK)
        pf = fake_price(lambda k, l: 1e7)
        with _raises(P.ScoreRefused):  # B = 11-07T00:00; one second before
            P.score(dec, CLOCK, D(2026, 11, 6, 23, 59, 59), pf, 0)
        with _raises(P.ScoreRefused):
            P.score(dec, CLOCK, D(2026, 11, 8), pf, None)
        with _raises(P.ScoreRefused):
            P.score(dec, CLOCK, D(2026, 11, 8), pf, 3)
        r = P.score(dec, CLOCK, D(2026, 11, 8), pf, 3, "3 late rows of a pool that completed in the gap hour (row ids in the note)")
        assert r["rows_after_drop"]["value"] == 3 and r["verdict"] in ("PASS", "FAIL")
    with tmp() as td:  # too few decisions: no boundary yet
        write_log(td, days(4, 40))
        with _raises(P.ScoreRefused):
            P.score(P.load_decisions(td, CLOCK), CLOCK, D(2026, 12, 1), fake_price(lambda k, l: 1e7), 0)


def test_only_decisions_before_B_are_scored():
    with tmp() as td:
        write_log(td, {**days(5, 20), "2026-11-07": 7})
        dec = P.load_decisions(td, CLOCK)
        seen = []
        def pf(items, lat_in=1.9, lat_out=None, fixed_k=None):
            seen.append(len(items))
            return fake_price(lambda k, l: 1e7)(items, lat_in, lat_out, fixed_k)
        r = P.score(dec, CLOCK, D(2026, 11, 8), pf, 0)
        assert set(seen) == {100} and r["deciding"]["n_decisions"] == 100


def test_not_found_over_1pct_is_not_decidable():
    with tmp() as td:
        write_log(td, days(5, 20))
        r = P.score(P.load_decisions(td, CLOCK), CLOCK, D(2026, 11, 8), fake_price(lambda k, l: 1e7, not_found=2), 0)
        assert r["verdict"] == "NOT_DECIDABLE" and "not found" in r["reason"]
        r = P.score(P.load_decisions(td, CLOCK), CLOCK, D(2026, 11, 8), fake_price(lambda k, l: 1e7, not_found=1), 0)
        assert r["verdict"] == "PASS"  # exactly 1 % is allowed


# ------------------------------------------------------------------------------------------------------------------------------ the gate
def pnl_wave(k, lat):
    i = int(k.split("-k")[1])
    return 1.2e7 + (i % 7 - 3) * 1e6  # always positive; noise small


def test_gate_pass_and_each_check_can_fail():
    with tmp() as td:
        write_log(td, days(6, 20))
        dec = P.load_decisions(td, CLOCK)
        r = P.score(dec, CLOCK, D(2026, 11, 9), fake_price(pnl_wave), 0)
        assert r["verdict"] == "PASS", r["checks"]
        assert all(all(v.values()) for k, v in r["checks"].items() if k in ("flat", "press"))
        assert r["deciding"]["attempts_by_day"] and r["boundary"]["B_utc"] == "2026-11-07T00:00:00Z"
        assert "NEVER count toward P18" in r["disclosure"]
        # 2.5 s entry negative -> FAIL on that check only
        r2 = P.score(dec, CLOCK, D(2026, 11, 9), fake_price(lambda k, l: pnl_wave(k, l) if l < 2 else -3e7), 0)
        assert r2["verdict"] == "FAIL" and not r2["checks"]["flat"]["entry_2p5s_mean_gt_0"]
        # top-3 carry the book: total > 0 but ex-top-3 < 0 -> FAIL
        def carried(k, l):
            i = int(k.split("-k")[1]); d = k[:10]
            return 4e9 if (d, i) in {("2026-11-02", 0), ("2026-11-02", 1), ("2026-11-02", 2)} else -1e7
        r3 = P.score(dec, CLOCK, D(2026, 11, 9), fake_price(carried), 0)
        assert r3["verdict"] == "FAIL" and r3["deciding"]["primary_1p9s"]["flat"]["total"] > 0 and not r3["checks"]["flat"]["total_ex_top3_gt_0"]
        # a minority of positive days -> FAIL (majority rule)
        r4 = P.score(dec, CLOCK, D(2026, 11, 9), fake_price(lambda k, l: 1.5e8 if k.startswith("2026-11-02") else -1e6), 0)
        assert r4["verdict"] == "FAIL" and not r4["checks"]["flat"]["majority_days_positive"]
        # pressure model alone failing is enough to fail the gate (pnl = 0.3 x send: flat mean > 0, pressure mean < 0)
        r5 = P.score(dec, CLOCK, D(2026, 11, 9), fake_price(lambda k, l: 0.3 * SEND), 0)
        assert r5["verdict"] == "FAIL" and r5["deciding"]["primary_1p9s"]["flat"]["mean"] > 0 > r5["deciding"]["primary_1p9s"]["press"]["mean"]
        assert not r5["checks"]["press"]["mean_gt_0"]


def test_fewer_than_100_trades_never_passes():
    with tmp() as td:
        write_log(td, days(6, 20))
        dec = P.load_decisions(td, CLOCK)
        def few(items, lat_in=1.9, lat_out=None, fixed_k=None):
            r = fake_price(pnl_wave)(items[:60], lat_in)
            return r
        r = P.score(dec, CLOCK, D(2026, 11, 9), few, 0)
        assert r["verdict"] == "NOT_DECIDABLE"  # fewer than 100 trades is labelled like fewer than 100 fills


def test_strand_deciding_is_full_loss_and_damm_line_counts_coverage():
    assert X.FULL_LOSS == -(0.25 * 1e9 + SEND + L.RENT_STRANDED)
    line = P.strand_line(15, [{"key": "a", "pnl_lamports": -1e8}, {"key": "b", "pnl_lamports": 5e7}])
    assert line["strands_total"] == 15 and line["strands_covered"] == 2 and line["REPORT_ONLY"] and abs(line["coverage"] - 2 / 15) < 1e-15
    assert abs(line["delta_vs_full_loss_sol_sum"] - ((-1e8 - X.FULL_LOSS) + (5e7 - X.FULL_LOSS)) / 1e9) < 1e-12
    none = P.strand_line(4, None)
    assert none["strands_covered"] is None and none["coverage"] is None and none["status"].startswith("NOT COMPUTED")
    zero = P.strand_line(4, [])
    assert zero["strands_covered"] == 0 and zero["status"] == "computed"
    with tmp() as td:
        write_log(td, days(5, 20))
        r = P.score(P.load_decisions(td, CLOCK), CLOCK, D(2026, 11, 8), fake_price(pnl_wave, strands=4), 0)
        assert r["report_only"]["strand_immediate_damm_sell"]["strands_total"] == 4 and set(r["report_only"]) >= {"fixed_k9", "entry_3p4s", "exit_3p4s"}
        assert r["report_only"]["REPORT_ONLY"] is True and "checks" in r  # report-only lines are not inside the checks


def test_report_only_lines_do_not_change_the_verdict():
    with tmp() as td:
        write_log(td, days(5, 20))
        dec = P.load_decisions(td, CLOCK)
        base = P.score(dec, CLOCK, D(2026, 11, 8), fake_price(pnl_wave), 0)
        def weird(items, lat_in=1.9, lat_out=None, fixed_k=None):
            f = fake_price(lambda k, l: pnl_wave(k, l) if (lat_out is None and fixed_k is None and lat_in < 3) else -9e9)
            return f(items, lat_in, lat_out, fixed_k)
        r = P.score(dec, CLOCK, D(2026, 11, 8), weird, 0)
        assert r["verdict"] == base["verdict"] == "PASS" and r["checks"] == base["checks"]


# ------------------------------------------------------------------------------------------------------------------------------ guards and runner
def test_scoped_patches_restore():
    old = (L.FWD_ACTIVE, L.FWD_START, L.FWD_END, L.LAT_S, S.k_slots)
    with P.patched(S, "k_slots", lambda lat, ss: 9):
        assert S.k_slots(1.9, 0.4) == 9
    with P.patched(L, "LAT_S", 3.4):
        assert L.LAT_S == 3.4
    with P.p18_window("/x", D(2026, 11, 2), D(2026, 11, 7)):
        assert L.FWD_ACTIVE == "/x" and L.FWD_START == D(2026, 11, 2) and L.FWD_END == D(2026, 11, 7)
    assert (L.FWD_ACTIVE, L.FWD_START, L.FWD_END, L.LAT_S, S.k_slots) == old
    assert S.k_slots(1.9, 0.4) == 5  # round(1.9/0.4) = 5 restored


def test_window_refuses_the_m10_window_and_partial_hours():
    for s, e in ((D(2026, 10, 25), D(2026, 11, 2)), (D(2026, 10, 31, 23), D(2026, 11, 2)), (D(2026, 11, 2), D(2026, 11, 2)), (D(2026, 11, 2, 0, 30), D(2026, 11, 3))):
        with _raises(P.ScoreRefused):
            with P.p18_window("/x", s, e):
                pass
    assert L.FWD_ACTIVE is None


def test_run_refuses_without_claiming_and_claims_once():
    with tmp() as td:
        root, out, marker = Path(td) / "log", Path(td) / "out", str(Path(td) / "marker")
        write_log(root, days(5, 20))
        st = Path(td) / "status.json"
        st.write_text(json.dumps({"stats": {"rows_after_drop": 0}}))
        kw = dict(price_fn_factory=lambda D_, c, B: fake_price(pnl_wave), load_fn=lambda c, B, od: {}, marker=marker)
        assert P.run(str(root), CLOCK, out, D(2026, 11, 6), str(st), **kw) == 6 and not Path(marker).exists()  # before B
        st.write_text(json.dumps({"stats": {"rows_after_drop": 2}}))
        assert P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), **kw) == 6 and not Path(marker).exists()  # unexplained drop
        assert P.run(str(root), D(2026, 11, 1), out, D(2026, 11, 8), str(st), **kw) == 6 and not Path(marker).exists()  # clock too early
        st.write_text(json.dumps({"stats": {"rows_after_drop": 0}}))
        assert P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), **kw) == 0 and Path(marker).exists()
        res = json.loads((out / P.OUT_JSON).read_text())
        assert res["verdict"] == "PASS" and res["schema"] == P.SCHEMA
        assert P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), **kw) == 3  # one look: output exists
        assert P.run(str(root), CLOCK, Path(td) / "out2", D(2026, 11, 8), str(st), **kw) == 4 and not (Path(td) / "out2").exists()  # marker exists, no dir left
        assert P.MARKER != R.MARKER


def test_counts_only_reads_decision_rows_only():
    with tmp() as td:
        write_log(td, days(3, 10))
        c = P.counts_only(td, CLOCK)
        assert c["n_decisions"] == 30 and c["by_day"] == {"2026-11-02": 10, "2026-11-03": 10, "2026-11-04": 10} and c["boundary"] is None and c["outcome_fields_read"] is False


# ------------------------------------------------------------------------------------------------------------------------------ reviewer round: restore on raise, look accounting, loader
def test_globals_restored_when_the_body_raises():
    old = (L.FWD_ACTIVE, L.FWD_START, L.FWD_END, L.LAT_S, S.k_slots)
    with pytest.raises(RuntimeError):
        with P.p18_window("/x", D(2026, 11, 2), D(2026, 11, 7)):
            assert L.FWD_ACTIVE == "/x"
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        with P.patched(L, "LAT_S", 3.4), P.patched(S, "k_slots", lambda lat, ss: 9):
            raise RuntimeError("boom")
    assert (L.FWD_ACTIVE, L.FWD_START, L.FWD_END, L.LAT_S, S.k_slots) == old


def _wired(td, rad=0):
    root, out, marker = Path(td) / "log", Path(td) / "out", str(Path(td) / "marker")
    write_log(root, days(5, 20))
    st = Path(td) / "status.json"
    st.write_text(json.dumps({"stats": {"rows_after_drop": rad}}))
    return root, out, marker, st


@pytest.mark.parametrize("exc", [RuntimeError("x"), KeyError("k"), MemoryError()])
def test_exception_after_the_claim_writes_a_write_once_error_record(exc):
    with tmp() as td:
        root, out, marker, st = _wired(td)
        def boom(c, B, od):
            raise exc
        rc = P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, load_fn=boom, price_fn_factory=lambda D_, c, B: fake_price(pnl_wave))
        assert rc == 8 and Path(marker).exists()
        rec = json.loads((out / P.OUT_JSON).read_text())
        assert rec["status"] == "ERROR" and rec["error_class"] == type(exc).__name__ and "deciding" not in rec and "report_only" not in rec
        with pytest.raises(FileExistsError):
            P.write_once(out / P.OUT_JSON, "{}")
        assert json.loads((out / P.OUT_JSON).read_text())["status"] == "ERROR"


def test_system_exit_after_the_claim_is_recorded_then_reraised():
    with tmp() as td:
        root, out, marker, st = _wired(td)
        def die(c, B, od):
            raise SystemExit(3)
        with pytest.raises(SystemExit):
            P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, load_fn=die, price_fn_factory=lambda D_, c, B: fake_price(pnl_wave))
        assert json.loads((out / P.OUT_JSON).read_text())["error_class"] == "SystemExit"


def test_wiring_and_fetcher_failures_happen_before_the_claim_and_leave_nothing():
    with tmp() as td:
        root, out, marker, st = _wired(td)
        def bad_fetch():
            raise OSError("no key file")
        assert P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, backfill_root=str(Path(td) / "bf"), make_fetch=bad_fetch) == 7  # root missing
        (Path(td) / "bf").mkdir()
        assert P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, backfill_root=str(Path(td) / "bf"), make_fetch=bad_fetch) == 7  # fetcher fails
        assert P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, load_fn="not callable") == 7
        assert P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, load_fn=lambda c, B, od: {}, base_map=str(Path(td) / "nope.json")) == 7
        assert not Path(marker).exists() and not out.exists()


def test_existing_marker_leaves_no_empty_out_dir():
    with tmp() as td:
        root, out, marker, st = _wired(td)
        Path(marker).write_text("x")
        assert P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, load_fn=lambda c, B, od: {}) == 4
        assert not out.exists()


def test_default_load_on_a_synthetic_backfill_layout(monkeypatch):
    import subprocess
    from tools import test_dbc_fwd as T
    saved = (dict(L.CFG_THR), set(L.CFG_XFER), set(L.CFG_UNMAPPED))
    monkeypatch.setattr(L, "SWAP3_IS_HOOK", L.SWAP3_IS_HOOK)
    monkeypatch.setattr(L, "FOB_EVIDENCE", L.FOB_EVIDENCE)
    t0 = D(2026, 11, 2).timestamp()
    delta = int(t0) - T.T0
    with tmp() as td:
        root = Path(td) / "backfill" / "meteora_dbc"
        root.mkdir(parents=True)
        rows = T.synth_rows()
        for h in range(3):
            lo, hi = (T.T0 + 3600 * h) * 1000, (T.T0 + 3600 * (h + 1)) * 1000
            lines = [json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour", "hour": h}, separators=(",", ":"))]
            for r in rows:
                if lo <= r["t_recv_ms"] < hi:
                    lines.append(json.dumps({**r, "config": CFG_NAMES.get(r["config"], r["config"]), **({"block_time": r["block_time"] + delta} if "block_time" in r else {}), "t_recv_ms": r["t_recv_ms"] + delta * 1000}, separators=(",", ":")))
            lines.append(json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour_end"}, separators=(",", ":")))
            name = root / f"tape-2026-11-02T{h:02d}.jsonl"
            name.write_text("\n".join(lines) + "\n")
            subprocess.run(["zstd", "-q", "--rm", "-f", str(name), "-o", str(name) + ".zst"], check=True)
        bm = Path(td) / "base.json"
        bm.write_text(json.dumps({c: {"thr": 85 * 10**9, "transfer_fee_bps": 0} for c in CFG_NAMES.values()}))
        out = Path(td) / "out"
        out.mkdir()
        old = (L.FWD_ACTIVE, L.FWD_START, L.FWD_END)
        fetched = []
        try:
            Dd = P.default_load(str(root), CLOCK, D(2026, 11, 2, 2), str(bm), lambda ks: fetched.append(ks) or {}, out)
            assert (out / "cfg_map_final.json").exists() and not fetched and P.R.scan_configs.__name__  # map written INSIDE the out dir; every config was in the base map
            assert len(Dd["good_hours"]) == 3 and len(Dd["bad_hours"]) == 1 and Dd["hours_needed"] == 4 and len(Dd["pool_names"]) >= 3
            assert (L.FWD_ACTIVE, L.FWD_START, L.FWD_END) == old
            pf = P.make_price_fn(Dd)  # the pricer runs on the loaded window; keys that do not exist are full loss (AM2 counts), nothing raises
            r = pf([("nokey", D(2026, 11, 2, 1).timestamp())], 1.9)
            assert r["counts"]["key_not_found_full_loss"] == 1
            r2 = pf([("nokey", D(2026, 11, 2, 1).timestamp())], 1.9, lat_out=3.4, fixed_k=9)
            assert r2["counts"]["key_not_found_full_loss"] == 1 and L.LAT_S == 1.9
            # an unmapped config over 5 % of rows refuses
            bm.write_text(json.dumps({CFG_NAMES["CfgIn1"]: {"thr": 85 * 10**9, "transfer_fee_bps": 0}}))
            with pytest.raises(P.ScoreRefused):
                P.default_load(str(root), CLOCK, D(2026, 11, 2, 2), str(bm), lambda ks: {}, out)
            assert (L.FWD_ACTIVE, L.FWD_START, L.FWD_END) == old
            with pytest.raises(P.ScoreRefused):  # a window reaching the m10 hours is refused
                P.default_load(str(root), D(2026, 10, 31), D(2026, 11, 2, 2), str(bm), lambda ks: {}, out)
        finally:
            L.CFG_THR.clear(); L.CFG_THR.update(saved[0])
            L.CFG_XFER.clear(); L.CFG_XFER.update(saved[1])
            L.CFG_UNMAPPED.clear(); L.CFG_UNMAPPED.update(saved[2])


def test_pre_clock_decisions_are_excluded_and_counted_through_run():
    with tmp() as td:
        root, out, marker = Path(td) / "log", Path(td) / "out", str(Path(td) / "marker")
        write_log(root, {"2026-11-01": 9, **days(5, 20, start=6)}, clock=D(2026, 11, 1, 1))
        st = Path(td) / "st.json"
        st.write_text(json.dumps({"stats": {"rows_after_drop": 0}}))
        clock = D(2026, 11, 6)
        assert P.run(str(root), clock, out, D(2026, 11, 12), str(st), marker=marker, load_fn=lambda c, B, od: {}, price_fn_factory=lambda D_, c, B: fake_price(pnl_wave)) == 0
        rec = json.loads((out / P.OUT_JSON).read_text())
        assert rec["log"]["excluded_pre_clock"] == 9 and rec["deciding"]["n_decisions"] == 100 and "2026-11-01" not in rec["deciding"]["attempts_by_day"]
        assert "RECEIVE time" in rec["disclosure"]


def test_k_per_hour_is_printed():
    with tmp() as td:
        write_log(td, days(5, 20))
        ss = {int(D(2026, 11, 2, h).timestamp() // 3600): 0.2 + 0.1 * (h % 2) for h in range(24)}
        r = P.score(P.load_decisions(td, CLOCK), CLOCK, D(2026, 11, 8), fake_price(pnl_wave), 0, slot_s=ss)
        kb = r["k_by_hour"]
        assert kb and all(v["k_1p9"] == S.k_slots(1.9, v["slot_s"]) for v in kb.values()) and {9, 6} <= {v["k_1p9"] for v in kb.values()}
        assert sum(v["decisions"] for v in kb.values()) == 100


def test_backfill_preflight_mirrors_the_m10_rule_and_runs_before_the_claim():
    import os, time
    clock, B = D(2026, 11, 2), D(2026, 11, 2, 2)
    with tmp() as td:
        root = Path(td) / "bf"
        root.mkdir()
        s0, e0 = P.window_hours(clock, B)
        assert (s0, e0) == (D(2026, 11, 1, 23), D(2026, 11, 2, 3))  # one lead hour before the clock, one tail hour past B
        ok, why = P.backfill_preflight(str(root), clock, B)
        assert not ok and "missing_4_of_4" in why
        for n in ["2026-11-01T23"] + [f"2026-11-02T{h:02d}" for h in range(3)]:
            (root / f"tape-{n}.jsonl.zst").write_text("x")
        t = time.time()
        ok, why = P.backfill_preflight(str(root), clock, B, now=t)
        assert not ok and "newest_mtime_only" in why  # a writer may still be active
        assert P.backfill_preflight(str(root), clock, B, now=t + 700) == (True, "ok")
        assert P.backfill_preflight(str(Path(td) / "nope"), clock, B)[0] is False
    with tmp() as td:  # through run(): a refusing preflight claims nothing and creates nothing
        lroot, out, marker = Path(td) / "log", Path(td) / "out", str(Path(td) / "marker")
        write_log(lroot, days(5, 20))
        st = Path(td) / "st.json"
        st.write_text(json.dumps({"stats": {"rows_after_drop": 0}}))
        rc = P.run(str(lroot), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, load_fn=lambda c, B_, od: {}, preflight_fn=lambda c, B_: (False, "missing"))
        assert rc == 5 and not Path(marker).exists() and not out.exists()


def _seven_bad_hours(monkeypatch, n_bad=7):
    """default_load with the real window/guard code but a stubbed config scan and a stubbed load_exploration that returns `n_bad` bad hours (R.MAX_BAD_HOURS is 6)."""
    monkeypatch.setattr(L, "SWAP3_IS_HOOK", L.SWAP3_IS_HOOK)
    monkeypatch.setattr(L, "FOB_EVIDENCE", L.FOB_EVIDENCE)
    monkeypatch.setattr(L, "CFG_THR", dict(L.CFG_THR))
    monkeypatch.setattr(L, "CFG_XFER", set(L.CFG_XFER))
    monkeypatch.setattr(L, "CFG_UNMAPPED", set(L.CFG_UNMAPPED))
    monkeypatch.setattr(R, "scan_configs", lambda root: {})
    monkeypatch.setattr(R, "resolve_configs", lambda counts, base, fetch: ({}, {"unmapped": [], "unmapped_row_share": 0.0}))
    monkeypatch.setattr(S, "load_exploration", lambda root=None, log=print, start=None, end=None: {"bad_hours": list(range(n_bad)), "good_hours": [99]})


def test_default_load_refuses_seven_bad_hours_and_accepts_six(monkeypatch):
    assert R.MAX_BAD_HOURS == 6
    _seven_bad_hours(monkeypatch, 7)
    with tmp() as td:
        bm = Path(td) / "b.json"
        bm.write_text("{}")
        out = Path(td) / "o"
        out.mkdir()
        with pytest.raises(P.ScoreRefused, match=r"7 hours missing/incomplete"):
            P.default_load(td, CLOCK, D(2026, 11, 2, 2), str(bm), lambda ks: {}, out)
        assert L.FWD_ACTIVE is None  # window globals restored
        monkeypatch.setattr(S, "load_exploration", lambda root=None, log=print, start=None, end=None: {"bad_hours": list(range(6)), "good_hours": [99]})
        assert P.default_load(td, CLOCK, D(2026, 11, 2, 2), str(bm), lambda ks: {}, out)["bad_hours"] == list(range(6))  # exactly at the bar passes


def test_run_with_seven_bad_hours_claims_the_marker_and_writes_not_decidable(monkeypatch):
    _seven_bad_hours(monkeypatch, 7)
    with tmp() as td:
        root, out, marker, st = _wired(td)
        bm = Path(td) / "b.json"
        bm.write_text("{}")
        load = lambda c, B, od: P.default_load(td, c, B, str(bm), lambda ks: {}, od)  # noqa: E731
        rc = P.run(str(root), CLOCK, out, D(2026, 11, 8), str(st), marker=marker, load_fn=load, base_map=str(bm), price_fn_factory=lambda D_, c, B: fake_price(pnl_wave))
        assert rc == 0 and Path(marker).exists()  # the look is consumed
        rec = json.loads((out / P.OUT_JSON).read_text())
        assert rec["verdict"] == "NOT_DECIDABLE" and "7 hours missing/incomplete" in rec["reason"] and "deciding" not in rec
        assert "NOT_DECIDABLE" in (out / P.OUT_MD).read_text()


def test_default_load_with_no_hour_files_refuses_as_no_usable_hours(monkeypatch):
    monkeypatch.setattr(L, "SWAP3_IS_HOOK", L.SWAP3_IS_HOOK)
    monkeypatch.setattr(L, "FOB_EVIDENCE", L.FOB_EVIDENCE)
    monkeypatch.setattr(L, "CFG_THR", dict(L.CFG_THR))
    monkeypatch.setattr(L, "CFG_XFER", set(L.CFG_XFER))
    monkeypatch.setattr(L, "CFG_UNMAPPED", set(L.CFG_UNMAPPED))
    with tmp() as td:
        root = Path(td) / "bf"
        root.mkdir()
        bm = Path(td) / "b.json"
        bm.write_text("{}")
        out = Path(td) / "o"
        out.mkdir()
        with pytest.raises(P.ScoreRefused, match="no usable backfill hours"):  # this is load_exploration's refusal, NOT the bad-hour bar
            P.default_load(str(root), CLOCK, D(2026, 11, 2, 2), str(bm), lambda ks: {}, out)
    assert L.FWD_ACTIVE is None


def test_guard_floor_is_11_01_01():
    assert P.GUARD_FLOOR == D(2026, 11, 1, 1)
    for s_ in (D(2026, 11, 1), D(2026, 11, 1, 0), D(2026, 10, 31, 23)):
        with _raises(P.ScoreRefused):
            with P.p18_window("/x", s_, D(2026, 11, 2)):
                pass
    with P.p18_window("/x", D(2026, 11, 1, 1), D(2026, 11, 2)):
        assert L.FWD_START == D(2026, 11, 1, 1)
    assert L.FWD_ACTIVE is None


def test_lead_hour_is_loaded_before_the_clock_and_clamped_to_the_floor():
    assert P.window_hours(D(2026, 11, 6), D(2026, 11, 10)) == (D(2026, 11, 5, 23), D(2026, 11, 10, 1))
    assert P.window_hours(D(2026, 11, 1, 1, 30), D(2026, 11, 5))[0] == D(2026, 11, 1, 1)  # clamped: the 00 hour is never loaded
    assert P.window_hours(D(2026, 11, 1, 0, 10), D(2026, 11, 5))[0] == D(2026, 11, 1, 1)
    assert "first 120 s" in P.BAD_HOUR_NOTE


def test_bad_hour_reason_is_printed():
    with tmp() as td:
        write_log(td, days(5, 20))
        r = P.score(P.load_decisions(td, CLOCK), CLOCK, D(2026, 11, 8), fake_price(pnl_wave), 0)
        assert r["bad_hour_note"] == P.BAD_HOUR_NOTE
        assert "bad_hour_excluded" in P.write(Path(td), r) and "lead hour" in (Path(td) / P.OUT_MD).read_text()


def test_test_window_only_inside_exploration_hours():
    for s_, e_ in ((D(2026, 10, 16, 23), D(2026, 10, 17, 1)), (D(2026, 10, 17), D(2026, 10, 18)), (D(2026, 10, 24), D(2026, 10, 25)), (D(2026, 10, 25), D(2026, 11, 1, 1)),
                   (D(2026, 11, 1), D(2026, 11, 2)), (D(2026, 11, 2), D(2026, 11, 3)), (D(2026, 10, 13, 23), D(2026, 10, 14, 2)), (D(2026, 10, 14, 0, 30), D(2026, 10, 15))):
        with _raises(P.ScoreRefused):
            with P.test_window("/x", s_, e_):
                pass
    with P.test_window("/x", D(2026, 10, 14), D(2026, 10, 17)):
        assert (L.FWD_ACTIVE, L.FWD_START, L.FWD_END) == ("/x", D(2026, 10, 14), D(2026, 10, 17))
    assert L.FWD_ACTIVE is None
    with _raises(P.ScoreRefused):  # default_load with test hours outside the exploration range refuses too
        P.default_load("/x", D(2026, 10, 15), D(2026, 10, 18), "/nope", lambda ks: {}, Path("/nonexistent"), test_hours=(D(2026, 10, 16), D(2026, 10, 18)))
    assert L.FWD_ACTIVE is None


def test_dry_real_layout_refuses_real_clock_and_sealed_hours(monkeypatch):
    for clock, win in ((P.EARLIEST_CLOCK, (D(2026, 10, 14), D(2026, 10, 15))), (D(2026, 11, 6), (D(2026, 10, 14), D(2026, 10, 15))), (D(2026, 10, 17), (D(2026, 10, 17), D(2026, 10, 18))),
                       (D(2026, 10, 16), (D(2026, 10, 16), D(2026, 10, 18))), (D(2026, 10, 13), (D(2026, 10, 14), D(2026, 10, 15)))):
        with _raises(P.ScoreRefused):
            P.dry_real_layout("/x", clock, win[0], win[1], "/nope")


def test_dry_real_layout_cli_needs_all_flags_and_prints_counts_only(monkeypatch, capsys):
    for argv in (["--test-window", "2026-10-14T00:00:00Z", "2026-10-15T00:00:00Z"], ["--dry-real-layout", "--counts-only"], ["--dry-real-layout", "--test-window", "2026-10-14T00:00:00Z", "2026-10-15T00:00:00Z"],
                 ["--counts-only", "--test-window", "2026-10-14T00:00:00Z", "2026-10-15T00:00:00Z", "--backfill-root", "/x"]):
        with pytest.raises(SystemExit):
            P.main(["--log-root", "/x", "--clock", "2026-10-14T00:00:00Z", *argv])
    # sealed window through main: refused (rc 6), nothing loaded
    assert P.main(["--log-root", "/x", "--clock", "2026-10-17T00:00:00Z", "--counts-only", "--dry-real-layout", "--test-window", "2026-10-17T00:00:00Z", "2026-10-18T00:00:00Z", "--backfill-root", "/x"]) == 6
    # a synthetic layout inside the exploration hours: counts only, no money fields
    from tools import test_dbc_fwd as T
    import subprocess
    monkeypatch.setattr(L, "SWAP3_IS_HOOK", L.SWAP3_IS_HOOK)
    monkeypatch.setattr(L, "FOB_EVIDENCE", L.FOB_EVIDENCE)
    monkeypatch.setattr(L, "CFG_THR", dict(L.CFG_THR))
    monkeypatch.setattr(L, "CFG_XFER", set(L.CFG_XFER))
    monkeypatch.setattr(L, "CFG_UNMAPPED", set(L.CFG_UNMAPPED))
    delta = int(D(2026, 10, 15).timestamp()) - T.T0
    with tmp() as td:
        root = Path(td) / "bf"
        root.mkdir()
        rows = T.synth_rows()
        for h in range(3):
            lo, hi = (T.T0 + 3600 * h) * 1000, (T.T0 + 3600 * (h + 1)) * 1000
            lines = [json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour", "hour": h}, separators=(",", ":"))]
            for r in rows:
                if lo <= r["t_recv_ms"] < hi:
                    lines.append(json.dumps({**r, "config": CFG_NAMES.get(r["config"], r["config"]), **({"block_time": r["block_time"] + delta} if "block_time" in r else {}), "t_recv_ms": r["t_recv_ms"] + delta * 1000}, separators=(",", ":")))
            lines.append(json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour_end"}, separators=(",", ":")))
            name = root / f"tape-2026-10-15T{h:02d}.jsonl"
            name.write_text("\n".join(lines) + "\n")
            subprocess.run(["zstd", "-q", "--rm", "-f", str(name), "-o", str(name) + ".zst"], check=True)
        bm = Path(td) / "base.json"
        bm.write_text(json.dumps({c: {"thr": 85 * 10**9, "transfer_fee_bps": 0} for c in CFG_NAMES.values()}))
        rc = P.main(["--log-root", "/x", "--clock", "2026-10-15T00:00:00Z", "--counts-only", "--dry-real-layout", "--test-window", "2026-10-15T00:00:00Z", "2026-10-15T03:00:00Z",
                     "--backfill-root", str(root), "--base-map", str(bm)])
        out = capsys.readouterr().out
        assert rc == 0
        j = json.loads(out[out.index("{\n"):])  # the loader logs per-hour row counts first
        assert j["test_only"] and j["outcome_fields_read"] is False and j["pnl_printed"] is False and j["good_hours"] == 3 and j["n_index_keys"] > 0 and j["n_items_resolved"] > 0
        for bad in ("pnl", "mean", "total", "verdict", "fills"):
            assert not [k for k in j if bad in k and k != "pnl_printed"] and bad not in json.dumps(j["resolve_counts"])
    assert L.FWD_ACTIVE is None


def test_counts_only_test_exploration_mode():
    with tmp() as td:
        log = DecisionLog(td)
        t = D(2026, 10, 15, 3).timestamp()
        for i in range(3):
            log.append(F.BOOK, int((t + i) * 1000), {"type": "decision", "key": f"e{i}", "block_time": t + i})
        with _raises(P.ScoreRefused):
            P.counts_only(td, D(2026, 10, 14))  # without the test switch the clock floor holds
        c = P.counts_only(td, D(2026, 10, 14), test_exploration=True)
        assert c["n_decisions"] == 3 and c["by_day"] == {"2026-10-15": 3}
    with tmp() as td:
        log = DecisionLog(td)
        log.append(F.BOOK, 1, {"type": "decision", "key": "late", "block_time": D(2026, 10, 25).timestamp()})
        with _raises(P.ScoreRefused):  # test mode never accepts a decision outside the exploration hours
            P.counts_only(td, D(2026, 10, 14), test_exploration=True)
