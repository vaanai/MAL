"""osb_recon latency-twin mode (QP-RULING-1116-recon-latency-twin section 1 (f)). Synthetic fixtures in tmp dirs only (no tape, no real book, no live path). Paper; nothing here says any book makes money."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import osb_board as B  # noqa: E402
import osb_recon as R  # noqa: E402
from tools import test_osb_board as TB  # noqa: E402
from tools import test_osb_recon as TR  # noqa: E402
from tools.test_osb_recon import snap_root  # noqa: E402,F401  (autouse fixture)

KEYS = ("h1", "h2", "h3", "s1", "s2", "s3", "s4")


@pytest.fixture(autouse=True)
def entries_after_s_start(monkeypatch):
    monkeypatch.setattr(TR, "T", TB.ms(15, 0))  # every entry after the S window start 2026-11-14T22:14:45Z


def rows_for(pids, first=0):
    rows = []
    for k, pid in enumerate(pids, first):
        rows += TB.tp(pid, TR.entry_ms(k), 5_000_000)
    return rows


def book_a(tmp, pids, bid="osb-a"):
    """book A: tp trades plus decision rows (as test_osb_recon.sim_book, with a chosen book id)"""
    dec = []
    for k, pid in enumerate(pids):
        t0 = TR.entry_ms(k)
        dec.append({"kind": "entry", "ms": t0, "order_id": pid, "pool": "P_" + pid, "meta": {"mint": "M_" + pid, "signal_slot": 1000 + k, "signal_t_recv_ms": t0 - 800}})
        dec.append({"kind": "exit", "ms": t0 + 2 * TB.H, "order_id": pid, "pool": "P_" + pid, "reason": "tp_1.5x"})
    mp, out = TB.make_book(tmp, bid, rows_for(pids))
    d = out / "runs" / "0001" / "decisions" / "l1-floor-v2"
    d.mkdir(parents=True)
    (d / "2026-11-15.jsonl").write_text("".join(json.dumps(r) + "\n" for r in dec))
    return mp


def lt_world(tmp, n=10, twin_rows=None, twin_over=None, k=12, ms_=3100, a_over=None):
    pids = ["m%d" % i for i in range(n)]
    mp = book_a(tmp, pids)
    over = {"twin_of": "osb-a", "pricing_lat_entry_s": 3.01}
    over.update(twin_over or {})
    tm, tout = TB.make_book(tmp, "osb-a-lt-a", twin_rows if twin_rows is not None else rows_for(pids), **over)
    live = []
    for i, pid in enumerate(pids):
        o = TR.live_order(pid, i)
        o["decision_slot"], o["landed_slot"], o["landed_ms"] = 1, 1 + k, o["decision_ms"] + ms_
        live.append(o)
    return mp, tm, tout, live


def run_both(tmp, mp, tm, live, *extra, lt=True):
    ex = TR.write_extract(tmp / "extract", live)
    out = tmp / "recon"
    base = ["--extract-dir", str(ex), "--manifest", str(mp), "--out-dir", str(out), "--now-ms", str(TB.END + 60_000)]
    rc_a = R.main(base, roots=(str(tmp),), out_roots=(str(out),))
    rc_t = R.main(base + ["--lt-twin-manifest", str(tm), *extra], roots=(str(tmp),), out_roots=(str(out),)) if lt else None
    return rc_a, rc_t, out


def load_lt(out):
    return json.loads((out / "recon-lt-a.json").read_text())


def test_book_a_s4_fails_while_twin_s4_passes_and_h_bands_equal_a(tmp_path):
    mp, tm, _, live = lt_world(tmp_path)
    rc_a, rc_t, out = run_both(tmp_path, mp, tm, live)
    assert rc_a == 0 and rc_t == 0
    a, t = json.loads((out / "recon.json").read_text()), load_lt(out)
    assert a["s4"]["status"] == "FAIL" and a["s4"]["p50_ms"] == 3100 and a["status"] == "FAIL"  # book A's S4 cannot be met by a bot landing 12 steps after the decision
    assert t["s4"]["status"] == "PASS" and t["s4"]["landing_k_p50"] == 12 and t["s4"]["landing_k_p90"] == 12 and t["s4"]["p50_ms"] == 3100 and t["s4"]["p90_ms"] == 3100
    assert (R.LT_K_P50, R.LT_K_P90) == (14, 19) and "band_p50_ms" not in t["s4"] and t["s4"]["ms_band"].startswith("none")
    assert [t[x]["status"] for x in KEYS] == ["PASS"] * 7 and t["status"] == "PASS" and t["schema"] == "osb_recon_lt.v1" and t["twin_book_id"] == "osb-a-lt-a"
    for x in ("h1", "h2", "h3"):
        assert t[x] == a[x]  # H1..H3 are book A's over book A's window
    assert t["h_window"] == a["window"] and t["book_a"]["status"] == "FAIL" and t["book_a"]["bands"]["s4"] == "FAIL"
    assert t["window"]["from_utc"] == R.LT_S_START_UTC
    md = (out / "recon-lt-a.md").read_text()
    assert "S4-twin" in md and "osb-a-lt-a" in md and "FAIL" in md  # book A's status is still printed


def test_outputs_are_separate_and_recon_json_is_never_written_in_twin_mode(tmp_path):
    mp, tm, _, live = lt_world(tmp_path)
    ex = TR.write_extract(tmp_path / "extract", live)
    out = tmp_path / "recon"
    rc = R.main(["--extract-dir", str(ex), "--manifest", str(mp), "--out-dir", str(out), "--now-ms", str(TB.END + 60_000), "--lt-twin-manifest", str(tm)], roots=(str(tmp_path),), out_roots=(str(out),))
    assert rc == 0 and (out / "recon-lt-a.json").exists() and (out / "recon-lt-a.md").exists() and not (out / "recon.json").exists() and not (out / "recon.md").exists()
    before = (out / "recon-lt-a.json").read_text()
    rc2, _, _ = run_both(tmp_path, mp, tm, live, lt=False)  # book A's own recon still produced, and it leaves the twin files alone
    assert rc2 == 0 and (out / "recon.json").exists() and (out / "recon-lt-a.json").read_text() == before


def test_exit_before_entry_landing_is_no_sim_and_stays_out_of_s1_s2(tmp_path):
    pids = ["m%d" % i for i in range(11)]
    trows = rows_for(pids[:10])
    trows += [TB.op("m10", TR.entry_ms(10))] + [{"kind": "excluded", "position_id": "m10", "stake_sol": s, "exclusion_reason": "exit_before_entry_landing"} for s in (0.10, 0.25)]
    mp = book_a(tmp_path, pids)
    tm, _ = TB.make_book(tmp_path, "osb-a-lt-a", trows, twin_of="osb-a", pricing_lat_entry_s=3.01)
    live = []
    for i, pid in enumerate(pids):
        o = TR.live_order(pid, i)
        o["decision_slot"], o["landed_slot"], o["landed_ms"] = 1, 13, o["decision_ms"] + 3100
        live.append(o)
    _, rc, out = run_both(tmp_path, mp, tm, live)
    assert rc == 0
    t = load_lt(out)
    row = next(r for r in t["rows"] if r.get("order_id") == "Lm10")
    assert row["class"] == "matched" and row["no_sim_price"] == "other" and row["entry_slip_pp"] is None and row["net_sim_sol"] is None
    assert t["excluded_reason_counts"] == {"exit_before_entry_landing": 1} and t["no_sim_price_counts"]["other"] == 1
    assert t["s1"]["n"] == 10 and t["s2"]["n"] == 10  # the excluded row is outside S1 and S2
    assert t["s4"]["n"] == 11  # landing steps belong to the live bot: still counted for S4-twin


def test_twin_s_bands_are_insufficient_below_ten_and_status_is_insufficient(tmp_path):
    mp, tm, _, live = lt_world(tmp_path, n=9)
    _, rc, out = run_both(tmp_path, mp, tm, live)
    t = load_lt(out)
    assert rc == 0 and [t[x]["status"] for x in ("s1", "s2", "s3", "s4")] == ["INSUFFICIENT"] * 4 and t["status"] == "INSUFFICIENT"


@pytest.mark.parametrize("k, expect", [(14, "PASS"), (15, "FAIL")])
def test_s4_twin_p50_edge(tmp_path, k, expect):
    mp, tm, _, live = lt_world(tmp_path, k=k)
    _, rc, out = run_both(tmp_path, mp, tm, live)
    t = load_lt(out)
    assert rc == 0 and t["s4"]["status"] == expect and t["status"] == ("PASS" if expect == "PASS" else "FAIL")


def test_s4_twin_p90_edge(tmp_path):
    for k90, expect in ((19, "PASS"), (20, "FAIL")):
        sub = tmp_path / ("k%d" % k90)
        sub.mkdir()
        mp, tm, _, live = lt_world(sub)
        for o in live[-2:]:  # nearest-rank p90 of 10 is the 9th value: two high values put it on k90
            o["landed_slot"] = o["decision_slot"] + k90
        _, rc, out = run_both(sub, mp, tm, live)
        t = load_lt(out)
        assert rc == 0 and t["s4"]["landing_k_p90"] == k90 and t["s4"]["status"] == expect


def test_any_failing_band_fails_the_twin_status_and_a_failing_h3_is_books_a(tmp_path):
    mp, tm, _, live = lt_world(tmp_path)
    live[0]["sell_reason_bridge"] = "time_stop"
    live[0]["led_close_reason"] = "time_stop"
    live[0]["exit_reason"] = "time_stop"
    _, rc, out = run_both(tmp_path, mp, tm, live)
    t = load_lt(out)
    assert rc == 0 and t["h3"]["status"] == "FAIL" and t["status"] == "FAIL"


@pytest.mark.parametrize("over, why", [
    ({"twin_of": "osb-b"}, "twin_of"),
    ({"pricing_lat_entry_s": 4.085}, "latency value"),
    ({"pricing_lat_entry_s": 5.59}, "latency value"),
])
def test_refuses_the_wrong_twin_or_latency_value(tmp_path, capsys, over, why):
    mp, tm, _, live = lt_world(tmp_path, twin_over=over)
    _, rc, out = run_both(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a.json").exists()
    assert "refused" in capsys.readouterr().err


def test_refuses_a_twin_manifest_without_the_latency_value_or_a_plain_book(tmp_path):
    pids = ["m%d" % i for i in range(10)]
    mp = book_a(tmp_path, pids)
    tm, _ = TB.make_book(tmp_path, "osb-a-lt-a", rows_for(pids), twin_of="osb-a", gap_exclusion="hold")  # a gap twin, not a latency twin
    live = [TR.live_order(p, i) for i, p in enumerate(pids)]
    _, rc, out = run_both(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a.json").exists()


def test_refuses_a_twin_that_differs_from_book_a_in_another_key(tmp_path):
    mp, tm, _, live = lt_world(tmp_path, twin_over={"send_lamports": 170000})
    _, rc, out = run_both(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a.json").exists()


def test_refuses_a_differing_decision_md5_at_the_common_last_hour(tmp_path, capsys):
    mp, tm, tout, live = lt_world(tmp_path)
    p = tout / "md5-by-hour.jsonl"
    rows = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    for r in rows:
        r["decision_md5"] = "e"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    _, rc, out = run_both(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a.json").exists() and "md5" in capsys.readouterr().err


def test_refuses_when_no_md5_hour_is_common(tmp_path):
    mp, tm, tout, live = lt_world(tmp_path)
    (tout / "md5-by-hour.jsonl").write_text("")
    _, rc, out = run_both(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a.json").exists()


def test_refuses_when_book_a_is_not_osb_a(tmp_path):
    pids = ["m%d" % i for i in range(10)]
    mp = book_a(tmp_path, pids, bid="A")
    tm, _ = TB.make_book(tmp_path, "osb-a-lt-a", rows_for(pids), twin_of="A", pricing_lat_entry_s=3.01)
    live = [TR.live_order(p, i) for i, p in enumerate(pids)]
    _, rc, out = run_both(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a.json").exists()


def test_both_twin_modes_at_once_are_refused(tmp_path):
    mp, tm, _, live = lt_world(tmp_path)
    _, rc, out = run_both(tmp_path, mp, tm, live, "--twin-manifest", str(tm))
    assert rc == 2 and not (out / "recon-lt-a.json").exists()


def test_s_window_end_at_a_differing_start_line_cuts_the_s_set_not_the_h_window(tmp_path):
    mp, tm, _, live = lt_world(tmp_path, n=12)
    end = B.utc(TR.entry_ms(10) - 30_000)[:19] + "Z"  # between entries 9 and 10: 10 trades are inside the S window
    _, rc, out = run_both(tmp_path, mp, tm, live, "--lt-s-end-utc", end)
    t = load_lt(out)
    assert rc == 0 and t["window"]["to_utc"] == end and t["s_window_end_input"] == end
    assert t["s1"]["n"] == 10 and t["s4"]["n"] == 10 and t["lt_counts_s_window"]["matched"] == 10
    assert t["counts"]["matched"] == 12 and t["h_window"]["to_utc"] != end  # book A's window and counts are unchanged
    _, rc2, out2 = run_both(tmp_path / "open", *_open(tmp_path / "open", 12))
    assert load_lt(out2)["s1"]["n"] == 12 and load_lt(out2)["s_window_end_input"] is None


def _open(tmp, n):
    tmp.mkdir()
    mp, tm, _, live = lt_world(tmp, n=n)
    return mp, tm, live


def test_s_window_start_drops_trades_before_the_am10_start_line(tmp_path, monkeypatch):
    monkeypatch.setattr(TR, "T", TB.ms(14, 13))  # entries 13:00 on 11-14, before the S start; book A's window starts 11-14T12
    mp, tm, _, live = lt_world(tmp_path)
    _, rc, out = run_both(tmp_path, mp, tm, live)
    t = load_lt(out)
    assert rc == 0 and t["lt_counts_s_window"]["matched"] == 0 and t["counts"]["matched"] == 10 and t["s1"]["status"] == "INSUFFICIENT" and t["status"] == "INSUFFICIENT"


@pytest.mark.parametrize("end", ["2026-11-14T22:14:45Z", "2026-11-14T22:00:00Z", "yesterday"])
def test_s_window_end_not_after_start_or_malformed_is_refused(tmp_path, end):
    mp, tm, _, live = lt_world(tmp_path)
    _, rc, out = run_both(tmp_path, mp, tm, live, "--lt-s-end-utc", end)
    assert rc == 2 and not (out / "recon-lt-a.json").exists()


def test_twin_outputs_carry_no_forbidden_word(tmp_path, capsys):
    mp, tm, _, live = lt_world(tmp_path)
    _, rc, out = run_both(tmp_path, mp, tm, live)
    text = (out / "recon-lt-a.md").read_text() + capsys.readouterr().out  # the json is checked by check_output at write time (string values)
    assert rc == 0 and not any(w in text.lower() for w in B.FORBIDDEN)
