"""osb_recon latency-twin mode, S window 2 (QP-RULING-1116-recon-latency-twin-ADD1; spec sha256 c022ebc4ee9f43a9cd736bf2aa0e7f664924a1e019ac479e6558980d527bd27c).
Synthetic fixtures in tmp dirs only (no tape, no real book, no live path). Paper; nothing here says any book makes money or that the sim matches live."""
import hashlib
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
from tools.test_osb_recon_lt import KEYS, rows_for  # noqa: E402

W2_END = TB.ms(17, 2)  # tape end of the fixture books, after every exit
W2_START = R.LT_W2_START_UTC


@pytest.fixture(autouse=True)
def entries_after_w2_start(monkeypatch):
    monkeypatch.setattr(TR, "T", TB.ms(16, 21))  # entry k is at 21:00 + k min on 2026-11-16, after the window 2 start 20:34:44; negative k is before it


def w2_world(tmp, n25=10, early10=0, late10=0, twin_bid="osb-a-lt-a", twin_over=None, pin=True, monkeypatch=None):
    """n25 matched 0.25 buys inside window 2; early10 0.10 buys before the start line; late10 0.10 buys inside the window (after the 0.25 ones)"""
    pids = ["e%d" % i for i in range(early10)] + ["m%d" % i for i in range(n25)] + ["x%d" % i for i in range(late10)]
    idx = [-30 + i for i in range(early10)]  # early rows 20:30.. ; the 0.25 rows 21:00.. ; late 0.10 rows after them
    idx = idx + list(range(n25)) + [n25 + i for i in range(late10)]
    dec, rows = [], []
    for k, pid in zip(idx, pids):
        t0 = TR.entry_ms(k)
        dec.append({"kind": "entry", "ms": t0, "order_id": pid, "pool": "P_" + pid, "meta": {"mint": "M_" + pid, "signal_slot": 1000 + k, "signal_t_recv_ms": t0 - 800}})
        dec.append({"kind": "exit", "ms": t0 + 2 * TB.H, "order_id": pid, "pool": "P_" + pid, "reason": "tp_1.5x"})
        rows += TB.tp(pid, t0, 5_000_000)
    mp, out = TB.make_book(tmp, "osb-a", rows, end_ms=W2_END)
    d = out / "runs" / "0001" / "decisions" / "l1-floor-v2"
    d.mkdir(parents=True)
    (d / "2026-11-16.jsonl").write_text("".join(json.dumps(r) + "\n" for r in dec))
    over = {"twin_of": "osb-a", "pricing_lat_entry_s": 3.01}
    over.update(twin_over or {})
    tm, tout = TB.make_book(tmp, twin_bid, rows, end_ms=W2_END, **over)
    live = []
    for k, pid in zip(idx, pids):
        o = TR.live_order(pid, k, stake=0.10 if pid[0] in "ex" else 0.25)
        o["decision_slot"], o["landed_slot"], o["landed_ms"] = 1, 13, o["decision_ms"] + 3100
        live.append(o)
    if pin and monkeypatch is not None:
        monkeypatch.setattr(R, "LT_W2_TWIN_MANIFEST_SHA256", hashlib.sha256(tm.read_bytes()).hexdigest())
    return mp, tm, tout, live


def run_w2(tmp, mp, tm, live, *extra, w2=True, out_name="recon"):
    ex = TR.write_extract(tmp / "extract", live, as_of=W2_END)
    out = tmp / out_name
    base = ["--extract-dir", str(ex), "--manifest", str(mp), "--out-dir", str(out), "--now-ms", str(W2_END + 60_000)]
    rc = R.main(base + ["--lt-twin-manifest", str(tm)] + (["--lt-window2"] if w2 else []) + list(extra), roots=(str(tmp),), out_roots=(str(out),))
    return rc, out


def load_w2(out):
    return json.loads((out / "recon-lt-a-w2.json").read_text())


def test_constants_are_the_addendum_values():
    assert R.LT_W2_START_UTC == "2026-11-16T20:34:44Z" and R.LT_W2_STAKE == 0.25 and R.LT_W2_TWIN_ID == "osb-a-lt-a"
    assert R.LT_W2_TWIN_MANIFEST_SHA256 == "811229cb20f109124966ba40603a00b7d9e4c61df6839b51ddd3acfa94b65883"
    assert (R.LT_W2_JSON, R.LT_W2_MD) == ("recon-lt-a-w2.json", "recon-lt-a-w2.md") and R.LT_S_START_UTC == "2026-11-14T22:14:45Z"


def test_window2_all_025_ten_trades_bands_and_separate_outputs(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, monkeypatch=monkeypatch)
    rc, out = run_w2(tmp_path, mp, tm, live)
    assert rc == 0
    t = load_w2(out)
    assert t["schema"] == "osb_recon_lt_w2.v1" and t["twin_book_id"] == "osb-a-lt-a" and t["window"]["from_utc"] == W2_START and t["s_stake_sol"] == 0.25
    assert t["off_stake_live_buys"] == {"n": 0, "by_stake_sol": {}}
    assert [t[x]["status"] for x in KEYS] == ["PASS"] * 7 and t["status"] == "PASS"
    assert t["s1"]["n"] == 10 and t["s2"]["n"] == 10 and t["s3"]["n"] == 10 and t["s4"]["n"] == 10 and t["s4"]["landing_k_p50"] == 12
    assert (out / "recon-lt-a-w2.md").exists() and not (out / "recon-lt-a.json").exists() and not (out / "recon.json").exists() and not (out / "recon-lt-a.md").exists()
    md = (out / "recon-lt-a-w2.md").read_text()
    assert "S window 2" in md and "0.25" in md and not any(w in md.lower() for w in B.FORBIDDEN)


def test_window1_and_book_a_outputs_are_untouched_by_window2(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, monkeypatch=monkeypatch)
    rc1, out = run_w2(tmp_path, mp, tm, live, w2=False)  # window 1 twin mode
    assert rc1 == 0 and (out / "recon-lt-a.json").exists()
    ex = TR.write_extract(tmp_path / "extract", live, as_of=W2_END)
    rc_a = R.main(["--extract-dir", str(ex), "--manifest", str(mp), "--out-dir", str(out), "--now-ms", str(W2_END + 60_000)], roots=(str(tmp_path),), out_roots=(str(out),))
    assert rc_a == 0
    before = {n: (out / n).read_bytes() for n in ("recon.json", "recon.md", "recon-lt-a.json", "recon-lt-a.md")}
    rc2, _ = run_w2(tmp_path, mp, tm, live)
    assert rc2 == 0 and (out / "recon-lt-a-w2.json").exists()
    assert {n: (out / n).read_bytes() for n in before} == before  # none of the window 1 or book A files was rewritten
    w1 = json.loads(before["recon-lt-a.json"])
    assert w1["schema"] == "osb_recon_lt.v1" and "off_stake_live_buys" not in w1 and w1["window"]["from_utc"] == R.LT_S_START_UTC  # window 1 keeps its own shape, start and all rungs


def test_a_010_buy_inside_window2_fails_the_status_and_stays_out_of_the_s_set(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, late10=1, monkeypatch=monkeypatch)
    rc, out = run_w2(tmp_path, mp, tm, live)
    t = load_w2(out)
    assert rc == 0 and t["status"] == "FAIL" and t["off_stake_live_buys"] == {"n": 1, "by_stake_sol": {"0.1": 1}}
    assert [t[x]["status"] for x in ("s1", "s2", "s3", "s4")] == ["PASS"] * 4  # the S bands themselves are met on the ten 0.25 rows ...
    assert t["s1"]["n"] == 10 and t["s2"]["n"] == 10 and t["s3"]["n"] == 10 and t["s4"]["n"] == 10  # ... and n counts only the 0.25 rows
    assert "stake" in (out / "recon-lt-a-w2.md").read_text() and "FAIL" in (out / "recon-lt-a-w2.md").read_text()


def test_an_unfilled_off_stake_sent_buy_also_fails(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, late10=1, monkeypatch=monkeypatch)
    o = live[-1]
    o.update(led_ok_buy=False, buy_failed=True, landed_ms=None, landed_slot=None)  # sent, never filled
    rc, out = run_w2(tmp_path, mp, tm, live)
    t = load_w2(out)
    assert rc == 0 and t["status"] == "FAIL" and t["off_stake_live_buys"]["n"] == 1


def test_s_band_n_counts_only_025_rows_so_nine_025_plus_010_is_insufficient_and_fail(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, n25=9, late10=2, monkeypatch=monkeypatch)
    rc, out = run_w2(tmp_path, mp, tm, live)
    t = load_w2(out)
    assert rc == 0 and [t[x]["status"] for x in ("s1", "s2", "s3", "s4")] == ["INSUFFICIENT"] * 4 and t["s1"]["n"] == 9 and t["off_stake_live_buys"]["n"] == 2 and t["status"] == "FAIL"


def test_010_rows_before_the_start_line_never_enter_s_and_do_not_fail(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, early10=3, monkeypatch=monkeypatch)
    assert R.B.parse_instant(W2_START) > TR.entry_ms(-30) and TR.entry_ms(-28) < R.B.parse_instant(W2_START)  # the early buys are before the start line
    rc, out = run_w2(tmp_path, mp, tm, live)
    t = load_w2(out)
    assert rc == 0 and t["off_stake_live_buys"]["n"] == 0 and t["s1"]["n"] == 10 and t["lt_counts_s_window"]["matched"] == 10 and t["status"] == "PASS"


def test_the_025_rows_are_priced_by_the_twin_025_column(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, monkeypatch=monkeypatch)
    for r in live:
        r["led_net_lamports"] = 5_000_000  # live net at 0.25 equal to the fixture's twin 0.25 net: S2 diff 0
    rc, out = run_w2(tmp_path, mp, tm, live)
    t = load_w2(out)
    assert rc == 0 and t["s2"]["status"] == "PASS" and abs(t["s2"]["sum_live_sol"] - t["s2"]["sum_sim_sol"]) < 1e-9 and abs(t["s2"]["sum_stake_sol"] - 2.5) < 1e-9


def test_s_window_end_input_cuts_the_s_set(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, n25=12, monkeypatch=monkeypatch)
    end = B.utc(TR.entry_ms(10) - 30_000)[:19] + "Z"
    rc, out = run_w2(tmp_path, mp, tm, live, "--lt-s-end-utc", end)
    t = load_w2(out)
    assert rc == 0 and t["window"]["to_utc"] == end and t["s_window_end_input"] == end and t["s1"]["n"] == 10 and t["status"] == "PASS"


@pytest.mark.parametrize("end", [R.LT_W2_START_UTC, "2026-11-16T20:00:00Z", "tomorrow"])
def test_s_window_end_not_after_the_w2_start_is_refused(tmp_path, monkeypatch, end):
    mp, tm, _, live = w2_world(tmp_path, monkeypatch=monkeypatch)
    rc, out = run_w2(tmp_path, mp, tm, live, "--lt-s-end-utc", end)
    assert rc == 2 and not (out / "recon-lt-a-w2.json").exists()


def test_refuses_any_other_twin_book_id(tmp_path, monkeypatch, capsys):
    mp, tm, _, live = w2_world(tmp_path, twin_bid="osb-a-lt-b", twin_over={"pricing_lat_entry_s": 3.01}, monkeypatch=monkeypatch)  # pinned hash follows the file: only book_id differs
    rc, out = run_w2(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a-w2.json").exists() and "book_id" in capsys.readouterr().err


def test_refuses_a_twin_manifest_that_is_not_the_pinned_file(tmp_path, capsys):
    mp, tm, _, live = w2_world(tmp_path)  # the pin is the real A-lt-ar.json hash, not the fixture's
    rc, out = run_w2(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a-w2.json").exists() and "pinned" in capsys.readouterr().err


def test_a_changed_twin_manifest_byte_is_refused(tmp_path, monkeypatch, capsys):
    mp, tm, _, live = w2_world(tmp_path, monkeypatch=monkeypatch)
    tm.write_text(tm.read_text() + "\n")  # same json, different bytes
    rc, out = run_w2(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a-w2.json").exists()


def test_window2_needs_the_twin_manifest(tmp_path, capsys):
    ex = TR.write_extract(tmp_path / "extract", [], as_of=W2_END)
    rc = R.main(["--extract-dir", str(ex), "--manifest", str(tmp_path / "x.json"), "--out-dir", str(tmp_path / "o"), "--lt-window2"], roots=(str(tmp_path),), out_roots=(str(tmp_path / "o"),))
    assert rc == 2 and "needs --lt-twin-manifest" in capsys.readouterr().err


@pytest.mark.parametrize("w2", [False, True])
def test_refuses_a_twin_manifest_with_gap_exclusion_off(tmp_path, monkeypatch, capsys, w2):
    """the reviewer's missing test from #1416: a latency twin with the feed-gap exclusion switched off is refused (by the manifest loader or the twin check), in both windows"""
    mp, tm, _, live = w2_world(tmp_path, twin_over={"gap_exclusion": "off"}, monkeypatch=monkeypatch)
    rc, out = run_w2(tmp_path, mp, tm, live, w2=w2)
    assert rc == 2 and not (out / "recon-lt-a-w2.json").exists() and not (out / "recon-lt-a.json").exists() and "refused" in capsys.readouterr().err


def test_gap_exclusion_hold_twin_is_refused_in_window2_too(tmp_path, monkeypatch):
    mp, tm, _, live = w2_world(tmp_path, twin_over={"gap_exclusion": "hold"}, monkeypatch=monkeypatch)
    rc, out = run_w2(tmp_path, mp, tm, live)
    assert rc == 2 and not (out / "recon-lt-a-w2.json").exists()


def test_board_adds_one_w2_line_only_when_the_w2_file_exists(tmp_path, monkeypatch):
    root = tmp_path / "reconroot"
    root.mkdir()
    monkeypatch.setattr(B, "RECON_ROOTS", (str(root),))
    now = W2_END
    live = {"recon": str(root / "recon.json")}
    assert B.recon_lt_w2_line(live, now) is None and B.recon_lt_w2_line(None, now) is None and B.recon_lt_w2_line({"recon": "/etc/recon.json"}, now) is None
    doc = {"schema": "osb_recon_lt_w2.v1", "status": "FAIL", "generated_ms": now - 1000, **{k: {"status": "INSUFFICIENT" if k == "s1" else "PASS"} for k in KEYS}}
    (root / "recon-lt-a-w2.json").write_text(json.dumps(doc))
    line = B.recon_lt_w2_line(live, now)
    assert line.startswith("latency twin osb-a-lt-a recon, S window 2") and "FAIL" in line and "S1 INSUFFICIENT" in line and "not decision-grade" in line
    assert B.recon_lt_line(live, now) is None  # the window 1 line does not read the w2 file
    doc["schema"] = "osb_recon_lt.v1"  # a w1 schema in the w2 file name is not accepted
    (root / "recon-lt-a-w2.json").write_text(json.dumps(doc))
    assert B.recon_lt_w2_line(live, now) is None
    doc["schema"], doc["generated_ms"] = "osb_recon_lt_w2.v1", now - B.STALE_MS - 1
    (root / "recon-lt-a-w2.json").write_text(json.dumps(doc))
    assert B.recon_lt_w2_line(live, now) is None  # stale
    assert B.DECISION_GRADE is False
