"""Tests for tools/arena_gap_reclassify.py: small synthetic tape files only; no real arena data."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena_gap_reclassify as G  # noqa: E402


def trade(prog, slot, t, typ="trade"):
    return {"v": 1, "type": typ, "prog": prog, "slot": slot, "t_recv_ms": t, "signature": f"s{prog}{slot}{t}", "side": "buy", "sol_lamports": 1}


def jump(prog, fs, ts, f_ms, t_ms, dl=True, kind="slot_jump"):
    return {"v": 1, "type": "gap", "prog": prog, "slot": fs, "t_recv_ms": t_ms, "kind": kind, "data_loss": dl, "from_ms": f_ms, "to_ms": t_ms,
            "from_slot": fs, "to_slot": ts, "missed_slots": ts - fs - 1}


def write(tmp, rows, name="tape-2026-10-23T00.jsonl"):
    p = tmp / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(p)


def run(tmp, rows, grace=0):
    sc = G.scan([write(tmp, rows)])
    recs = G.reclassify(sc, grace)
    return {(r.get("prog"), r["type"], r.get("from_slot")): r for r in recs}, recs


T0 = 1_000_000


def test_skew_gap_fully_covered_by_late_rows(tmp_path):
    # jump 100 -> 140 logged at T0; the skipped slots 101..139 arrive 20 s later from the lagging socket
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200)]
    rows += [trade("pumpswap", s, T0 + 20_000) for s in range(101, 140)]
    rows.append(trade("pumpswap", 140, T0 + 21_000))
    by, _ = run(tmp_path, rows)
    g = by[("pumpswap", "gap", 100)]
    assert g["class"] == "covered" and g["uncovered_slots"] == 0 and g["uncovered_ranges"] == []
    p = by[("pumpswap", "program", None)]
    assert p["flagged"] == 1 and p["covered"] == 1 and p["uncovered_ranges_merged"] == [] and p["covered_share"] == 1.0


def test_truly_empty_range(tmp_path):
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200), trade("pumpswap", 99, T0), trade("pumpswap", 140, T0 + 500), trade("pumpswap", 141, T0 + 5000)]
    by, _ = run(tmp_path, rows)
    g = by[("pumpswap", "gap", 100)]
    assert g["class"] == "empty" and g["skipped_slots"] == 39 and g["uncovered_slots"] == 39 and g["uncovered_ranges"] == [[101, 139]]
    assert by[("pumpswap", "program", None)]["uncovered_slots_merged"] == 39


def test_partial_overlap_gives_exact_ranges(tmp_path):
    have = set(range(101, 140)) - {110, 111, 112, 130}
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200)] + [trade("pumpswap", s, T0 + 9000) for s in sorted(have)] + [trade("pumpswap", 141, T0 + 9000)]
    by, _ = run(tmp_path, rows)
    g = by[("pumpswap", "gap", 100)]
    assert g["class"] == "partial" and g["uncovered_slots"] == 4
    assert g["uncovered_ranges"] == [[110, 112], [130, 130]]
    assert by[("pumpswap", "program", None)]["partial"] == 1


def test_pumpfun_is_separate_from_pumpswap(tmp_path):
    # pumpswap has rows in 101..139; pump.fun has none: a pumpswap row must never cover a pump.fun slot
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200), jump("pumpfun", 100, 140, T0, T0 + 200)]
    rows += [trade("pumpswap", s, T0 + 5000) for s in range(101, 140)]
    rows += [trade("pumpfun", 150, T0 + 6000)]
    by, _ = run(tmp_path, rows)
    assert by[("pumpswap", "gap", 100)]["class"] == "covered"
    assert by[("pumpfun", "gap", 100)]["class"] == "empty"
    assert by[("pumpfun", "program", None)]["uncovered_ranges_merged"] == [[101, 139]]
    assert by[("pumpswap", "program", None)]["uncovered_ranges_merged"] == []


def test_non_data_rows_do_not_cover_and_create_does(tmp_path):
    rows = [jump("pumpfun", 100, 135, T0, T0 + 100), {"v": 1, "type": "hb", "prog": "pumpfun", "slot": 110, "t_recv_ms": T0 + 3000},
            trade("pumpfun", 120, T0 + 4000, typ="create"), trade("pumpfun", 200, T0 + 9000)]
    by, _ = run(tmp_path, rows)
    assert by[("pumpfun", "gap", 100)]["uncovered_ranges"] == [[101, 119], [121, 134]]


def test_tape_cut_is_unresolved_and_not_in_merged_ranges(tmp_path):
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200), trade("pumpswap", 140, T0 + 300)]
    by, _ = run(tmp_path, rows, grace=120)
    assert by[("pumpswap", "gap", 100)]["class"] == "unresolved_tape_cut"
    p = by[("pumpswap", "program", None)]
    assert p["unresolved_tape_cut"] == 1 and p["uncovered_ranges_merged"] == []


def test_merged_ranges_union_across_gaps_and_dedupe(tmp_path):
    rows = [jump("pumpswap", 100, 140, T0, T0 + 1), jump("pumpswap", 100, 140, T0, T0 + 1), jump("pumpswap", 120, 160, T0, T0 + 2), trade("pumpswap", 999, T0 + 99)]
    by, _ = run(tmp_path, rows)
    p = by[("pumpswap", "program", None)]
    assert p["flagged"] == 2
    assert p["uncovered_ranges_merged"] == [[101, 159]]
    assert p["uncovered_slots_merged"] == 59


def test_other_data_loss_kinds_pass_through_unreclassified(tmp_path):
    rows = [jump("pumpswap", 0, 0, T0, T0 + 4000, kind="silence"), jump("pumpswap", 0, 0, T0 + 1000, T0 + 5000, kind="silence"),
            jump("all", 0, 0, T0, T0 + 1000, kind="process_restart"), jump("pumpswap", 0, 0, T0, T0, dl=False, kind="socket_reconnect"),
            trade("pumpswap", 5, T0)]
    by, recs = run(tmp_path, rows)
    nr = next(r for r in recs if r["type"] == "not_reclassified")["kinds"]
    assert nr["pumpswap:silence"] == {"rows": 2, "union_s": 5.0}
    assert nr["all:process_restart"]["rows"] == 1 and "pumpswap:socket_reconnect" not in nr
    assert by[("pumpswap", "program", None)]["flagged"] == 0


def test_deterministic_sha_and_order_independent(tmp_path):
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200)] + [trade("pumpswap", s, T0 + 9000) for s in range(101, 120)] + [trade("pumpswap", 150, T0 + 9000)]
    a = write(tmp_path, rows, "tape-2026-10-23T00.jsonl")
    b = write(tmp_path, [trade("pumpswap", s, T0 + 9000) for s in range(120, 140)], "tape-2026-10-23T01.jsonl")
    l1 = G.render(G.reclassify(G.scan([a, b]), 0))
    l2 = G.render(G.reclassify(G.scan([b, a, a]), 0))
    assert l1 == l2
    last = json.loads(l1[-1])
    assert last["type"] == "sha256" and last["lines"] == len(l1) - 1
    assert json.loads(l1[0])["class"] == "covered"  # coverage is read across files


def test_refuses_policy_paths_and_missing_files(tmp_path, capsys):
    d = tmp_path / "ledger"
    d.mkdir()
    p = d / "tape-2026-10-23T00.jsonl"
    p.write_text("")
    assert G.main(["--tape", str(p)]) == 2
    assert G.main(["--tape", str(tmp_path / "nope.jsonl")]) == 2


def test_cli_counts_only_and_zst(tmp_path, capsys):
    import shutil
    import subprocess
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200)] + [trade("pumpswap", s, T0 + 9000) for s in range(101, 140)] + [trade("pumpswap", 141, T0 + 9000)]
    p = write(tmp_path, rows)
    paths = [p]
    if shutil.which("zstd"):
        subprocess.run(["zstd", "-q", "-f", p, "-o", p + ".zst"], check=True)
        Path(p).unlink()
        paths = [p + ".zst"]
    out = tmp_path / "o.jsonl"
    assert G.main(["--tape", *paths, "--grace-s", "0", "--counts-only", "--out", str(out)]) == 0
    recs = [json.loads(x) for x in out.read_text().splitlines()]
    assert not any(r["type"] == "gap" for r in recs)
    ps = next(r for r in recs if r["type"] == "program" and r["prog"] == "pumpswap")
    assert ps["covered"] == 1 and "uncovered_ranges_merged" not in ps


def test_outcome_fields_are_never_needed(tmp_path):
    # rows carrying outcome-like fields change nothing: only prog/slot/t_recv_ms are read
    r1 = [jump("pumpswap", 100, 140, T0, T0 + 1)] + [trade("pumpswap", s, T0 + 9000) for s in range(101, 140)] + [trade("pumpswap", 141, T0 + 9000)]
    r2 = [dict(r, pnl_sol=5, sol_lamports=99) if r["type"] == "trade" else r for r in r1]
    a = G.render(G.reclassify(G.scan([write(tmp_path, r1, "a.jsonl")]), 0))
    b = G.render(G.reclassify(G.scan([write(tmp_path, r2, "b.jsonl")]), 0))
    assert a == b
