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
LATE_TAPE = B.day_start_ms("2026-12-02") + 3 * H
DEC_TEXT = (ROOT / "DEC" / "DEC-035-live-learning-lane.md").read_text()


class Fx:
    def __init__(self, tmp, book_id="osb-u4", tape_end=TAPE_END, start_hour="2026-11-17T12", extra_meta=None):
        self.sim = tmp / "c1nf-open-sim"
        self.root = self.sim / book_id
        self.book_id = book_id
        dec = {"c1nf-fwd-v1": "c1nf", "osb-u4": "u4", "osb-u2": "u2", "osb-v2-u2": "u2", "osb-u1": "u1", "osb-u1-lowrep": "u1_lowrep"}[book_id.split("-lat")[0].split("-send")[0]]
        self.run = self.root / "runs" / "0001"
        self.run.mkdir(parents=True)
        (self.root / "book").symlink_to(self.run)
        meta = {"book_id": book_id, "decider": dec, "start_hour": start_hour, "pricing_lat_entry_s": 4.085, "send_lamports": 505000}
        meta.update(extra_meta or {})
        (self.root / "meta.json").write_text(json.dumps(meta))
        (self.root / "status.json").write_text(json.dumps({"book_id": book_id, "last_hour": "x", "last_hour_end_ms": tape_end, "active": True, "run": "0001"}))
        self.bot = "c1nf-fwd-" + book_id
        self.n = 0

    def rows(self, bot, rows, day="2026-11-17", run=None):
        d = (run or self.run) / "ledger" / bot
        d.mkdir(parents=True, exist_ok=True)
        with open(d / ("trades-%s.jsonl" % day), "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def entry(self, bot, entry_ms, exit_ms, nets, stakes=(0.1, 0.25), excluded=None, run=None, written_ms=None, cluster=None):
        """one position: open row + a trade row per stake (nets: {stake: (flat_lamports, press_lamports)}) or excluded rows"""
        self.n += 1
        pid = "p%d-%s" % (self.n, bot)
        meta = {"meta": {"cluster": cluster}} if cluster else {}
        rows = [{"kind": "open", "position_id": pid, "bot_id": bot, "decision_ms": {"entry": entry_ms, "exit": None}, **meta}]
        for s in stakes:
            if excluded:
                rows.append({"kind": "excluded", "position_id": pid, "bot_id": bot, "stake_sol": s, "decision_ms": {"entry": entry_ms, "exit": exit_ms}, "exclusion_reason": excluded, "legs": None, **({"written_ms": written_ms} if written_ms is not None else {})})
            else:
                f, p = nets[s] if isinstance(nets, dict) else (nets[0] * s / 0.1, nets[1] * s / 0.1)
                rows.append({"kind": "trade", "position_id": pid, "bot_id": bot, "stake_sol": s, "decision_ms": {"entry": entry_ms, "exit": exit_ms}, "exit_reason": "timed", **meta,
                             "legs": {"total": {"flat_1p9": {"state": "filled", "net_lamports": int(f)}, "press_1p9": {"state": "filled", "net_lamports": int(p)}}}})
        self.rows(bot, rows, day=B.day_of(entry_ms), run=run)

    def build(self, now_ms, n_watched=57, **kw):
        return B.build(str(self.root), now_ms, n_watched, roots=(str(self.sim),), **kw)

    def refuse_row(self, bot, entry_ms, reason="no_prints"):
        self.n += 1
        self.rows(bot, [{"kind": "refused", "position_id": "r%d" % self.n, "bot_id": bot, "decision_ms": {"entry": entry_ms, "exit": None}, "reason": reason}], day=B.day_of(entry_ms))


def good_book(fx, n=12, flat=2_000_000, press=1_500_000, plc=(-1_000_000, -1_200_000)):
    """n priced trades at 0.10, spread over 11-17 .. 11-22, all positive (flat, press lamports at 0.10; scaled by stake for 0.25); placebo negative"""
    for i in range(n):
        d = D0 + (i % 6) * D
        e = max(d + 13 * H, S + 1 * H) + i * 60_000
        fx.entry(fx.bot, e, e + 4 * H, (flat, press))
        if fx.book_id in ("osb-u4", "osb-u2", "osb-v2-u2"):
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
    for i in range(24):
        e = S + (i + 1) * H + (i % 5) * D
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
    fx = Fx(tmp_path, tape_end=LATE_TAPE)
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


@pytest.mark.parametrize("sfx", ["-lat3010", "-lat5590", "-send160k"])
def test_twin_and_fee_out_roots_refused(tmp_path, sfx):
    fx = Fx(tmp_path, "osb-u4" + sfx, extra_meta={"pricing_lat_entry_s": 3.010})
    with pytest.raises(B.Refused, match="not built"):
        fx.build(L1)


def test_only_active_run_is_read(tmp_path):
    fx = Fx(tmp_path)
    run2 = fx.root / "runs" / "0002"
    run2.mkdir()
    fx.entry(fx.bot, S + H, S + 2 * H, (1e6, 1e6))                       # old run 0001 (replayed after a restart)
    fx.entry(fx.bot, S + H, S + 2 * H, (1e6, 1e6), run=run2)
    (fx.root / "book").unlink()
    (fx.root / "book").symlink_to(run2)
    (fx.root / "status.json").write_text(json.dumps({"last_hour_end_ms": TAPE_END, "active": True, "run": "0002"}))
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
        assert "best of 57 book rows; treat any good number as shrunk" in md
    o = fx.build(S + 2 * D, n_watched=57)
    assert "best of 57 rows" in " ".join(d["text"] for d in o["disclosures"]) and o["n_books_watched_source"].startswith("--n-watched")


@pytest.mark.parametrize("name", [n for n in dir(T) if n.isupper() and not n.startswith("QP1118_") and n != "E1_CODE_SHA_V2"])  # QP1118_* are QP-ruling text, tested below
def test_disclosure_constants_are_verbatim_in_dec(name):
    assert getattr(T, name) in DEC_TEXT


def test_c1nf_disclosures_include_late_print_and_u_books_bounded_state(tmp_path):
    fx = Fx(tmp_path, "c1nf-fwd-v1", extra_meta={"pools_first_seen_in_warmup": 4321})
    md = B.render(fx.build(S + D))
    assert T.C1_LATE_PRINT in md and T.C1_SEC4.replace("<n_books_watched>", "57") in md and "pools first seen in warm-up: 4321" in md and "in-sample, post-hoc filter, September" in md
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
        B.build(str(fx.root), L1, 57)                           # not under the real SIM_ROOT
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
    assert B.main([str(fx.root), "--out-dir", str(tmp_path / "board"), "--now-ms", str(L1), "--n-watched", "57"]) == 0
    assert B.main([str(fx.root), "--out-dir", str(tmp_path / "nope"), "--now-ms", str(L1), "--n-watched", "57"]) == 2
    assert B.main([str(tmp_path), "--out-dir", str(tmp_path / "board"), "--n-watched", "57"]) == 2
    assert B.main([str(fx.root), "--out-dir", str(tmp_path / "board"), "--now-ms", str(L1), "--n-watched", "29"]) == 2


# ------------------------------------------------------------------------------------------------------------------------------ PR #1435 review fixes


def test_n_watched_required_and_floor(tmp_path):
    fx = Fx(tmp_path)
    for bad in (None, 29, 0):
        with pytest.raises(B.Refused, match="n-watched"):
            fx.build(L1, n_watched=bad)
    o = fx.build(L1, n_watched=88)
    assert "best of 88 book rows" in B.render(o) and "88" in o["disclosures"][0]["text"]
    with pytest.raises(SystemExit):
        B.main([str(fx.root), "--out-dir", str(tmp_path / "b")])      # argparse: --n-watched is required


def test_u_warmup_never_uses_pools_first_seen(tmp_path):
    for bid in ("osb-u4", "osb-u2"):
        fx = Fx(tmp_path / bid, bid, extra_meta={"pools_first_seen_in_warmup": 4321})
        o = fx.build(S + D)
        txt = " ".join(d["text"] for d in o["disclosures"])
        assert "4321" not in txt and "4321" not in B.render(o) and "excluded and counted: not recorded by the runner" in txt
        assert o["pools_first_seen_in_warmup"] is None


def test_read_is_frozen_as_of_the_read_instant(tmp_path):
    fx = Fx(tmp_path, tape_end=LATE_TAPE)
    good_book(fx)                                                  # n = 10 at L1: the read instant is L1
    first = fx.build(L1)
    assert first["bar"]["verdict"] == "GO" and first["bar"]["read_instant_utc"] == B.utc(L1)
    for i in range(10):                                            # later bad trades never change the frozen result
        e = L1 + D + i * H
        fx.entry(fx.bot, e, e + H, (-9_000_000, -9_000_000))
        fx.entry(fx.bot + "-plc", e, e + H, (9_000_000, 9_000_000))
    later = fx.build(L1 + 3 * D)
    assert later["bar"]["verdict"] == "GO" and later["bar"]["frozen"] == first["bar"]["frozen"]
    assert later["cells"]["0.1"]["legs"]["flat"]["total_sol"] < 0            # the live cells moved on
    md = B.render(later)
    assert "not the read" in md and "Frozen result as of the read instant" in md


def test_n_below_10_at_l1_is_judged_at_l2_exactly(tmp_path):
    fx = Fx(tmp_path, tape_end=LATE_TAPE)
    good_book(fx, n=6)                                             # 5 trades by L1, 6 by L2
    assert fx.build(L1)["look_state"] == "extended" and fx.build(L1)["bar"]["verdict"] is None
    for i in range(14):                                            # trades AFTER L2 take n past 10 now
        e = L2 + D + i * H
        fx.entry(fx.bot, e, e + H, (1_000_000, 1_000_000))
        fx.entry(fx.bot + "-plc", e, e + H, (-1, -1))
    o = fx.build(L2 + 3 * D)
    assert o["cells"]["0.1"]["n"] >= 10                            # n now
    assert o["look_state"] == "too_rare" and o["bar"]["verdict"] == "NO-GO" and "as of L2" in o["status"]
    assert B.render(o).count("too rare to judge") >= 1


def day_cut(day):
    return B.day_start_ms(day) + D + B.BOOK_LAG_MS


def test_read_at_first_completed_day_that_reaches_10(tmp_path):
    fx = Fx(tmp_path, tape_end=LATE_TAPE)
    good_book(fx, n=6)                                             # n < 10 at L1
    for i in range(6):                                             # day 11-22 brings n to >= 10
        e = L1 + H + i * 60_000
        fx.entry(fx.bot, e, e + H, (1_000_000, 1_000_000))
        fx.entry(fx.bot + "-plc", e, e + H, (-1_000_000, -1_000_000))
    c = day_cut("2026-11-22")
    assert fx.build(L1)["look_state"] == "extended" and fx.build(c - 1)["look_state"] == "extended"
    o = fx.build(c)
    assert o["look_state"] == "look" and o["bar"]["read_instant_utc"] == B.utc(c) and o["bar"]["frozen"]["cell_0.1"]["n"] >= 10
    for i in range(5):                                             # later rows are not in the frozen cell
        e = c + H + i * H
        fx.entry(fx.bot, e, e + H, (-9_000_000, -9_000_000))
        fx.entry(fx.bot + "-plc", e, e + H, (9_000_000, 9_000_000))
    later = fx.build(L2 + 2 * D)
    assert later["bar"]["read_instant_utc"] == B.utc(c) and later["bar"]["frozen"] == o["bar"]["frozen"] and later["bar"]["verdict"] == o["bar"]["verdict"]


def test_never_reaches_10_reads_at_l2(tmp_path):
    fx = Fx(tmp_path, tape_end=LATE_TAPE)
    good_book(fx, n=6)
    o = fx.build(L2)
    assert o["look_state"] == "too_rare" and o["bar"]["read_instant_utc"] == B.utc(L2) and o["bar"]["verdict"] == "NO-GO"


def test_n_ge_10_at_l1_reads_at_l1(tmp_path):
    fx = Fx(tmp_path, tape_end=LATE_TAPE)
    good_book(fx)
    o = fx.build(L2 + D)
    assert o["look_state"] == "look" and o["bar"]["read_instant_utc"] == B.utc(L1)


def test_waiting_for_tape_is_not_a_read(tmp_path):
    fx = Fx(tmp_path)                                              # tape ends 11-23T03, before L2
    good_book(fx, n=6)
    o = fx.build(L2 + D)
    assert o["look_state"] == "waiting_tape" and o["bar"]["verdict"] is None and "not a read" in o["status"]


def test_replay_in_progress_shows_no_cells_no_verdict(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx)
    # restart: the new run replays from warm-up, status.json moves (earlier tape end, new run, not yet active) while `book` still points at the old run
    (fx.root / "status.json").write_text(json.dumps({"last_hour": "warm", "last_hour_end_ms": D0 - 5 * D, "active": False, "run": "0002"}))
    o = fx.build(L2 + D)
    assert o["look_state"] == "replay" and o["cells"] == {} and o["bar"]["verdict"] is None and "replay in progress: not a read" in o["status"]
    assert o["entries"]["forward_entries"] == 0
    # a status that is active but names a different run than the `book` link is also refused as a read
    (fx.root / "status.json").write_text(json.dumps({"last_hour_end_ms": TAPE_END, "active": True, "run": "0002"}))
    assert fx.build(L1)["look_state"] == "replay"


def test_engine_refused_buys_count_in_the_excluded_share(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx, n=12)
    for i in range(30):
        fx.refuse_row(fx.bot, S + (i + 1) * 60_000)
    o = fx.build(L1)
    e = o["entries"]
    assert e["engine_refused"] == 30 and e["forward_entries"] == 42 and e["excluded"] == 30 and e["excluded_share"] == pytest.approx(30 / 42)
    assert o["bar"]["verdict"] == "NO-GO" and not o["bar"]["legs"]["flat"]["4_excluded_share_le_20pct_of_forward_entries"]["met"]
    assert e["excluded_by_reason"] == {"refused:no_prints": 30}


def test_run_link_outside_book_folder_refused(tmp_path):
    fx = Fx(tmp_path)
    outside = tmp_path / "elsewhere" / "0001"
    (outside / "ledger" / "x").mkdir(parents=True)
    (fx.root / "book").unlink()
    (fx.root / "book").symlink_to(outside)
    with pytest.raises(B.Refused, match="active run"):
        fx.build(L1)
    assert B.main([str(fx.root), "--out-dir", str(tmp_path / "board"), "--n-watched", "57"]) == 2


def test_symlinked_json_outside_refused_cleanly(tmp_path, monkeypatch):
    fx = Fx(tmp_path)
    ext = tmp_path / "ext.json"
    ext.write_text((fx.root / "meta.json").read_text())
    (fx.root / "meta.json").unlink()
    (fx.root / "meta.json").symlink_to(ext)
    with pytest.raises(B.Refused, match="outside"):
        fx.build(L1)
    monkeypatch.setattr(B, "SIM_ROOT", str(fx.sim))
    assert B.main([str(fx.root), "--out-dir", str(tmp_path / "board"), "--n-watched", "57"]) == 2


def test_bad_rows_are_counted_not_crashes(tmp_path, monkeypatch):
    fx = Fx(tmp_path)
    good_book(fx, n=3)
    d = fx.run / "ledger" / fx.bot
    with open(d / ("trades-%s.jsonl" % B.day_of(S)), "a") as fh:
        fh.write("[1, 2]\n{\"kind\": \"open\"}\n{\"kind\": \"trade\", \"position_id\": \"z\"}\nnot json\n")
    o = fx.build(S + 2 * D)
    assert o["malformed_ledger_rows"] == 4 and o["cells"]["0.1"]["n"] == 3


def test_not_built_items_listed(tmp_path):
    fx = Fx(tmp_path)
    o = fx.build(S + D)
    md = B.render(o)
    assert len(o["not_built"]) >= 5 and "## Not built here" in md
    for k in ("twin", "ledger-incomplete", "peak capital at risk", "pre-S row scan"):
        assert any(k in x for x in o["not_built"]) and k in md


def test_ledger_files_go_through_the_guard(tmp_path):
    fx = Fx(tmp_path)
    good_book(fx, n=2)
    outside = tmp_path / "out.jsonl"
    outside.write_text('{"kind": "open", "position_id": "q", "decision_ms": {"entry": %d}}\n' % (S + H))
    (fx.run / "ledger" / fx.bot / "trades-2026-11-30.jsonl").symlink_to(outside)
    with pytest.raises(B.Refused, match="outside"):
        fx.build(S + 2 * D)


def test_exclusion_written_after_read_instant_stays_in_denominator_only(tmp_path):
    fx = Fx(tmp_path, tape_end=LATE_TAPE)
    good_book(fx)
    for i in range(6):
        fx.entry(fx.bot, S + (i + 1) * H, S + (i + 2) * H, None, excluded="feed_gap_exit", written_ms=L1 + D)     # written after the read instant L1
    live = fx.build(L1 + 2 * D)
    assert live["bar"]["frozen"]["entries"]["excluded"] == 0 and live["bar"]["frozen"]["entries"]["forward_entries"] == 16
    assert live["bar"]["frozen"]["entries"]["excluded_share"] == 0 and live["bar"]["verdict"] == "GO"
    assert live["entries"]["excluded"] == 6                         # the live (unfrozen) view sees them
    fx2 = Fx(tmp_path / "w", tape_end=LATE_TAPE)
    good_book(fx2)
    for i in range(6):
        fx2.entry(fx2.bot, S + (i + 1) * H, S + (i + 2) * H, None, excluded="feed_gap_exit", written_ms=L1 - H)   # written before: counted
    o = fx2.build(L1 + 2 * D)
    assert o["bar"]["frozen"]["entries"]["excluded"] == 6 and o["bar"]["verdict"] == "NO-GO"


def test_exclusion_without_written_ms_counts_and_is_reported(tmp_path):
    fx = Fx(tmp_path, tape_end=LATE_TAPE)
    good_book(fx)
    for i in range(6):
        fx.entry(fx.bot, S + (i + 1) * H, S + (i + 2) * H, None, excluded="missing_v")
    o = fx.build(L1 + 2 * D)
    e = o["bar"]["frozen"]["entries"]
    assert e["excluded"] == 6 and e["excluded_without_written_ms"] == 6 and o["bar"]["verdict"] == "NO-GO"
    assert "no written_ms (counted as excluded): 6" in B.render(o)


# ------------------------------------------------------------------------------------------------------------------------------ U1 (osb-u1, osb-u1-lowrep)
# md5 of the board JSON / MD of the three existing books on the fixtures below, taken on the code BEFORE the U1 change (main aa26cba9); they must not move.
# 2026-11-18 (QP-RULING-1118-u2-v2): the two osb-u2 rows were moved deliberately (osb-u2 now also prints the v1-defect disclosure); the c1nf and u4 rows are unchanged.
BASELINE_MD5 = {
    'c1nf-fwd-v1/before': ('8d692d61a409ab0d7062338b733d37b1', 'ec6490a1270407d6325fa36ede8ff8e4'),
    'c1nf-fwd-v1/look': ('01209671ca5f5d3020c5f99877d37b50', 'b50a6f89d006697bab7a400bc4e8d8fb'),
    'osb-u2/before': ('ad1e4c1326e5a9a6befd94c885fad7a0', 'b1da65ce7fe8c4eaa289cba429ba3fa2'),
    'osb-u2/look': ('7ccf6cf3d7646d5e50ba1cb48f5553aa', '7c28897aef555623c07e7d4c2f62991a'),
    'osb-u4/before': ('c0068feddabe3d447eccba0d4bb16874', 'f73a0cc12d44e68c9abb359361ace6d0'),
    'osb-u4/look': ('613cf88e9dcc2b2862b82502e213266e', '10f27d0472a44669094ba3f5776aca0d'),
}


def existing_book_hashes(tmp_path):
    """c1nf / u4 / u2 fixtures incl. 0.5 and 1 SOL stakes, a gap file and a counts row; md5 of board JSON and MD before the look and at the look"""
    got = {}
    for bid in ("c1nf-fwd-v1", "osb-u4", "osb-u2"):
        fx = Fx(tmp_path / bid, bid, extra_meta={"pools_first_seen_in_warmup": 77})
        good_book(fx)
        for k in range(3):
            e = S + (k + 2) * H
            fx.entry(fx.bot, e, e + 3 * H, {0.1: (1e6, 1e6), 0.25: (2e6, 2e6), 0.5: (3e6, 3e6), 1.0: (4e6, 4e6)}, stakes=(0.1, 0.25, 0.5, 1.0))
        (fx.root / "gap_exclusions.json").write_text(json.dumps({"main": {"total": {"entries": 40, "feed_gap_timed": 3, "feed_gap_requested": 4},
                                                                         "by_day": {"2026-11-17": {"entries": 5, "feed_gap_timed": 1, "feed_gap_requested": 0, "excluded_share": 0.2}}}}))
        (fx.root / "counts.jsonl").write_text(json.dumps({"hour": "2026-11-17T12", "warmup": False, "entries": 1, "decider": {"a": 2}, "book": {"b": 3}}) + "\n")
        for nm, now in (("before", L1 - 1), ("look", L1)):
            o = fx.build(now)
            assert {"0.5", "1"} <= set(o["cells"])
            got[bid + "/" + nm] = (hashlib.md5(json.dumps(o, sort_keys=True).encode()).hexdigest(), hashlib.md5(B.render(o).encode()).hexdigest())
    return got


def test_existing_three_books_byte_identical_before_and_after_u1(tmp_path):
    assert existing_book_hashes(tmp_path) == BASELINE_MD5


U1_COUNTS = [
    {"hour": "2026-11-17T22", "warmup": True, "entries": 0, "decider": {"u1_launchers_listed": 2890, "graduates_seen": 9}, "book": {}},
    {"hour": "2026-11-18T01", "warmup": False, "entries": 1, "decider": {"graduates_seen": 10, "universe_graduates": 8, "gate_pass": 3, "gate_fail": 5, "entries": 2, "cap_open": 1, "cap_open_stale": 7,
                                                                       "mean_hist_sum": 12, "entry_hist_own": 2, "entry_hist_funder": 1, "u1_fetch_once_unlinked": 4, "hub_H1": 1, "stop_exits": 1, "u1_rpc_calls": 6}, "book": {}},
    {"hour": "2026-11-19T01", "warmup": False, "entries": 0, "decider": {"graduates_seen": 10, "universe_graduates": 9, "u1_fetch_once_unlinked": 1}, "book": {}},
]


def u1_fx(tmp, bid="osb-u1", counts=U1_COUNTS):
    fx = Fx(tmp, bid, start_hour="2026-11-18T00")
    fx.start = B.hour_ms("2026-11-18T00")
    (fx.root / "counts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in counts))
    return fx


def u1_book(fx, n=12):
    for i in range(n):
        e = fx.start + H + (i % 5) * D + i * 60_000
        fx.entry(fx.bot, e, e + 4 * H, (2_000_000, 1_500_000), stakes=(0.1, 0.25, 0.5))


def test_u1_before_look_no_verdict_no_random_pick_columns(tmp_path):
    fx = u1_fx(tmp_path)
    u1_book(fx)
    o = fx.build(fx.start + 2 * D, n_watched=39)
    assert o["decider"] == "u1" and o["deciding"] is True and o["status"] == B.BEFORE_LOOK and o["random_pick"] is None
    assert o["placebo"]["state"] == "placebo: built once at the look"
    md = B.render(o)
    assert "placebo: built once at the look" in md and "Random-pick (placebo) book" not in md and "U1 board: osb-u1" in md
    assert "**Verdict: none (before the look instant: not a read)**" in md
    assert {"0.1", "0.25", "0.5"} <= set(o["cells"]) and o["cells"]["0.5"]["label"] == B.LABEL_EXTRAP and o["cells"]["0.1"]["label"] == "model; no live fills for this rule"
    assert o["S_utc"] == "2026-11-18T00:00:00Z" and o["L1_utc"] == "2026-11-23T00:00:00Z"


def test_u1_n_watched_floor_is_39_and_rows(tmp_path):
    fx = u1_fx(tmp_path)
    for bad in (None, 30, 38):
        with pytest.raises(B.Refused, match="n-watched"):
            fx.build(L1, n_watched=bad)
    o = fx.build(fx.start + D, n_watched=39)
    assert "best of 39 book rows" in B.render(o) and "adds none" in B.render(o)
    assert fx.build(fx.start + D, n_watched=57)["n_books_watched"] == 57
    assert B.ROWS_PER_BOOK["u1"] == 9 and B.ROWS_PER_BOOK["u1_lowrep"] == 0
    fx2 = Fx(tmp_path / "u4", "osb-u4")
    assert fx2.build(S + D, n_watched=30)["n_books_watched"] == 30       # the existing floor of 30 is unchanged for the other three books


def test_u1_thirteen_disclosures_verbatim_from_runner_and_dec(tmp_path):
    import c1nf_forward_sim as C
    import u1_forward_sim as U
    fx = u1_fx(tmp_path)
    (fx.root / "gap_exclusions.json").write_text(json.dumps({"main": {"total": {"entries": 40, "feed_gap_timed": 3, "feed_gap_requested": 4}, "by_day": {}}}))
    o = fx.build(fx.start + D, n_watched=57)
    txt = {d["id"].split(" ")[0]: d["text"] for d in o["disclosures"]}
    assert [d["id"].split(" ")[0] for d in o["disclosures"]][:13] == [str(i) for i in range(1, 14)]
    credits = txt["1"].split("Helius credits used to date: ")[1][:-1]
    assert txt["1"] == T.U1_SEC9.replace("<n_books_watched>", "57").replace("<n>", credits) and "best of 57 rows" in txt["1"]
    assert "credits file is never opened" in txt["1"] and "forward RPC calls so far (u1_rpc_calls): 6" in txt["1"]
    for i, c in (("2", U.FWD_DISCLOSURE), ("3", U.FETCH_ONCE_DISCLOSURE), ("4", U.RATE_DISCLOSURE), ("7", U.H4_DISCLOSURE), ("9", U.LOWREP_DISCLOSURE), ("10", U.PLACEBO_NO_RPC_DISCLOSURE)):
        assert txt[i] == c
    assert txt["6"] == U.CAP_OPEN_NOT_COMPUTED
    assert txt["8"] == C.GAP_DISCLOSURE.format(n=7, N=40, timed=3, requested=4) and "excluded 7 of 40 entries (3/4)" in txt["8"]
    assert txt["5"].endswith(U.PLACEBO_DISCLOSURE) and "[not built:" in txt["5"]
    assert txt["11"] == T.U1_PILOT_D1 and txt["12"] == T.U1_PILOT_D2 and "(5)" in txt["13"]
    md = B.render(o)
    for c in (U.FWD_DISCLOSURE, U.H4_DISCLOSURE, U.CAP_OPEN_NOT_COMPUTED, T.U1_PILOT_D1, T.U1_PILOT_D2):
        assert c in md
    # the constants held in the text module and the imported ones are verbatim in the DEC-035 record
    for c in (T.U1_SEC9, T.U1_PILOT_D1, T.U1_PILOT_D2, U.FWD_DISCLOSURE, U.FETCH_ONCE_DISCLOSURE, U.RATE_DISCLOSURE, U.H4_DISCLOSURE, U.LOWREP_DISCLOSURE, U.PLACEBO_NO_RPC_DISCLOSURE, U.CAP_OPEN_NOT_COMPUTED):
        assert c in DEC_TEXT
    assert U.PLACEBO_DISCLOSURE.split("{sha}")[0] in DEC_TEXT and C.GAP_DISCLOSURE.split("{n}")[0] in DEC_TEXT


def test_u1_cap_open_stale_never_a_number(tmp_path):
    fx = u1_fx(tmp_path)
    o = fx.build(fx.start + 2 * D, n_watched=39)
    assert "cap_open_stale" not in o["counters_forward_hours_summed"] and "cap_open_stale" not in json.dumps(o["u1_counters_by_day"])
    assert "cap_open_stale not computed" in B.render(o) and "7 entries were refused" not in B.render(o)
    assert o["counters_forward_hours_summed"]["cap_open"] == 1


def test_u1_fetch_once_unlinked_funder_incomplete_and_coverage_not_built(tmp_path):
    fx = u1_fx(tmp_path)
    o = fx.build(fx.start + 3 * D, n_watched=39)
    assert o["u1_fetch_once_unlinked"]["total"] == 5 and o["u1_fetch_once_unlinked"]["by_day"] == {"2026-11-18": 4, "2026-11-19": 1}
    assert o["funder_incomplete_days"]["days"] == ["2026-11-18"]            # 4 of 10 > 20%; 1 of 10 is not
    assert o["entry_history"]["mean_abs_H_at_gate_passes"] == pytest.approx(4.0) and o["entry_history"]["funder_linked_share"] == pytest.approx(1 / 3)
    cov = o["coverage_final"]
    assert cov["built"] is False and cov["u1_launchers_listed_all_rows_summed"] == 2890
    md = B.render(o)
    assert "u1_fetch_once_unlinked" in md and "W coverage (final figure): not built" in md and "hub_H1" in md
    assert any("coverage" in x and "not built" in x for x in o["not_built"])


def test_u1_bar_never_prints_go_and_conditions_6_7_not_evaluated(tmp_path):
    fx = u1_fx(tmp_path, counts=[])
    u1_book(fx)
    o = fx.build(fx.start + 5 * D, n_watched=39)
    assert o["look_state"] == "look" and o["bar"]["shown"] is True
    assert o["bar"]["verdict"] == B.U1_NOT_DECIDED and o["bar"]["verdict"] != "GO"
    for leg in ("flat", "press"):
        c = o["bar"]["legs"][leg]
        assert [k for k, v in c.items() if v["met"] is None] == ["6_book_mean_sol_per_trade_gt_placebo_mean", "7_total_net_sol_gt_0_after_dropping_the_largest_cluster"]   # fixture rows carry no meta.cluster
        assert all(v["met"] for k, v in c.items() if k[0] in "12345")
        assert "meta.cluster missing on 10 of 10" in c["7_total_net_sol_gt_0_after_dropping_the_largest_cluster"]["value"]
    md = B.render(o)
    assert "not evaluated here" in md and "**Verdict: GO**" not in md and "NOT DECIDED HERE" in md


def test_u1_condition_1_5_failure_is_no_go(tmp_path):
    fx = u1_fx(tmp_path, counts=[])
    for i in range(12):
        e = fx.start + H + (i % 5) * D + i * 60_000
        fx.entry(fx.bot, e, e + 4 * H, (-2_000_000, -1_500_000), stakes=(0.1,))
    o = fx.build(fx.start + 5 * D, n_watched=39)
    assert o["bar"]["verdict"] == "NO-GO"


def u1_cluster_book(fx, plan):
    """plan: [(cluster, flat_lamports, press_lamports)] one closed 0.1/0.25/0.5 position each, spread over the first 5 days"""
    for i, (c, f, p) in enumerate(plan):
        e = fx.start + H + (i % 5) * D + i * 60_000
        fx.entry(fx.bot, e, e + 4 * H, (f, p), stakes=(0.1, 0.25, 0.5), cluster=c)


def test_u1_condition_7_from_meta_cluster_and_cluster_counts(tmp_path):
    fx = u1_fx(tmp_path, counts=[])
    # cluster A: 4 trades of +3 millis; B: 2 of +1; C..F: 1 of +1 each (12 trades, 6 clusters). Without ALL of A the total is still > 0.
    u1_cluster_book(fx, [("aaaaaaaaaaaa", 3_000_000, 3_000_000)] * 4 + [("bbbbbbbbbbbb", 1_000_000, 1_000_000)] * 2 + [(c * 12, 1_000_000, 1_000_000) for c in "cdef"] + [("gggggggggggg", 1_000_000, 1_000_000)] * 2)
    o = fx.build(fx.start + 5 * D, n_watched=39)
    cl = o["clusters"]
    assert cl["entries"]["distinct_clusters_entered"] == 7 and cl["entries"]["entries_with_cluster"] == 12 and cl["entries"]["largest_cluster_share_of_entries"] == pytest.approx(4 / 12)
    assert cl["legs"]["flat"]["total_sol"] == pytest.approx(0.020) and cl["legs"]["flat"]["total_ex_largest_cluster_sol"] == pytest.approx(0.008)
    for leg in ("flat", "press"):
        c7 = o["bar"]["legs"][leg]["7_total_net_sol_gt_0_after_dropping_the_largest_cluster"]
        assert c7["met"] is True and c7["value"] == pytest.approx(0.006)   # frozen at L1: day 5 is not complete, 10 of 12 trades closed
    assert o["bar"]["verdict"] == B.U1_NOT_DECIDED and o["bar"]["frozen"]["clusters_0.1"]["legs"]["flat"]["total_ex_largest_cluster_sol"] == pytest.approx(0.006)
    md = B.render(o)
    assert "7 distinct over 12 forward entries" in md and "aaaaaaaaaaaa" not in md and "aaaaaaaaaaaa" not in json.dumps(o)   # never an identifier


def test_u1_condition_7_fails_when_one_cluster_carries_the_total(tmp_path):
    fx = u1_fx(tmp_path, counts=[])
    u1_cluster_book(fx, [("aaaaaaaaaaaa", 5_000_000, 5_000_000)] * 4 + [(c * 12, -500_000, -500_000) for c in "bcdefghi"])
    o = fx.build(fx.start + 5 * D, n_watched=39)
    assert o["bar"]["legs"]["flat"]["1_total_net_sol_gt_0"]["met"] is True
    assert o["bar"]["legs"]["flat"]["7_total_net_sol_gt_0_after_dropping_the_largest_cluster"]["met"] is False and o["bar"]["verdict"] == "NO-GO"


def test_u1_n429_per_day_status_counts_not_built_and_partial_day(tmp_path):
    counts = [{"hour": "2026-11-18T01", "warmup": False, "decider": {"graduates_seen": 10, "u1_fetch_once_unlinked": 4, "u1_n429": 2, "status_resolved": 99}},
              {"hour": "2026-11-20T01", "warmup": False, "decider": {"graduates_seen": 10, "u1_fetch_once_unlinked": 5, "u1_n429": 0}}]
    fx = u1_fx(tmp_path, counts=counts)
    fx.root.joinpath("status.json").write_text(json.dumps({"last_hour": "x", "last_hour_end_ms": fx.start + 2 * D + 90 * 60_000 + 5 * H, "active": True, "run": "0001"}))   # 11-18 and 11-19 complete; 11-20 in progress
    o = fx.build(fx.start + 3 * D, n_watched=39)
    assert o["u1_counters_by_day"]["2026-11-18"]["u1_n429"] == 2 and "status_resolved" not in json.dumps(o["u1_counters_by_day"])
    assert o["funder_incomplete_days"]["days"] == ["2026-11-18"] and o["funder_incomplete_days"]["partial_days_flagged_so_far"] == ["2026-11-20"]
    assert any("funder status counts" in x and "not built" in x for x in o["not_built"]) and "partial days flagged so far" in B.render(o)


def test_u1_disclosure_13_verbatim_in_dec(tmp_path):
    assert T.U1_DISC13 in DEC_TEXT and "(5) above for this book" in T.U1_DISC13
    o = u1_fx(tmp_path).build(S, n_watched=39)
    assert [d["text"] for d in o["disclosures"] if d["id"] == "13"] == [T.U1_DISC13]


def test_u1_lowrep_shown_beside_no_bar(tmp_path):
    fx = u1_fx(tmp_path, "osb-u1-lowrep", counts=[])
    u1_book(fx)
    o = fx.build(fx.start + 5 * D, n_watched=39)
    assert o["decider"] == "u1_lowrep" and o["deciding"] is False and o["bar"]["verdict"] is None and o["bar"]["applicable"] is False
    md = B.render(o)
    assert "bar: not applicable" in md and "Verdict:" not in md and "Conditions" not in md and "shown beside the book" in md
    assert "adds no row to n_books_watched" in " ".join(d["text"] for d in o["disclosures"])
    assert len(o["disclosures"]) >= 13


def test_u1_twin_and_mismatch_refused(tmp_path):
    fx = u1_fx(tmp_path)
    with pytest.raises(B.Refused):
        Fx(tmp_path / "t", "osb-u1-lat3010", extra_meta={"decider": "u1"}).build(L1)
    (fx.root / "meta.json").write_text(json.dumps({"book_id": "osb-u1", "decider": "u4", "start_hour": "2026-11-18T00"}))
    with pytest.raises(B.Refused, match="does not match"):
        fx.build(L1, n_watched=39)


def test_u1_output_board_safe_and_written(tmp_path):
    fx = u1_fx(tmp_path)
    u1_book(fx)
    o = fx.build(fx.start + 5 * D, n_watched=39)
    keys = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                keys.add(k)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(o)
    assert not (B.FORBIDDEN_KEYS & keys)
    B.output_guard(B.render(o), o)
    o2 = B.write(o, tmp_path / "board", out_roots=(str(tmp_path / "board"),))
    assert json.loads((tmp_path / "board" / "osb-u1.json").read_text())["md_sha256"] == o2["md_sha256"]


def test_u1_replay_in_progress_shows_no_cells(tmp_path):
    fx = u1_fx(tmp_path)
    u1_book(fx)
    (fx.root / "status.json").write_text(json.dumps({"last_hour": "warm", "last_hour_end_ms": fx.start - D, "active": False, "run": "0002"}))
    o = fx.build(fx.start + 6 * D, n_watched=39)
    assert o["look_state"] == "replay" and o["cells"] == {} and o["u1_counters_by_day"] == {} and o["bar"]["verdict"] is None


# ------------------------------------------------------------------------------------------------------------------------------ osb-v2-u2 (QP-RULING-1118-u2-v2 section 4)
RULING_FILE = Path("/data/mal/hunt-1008/ops/QP-RULING-1118-u2-v2.md")
V1_DEFECT_TEXT = ("osb-u2 (U2 v1) has a simulator defect found from code on 2026-11-18 (QP-RULING-1118-u2-v2): its pricing engine was not fed warm-up hours and dropped pools quiet for more than 20,000 slots (about 2.2 h), while U2 picks pools whose last print can be up to 6 h old. Picks the engine had not seen were refused (no_prints), and at the first mark the slot clock was not ready (clock_not_ready). As of <instant>: <r> of <N> forward entries refused (no_prints <a>, clock_not_ready <b>); placebo <r_p> of <N_p>. Its priced trades are a subset biased towards recently active pools. osb-u2 cannot license U2 by itself; its look is reported beside osb-v2-u2 and never pooled with it.")
V2_TEXT = ("osb-v2-u2 (U2 v2) uses the same decider and constants as osb-u2 (decision md5 equal on fixtures) and differs only in pricing coverage: its engine is fed the 7 h before S2 and never prunes pools (trim_history only). It is the second book of the same rule, started after the first book's early board was visible; treat any good number as shrunk (best of <n_books_watched> rows, osb-u2's 9 included).")


def test_ruling_constants_match_ruling_file():
    assert T.QP1118_U2_V1_DEFECT == V1_DEFECT_TEXT and T.QP1118_V2_U2 == V2_TEXT
    try:
        txt = RULING_FILE.read_text()
    except OSError:
        pytest.skip("ruling file not readable here")
    assert '"' + ENGINE_WARMUP_TEXT + '"' in txt
    assert '> "' + V1_DEFECT_TEXT + '"' in txt and '> "' + V2_TEXT + '"' in txt


ENGINE_WARMUP_TEXT = "engine state starts at S2 - 7 h: a pool whose pricing would need a print before then (V fallback) is priced as the engine prices it, or excluded and counted"


def test_v2_u2_builds_like_u2_with_its_disclosure(tmp_path):
    fx = Fx(tmp_path / "v2", "osb-v2-u2")
    good_book(fx)
    fx.refuse_row(fx.bot, S + 3 * H)
    o = fx.build(L1, n_watched=66)
    assert o["decider"] == "u2" and o["book_id"] == "osb-v2-u2" and o["bar"]["shown"] and o["n_books_watched"] == 66
    md = B.render(o)
    assert V2_TEXT.replace("<n_books_watched>", "66") in md
    assert "<n_books_watched>" not in md
    see = "[see osb-u2 board %s]" % o["generated_utc"]
    want = V1_DEFECT_TEXT.replace("<instant>", o["generated_utc"])
    for br in ("<r_p>", "<N_p>", "<r>", "<N>", "<a>", "<b>"):
        want = want.replace(br, see)
    assert want in md
    assert T.QP1118_V2_ENGINE_WARMUP in md and T.QP1118_V2_ENGINE_WARMUP == ENGINE_WARMUP_TEXT
    assert T.U2_SEC4.replace("<n_books_watched>", "66") in md and T.BOUNDED in md and "Random-pick" in md
    assert "The 9 rows of this book" in md
    ids = [d["id"] for d in o["disclosures"]]
    assert ids.count("osb-v2-u2 (QP-RULING-1118-u2-v2 section 4)") == 1
    # same board as osb-u2 apart from the id and the disclosures
    fu = Fx(tmp_path / "v1", "osb-u2")
    good_book(fu)
    fu.refuse_row(fu.bot, S + 3 * H)
    ou = fu.build(L1, n_watched=66)
    for k in ("cells", "random_pick", "entries", "random_pick_entries", "bar", "forward_complete_days"):
        assert o[k] == ou[k]


def test_v2_u2_n_watched_floor_and_twins_refused(tmp_path):
    fx = Fx(tmp_path, "osb-v2-u2")
    with pytest.raises(B.Refused):
        fx.build(L1, n_watched=29)
    with pytest.raises(B.Refused):
        fx.build(L1, n_watched=38)
    fx.build(L1, n_watched=39)
    for sfx in ("-lat3010", "-lat5590", "-send160k"):
        t = Fx(tmp_path / ("t" + sfx), "osb-v2-u2" + sfx)
        with pytest.raises(B.Refused):
            t.build(L1)


def test_u2_v1_defect_disclosure_filled_unrounded(tmp_path):
    fx = Fx(tmp_path, "osb-u2")
    good_book(fx, n=4)
    fx.refuse_row(fx.bot, S + 2 * H, "no_prints: no prints seen for pool")
    fx.refuse_row(fx.bot, S + 3 * H, "no_prints: no prints seen for pool")
    fx.refuse_row(fx.bot, S + 4 * H, "clock_not_ready")
    fx.refuse_row(fx.bot + "-plc", S + 2 * H, "no_prints: no prints seen for pool")
    now = S + 2 * D
    o = fx.build(now)
    want = (V1_DEFECT_TEXT.replace("<instant>", o["generated_utc"]).replace("<r>", "3").replace("<N>", "7").replace("<a>", "2").replace("<b>", "1")
            .replace("<r_p>", "1").replace("<N_p>", "5"))
    assert o["entries"]["forward_entries"] == 7 and o["random_pick_entries"]["forward_entries"] == 5
    assert want in B.render(o) and "<r>" not in want
    # the other boards do not carry it
    for bid in ("osb-u4", "c1nf-fwd-v1"):
        assert "simulator defect found from code" not in B.render(Fx(tmp_path / bid, bid).build(now))


def test_v2_u2_warmup_window_is_w2_from_its_own_start_hour(tmp_path):
    fx = Fx(tmp_path / "a", "osb-v2-u2", start_hour="2026-11-18T12")
    md = B.render(fx.build(B.hour_ms("2026-11-18T12") + 2 * D))
    part = md.split("Disclosures (verbatim)")[1]
    assert "W = [2026-11-04T12:00Z, S)" in part and "starts at 2026-11-04T12:00Z" in part and "before 2026-11-04T12:00Z" in part and "2026-11-03T12:00Z" not in part
    # W2 never starts before 2026-11-03T03:00Z
    fx = Fx(tmp_path / "b", "osb-v2-u2", start_hour="2026-11-10T00")
    part = B.render(fx.build(B.hour_ms("2026-11-10T00") + 2 * D)).split("Disclosures (verbatim)")[1]
    assert "W = [2026-11-03T03:00Z, S)" in part
    # osb-u2 keeps its own window
    part = B.render(Fx(tmp_path / "c", "osb-u2", start_hour="2026-11-18T12").build(B.hour_ms("2026-11-18T12") + 2 * D)).split("Disclosures (verbatim)")[1]
    assert "W = [2026-11-03T12:00Z, S)" in part


def test_v2_code_sha_note_is_book_specific(tmp_path):
    v2 = B.render(Fx(tmp_path / "v2", "osb-v2-u2").build(L1))
    v1 = B.render(Fx(tmp_path / "v1", "osb-u2").build(L1))
    assert T.E1_CODE_SHA_V2 in v2 and "equals the C1-NF book's at 6faf1d4b" not in v2 and T.E1_CODE_SHA in v1 and "d15ff87d" in v2


def test_v2_companion_board_fills_the_six_numbers(tmp_path):
    fu = Fx(tmp_path / "v1", "osb-u2")
    good_book(fu, n=4)
    fu.refuse_row(fu.bot, S + 2 * H, "no_prints: no prints seen for pool")
    fu.refuse_row(fu.bot, S + 4 * H, "clock_not_ready")
    fu.refuse_row(fu.bot + "-plc", S + 2 * H, "no_prints: no prints seen for pool")
    ou = fu.build(S + 2 * D)
    p = tmp_path / "osb-u2.json"
    p.write_text(json.dumps(ou))
    comp = B.load_companion(str(p))
    fx = Fx(tmp_path / "v2", "osb-v2-u2")
    md = B.render(B.build(str(fx.root), S + 2 * D, 66, roots=(str(fx.sim),), companion=comp))
    want = (V1_DEFECT_TEXT.replace("<instant>", ou["generated_utc"]).replace("<r>", "2").replace("<N>", "6").replace("<a>", "1").replace("<b>", "1").replace("<r_p>", "1").replace("<N_p>", "5"))
    assert want in md and "[see osb-u2" not in md
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"schema": B.SCHEMA, "book_id": "osb-u4", "entries": {}, "random_pick_entries": {}}))
    with pytest.raises(B.Refused):
        B.load_companion(str(bad))
    with pytest.raises(B.Refused):
        B.build(str(fu.root), S + 2 * D, 66, roots=(str(fu.sim),), companion=comp)
