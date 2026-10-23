"""DEC-031 Amendment 8 item 7: the dust-rule flip read (tools/arena_flip_read.py). Synthetic ledgers only: no real ledger, decision log or tape is opened."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools import arena_flip_read as FR
from tools import arena_report_legs as RL
from tools import arena_stack_v2 as V2
from tools.arena_paper import book_fail_legs
from tools.arena_stack import fail_legs
from tools.test_arena_report_legs import (DAY, DAY0, NOW, RENT, STACK0, _plenty_of_disk, open_row, start_row, synth_row,  # noqa: F401  (autouse fixture)
                                          write_world)

SEND = 160_000
STACK160 = {**STACK0, "send": SEND}
STAKE = 250_000_000
STRAND = -STAKE - SEND - RENT  # a strand at the book's own send
STRAND505 = -STAKE - 505_000 - RENT


def tiny_fill_pnl(sv):
    """pnl_nofail of a filled leg whose sell is worth `sv` lamports, at send 160,000 and rent 0 (arena_stack.round_trip: sv - stake - 2 send - rent)."""
    return float(sv - STAKE - 2 * SEND)


def row(pid, i, day, pnl, state="filled", filled=True):
    """synth_row, with the stored fail legs re-derived at the book's own send (synth_row prices them at 505,000; the scorer recomputes and compares them)."""
    r = synth_row(pid, i, day, pnl, state, filled, stack=STACK160)
    for lat in ("1p3", "1p9"):
        for rule in ("total", "sc"):
            br = r["calc"][lat]["by_rule"][rule]
            a = (np.array([br["pnl_nofail"]]), np.array([br["filled"]]))
            if rule == "total":
                flat, press, _, _ = book_fail_legs(*a, np.array([br["strand"]]), np.array([0.0]), np.array([0.0]), SEND)
            else:
                flat, press, _, _ = fail_legs(*a, np.array([0.0]), np.array([0.0]), SEND)
            r["legs"][rule][f"flat_{lat}"]["net_lamports"] = float(flat[0])
            r["legs"][rule][f"press_{lat}"]["net_lamports"] = float(press[0])
    return r


def book(pnls, kinds=None):
    rows = [{**start_row(), "stack": dict(STACK160)}]
    for i, p in enumerate(pnls):
        pid, day = f"p{i:03d}", i // (len(pnls) // 5)
        rows += [{**open_row(pid, i, day), "stack": dict(STACK160)}, row(pid, i, day, float(p), *(kinds[i] if kinds else ("filled", True)))]
    return rows


def test_proceeds_are_recovered_from_the_row_and_only_the_dust_band_flips():
    rows = [row("a", 0, 0, tiny_fill_pnl(160_000)), row("b", 1, 0, tiny_fill_pnl(504_999)), row("c", 2, 0, tiny_fill_pnl(505_000)), row("d", 3, 0, tiny_fill_pnl(2_000_000)),
            row("e", 4, 0, 5_000_000), row("g", 5, 0, -SEND, "guarded", False)]
    assert FR.proceeds_of(rows[0], rows[0]["calc"]["1p9"]["by_rule"]["total"]) == 160_000
    out, flips = FR.reclassify_dust_flips(rows)
    assert sorted({f["position_id"] for f in flips}) == ["a", "b"]  # 505,000 itself is not below the old line; guarded never flips
    assert len(flips) == 4  # two trips x two latencies, rule total only
    assert all(f["lat"] in ("1p3", "1p9") for f in flips)
    a = out[0]["calc"]["1p9"]["by_rule"]["total"]
    assert (a["state"], a["strand"], a["filled"], a["e1_extra"]) == ("stranded", True, True, 0.0) and a["pnl_nofail"] == STRAND
    assert out[0]["calc"]["1p9"]["by_rule"]["sc"] == rows[0]["calc"]["1p9"]["by_rule"]["sc"]  # rule sc has no dust rule: untouched
    assert out[2]["calc"]["1p9"]["by_rule"]["total"] == rows[2]["calc"]["1p9"]["by_rule"]["total"]
    assert flips[0]["move_lamports"] == STRAND - tiny_fill_pnl(160_000)
    _o, hard = FR.reclassify_dust_flips(rows, strand_send=505_000)
    assert hard[0]["pnl_after"] == STRAND505


def test_move_matches_the_amendment_figure_when_priced_at_the_505k_send():
    # Am.8 item 7: "each flip moves its leg by 2,384,280 to 2,729,280 lamports" (a 160,000 fill against the strand at the 505,000 stack)
    lo, hi = tiny_fill_pnl(160_000), tiny_fill_pnl(504_999)
    for r, expect in ((row("a", 0, 0, lo), 2_384_280), (row("b", 1, 0, hi), 2_729_279)):
        _o, fl = FR.reclassify_dust_flips([r], strand_send=505_000)
        assert -fl[0]["move_lamports"] == pytest.approx(expect, abs=1)


def test_a_505k_book_has_no_flips_and_says_so():
    r = synth_row("a", 0, 0, 5_000_000)
    out, flips = FR.reclassify_dust_flips([r])
    assert flips == [] and out[0] == r
    assert FR.flip_book([r])["covered"] is False


def test_close_blocked_trips_are_reclassified_first_and_cannot_flip():
    rows = [row("a", 0, 0, tiny_fill_pnl(200_000)), row("b", 1, 0, tiny_fill_pnl(300_000))]
    b = FR.flip_book(rows, ["a"])
    assert b["flips"]["n_positions"] == 1 and {f["position_id"] for f in b["flip_rows"]} == {"b"}


def test_count_sol_and_cell_effect_by_hand(tmp_path):
    rng = np.random.default_rng(3)
    pnls = list(rng.normal(10e6, 1e6, 100))
    for i in (7, 8, 9):  # three tiny fills, sold for 200k, 300k, 400k
        pnls[i] = tiny_fill_pnl(200_000 + 100_000 * (i - 7))
    root, reg = write_world(tmp_path, book(pnls))
    rep = FR.build_flip_report("botA", root, reg, now_ms=NOW)
    b = rep["books"]["0.25"]
    assert b["covered"] and b["sends"] == [160_000] and b["flips"]["n"] == 6 and b["flips"]["n_positions"] == 3
    exp_move = sum(STRAND - tiny_fill_pnl(s) for s in (200_000, 300_000, 400_000)) / 1e9
    assert b["flips"]["by_lat"]["1p9"]["n"] == 3 and b["flips"]["by_lat"]["1p9"]["move_sol"] == pytest.approx(exp_move)
    # rule total, strand bypasses the fail blend: the flat leg of a tiny fill is 0.85 x pnl - 0.15 x send, of a strand the pnl itself
    flat_fill = lambda p: 0.85 * p - 0.15 * SEND  # noqa: E731
    exp_total = sum(STRAND - flat_fill(tiny_fill_pnl(s)) for s in (200_000, 300_000, 400_000)) / 1e9
    assert b["cell_effect"]["flat_1p9_total"] == pytest.approx(exp_total)
    assert b["cell_effect"]["flat_1p9_sc"] == 0.0 and b["cell_effect"]["press_1p9_sc"] == 0.0  # rule sc has no dust rule
    assert b["cell_effect"]["press_1p9_total"] < 0 and b["cell_effect_at_505k_send"]["flat_1p9_total"] < b["cell_effect"]["flat_1p9_total"]
    text = FR.render(rep)
    assert "FLIPPED LEGS" in text and "6 legs in 3 trips" in text and "GATE WITH FLIPPED LEGS PRICED AS STRANDS" in text and "Nothing here is a profit claim" in text


def test_a_book_that_passes_only_with_the_flips_priced_as_fills_does_not_pass(tmp_path):
    # tuned so the as-fills 1.9 s sub-cells meet every criterion and the as-strands ones do not
    root, reg = write_world(tmp_path, book(MARGINAL))
    rep = FR.build_flip_report("botA", root, reg, now_ms=NOW)
    g = rep["gate_with_flips_as_strands"]
    assert rep["scorer_status"] == "PASS" and g["passes_as_fills"] is True and g["passes_as_strands"] is False and g["verdict"] == "FAIL"
    assert "FAIL ***" in FR.render(rep)


def test_a_clean_book_with_no_flips_keeps_its_verdict(tmp_path):
    rng = np.random.default_rng(11)
    root, reg = write_world(tmp_path, book(list(rng.normal(10e6, 1e6, 100))))
    g = FR.build_flip_report("botA", root, reg, now_ms=NOW)["gate_with_flips_as_strands"]
    assert g["flips_deciding_1p9"] == 0 and g["passes_as_fills"] and g["passes_as_strands"] and g["verdict"] == "PASS"


def test_expect_send_refuses_another_send(tmp_path):
    root, reg = write_world(tmp_path, book([5e6] * 100))
    with pytest.raises(FR.Refused, match="pins 505000"):
        FR.build_flip_report("botA", root, reg, now_ms=NOW, expect_send=505_000)


def test_read_only_and_wrapper_prints_the_flip_read_after_the_legs(tmp_path, capsys):
    import hashlib
    rng = np.random.default_rng(3)
    pnls = list(rng.normal(10e6, 1e6, 100))
    pnls[5] = tiny_fill_pnl(250_000)
    root, reg = write_world(tmp_path, book(pnls))
    dig = lambda: hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file())).hexdigest()  # noqa: E731
    before = dig()
    out = tmp_path / "legs.json"
    rc = V2.report_main(["--expect-send", "160000", "--bot", "botA", "--ledger-root", root, "--registry", reg, "--now-ms", str(NOW), "--json", str(out)])
    txt = capsys.readouterr().out
    assert rc == 0 and dig() == before
    assert txt.index("ARENA REPORT LEGS") < txt.index("ARENA FLIP READ") and "FLIPPED LEGS" in txt
    assert "books" in json.loads(out.read_text()) and "gate_with_flips_as_strands" not in json.loads(out.read_text())  # the legs report owns --json
    assert FR.main(["--bot", "botA", "--ledger-root", root, "--registry", reg, "--now-ms", str(NOW), "--tape-dir", "ignored"]) == 0


@pytest.mark.parametrize("bad", ["sealed-x", "shadow-y"])
def test_closed_words_are_refused_before_any_filesystem_access(bad):
    with pytest.raises(FR.Refused):
        FR.build_flip_report("botA", "/x/" + bad, "/y/bots.json")


def _marginal():
    """99 clean trips about +10M lamports and one tiny fill (sold for 300,000): found by a scan of the mean (9M to 11M: PASS as fills, FAIL as strands; 12M and up: PASS both)."""
    p = list(np.random.default_rng(5).normal(10e6, 1e6, 100))
    p[7] = tiny_fill_pnl(300_000)
    return p


MARGINAL = _marginal()
