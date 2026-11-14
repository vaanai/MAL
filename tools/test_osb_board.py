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


@pytest.fixture(autouse=True)
def snap_root(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "SNAP_ROOT", str(tmp_path / "snap"))  # never the real /data2 path
    monkeypatch.setattr(B, "RECON_ROOTS", (str(tmp_path),))


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


def set_status(out, end_ms, wall="2026-11-16T06:00:30+00:00", **extra):
    """status.json as osb_follow writes it, plus md5-by-hour.jsonl (the verified hours of the active run)"""
    st = {"book_id": out.name, "stakes_sol": [0.1, 0.25], "run": "0001"}
    md5 = out / "md5-by-hour.jsonl"
    if end_ms is not None:
        st.update(last_hour=B.utc(end_ms - H)[:13], last_hour_end_ms=end_ms, wall_time_utc=wall, active=True)
        md5.write_text(json.dumps({"hour": B.utc(end_ms - H)[:13], "end_ms": end_ms, "decision_md5": "d", "ledger_md5": "l"}) + "\n")
    elif md5.exists():
        md5.unlink()
    st.update(extra)
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
    assert d["decision_grade"] is False and res["decision_grade"] is False and res["recon"] == {"status": "NOT COMPARABLE: not decision-grade"}
    assert "holds; not decision-grade (recon NOT COMPARABLE): no live step rests on it" in md and "QP floor (a fact, not a recommendation)" in md and "QP floor: n/a" in md
    assert "| holds |" not in md and "QP floor: holds |" not in md and "QP floor: holds\n" not in md


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


def test_pin_mismatch_refuses_only_that_book_even_after_startup(tmp_path):
    ca, outa = make_book(tmp_path, "A", CONTROL_ROWS)
    cc, outc = make_book(tmp_path, "C", VARIANT_ROWS)
    bf = books_file(tmp_path, [(ca, "control"), (cc, "variant")])
    assert run(tmp_path, bf)[0] == 0
    (outc / "manifest.sha256").write_text("0" * 64 + "\n")  # a restart under another manifest, seen at the next build
    rc, o = run(tmp_path, bf)
    res = load(o)
    assert rc == 0 and [b["state"] for b in res["books"]] == ["running", "REFUSED: manifest pin"] and res["books"][1]["rows"] == [] and len(res["books"][0]["rows"]) == 2


def test_a_book_whose_driver_has_not_started_is_starting(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    cc, outc = make_book(tmp_path, "C", VARIANT_ROWS)
    import shutil
    shutil.rmtree(outc)  # no out_root yet
    rc, o = run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant")]))
    res = load(o)
    assert rc == 0 and [b["state"] for b in res["books"]] == ["running", "starting"] and len(res["books"][0]["rows"]) == 2
    (outc / "x").parent.mkdir(parents=True)  # out_root exists, no pin yet
    assert load(run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant")]))[1])["books"][1]["state"] == "starting"


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
    assert res["banner"] == "open simulation, paper, not a gate read; no book has cleared the promotion gate" and res["recon"] == {"status": "NOT COMPARABLE: not decision-grade"} and "NOT COMPARABLE: not decision-grade" in md
    assert res["live"] == {"note": "passed through"} and res["n_books_watched"] == 6 and "best of 6 book rows; treat as shrunk" in res["caveat"]
    assert row(res, "A", 0.10)["impact_label"] == "model; live fills exist, not reconciled (recon NOT COMPARABLE)" and row(res, "A", 0.25)["impact_label"] == "model; live check pending"
    a, c, d = (next(b for b in res["books"] if b["id"] == i) for i in "ACD")
    assert "the lane limit layer (max_open 16, budget, daily stop) is not applied" in a["title"] and "exact live configuration" not in a["title"] and a["params"]["limit_layer"] == "not applied" and res["limit_layer"] == "not applied"
    assert "No limit layer (max_open, budget, daily stop) is applied to any book" in md
    assert "Q_STAR=1000000000.0" in c["title"] and "TP=1.3" in d["title"]
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
    """the twin PR changes osb_follow (gap_exclusion hold) and osb_board only: the v0 board code and the pnl reader are untouched (their own tests carry the byte-identical goldens)"""
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", "origin/main", "--", "scripts/research/floor_forward_ab.py", "scripts/research/floor_nov_pnl.py"], capture_output=True, text=True)
    assert r.returncode != 0 or r.stdout.strip() == ""


# ------------------------------------------------------------------------------------------------ round 2 (QP B1-B3, reviewer B1-B5)
def books_file_starts(tmp, specs, live=None):
    p = tmp / "books.json"
    p.write_text(json.dumps({"books": [{"manifest": str(mp), "role": role, "start_instant_utc": st} for mp, role, st in specs], "live": live}))
    return p


def test_pairing_uses_the_same_entry_window_when_starts_differ(tmp_path):
    """control started a day earlier: its entries before the variant's start instant are not counted against the variant (QP B1)"""
    ctl_rows = tp("c1", ms(14, 13), -9_000_000) + tp("c2", ms(15, 13), 1_000_000) + tp("c3", ms(15, 15), 1_000_000)
    var_rows = tp("v1", ms(15, 13), 2_000_000) + tp("v2", ms(15, 15), 2_000_000)
    ca, _ = make_book(tmp_path, "A", ctl_rows)
    cc, _ = make_book(tmp_path, "C", var_rows)
    bf = books_file_starts(tmp_path, [(ca, "control", "2026-11-14T00:00:00Z"), (cc, "variant", "2026-11-15T10:00:00Z")])
    rc, out = run(tmp_path, bf)
    res = load(out)
    v = row(res, "C", 0.25)
    pr = v["vs_control"]
    assert pr["entry_from_utc"] == "2026-11-15T10:00:00Z" and pr["control_n_closed"] == 2 and pr["control_net_sol_flat"] == pytest.approx(0.002) and pr["variant_net_sol_flat"] == pytest.approx(0.004)
    assert pr["diff_sol_flat"] == pytest.approx(0.002) and v["disclosure"]["control_forward"]["net_sol_flat"] == pytest.approx(0.002) and v["disclosure"]["forward_net_sol_flat"] == pytest.approx(0.004)
    assert v["disclosure"]["window_basis"].startswith("paired: entries from 2026-11-15T10:00:00Z") and v["disclosure"]["first_forward_day_from_utc"] == "2026-11-15T10:00:00Z"
    assert row(res, "A", 0.25)["windows"]["forward_all"]["net_sol_flat"] == pytest.approx(-0.007)  # the control's own forward figure keeps its earlier entries
    assert "(forward from 10:00Z)" in (out / "board.md").read_text()


def test_one_bad_book_does_not_blank_the_others_and_follow_continues(tmp_path, capsys):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    cc, outc = make_book(tmp_path, "C", VARIANT_ROWS)
    # a half-written last line (no newline) is ignored, the rows before it count
    led = outc / "runs" / "0001" / "ledger" / "l1-floor-v2" / "trades-x.jsonl"
    led.write_text(led.read_text() + '{"kind": "trade", "position_id": "v9", "stake_sol": 0.2')
    rc, out = run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant")]))
    res = load(out)
    assert rc == 0 and [b["state"] for b in res["books"]] == ["running", "running"] and row(res, "C", 0.25)["windows"]["forward_all"]["n_closed"] == 4
    # a corrupt COMPLETE line breaks that book only: ERROR with the exception class, the board is still written, the follow loop goes on
    led.write_text(led.read_text() + "\nnot json\n")
    polls = []
    rc, out = run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant")]), "--follow", sleep_fn=polls.append, max_polls=3)
    res = load(out)
    assert rc == 0 and [b["state"] for b in res["books"]] == ["running", "ERROR"] and res["books"][1]["error_class"] == "JSONDecodeError" and res["books"][1]["rows"] == []
    assert len(row(res, "A", 0.25)["trades"]) == 6 and str(tmp_path) not in json.dumps(res)


def test_forbidden_word_in_a_title_withholds_that_book_only(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    cc, _ = make_book(tmp_path, "C", VARIANT_ROWS, title="the mint rule")
    rc, out = run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant")], live={"x": "pool"}))
    res = load(out)
    assert rc == 0 and res["books"][1]["state"] == "ERROR" and res["books"][1]["error_class"] == "ForbiddenWord" and res["books"][1]["id"] == "C" and res["live"] == {"withheld": "forbidden word"}
    assert len(res["books"][0]["rows"]) == 2 and "mint" not in (out / "board.md").read_text().lower()


def test_catching_up_run_does_not_move_the_board_backwards(tmp_path):
    ca, outa = make_book(tmp_path, "A", CONTROL_ROWS)
    rc, o1 = run(tmp_path, books_file(tmp_path, [(ca, "control")]))
    before = row(load(o1), "A", 0.25)
    # driver restarted: a new run 0002 replays from the start hour; status.json describes it, `book` still points at the old complete run 0001, md5-by-hour keeps the verified hours
    (outa / "runs" / "0002" / "ledger" / "l1-floor-v2").mkdir(parents=True)
    (outa / "runs" / "0002" / "ledger" / "l1-floor-v2" / "trades-x.jsonl").write_text(json.dumps(CONTROL_ROWS[0]) + "\n")
    set_status(outa, ms(14, 13), run="0002", catching_up_to_stored_hours=True, active=False)
    # set_status rewrote md5-by-hour for the smaller hour: put the verified list back (the driver never shortens it)
    (outa / "md5-by-hour.jsonl").write_text(json.dumps({"hour": "2026-11-16T05", "end_ms": END, "decision_md5": "d", "ledger_md5": "l"}) + "\n")
    rc, o2 = run(tmp_path, books_file(tmp_path, [(ca, "control")]))
    res = load(o2)
    after = row(res, "A", 0.25)
    assert res["books"][0]["state"] == "CATCHING UP" and res["tape_through_utc"] == "2026-11-16T06:00:00Z" and res["books"][0]["last_hour"] == "2026-11-16T05"
    assert after["windows"] == before["windows"] and after["trades"] == before["trades"] and all(o["age_h"] >= 0 for o in after["open"])
    # after the flip the new run is the active one and the state is normal again
    os.replace(outa / "runs" / "0002", outa / "runs" / "0002x")
    (outa / "book").unlink()
    os.symlink("runs/0001", outa / "book")
    set_status(outa, END, run="0001")
    assert load(run(tmp_path, books_file(tmp_path, [(ca, "control")]))[1])["books"][0]["state"] == "running"


def test_ledger_rows_after_the_board_hour_are_not_counted(tmp_path):
    ahead = tp("z1", ms(16, 6, 30), 9_000_000) + tp("z2", ms(16, 5, 30), 7_000_000)  # z1 enters after the tape end; z2 enters before it and exits (2 h later) after it
    mp, _ = make_book(tmp_path, "A", CONTROL_ROWS + ahead)
    rc, out = run(tmp_path, books_file(tmp_path, [(mp, "control")]))
    r = row(load(out), "A", 0.25)
    assert len(r["trades"]) == 6 and "A-00011" not in {t["tid"] for t in r["trades"]} and sorted(o["tid"] for o in r["open"]) == ["A-00008", "A-00009"] and "A-00010" not in {t["tid"] for t in r["trades"]}
    assert all(o["age_h"] >= 0 for o in r["open"]) and r["windows"]["day_in_progress"]["n_closed"] == 1 and r["unpriced_entries"] == 1


def test_stuck_status_is_rebuilt_and_shows_stale(tmp_path):
    mp, out = make_book(tmp_path, "A", CONTROL_ROWS)  # wall_time 2026-11-16T06:00:30Z, never updated again
    clock = [END + 60_000]
    polls = []

    def sleeper(s):
        polls.append(s)
        clock[0] += 20 * 60_000
    ob = tmp_path / "board"
    rc = B.main([str(books_file(tmp_path, [(mp, "control")])), "--out-dir", str(ob), "--follow", "--poll-s", "5"], roots=(str(tmp_path),), out_roots=(str(ob),), sleep_fn=sleeper, max_polls=12, clock_ms=lambda: clock[0])
    assert rc == 0 and load(ob)["books"][0]["state"] == "STALE" and load(ob)["generated_utc"] > "2026-11-16T09:00:00Z"
    assert "STALE" in (ob / "board.md").read_text()


def test_book_link_flipped_between_builds_keeps_ids(tmp_path):
    mp, out = make_book(tmp_path, "A", CONTROL_ROWS)
    bf = books_file(tmp_path, [(mp, "control")])
    r1 = row(load(run(tmp_path, bf)[1]), "A", 0.25)
    import shutil
    shutil.copytree(out / "runs" / "0001", out / "runs" / "0002")  # the driver verified a restart: same decisions, new run dir
    (out / "book").unlink()
    os.symlink("runs/0002", out / "book")
    set_status(out, END, run="0002")
    r2 = row(load(run(tmp_path, bf)[1]), "A", 0.25)
    assert r1["trades"] == r2["trades"] and r1["open"] == r2["open"] and r1["windows"] == r2["windows"]


def test_unpriced_exemption_is_exact():
    with pytest.raises(B.Refusal):
        B.check_forbidden({"priced_mark": 1}, "")
    with pytest.raises(B.Refusal):
        B.check_forbidden({"a": "repriced"}, "")
    with pytest.raises(B.Refusal):
        B.check_forbidden({"unpriced_entries_x": 1}, "")
    B.check_forbidden({"unpriced_entries": 1}, B.UNPRICED_SENTENCE + B.UNPRICED_HEAD)
    with pytest.raises(B.Refusal):
        B.check_forbidden({}, "the price moved")


def test_a_variant_that_holds_is_never_a_bare_holds(world):
    tmp, ca, cc = world
    md = (run(tmp, books_file(tmp, [(ca, "control"), (cc, "variant")]))[1] / "board.md").read_text()
    assert all("holds" not in ln or "not decision-grade" in ln or "does not hold" in ln for ln in md.splitlines())


# ------------------------------------------------------------------------------------------------ minors round (QP m6-m8, reviewer)
HEALTHY_DIGEST = "6e2409b7a3cf5d9f3220b02b3dbe450d9acc3f8042c3d90541ee1cfda8f337d9"  # sha256 of the healthy two-book board.json made by the code before this PR (board_version, manifest_sha256, today_day removed)


def test_healthy_board_is_unchanged_except_today_day(world):
    import hashlib
    tmp, ca, cc = world
    res = load(run(tmp, books_file(tmp, [(ca, "control"), (cc, "variant")]))[1])
    assert [b["today_day"] for b in res["books"]] == ["2026-11-15", "2026-11-15"]
    res.pop("board_version")
    for b in res["books"]:
        b.pop("manifest_sha256")
        b.pop("today_day")
    assert hashlib.sha256(json.dumps(res, sort_keys=True).encode()).hexdigest() == HEALTHY_DIGEST


def test_lagging_book_shows_its_own_day(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    cc, outc = make_book(tmp_path, "C", VARIANT_ROWS, end_ms=ms(15, 12), wall="2026-11-16T01:00:00+00:00")  # tape end a day behind, stale
    rc, out = run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant")]))
    res = load(out)
    a, c = res["books"]
    assert a["today_day"] == "2026-11-15" and c["today_day"] == "2026-11-14" and c["state"] == "STALE"
    md = (out / "board.md").read_text()
    assert "| (11-14) +" in md and "| (11-15) +" not in md  # only the lagging book's today carries its own day


def test_withheld_row_keeps_id_and_keys(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    cc, _ = make_book(tmp_path, "Cx", VARIANT_ROWS, title="the mint rule")
    cp, _ = make_book(tmp_path, "pool-9", VARIANT_ROWS)
    res = load(run(tmp_path, books_file(tmp_path, [(ca, "control"), (cc, "variant"), (cp, "variant")]))[1])
    w = res["books"][1]
    assert w["id"] == "Cx" and w["state"] == "ERROR" and w["manifest_sha256"] is None and w["stakes_sol"] is None and "today_day" in w
    assert res["books"][2]["id"] == "book-3" and res["books"][2]["error_class"] == "ForbiddenWord"  # the id itself carries a forbidden word


def test_snapshots_live_under_the_snapshot_root_are_swept_and_private(tmp_path):
    B.SNAP_ROOT = str(tmp_path / "snap")
    old = tmp_path / "snap" / "osb-board-old"
    new = tmp_path / "snap" / "osb-board-new"
    old.mkdir(parents=True)
    new.mkdir()
    t = time_now() - 2 * 3600
    os.utime(old, (t, t))
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    assert run(tmp_path, books_file(tmp_path, [(ca, "control")]))[0] == 0
    assert not old.exists() and new.exists()
    assert (os.stat(tmp_path / "snap").st_mode & 0o777) == 0o700
    assert [p.name for p in (tmp_path / "snap").iterdir()] == ["osb-board-new"]  # this build's own snapshot dir is gone


def time_now():
    import time
    return time.time()


def test_real_snapshot_root_is_refused_under_pytest(monkeypatch):
    monkeypatch.setattr(B, "SNAP_ROOT", B.REAL_SNAP_ROOT)
    with pytest.raises(RuntimeError):
        B.snap_root()
    assert not os.path.exists(B.REAL_SNAP_ROOT)


# ------------------------------------------------------------------------------------------------------------------------------ recon wiring (PR-3)
def recon_file(tmp, status="PASS", generated_ms=None, **over):
    r = {"schema": "osb_recon.v1", "status": status, "generated_ms": END + 30_000 if generated_ms is None else generated_ms, "generated_utc": "2026-11-16T06:00:30Z", "book_id": "A", "extract_as_of_ms": END,
         "window": {"from_utc": "2026-11-14T12:00:00Z", "to_utc": "2026-11-16T06:00:00Z"}, "counts": {"matched": 12, "live_only": 0 if status != "FAIL" else 1, "sim_only": 3},
         "counts_by_reason": {"sim_only:restart_lockout": 2, "sim_only:executor:lane_full": 1}, **{k: {"status": status} for k in ("h1", "h2", "h3", "s1", "s2", "s3", "s4")}}
    r.update(over)
    p = tmp / "recon.json"
    p.write_text(json.dumps(r))
    return p


def board_with(tmp, live):
    ca, _ = make_book(tmp, "A", CONTROL_ROWS)
    rc, out = run(tmp, books_file(tmp, [(ca, "control")], live=live))
    assert rc == 0
    return load(out), (out / "board.md").read_text()


def test_recon_pass_shows_status_line_and_counts(tmp_path):
    res, md = board_with(tmp_path, {"recon": str(recon_file(tmp_path, "PASS")), "bot_id": "floor-lane-1"})
    assert res["recon"]["status"] == "PASS" and res["recon"]["counts"] == {"matched": 12, "live_only": 0, "sim_only": 3}
    assert res["recon"]["counts_by_reason"]["sim_only:restart_lockout"] == 2 and res["recon_line"].startswith("PASS (book A against the live bot, 2026-11-14T12:00:00Z to 2026-11-16T06:00:00Z)")
    assert "NOT COMPARABLE" not in res["recon_line"] and "sim_off" not in res and res["decision_grade"] is False
    assert res["live"] == {"bot_id": "floor-lane-1"} and "recon.json" not in json.dumps(res)  # the path is not echoed
    assert "Recon: PASS (book A" in md and "SIM OFF" not in md


def test_recon_fail_marks_every_book_sim_off(tmp_path):
    res, md = board_with(tmp_path, {"recon": str(recon_file(tmp_path, "FAIL"))})
    assert res["recon"]["status"] == "FAIL" and res["sim_off"] == "SIM OFF: not decision-grade" and res["live"] is None
    assert all(bk["sim_off"] == "SIM OFF: not decision-grade" for bk in res["books"]) and "SIM OFF: not decision-grade" in res["recon_line"]
    assert "every figure below is greyed" in md and "SIM OFF: not decision-grade: " in md


@pytest.mark.parametrize("how", ["insufficient", "missing", "garbage", "bad_schema", "stale", "bad_counts", "future", "no_path"])
def test_recon_unusable_keeps_not_comparable(tmp_path, how):
    p = tmp_path / "recon.json"
    if how == "insufficient":
        p = recon_file(tmp_path, "INSUFFICIENT")
    elif how == "garbage":
        p.write_text("{nope")
    elif how == "bad_schema":
        p = recon_file(tmp_path, "PASS", schema="other")
    elif how == "stale":
        p = recon_file(tmp_path, "PASS", generated_ms=END + 60_000 - 3 * 3_600_000 - 1)
    elif how == "bad_counts":
        p = recon_file(tmp_path, "PASS", counts={"matched": "x"})
    elif how == "future":
        p = recon_file(tmp_path, "PASS", generated_ms=END + 10 * 3_600_000)
    live = {"recon": 5} if how == "no_path" else {"recon": str(p)}
    res, md = board_with(tmp_path, live)
    assert res["recon"] == {"status": "NOT COMPARABLE: not decision-grade"} and res["recon_line"] == "NOT COMPARABLE: not decision-grade" and "sim_off" not in res and "NOT COMPARABLE: not decision-grade" in md


def test_recon_fresh_boundary_and_forbidden_word_in_recon_is_ignored(tmp_path):
    res, _ = board_with(tmp_path, {"recon": str(recon_file(tmp_path, "PASS", generated_ms=END + 60_000 - 3 * 3_600_000))})
    assert res["recon"]["status"] == "PASS"  # exactly 3 h old is still fresh
    res2, _ = board_with(tmp_path, {"recon": str(recon_file(tmp_path, "PASS", counts_by_reason={"sim_only:pool_x": 1}))})
    assert res2["recon"] == {"status": "NOT COMPARABLE: not decision-grade"}  # a forbidden word never reaches the board


@pytest.mark.parametrize("over", [{"extract_as_of_ms": END + 60_000 - 3 * 3_600_000 - 1}, {"book_id": "C"}, {"book_id": None}, {"extract_as_of_ms": "x"}])
def test_recon_stale_extract_or_other_book_keeps_not_comparable(tmp_path, over):
    res, _ = board_with(tmp_path, {"recon": str(recon_file(tmp_path, "FAIL", **over))})
    assert res["recon"] == {"status": "NOT COMPARABLE: not decision-grade"} and "sim_off" not in res  # a stale extract or another book never switches the board off or on


def test_recon_path_outside_the_recon_roots_is_not_read(tmp_path, monkeypatch):
    p = recon_file(tmp_path, "FAIL")
    monkeypatch.setattr(B, "RECON_ROOTS", (str(tmp_path / "elsewhere"),))
    res, _ = board_with(tmp_path, {"recon": str(p)})
    assert res["recon"] == {"status": "NOT COMPARABLE: not decision-grade"}
    other = tmp_path / "notrecon.json"
    other.write_text(p.read_text())
    monkeypatch.setattr(B, "RECON_ROOTS", (str(tmp_path),))
    res2, _ = board_with(tmp_path, {"recon": str(other)})
    assert res2["recon"] == {"status": "NOT COMPARABLE: not decision-grade"}  # only a file named recon.json
