"""floor_forward_ab / floor_nov_pnl censoring + pairing + path refusal + variant header, on synthetic fixtures only (no tape, no real ledger)."""
import json, subprocess, sys, tempfile, types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import floor_forward_ab as AB  # noqa: E402
import floor_nov_pnl as P  # noqa: E402
import floor_tape_paths as TP  # noqa: E402


def ms(d, h=12, m=0):
    return int(datetime(2026, 11, d, h, m, tzinfo=timezone.utc).timestamp() * 1000)


def op(pid, pool, t):
    return {"kind": "open", "position_id": pid, "pool": pool, "decision_ms": {"entry": t}}


def tr(pid, stake, net, t0, state="filled"):
    legs = {f"{m}_1p9": {"net_lamports": net, "state": state} for m in ("flat", "press")}
    return {"kind": "trade", "position_id": pid, "stake_sol": stake, "exit_reason": "tp_1.5x", "decision_ms": {"entry": t0, "exit": t0 + 1000}, "legs": {"total": legs}, "calc": {}}


def ex(pid, stake, reason="feed_gap"):
    return {"kind": "excluded", "position_id": pid, "stake_sol": stake, "exclusion_reason": reason}


def write(d, rows, variant=None):
    p = Path(d) / "ledger" / "l1-floor-v2"
    p.mkdir(parents=True)
    (p / "trades-x.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    if variant is not None:
        (Path(d) / "variant.json").write_text(json.dumps(variant))
    return str(Path(d) / "ledger")


def run_pnl(d, rows, to_ms=None, variant=None):
    P.main([write(d, rows, variant), d + "/o.json"] + (["--to-ms", str(to_ms)] if to_ms else []))
    return json.load(open(d + "/o.json"))


def both(pid, pool, t, net):
    return [op(pid, pool, t)] + [tr(pid, s, net, t) for s in (0.10, 0.25)]


def test_open_at_end_is_open_not_feed_gap_and_gap_is_separate():
    rows = both("a", "A", ms(5, 1), 1_000_000) + [op("o", "O", ms(5, 2))] + [op("g", "G", ms(5, 3)), ex("g", 0.10), ex("g", 0.25)]
    with tempfile.TemporaryDirectory() as d:
        day = run_pnl(d, rows)["entries_by_day"]["0.1:flat"]["2026-11-05"]
    assert (day["n_entries"], day["n_closed"], day["n_engine_excluded"], day["n_open_at_tape_end"]) == (3, 1, 1, 1)
    assert day["exclusion_reasons"] == {"feed_gap": 1}
    assert "n_unpriced" not in day


def test_day_with_open_position_is_incomplete_and_has_no_totals():
    rows = both("a", "A", ms(5, 1), 1_000_000) + [op("o", "O", ms(5, 2))]
    with tempfile.TemporaryDirectory() as d:
        day = run_pnl(d, rows)["entries_by_day"]["0.25:press"]["2026-11-05"]
    assert day["complete"] is False and day["total_sol"] is None and day["ex_top3_sol"] is None and day["n_closed"] == 1


def test_bound_uses_hold_tick_and_leg_margin():
    last = ms(5, 23)
    b = P.complete_bound_ms(last)
    assert b == last + 86_400_000 + 1_900 + 1_000 + 1_550
    rows = both("a", "A", last, 1_000_000)
    with tempfile.TemporaryDirectory() as d:
        assert run_pnl(d, rows, to_ms=b - 1)["entries_by_day"]["0.1:flat"]["2026-11-05"]["complete"] is False
    with tempfile.TemporaryDirectory() as d:
        r = run_pnl(d, rows, to_ms=b)["entries_by_day"]["0.1:flat"]["2026-11-05"]
        assert r["complete"] is True and abs(r["total_sol"] - 1e-3) < 1e-12


def test_cutover_hour_is_excluded():
    rows = both("a", "A", ms(7, 18, 30), 5) + both("b", "B", ms(7, 19, 1), 7)
    with tempfile.TemporaryDirectory() as d:
        day = run_pnl(d, rows)["entries_by_day"]["0.1:flat"]["2026-11-07"]
    assert day["n_entries"] == 1


def test_variant_header_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        r = run_pnl(d, both("a", "A", ms(5, 1), 1), variant={"variant": {"Q_STAR": 1e9}, "frozen_rule": False})
    assert r["header"]["frozen_rule"] is False and r["header"]["variant"] == {"Q_STAR": 1e9}
    with tempfile.TemporaryDirectory() as d:
        assert run_pnl(d, both("a", "A", ms(5, 1), 1))["header"]["frozen_rule"] is None


def pnl_json(rows, to_ms, variant=None):
    with tempfile.TemporaryDirectory() as d:
        return run_pnl(d, rows, to_ms=to_ms, variant=variant)


def test_table_incomplete_day_prints_no_delta_and_pooled_forward_uses_complete_days_only():
    far = ms(20)
    # 11-10 (in-sample) complete; 11-12 forward complete; 11-13 forward has an open position in the vault arm only
    base = both("a", "A", ms(10, 1), 1_000_000) + both("b", "B", ms(12, 1), 2_000_000) + both("c", "C", ms(13, 1), 4_000_000)
    vault = both("a", "A", ms(10, 1), 1_000_000) + both("b", "B", ms(12, 1), 3_000_000) + both("c", "C", ms(13, 1), 4_000_000) + [op("v", "V", ms(13, 2))]
    pb, pv = pnl_json(base, far, {"variant": {}, "frozen_rule": True}), pnl_json(vault, far, {"variant": {"Q_STAR": 1e9}, "frozen_rule": False})
    with tempfile.TemporaryDirectory() as d:
        for n, o in (("b", pb), ("v", pv)):
            json.dump(o, open(f"{d}/{n}.json", "w"))
        AB.table(types.SimpleNamespace(base=d + "/b.json", variant=d + "/v.json", out_json=d + "/ab.json", out_md=d + "/ab.md", end_hour="2026-11-19T23", forward_from="2026-11-12"))
        r, md = json.load(open(d + "/ab.json")), open(d + "/ab.md").read()
    leg = r["legs"]["0.1:flat"]
    days = {x["day"]: x for x in leg["days"]}
    assert days["2026-11-13"]["complete"] is False and "delta" not in days["2026-11-13"] and "total_sol" not in days["2026-11-13"]["vault1.0"]
    assert days["2026-11-13"]["vault1.0"]["n_open_at_tape_end"] == 1
    assert days["2026-11-12"]["complete"] and abs(days["2026-11-12"]["delta"]["total_sol"] - 1e-3) < 1e-12
    f = leg["pooled"]["FORWARD"]
    assert f["n_complete_days"] == 1 and f["days"] == ["2026-11-12"]
    assert abs(f["base"]["total_sol"] - 2e-3) < 1e-12 and abs(f["vault1.0"]["total_sol"] - 3e-3) < 1e-12
    assert leg["pooled"]["IN-SAMPLE"]["n_complete_days"] == 1
    assert ("paper; days < 2026-11-12 are IN-SAMPLE for the variant choice; FORWARD days count only when COMPLETE; nothing here is a gate read; "
            "the delta is not paired unless the paired table says so") in md
    assert "INCOMPLETE" in md and "frozen_rule=False" in md and "frozen_rule=True" in md


def test_pair_groups_same_retimed_base_only_variant_only():
    def pos(pid, pool, t, net, status="closed"):
        return {"pid": pid, "pool": pool, "t0": t, "day": "2026-11-12", "status": {"0.1": status}, "net": {"0.1:flat": net}}
    base = [pos("b1", "S", ms(12, 1), 1e6), pos("b2", "R", ms(12, 2), 2e6), pos("b3", "B", ms(12, 3), 4e6), pos("b4", "X", ms(12, 4), 8e6, "open_at_tape_end")]
    vault = [pos("v1", "S", ms(12, 1), 1.5e6), pos("v2", "R", ms(12, 5), 3e6), pos("v3", "V", ms(12, 6), 16e6), pos("v4", "X", ms(12, 4), 5e6)]
    g = AB.pair(base, vault, "0.1", "flat", {"2026-11-12"})
    assert (g["same"]["n"], g["retimed"]["n"], g["base_only"]["n"], g["variant_only"]["n"]) == (2, 1, 1, 1)
    assert g["same"]["base"]["n_priced"] == 1 and g["same"]["vault1.0"]["n_priced"] == 2  # X: base open, vault priced
    assert abs(g["same"]["vault1.0"]["total_sol"] - 6.5e-3) < 1e-12
    assert abs(g["retimed"]["base"]["total_sol"] - 2e-3) < 1e-12 and abs(g["retimed"]["vault1.0"]["total_sol"] - 3e-3) < 1e-12
    assert g["base_only"]["vault1.0"]["n_priced"] == 0 and abs(g["base_only"]["base"]["total_sol"] - 4e-3) < 1e-12
    assert abs(g["variant_only"]["vault1.0"]["total_sol"] - 16e-3) < 1e-12


def test_refused_tape_dirs():
    bad = ["/var/lib/mal/sealed/fast-trades-tip", "/var/lib/mal-live/x", "/data/mal/arena/tape", "/home/claude/mal-data/arena-mirror/tape", "/data/mal/meta-capture",
           "/data/mal/blocks/forward-1016/trades", "/data2/mal/scratch/walk2/x", "/data2/mal/scratch/probe-a", "/data2/mal/scratch/hv4", "/data2/mal/scratch/q50", "/home/claude/tape",
           "/data2/mal/arena-tip-feed/other", "/data2/mal/scratchy", "/data2/mal/scratch", "/data2/mal/scratch/arena-tip-equiv/x", "/data2/mal/scratch/gapfill-x/tape"]
    for p in bad:
        assert TP.refusal(p), p
    for p in ("/data2/mal/arena-tip-feed/hv7", "/data2/mal/scratch/floor-nov-funnel/tapecopy", "/data2/mal/scratch/floor-forward-ab/2026-11-12T20/tapelink", "/data2/mal/scratch/floor-gap-range/x"):
        assert TP.refusal(p) is None, p


def test_paper_script_refuses_before_any_work():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/research/floor_nov_paper.py"), "--root", "/nonexistent-x", "--tape-dir", "/var/lib/mal/sealed/x", "--from-ms", "0", "--to-ms", "1"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "refused path" in (r.stderr + r.stdout)


def _table(d, base, vault, forward_from="2026-11-12"):
    for n, o in (("b", base), ("v", vault)):
        json.dump(o, open(f"{d}/{n}.json", "w"))
    AB.table(types.SimpleNamespace(base=d + "/b.json", variant=d + "/v.json", out_json=d + "/ab.json", out_md=d + "/ab.md", end_hour="2026-11-19T23", forward_from=forward_from))
    return json.load(open(d + "/ab.json")), open(d + "/ab.md").read()


def test_table_refuses_pnl_without_bound():
    rows = both("a", "A", ms(10, 1), 1_000_000)
    ok, nb = pnl_json(rows, ms(20)), pnl_json(rows, None)
    assert ok["bound_applied"] is True and nb["bound_applied"] is False
    for base, vault in ((nb, ok), (ok, nb)):
        with tempfile.TemporaryDirectory() as d:
            try:
                _table(d, base, vault)
                raise AssertionError("table accepted a pnl file without --to-ms")
            except SystemExit as e:
                assert "--to-ms" in str(e)


def test_like_for_like_subtotal_excludes_one_side_priced_pairs():
    def pos(pid, pool, t, net, status="closed"):
        return {"pid": pid, "pool": pool, "t0": t, "day": "2026-11-12", "status": {"0.1": status}, "net": {"0.1:flat": net}}
    base = [pos("b1", "S", ms(12, 1), 1e6), pos("b2", "R", ms(12, 2), 2e6), pos("b4", "X", ms(12, 4), 8e6, "open_at_tape_end")]
    vault = [pos("v1", "S", ms(12, 1), 1.5e6), pos("v2", "R", ms(12, 5), 3e6), pos("v4", "X", ms(12, 4), 5e6), pos("v3", "V", ms(12, 6), 16e6)]
    L = AB.pair(base, vault, "0.1", "flat", {"2026-11-12"})["like_for_like"]["same_plus_retimed"]
    assert L["n_both_priced"] == 2 and abs(L["base_sol"] - 3e-3) < 1e-12 and abs(L["vault_sol"] - 4.5e-3) < 1e-12 and abs(L["delta_sol"] - 1.5e-3) < 1e-12
    assert L["n_vault_priced_only"] == 1 and abs(L["vault_sol_one_side"] - 5e-3) < 1e-12 and L["n_base_priced_only"] == 0
    # the vault-only pool V (not a pair) is in neither the delta nor the one-side row
    assert abs(L["vault_sol"] - 4.5e-3) < 1e-12


def test_like_for_like_rows_in_markdown_and_heading_does_not_promise_a_delta():
    rows_b = both("a", "A", ms(12, 1), 1_000_000) + [op("x", "X", ms(12, 2)), ex("x", 0.10, "missing_v"), ex("x", 0.25, "missing_v")]
    rows_v = both("a", "A", ms(12, 1), 3_000_000) + both("x", "X", ms(12, 2), 5_000_000)
    with tempfile.TemporaryDirectory() as d:
        r, md = _table(d, pnl_json(rows_b, ms(20)), pnl_json(rows_v, ms(20)))
    assert "like-for-like" in md and "NOT in the delta" in md and "delta of totals below" not in md
    L = r["legs"]["0.1:flat"]["paired"]["FORWARD"]["like_for_like"]["same_plus_retimed"]
    assert L["n_both_priced"] == 1 and abs(L["delta_sol"] - 2e-3) < 1e-12 and L["n_vault_priced_only"] == 1
    assert "engine exclusion reasons, FORWARD: base {'missing_v': 1}" in md
    assert r["legs"]["0.1:flat"]["pooled"]["FORWARD"]["base"]["exclusion_reasons"] == {"missing_v": 1}


def test_engine_exclusion_reasons_are_all_counted_with_breakdown():
    rows = [op("g", "G", ms(5, 3)), ex("g", 0.10), ex("g", 0.25), op("m", "M", ms(5, 4)), ex("m", 0.10, "missing_v"), ex("m", 0.25, "missing_v"),
            op("r", "R", ms(5, 5)), ex("r", 0.10, "engine_restart"), ex("r", 0.25, "engine_restart")]
    with tempfile.TemporaryDirectory() as d:
        day = run_pnl(d, rows)["entries_by_day"]["0.1:flat"]["2026-11-05"]
    assert day["n_engine_excluded"] == 3 and day["n_open_at_tape_end"] == 0
    assert day["exclusion_reasons"] == {"feed_gap": 1, "missing_v": 1, "engine_restart": 1}


def test_header_follows_forward_from():
    assert AB.header("2026-11-15").startswith("paper; days < 2026-11-15 are IN-SAMPLE")
    assert AB.header() == AB.HEADER and "days < 2026-11-12" in AB.HEADER


def test_kcap_block_withheld_when_a_position_is_open_at_tape_end():
    with tempfile.TemporaryDirectory() as d:
        r = run_pnl(d, both("a", "A", ms(5, 1), 1_000_000) + [op("o", "O", ms(5, 2))], to_ms=ms(20))
        assert r["out"] is None and "withheld" in r["out_refused"]
    with tempfile.TemporaryDirectory() as d:
        r = run_pnl(d, both("a", "A", ms(5, 1), 1_000_000), to_ms=ms(20))
        assert r["out"] and "out_refused" not in r


def test_per_file_symlink_guard_and_narrowed_roots():
    import os
    with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
        real, evil = Path(allowed) / "tape-2026-11-02T01.jsonl.zst", Path(outside) / "tape-x.jsonl.zst"
        real.write_text("x"); evil.write_text("y")
        TP.require_files_ok(allowed, roots=(os.path.realpath(allowed),))
        os.symlink(evil, Path(allowed) / "tape-2026-11-02T02.jsonl.zst")
        try:
            TP.require_files_ok(allowed, roots=(os.path.realpath(allowed),))
            raise AssertionError("file symlink escaping the allowed root was accepted")
        except SystemExit as e:
            assert "tape-2026-11-02T02" in str(e)
        # non-tape entries are not opened by the replay and are ignored
    assert TP.refusal("/data2/mal/scratch/floor-nov-funnel/tapecopy") is None and TP.refusal("/data2/mal/scratch/other-job/x")
