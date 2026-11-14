"""osb_board twin pair view (QP ruling 2026-11-14 Q6). Synthetic ledgers only; no tape, no real book, no host path. Nothing here says any book makes money."""
import json

import pytest

from tools.test_osb_board import (CONTROL_ROWS, START, books_file, load, make_book, ms, op, run, snap_root, tp, tr, tstop)  # noqa: F401
import osb_board as B  # noqa: E402  (tools.test_osb_board puts scripts/research on sys.path)

STRICT_ROWS = tp("a1", ms(14, 13), 4_000_000) + tstop("a3", ms(14, 16), -3_000_000) + [
    op("a7", ms(14, 14))] + [{"kind": "excluded", "position_id": "a7", "stake_sol": s, "exclusion_reason": "feed_gap"} for s in (0.10, 0.25)]


def flagged(pid, t0, net, blind):
    rows = tp(pid, t0, net)
    for r in rows:
        if r["kind"] == "trade":
            r.update(gap_in_hold=True, blind_s=blind, gap_count=2, gap_holes=[[t0 + 1, t0 + 2]])
    return rows


def excl(pid, t0, why):
    return [op(pid, t0)] + [{"kind": "excluded", "position_id": pid, "stake_sol": s, "exclusion_reason": why} for s in (0.10, 0.25)]


TWIN_ROWS = (tp("a1", ms(14, 13), 4_000_000) + tstop("a3", ms(14, 16), -3_000_000) + flagged("a7", ms(14, 14), -2_000_000, 12.5) + excl("a9", ms(15, 1), "feed_gap_entry")
             + excl("a10", ms(15, 2), "feed_gap_exit") + excl("a11", ms(15, 3), "feed_gap_exit") + excl("a12", ms(15, 4), "missing_v") + excl("a13", ms(15, 5), "guard_ref_missing"))


def pair_world(tmp, same=True, write_md5=True):
    sm, so = make_book(tmp, "S", STRICT_ROWS)
    tm, to = make_book(tmp, "T", TWIN_ROWS, gap_exclusion="hold", twin_of="S")
    if write_md5:
        (so / "decisions.md5").write_text("a" * 32 + "\n")
        (to / "decisions.md5").write_text(("a" if same else "b") * 32 + "\n")
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
    assert "strict: trades with any feed gap in the 24 h hold left out (1 left out); in Nov 2-12 the left-out trades were net negative (DEC-035 Am.6 Erratum 3), so strict can overstate" in md
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


def test_missing_md5_is_pending_and_not_a_pair(tmp_path):
    sm, tm = pair_world(tmp_path, write_md5=False)
    res = load(run(tmp_path, bf(tmp_path, sm, tm))[1])
    assert res["pairs"][0]["status"] == "pending" and "## Pair" not in (tmp_path / "board" / "board.md").read_text()


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
