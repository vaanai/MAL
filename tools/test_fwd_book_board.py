"""fwd_book_board: board side of c1nf-fwd-v1 / osb-u4 / osb-u2. Synthetic book dirs only (no tape, no real ledger, no host path). Paper; an open simulation, not a gate read.
Nothing here says any book makes money."""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import fwd_book_board as B  # noqa: E402
import fwd_book_board_text as T  # noqa: E402

H, D = 3_600_000, 86_400_000
D0 = B.day_start_ms("2026-11-17")
S = D0 + 12 * H                      # start_hour 2026-11-17T12
TAPE_END = B.day_start_ms("2026-11-23") + 3 * H   # complete days 11-17 .. 11-22 (6)
L1, L2 = S + 5 * D, S + 10 * D
LAM = 1_000_000_000
DEC_TEXT = (ROOT / "DEC" / "DEC-035-live-learning-lane.md").read_text()


class Fx:
    def __init__(self, tmp, book_id="osb-u4", tape_end=TAPE_END, start_hour="2026-11-17T12", extra_meta=None):
        self.sim = tmp / "c1nf-open-sim"
        self.root = self.sim / book_id
        self.book_id = book_id
        dec = {"c1nf-fwd-v1": "c1nf", "osb-u4": "u4", "osb-u2": "u2"}[book_id.split("-lat")[0].split("-send")[0]]
        self.run = self.root / "runs" / "0001"
        self.run.mkdir(parents=True)
        (self.root / "book").symlink_to(self.run)
        meta = {"book_id": book_id, "decider": dec, "start_hour": start_hour, "pricing_lat_entry_s": 4.085, "send_lamports": 505000}
        meta.update(extra_meta or {})
        (self.root / "meta.json").write_text(json.dumps(meta))
        (self.root / "status.json").write_text(json.dumps({"book_id": book_id, "last_hour": "x", "last_hour_end_ms": tape_end}))
        self.bot = "c1nf-fwd-" + book_id
        self.n = 0

    def rows(self, bot, rows, day="2026-11-17", run=None):
        d = (run or self.run) / "ledger" / bot
        d.mkdir(parents=True, exist_ok=True)
        with open(d / ("trades-%s.jsonl" % day), "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def entry(self, bot, entry_ms, exit_ms, nets, stakes=(0.1, 0.25), excluded=None, run=None):
        """one position: open row + a trade row per stake (nets: {stake: (flat_lamports, press_lamports)}) or excluded rows"""
        self.n += 1
        pid = "p%d-%s" % (self.n, bot)
        rows = [{"kind": "open", "position_id": pid, "bot_id": bot, "decision_ms": {"entry": entry_ms, "exit": None}}]
        for s in stakes:
            if excluded:
                rows.append({"kind": "excluded", "position_id": pid, "bot_id": bot, "stake_sol": s, "decision_ms": {"entry": entry_ms, "exit": exit_ms}, "exclusion_reason": excluded, "legs": None})
            else:
                f, p = nets[s] if isinstance(nets, dict) else (nets[0] * s / 0.1, nets[1] * s / 0.1)
                rows.append({"kind": "trade", "position_id": pid, "bot_id": bot, "stake_sol": s, "decision_ms": {"entry": entry_ms, "exit": exit_ms}, "exit_reason": "timed",
                             "legs": {"total": {"flat_1p9": {"state": "filled", "net_lamports": int(f)}, "press_1p9": {"state": "filled", "net_lamports": int(p)}}}})
        self.rows(bot, rows, day=B.day_of(entry_ms), run=run)

    def build(self, now_ms, **kw):
        return B.build(str(self.root), now_ms, roots=(str(self.sim),), **kw)


def good_book(fx, n=12, flat=2_000_000, press=1_500_000, plc=(-1_000_000, -1_200_000)):
    """n priced trades at 0.10, spread over 11-17 .. 11-22, all positive (flat, press lamports at 0.10; scaled by stake for 0.25); placebo negative"""
    for i in range(n):
        d = D0 + (i % 6) * D
        e = max(d + 13 * H, S + 1 * H) + i * 60_000
        fx.entry(fx.bot, e, e + 4 * H, (flat, press))
        if fx.book_id != "c1nf-fwd-v1":
            fx.entry(fx.bot + "-plc", e, e + 4 * H, plc)


# ------------------------------------------------------------------------------------------------------------------------------ numbers
def test_cells_both_legs_top3_and_days(tmp_path):
    fx = Fx(tmp_path)
    # 0.10: nets flat [+5, +4, +3, +1, -1, +0.5] SOL-millis on 11-17 (3), 11-18 (3); press shifted down
    nets = [(5, 4), (4, 3), (3, 2), (1, 0), (-1, -2), (0.5, -0.5)]
    for i, (f, p) in enumerate(nets):
        e = S + (1 + i) * H + (D if i >= 3 else 0)
        fx.entry(fx.bot, e, e + 2 * H, (f * 1e6, p * 1e6))
    o = fx.build(S + 2 * D)
    c = o["cells"]["0.1"]
    assert c["n"] == 6 and c["days_complete"] == 6
    fl, pr = c["legs"]["flat"], c["legs"]["press"]
    assert fl["total_sol"] == pytest.approx(0.0125) and fl["total_ex_top3_sol"] == pytest.approx(0.0005)   # drops 5, 4, 3 millis
    assert pr["total_sol"] == pytest.approx(0.0065) and pr["total_ex_top3_sol"] == pytest.approx(-0.0025)   # press millis 4,3,2,0,-2,-0.5: drop 4,3,2 -> -2.5
    assert (fl["days_positive"], fl["days_not_positive"]) == (2, 4)           # four empty days count complete and not positive
    assert (fl["days_with_trades_positive"], fl["days_with_trades_not_positive"]) == (2, 0)
    assert (pr["days_positive"], pr["days_with_trades_positive"], pr["days_with_trades_not_positive"]) == (1, 1, 1)   # press day 2 nets -2.5 millis
    assert "0.25" in o["cells"] and o["cells"]["0.25"]["legs"]["flat"]["total_sol"] == pytest.approx(0.0125 * 2.5)
    assert fl["per_day"][0]["n"] == 3 and fl["per_day"][2]["n"] == 0
    assert fl["mean_sol"] == pytest.approx(0.0125 / 6)


def test_ex_top3_none_when_n_le_3_and_forward_only(tmp_path):
    fx = Fx(tmp_path)
    for i in range(3):
        fx.entry(fx.bot, S + (i + 1) * H, S + (i + 2) * H, (1e6, 1e6))
    fx.entry(fx.bot, S - H, S + H, (9e6, 9e6))             # decided before S: not forward (and counted as a pre-S row)
    o = fx.build(S + 2 * D)
    assert o["cells"]["0.1"]["n"] == 3 and o["cells"]["0.1"]["legs"]["flat"]["total_ex_top3_sol"] is None
    assert o["rows_decided_before_S"] > 0 and o["void"] is True


def test_incomplete_day_and_open_not_counted(tmp_path):
    fx = Fx(tmp_path, tape_end=B.day_start_ms("2026-11-18") + 30 * 60_000)    # only 11-17 is NOT complete (needs 90 min past its end)
    fx.entry(fx.bot, S + H, S + 2 * H, (1e6, 1e6))
    o = fx.build(S + D)
    assert o["forward_complete_days"] == [] and o["cells"]["0.1"]["n"] == 0 and o["cells"]["0.1"]["closed_on_incomplete_day"] == 1
    fx2 = Fx(tmp_path / "b", tape_end=B.day_start_ms("2026-11-18") + 90 * 60_000)
    fx2.entry(fx2.bot, S + H, S + 2 * H, (1e6, 1e6))
    assert fx2.build(S + D)["forward_complete_days"] == ["2026-11-17"]


def test_exclusions_dedup_per_position_and_share(tmp_path):
    fx = Fx(tmp_path)
    for i in range(8):
        fx.entry(fx.bot, S + (i + 1) * H, S + (i + 2) * H, (1e6, 1e6))
    fx.entry(fx.bot, S + 10 * H, S + 11 * H, None, excluded="feed_gap_exit")
    fx.entry(fx.bot, S + 12 * H, S + 13 * H, None, excluded="missing_v")
    e = fx.build(S + 3 * D)["entries"]
    assert e["forward_entries"] == 10 and e["excluded"] == 2 and e["excluded_share"] == pytest.approx(0.2)
    assert e["excluded_by_reason"] == {"feed_gap_exit": 1, "missing_v": 1}      # two stakes per position, counted once


# ------------------------------------------------------------------------------------------------------------------------------ the look and the bar
def test_before_look_is_not_a_read_and_has_no_verdict(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx)
    o = fx.build(L1 - 1)
    assert o["status"] == "before the look instant: not a read" == B.BEFORE_LOOK
    assert o["bar"]["shown"] is False and o["bar"]["verdict"] is None
    md = B.render(o)
    assert "**Status: before the look instant: not a read**" in md and "none (before the look instant: not a read)" in md and "NOT a read" in md


def test_go_at_l1_u4_under_both_legs(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx)
    o = fx.build(L1)
    assert o["look_state"] == "look" and o["bar"]["shown"] and o["bar"]["verdict"] == "GO"
    for leg in ("flat", "press"):
        assert all(v["met"] for v in o["bar"]["legs"][leg].values()) and "6_book_mean_sol_per_trade_gt_random_pick_mean" in o["bar"]["legs"][leg]
    assert "**Verdict: GO**" in B.render(o)


@pytest.mark.parametrize("kw,failed", [
    (dict(flat=2_000_000, press=-300_000), "1_total_net_sol_gt_0"),                # flat leg positive, pressure leg negative: NO-GO (both legs required)
    (dict(plc=(3_000_000, 3_000_000)), "6_book_mean_sol_per_trade_gt_random_pick_mean"),
])
def test_no_go_cases(tmp_path, kw, failed):
    fx = Fx(tmp_path)
    good_book(fx, **kw)
    o = fx.build(L1)
    assert o["bar"]["verdict"] == "NO-GO"
    assert any(not o["bar"]["legs"][leg][failed]["met"] for leg in ("flat", "press"))


def test_no_go_top3_concentration(tmp_path):
    fx = Fx(tmp_path)
    for i in range(12):
        e = S + (i + 1) * H + (i % 6) * D
        fx.entry(fx.bot, e, e + H, ((50_000_000 if i < 3 else -1_000_000), (50_000_000 if i < 3 else -1_000_000)))
    o = fx.build(L1)
    assert o["bar"]["legs"]["flat"]["1_total_net_sol_gt_0"]["met"] and not o["bar"]["legs"]["flat"]["2_total_ex_top3_gt_0"]["met"] and o["bar"]["verdict"] == "NO-GO"


def test_no_go_days_and_exclusions_and_pre_s(tmp_path):
    fx = Fx(tmp_path)
    for i in range(12):   # one big positive day, many small negative days
        e = S + (i + 1) * H
        fx.entry(fx.bot, e, e + H, (1_000_000, 1_000_000)) if i < 4 else fx.entry(fx.bot, S + (i % 5 + 1) * D + H, S + (i % 5 + 1) * D + 2 * H, (-1_000_000, -1_000_000))
    fx.entry(fx.bot + "-plc", S + H, S + 2 * H, (-1, -1))
    o = fx.build(L1)
    assert not o["bar"]["legs"]["flat"]["3_days_positive_gt_not_positive_among_days_with_a_closed_trade"]["met"]
    fx2 = Fx(tmp_path / "x")
    good_book(fx2)
    for i in range(6):
        fx2.entry(fx2.bot, S + (i + 1) * H, S + (i + 2) * H, None, excluded="feed_gap_exit")
    o2 = fx2.build(L1)
    assert o2["entries"]["excluded_share"] > 0.2 and not o2["bar"]["legs"]["flat"]["4_excluded_share_le_20pct_of_forward_entries"]["met"] and o2["bar"]["verdict"] == "NO-GO"
    fx3 = Fx(tmp_path / "y")
    good_book(fx3)
    fx3.entry(fx3.bot, S - 5 * H, S + H, (1e6, 1e6))
    o3 = fx3.build(L1)
    assert o3["void"] and not o3["bar"]["legs"]["flat"]["5_zero_rows_decided_before_S"]["met"] and o3["bar"]["verdict"] == "NO-GO"
    assert "BOOK VOID" in B.render(o3)


def test_too_few_trades_extend_then_too_rare(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx, n=6)
    o1 = fx.build(L1)
    assert o1["look_state"] == "extended" and "too few trades to judge" in o1["status"] and o1["bar"]["verdict"] is None
    o2 = fx.build(L2)
    assert o2["look_state"] == "too_rare" and o2["bar"]["verdict"] == "NO-GO" and "too rare to judge" in o2["status"]


def test_c1nf_has_no_placebo_condition_and_u2_has(tmp_path):
    fx = Fx(tmp_path, "c1nf-fwd-v1")
    good_book(fx)
    o = fx.build(L1)
    assert o["random_pick"] is None and o["bar"]["verdict"] == "GO"
    assert not any(k.startswith("6_") for k in o["bar"]["legs"]["flat"])
    fx2 = Fx(tmp_path / "u2", "osb-u2")
    good_book(fx2)
    assert any(k.startswith("6_") for k in fx2.build(L1)["bar"]["legs"]["flat"])


def test_placebo_line_next_to_book(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx)
    o = fx.build(L1)
    assert o["random_pick"]["0.1"]["legs"]["flat"]["mean_sol"] == pytest.approx(-0.001)
    md = B.render(o)
    assert "Random-pick (placebo) book" in md and "-0.001000" in md and "0.002000" in md


def test_twin_gets_no_bar(tmp_path):
    fx = Fx(tmp_path, "osb-u4-lat3010", extra_meta={"pricing_lat_entry_s": 3.010})
    good_book(fx)
    o = fx.build(L1)
    assert o["deciding"] is False and o["bar"]["verdict"] is None and "never deciding" in B.render(o)


def test_only_active_run_is_read(tmp_path):
    fx = Fx(tmp_path)
    run2 = fx.root / "runs" / "0002"
    run2.mkdir()
    fx.entry(fx.bot, S + H, S + 2 * H, (1e6, 1e6))                       # old run 0001 (replayed after a restart)
    fx.entry(fx.bot, S + H, S + 2 * H, (1e6, 1e6), run=run2)
    (fx.root / "book").unlink()
    (fx.root / "book").symlink_to(run2)
    assert fx.build(S + 2 * D)["cells"]["0.1"]["n"] == 1


# ------------------------------------------------------------------------------------------------------------------------------ labels, disclosures
def test_labels_and_best_of_n(tmp_path):
    for bid, lab in (("c1nf-fwd-v1", "model; no live C1-NF fills"), ("osb-u2", "model; no live fills for this rule")):
        fx = Fx(tmp_path / bid, bid)
        fx.entry(fx.bot, S + H, S + 2 * H, {0.1: (1e6, 1e6), 0.25: (1e6, 1e6), 0.5: (1e6, 1e6), 1.0: (1e6, 1e6)}, stakes=(0.1, 0.25, 0.5, 1.0))
        o = fx.build(S + 2 * D)
        assert o["cells"]["0.1"]["label"] == lab and o["cells"]["0.25"]["label"] == lab
        assert o["cells"]["0.5"]["label"] == "model only, extrapolated; not for steering" and o["cells"]["1"]["label"] == "model only, extrapolated; not for steering"
        md = B.render(o)
        assert "best of %d book rows; treat any good number as shrunk" % B.ROWS_PER_BOOK[o["decider"]] in md
    o = fx.build(S + 2 * D, n_watched=57)
    assert "best of 57 rows" in " ".join(d["text"] for d in o["disclosures"]) and o["n_books_watched_source"] == "--n-watched"


@pytest.mark.parametrize("name", [n for n in dir(T) if n.isupper()])
def test_disclosure_constants_are_verbatim_in_dec(name):
    assert getattr(T, name) in DEC_TEXT


def test_c1nf_disclosures_include_late_print_and_u_books_bounded_state(tmp_path):
    fx = Fx(tmp_path, "c1nf-fwd-v1", extra_meta={"pools_first_seen_in_warmup": 4321})
    md = B.render(fx.build(S + D))
    assert T.C1_LATE_PRINT in md and T.C1_SEC4.replace("<n_books_watched>", "12") in md and "pools first seen in warm-up: 4321" in md and "in-sample, post-hoc filter, September" in md
    for bid in ("osb-u4", "osb-u2"):
        fx = Fx(tmp_path / bid, bid)
        (fx.root / "gap_exclusions.json").write_text(json.dumps({"main": {"total": {"entries": 40, "feed_gap_timed": 3, "feed_gap_requested": 4}, "by_day": {}}}))
        md = B.render(fx.build(S + D))
        assert T.BOUNDED in md and "excluded 7 of 40 entries (3/4)" in md and "<n>" not in md.split("Disclosures (verbatim)")[1].replace("<n_books_watched>", "")
        assert "not recorded by the runner" in md
    assert T.U4_NS in B.render(Fx(tmp_path / "u4b", "osb-u4").build(S + D)) and T.U4_NS not in B.render(Fx(tmp_path / "u2b", "osb-u2").build(S + D))


# ------------------------------------------------------------------------------------------------------------------------------ guards, io
def test_never_open_names_refused_and_not_read(tmp_path):
    with pytest.raises(B.Refused):
        B.guarded(str(tmp_path), tmp_path / "tape-2026-11-17T12.jsonl")
    for part in ("ledger-src", "wallet-ledger", "duckdb-tmp"):
        with pytest.raises(B.Refused):
            B.guarded(str(tmp_path), tmp_path / part / "x")
    fx = Fx(tmp_path / "b")
    good_book(fx)
    (fx.root / "tape-2026-11-17T12.jsonl").write_text("CANARY-TAPE")
    for d in ("ledger-src", "wallet-ledger", "duckdb-tmp"):
        (fx.root / d).mkdir()
        (fx.root / d / "f.jsonl").write_text("CANARY-" + d)
    (fx.run / "ledger" / fx.bot / "other.jsonl").write_text("CANARY-OTHER")
    o = fx.build(L1)
    assert "CANARY" not in json.dumps(o) and "CANARY" not in B.render(o)


def test_unknown_book_and_bad_root_refused(tmp_path):
    fx = Fx(tmp_path)
    with pytest.raises(B.Refused):
        B.build(str(fx.root), L1)                               # not under the real SIM_ROOT
    (fx.root / "meta.json").write_text(json.dumps({"book_id": "osb-u9", "decider": "u4", "start_hour": "2026-11-17T12"}))
    with pytest.raises(B.Refused):
        fx.build(L1)
    other = tmp_path / "c1nf-open-sim" / "osb-u1"
    other.mkdir()
    with pytest.raises(B.Refused):
        B.load_book(str(other), roots=(str(fx.sim),))


def test_output_guard_and_out_dir(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx)
    o = fx.build(L1)
    with pytest.raises(B.Refused):
        B.write(o, tmp_path / "elsewhere", out_roots=(str(tmp_path / "board"),))
    with pytest.raises(B.Refused):
        B.output_guard("a " + "A" * 44 + " b", {})
    with pytest.raises(B.Refused):
        B.output_guard("", {"x": [{"mint": 1}]})
    B.output_guard(B.render(o), o)   # the real output passes


def test_write_files_and_sha(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx)
    o2 = B.write(fx.build(L1), tmp_path / "board", out_roots=(str(tmp_path / "board"),))
    md = (tmp_path / "board" / "osb-u4.md").read_bytes()
    j = json.loads((tmp_path / "board" / "osb-u4.json").read_text())
    assert j["md_sha256"] == hashlib.sha256(md).hexdigest() == o2["md_sha256"]
    assert j["paper_only"] is True and j["gate_read"] is False and j["schema"] == "fwd_book_board.v1"
    assert "No book has cleared the promotion gate" in md.decode() and "makes money" in md.decode()
    assert not [p for p in (tmp_path / "board").iterdir() if ".tmp." in p.name]


def test_main_exit_codes(tmp_path, monkeypatch):
    fx = Fx(tmp_path)
    good_book(fx)
    monkeypatch.setattr(B, "SIM_ROOT", str(fx.sim))
    monkeypatch.setattr(B, "OUT_ROOTS", (str(tmp_path / "board"),))
    assert B.main([str(fx.root), "--out-dir", str(tmp_path / "board"), "--now-ms", str(L1)]) == 0
    assert B.main([str(fx.root), "--out-dir", str(tmp_path / "nope"), "--now-ms", str(L1)]) == 2
    assert B.main([str(tmp_path), "--out-dir", str(tmp_path / "board")]) == 2
