"""Tests for the LP-law part of tools/exp012_forward_vbook.py (DEC-016 Amendment 5 section 7(a), (c), (d)).

Synthetic fixtures only (tools.exp012_fixtures through test_exp012_forward_vbook's walk). Nothing opens /data. The fixture
trades: the entered mints are mB2 (3050/3060), mC (4050/4060), mD (5050/5050) and mE (6050/6060) as entry/exit fill slots; mA and mB are not entered.

Run: PYTHONPATH=$PWD python -m pytest -q tools/test_exp012_forward_vbook_lp.py
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import tools.exp012_forward as fw
import tools.exp012_forward_vbook as vb
import tools.exp012_forward_vmap as vm
import tools.pumpswap_lp_history as lph
import tools.test_exp012_forward as tfw
import tools.test_exp012_forward_vbook as tvb
from tools.pumpswap_virtual import load_map
from tools.test_exp012_forward_vbook import POOLS, V, VBase, write_vmap
from tools.test_pumpswap_lp_history import FakeChain, pk

PEND = 1000
X0 = V + PEND  # V0 at pool creation
T0 = int(datetime(2026, 10, 16, 1, 0, 0, tzinfo=timezone.utc).timestamp())
T1 = T0 + 600
S = 1_000_000


def dep(slot: int, lp: int = 100_000, s_before: int = S, bt: int | None = None) -> dict:
    return {"slot": slot, "block_time": T0 - 5000 + slot if bt is None else bt, "sig": f"sig{slot}", "kind": "deposit" if lp > 0 else "withdraw", "s_before": s_before, "lp_delta": lp}


def chain_events(evs: list[dict]) -> list[dict]:
    """Chain the supply through the events (s_before of each = S_after of the previous) and number them."""
    s = S
    out = []
    for i, e in enumerate(evs):
        out.append({**e, "s_before": s, "idx": i})
        s += e["lp_delta"]
    return out


# ---- pure functions ----------------------------------------------------------------------------------------------


def test_entry_slot_v0_inverts_events_after_the_entry_fill() -> None:
    evs = chain_events([dep(1070)])  # after the entry (1050) and after the exit (1060)
    v0_final = lph.replay_forward(X0, evs)
    st, cands, fl = vb.pool_values(1050, 1060, v0_final, PEND, evs, (T0, T1))
    assert st == "ok" and not fl["hold"] and not fl["same_slot"]
    assert cands == [lph.v0_before(v0_final, evs) - PEND]  # the smallest preimage, minus the final-map pending
    assert abs(cands[0] - (X0 - PEND)) <= 1  # the pool's real creation V0, to the inversion's lamport


def test_events_before_the_entry_slot_are_kept() -> None:
    evs = chain_events([dep(1040)])
    v0_final = lph.replay_forward(X0, evs)
    st, cands, _ = vb.pool_values(1050, 1060, v0_final, PEND, evs, (T0, T1))
    assert (st, cands) == ("ok", [v0_final - PEND])


def test_snapshot_anchor_applies_events_forward_to_the_entry_slot() -> None:
    evs = chain_events([dep(900), dep(1040), dep(1070)])
    snap_span = (T0 - 4000, T0 - 3990)  # an earlier fetch: the event at slot 900 (bt T0-4100) is before it, the others after
    evs[0]["block_time"], evs[1]["block_time"], evs[2]["block_time"] = T0 - 4100, T0 - 3000, T0 - 2000
    anchor = lph.replay_forward(X0, evs[:1])
    st, cands, _ = vb.pool_values(1050, 1060, anchor, PEND, evs, snap_span)
    assert st == "ok"
    assert cands == [lph.replay_forward(anchor, evs[1:2]) - PEND]  # forward across 1040 only; 1070 is after the entry


def test_event_inside_the_hold_prices_at_entry_and_exit_slot_values() -> None:
    evs = chain_events([dep(1055)])  # 1050 < 1055 <= 1060
    v0_final = lph.replay_forward(X0, evs)
    st, cands, fl = vb.pool_values(1050, 1060, v0_final, PEND, evs, (T0, T1))
    assert st == "ok" and fl["hold"] and not fl["same_slot"]
    assert cands == sorted({lph.v0_before(v0_final, evs) - PEND, v0_final - PEND}) and len(cands) == 2
    st, cands, fl = vb.pool_values(1050, 1050, v0_final, PEND, evs, (T0, T1))  # no exit fill -> x = last priced slot (here: entry)
    assert st == "ok" and not fl["hold"] and len(cands) == 1


def test_no_exit_fill_uses_the_last_priced_slot() -> None:
    evs = chain_events([dep(1085)])
    v0_final = lph.replay_forward(X0, evs)
    st, cands, fl = vb.pool_values(1050, 1090, v0_final, PEND, evs, (T0, T1))  # x = last_slot 1090
    assert st == "ok" and fl["hold"] and len(cands) == 2


def test_same_slot_event_is_priced_both_ways() -> None:
    evs = chain_events([dep(1050)])
    v0_final = lph.replay_forward(X0, evs)
    st, cands, fl = vb.pool_values(1050, 1050, v0_final, PEND, evs, (T0, T1))
    assert st == "ok" and fl["same_slot"] and not fl["hold"]
    assert cands == sorted({lph.v0_before(v0_final, evs) - PEND, v0_final - PEND}) and len(cands) == 2
    evs = chain_events([dep(1060)])  # in the exit fill's slot: inside the hold, and same-slot at the exit
    st, cands, fl = vb.pool_values(1050, 1060, lph.replay_forward(X0, evs), PEND, evs, (T0, T1))
    assert st == "ok" and fl["hold"] and fl["same_slot"]


def test_ambiguous_placement_inside_the_anchor_span_is_unresolved() -> None:
    evs = chain_events([dep(1070, bt=T0 + 100)])  # inside the final fetch's own span: before or after the read
    v0_read = lph.replay_forward(X0, evs)  # one placement; the other leaves the V0 without the event
    st, why, _ = vb.pool_values(1050, 1060, v0_read, PEND, evs, (T0, T1))
    assert (st, why) == ("unresolved", "ambiguous_anchor_placement")
    evs0 = chain_events([dep(1020, lp=0, bt=T0 + 100)])  # a zero-delta event cannot move V0: both placements agree
    assert vb.pool_values(1050, 1060, X0, PEND, evs0, (T0, T1))[0] == "ok"


def test_k_parametric_through_the_slots_only() -> None:
    evs = chain_events([dep(1070)])
    v0_final = lph.replay_forward(X0, evs)
    a = vb.pool_values(1050, 1060, v0_final, PEND, evs, (T0, T1))
    b = vb.pool_values(1050, 1072, v0_final, PEND, evs, (T0, T1))  # a later exit (a larger k) puts the event inside the hold
    assert len(a[1]) == 1 and len(b[1]) == 2 and b[2]["hold"]


def row(mint, flat, press, e=1, x=2):
    return {"mint": mint, "mig_ms": 1, "entered": True, "flat": flat, "press": press, "flat_sol": flat / 1e9, "press_sol": press / 1e9, "entry_slot": e, "exit_slot": x, "no_v_pools": []}


def test_min_is_taken_separately_per_leg_and_only_for_affected_trades() -> None:
    base = [row("a", 5.0, 5.0), row("b", 7.0, 7.0)]
    passes = {"c0": [row("a", 1.0, 9.0), row("b", 0.0, 0.0)], "c1": [row("a", 4.0, 2.0), row("b", 0.0, 0.0)]}
    info = {("a", 1): {"changed": {"p"}, "bad": {}, "hold": True, "same_slot": False}, ("b", 1): {"changed": set(), "bad": {}, "hold": False, "same_slot": False}}
    d = Path(__import__("tempfile").mkdtemp())
    out, rep = vb.lp_min_rows(base, ({"p": {10, 20}}, info), {"p": 15}, lambda path, tag: passes[tag], d, "c")
    assert rep["n_passes"] == 2 and [m["tag"] for m in rep["maps"]] == ["c-0", "c-1"]
    a, b = out
    assert (a["flat"], a["press"]) == (1.0, 2.0)  # the lower of each leg, from different passes
    assert (a["flat_sol"], a["press_sol"]) == (1e-9, 2e-9)
    assert b["flat"] == 7.0  # not affected: untouched
    assert [load_map(d / m["file"])["p"] for m in rep["maps"]] == [10, 20]
    assert all(m["sha256"] == fw._sha256_file(d / m["file"]) for m in rep["maps"])


def test_a_slot_shift_under_the_candidate_map_marks_the_trade() -> None:
    base = [row("a", 5.0, 5.0)]
    info = {("a", 1): {"changed": {"p"}, "bad": {}, "hold": True, "same_slot": False}}
    d = Path(__import__("tempfile").mkdtemp())
    out, rep = vb.lp_min_rows(base, ({"p": {10}}, info), {"p": 15}, lambda path, tag: [row("a", 1.0, 1.0, x=9)], d, "c")
    assert ("a", 1) in rep["shifted"]
    bad = vb.with_lp_bad(out, info, rep["shifted"])
    assert bad[0]["no_v_pools"] == ["p"]


def test_with_lp_bad_makes_a_null_v_trade() -> None:
    rows = [{**row("a", 1.0, 1.0), "no_v_pools": []}, {**row("b", 1.0, 1.0), "no_v_pools": []}]
    info = {("a", 1): {"bad": {"pA": "lphist_unresolved:x"}, "changed": set(), "hold": False, "same_slot": False}, ("b", 1): {"bad": {}, "changed": set(), "hold": False, "same_slot": False}}
    out = vb.with_lp_bad(rows, info, {})
    assert out[0]["no_v_pools"] == ["pA"] and out[1]["no_v_pools"] == []


def test_merge_meta_without_lp_moves_is_refused_and_sidecars_are_sha_checked(tmp_path: Path) -> None:
    meta_path = tmp_path / "m.merge.json"
    with pytest.raises(fw.Refused) as cm:
        vb.load_lp_moves({"sha256": {}}, meta_path)
    assert "no lp_moves" in str(cm.value)
    un, ur = tmp_path / "m.unexplained.json", tmp_path / "m.unresolved.json"
    un.write_text(json.dumps(["pU"]))
    ur.write_text(json.dumps({"pR": "no_v_base"}))
    lm = {"unexplained_file": un.name, "unexplained_sha256": fw._sha256_file(un), "unresolved_file": ur.name, "unresolved_sha256": fw._sha256_file(ur), "n_unexplained": 1, "n_unresolved": 1}
    bad, _ = vb.load_lp_moves({"lp_moves": lm}, meta_path)
    assert bad == {"pU": "merge_unexplained", "pR": "merge_unresolved:no_v_base"}
    un.write_text(json.dumps([]))  # changed after the merge
    with pytest.raises(fw.Refused):
        vb.load_lp_moves({"lp_moves": lm}, meta_path)


# ---- end to end -------------------------------------------------------------------------------------------------


class LpBase(VBase):
    def prep(self, out: Path, events: dict[str, list[dict]] | None = None, unresolved: dict[str, str] | None = None, meta: bool = False, unexplained: tuple = (), merge_unresolved: dict | None = None, **ent_extra) -> dict:
        """The V map (+ detail + fetch), an lphist file with meta, its ledger line, and optionally a merge meta with sidecars."""
        events = events or {}
        d = out.parent
        vmap, detail = {}, {}
        for p in POOLS:
            evs = chain_events(events.get(p, []))
            v0 = lph.replay_forward(X0, evs)
            vmap[p], detail[p] = v0 - PEND, {"pending": PEND, "v_base": v0}
        path = d / "pool_v.json"
        sha = write_vmap(path, vmap)
        vm.side(path, ".detail.json").write_text(json.dumps(detail) + "\n")
        fj = {"fetch_started_utc": "2026-10-16T01:00:00Z", "fetch_ended_utc": "2026-10-16T01:10:00Z", "fetch_slot_min": 900, "fetch_slot_max": 910, "new": True}
        vm.side(path, ".fetch.json").write_text(json.dumps(fj) + "\n")
        marker = vb.find_final_marker(out, d / "ledger.jsonl", True)
        t_from = int(fw.parse_clock(marker["clean_clock"]).timestamp()) - 3600
        hist = {}
        for p in POOLS:
            if p in (unresolved or {}):
                hist[p] = {"lp_mint": pk(9), "events": [], "resolved": False, "reason": unresolved[p], "lp_supply": None, "attempts": 1}
            else:
                hist[p] = {"lp_mint": pk(9), "events": chain_events(events.get(p, [])), "resolved": True, "reason": None, "lp_supply": 1, "attempts": 1, **ent_extra}
        lh = self.write_lphist(out, hist, t_from, name="lph1.json")
        res = {"vpath": path, "sha": sha, "lphist": lh, "t_from": t_from, "meta": None}
        if meta:
            res["meta"] = self.write_meta(d, sha, path, unexplained, merge_unresolved or {})
        return res

    def write_lphist(self, out: Path, hist: dict, t_from: int, name: str, ledger: bool = True, t_to: int = T1) -> Path:
        d = out.parent
        f = d / name
        f.write_text(json.dumps(hist, sort_keys=True) + "\n")
        sha = fw._sha256_file(f)
        mp = f.with_name(f.name + ".meta.json")
        mp.write_text(json.dumps({"sha256": sha, "n_pools": len(hist), "n_unresolved": sum(1 for e in hist.values() if not e["resolved"]), "t_from_unix": t_from, "t_to_unix": t_to, "attempts": []}) + "\n")
        if ledger:
            marker = vb.find_final_marker(out, d / "ledger.jsonl", True)
            fw.ledger_append(d / vb.LPHIST_RUNS_NAME, {"state": "COMPLETED", "clean_clock": marker["clean_clock"], "read_end": marker["read_end"], "test_window": True, "sha256": sha, "utc_time": "2026-10-16T01:30:00Z", "n_pools": len(hist), "n_unresolved": 0, "attempts": []})
        return f

    def write_meta(self, d: Path, sha: str, vpath: Path, unexplained: tuple, merge_unresolved: dict) -> Path:
        un, ur = d / "pool_v.merge.json.unexplained.json", d / "pool_v.merge.json.unresolved.json"
        un.write_text(json.dumps(sorted(unexplained)))
        ur.write_text(json.dumps(merge_unresolved))
        meta = {
            "sha256": {"out": sha, "final_detail": fw._sha256_file(vm.side(vpath, ".detail.json")), "snapshots": [], "snapshot_details": []},
            "final_fetch": tvb.FRESH, "filled_pools": [],
            "lp_moves": {"n_explained": 0, "n_unexplained": len(unexplained), "n_unresolved": len(merge_unresolved), "ceiling": 0.001, "unexplained_file": un.name, "unexplained_sha256": fw._sha256_file(un), "unresolved_file": ur.name, "unresolved_sha256": fw._sha256_file(ur), "fetch_spans_unix": [[T0, T1]]},
        }
        mp = d / "pool_v.merge.json"
        mp.write_text(json.dumps(meta))
        return mp

    def go(self, walk, art, out, p: dict, lphist: Path | None = None, vb_out: Path | None = None):
        extra = ["--lphist", str(lphist or p["lphist"])]
        if p["meta"]:
            extra += ["--vmap-merge-meta", str(p["meta"])]
        rc, err = self.run_vbook(walk, art, out, p["vpath"], p["sha"], *extra, vb_out=vb_out)
        rep = None
        f = (vb_out or out.parent / "vb") / "vbook_report.json"
        if f.exists():
            rep = json.loads(f.read_text())
        return rc, err, rep

    def ledger_lines(self, out: Path) -> list[dict]:
        f = out.parent / vb.RUNS_LEDGER_NAME
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


class TestEndToEnd(LpBase):
    def test_no_lp_events_prices_at_the_final_map_and_reports_zero_counts(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out)
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        lp = rep["lp_pricing"]
        self.assertEqual([m for m in lp["vmaps"] if m["kind"] == "primary_c"], [])
        c = lp["counts"]
        self.assertEqual((c["n_entered_on_lp_moved_pools"], c["n_entered_with_lp_event_inside_hold"], c["n_entered_same_slot_both_ways"], c["n_entered_touching_any_unresolved"]), (0, 0, 0, 0))
        self.assertEqual(lp["lphist"]["lphist_sha256"], fw._sha256_file(p["lphist"]))
        self.assertFalse(lp["sensitivity_final_map_v0"]["differs_from_primary"])
        self.assertFalse(any("sensitivity" in b for b in rep["live_blockers"]))
        self.assertIn(rep["b_verdict"], ("PASS", "FAIL"))
        started = [x for x in self.ledger_lines(out) if x["state"] == "STARTED"][0]
        self.assertEqual(started["lphist_sha256"], fw._sha256_file(p["lphist"]))

    def test_entry_slot_pricing_changes_pnl_and_the_variant_map_has_a_sha(self) -> None:
        walk, art, out = self.final()
        evs = {"pool-mC": [dep(4070)]}  # after the entry (1050) and the exit (1060) fills
        p = self.prep(out, evs)
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        lp = rep["lp_pricing"]
        self.assertEqual(lp["counts"]["n_entered_on_lp_moved_pools"], 1)
        self.assertEqual(lp["counts"]["n_entered_with_lp_event_inside_hold"], 0)
        prim = [m for m in lp["vmaps"] if m["kind"] == "primary_c"]
        self.assertEqual(len(prim), 1)
        mp = out.parent / "vb" / "vmaps" / prim[0]["file"]
        self.assertEqual(fw._sha256_file(mp), prim[0]["sha256"])
        m = load_map(mp)
        base = load_map(p["vpath"])
        self.assertEqual({k for k in m if m[k] != base[k]}, {"pool-mC"})
        evl = chain_events(evs["pool-mC"])
        self.assertEqual(m["pool-mC"], lph.v0_before(lph.replay_forward(X0, evl), evl) - PEND)
        self.assertNotEqual(m["pool-mC"], base["pool-mC"])

    def test_hold_event_takes_the_lower_pnl_per_leg_over_both_maps(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, {"pool-mC": [dep(4055)]})
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        lp = rep["lp_pricing"]
        self.assertEqual(lp["counts"]["n_entered_with_lp_event_inside_hold"], 1)
        prim = [m for m in lp["vmaps"] if m["kind"] == "primary_c"]
        self.assertEqual(len(prim), 2)
        per_map = []
        for m in prim:
            with tfw.patched():
                rows, thr = vb.score_hours_v(walk, tvb.e11_hours(), art, out.parent / f"re{m['tag']}", out.parent / "vb" / "vmaps" / m["file"], "v", out.parent / f"rc{m['tag']}", False)
            per_map.append({r["mint"]: r for r in rows if r["score"] >= thr})
        ma = [pm["mC"] for pm in per_map]
        self.assertNotEqual(ma[0]["flat"], ma[1]["flat"])
        lows = {leg: min(x[leg] for x in ma) for leg in ("flat", "press")}
        entered = {r["mint"] for r in fw.read_rows(out / "rows.jsonl") if r["entered"]}
        others = sum(per_map[0][k]["flat"] for k in entered if k != "mC")
        got = rep["b_mcap_mode_v"]["flat_15"]["total_sol"]
        self.assertAlmostEqual(got, (others + lows["flat"]) / 1e9, places=8)

    def test_same_slot_event_is_priced_both_ways(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, {"pool-mC": [dep(4050)]})
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        c = rep["lp_pricing"]["counts"]
        self.assertEqual(c["n_entered_same_slot_both_ways"], 1)
        self.assertEqual(len([m for m in rep["lp_pricing"]["vmaps"] if m["kind"] == "primary_c"]), 2)

    def test_ambiguous_anchor_placement_is_unresolved_and_not_decidable(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, {"pool-mC": [dep(4070, bt=T0 + 100)]})
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        self.assertEqual(rep["lp_pricing"]["counts"]["n_entered_touching_ambiguous_or_inconsistent"], 1)
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)  # 1 of 6 entered trades > 1%
        self.assertIn("pool-mC", rep["null_v"]["null_v_pool_ids"])
        self.assertTrue(any("NOT_DECIDABLE" in b for b in rep["live_blockers"]))

    def test_lphist_unresolved_pool_is_null_v(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, unresolved={"pool-mC": "logs_truncated"})
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)
        self.assertEqual(rep["lp_pricing"]["counts"]["n_entered_touching_lphist_unresolved"], 1)

    def test_merge_unexplained_and_unresolved_pools_are_null_v(self) -> None:
        for kw, key in (({"unexplained": ("pool-mB2",)}, "n_entered_touching_merge_unexplained"), ({"merge_unresolved": {"pool-mB2": "no_v_base"}}, "n_entered_touching_merge_unresolved")):
            walk, art, out = self.final()
            p = self.prep(out, meta=True, **kw)
            rc, err, rep = self.go(walk, art, out, p)
            self.assertEqual(rc, 0, err)
            self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)
            self.assertEqual(rep["lp_pricing"]["counts"][key], 1)
            self.assertEqual(rep["lp_pricing"]["lphist"]["merge_lp_moves"]["n_explained"], 0)

    def test_merge_meta_without_lp_moves_refuses_before_started(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, meta=True)
        doc = json.loads(p["meta"].read_text())
        del doc["lp_moves"]
        p["meta"].write_text(json.dumps(doc))
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 2)
        self.assertIn("no lp_moves", err)
        self.assertEqual(self.ledger_lines(out), [])

    def test_supply_read_before_the_final_fetch_is_unresolved(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, supply_slot=5)  # fetch_slot_max is 910
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)

    def test_only_the_first_completed_lphist_run_is_used(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out)
        hist = json.loads(p["lphist"].read_text())
        second = self.write_lphist(out, {**hist, "extra": {"lp_mint": pk(9), "events": [], "resolved": True, "reason": None, "lp_supply": 1}}, p["t_from"], "lph2.json")
        rc, err, rep = self.go(walk, art, out, p, lphist=second)
        self.assertEqual(rc, 2, err)
        self.assertIn("first completed lphist run", err)
        self.assertEqual(self.ledger_lines(out), [])  # refused before STARTED: the window is not spent
        rc, err, rep = self.go(walk, art, out, p)  # the first one still works
        self.assertEqual(rc, 0, err)

    def test_lphist_without_a_ledger_line_or_with_a_late_range_refuses(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out)
        orphan = self.write_lphist(out, {**json.loads(p["lphist"].read_text()), "extra": {"lp_mint": pk(9), "events": [], "resolved": True, "reason": None, "lp_supply": 1}}, p["t_from"], "orphan.json", ledger=False)
        rc, err, _ = self.go(walk, art, out, p, lphist=orphan)
        self.assertEqual(rc, 2)
        late = self.write_lphist(out, {}, p["t_from"] + 7200, "late.json", ledger=False)
        (out.parent / vb.LPHIST_RUNS_NAME).unlink()
        marker = vb.find_final_marker(out, out.parent / "ledger.jsonl", True)
        fw.ledger_append(out.parent / vb.LPHIST_RUNS_NAME, {"state": "COMPLETED", "clean_clock": marker["clean_clock"], "read_end": marker["read_end"], "test_window": True, "sha256": fw._sha256_file(late), "utc_time": "x"})
        rc, err, _ = self.go(walk, art, out, p, lphist=late)
        self.assertEqual(rc, 2)
        self.assertIn("after the window start - 1 h", err)

    def test_test_window_without_lphist_keeps_the_old_behaviour(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 0, err)
        self.assertIsNone(json.loads((out.parent / "vb" / "vbook_report.json").read_text())["lp_pricing"])

    def test_sensitivity_line_goes_to_live_blockers_and_pending0_is_report_only(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, {"pool-mC": [dep(4070)]})
        real = vb.book_summary
        calls = []

        def fake(rows, runs, _real=real):
            calls.append(1)
            r = _real(rows, runs)
            # call order: primary (c), sensitivity (d)(i), pending = 0 (d)(ii)
            return {**r, "verdict": {1: "FAIL", 2: "PASS", 3: "PASS"}[len(calls)]}

        with mock.patch.object(vb, "book_summary", fake):
            rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(calls), 3)
        self.assertTrue(rep["lp_pricing"]["sensitivity_final_map_v0"]["differs_from_primary"])
        self.assertTrue(any("sensitivity (d)(i)" in b and "PASS" in b and "FAIL" in b for b in rep["live_blockers"]))
        self.assertIn(rep["b_verdict"], ("PASS", "FAIL"))  # (B)'s verdict is the primary's, not the sensitivity's
        self.assertEqual(rep["lp_pricing"]["pending_zero_report_only"]["verdict"], "PASS")
        self.assertFalse(any("pending" in b for b in rep["live_blockers"]))
        self.assertTrue([m for m in rep["lp_pricing"]["vmaps"] if m["kind"] == "pending0_report_only"])
        rep_md = (out.parent / "vb" / "vbook_report.md").read_text()
        self.assertIn("pending = 0 (report only)", rep_md)

    def test_pending_zero_does_not_change_the_primary(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, {"pool-mC": [dep(4070)]})
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        # same primary with the pending-0 passes removed from the report: the (c) maps are the same either way
        prim = [m for m in rep["lp_pricing"]["vmaps"] if m["kind"] == "primary_c"]
        self.assertEqual(len(prim), 1)
        self.assertEqual(rep["lp_pricing"]["pending_zero_report_only"]["n_entered"], rep["b_mcap_mode_v"]["n_entered"])

    def test_pool_ids_are_not_printed(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, {"pool-mC": [dep(4070)]}, unresolved={"pool-mC": "logs_truncated"})
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("pool-", err)
        self.assertIn("pool-mC", json.dumps(rep["null_v"]))  # ids are in the report file only


class TestLphistEntered(LpBase):
    def final_with_map(self):
        walk, art, out = self.final()
        p = self.prep(out)
        (out.parent / vb.LPHIST_RUNS_NAME).unlink()  # prep wrote a canned line; this class makes its own
        return walk, art, out, p

    def fake_rpc(self):
        pools_seen = []

        def rpc(method, params):
            if method == "getMultipleAccounts":
                pools_seen.extend(params[0])
                return {"context": {"slot": 5}, "value": [__import__("tools.test_pumpswap_lp_history", fromlist=["x"]).pool_account(pk(9), 1) for _ in params[0]]}
            if method == "getSignaturesForAddress":
                return []
            raise AssertionError(method)

        return rpc, pools_seen

    def do_run(self, walk, art, out, p, name="lph.json", rpc=None):
        return vb.run_lphist_entered(walk, out, out.parent / "ledger.jsonl", p["vpath"], p["sha"], out.parent / name, art, tvb.FREEZE_COMMIT, None, True, rpc=rpc, sleep=lambda s: None)

    def test_collects_the_pools_of_the_entered_set_and_logs_the_run(self) -> None:
        walk, art, out, p = self.final_with_map()
        rpc, seen = self.fake_rpc()
        with tfw.patched():
            line = self.do_run(walk, art, out, p, rpc=rpc)
        entered = {r["mint"] for r in fw.read_rows(out / "rows.jsonl") if r["entered"]}
        want = sorted(f"pool-{m}" for m in entered)
        self.assertEqual(sorted(set(seen)), want)
        f = out.parent / "lph.json"
        meta = json.loads((out.parent / "lph.json.meta.json").read_text())
        self.assertEqual(meta["sha256"], fw._sha256_file(f))
        self.assertEqual(meta["t_to_unix"], T1)
        cc = int(fw.parse_clock(vb.find_final_marker(out, out.parent / "ledger.jsonl", True)["clean_clock"]).timestamp())
        self.assertEqual(meta["t_from_unix"], cc - 3600)
        self.assertEqual(json.loads((out.parent / "lph.json.pools.json").read_text()), want)
        self.assertEqual(sorted(json.loads(f.read_text())), want)
        self.assertFalse(f.stat().st_mode & 0o222)  # read-only
        lines = [json.loads(x) for x in (out.parent / vb.LPHIST_RUNS_NAME).read_text().splitlines()]
        self.assertEqual(len(lines), 1)
        for k, v in (("sha256", meta["sha256"]), ("n_pools", len(want)), ("n_unresolved", 0), ("state", "COMPLETED"), ("clean_clock", line["clean_clock"])):
            self.assertEqual(lines[0][k], v)
        self.assertIn("utc_time", lines[0])
        self.assertIn("attempts", lines[0])
        self.assertEqual(vb.first_lphist_run(out.parent / vb.LPHIST_RUNS_NAME, line["clean_clock"], line["read_end"], True)["sha256"], meta["sha256"])
        self.assertEqual(self.ledger_lines(out), [])  # no V-priced pass, so no vbook claim

    def test_a_second_run_appends_and_the_first_stays_the_one_vbook_accepts(self) -> None:
        walk, art, out, p = self.final_with_map()
        rpc, _ = self.fake_rpc()
        with tfw.patched():
            l1 = self.do_run(walk, art, out, p, "lph_a.json", rpc)
            l2 = self.do_run(walk, art, out, p, "lph_b.json", rpc)
        lines = [json.loads(x) for x in (out.parent / vb.LPHIST_RUNS_NAME).read_text().splitlines()]
        self.assertEqual([x["sha256"] for x in lines], [l1["sha256"], l2["sha256"]])
        first = vb.first_lphist_run(out.parent / vb.LPHIST_RUNS_NAME, l1["clean_clock"], l1["read_end"], True)
        self.assertEqual(first["sha256"], l1["sha256"])

    def test_refuses_without_the_final_marker_and_an_existing_out(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        with self.assertRaises(fw.Refused):
            vb.run_lphist_entered(walk, out, out.parent / "ledger.jsonl", out.parent / "x.json", "0" * 64, out.parent / "o.json", art, tvb.FREEZE_COMMIT, None, True, rpc=lambda *a: None)

    def test_the_cli_prints_no_pool_id_and_no_url(self) -> None:
        walk, art, out, p = self.final_with_map()
        rpc, _ = self.fake_rpc()
        err = io.StringIO()
        argv = ["lphist-entered", "--walk-dir", str(walk), "--final-out-dir", str(out), "--final-ledger", str(out.parent / "ledger.jsonl"), "--vmap", str(p["vpath"]), "--vmap-sha256", p["sha"], "--out", str(out.parent / "cli.json"), "--artifact-dir", str(art), "--freeze-commit", tvb.FREEZE_COMMIT, "--test-window"]
        with tfw.patched(), mock.patch("sys.stderr", err), mock.patch("tools.pumpswap_simulate.Rpc", lambda url: rpc), mock.patch("tools.pumpswap_virtual._rpc_url", lambda: "https://x/?api-key=SECRET"):
            rc = vb.main(argv)
        self.assertEqual(rc, 0, err.getvalue())
        self.assertNotIn("pool-", err.getvalue())
        self.assertNotIn("SECRET", err.getvalue())

    def test_a_failing_rpc_prints_only_the_type(self) -> None:
        walk, art, out, p = self.final_with_map()

        def rpc(method, params):
            raise SystemExit("boom https://x/?api-key=SECRET")

        err = io.StringIO()
        argv = ["lphist-entered", "--walk-dir", str(walk), "--final-out-dir", str(out), "--final-ledger", str(out.parent / "ledger.jsonl"), "--vmap", str(p["vpath"]), "--vmap-sha256", p["sha"], "--out", str(out.parent / "cli2.json"), "--artifact-dir", str(art), "--freeze-commit", tvb.FREEZE_COMMIT, "--test-window"]
        with tfw.patched(), mock.patch("sys.stderr", err), mock.patch("tools.pumpswap_simulate.Rpc", lambda url: rpc), mock.patch("tools.pumpswap_virtual._rpc_url", lambda: "https://x/?api-key=SECRET"):
            rc = vb.main(argv)
        self.assertEqual(rc, 0)  # fetch_lp_history records every failure as an unresolved pool, never the message
        self.assertNotIn("SECRET", err.getvalue())
        meta = json.loads((out.parent / "cli2.json.meta.json").read_text())
        self.assertGreater(meta["n_unresolved"], 0)
        self.assertNotIn("SECRET", (out.parent / "cli2.json").read_text())
