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


T0 = 1_000_000
SENT = 10**9


def sentinels(t=T0 + 500_000):
    return [trade("pumpswap", SENT, t), trade("pumpfun", SENT, t)]


def run(tmp, rows, grace=120, sentinel=True):
    rows = rows + (sentinels() if sentinel else [])
    sc = G.scan([write(tmp, rows)], grace)
    recs = G.reclassify(sc, grace)
    return {(r.get("prog"), r["type"], r.get("from_slot")): r for r in recs}, recs



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
    by, _ = run(tmp_path, rows, sentinel=False)
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
    c = write(tmp_path, sentinels(), "tape-2026-10-23T02.jsonl")
    l1 = G.render(G.reclassify(G.scan([a, b, c]), 120))
    l2 = G.render(G.reclassify(G.scan([c, b, a, a]), 120))
    assert l1 == l2
    last = json.loads(l1[-1])
    assert last["type"] == "sha256" and last["lines"] == len(l1) - 1
    assert next(json.loads(x) for x in l1 if '"type":"gap"' in x)["class"] == "covered"  # coverage is read across files
    assert json.loads(l1[0]) == {"type": "header", **{k: v for k, v in json.loads(l1[0]).items() if k != "type"}} and json.loads(l1[0])["grace_s"] == 120


def test_refuses_policy_paths_and_missing_files(tmp_path, capsys):
    d = tmp_path / "ledger"
    d.mkdir()
    p = d / "tape-2026-10-23T00.jsonl"
    p.write_text("")
    assert G.main(["--tape", str(p)]) == 2
    assert G.main(["--tape", str(tmp_path / "tape-2026-10-23T05.jsonl")]) == 2
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
    sent = write(tmp_path, sentinels(), "tape-2026-10-23T01.jsonl")
    assert G.main(["--tape", *paths, sent, "--counts-only", "--out", str(out)]) == 0
    recs = [json.loads(x) for x in out.read_text().splitlines()]
    assert not any(r["type"] == "gap" for r in recs)
    ps = next(r for r in recs if r["type"] == "program" and r["prog"] == "pumpswap")
    assert ps["covered"] == 1 and "uncovered_ranges_merged" not in ps


def test_outcome_fields_are_never_needed(tmp_path):
    # rows carrying outcome-like fields change nothing: only prog/slot/t_recv_ms are read
    r1 = [jump("pumpswap", 100, 140, T0, T0 + 1)] + [trade("pumpswap", s, T0 + 9000) for s in range(101, 140)] + [trade("pumpswap", 141, T0 + 9000)]
    r2 = [dict(r, pnl_sol=5, sol_lamports=99) if r["type"] == "trade" else r for r in r1]
    a = G.render(G.reclassify(G.scan([write(tmp_path, r1 + sentinels(), "tape-2026-10-23T00.jsonl")]), 120))
    b = G.render(G.reclassify(G.scan([write(tmp_path, r2 + sentinels(), "tape-2026-10-24T00.jsonl")]), 120))
    assert a[1:-1] == b[1:-1]  # header differs only by the hour


def test_f2_late_600s_row_does_not_cover(tmp_path):
    # slots 101..139 arrive 600 s after to_ms: not covered, whether or not tape runs far past the gap
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200)] + [trade("pumpswap", s, T0 + 200 + 600_000) for s in range(101, 140)]
    by_long, _ = run(tmp_path, rows)  # tape runs to +700 s
    rows_cut = rows[:1] + [trade("pumpswap", s, T0 + 130_000) for s in range(101, 120)]
    by_cut, _ = run(tmp_path, rows_cut + [trade("pumpswap", SENT, T0 + 400_000)])
    assert by_long[("pumpswap", "gap", 100)]["class"] == "empty"
    assert by_long[("pumpswap", "gap", 100)]["late_until_ms"] is None
    # same gap, tape cut at +130 s versus long tape: identical class for the same covering set
    assert by_cut[("pumpswap", "gap", 100)]["class"] == "empty"


def test_grace_boundary_inclusive_and_late_until(tmp_path):
    g = 120_000
    rows = [jump("pumpswap", 100, 132, T0, T0 + 200)]
    rows += [trade("pumpswap", s, T0 + 200 + g) for s in range(101, 131)]       # exactly at the limit: covers
    rows += [trade("pumpswap", 131, T0 + 200 + g + 1)]                           # one ms past: does not
    by, _ = run(tmp_path, rows)
    gp = by[("pumpswap", "gap", 100)]
    assert gp["class"] == "partial" and gp["uncovered_ranges"] == [[131, 131]]
    assert gp["late_until_ms"] == T0 + 200 + g
    by2, _ = run(tmp_path, [jump("pumpswap", 100, 132, T0, T0 + 200)] + [trade("pumpswap", s, T0 + 300) for s in range(101, 132)])
    assert by2[("pumpswap", "gap", 100)]["class"] == "covered" and by2[("pumpswap", "gap", 100)]["late_until_ms"] == T0 + 300


def test_endpoints_are_not_skipped(tmp_path):
    # rows only at from_slot and to_slot cover nothing; the skipped range is exactly from+1..to-1
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200), trade("pumpswap", 100, T0 + 10), trade("pumpswap", 140, T0 + 10)]
    by, _ = run(tmp_path, rows)
    g = by[("pumpswap", "gap", 100)]
    assert g["skipped_slots"] == 39 and g["uncovered_ranges"] == [[101, 139]] and g["class"] == "empty"


def test_noncontiguous_or_duplicate_hours_refused(tmp_path):
    a = write(tmp_path, sentinels(), "tape-2026-10-23T00.jsonl")
    c = write(tmp_path, sentinels(), "tape-2026-10-23T02.jsonl")
    assert G.main(["--tape", a, c]) == 2
    d = write(tmp_path, sentinels(), "tape-2026-10-23T00.jsonl.zst")
    assert G.main(["--tape", a, d]) == 2
    assert G.main(["--tape", write(tmp_path, [], "other.jsonl")]) == 2


def test_max_gap_slots_refused(tmp_path):
    p = write(tmp_path, [jump("pumpswap", 100, 100 + G.MAX_GAP_SLOTS + 5, T0, T0 + 1)])
    assert G.main(["--tape", p]) == 2


def test_out_goes_through_guard(tmp_path):
    p = write(tmp_path, sentinels())
    bad = tmp_path / "bots.json"
    assert G.main(["--tape", p, "--out", str(bad)]) == 2 and not bad.exists()


def test_windows_counts_and_header(tmp_path):
    hr = 3_600_000
    base = 1_792_400_400_000 // hr * hr  # an hour boundary; tape hours named for it below
    from datetime import datetime, timezone
    nm = lambda i: "tape-" + datetime.fromtimestamp((base + i * hr) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"  # noqa: E731
    files = []
    for i in range(6):
        rows = [trade("pumpswap", SENT + i, base + i * hr + 10), trade("pumpfun", SENT + i, base + i * hr + 10)]
        if i == 1:  # hour 1: a covered gap
            rows += [jump("pumpswap", 100, 140, base + hr + 1000, base + hr + 1200)] + [trade("pumpswap", s, base + hr + 2000) for s in range(101, 140)]
        if i == 3:  # hour 3: an empty gap
            rows += [jump("pumpswap", 500, 540, base + 3 * hr + 1000, base + 3 * hr + 1200)]
        files.append(write(tmp_path, rows, nm(i)))
    files.append(write(tmp_path, [trade("pumpswap", SENT + 9, base + 6 * hr + 500_000)], nm(6)))
    recs = G.reclassify(G.scan(files, 120), 120, (2, 4))
    w = {(r["prog"], r["hours"]): r for r in recs if r["type"] == "windows"}
    # 7 hours: 2 h windows = 6 starts; those touching hour 3 (starts 2h,3h) are bad
    assert w[("pumpswap", 2)]["windows"] == 6 and w[("pumpswap", 2)]["all_covered"] == 4
    assert w[("pumpswap", 4)]["windows"] == 4 and w[("pumpswap", 4)]["all_covered"] == 0
    assert w[("pumpfun", 4)]["all_covered"] == 4


def produced_file(tmp, first, last, ranges, name="produced.jsonl", queried=None):
    import arena_fetch_produced_slots as F
    p = tmp / name
    p.write_text("\n".join(F.render(F.ALLOWED_URLS[0], queried or [[first, last]], ranges)) + "\n")
    return str(p)


def test_produced_slots_no_block_counts_as_covered(tmp_path):
    # skipped 101..139; rows for all but 110..112 and 130. The leader skipped 110..112 (no block) but 130 produced a block.
    have = set(range(101, 140)) - {110, 111, 112, 130}
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200)] + [trade("pumpswap", s, T0 + 900) for s in sorted(have)] + sentinels()
    rows += [jump("pumpswap", 200, 240, T0, T0 + 200)] + [trade("pumpswap", s, T0 + 900) for s in range(201, 240) if s not in (210, 211)]
    sc = G.scan([write(tmp_path, rows)], 120)
    produced = [[101, 109], [113, 139], [201, 209], [212, 239]]   # 110-112 and 210-211 produced no block
    pf = G.load_produced(produced_file(tmp_path, 101, 239, [r for r in produced if r[0] <= 139] + [[r[0], r[1]] for r in produced if r[0] >= 201]))
    recs = G.reclassify(sc, 120, (), pf)
    gaps = {r["from_slot"]: r for r in recs if r["type"] == "gap"}
    assert gaps[100]["class"] == "partial" and gaps[100]["uncovered_ranges"] == [[130, 130]] and gaps[100]["no_block_slots"] == 3
    assert gaps[200]["class"] == "covered" and gaps[200]["no_block_slots"] == 2 and gaps[200]["uncovered_ranges"] == []
    ps = next(r for r in recs if r["type"] == "program" and r["prog"] == "pumpswap")
    assert ps["covered"] == 1 and ps["covered_via_no_block"] == 1 and ps["no_block_slots"] == 5
    assert recs[0]["produced_slots_sha256"] == pf["sha256"]
    # without the file nothing changes
    base = {r["from_slot"]: r for r in G.reclassify(sc, 120) if r["type"] == "gap"}
    assert base[200]["class"] == "partial"


def test_produced_file_checks(tmp_path):
    good = produced_file(tmp_path, 1, 10, [[1, 5]])
    G.load_produced(good)
    lines = Path(good).read_text().splitlines()
    Path(good).write_text(lines[0].replace('"count":5', '"count":6') + "\n" + lines[1] + "\n")
    with pytest.raises(G.Refused):
        G.load_produced(good)
    with pytest.raises(G.Refused):
        G.load_produced(produced_file(tmp_path, 1, 10, [[1, 5], [4, 8]], "b.jsonl"))
    # uncovered slot outside the file span -> refused
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200)] + sentinels()
    sc = G.scan([write(tmp_path, rows)], 120)
    with pytest.raises(G.Refused):
        G.reclassify(sc, 120, (), G.load_produced(produced_file(tmp_path, 101, 120, [[101, 120]], "c.jsonl")))


def test_fetch_chunks_and_allowlist():
    import arena_fetch_produced_slots as F
    calls = []
    def getter(url, a, b):
        calls.append((a, b))
        return [s for s in range(a, b + 1) if s % 10]   # every 10th slot produced no block
    sleeps = []
    r = F.fetch(F.ALLOWED_URLS[0], [[1, 120_000], [500_000, 500_099]], getter, sleeps.append)
    assert calls == [(1, 50_000), (50_001, 100_000), (100_001, 120_000), (500_000, 500_099)] and len(sleeps) == 3
    assert r[0] == [1, 9] and r[1] == [11, 19] and r[-1] == [500_091, 500_099]
    with pytest.raises(ValueError):
        F.fetch("https://mainnet.helius-rpc.com", [[1, 5]], getter, sleeps.append)
    with pytest.raises(RuntimeError):
        F.fetch(F.ALLOWED_URLS[0], [[1, 5]], lambda u, a, b: [99], sleeps.append)


def test_fetch_merge_and_unqueried_slots_refused(tmp_path):
    import arena_fetch_produced_slots as F
    assert F.merge_ranges([[10, 12], [20, 22], [100, 101]], 10) == [[10, 22], [100, 101]]
    assert F.merge_ranges([[10, 12], [20, 22]], 0) == [[10, 12], [20, 22]]
    # queried only 101..109; the gap's uncovered slot 130 is outside the queried ranges -> refused
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200)] + [trade("pumpswap", s, T0 + 900) for s in range(101, 140) if s not in (105, 130)] + sentinels()
    sc = G.scan([write(tmp_path, rows)], 120)
    pf = G.load_produced(produced_file(tmp_path, 101, 109, [[101, 104], [106, 109]], queried=[[101, 109]]))
    with pytest.raises(G.Refused):
        G.reclassify(sc, 120, (), pf)
    pf2 = G.load_produced(produced_file(tmp_path, 101, 130, [[101, 104], [106, 109], [130, 130]], "p2.jsonl", queried=[[101, 109], [130, 130]]))
    g = next(r for r in G.reclassify(sc, 120, (), pf2) if r["type"] == "gap")
    assert g["class"] == "partial" and g["no_block_slots"] == 1 and g["uncovered_ranges"] == [[130, 130]]


def test_produced_file_applies_only_to_its_program(tmp_path):
    rows = [jump("pumpswap", 100, 140, T0, T0 + 200), jump("pumpfun", 100, 140, T0, T0 + 200)] + sentinels()
    sc = G.scan([write(tmp_path, rows)], 120)
    pf = G.load_produced(produced_file(tmp_path, 101, 139, [], "np.jsonl"))   # prog pumpswap: nothing produced = all 39 slots had no block
    gaps = {r["prog"]: r for r in G.reclassify(sc, 120, (), pf) if r["type"] == "gap"}
    assert gaps["pumpswap"]["class"] == "covered" and gaps["pumpswap"]["no_block_slots"] == 39
    assert gaps["pumpfun"]["class"] == "empty" and gaps["pumpfun"]["no_block_slots"] == 0
