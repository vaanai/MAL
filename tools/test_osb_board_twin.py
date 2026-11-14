"""osb_board twin pair view (QP ruling 2026-11-14 Q6). Synthetic ledgers only; no tape, no real book, no host path. Nothing here says any book makes money."""
import json

import pytest

from tools.test_osb_board import (H, VARIANT_ROWS, row, CONTROL_ROWS, START, books_file, load, make_book, ms, op, run, snap_root, tp, tr, tstop)  # noqa: F401
import osb_board as B  # noqa: E402  (tools.test_osb_board puts scripts/research on sys.path)

def set_dmd5(out, value, hour=None):
    """rewrite the decision_md5 of the stored hours of a fixture book (the host decision digest the board compares); hour=None: every line"""
    p = out / "md5-by-hour.jsonl"
    lines = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    for r in lines:
        if hour is None or r["hour"] == hour:
            r["decision_md5"] = value
    p.write_text("\n".join(json.dumps(r) for r in lines) + "\n")


STRICT_ROWS = tp("a1", ms(14, 13), 4_000_000) + tstop("a3", ms(14, 16), -3_000_000) + [
    op("a7", ms(14, 14))] + [{"kind": "excluded", "position_id": "a7", "stake_sol": s, "exclusion_reason": "feed_gap"} for s in (0.10, 0.25)]


def flagged(pid, t0, net, blind, state="filled", **over):
    rows = [op(pid, t0)] + [tr(pid, s, net, t0, t0 + 2 * H, "tp_1.5x", state) for s in (0.10, 0.25)]
    for r in rows:
        if r["kind"] == "trade":
            r.update(dict(gap_in_hold=True, blind_s=blind, gap_count=2, gap_holes=[[t0 + 1, t0 + 2]]), **over)
    return rows


def excl(pid, t0, why):
    return [op(pid, t0)] + [{"kind": "excluded", "position_id": pid, "stake_sol": s, "exclusion_reason": why} for s in (0.10, 0.25)]


TWIN_ROWS = (tp("a1", ms(14, 13), 4_000_000) + tstop("a3", ms(14, 16), -3_000_000) + flagged("a7", ms(14, 14), -2_000_000, 12.5) + excl("a9", ms(15, 1), "feed_gap_entry")
             + excl("a10", ms(15, 2), "feed_gap_exit") + excl("a11", ms(15, 3), "feed_gap_exit") + excl("a12", ms(15, 4), "missing_v") + excl("a13", ms(15, 5), "guard_ref_missing"))


def pair_world(tmp, same=True, write_md5=True):
    sm, so = make_book(tmp, "S", STRICT_ROWS)
    tm, to = make_book(tmp, "T", TWIN_ROWS, gap_exclusion="hold", twin_of="S")
    if not same:
        set_dmd5(to, "b" * 32)
    return sm, tm


def bf(tmp, sm, tm):
    p = tmp / "books.json"
    p.write_text(json.dumps({"books": [{"manifest": str(sm), "role": "variant", "start_instant_utc": START},
                                       {"manifest": str(tm), "role": "variant", "start_instant_utc": START, "twin_of": "S"}], "live": None}))
    return p


def test_pair_is_shown_side_by_side_with_the_ruling_labels_and_no_sum(tmp_path):
    sm, tm = pair_world(tmp_path)
    rc, out = run(tmp_path, bf(tmp_path, sm, tm))
    assert rc == 0
    res, md = load(out), (out / "board.md").read_text()
    assert res["pairs"] == [{"twin": "T", "strict": "S", "status": "pair", "line": None}]
    assert "## Pair, stake 0.25: T (incl. gap-in-hold) beside S (strict)" in md
    assert md.index("incl. gap-in-hold: priced through feed gaps inside the hold; take-profit blind during a gap, as live is when its feed has the same gap") < md.index("strict: trades with any feed gap")
    assert "strict: trades with any feed gap in the 24 h hold left out (1 left out, all days); in Nov 2-12 the left-out trades were net negative (DEC-035 Am.6 Erratum 3), so strict can overstate" in md
    assert "still excluded (T): gap at entry 1, gap at exit 2, missing V 1, other 1." in md
    assert "gap-in-hold, blind 12.5 s" in md
    assert "neither set is decision-grade (recon NOT COMPARABLE); no stake step rests on either (Am.6)" in md
    assert "| window (flat / press (n closed)) | incl. gap-in-hold | strict |" in md
    twin_row = next(r for b in res["books"] if b["id"] == "T" for r in b["rows"] if r["stake_sol"] == 0.25)
    assert [t["gap_in_hold"] for t in twin_row["trades"] if "gap_in_hold" in t] == [True] and twin_row["excluded_counts"]["gap_at_exit"] == 2
    assert twin_row["windows"]["incl_backfill"]["n_closed"] == 3  # a1, a3 and the flagged a7: the twin's own figure, never added to the strict one
    strict_row = next(r for b in res["books"] if b["id"] == "S" for r in b["rows"] if r["stake_sol"] == 0.25)
    assert strict_row["windows"]["incl_backfill"]["n_closed"] == 2 and strict_row["excluded_counts"]["feed_gap"] == 1


def test_differing_decisions_show_the_twin_as_a_separate_book(tmp_path):
    sm, tm = pair_world(tmp_path, same=False)
    res, md = load(run(tmp_path, bf(tmp_path, sm, tm))[1]), None
    md = (tmp_path / "board" / "board.md").read_text()
    assert res["pairs"][0]["status"] == "differ" and "twin decisions differ: not a pricing-only twin" in md
    assert "## Pair" not in md and "incl. gap-in-hold: priced" not in md
    assert all("excluded_counts" not in r for b in res["books"] for r in b["rows"])


def excl_real(pid, t0):
    """a strict excluded row as the engine writes it: with the decision instants"""
    return [op(pid, t0)] + [{"kind": "excluded", "position_id": pid, "stake_sol": s, "exclusion_reason": "feed_gap", "decision_ms": {"entry": t0, "exit": t0 + 2 * H}} for s in (0.10, 0.25)]


STRICT2 = tp("a1", ms(14, 13), 4_000_000) + excl_real("a7", ms(14, 14))
TWIN2 = tp("a1", ms(14, 13), 4_000_000) + flagged("a7", ms(14, 14), -2_000_000, 3.0)


def two_books(tmp, twin_rows=TWIN2, strict_rows=STRICT2):
    sm, so = make_book(tmp, "S", strict_rows)
    tm, to = make_book(tmp, "T", twin_rows, gap_exclusion="hold", twin_of="S")
    return sm, tm, so, to


def test_no_md5_files_pairs_from_the_rows_and_never_writes(tmp_path):
    sm, tm, so, to = two_books(tmp_path)
    res = load(run(tmp_path, bf(tmp_path, sm, tm))[1])
    assert res["pairs"][0]["status"] == "pair" and not (so / "decisions.md5").exists() and not (to / "decisions.md5").exists()  # a priced twin trade and an excluded strict row give equal digests


def test_stale_decisions_file_is_ignored_and_digest_decides(tmp_path):
    sm, tm, so, to = two_books(tmp_path)
    (to / "decisions.md5").write_text("c" * 32 + "\n")  # a stale disagreeing file is ignored: the host digests are equal
    assert load(run(tmp_path, bf(tmp_path, sm, tm))[1])["pairs"][0]["status"] == "pair"
    set_dmd5(to, "c" * 32)
    assert load(run(tmp_path, bf(tmp_path, sm, tm))[1])["pairs"][0]["status"] == "differ"


def test_unreadable_side_is_pending(tmp_path):
    sm, tm, so, to = two_books(tmp_path)
    (to / "book").unlink()
    res = load(run(tmp_path, bf(tmp_path, sm, tm))[1])
    assert res["pairs"][0]["status"] == "pending" and "## Pair" not in (tmp_path / "board" / "board.md").read_text()


def test_decisions_digest_is_equal_for_priced_and_excluded_positions():
    import osb_follow as OF
    assert OF.decisions_md5_from_rows(STRICT2) == OF.decisions_md5_from_rows(TWIN2)
    assert OF.decisions_md5_from_rows(STRICT2) != OF.decisions_md5_from_rows(STRICT2[:-3])


def test_exit_invalid_across_a_gap_is_counted_from_the_rows(tmp_path):
    rows = tp("a1", ms(14, 13), 4_000_000) + flagged("a7", ms(14, 14), -2_000_000, 3.0, state="exit_invalid")
    sm, tm, so, to = two_books(tmp_path, twin_rows=rows)
    res, md = load(run(tmp_path, bf(tmp_path, sm, tm))[1]), (tmp_path / "board" / "board.md").read_text()
    assert "- exit_invalid across a gap (T): 1 (a strand, not dropped)." in md
    assert row(res, "T", 0.25)["excluded_counts"]["exit_invalid_across_gap"] == 1 and row(res, "S", 0.25)["excluded_counts"]["exit_invalid_across_gap"] == 0


@pytest.mark.parametrize("over", [{"blind_s": "x"}, {"blind_s": None}, {"blind_s": -1}, {"gap_count": "2"}, {"gap_holes": "no"}, {"gap_holes": [[1]]}])
def test_malformed_twin_rows_do_not_take_the_board_down(tmp_path, over):
    bad = flagged("a7", ms(14, 14), -2_000_000, 3.0, **over)
    for r in bad:
        if r.get("blind_s", 1) is None:
            r.pop("blind_s")
    sm, tm, so, to = two_books(tmp_path, twin_rows=tp("a1", ms(14, 13), 4_000_000) + bad)
    rc, out = run(tmp_path, bf(tmp_path, sm, tm))
    assert rc == 0
    res, md = load(out), (out / "board.md").read_text()
    assert {b["id"] for b in res["books"]} == {"S", "T"} and all(b["state"] == "running" for b in res["books"])
    assert "malformed twin rows (T): 1." in md and "gap-in-hold, blind" not in md and row(res, "T", 0.25)["windows"]["incl_backfill"]["n_closed"] == 2  # the trade stays, the flag is dropped


def test_unparseable_flag_line_is_skipped(tmp_path):
    sm, tm, so, to = two_books(tmp_path)
    f = next((to / "runs" / "0001" / "ledger").glob("*/trades-x.jsonl"))
    f.write_text(f.read_text() + '{"kind": "trade", "gap_in_hold": tru\n')
    assert run(tmp_path, bf(tmp_path, sm, tm))[0] == 0


def pbooks(tmp, specs, live=None):
    p = tmp / "books.json"
    p.write_text(json.dumps({"books": [dict(manifest=str(mp), role=role, start_instant_utc=START, **({"twin_of": tw} if tw else {})) for mp, role, tw in specs], "live": live}))
    return p


def cmp_world(tmp, with_control_twin=True):
    a, ao = make_book(tmp, "A", CONTROL_ROWS)
    v, vo = make_book(tmp, "V", VARIANT_ROWS, ov={"Q_STAR": 1.0e9})
    vt, vto = make_book(tmp, "VT", VARIANT_ROWS + flagged("v9", ms(15, 12), 1_000_000, 2.0), ov={"Q_STAR": 1.0e9}, gap_exclusion="hold", twin_of="V")
    specs = [(a, "control", None), (v, "variant", None), (vt, "variant", "V")]
    if with_control_twin:
        at, ato = make_book(tmp, "AT", CONTROL_ROWS + flagged("a9", ms(15, 12), 1_000_000, 2.0), gap_exclusion="hold", twin_of="A")
        specs.append((at, "variant", "A"))
    return pbooks(tmp, specs)


def test_twin_is_compared_only_with_the_control_twin(tmp_path):
    res = load(run(tmp_path, cmp_world(tmp_path))[1])
    assert row(res, "V", 0.25)["vs_control"]["control_book"] == "A"  # strict with strict
    assert row(res, "VT", 0.25)["vs_control"]["control_book"] == "AT"  # twin with the control's twin, never with A
    at = row(res, "AT", 0.25)
    assert at["vs_control"] is None and at["disclosure"]["floor_holds"] is None  # the control's own twin has no comparison
    assert next(b for b in res["books"] if b["id"] == "AT")["role"] == "control (incl. gap-in-hold twin)"


def test_twin_without_a_control_twin_shows_na(tmp_path):
    res = load(run(tmp_path, cmp_world(tmp_path, with_control_twin=False))[1])
    vt = row(res, "VT", 0.25)
    assert vt["vs_control"] is None and vt["disclosure"]["floor_holds"] is None
    assert row(res, "V", 0.25)["vs_control"]["control_book"] == "A"


def test_no_twin_means_no_pairs_key_and_no_new_row_keys(tmp_path):
    ca, _ = make_book(tmp_path, "A", CONTROL_ROWS)
    res = load(run(tmp_path, books_file(tmp_path, [(ca, "control")]))[1])
    assert "pairs" not in res
    assert all("pair" not in b and "excluded_counts" not in r for b in res["books"] for r in b["rows"])
    assert all("gap_in_hold" not in t for b in res["books"] for r in b["rows"] for t in r["trades"])


@pytest.mark.parametrize("bad", [{"twin_of": "nope"}, {"twin_of": "T"}])
def test_bad_twin_of_is_refused(tmp_path, bad):
    sm, tm = pair_world(tmp_path)
    p = tmp_path / "books.json"
    p.write_text(json.dumps({"books": [{"manifest": str(sm), "role": "variant", "start_instant_utc": START},
                                       {"manifest": str(tm), "role": "variant", "start_instant_utc": START, **bad}], "live": None}))
    with pytest.raises(B.Refusal):
        B.load_books_file(p, roots=(str(tmp_path),))


def test_twin_of_on_a_strict_manifest_is_refused(tmp_path):
    sm, tm = pair_world(tmp_path)
    p = tmp_path / "books.json"
    p.write_text(json.dumps({"books": [{"manifest": str(sm), "role": "variant", "start_instant_utc": START, "twin_of": "T"},
                                       {"manifest": str(tm), "role": "variant", "start_instant_utc": START}], "live": None}))
    with pytest.raises(B.Refusal):
        B.load_books_file(p, roots=(str(tmp_path),))




# ---- QP ruling 2026-11-14 board gap-in-hold ADD2: identity on the host decision_md5, pending / escalation, header
from tools.test_osb_board import END, set_status  # noqa: E402

PENDING_PLAIN = "twin identity pending: the twin is at %s, its strict book at %s; compared when both are at the same hour"


def HK(end_ms):
    """the hour key of the hour ending at end_ms"""
    return B.utc(end_ms - H)[:13]


def write_hours(out, end_ms, count=3, md5="d"):
    """md5-by-hour.jsonl as the driver writes it: `count` verified hours ending at end_ms, each with the given decision_md5 (cumulative digest: here the same value per hour)"""
    set_status(out, end_ms)
    if end_ms is None:
        return
    out.joinpath("md5-by-hour.jsonl").write_text("".join(json.dumps({"hour": HK(e), "end_ms": e, "decision_md5": md5, "ledger_md5": "l"}) + "\n" for e in range(end_ms - (count - 1) * H, end_ms + 1, H)))


def world(tmp, twin_end=END, strict_end=END, twin_rows=TWIN2, strict_rows=STRICT2, twin_md5="d", strict_md5="d", **tw):
    sm, so = make_book(tmp, "S", strict_rows)
    tm, to = make_book(tmp, "T", twin_rows, twin_of="S", **({"gap_exclusion": "hold"} if "pricing_lat_entry_s" not in tw else {}), **tw)
    write_hours(to, twin_end, count=10, md5=twin_md5)
    write_hours(so, strict_end, count=10, md5=strict_md5)
    return bf(tmp, sm, tm), to, so


def go(tmp, p, now=END + 60_000):
    rc, out = run(tmp, p, now=now)
    assert rc == 0
    return load(out), (out / "board.md").read_text()


def test_latency_twin_with_its_exit_row_written_later_is_a_pair(tmp_path):
    """(a) strict has open + trade, the latency twin only the open row (its trade row trails): same H, equal decision_md5 -> pair; no ledger row decides"""
    strict = [op("p", ms(15, 3))] + [tr("p", s, 1_000_000, ms(15, 3), ms(15, 3) + 20_000, "tp_1.5x", "filled") for s in (0.10, 0.25)]
    p, to, so = world(tmp_path, twin_rows=[op("p", ms(15, 3))], strict_rows=strict, pricing_lat_entry_s=4.085)
    res, md = go(tmp_path, p)
    assert res["pairs"][0]["status"] == "pair" and res["pairs"][0]["kind"] == "latency" and "## Latency pair" in md


def test_different_decision_md5_at_the_common_hour_is_differ(tmp_path):
    """(b)"""
    p, to, so = world(tmp_path, twin_md5="x")
    res, md = go(tmp_path, p)
    assert res["pairs"][0]["status"] == "differ" and "twin decisions differ: not a pricing-only twin" in md and "## Pair" not in md
    assert next(b for b in res["books"] if b["id"] == "T")["state"] != "pending"  # shown as a separate book


def test_differ_at_the_common_hour_even_when_the_twin_is_behind(tmp_path):
    p, to, so = world(tmp_path, twin_end=END - 5 * H, twin_md5="x")
    assert go(tmp_path, p)[0]["pairs"][0]["status"] == "differ"


def test_twin_behind_confirmed_through_h_rows_withheld_no_number(tmp_path):
    """(c)"""
    p, to, so = world(tmp_path, twin_end=END - 5 * H)
    res, md = go(tmp_path, p)
    pr = res["pairs"][0]
    assert pr["status"] == "pending" and pr["line"] == "twin identity confirmed through %s; pair view when both are at the same hour" % HK(END - 5 * H)
    assert pr["line"] in md and "## Pair" not in md and "twin decisions differ" not in md
    twin = next(b for b in res["books"] if b["id"] == "T")
    assert twin["state"] == "pending" and twin["rows"] == []
    assert "| T | variant | 0.25" not in md and "| T | variant | 0.10" not in md
    assert res["tape_through_utc"] == B.utc(END) and res["catching_up"] == ["T"]
    assert "- catching up (pending twins only; a STALE book is shown by its own state): T." in md


def test_twin_ahead_no_hour_and_no_common_hour_are_pending(tmp_path):
    p, to, so = world(tmp_path, twin_end=END, strict_end=END - 2 * H)
    assert go(tmp_path, p)[0]["pairs"][0]["line"] == "twin identity confirmed through %s; pair view when both are at the same hour" % HK(END - 2 * H)
    (tmp_path / "a").mkdir()
    p, to, so = world(tmp_path / "a", twin_end=None)
    res, md = go(tmp_path / "a", p)
    assert res["pairs"][0]["status"] == "pending" and res["pairs"][0]["line"] == PENDING_PLAIN % ("no hour yet", HK(END)) and PENDING_PLAIN % ("no hour yet", HK(END)) in md
    (tmp_path / "b").mkdir()
    p, to, so = world(tmp_path / "b", twin_end=END - 10 * H)
    write_hours(to, END - 10 * H, count=1)
    write_hours(so, END, count=2)
    res, md = go(tmp_path / "b", p)
    assert res["pairs"][0]["status"] == "pending" and res["pairs"][0]["line"] == PENDING_PLAIN % (HK(END - 10 * H), HK(END))


def test_torn_or_digestless_file_is_pending_never_differ(tmp_path):
    p, to, so = world(tmp_path)
    f = to / "md5-by-hour.jsonl"
    f.write_text(f.read_text() + '{"hour": "2026-11-16T06", "end_ms": 1, "decis')  # torn last line is skipped; the complete hours agree
    assert go(tmp_path, p)[0]["pairs"][0]["status"] == "pair"
    (tmp_path / "x").mkdir()
    p, to, so = world(tmp_path / "x")
    f = to / "md5-by-hour.jsonl"
    f.write_text("\n".join(json.dumps({"hour": json.loads(ln)["hour"], "end_ms": json.loads(ln)["end_ms"]}) for ln in f.read_text().splitlines()) + "\n")  # no decision_md5 field
    res, md = go(tmp_path / "x", p)
    assert res["pairs"][0]["status"] == "pending" and "twin decisions differ" not in md


def test_status_change_between_reads_is_pending(tmp_path, monkeypatch):
    real = B.hour_now
    monkeypatch.setattr(B, "hour_now", lambda b: "2099-01-01T00" if b["m"]["book_id"] == "T" else real(b))
    p, to, so = world(tmp_path)
    res, md = go(tmp_path, p)
    assert res["pairs"][0]["status"] == "pending" and "twin decisions differ" not in md


def test_escalation_6h_24h_and_cleared_when_the_hours_meet(tmp_path):
    """(d) wall clock injected through --now-ms; pending_since_utc is carried through the board.json of the out dir"""
    p, to, so = world(tmp_path, twin_end=END - 5 * H)
    t0 = END + 60_000
    res, md = go(tmp_path, p, now=t0)
    pr = res["pairs"][0]
    assert pr["pending_since_utc"] == B.utc(t0) and "pending for" not in pr["line"]
    res, md = go(tmp_path, p, now=t0 + 6 * H)  # exactly 6 h: not more than 6 h
    assert "pending for" not in res["pairs"][0]["line"]
    res, md = go(tmp_path, p, now=t0 + 7 * H + 1000)
    pr = res["pairs"][0]
    assert pr["pending_since_utc"] == B.utc(t0) and pr["line"] == "twin identity pending for 7 h: the twin is at %s, its strict book at %s; check the twin's job" % (HK(END - 5 * H), HK(END)) and pr["line"] in md
    res, md = go(tmp_path, p, now=t0 + 25 * H)
    assert res["pairs"][0]["line"].endswith("check the twin's job; the manager stops the twin or records why") and "pending for 25 h" in res["pairs"][0]["line"]
    write_hours(to, END)  # the hours meet: cleared
    res, md = go(tmp_path, p, now=t0 + 26 * H)
    assert res["pairs"][0]["status"] == "pair" and "pending_since_utc" not in res["pairs"][0] and "pending for" not in md
    write_hours(to, END - 5 * H)  # pending again: a new clock starts now
    res, md = go(tmp_path, p, now=t0 + 27 * H)
    assert res["pairs"][0]["pending_since_utc"] == B.utc(t0 + 27 * H)


def test_unreadable_previous_board_json_starts_the_clock_now(tmp_path):
    p, to, so = world(tmp_path, twin_end=END - 5 * H)
    (tmp_path / "board").mkdir()
    (tmp_path / "board" / "board.json").write_text("{not json")
    res, md = go(tmp_path, p)
    assert res["pairs"][0]["pending_since_utc"] == B.utc(END + 60_000)


def test_stale_decisions_md5_file_does_not_enter_the_pairing(tmp_path):
    """(e) no shim: a stale disagreeing decisions.md5 file on each book, equal host digests -> pair; unequal digests with equal files -> differ"""
    p, to, so = world(tmp_path)
    (so / "decisions.md5").write_text("a" * 32 + "\n")
    (to / "decisions.md5").write_text("b" * 32 + "\n")
    assert go(tmp_path, p)[0]["pairs"][0]["status"] == "pair"
    (tmp_path / "x").mkdir()
    p, to, so = world(tmp_path / "x", twin_md5="x")
    (so / "decisions.md5").write_text("a" * 32 + "\n")
    (to / "decisions.md5").write_text("a" * 32 + "\n")
    assert go(tmp_path / "x", p)[0]["pairs"][0]["status"] == "differ"


def test_new_lines_pass_the_forbidden_word_check(tmp_path):
    """(f)"""
    for line in (B.PAIR_HOURS % ("2026-11-13T10", "no hour yet"), B.PAIR_CONFIRMED % "2026-11-13T10", B.PAIR_LONG % (7, "2026-11-13T10", "2026-11-14T15") + B.PAIR_LONG24, B.PAIR_DIFFER,
                 "- catching up (pending twins only; a STALE book is shown by its own state): T."):
        B.check_forbidden({"line": line}, line)
    p, to, so = world(tmp_path, twin_end=END - 5 * H)
    res, md = go(tmp_path, p, now=END + 30 * H)
    B.check_forbidden(res, md)
