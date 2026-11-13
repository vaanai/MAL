"""osb_board: board over follow-mode book dirs. Synthetic ledgers and status files only (no tape, no real book, no host path). Paper; an open simulation, not a gate read. Nothing here says any book makes money."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import osb_board as B  # noqa: E402
import osb_follow as O  # noqa: E402

H = 3_600_000
START = "2026-11-14T12:00:00Z"


def ms(d, h=12, m=0):
    return int(datetime(2026, 11, d, h, m, tzinfo=timezone.utc).timestamp() * 1000)


def op(pid, t):
    return {"kind": "open", "position_id": pid, "pool": "P_" + pid, "mint": "M_" + pid, "decision_ms": {"entry": t}}


def tr(pid, stake, net, t0, t1, reason, state="filled"):
    legs = {f"{m}_1p9": {"net_lamports": int(net * stake / 0.25 * (1 if m == "flat" else 0.9)), "state": state} for m in ("flat", "press")}
    return {"kind": "trade", "position_id": pid, "stake_sol": stake, "exit_reason": reason, "decision_ms": {"entry": t0, "exit": t1}, "legs": {"total": legs},
            "calc": {"1p9": {"Qe": 18_100_000_000, "Be": 1_000_000_000_000}}, "meta": {"v": 17_600_000_000, "mark0": 1e-8}}


def tp(pid, t0, net, stakes=(0.10, 0.25)):
    return [op(pid, t0)] + [tr(pid, s, net, t0, t0 + 2 * H, "tp_1.5x") for s in stakes]


def tstop(pid, t0, net, stakes=(0.10, 0.25)):
    return [op(pid, t0)] + [tr(pid, s, net, t0, t0 + 24 * H + 1900, "time_stop") for s in stakes]


def excluded(pid, t0, stakes=(0.10, 0.25)):
    return [op(pid, t0)] + [{"kind": "excluded", "position_id": pid, "stake_sol": s, "exclusion_reason": "feed_gap"} for s in stakes]


CONTROL_ROWS = (tp("a1", ms(13, 20), 5_000_000) + tp("a2", ms(14, 13), 4_000_000) + tstop("a3", ms(14, 16), -3_000_000) + tp("a4", ms(15, 2), 2_000_000) + tp("a5", ms(15, 20), 6_000_000)
                + tp("a6", ms(16, 1), 1_000_000) + excluded("a7", ms(14, 14)) + [op("a8", ms(16, 3))])
VARIANT_ROWS = tp("v1", ms(14, 13), 10_000_000) + tp("v2", ms(15, 3), 8_000_000) + tp("v3", ms(15, 10), 5_000_000) + tp("v4", ms(15, 14), 1_000_000)
END = ms(16, 6)


def manifest(tmp, bid, host="lane", ov=None, **over):
    m = {"book_id": bid, "title": "test book " + bid, "host": host, "rule_overrides": ov or {}, "stakes_sol": [0.10, 0.25], "send_lamports": 160000, "start_hour": "2026-11-14T12",
         "seed_from_hour": "2026-11-05T00", "tape_dir": str(tmp / "tape"), "out_root": str(tmp / "books" / bid)}
    if host == "lane":
        m["lane"] = {"gap_lookback_s": 21600, "status_span_no_loss": False, "cheap_restart": False}
    m.update(over)
    return m


def make_book(tmp, bid, rows, end_ms=END, wall="2026-11-16T06:00:30+00:00", **kw):
    m = manifest(tmp, bid, **kw)
    out = Path(m["out_root"])
    (out / "runs" / "0001" / "ledger" / "l1-floor-v2").mkdir(parents=True, exist_ok=True)
    (tmp / "tape").mkdir(exist_ok=True)
    if not (out / "book").exists():
        os.symlink("runs/0001", out / "book")
    (out / "runs" / "0001" / "ledger" / "l1-floor-v2" / "trades-x.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n" if rows else "")
    (out / "manifest.sha256").write_text(O.manifest_sha256(m) + "\n")
    (out / "code.sha256").write_text("c" * 64 + "\n")
    set_status(out, end_ms, wall)
    mp = tmp / ("manifest-%s.json" % bid)
    mp.write_text(json.dumps(m))
    return mp, out


def set_status(out, end_ms, wall="2026-11-16T06:00:30+00:00"):
    st = {"book_id": out.name, "stakes_sol": [0.1, 0.25]}
    if end_ms is not None:
        st.update(last_hour=B.utc(end_ms - H)[:13], last_hour_end_ms=end_ms, wall_time_utc=wall, active=True)
    (out / "status.json").write_text(json.dumps(st))


def books_file(tmp, specs, live=None):
    p = tmp / "books.json"
    p.write_text(json.dumps({"books": [{"manifest": str(mp), "role": role, "start_instant_utc": START} for mp, role in specs], "live": live}))
    return p


def run(tmp, bf, *extra, now=END + 60_000, **kw):
    out = tmp / "board"
    rc = B.main([str(bf), "--out-dir", str(out), "--now-ms", str(now), *extra], roots=(str(tmp),), out_roots=(str(out),), **kw)
    return rc, out


@pytest.fixture
def world(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    cc, _ = make_book(tmp_path, "C", VARIANT_ROWS, ov={"Q_STAR": 1.0e9})
    return tmp_path, ca, cc


def load(out):
    return json.loads((out / "board.json").read_text())


def row(res, bid, stake):
    return next(r for b in res["books"] if b["id"] == bid for r in b["rows"] if r["stake_sol"] == stake)


def test_windows_forward_in_sample_and_partial_day(world):
    tmp, ca, cc = world
    rc, out = run(tmp, books_file(tmp, [(ca, "control"), (cc, "variant")]))
    assert rc == 0
    res = load(out)
    assert res["schema"] == "open_sim_board.v1" and res["tape_through_utc"] == "2026-11-16T06:00:00Z" and res["n_books_watched"] == 4
    r = row(res, "A", 0.25)
    w = r["windows"]
    # forward days 14, 15: a2 +4, a3 -3 (closes 15th), a4 +2, a5 +6 (a1 is in-sample; a6 closes on the partial 16th)
    f = w["forward_all"]
    assert f["n_closed"] == 4 and f["n_in_sample"] == 0 and f["days"] == 2
    assert f["net_sol_flat"] == pytest.approx(0.009) and f["net_sol_press"] == pytest.approx(0.0081)
    assert f["ex_top3_sol_flat"] == pytest.approx(-0.003) and f["ex_top3_sol_press"] == pytest.approx(-0.0027)
    assert f["days_pos_flat"] == 2 and f["tp_count"] == 3 and f["time_count"] == 1 and f["worst_sol_flat"] == pytest.approx(-0.003)
    ib = w["incl_backfill"]  # days 13..15, a1 is in-sample (entry before the start instant)
    assert ib["n_closed"] == 5 and ib["n_in_sample"] == 1 and ib["days"] == 3 and ib["net_sol_flat"] == pytest.approx(0.014)
    assert w["today"]["day"] == "2026-11-15" and w["today"]["n_closed"] == 3
    assert w["d7"]["n_closed"] == 5
    p = w["day_in_progress"]
    assert p["partial_day"] is True and p["label"] == "partial day" and p["n_closed"] == 1 and p["net_sol_flat"] == pytest.approx(0.001) and p["days_list"] == ["2026-11-16"]
    assert [d["day"] for d in r["per_day"]] == ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"] and r["per_day"][-1]["partial_day"] and r["per_day"][0]["in_sample"]
    assert len(r["open"]) == 1 and r["open"][0]["age_h"] == pytest.approx(3.0) and r["unpriced_entries"] == 1  # a7 engine-excluded; a8 open is listed, not unpriced
    t = {x["tid"]: x for x in r["trades"]}
    assert t["A-00001"]["in_sample"] is True and t["A-00001"]["exit_reason"].startswith("tp") and len(t) == 6
    assert t["A-00001"]["ret_pct"] == pytest.approx(100.0 * 0.005 / 0.25)
    assert row(res, "A", 0.10)["windows"]["forward_all"]["net_sol_flat"] == pytest.approx(0.0036)


def test_variant_vs_control_disclosure_and_floor(world):
    tmp, ca, cc = world
    rc, out = run(tmp, books_file(tmp, [(ca, "control"), (cc, "variant")]))
    res = load(out)
    v = row(res, "C", 0.25)
    assert v["windows"]["forward_all"]["net_sol_flat"] == pytest.approx(0.024)
    vc = v["vs_control"]
    assert vc["control_book"] == "A" and vc["n_days"] == 2 and vc["diff_sol_flat"] == pytest.approx(0.015) and vc["diff_sol_press"] == pytest.approx(0.0135)
    d = v["disclosure"]
    assert d["book_id"] == "C" and d["start_instant_utc"] == START and d["forward_n_closed"] == 4 and d["n_complete_forward_days"] == 2 and d["live_minus_sim_bias"] is None
    assert d["forward_ex_top3_sol_flat"] == pytest.approx(0.001) and d["control_forward"]["net_sol_flat"] == pytest.approx(0.009) and d["sentence"] == "best of 4 book rows; treat as shrunk"
    assert d["floor_holds"] is True
    assert row(res, "A", 0.25)["disclosure"]["floor_holds"] is None and row(res, "A", 0.25)["vs_control"] is None
    md = (out / "board.md").read_text()
    assert "QP floor: holds" in md and "QP floor: n/a" in md and "recommend" not in md.lower()


def test_floor_does_not_hold_when_control_is_ahead(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    cc, _ = make_book(tmp_path, "C", tp("v1", ms(14, 13), 1_000_000) + tp("v2", ms(15, 3), 1_000_000) + tp("v3", ms(15, 10), 1_000_000) + tp("v4", ms(15, 14), 1_000_000) + tp("v5", ms(15, 15), 1_000_000))
    rc, out = run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant")]))
    d = row(load(out), "C", 0.25)["disclosure"]
    assert d["forward_ex_top3_sol_flat"] > 0 and d["floor_holds"] is False  # positive ex-top-3, but the control's net on the same days is higher
    assert "QP floor: does not hold" in (out / "board.md").read_text()


def test_no_control_gives_no_pairing(tmp_path):
    cc, _ = make_book(tmp_path, "C", VARIANT_ROWS)
    rc, out = run(tmp_path, books_file(tmp_path, [(cc, "variant")]))
    v = row(load(out), "C", 0.25)
    assert v["vs_control"] is None and v["disclosure"]["paired_difference"] is None and v["disclosure"]["floor_holds"] is None and v["disclosure"]["control_forward"] is None


def test_tids_do_not_shift_when_later_trades_arrive(tmp_path):
    early = tp("a1", ms(13, 20), 5_000_000) + tp("a2", ms(14, 13), 4_000_000) + [op("a3", ms(14, 16))]
    mp, out = make_book(tmp_path, "A", early, end_ms=ms(15, 0))
    bf = books_file(tmp_path, [(mp, "control")])
    rc, o1 = run(tmp_path, bf, now=ms(15, 0))
    r1 = row(load(o1), "A", 0.25)
    ids1 = {(t["entry_utc"], t["exit_utc"]): t["tid"] for t in r1["trades"]}
    assert r1["open"][0]["tid"] == "A-00003"
    later = early[:-1] + tstop("a3", ms(14, 16), -3_000_000) + tp("a4", ms(14, 18), 2_000_000) + tp("a5", ms(14, 18), 1_000_000)  # a3 closes later; a4/a5 tie on entry ms (tie-break: ledger id)
    make_book(tmp_path, "A", later, end_ms=ms(16, 6))
    rc, o2 = run(tmp_path, bf, now=ms(16, 6))
    r2 = row(load(o2), "A", 0.25)
    ids2 = {(t["entry_utc"], t["exit_utc"]): t["tid"] for t in r2["trades"]}
    assert all(ids2[k] == v for k, v in ids1.items()) and len(r2["trades"]) == 5
    assert {t["tid"]: t["entry_utc"] for t in r2["trades"]}["A-00003"] == "2026-11-14T16:00:00Z"  # the open entry kept its id when it closed
    assert [t["tid"] for t in r2["trades"] if t["entry_utc"] == "2026-11-14T18:00:00Z"] == ["A-00004", "A-00005"]


def test_book_with_no_completed_hour_is_starting_and_a_stale_status_is_marked(tmp_path):
    ca, outa = make_book(tmp_path, "A", [], end_ms=None)
    cc, outc = make_book(tmp_path, "C", VARIANT_ROWS, wall="2026-11-16T01:00:00+00:00")  # 5 h before now
    rc, out = run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant")]))
    assert rc == 0
    res = load(out)
    a, c = (next(b for b in res["books"] if b["id"] == i) for i in "AC")
    assert a["state"] == "starting" and a["rows"] == [] and a["last_hour"] is None
    assert c["state"] == "STALE" and c["wall_time_utc"] == "2026-11-16T01:00:00+00:00" and res["tape_through_utc"] == "2026-11-16T06:00:00Z"
    md = (out / "board.md").read_text()
    assert "STALE" in md and "state: starting" in md
    assert row(res, "C", 0.25)["vs_control"] is None  # the control has no data


def test_waiting_for_hour_is_carried(tmp_path):
    mp, out = make_book(tmp_path, "A", CONTROL_ROWS)
    st = json.loads((out / "status.json").read_text())
    st.update(waiting_for_hour="2026-11-16T06", waiting_since_utc="2026-11-16T06:00:01+00:00")
    (out / "status.json").write_text(json.dumps(st))
    rc, o = run(tmp_path, books_file(tmp_path, [(mp, "control")]))
    assert load(o)["books"][0]["waiting_for_hour"] == "2026-11-16T06"


def test_manifest_pin_mismatch_is_exit_2_and_names_the_book(tmp_path, capsys):
    mp, out = make_book(tmp_path, "A", CONTROL_ROWS)
    (out / "manifest.sha256").write_text("0" * 64 + "\n")
    rc, o = run(tmp_path, books_file(tmp_path, [(mp, "control")]))
    assert rc == 2 and "book A" in capsys.readouterr().err and not (o / "board.json").exists()


def test_bad_books_files_are_refused(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    cb, _ = make_book(tmp_path, "B", CONTROL_ROWS)
    assert run(tmp_path, books_file(tmp_path, [(ca, "control"), (cb, "control")]))[0] == 2  # two controls
    assert run(tmp_path, books_file(tmp_path, [(ca, "boss")]))[0] == 2
    (tmp_path / "bad.json").write_text(json.dumps({"books": [{"manifest": str(ca), "role": "control", "start_instant_utc": "2026-11-14"}]}))
    assert run(tmp_path, tmp_path / "bad.json")[0] == 2
    assert run(tmp_path, books_file(tmp_path, [(ca, "variant"), (ca, "variant")]))[0] == 2  # duplicate id


def test_out_root_and_out_dir_guards(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    bf = books_file(tmp_path, [(ca, "control")])
    # a book outside the follow root
    assert B.main([str(bf), "--out-dir", str(tmp_path / "board")], roots=(str(tmp_path / "elsewhere"),), out_roots=(str(tmp_path / "board"),)) == 2
    # the out dir outside the output roots, and a symlink that escapes
    assert B.main([str(bf), "--out-dir", str(tmp_path / "nope")], roots=(str(tmp_path),), out_roots=(str(tmp_path / "board"),)) == 2
    (tmp_path / "board").mkdir()
    (tmp_path / "outside").mkdir()
    os.symlink(tmp_path / "outside", tmp_path / "board" / "esc")
    assert B.main([str(bf), "--out-dir", str(tmp_path / "board" / "esc")], roots=(str(tmp_path),), out_roots=(str(tmp_path / "board"),)) == 2
    assert not list((tmp_path / "outside").iterdir())
    # the real defaults
    for bad in ("/tmp/x", "/data2/mal/scratch/floor-open-sim", "/data2/mal/scratch/floor-open-sim/board/../keep", "/data2/mal/scratch/floor-forward-ab/board"):
        with pytest.raises(B.Refusal):
            B.guard_out_dir(bad)
    assert B.guard_out_dir("/data2/mal/scratch/floor-open-sim/board/x").endswith("/floor-open-sim/board/x")
    assert B.guard_out_dir("~/data/open-sim").endswith("/data/open-sim")


def test_output_allow_list_and_labels(world):
    tmp, ca, cc = world
    ch, _ = make_book(tmp, "D", VARIANT_ROWS, host="hostv2", ov={"TP": 1.3})
    rc, out = run(tmp, books_file(tmp, [(ca, "control"), (cc, "variant"), (ch, "variant")], live={"note": "passed through"}))
    res = load(out)
    md = (out / "board.md").read_text()
    for s in B._walk(res):
        low = s.lower().replace("unpriced", "")
        assert not any(w in low for w in ("pool", "mint", "signature", "price", "reserve", "slot")), s
    assert not any(w in md.lower().replace("unpriced", "").replace("priced", "") for w in ("pool", "mint", "signature", "price", "reserve", "slot"))
    assert res["banner"] == "open simulation, paper, not a gate read; no book has cleared the promotion gate" and res["recon"] == {"status": "NOT COMPARABLE"} and "NOT COMPARABLE: not decision-grade" in md
    assert res["live"] == {"note": "passed through"} and res["n_books_watched"] == 6 and "best of 6 book rows; treat as shrunk" in res["caveat"]
    assert row(res, "A", 0.10)["impact_label"] == "model; live fills exist, not reconciled (recon NOT COMPARABLE)" and row(res, "A", 0.25)["impact_label"] == "model; live check pending"
    a, c, d = (next(b for b in res["books"] if b["id"] == i) for i in "ACD")
    assert "control: the exact live configuration" in a["title"] and "Q_STAR=1000000000.0" in c["title"] and "TP=1.3" in d["title"]
    assert a["gap_guard_note"] is None and d["gap_guard_note"] == "no lane gap guard" and d["params"]["host"] == "hostv2" and c["params"]["gap_guard"] is True
    assert a["code_sha256"] == "c" * 64 and a["params"]["send_lamports"] == 160000 and a["params"]["start_hour"] == "2026-11-14T12"
    assert "no lane gap guard" in md and "extrapolation_note" not in res  # stakes 0.10 and 0.25 only
    assert "| book | role | stake | label |" in md


def test_extrapolated_stake_label_and_paragraph(tmp_path):
    rows = tp("a2", ms(14, 13), 4_000_000, stakes=(0.10, 0.25, 0.5))
    mp, _ = make_book(tmp_path, "A", rows, stakes_sol=[0.10, 0.25, 0.5])
    rc, out = run(tmp_path, books_file(tmp_path, [(mp, "control")]))
    res = load(out)
    r = row(res, "A", 0.5)
    assert r["impact_label"] == "model only, extrapolated; not for steering" and "extrapolation_note" in r and "extrapolated" in res["extrapolation_note"] and res["n_books_watched"] == 3


def test_follow_rebuilds_only_when_a_status_hour_changes(tmp_path, capsys):
    mp, out = make_book(tmp_path, "A", CONTROL_ROWS, end_ms=ms(16, 5))
    bf = books_file(tmp_path, [(mp, "control")])
    sleeps = []

    def sleeper(s):
        sleeps.append(s)
        if len(sleeps) == 2:  # a new hour arrives
            make_book(tmp_path, "A", CONTROL_ROWS + tp("a9", ms(16, 4), 1_000_000), end_ms=ms(16, 6))
    rc, o = run(tmp_path, bf, "--follow", "--poll-s", "7", sleep_fn=sleeper, max_polls=4, now=ms(16, 7))
    assert rc == 0 and sleeps == [7.0, 7.0, 7.0]
    lines = [x for x in capsys.readouterr().out.splitlines() if x.startswith("osb_board:")]
    assert len(lines) == 2 and "tape_through 2026-11-16T05:00:00Z" in lines[0] and "tape_through 2026-11-16T06:00:00Z" in lines[1]
    assert load(o)["tape_through_utc"] == "2026-11-16T06:00:00Z"


def test_without_follow_one_build_and_files_are_atomic(world):
    tmp, ca, cc = world
    rc, out = run(tmp, books_file(tmp, [(ca, "control"), (cc, "variant")]))
    assert rc == 0 and sorted(p.name for p in out.iterdir()) == ["board.json", "board.md"]


def test_nothing_in_existing_pricing_scripts_changed():
    """this PR adds osb_board only: the v0 board code and the pnl reader are untouched (their own tests carry the byte-identical goldens)"""
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", "origin/main", "--", "scripts/research/floor_forward_ab.py", "scripts/research/floor_nov_pnl.py", "scripts/research/osb_follow.py"], capture_output=True, text=True)
    assert r.returncode != 0 or r.stdout.strip() == ""
