"""osb_recon: book A against a synthetic live extract. Fixtures in tmp dirs only (no tape, no real book, no live path). Paper; nothing here says any book makes money."""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import osb_board as B  # noqa: E402
import osb_recon as R  # noqa: E402
from tools import test_osb_board as TB  # noqa: E402

H = 3_600_000
FIXTURE_STRINGS = ("P_", "M_", "SIGFIX")
T = TB.ms(14, 13)  # first entry; the book starts 2026-11-14T12


@pytest.fixture(autouse=True)
def snap_root(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "SNAP_ROOT", str(tmp_path / "snap"))


def entry_ms(i):
    return T + i * 60_000


def sim_book(tmp, specs):
    """specs: [(pid, i, net, stake)]; A's ledger (tp trades at 0.10/0.25) plus its decision rows"""
    rows, dec = [], []
    for pid, i, net, _stake in specs:
        t0 = entry_ms(i)
        rows += TB.tp(pid, t0, net)
        dec.append({"kind": "entry", "ms": t0, "order_id": pid, "pool": "P_" + pid, "meta": {"mint": "M_" + pid, "signal_slot": 1000 + i, "signal_t_recv_ms": t0 - 800}})
        dec.append({"kind": "exit", "ms": t0 + 2 * H, "order_id": pid, "pool": "P_" + pid, "reason": "tp_1.5x"})
    mp, out = TB.make_book(tmp, "A", rows)
    d = out / "runs" / "0001" / "decisions" / "l1-floor-v2"
    d.mkdir(parents=True)
    (d / "2026-11-14.jsonl").write_text("".join(json.dumps(r) + "\n" for r in dec))
    return mp


def live_order(pid, i, stake=0.10, **kw):
    t0 = entry_ms(i)
    pos_price = None
    o = {"kind": "order", "order_id": "L" + pid, "has_host_entry": True, "pool": "P_" + pid, "trig_slot": 1000 + i, "trig_ms": t0 - 800, "decision_ms": t0, "stake_sol": stake, "mark0": 1e-8,
         "sent": True, "send_ms": t0 + 100, "stake_lamports": int(stake * 1e9), "refused_why": None, "refused_kind": None, "landed_ms": t0 + 1800, "landed_slot": 5, "decision_slot": 1, "buy_failed": False,
         "exit_reason": "tp_1.5x", "exit_ms": t0 + 2 * H, "has_host_exit": True, "sell_send_ms": t0 + 2 * H, "sell_reason_bridge": "tp_1.5x", "sell_landed_ms": t0 + 2 * H + 1800,
         "led_intent_ms": t0 + 100, "led_net_lamports": int(5_000_000 * stake / 0.25), "led_fees_lamports": 160_000, "led_rent_unreclaimed_lamports": 0, "led_close_ev": "exit", "led_close_reason": "tp_1.5x",
         "led_close_ms": t0 + 2 * H, "led_ok_buy": True, "led_estimated_rows": 0, "led_stake_lamports": int(stake * 1e9),
         "fill_spend_lamports": int(stake * 1e9), "fill_tokens_received": None, "fill_price_vs_quote_bps": 10, "fill_tokens_source": "meta", "fill_estimated": False}
    # live tokens chosen so the live price equals A's landing price (slip 0) unless the test moves it
    sp = R.sim_price({"landing": {"Qe": 18_100_000_000, "Be": 1_000_000_000_000}}, stake)
    o["fill_tokens_received"] = int(stake * 1e9 / (sp * 1000.0))
    o.update(kw)
    return o


def slip_tokens(stake, slip_pp):
    sp = R.sim_price({"landing": {"Qe": 18_100_000_000, "Be": 1_000_000_000_000}}, stake)
    return int(stake * 1e9 / (sp * (1 + slip_pp / 100.0) * 1000.0))


def write_extract(d, rows, as_of=TB.END):
    d.mkdir(parents=True, exist_ok=True)
    hdr = {"kind": "header", "schema": R.EXTRACT_SCHEMA, "since_ms": TB.ms(14, 12), "as_of_ms": as_of}
    body = "".join(json.dumps(r) + "\n" for r in [hdr] + rows).encode()
    (d / R.EXTRACT_NAME).write_bytes(body)
    (d / R.META_NAME).write_text(json.dumps({"extract_sha256": hashlib.sha256(body).hexdigest()}))
    return d


def recon(tmp, specs, live_rows, now=TB.END + 60_000, extract_mut=None):
    mp = sim_book(tmp, specs)
    ex = write_extract(tmp / "extract", live_rows)
    out = tmp / "recon"
    rc = R.main(["--extract-dir", str(ex), "--manifest", str(mp), "--out-dir", str(out), "--now-ms", str(now)], roots=(str(tmp),), out_roots=(str(out),))
    return rc, out


def load(out):
    return json.loads((out / "recon.json").read_text())


def by_reason(res):
    return res["counts_by_reason"]


def ten(tmp_extra=()):
    """ten matched, in-band pairs (n = 10 is S1's minimum)"""
    specs = [("m%d" % k, k, 5_000_000, 0.10) for k in range(10)]
    live = [live_order("m%d" % k, k) for k in range(10)]
    return specs, live


def test_each_class_and_reason(tmp_path):
    specs = [("m1", 0, 5_000_000, 0.10), ("s_lock", 1, 1, 0.1), ("s_lock2", 2, 1, 0.1), ("s_down", 3, 1, 0.1), ("s_exec", 4, 1, 0.1), ("s_nl", 5, 1, 0.1),
             ("s_edge", 6, 1, 0.1), ("s_edge11", 7, 1, 0.1), ("s_gap", 8, 1, 0.1), ("s_unx", 9, 1, 0.1), ("s_blk", 10, 1, 0.1)]
    live = [live_order("m1", 0), live_order("L1", 20),  # matched; live-only (no A entry)
            live_order("s_exec", 4, sent=False, refused_why="lane_full", led_ok_buy=False, led_close_ev=None),
            live_order("s_nl", 5, led_ok_buy=False, led_close_ev="writeoff", led_close_reason="buy_expired", landed_ms=None)]
    t = lambda i: entry_ms(i) - 800
    live += [{"kind": "gap_refusal", "t_ms": entry_ms(1), "pool": "P_s_lock", "trig_slot": 1001, "trig_ms": t(1), "reason": "feed_gap", "gap_kind": "host_down"},
             {"kind": "gap_refusal", "t_ms": entry_ms(2), "pool": "P_s_lock2", "trig_slot": 1002, "trig_ms": t(2), "reason": "pool_blocked_after_restart"},
             {"kind": "restart", "t_ms": entry_ms(3) + 5, "failed": False, "down_from_ms": t(3) - 1000, "down_to_ms": t(3) + 1000, "shadow_complete": True},
             # timing edge: a refusal on the same pool at another slot, exactly 10 s away (inside) and 10.001 s away (outside)
             {"kind": "gap_refusal", "t_ms": 1, "pool": "P_s_edge", "trig_slot": 5, "trig_ms": t(6) + 10_000, "reason": "feed_gap", "gap_kind": "data_loss"},
             {"kind": "gap_refusal", "t_ms": 1, "pool": "P_s_edge11", "trig_slot": 5, "trig_ms": t(7) + 10_001, "reason": "feed_gap", "gap_kind": "data_loss"},
             {"kind": "gap_refusal", "t_ms": entry_ms(8), "pool": "P_s_gap", "trig_slot": 1008, "trig_ms": t(8), "reason": "feed_gap", "gap_kind": "data_loss"},
             {"kind": "gap_refusal", "t_ms": entry_ms(10), "pool": "P_s_blk", "trig_slot": 1010, "trig_ms": t(10), "reason": "pool_blocked_after_gap"}]
    rc, out = recon(tmp_path, specs, live)
    assert rc == 0
    res = load(out)
    c = by_reason(res)
    assert c == {"live_only:superset_violation": 1, "sim_only:restart_lockout": 2, "sim_only:host_down_no_row": 1, "sim_only:executor:lane_full": 1, "sim_only:buy_not_landed": 1,
                 "sim_only:timing_edge": 1, "sim_only:feed_gap_mismatch": 1, "sim_only:unexplained": 3}
    assert res["counts"] == {"matched": 1, "live_only": 1, "sim_only": 10}
    assert res["h1"]["status"] == "FAIL" and res["h2"]["status"] == "FAIL" and res["status"] == "FAIL"
    # the +-10 s edge: 10.000 s is a timing edge, 10.001 s is not
    unx = {r["tid"] for r in res["rows"] if r["reason"] == "unexplained"}
    assert len(unx) == 3
    assert any(r["class"] == "matched" and r["order_id"] == "Lm1" for r in res["rows"])


def test_feed_gap_mismatch_counts_for_h2_and_host_down_does_not(tmp_path):
    specs = [("a", 0, 1, 0.1), ("b", 1, 1, 0.1)]
    live = [{"kind": "gap_refusal", "t_ms": 1, "pool": "P_a", "trig_slot": 1000, "trig_ms": entry_ms(0) - 800, "reason": "feed_gap", "gap_kind": "host_down"},
            {"kind": "gap_refusal", "t_ms": 1, "pool": "P_b", "trig_slot": 1001, "trig_ms": entry_ms(1) - 800, "reason": "feed_gap", "gap_kind": "data_loss"}]
    _, out = recon(tmp_path, specs, live)
    res = load(out)
    assert by_reason(res) == {"sim_only:feed_gap_mismatch": 1, "sim_only:restart_lockout": 1}
    assert res["h2"]["status"] == "FAIL" and res["h2"]["unexplained_by_day"] == {"2026-11-14": 1}


def test_all_in_band_with_ten_matched_passes(tmp_path):
    specs, live = ten()
    _, out = recon(tmp_path, specs, live)
    res = load(out)
    assert res["counts"]["matched"] == 10
    assert [res[k]["status"] for k in ("h1", "h2", "h3", "s1", "s2", "s3", "s4")] == ["PASS"] * 7 and res["status"] == "PASS"
    assert abs(res["s1"]["median_slip_pp"]) < 1e-3 and res["s4"]["p50_ms"] == 1800


def test_insufficient_below_ten(tmp_path):
    specs, live = ten()
    _, out = recon(tmp_path, specs[:9], live[:9])
    res = load(out)
    assert res["s1"]["status"] == "INSUFFICIENT" and res["status"] == "INSUFFICIENT" and res["h1"]["status"] == "PASS"


@pytest.mark.parametrize("mut,band_key", [
    (lambda o: o.update(fill_tokens_received=slip_tokens(0.10, 6.0)), "s1"),          # median slip +6 pp
    (lambda o: o.update(led_net_lamports=-5_000_000), "s2"),                        # live net -5 pp vs sim +2 pp, every trade
    (lambda o: o.update(landed_ms=o["decision_ms"] + 3500), "s4"),                    # p50 3.5 s
    (lambda o: o.update(led_rent_unreclaimed_lamports=2_039_280), "s3"),              # unreclaimed rent
    (lambda o: o.update(exit_reason="time_24h"), "h3"),                               # exit reason differs, uncoded
])
def test_band_fail(tmp_path, mut, band_key):
    specs, live = ten()
    for o in live:
        mut(o)
    _, out = recon(tmp_path, specs, live)
    res = load(out)
    assert res[band_key]["status"] == "FAIL" and res["status"] == "FAIL"


def test_s1_beyond_8pp_count(tmp_path):
    specs, live = ten()
    for o in live[:3]:
        o["fill_tokens_received"] = slip_tokens(0.10, 12.0)
    _, out = recon(tmp_path, specs, live)
    assert load(out)["s1"]["status"] == "FAIL" and load(out)["s1"]["n_beyond"] == 3


def test_h3_coded_exception_host_down_at_take_profit(tmp_path):
    specs, live = ten()
    o = live[0]
    o.update(has_host_exit=False, exit_reason=None, sell_reason_bridge="time_24h", exit_ms=None)
    live.append({"kind": "restart", "t_ms": 1, "failed": False, "down_from_ms": entry_ms(0) + H, "down_to_ms": entry_ms(0) + 3 * H, "shadow_complete": False})
    _, out = recon(tmp_path, specs, live)
    res = load(out)
    assert res["h3"]["n_coded"] == 1 and res["h3"]["status"] == "PASS"


def test_no_rung_left_out_of_s1_s2(tmp_path):
    specs, live = ten()
    for o in live[:2]:  # two 0.03 trades: A has no 0.03 rung
        o.update(stake_lamports=30_000_000, led_stake_lamports=30_000_000, stake_sol=0.03)
    _, out = recon(tmp_path, specs, live)
    res = load(out)
    assert res["counts"]["matched"] == 10 and res["s1"]["n"] == 8 and res["s1"]["status"] == "INSUFFICIENT"
    assert sum(1 for r in res["rows"] if r.get("no_rung")) == 2 and res["discordance"]["no_rung"] == 2


def test_live_failure_wilson_and_not_pooled(tmp_path):
    assert R.wilson(0, 0) == (None, None)
    lo, hi = R.wilson(3, 20)
    assert 0.05 < lo < 0.15 < hi < 0.4
    specs, live = ten()
    live.append(live_order("m0", 0, order_id="Lx", pool="P_zz", trig_ms=entry_ms(40), led_ok_buy=False, led_close_ev="writeoff", landed_ms=None))
    _, out = recon(tmp_path, specs, live)
    lf = load(out)["live_failure"]
    assert lf["n_sent"] == 11 and lf["n_not_filled"] == 1 and lf["flat_fail_assumed"] == 0.15 and "not combined" in lf["note"]


def test_output_allow_list_and_stdout(tmp_path, capsys):
    specs, live = ten()
    live.append({"kind": "gap_refusal", "t_ms": 1, "pool": "P_zzz", "trig_slot": 1, "trig_ms": 1, "reason": "feed_gap", "gap_kind": "host_down"})
    live.append(live_order("odd", 30, sent=False, refused_why="stale_slot_price_X!", led_ok_buy=False))
    live.append(live_order("odd2", 31))
    _, out = recon(tmp_path, specs + [("odd", 30, 1, 0.1)], live)
    text = (out / "recon.json").read_text() + (out / "recon.md").read_text() + capsys.readouterr().out
    for s in FIXTURE_STRINGS:
        assert s not in text
    res = load(out)
    allowed = {"order_id", "tid", "class", "reason", "entry_utc", "stake_sol", "no_rung", "entry_slip_pp", "landing_ms", "exit_reason_live", "exit_reason_sim", "exit_dt_ms", "exit_coded",
               "net_live_sol", "net_sim_sol", "net_live_pp", "net_sim_pp", "fees_live_pp", "fees_model_pp", "rent_unreclaimed"}
    assert all(set(r) <= allowed for r in res["rows"])
    assert any(r["reason"] == "executor:other" for r in res["rows"])  # a live word holding a forbidden string never reaches the output
    assert "|" in (out / "recon.md").read_text()


def test_refusals(tmp_path):
    specs, live = ten()
    mp = sim_book(tmp_path, specs)
    ex = write_extract(tmp_path / "extract", live)
    out = tmp_path / "recon"
    base = ["--manifest", str(mp), "--now-ms", str(TB.END)]
    kw = dict(roots=(str(tmp_path),), out_roots=(str(out),))
    assert R.main(["--extract-dir", "/data2/mal/scratch/floor-open-sim/live-extract", "--out-dir", str(out), *base], **kw) == 2  # a real root under pytest
    assert R.main(["--extract-dir", str(ex), "--out-dir", "/data2/mal/scratch/floor-open-sim/recon", *base], **kw) == 2
    assert R.main(["--extract-dir", str(ex), "--out-dir", str(tmp_path / "elsewhere"), *base], **kw) == 2  # outside the out roots
    (ex / R.EXTRACT_NAME).write_bytes((ex / R.EXTRACT_NAME).read_bytes() + b"\n{}\n")
    assert R.main(["--extract-dir", str(ex), "--out-dir", str(out), *base], **kw) == 2  # the meta sha256 does not match
    ex2 = write_extract(tmp_path / "extract2", live)
    body = (ex2 / R.EXTRACT_NAME).read_text().replace(R.EXTRACT_SCHEMA, "other")
    (ex2 / R.EXTRACT_NAME).write_text(body)
    (ex2 / R.META_NAME).write_text(json.dumps({"extract_sha256": hashlib.sha256(body.encode()).hexdigest()}))
    assert R.main(["--extract-dir", str(ex2), "--out-dir", str(out), *base], **kw) == 2  # another schema
    assert not (out / "recon.json").exists()


def test_lock_is_held_refuses(tmp_path):
    import fcntl
    specs, live = ten()
    mp = sim_book(tmp_path, specs)
    ex = write_extract(tmp_path / "extract", live)
    out = tmp_path / "recon"
    out.mkdir()
    with open(out / ".osb-recon.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert R.main(["--extract-dir", str(ex), "--manifest", str(mp), "--out-dir", str(out), "--now-ms", str(TB.END)], roots=(str(tmp_path),), out_roots=(str(out),)) == 2


def test_atomic_outputs_no_tmp(tmp_path):
    specs, live = ten()
    _, out = recon(tmp_path, specs, live)
    assert sorted(p for p in os.listdir(out) if not p.startswith(".")) == ["recon.json", "recon.md"]
    assert not [p for p in os.listdir(out) if ".tmp" in p]
