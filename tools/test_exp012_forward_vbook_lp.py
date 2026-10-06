"""Tests for the LP-law part of tools/exp012_forward_vbook.py (DEC-016 Amendment 5 section 7(a), (c), (d)).

Synthetic fixtures only (tools.exp012_fixtures through test_exp012_forward_vbook's walk). Nothing opens /data. The fixture
trades: the entered mints are mB2 (3050/3060), mC (4050/4060), mD (5050/5050) and mE (6050/6060) as entry/exit fill slots; mA and mB are not entered.

Run: PYTHONPATH=$PWD python -m pytest -q tools/test_exp012_forward_vbook_lp.py
"""

from __future__ import annotations

import argparse
import io
import json
import os
import time
from contextlib import redirect_stderr, redirect_stdout
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


def info_of(key, cands, **kw):
    return {key: {"changed": set(cands), "cands": {q: list(v) for q, v in cands.items()}, "bad": {}, "hold": True, "same_slot": False, **kw}}


def tmpdir() -> Path:
    return Path(__import__("tempfile").mkdtemp())


def test_min_is_taken_separately_per_leg_and_only_for_affected_trades() -> None:
    base = [row("a", 5.0, 5.0), row("b", 7.0, 7.0)]
    passes = {"c0-0": [row("a", 1.0, 9.0), row("b", 0.0, 0.0)], "c0-1": [row("a", 4.0, 2.0), row("b", 0.0, 0.0)]}
    info = {**info_of(("a", 1), {"p": [10, 20]}), ("b", 1): {"changed": set(), "cands": {}, "bad": {}, "hold": False, "same_slot": False}}
    d = tmpdir()
    out, rep = vb.lp_min_rows(base, ({}, info), {"p": 15}, lambda path, tag: passes[tag], d, "c")
    assert rep["n_passes"] == 2 and [m["tag"] for m in rep["maps"]] == ["c-0-0", "c-0-1"]
    a, b = out
    assert (a["flat"], a["press"]) == (1.0, 2.0)  # the lower of each leg, from different passes
    assert (a["flat_sol"], a["press_sol"]) == (1e-9, 2e-9)
    assert b["flat"] == 7.0  # not affected: untouched
    assert [load_map(d / m["file"])["p"] for m in rep["maps"]] == [10, 20]
    assert all(m["sha256"] == fw._sha256_file(d / m["file"]) for m in rep["maps"])


def test_full_product_over_a_trades_changed_pools_finds_the_cross_combination() -> None:
    base = [row("a", 9.0, 9.0)]
    table = {(10, 30): 5.0, (10, 40): 3.0, (20, 30): 4.0, (20, 40): 8.0}  # the minimum 3.0 is the cross (10, 40): neither pool's own extreme path finds it

    def run_pass(path, tag):
        m = load_map(path)
        return [row("a", table[(m["p"], m["q"])], table[(m["p"], m["q"])] + 1)]

    d = tmpdir()
    out, rep = vb.lp_min_rows(base, ({}, info_of(("a", 1), {"p": [10, 20], "q": [30, 40]})), {"p": 15, "q": 35}, run_pass, d, "c")
    assert rep["n_passes"] == 4  # 2 x 2 products
    assert (out[0]["flat"], out[0]["press"]) == (3.0, 4.0)
    assert not rep["shifted"]


def test_trades_sharing_a_pool_with_the_same_candidates_both_get_candidate_rows() -> None:
    info = {**info_of(("m1", 1), {"p": [50, 60]}), **info_of(("m2", 2), {"p": [50, 60]})}
    base = [row("m1", 100.0, 100.0), {**row("m2", 100.0, 100.0), "mig_ms": 2}]  # priced at the final-map value 70

    def run_pass(path, tag):
        v = -1000.0 if load_map(path)["p"] == 50 else 100.0
        return [row("m1", v, v), {**row("m2", v, v), "mig_ms": 2}]

    out, rep = vb.lp_min_rows(base, ({}, info), {"p": 70}, run_pass, tmpdir(), "c")
    assert rep["n_groups"] == 2
    assert [(r["flat"], r["press"]) for r in out] == [(-1000.0, -1000.0), (-1000.0, -1000.0)]  # the second group's repeated maps reuse the cached rows
    assert not rep["shifted"]


def test_a_trade_with_missing_candidate_rows_is_null_v_not_the_base_row() -> None:
    info = info_of(("m1", 1), {"p": [50, 60]})
    out, rep = vb.lp_min_rows([row("m1", 100.0, 100.0)], ({}, info), {"p": 70}, lambda path, tag: [], tmpdir(), "c")
    assert rep["shifted"] == {("m1", 1): {"candidate_rows_missing"}}
    assert vb.lp_counts(info, rep["shifted"])["n_entered_candidate_rows_missing"] == 1
    assert vb.with_lp_bad([{**out[0], "no_v_pools": []}], info, rep["shifted"])[0]["no_v_pools"] == ["p"]


def test_over_the_combo_cap_the_trade_is_null_v_and_no_pass_is_run() -> None:
    cands = {f"p{i}": [1, 2] for i in range(7)}  # 128 > 64
    calls = []
    d = tmpdir()
    out, rep = vb.lp_min_rows([row("a", 5.0, 5.0)], ({}, info_of(("a", 1), cands)), {k: 0 for k in cands}, lambda path, tag: calls.append(tag) or [], d, "c")
    assert calls == [] and rep["shifted"] == {("a", 1): {"too_many_candidate_combinations"}}
    assert out[0]["flat"] == 5.0
    rows = [{**out[0], "no_v_pools": []}]
    assert vb.with_lp_bad(rows, info_of(("a", 1), cands), rep["shifted"])[0]["no_v_pools"] == sorted(cands)
    assert vb.lp_counts(info_of(("a", 1), cands), rep["shifted"])["n_entered_over_combo_cap"] == 1


def test_candidates_are_per_trade_and_trades_sharing_a_pool_get_separate_groups() -> None:
    base = [row("a", 9.0, 9.0), {**row("b", 9.0, 9.0), "mig_ms": 2}]
    info = {**info_of(("a", 1), {"p": [10, 20]}), **info_of(("b", 2), {"p": [50]})}  # b never sees a's 10 / 20, a never sees b's 50
    seen = []

    def run_pass(path, tag):
        v = load_map(path)["p"]
        seen.append(v)
        return [row("a", float(v), float(v)), {**row("b", float(v), float(v)), "mig_ms": 2}]

    d = tmpdir()
    out, rep = vb.lp_min_rows(base, ({}, info), {"p": 15}, run_pass, d, "c")
    assert rep["n_groups"] == 2
    assert sorted(seen) == [10, 20, 50]
    assert out[0]["flat"] == 10.0 and out[1]["flat"] == 50.0  # a: min over {10, 20}; b: only 50


def test_entered_set_change_in_a_candidate_pass_nulls_the_trade_adds_nothing_and_never_raises() -> None:
    base = [row("a", 5.0, 5.0), {**row("x", 5.0, 5.0), "mig_ms": 2, "entered": False}]

    def run_pass(path, tag):
        return [{**row("a", 1.0, 1.0), "entered": False}, {**row("x", 0.0, 0.0), "mig_ms": 2, "entered": True}]  # drops a, would add x

    d = tmpdir()
    info = info_of(("a", 1), {"p": [10]})
    out, rep = vb.lp_min_rows(base, ({}, info), {"p": 15}, run_pass, d, "c")
    assert rep["shifted"] == {("a", 1): {"entry_dropped_in_candidate_pass"}}
    assert rep["n_would_add"] == 1
    assert [r["mint"] for r in out if r["entered"]] == ["a"]  # x is not added: the entered set is (A)'s
    c = vb.lp_counts(info, rep["shifted"], rep["n_would_add"])
    assert (c["n_entered_dropped_in_candidate_pass"], c["n_mints_candidate_pass_would_add_not_added"]) == (1, 1)


def test_a_slot_move_in_a_candidate_pass_is_counted() -> None:
    info = info_of(("a", 1), {"p": [10]})
    out, rep = vb.lp_min_rows([row("a", 5.0, 5.0)], ({}, info), {"p": 15}, lambda path, tag: [row("a", 1.0, 1.0, x=9)], tmpdir(), "c")
    assert vb.lp_counts(info, rep["shifted"])["n_entered_slot_moved_in_candidate_pass"] == 1


def test_short_account_anchor_is_its_stored_v_with_zero_pending() -> None:
    fdoc = {"fetch_started_utc": "2026-10-16T01:00:00Z", "fetch_ended_utc": "2026-10-16T01:10:00Z"}
    anchors, _ = vb.load_anchors(fdoc, {"p": {"pending": None, "v_base": None}, "q": {"pending": 5}}, {"p": 777, "q": 888}, None, [])
    assert anchors["p"][:2] == (777, 0)
    assert anchors["q"] == "no_v_base"  # pending is an int and v_base is missing: unknown, never the stored V


def test_slots_recorded_in_the_fetch_are_used_for_the_anchor_span() -> None:
    sp = vm.Span((T0, T1))
    sp.slot_min, sp.slot_max = 4060, 4080  # an event at slot 4070 is inside the final fetch's slots whatever its block time
    evs = chain_events([dep(4070)])
    v0_read = lph.replay_forward(X0, evs)
    assert vb.pool_values(4050, 4060, v0_read, PEND, evs, sp)[:2] == ("unresolved", "ambiguous_anchor_placement")
    sp.slot_min, sp.slot_max = 4071, 4080  # the event is before the fetch: definite
    assert vb.pool_values(4050, 4060, v0_read, PEND, evs, sp)[0] == "ok"


def test_a_slot_shift_under_the_candidate_map_marks_the_trade() -> None:
    base = [row("a", 5.0, 5.0)]
    info = info_of(("a", 1), {"p": [10]})
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
    def setUp(self) -> None:
        super().setUp()
        for target in (mock.patch.object(vm, "CUTOFF", "2026-10-01T00:00:00Z"), mock.patch.object(vb, "CUTOFF", "2026-10-01T00:00:00Z"), mock.patch.object(time, "sleep")):
            target.start()
            self.addCleanup(target.stop)

    def prep(self, out: Path, events: dict[str, list[dict]] | None = None, unresolved: dict[str, str] | None = None, meta_edit=None, unexplained: tuple = (), merge_unresolved: dict | None = None, strip_slots: bool = False, **ent_extra) -> dict:
        """A REAL merge: `fetch` (snapshot), `snapshot`, `fetch --new` (the FINAL fetch map) and `merge` through exp012_forward_vmap, so the
        V map vbook gets is a real merge OUT (no .detail.json / .fetch.json beside it). Then an lphist file with meta and its ledger line."""
        events = events or {}
        d = out.parent
        v0s = {pool: lph.replay_forward(X0, chain_events(events.get(pool, []))) for pool in POOLS}
        (d / "pools.json").write_text(json.dumps(sorted(POOLS)))

        def fetch_at(slot):
            return lambda chunk: [(v0s[q] - PEND, None, {"pending": PEND, "v_base": v0s[q], "lp_supply": S, "slot": slot}) for q in chunk]

        def do_fetch(path: Path, new: bool, slot: int) -> None:
            ns = argparse.Namespace(pools=str(d / "pools.json"), vmap=str(path), rps=5.0, new=new)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(vm.cmd_fetch(ns, fetch=fetch_at(slot)), 0)

        do_fetch(d / "snapsrc.json", False, 8000)
        with redirect_stdout(io.StringIO()):
            vm.cmd_snapshot(argparse.Namespace(vmap=str(d / "snapsrc.json"), out=str(d / "snaps")))
        snap = [x for x in (d / "snaps").glob("vmap-snapshot-*.json") if ".json." not in x.name][0]
        ffm = d / "ffm.json"
        do_fetch(ffm, True, 9005)  # the FINAL fetch map (fetch --new)
        if strip_slots:  # a final fetch record with no slots (before the merge, so the meta binds the stripped file)
            fj0 = vm.side(ffm, ".fetch.json")
            fdoc0 = json.loads(fj0.read_text())
            fj0.write_text(json.dumps({k: v for k, v in fdoc0.items() if not k.startswith("fetch_slot_")}))
        out_map = d / "pool_v.json"
        ns = argparse.Namespace(final=str(ffm), pools=str(d / "pools.json"), snapshot=[str(snap)], snapshot_fetch=[], dry_run=False, lphist=[], out=str(out_map))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(vm.cmd_merge(ns), 0)
        mp = Path(str(out_map) + ".merge.json")
        doc = json.loads(mp.read_text())
        for key, content in (("unexplained", sorted(unexplained)), ("unresolved", merge_unresolved or {})):
            if content:
                f = d / doc["lp_moves"][f"{key}_file"]
                os.chmod(f, 0o644)
                f.write_text(json.dumps(content))
                doc["lp_moves"][f"{key}_sha256"] = fw._sha256_file(f)
        if meta_edit:
            meta_edit(doc)
        os.chmod(mp, 0o644)
        mp.write_text(json.dumps(doc))
        self.meta_sha = fw._sha256_file(mp)
        marker = vb.find_final_marker(out, d / "ledger.jsonl", True)
        t_from = int(fw.parse_clock(marker["clean_clock"]).timestamp()) - 3600
        self.span = vm.span_of(json.loads(vm.side(ffm, ".fetch.json").read_text()))
        self.t_to = self.span[1]
        hist = {}
        for pool in POOLS:
            if pool in (unresolved or {}):
                hist[pool] = {"lp_mint": pk(9), "events": [], "resolved": False, "reason": unresolved[pool], "lp_supply": None, "attempts": 1}
            else:
                hist[pool] = {"lp_mint": pk(9), "events": chain_events(events.get(pool, [])), "resolved": True, "reason": None, "lp_supply": 1, "supply_slot": 10000, "attempts": 1, **ent_extra}
        lh = self.write_lphist(out, hist, t_from, name="lph1.json")
        return {"vpath": out_map, "sha": fw._sha256_file(out_map), "lphist": lh, "t_from": t_from, "meta": mp, "ffm": ffm, "snap": snap}

    def write_lphist(self, out: Path, hist: dict, t_from: int, name: str, ledger: bool = True, t_to: int | None = None, meta_sha: str | None = None) -> Path:
        d = out.parent
        f = d / name
        f.write_text(json.dumps(hist, sort_keys=True) + "\n")
        sha = fw._sha256_file(f)
        msha = meta_sha or self.meta_sha
        mp = f.with_name(f.name + ".meta.json")
        mp.write_text(json.dumps({"sha256": sha, "n_pools": len(hist), "n_unresolved": sum(1 for e in hist.values() if not e["resolved"]), "t_from_unix": t_from, "t_to_unix": self.t_to if t_to is None else t_to, "merge_meta_sha256": msha, "attempts": []}) + "\n")
        if ledger:
            marker = vb.find_final_marker(out, d / "ledger.jsonl", True)
            fw.ledger_append(d / vb.LPHIST_RUNS_NAME, {"state": "COMPLETED", "clean_clock": marker["clean_clock"], "read_end": marker["read_end"], "test_window": True, "sha256": sha, "merge_meta_sha256": msha, "utc_time": "2026-10-16T01:30:00Z", "n_pools": len(hist), "n_unresolved": 0, "attempts": []})
        return f

    def go(self, walk, art, out, p: dict, lphist: Path | None = None, vb_out: Path | None = None):
        extra = ["--lphist", str(lphist or p["lphist"]), "--vmap-merge-meta", str(p["meta"]), "--final-fetch-map", str(p["ffm"]), "--snapshot", str(p["snap"])]
        rc, err = self.run_vbook(walk, art, out, p["vpath"], p["sha"], *extra, vb_out=vb_out)
        rep = None
        f = (vb_out or out.parent / "vb") / "vbook_report.json"
        if f.exists():
            rep = json.loads(f.read_text())
        return rc, err, rep

    def direct(self, walk, art, out, p: dict, score_fn=vb.score_hours_v):
        """run_vbook called directly (a custom score_fn); returns (report or None, Refused or None)."""
        try:
            with tfw.patched():
                rep = vb.run_vbook(walk, out, out.parent / "ledger.jsonl", p["vpath"], p["sha"], out.parent / "vb", art, tvb.FREEZE_COMMIT, None, True, score_fn=score_fn, vmap_merge_meta=p["meta"], lphist=p["lphist"], final_fetch_map=p["ffm"], snapshots=[p["snap"]])
            return rep, None
        except fw.Refused as exc:
            return None, exc

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
        p = self.prep(out, {"pool-mC": [dep(9005)]})  # inside the final fetch's slots
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
            p = self.prep(out, **kw)
            rc, err, rep = self.go(walk, art, out, p)
            self.assertEqual(rc, 0, err)
            self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)
            self.assertEqual(rep["lp_pricing"]["counts"][key], 1)
            self.assertEqual(rep["lp_pricing"]["lphist"]["merge_lp_moves"]["n_explained"], 0)

    def test_merge_meta_without_lp_moves_refuses_before_started(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, meta_edit=lambda doc: doc.pop("lp_moves"))
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 2)
        self.assertIn("no lp_moves", err)
        self.assertEqual(self.ledger_lines(out), [])

    def test_dry_run_merge_meta_is_refused(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, meta_edit=lambda doc: doc.update(dry_run=True))
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 2)
        self.assertIn("dry-run", err)
        self.assertEqual(self.ledger_lines(out), [])

    def test_missing_supply_slot_is_unresolved(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, supply_slot=None)
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)

    def test_supply_read_before_the_final_fetch_is_unresolved(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, supply_slot=5)  # fetch_slot_max is 9010
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
        fw.ledger_append(out.parent / vb.LPHIST_RUNS_NAME, {"state": "COMPLETED", "clean_clock": marker["clean_clock"], "read_end": marker["read_end"], "test_window": True, "sha256": fw._sha256_file(late), "merge_meta_sha256": self.meta_sha, "utc_time": "x"})
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
        return vb.run_lphist_entered(walk, out, out.parent / "ledger.jsonl", p["vpath"], p["sha"], out.parent / name, art, tvb.FREEZE_COMMIT, None, True, vmap_merge_meta=p["meta"], final_fetch_map=p["ffm"], rpc=rpc, sleep=lambda s: None)

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
        self.assertEqual(meta["t_to_unix"], self.t_to)
        self.assertEqual(meta["merge_meta_sha256"], fw._sha256_file(p["meta"]))
        cc = int(fw.parse_clock(vb.find_final_marker(out, out.parent / "ledger.jsonl", True)["clean_clock"]).timestamp())
        self.assertEqual(meta["t_from_unix"], cc - 3600)
        self.assertEqual(json.loads((out.parent / "lph.json.pools.json").read_text()), want)
        self.assertEqual(sorted(json.loads(f.read_text())), want)
        self.assertFalse(f.stat().st_mode & 0o222)  # read-only
        lines = [json.loads(x) for x in (out.parent / vb.LPHIST_RUNS_NAME).read_text().splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["merge_meta_sha256"], fw._sha256_file(p["meta"]))
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
            vb.run_lphist_entered(walk, out, out.parent / "ledger.jsonl", out.parent / "x.json", "0" * 64, out.parent / "o.json", art, tvb.FREEZE_COMMIT, None, True, vmap_merge_meta=out.parent / "m", final_fetch_map=out.parent / "f", rpc=lambda *a: None)

    def test_the_cli_prints_no_pool_id_and_no_url(self) -> None:
        walk, art, out, p = self.final_with_map()
        rpc, _ = self.fake_rpc()
        err = io.StringIO()
        argv = ["lphist-entered", "--walk-dir", str(walk), "--final-out-dir", str(out), "--final-ledger", str(out.parent / "ledger.jsonl"), "--vmap", str(p["vpath"]), "--vmap-sha256", p["sha"], "--vmap-merge-meta", str(p["meta"]), "--final-fetch-map", str(p["ffm"]), "--out", str(out.parent / "cli.json"), "--artifact-dir", str(art), "--freeze-commit", tvb.FREEZE_COMMIT, "--test-window"]
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
        argv = ["lphist-entered", "--walk-dir", str(walk), "--final-out-dir", str(out), "--final-ledger", str(out.parent / "ledger.jsonl"), "--vmap", str(p["vpath"]), "--vmap-sha256", p["sha"], "--vmap-merge-meta", str(p["meta"]), "--final-fetch-map", str(p["ffm"]), "--out", str(out.parent / "cli2.json"), "--artifact-dir", str(art), "--freeze-commit", tvb.FREEZE_COMMIT, "--test-window"]
        with tfw.patched(), mock.patch("sys.stderr", err), mock.patch("tools.pumpswap_simulate.Rpc", lambda url: rpc), mock.patch("tools.pumpswap_virtual._rpc_url", lambda: "https://x/?api-key=SECRET"):
            rc = vb.main(argv)
        self.assertEqual(rc, 0)  # fetch_lp_history records every failure as an unresolved pool, never the message
        self.assertNotIn("SECRET", err.getvalue())
        meta = json.loads((out.parent / "cli2.json.meta.json").read_text())
        self.assertGreater(meta["n_unresolved"], 0)
        self.assertNotIn("SECRET", (out.parent / "cli2.json").read_text())


class TestBindingAndCandidatePasses(LpBase):
    def test_vbook_runs_on_a_real_merge_out_that_has_no_sidecars(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out)
        self.assertFalse(vm.side(p["vpath"], ".detail.json").exists() or vm.side(p["vpath"], ".fetch.json").exists())
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        self.assertEqual(rep["lp_pricing"]["lphist"]["merge_meta_sha256"], fw._sha256_file(p["meta"]))
        self.assertEqual(rep["lp_pricing"]["lphist"]["final_fetch_slot_max"], 9005)

    def test_a_tampered_final_fetch_map_refuses_before_started(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out)
        fj = vm.side(p["ffm"], ".fetch.json")
        doc = json.loads(fj.read_text())
        doc["fetch_slot_max"] = 1  # would switch the supply-slot check off in effect
        fj.write_text(json.dumps(doc))
        rc, err, _ = self.go(walk, art, out, p)
        self.assertEqual(rc, 2)
        self.assertIn("sha256.fetch", err)
        self.assertEqual(self.ledger_lines(out), [])

    def test_vbook_needs_the_final_fetch_map_and_the_lphist_bound_to_this_merge_meta(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out)
        rc, err = self.run_vbook(walk, art, out, p["vpath"], p["sha"], "--lphist", str(p["lphist"]), "--vmap-merge-meta", str(p["meta"]))
        self.assertEqual(rc, 2)
        self.assertIn("--final-fetch-map", err)
        other = out.parent / "other.merge.json"
        other.write_text(p["meta"].read_text() + " ")  # same content bar one space: another file, another sha256
        rc, err = self.run_vbook(walk, art, out, p["vpath"], p["sha"], "--lphist", str(p["lphist"]), "--vmap-merge-meta", str(other), "--final-fetch-map", str(p["ffm"]))
        self.assertEqual(rc, 2)
        self.assertIn("merge_meta_sha256", err)
        self.assertEqual(self.ledger_lines(out), [])

    def test_lphist_ending_before_the_final_fetch_refuses(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out)
        (out.parent / vb.LPHIST_RUNS_NAME).unlink()
        short = self.write_lphist(out, json.loads(p["lphist"].read_text()), p["t_from"], "short.json", t_to=self.t_to - 1)
        rc, err, _ = self.go(walk, art, out, p, lphist=short)
        self.assertEqual(rc, 2)
        self.assertIn("before the final fetch's end", err)
        self.assertEqual(self.ledger_lines(out), [])

    def test_lphist_entered_refuses_a_dry_run_meta_and_a_wrong_fetch_map(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out)
        (out.parent / vb.LPHIST_RUNS_NAME).unlink()
        doc = json.loads(p["meta"].read_text())
        dry = out.parent / "x.merge.json"
        dry.write_text(json.dumps({**doc, "dry_run": True}))
        with self.assertRaises(fw.Refused) as cm, tfw.patched():
            vb.run_lphist_entered(walk, out, out.parent / "ledger.jsonl", p["vpath"], p["sha"], out.parent / "l.json", art, tvb.FREEZE_COMMIT, None, True, vmap_merge_meta=dry, final_fetch_map=p["ffm"], rpc=lambda *a: None)
        self.assertIn("dry-run", str(cm.exception))
        other = out.parent / "other_ffm.json"
        for suffix in (".fetch.json", ".detail.json"):
            other.with_name(other.name + suffix).write_text(vm.side(p["ffm"], suffix).read_text() + " ")
        with self.assertRaises(fw.Refused), tfw.patched():
            vb.run_lphist_entered(walk, out, out.parent / "ledger.jsonl", p["vpath"], p["sha"], out.parent / "l2.json", art, tvb.FREEZE_COMMIT, None, True, vmap_merge_meta=p["meta"], final_fetch_map=other, rpc=lambda *a: None)

    def test_a_candidate_pass_that_drops_an_entry_never_refuses_after_started(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, {"pool-mC": [dep(4070)]})
        real = vb.score_hours_v

        def fake(walk_, pool, art_, scratch, vmap, mode, counts, frozen):
            rows, thr = real(walk_, pool, art_, scratch, vmap, mode, counts, frozen)
            if not frozen and mode == "v" and Path(vmap).name.startswith("vmap-c"):  # a candidate map of the primary pricing
                rows = [dict(r) for r in rows]
                for r in rows:
                    if r["mint"] == "mC":
                        r["score"] = thr - 1.0  # (A) entered it, this pass would not
            return rows, thr

        rep, exc = self.direct(walk, art, out, p, score_fn=fake)
        self.assertIsNone(exc)
        self.assertEqual([x["state"] for x in self.ledger_lines(out)], ["STARTED", "DONE"])
        c = rep["lp_pricing"]["counts"]
        self.assertEqual(c["n_entered_dropped_in_candidate_pass"], 1)
        self.assertEqual(c["n_mints_candidate_pass_would_add_not_added"], 0)  # the add side is unit-tested (no in-window non-entered mint here)
        self.assertEqual(rep["b_mcap_mode_v"]["n_entered"], 4)  # the entered set is (A)'s
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)
        self.assertIn("pool-mC", rep["null_v"]["null_v_pool_ids"])

    def test_a_candidate_pass_that_moves_the_exit_slot_nulls_the_trade(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, {"pool-mC": [dep(4070)]})
        real = vb.score_hours_v

        def fake(walk_, pool, art_, scratch, vmap, mode, counts, frozen):
            rows, thr = real(walk_, pool, art_, scratch, vmap, mode, counts, frozen)
            if not frozen and mode == "v" and Path(vmap).name.startswith("vmap-c"):
                rows = [dict(r) for r in rows]
                for r in rows:
                    if r["mint"] == "mC":
                        r["exit_slot"] = (r["exit_slot"] or 0) + 1
            return rows, thr

        rep, exc = self.direct(walk, art, out, p, score_fn=fake)
        self.assertIsNone(exc)
        self.assertEqual(rep["lp_pricing"]["counts"]["n_entered_slot_moved_in_candidate_pass"], 1)
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)

    def test_sensitivity_uses_the_same_null_v_set_as_the_primary(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, unresolved={"pool-mC": "logs_truncated"})
        rc, err, rep = self.go(walk, art, out, p)
        self.assertEqual(rc, 0, err)
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)
        self.assertEqual(rep["lp_pricing"]["sensitivity_final_map_v0"]["verdict"], vb.NOT_DECIDABLE)  # only the pricing differs
        self.assertFalse(any("sensitivity" in b for b in rep["live_blockers"]))


class TestSlotMaxRefusal(LpBase):
    def test_a_final_fetch_without_fetch_slot_max_refuses_outside_a_test_window(self) -> None:
        walk, art, out = self.final()
        p = self.prep(out, strip_slots=True)
        marker = vb.find_final_marker(out, out.parent / "ledger.jsonl", True)
        ledger = out.parent / "REAL_LPHIST_RUNS.jsonl"
        line = json.loads((out.parent / vb.LPHIST_RUNS_NAME).read_text().splitlines()[0])
        fw.ledger_append(ledger, {**line, "test_window": False})
        meta = json.loads(p["meta"].read_text())
        args = (p["lphist"], ledger, {"clean_clock": marker["clean_clock"], "read_end": marker["read_end"]})
        with self.assertRaises(fw.Refused) as cm:
            vb.build_lp_context(*args, False, load_map(p["vpath"]), meta, p["meta"], [p["snap"]], p["ffm"])
        self.assertIn("fetch_slot_max", str(cm.exception))
