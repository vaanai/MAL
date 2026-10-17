"""Tests for tools/r4b_read.py on SYNTHETIC walker-layout hours in tmp dirs and stubs (boosters, decoder pins, fee-arm record). Nothing here opens /data/mal sealed data.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages:. /data/mal/venv/bin/python -m pytest tools/test_r4b_read.py

Scale: the module patches the pinned constants down (E = W0 + 4 h, N = A = 1, gate n 3, 1 date) so a whole read runs on 8 synthetic hours; the pure tests that need the real
constants (NOT_DECIDABLE N < 20 / A < 125, PASS / FAIL books) restore them with the `real` fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

duckdb = pytest.importorskip("duckdb")

from tools import exp012_forward as ef  # noqa: E402
from tools import r4b_book as bk  # noqa: E402
from tools import r4b_count as rc  # noqa: E402
from tools import r4b_features as rf  # noqa: E402
from tools import r4b_frozen as FZ  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools import r4b_read as rr  # noqa: E402
from tools.test_r4b_layout import S, complete, create, make_walk, migration, pda_of, trade  # noqa: E402

HOUR0 = datetime(2026, 10, 16, 3, tzinfo=timezone.utc)
REAL = {k: getattr(rr, k) for k in ("STOP_HOURS", "MIN_BLOCKS", "MIN_ATTEMPTS", "GATE_N", "GATE_DATES", "H0", "EARLIEST_W0")}
NH = 8


def hkey(i: int) -> str:
    return ef.hour_key(HOUR0 + timedelta(hours=i))


def s_of(i: int) -> int:
    return S + 7200 * i


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sph_ok(path):
    return 10000.0


@pytest.fixture(scope="module", autouse=True)
def small():
    mp = pytest.MonkeyPatch()
    mp.setattr(rr, "STOP_HOURS", 4)
    mp.setattr(rr, "MIN_BLOCKS", 1)
    mp.setattr(rr, "MIN_ATTEMPTS", 1)
    mp.setattr(rr, "GATE_N", 3)
    mp.setattr(rr, "GATE_DATES", 1)
    mp.setattr(rr, "H0", hkey(0))
    mp.setattr(rr, "EARLIEST_W0", hkey(0))
    mp.setattr(rc, "BLOCK_S", 4 * 3600)
    mp.setattr(rc, "MIN_LIVE_TOKENS", 2)
    mp.setattr(rc, "MIN_MARKS_WITH_LIVE", 3)
    yield mp
    mp.undo()


@pytest.fixture()
def real(monkeypatch):
    for k, v in REAL.items():
        monkeypatch.setattr(rr, k, v)


def stream(n_hours: int, per_hour: int = 3) -> dict:
    """per_hour tokens graduate at the start of each hour and print every 120 s until the end of the walk (sides alternate)."""
    hours = {hkey(i): {"trades": [], "creates": [], "migrations": []} for i in range(n_hours)}
    end_slot = s_of(n_hours)
    for i in range(n_hours):
        for c in "abc"[:per_hour]:
            m = f"M{i}{c}"
            ms = s_of(i) + 100
            hours[hkey(i)]["migrations"] += [complete(m, ms), migration(m, ms, pda_of(m))]
            hours[hkey(i)]["creates"].append(create(m, "C" + m, slot=s_of(i) - 500))
            j, sl = 0, ms + 10 + "abc".index(c)
            while sl < end_slot:
                h = (sl - S) // 7200
                hours[hkey(h)]["trades"].append(trade(m, sl, 1, 0, side="buy" if j % 2 == 0 else "sell", sol=1_000_000_000 * (1 + j % 3), tok=5_000_000 * (1 + j % 3)))
                j += 1
                sl += 240
    return hours


class FakeBooster:
    """score = -f_age_s: the youngest tokens rank first (stub of the five boosters; no lightgbm)."""

    def predict(self, X):
        return -np.asarray(X)[:, rf.FEATS.index("age_s")]


def code_shas() -> dict:
    d = Path(rr.__file__).resolve().parent
    return {m: sha(d / f"{m}.py") for m in rr.CODE_MODULES}


@pytest.fixture(scope="module")
def world(small, tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    walk = make_walk(root / "walk", stream(NH))
    ccfg = rc.Config(walk, root / "counts", hkey(1), n_blocks=1, a_attempts=1, h0=hkey(0))
    rc.run(ccfg, sph_fn=sph_ok, canonical_fn=pda_of)
    rec = root / "rec"
    rec.mkdir()
    (rec / "e1.json").write_text(json.dumps({"n": 25, "stake_lamports": 50_000_000, "pnl_gap_lamports_live_minus_sim_mean": -100000.0}))
    (rec / "sell.py").write_text("# stand-in for the pinned sell builder\n")
    (rec / "a3.json").write_text(json.dumps({"schema": "r4b_a3_v1", "checked_from": "2026-10-16T00:00:00Z", "checked_through": "2026-10-16T23:00:00Z", "halts": [{"at": "2026-10-16T01:00:00Z", "reason": "before W0"}]}))
    (rec / "view.txt").write_text("input view\n")
    mints = {f"M{i}{c}": {"fetch_ok": True, "owner_program": rr.SPL_TOKEN_PROGRAM, "freeze_authority": None, "token2022_extensions": []} for i in range(NH) for c in "abc"}
    mints["M1a"] = {"fetch_ok": True, "owner_program": rr.SPL_TOKEN_PROGRAM, "freeze_authority": "FRZ"}  # strand-class
    mints["M1b"] = {"fetch_ok": False}  # a failed fetch is strand-class too
    (rec / "mints.json").write_text(json.dumps({"fetch_slot": 1, "mints": mints}))
    tries = rec / "tries.jsonl"
    tries.write_text(json.dumps({"config": {"key": "r4b_exp027_family_x"}}) + "\n" + json.dumps({"config": {"key": "r4b_exp027_family"}, "tool": "x"}) + "\n")
    return {"root": root, "walk": walk, "counts": root / "counts", "rec": rec, "tries": tries, "ccfg": ccfg}


def exp_text(world, **over) -> str:
    rec = world["rec"]
    v = {
        "EXP027_BOOK_ID": "r4-b", "EXP027_FAMILY": "DEC-030 slot 5, push-family book 1", "EXP027_ROUTE": "R1", "EXP027_COUNT_START": "W0 = first full UTC hour after merge",
        "EXP027_LAST_W0": "2026-10-27T00", "EXP027_STOP_RULE": f"calendar stop E = W0 + {rr.STOP_HOURS} h; the read requires at least {rr.MIN_BLOCKS} blocks and at least {rr.MIN_ATTEMPTS} attempts",
        "EXP027_EXIT_HORIZON_H": "1", "EXP027_CAP_END": "cap = E", "EXP027_DEADLINE": "E + 48 h", "EXP027_ALPHA": "0.025/k, Bonferroni", "EXP027_DECISION_P": "largest p of the 4 cells",
        "EXP027_SEND_FEE_LAMPORTS": "505000, or 160000 only under section 5.3", "EXP027_RENT_DECIDING": "0 per filled trip under section 5.2",
        "EXP027_W0": hkey(1), "EXP027_K": "1", "EXP027_DECODER_REF": "abc1234", "EXP027_TIERS_SHA256": rr.fee_tiers_sha256(),
        "EXP027_E1_RECORD": f"{rec / 'e1.json'}@{sha(rec / 'e1.json')}", "EXP027_INPUT_VIEWS": f"{rec / 'view.txt'}@{sha(rec / 'view.txt')}",
        "EXP027_TRIES_ENTRY": "r4b_exp027_family", "EXP027_MINT_ACCOUNTS": f"{rec / 'mints.json'}@{sha(rec / 'mints.json')}", "EXP027_FEE_ARM": "NONE",
        "EXP027_E1_MODE": rr.E1_MODE, "EXP027_WALK_DIR": str(Path(world["walk"]).resolve()), "EXP027_SELL_BUILDER": f"{rec / 'sell.py'}@{sha(rec / 'sell.py')}", "EXP027_A3_RECORD": f"{rec / 'a3.json'}@{sha(rec / 'a3.json')}",
        "EXP027_E_A_MD5": "3e9bc6225377927682d80b9441391a96", "EXP027_E_B_MD5": "0" * 32, "EXP027_E_C_MD5": "54052f798013a71e3a9f40e0d6830ac1", "EXP027_E_D_MD5": "1" * 32,
    }
    for m, h in code_shas().items():
        v[f"EXP027_SHA256_{m.upper()}"] = h
    v.update(over)
    return "# EXP-027 (test fixture)\n\n```\n" + "".join(f"{k}: {x}\n" for k, x in v.items() if x is not None) + "```\n"


def mk(tmp_path, world, *, counts=None, exp_extra="", **over):
    exp = tmp_path / "EXP-027.md"
    exp.write_text(exp_text(world, **over) + exp_extra)
    return rr.Paths(world["walk"], tmp_path / "out", counts or world["counts"], exp, world["tries"], None)


def mkdeps(world, **kw):
    log: list[str] = []
    d = dict(now_fn=lambda: HOUR0 + timedelta(hours=1 + 4 + 24), clean_fn=lambda p: [], sync_fn=lambda: [], window_fn=lambda e, l, st: ([], {"reg_commit": "r" * 40, "exp_sha256": "e" * 64, "prefix_sha256": "p" * 64, "appended": []}), decoder_fn=lambda ref: [], sph_fn=sph_ok, canonical_fn=pda_of,
             boosters_fn=lambda md: [FakeBooster()], models_check_fn=lambda md: [], head_fn=lambda: "deadbeef", stderr=log.append, memory_limit="2GB", threads=2,
             walk_pin=str(Path(world["walk"]).resolve()))
    d.update(kw)
    dep = rr.Deps(**d)
    dep.log = log  # type: ignore[attr-defined]
    return dep


def refused(paths, deps, *needles):
    with pytest.raises(rr.ReadRefused) as ei:
        rr.read(paths, deps)
    msg = " | ".join(ei.value.reasons)
    for n in needles:
        assert n in msg, (n, msg)
    assert not (paths.out_dir / rr.READS).exists() and not (paths.out_dir / rr.LOCK).exists() and not (paths.out_dir / rr.RESULT).exists()
    return ei.value.reasons


def main_events(out_dir):
    """The read's own ledger lines (started / completed / aborted); report-only lines come after `completed` and are checked separately."""
    return [e for e in rr.reads_events(out_dir) if not e["event"].startswith("report_")]


def report(out_dir, step):
    return json.loads((out_dir / ("report-" + step.replace(".", "_") + ".json")).read_text())


def write_counts(cdir: Path, lines) -> None:
    (cdir / rc.COUNTS).write_text("".join(rc.stable_json(x) + "\n" for x in lines))


# ---- the happy path and its order ---------------------------------------------------------------------------------------------------------------
def test_read_end_to_end_and_order(tmp_path, world):
    paths = mk(tmp_path, world)
    seen = {}
    deps = mkdeps(world)
    deps.stderr = lambda s: seen.update(text=s, result_exists_at_verdict=(paths.out_dir / rr.RESULT).exists(), ledger=[e["event"] for e in rr.reads_events(paths.out_dir)])
    res = rr.read(paths, deps)
    assert seen["result_exists_at_verdict"] is False and seen["ledger"] == ["started"]  # verdict BEFORE any result file, after `started`
    assert seen["text"].startswith("VERDICT: ")
    ev = rr.reads_events(paths.out_dir)
    assert [e["event"] for e in ev[:2]] == ["started", "completed"] and ev[0]["token"] == ev[1]["token"] == res["token"]
    assert all(e["event"] == "report_completed" for e in ev[2:]) and [e["step"] for e in ev[2:]] == [n for n, _ in rr.REPORT_STEP_NAMES]
    assert (paths.out_dir / rr.RESULT).exists() and ev[1]["result_sha256"] == sha(paths.out_dir / rr.RESULT)
    assert not (paths.out_dir / "scratch").exists()
    assert res["A_attempts"] > 0 and res["N_blocks_with_attempts"] == 1
    assert set(res["cells"]) == {"flat_1.3", "flat_1.9", "press_1.3", "press_1.9"} and all(c["n"] == res["A_attempts"] for c in res["cells"].values())
    assert res["keys"]["ok"] is True and res["keys"]["not_subset_marks"] == 0
    assert res["stack"]["send"] == 505_000 and res["stack"]["rent"] == 0 and res["fee_arm"]["source"] == "505k"
    assert not any(k in c for c in res["cells"].values() for k in rr.BLOCK_SUM_KEYS)  # block sums are report-only, in their own file
    out = paths.out_dir
    assert set(report(out, "block_sums")["data"]) == set(res["cells"]) and report(out, "lp_discontinuities")["data"]["pairs"] > 0
    assert set(report(out, "rent")["data"]) == {"flat_1.3", "flat_1.9", "press_1.3", "press_1.9"} and set(report(out, "send_55k")["data"]) == set(report(out, "no_auto_stranded")["data"])
    assert set(report(out, "lat4.0")["data"]) == {"flat_4.0", "press_4.0"}
    idx = json.loads((out / "report-only.json").read_text())["report_only"]
    assert set(idx) == {n for n, _ in rr.REPORT_STEP_NAMES} and all(v["status"] == "ok" for v in idx.values())
    assert res["d3"]["dropped_rows"] >= 0 and "not money made" in res["claim"]
    assert res["strand_class_trades"] == 2 and res["stranded_counts"] == {"flat_1.3": 2, "flat_1.9": 2}  # M1a (freeze authority) and M1b (failed fetch) were bought
    assert all(-30.0 < c["mean_pct"] < -10.0 for c in res["cells"].values())  # 2 of 12 trips lose the stake; the other 10 lose only fees
    # a negative synthetic book (constant reserves: only fees) is a FAIL, not a PASS
    assert res["verdict"] == "FAIL" and all(c["mean_sol"] < 0 for c in res["cells"].values())
    # second run refuses and writes nothing
    before = (paths.out_dir / rr.READS).read_text()
    with pytest.raises(rr.ReadRefused, match="no second run"):
        rr.read(paths, mkdeps(world))
    assert (paths.out_dir / rr.READS).read_text() == before


def test_mints_missing_from_the_fetch_file_are_strand_class(tmp_path, world):
    empty = tmp_path / "mints_empty.json"
    empty.write_text(json.dumps({"fetch_slot": 1, "mints": {}}))
    res = rr.read(mk(tmp_path, world, EXP027_MINT_ACCOUNTS=f"{empty}@{sha(empty)}"), mkdeps(world))
    A = res["A_attempts"]
    assert res["strand_class_trades"] == A and res["stranded_counts"] == {"flat_1.3": A, "flat_1.9": A}
    assert all(c["mean_pct"] < -70 for c in res["cells"].values())  # stake + one send + rent lost on every stranded trip (fail legs keep 71-85 % of it)


def test_forward_features_use_asof_filters_and_asof_grads(tmp_path, world, monkeypatch):
    calls = []
    orig = rf.mint_features

    def spy(*a, **k):
        calls.append((k.get("grads_rule", "frozen"), k.get("mint_filters", "frozen")))
        return orig(*a, **k)

    monkeypatch.setattr(rf, "mint_features", spy)
    res = rr.read(mk(tmp_path, world), mkdeps(world))
    assert calls and set(calls) == {("asof_pda_first_print", "asof"), ("frozen", "frozen")}  # deciding calls: both asof; the frozen calls are the report-only dual
    assert calls.count(("frozen", "frozen")) == res["mints"] and calls.count(("asof_pda_first_print", "asof")) == 2 * res["mints"]  # deciding pass + the dual run's asof pass


def test_verdict_is_printed_before_the_dual_run_starts(tmp_path, world, monkeypatch):
    frozen_calls = []
    orig = rf.mint_features

    def spy(*a, **k):
        if k.get("mint_filters") == "frozen":
            frozen_calls.append(1)
        return orig(*a, **k)

    monkeypatch.setattr(rf, "mint_features", spy)
    paths = mk(tmp_path, world)
    seen = {}
    deps = mkdeps(world)
    deps.stderr = lambda s_: seen.update(frozen_at_verdict=len(frozen_calls), result_file=(paths.out_dir / rr.RESULT).exists())
    res = rr.read(paths, deps)
    assert seen == {"frozen_at_verdict": 0, "result_file": False} and len(frozen_calls) > 0
    assert report(paths.out_dir, "asof_vs_frozen")["data"]["asof_only_rows"] >= 0


def test_unpriced_exit_pays_the_rent_like_a_strand_class_mint():
    assert not hasattr(rr, "UNPRICED_EXIT_RENT_LAMPORTS")
    stake = FZ.STAKE_LAMPORTS
    full = -stake - 505_000 - FZ.RENT_STRANDED
    for kw in (dict(Qx=np.array([np.nan, 77e9, 77e9])), dict(Qx=np.array([0.0, 77e9, 77e9])), dict(Bx=np.array([0.0, 2.5e14, 2.5e14]))):
        u = rr.price_variant(trip(**kw), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
        assert u["nofail"][0] == full and u["stranded"][0] and u["filled"][0]
    sc = rr.price_variant(trip(strand_class=np.array([True, False, False])), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    assert sc["nofail"][0] == full


def test_count_job_refusal_record_refuses_before_the_lock(tmp_path, world):
    cdir = tmp_path / "counts"
    shutil.copytree(world["counts"], cdir)
    (cdir / rc.REFUSED).write_text(rc.stable_json({"refused": True, "through": hkey(3), "reasons": ["day(s) ['2026-10-16'] have a share of candidate PDA pools with a missing V0 above 0.02"]}) + "\n")
    refused(mk(tmp_path, world, counts=cdir), mkdeps(world), "count job recorded refusal(s)", "missing V0")


def test_build_contradicting_counts_is_not_decidable_count_build_divergence(tmp_path, world, monkeypatch):
    real_build = rl.build

    def build(*a, **k):
        st = real_build(*a, **k)
        if not k.get("structure_only"):  # the pre-lock structure pass is clean (it matches counts.jsonl); the full build after the lock contradicts it
            st["v0_missing_days_over"] = ["2026-10-16"]
        return st

    monkeypatch.setattr(rl, "build", build)
    paths = mk(tmp_path, world)
    res = rr.read(paths, mkdeps(world))
    assert res["verdict"] == "NOT_DECIDABLE" and res["reasons"][0].startswith("count_build_divergence") and "v0-missing" in res["reasons"][0]
    assert [e["event"] for e in rr.reads_events(paths.out_dir)] == ["started", "completed"] and (paths.out_dir / rr.RESULT).exists()  # no report-only steps after a divergence


def test_layout_refusal_in_the_build_is_also_a_divergence(tmp_path, world, monkeypatch):
    real_build = rl.build

    def build(*a, **k):
        if k.get("structure_only"):
            return real_build(*a, **k)
        raise rl.LayoutRefused("hour x: 3 print(s) of MEMBER pool(s) have a null virtual_quote_reserves")

    monkeypatch.setattr(rl, "build", build)
    res = rr.read(mk(tmp_path, world), mkdeps(world))
    assert res["verdict"] == "NOT_DECIDABLE" and "count_build_divergence" in res["reasons"][0] and "null virtual_quote_reserves" in res["reasons"][0]


def test_report_only_asof_vs_frozen_null_bt_and_gap(tmp_path, world):
    res = rr.read(mk(tmp_path, world), mkdeps(world))
    out = tmp_path / "out"
    d = report(out, "asof_vs_frozen")["data"]
    assert "NOT removed" in d["note"] or "NOT" in d["note"]
    assert d["asof_only_entered_top10"] <= res["A_attempts"]
    for name, c in d["pnl_asof_only_trades"].items():
        assert c["n"] + d["pnl_other_trades"][name]["n"] == res["A_attempts"]
    nb = report(out, "null_block_time")["data"]
    assert set(nb) <= set(rl.hour_range(rr.H0, hkey(8))) and all(v == 0 for v in nb.values())
    gap = report(out, "gap_table")["data"]
    assert set(gap) == {hkey(1), hkey(2), hkey(3), hkey(4)}  # marks in [W0, E - 1h]
    for g in gap.values():
        assert g["gap"] == g["live"] - g["decision_rows"] >= 0 and g["lt3_prints"] + g["d3_drop"] + g["no_feature_row"] == g["gap"]


def test_asof_only_rows_stay_in_the_book(tmp_path, world, monkeypatch):
    # force the frozen rule to lose every row of one mint: those rows become asof-only; they are reported, never removed from the deciding book
    orig = rf.mint_features

    def spy(prep, c, *a, **k):
        if k.get("mint_filters") == "frozen" and c.mid == 0:
            return None
        return orig(prep, c, *a, **k)

    (tmp_path / "a").mkdir()
    base = rr.read(mk(tmp_path / "a", world), mkdeps(world))
    monkeypatch.setattr(rf, "mint_features", spy)
    (tmp_path / "b").mkdir()
    res = rr.read(mk(tmp_path / "b", world), mkdeps(world))
    d = report(tmp_path / "b" / "out", "asof_vs_frozen")["data"]
    assert d["asof_only_rows"] >= 1 and d["frozen_only_rows"] == 0
    assert res["A_attempts"] == base["A_attempts"] and res["cells"]["flat_1.3"]["total_sol"] == base["cells"]["flat_1.3"]["total_sol"]


def test_subset_violation_is_not_decidable(tmp_path, world, monkeypatch):
    orig = rr.live_keys

    def drop_one(*a, **k):
        out = orig(*a, **k)
        for t in out:  # remove the mint with the most prints from every mark: its decision rows are now outside the live set
            if out[t]:
                out[t].pop(max(out[t], key=out[t].get))
        return out

    monkeypatch.setattr(rr, "live_keys", drop_one)
    res = rr.read(mk(tmp_path, world), mkdeps(world))
    assert res["verdict"] == "NOT_DECIDABLE" and res["keys"]["ok"] is False and (res["keys"]["not_subset_marks"] >= 1 or res["keys"]["live_md5_mismatch_marks"] >= 1)


def test_gap_by_cause_unit():
    live = {"a": 2, "b": 5, "c": 5, "d": 9}
    g = rr.gap_by_cause(live, {"d"}, {(1, 100)}, {"a": 0, "b": 1, "c": 2, "d": 3}, 100)
    assert g == {"lt3_prints": 1, "d3_drop": 1, "no_feature_row": 1}


def test_window_and_d3_rule(tmp_path, world):
    res = rr.read(mk(tmp_path, world), mkdeps(world))
    assert res["A_attempts"] <= 4 * 10  # entry marks in [W0, E - 1h]: 4 marks, K = 10
    assert res["d3"]["kept_pre_filter"] >= res["d3"]["dropped_rows"]
    assert res["w0"] == hkey(1) and res["e"] == hkey(5) and res["alpha"] == 0.025


def test_keys_md5_mismatch_is_not_decidable(tmp_path, world):
    cdir = tmp_path / "counts"
    shutil.copytree(world["counts"], cdir)
    lines = rc.read_counts(cdir)
    lines[2]["live_md5"] = "0" * 32
    write_counts(cdir, lines)
    res = rr.read(mk(tmp_path, world, counts=cdir), mkdeps(world))
    assert res["verdict"] == "NOT_DECIDABLE" and res["keys"]["ok"] is False and res["keys"]["live_md5_mismatch_marks"] >= 1
    assert any("subset" in r for r in res["reasons"])


def test_stage_failure_writes_aborted_and_spends_the_read(tmp_path, world, monkeypatch):
    paths = mk(tmp_path, world)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rr, "run_deciding", boom)
    with pytest.raises(RuntimeError):
        rr.read(paths, mkdeps(world))
    ev = rr.reads_events(paths.out_dir)
    assert [e["event"] for e in ev] == ["started", "aborted"] and "disk full" in ev[1]["reasons"][0]
    assert not (paths.out_dir / rr.RESULT).exists()
    with pytest.raises(rr.ReadRefused, match="no second run"):
        rr.read(paths, mkdeps(world))
    assert len(rr.reads_events(paths.out_dir)) == 2


# ---- preflight refusals: REFUSED, nothing written -------------------------------------------------------------------------------------------------
def test_preflight_ok_writes_nothing(tmp_path, world):
    paths = mk(tmp_path, world)
    plan, fee, reasons = rr.preflight(paths, mkdeps(world), rr.now_utc())
    assert reasons == [] and plan is not None and plan.k == 1 and ef.hour_key(plan.e) == hkey(5) and plan.hours[0] == hkey(0) and plan.hours[-1] == hkey(7)
    assert not paths.out_dir.exists()


def test_pin_line_duplicated_or_missing(tmp_path, world):
    refused(mk(tmp_path, world, exp_extra="EXP027_K: 2\n"), mkdeps(world), "EXP027_K: matches 2 times")
    refused(mk(tmp_path, world, EXP027_K=None), mkdeps(world), "EXP027_K: matches 0 times")


def test_route_pending(tmp_path, world):
    refused(mk(tmp_path, world, EXP027_ROUTE="PENDING"), mkdeps(world), "route is not decided")


def test_exp_dirty_vs_head(tmp_path, world):
    refused(mk(tmp_path, world), mkdeps(world, clean_fn=lambda p: ["EXP-027.md differs from HEAD"]), "differs from HEAD")


def test_code_sha_mismatch_and_placeholder(tmp_path, world):
    refused(mk(tmp_path, world, EXP027_SHA256_R4B_BOOK="f" * 64), mkdeps(world), "code sha256 of r4b_book.py")
    refused(mk(tmp_path, world, EXP027_SHA256_R4B_COUNT="<TO FILL>"), mkdeps(world), "EXP027_SHA256_R4B_COUNT is not a sha256")


def test_model_md5_decoder_and_tiers(tmp_path, world):
    refused(mk(tmp_path, world), mkdeps(world, models_check_fn=lambda md: ["r4-b_seed3.txt: md5 x / sha256 y do not match the pins"]), "r4-b_seed3.txt")
    refused(mk(tmp_path, world), mkdeps(world, decoder_fn=lambda ref: ["decoder blob a: pinned b, actual c"]), "decoder blob a")
    refused(mk(tmp_path, world, EXP027_TIERS_SHA256="0" * 64), mkdeps(world), "FEE_TIERS sha256")


def test_default_models_check_runs_on_repo_models():
    assert rr.default_models_check(None) == []  # the five boosters, features.txt and the fixture equal the pins


def test_e_records_missing(tmp_path, world):
    for e in "ABCD":
        refused(mk(tmp_path, world, **{f"EXP027_E_{e}_MD5": "<TO FILL>"}), mkdeps(world), f"E-{e.lower()} md5 record is missing")


def test_e1_record(tmp_path, world):
    rec = world["rec"]
    smallrec = rec / "e1_small.json"
    smallrec.write_text(json.dumps({"n": 19, "pnl_gap_lamports_live_minus_sim_mean": -1.0}))
    refused(mk(tmp_path, world, EXP027_E1_RECORD=f"{smallrec}@{sha(smallrec)}"), mkdeps(world), "E1 record n is 19")
    refused(mk(tmp_path, world, EXP027_E1_RECORD=f"{rec / 'e1.json'}@{'0' * 64}"), mkdeps(world), "E1 record")
    refused(mk(tmp_path, world, EXP027_E1_RECORD=f"{rec / 'nope.json'}@{'0' * 64}"), mkdeps(world), "does not exist")


def test_input_views_and_tries(tmp_path, world):
    refused(mk(tmp_path, world, EXP027_INPUT_VIEWS=f"{world['rec'] / 'view.txt'}@{'0' * 64}"), mkdeps(world), "input view")
    refused(mk(tmp_path, world, EXP027_TRIES_ENTRY="EXP-999"), mkdeps(world), "no entry whose key is exactly")


def test_mint_account_file_pinned(tmp_path, world):
    refused(mk(tmp_path, world, EXP027_MINT_ACCOUNTS=f"{world['rec'] / 'mints.json'}@{'0' * 64}"), mkdeps(world), "mint-account file")


def test_calendar_not_passed(tmp_path, world):
    refused(mk(tmp_path, world), mkdeps(world, now_fn=lambda: HOUR0 + timedelta(hours=1 + 4) - timedelta(seconds=1)), "has not passed")


def test_w0_bounds(tmp_path, world, monkeypatch):
    monkeypatch.setattr(rr, "EARLIEST_W0", hkey(3))
    refused(mk(tmp_path, world), mkdeps(world), "is before")
    monkeypatch.setattr(rr, "EARLIEST_W0", hkey(0))
    refused(mk(tmp_path, world, EXP027_LAST_W0="2026-10-16T03"), mkdeps(world), "after EXP027_LAST_W0")


def test_hour_not_sealed_or_row_mismatch(tmp_path, world):
    walk = make_walk(tmp_path / "w", stream(NH), unsealed=[hkey(6)], rows_delta={(hkey(7), "trades"): 3})
    paths = mk(tmp_path, {**world, "walk": walk})
    r = refused(paths, mkdeps({**world, "walk": walk}), "not sealed")
    assert any(hkey(7) in x and "rows=" in x for x in r)


@pytest.mark.parametrize("bad", [5, 6, 7])
def test_exit_hour_and_extra_trade_hours_must_be_verified(tmp_path, world, bad):
    # E = hkey(5); the allowlist runs to E + 3h: hours 5, 6, 7 (the exit hour and the two extra trade hours) must all be sealed and verified
    walk = make_walk(tmp_path / "w", stream(NH), no_verify=[hkey(bad)])
    refused(mk(tmp_path, {**world, "walk": walk}), mkdeps({**world, "walk": walk}), hkey(bad))


def test_counts_must_cover_every_hour(tmp_path, world):
    cdir = tmp_path / "counts"
    shutil.copytree(world["counts"], cdir)
    ls = rc.read_counts(cdir)
    write_counts(cdir, ls[:-1])
    refused(mk(tmp_path, world, counts=cdir), mkdeps(world), "counts.jsonl lacks 1 hour")
    refused(mk(tmp_path, world, counts=tmp_path / "nocounts"), mkdeps(world), "the count job has not run")


def test_counts_lacking_hour_in_h0_to_e_and_extra_hours_rule(tmp_path, world):
    cdir = tmp_path / "counts"
    shutil.copytree(world["counts"], cdir)
    ls = rc.read_counts(cdir)
    assert [ln["extra_hours_loaded"] for ln in ls] == [2, 2, 2, 2, 1, 0]  # the count job's own rule: E-1 carries 1, E carries 0
    write_counts(cdir, [x for x in ls if x["hour"] != hkey(2)])
    refused(mk(tmp_path, world, counts=cdir), mkdeps(world), "counts.jsonl lacks 1 hour(s) of [H0, E]")
    for i, bad in ((0, 1), (3, 0), (4, 2), (5, 1)):
        ls2 = [dict(x) for x in ls]
        ls2[i]["extra_hours_loaded"] = bad
        write_counts(cdir, ls2)
        refused(mk(tmp_path, world, counts=cdir), mkdeps(world), f"hour {ls[i]['hour']}: extra_hours_loaded is {bad}")


def test_count_config_pins_must_equal_the_exp(tmp_path, world):
    for k, val in (("w0", hkey(2)), ("h0", hkey(1)), ("a_attempts", 7), ("n_blocks", 3), ("stop_mode", "attempts"), ("calendar_stop", hkey(6)), ("exit_horizon_h", 2)):
        cdir = tmp_path / f"c_{k}"
        shutil.copytree(world["counts"], cdir)
        pin = json.loads((cdir / rc.CONFIG).read_text())
        pin[k] = val
        (cdir / rc.CONFIG).write_text(json.dumps(pin))
        refused(mk(tmp_path, world, counts=cdir), mkdeps(world), "count config.json pins differ from the EXP's")
        shutil.rmtree(tmp_path / "out", ignore_errors=True)


def test_counts_sha_and_code_pins(tmp_path, world):
    cdir = tmp_path / "counts"
    shutil.copytree(world["counts"], cdir)
    ls = rc.read_counts(cdir)
    ls[1]["sha256"]["trades"] = "0" * 64
    write_counts(cdir, ls)
    refused(mk(tmp_path, world, counts=cdir), mkdeps(world), "counts.jsonl sha256 differs")
    shutil.copytree(world["counts"], tmp_path / "counts_b")
    pin = json.loads((tmp_path / "counts_b" / rc.CONFIG).read_text())
    pin["code_sha256"]["r4b_count.py"] = "0" * 64
    (tmp_path / "counts_b" / rc.CONFIG).write_text(json.dumps(pin))
    refused(mk(tmp_path, world, counts=tmp_path / "counts_b"), mkdeps(world), "count job ran r4b_count.py")


def test_precount_refusals_from_the_counts(tmp_path, world):
    cdir = tmp_path / "counts"
    shutil.copytree(world["counts"], cdir)
    ls = rc.read_counts(cdir)
    for ln in ls:
        if ln.get("grad_pda_match") is not None:
            ln["grad_pda_match"] = 0
    write_counts(cdir, ls)
    refused(mk(tmp_path, world, counts=cdir), mkdeps(world), "PDA match")
    cdir2 = tmp_path / "counts2"
    shutil.copytree(world["counts"], cdir2)
    ls = rc.read_counts(cdir2)
    ls[3]["mixed_null_txi_slots"] = 2
    write_counts(cdir2, ls)
    refused(mk(tmp_path, world, counts=cdir2), mkdeps(world), "mixing null and non-null tx_index")


def test_fewer_than_100_marks_with_10_live(tmp_path, world, monkeypatch):
    monkeypatch.setattr(rc, "MIN_MARKS_WITH_LIVE", 100)
    monkeypatch.setattr(rc, "MIN_LIVE_TOKENS", 10)
    refused(mk(tmp_path, world), mkdeps(world), "counted marks with >= 10 live tokens")


def test_date_with_graduations_and_no_decisions(tmp_path, world, monkeypatch):
    monkeypatch.setattr(rc, "aggregate_problems", lambda lines, cfg, e: ["UTC date 2026-10-17: 12 graduations (members) but 0 decision rows"])
    refused(mk(tmp_path, world), mkdeps(world), "graduations (members) but 0 decision rows")


def test_date_predicate_itself():
    def line(h, d, hh, dec, grad_hour, members):
        return {"hour": h, "mark_t": int(datetime(2026, 10, d, hh, tzinfo=timezone.utc).timestamp()), "live_tokens": 0, "decision_rows": dec, "grad_hour": grad_hour, "grad_members": members}

    lines = [line("2026-10-16T22", 16, 22, 0, "2026-10-16T21", 4), line("2026-10-16T23", 16, 23, 0, "2026-10-16T22", 0), line("2026-10-17T00", 17, 0, 0, "2026-10-16T23", 0)]
    cfg = rc.Config(Path("."), Path("."), "2026-10-16T22", h0="2026-10-16T01")
    assert any("0 decision rows" in p for p in rc.date_problems(lines, cfg, None))


def test_unmeasurable_sph_hour(tmp_path, world):
    calls = {"n": 0}

    def sph(path):
        calls["n"] += 1
        return None if f"{hkey(6)}.jsonl" in str(path) else 10000.0

    refused(mk(tmp_path, world), mkdeps(world, sph_fn=sph), f"hour {hkey(6)}: slots-per-hour is unmeasurable")
    assert calls["n"] == 2  # only the two hours outside counts.jsonl (E+1h, E+2h) were measured; the count job's sph is reused


def test_second_run_and_lock(tmp_path, world):
    paths = mk(tmp_path, world)
    paths.out_dir.mkdir()
    (paths.out_dir / rr.LOCK).write_text("x")
    with pytest.raises(rr.ReadRefused, match="READ.lock exists"):
        rr.read(paths, mkdeps(world))
    assert not (paths.out_dir / rr.READS).exists()
    (paths.out_dir / rr.LOCK).unlink()
    (paths.out_dir / rr.READS).write_text(json.dumps({"event": "aborted"}) + "\n")
    with pytest.raises(rr.ReadRefused, match="no second run"):
        rr.read(paths, mkdeps(world))
    tok = rr.take_lock(tmp_path / "lk")
    with pytest.raises(rr.ReadRefused):
        rr.take_lock(tmp_path / "lk")
    assert len(tok) == 16


def test_all_reasons_are_reported_together(tmp_path, world):
    r = refused(mk(tmp_path, world, EXP027_ROUTE="PENDING", EXP027_E_A_MD5="<TO FILL>", EXP027_K="zero"), mkdeps(world, decoder_fn=lambda ref: ["decoder blob a differs"]),
                "route is not decided", "E-a md5", "EXP027_K", "decoder blob a differs")
    assert len(r) >= 4


def test_no_cli_override_of_pinned_values(tmp_path):
    flags = set()
    orig = argparse.ArgumentParser.add_argument

    def spy(self, *a, **k):
        flags.update(x for x in a if x.startswith("-"))
        return orig(self, *a, **k)

    argparse.ArgumentParser.add_argument = spy  # type: ignore[assignment]
    try:
        with pytest.raises(SystemExit):
            rr.main(["--help"])
    finally:
        argparse.ArgumentParser.add_argument = orig  # type: ignore[assignment]
    assert flags - {"-h", "--help"} == {"--walk-dir", "--out-dir", "--counts-dir", "--check-window", "--memory-limit", "--threads"}  # R4: no --exp-file, no --models-dir
    for bad in ("--send-fee", "--k", "--w0", "--stop-hours", "--alpha", "--rent", "--latency", "--exp-file", "--models-dir"):
        with pytest.raises(SystemExit) as ei:
            rr.main(["--walk-dir", str(tmp_path), bad, "1"])
        assert ei.value.code == 2


def test_main_exit_code_on_refusal_and_fixed_paths(tmp_path, world, capsys, monkeypatch):
    seen = {}
    real_read = rr.read

    def spy(paths, deps=None):
        seen.update(paths=paths, deps=deps)
        return real_read(paths, deps)

    monkeypatch.setattr(rr, "read", spy)
    out = tmp_path / "out"
    code = rr.main(["--walk-dir", str(world["walk"]), "--out-dir", str(out), "--counts-dir", str(world["counts"])])
    assert code == 2 and "REFUSED:" in capsys.readouterr().err and not out.exists()  # the repo has no EXP-027 with every pin yet (or a dirty one): refused, nothing written
    assert seen["paths"].exp_file == rr.EXP_FILE and seen["paths"].models_dir is None and seen["deps"].walk_pin == rr.PINNED_WALK_DIR
    assert rr.PINNED_WALK_DIR == "/data/mal/blocks/forward-1016"


# ---- fee-arm record: the 160k switch -------------------------------------------------------------------------------------------------------------
STARTED = datetime(2026, 11, 8, tzinfo=timezone.utc)


def fee_rec(tmp_path, **d):
    base = {"verdict_at": "2026-10-20T00:00:00Z", "test_a_pass": True, "test_b_pass": True, "test_b_upper95_slots": 0.8}
    base.update(d)
    p = tmp_path / "fee.json"
    p.write_text(json.dumps(base))
    return p


def v_fee(val):
    return {"EXP027_FEE_ARM": val}


def test_fee_switch_a_and_b_pass_gives_160k_and_shift(tmp_path):
    p = fee_rec(tmp_path)
    probs: list[str] = []
    d = rr.fee_arm_decision(v_fee(f"{p}@{sha(p)}"), STARTED, probs)
    assert probs == [] and d["send"] == 160_000 and d["delta_slots"] == 0.8 and d["source"] == "160k"


def test_fee_switch_negative_bound_floors_at_zero(tmp_path):
    p = fee_rec(tmp_path, test_b_upper95_slots=-0.4)
    d = rr.fee_arm_decision(v_fee(f"{p}@{sha(p)}"), STARTED, [])
    assert d["send"] == 160_000 and d["delta_slots"] == 0.0


@pytest.mark.parametrize("over", [{"test_a_pass": False}, {"test_b_pass": False}, {"test_a_pass": None}, {"test_b_upper95_slots": None}, {"test_b_pass": "yes"}])
def test_fee_switch_anything_else_is_505k(tmp_path, over):
    p = fee_rec(tmp_path, **over)
    d = rr.fee_arm_decision(v_fee(f"{p}@{sha(p)}"), STARTED, [])
    assert d["send"] == 505_000 and d["delta_slots"] == 0.0 and d["source"] == "505k"


def test_fee_switch_fallbacks(tmp_path):
    assert rr.fee_arm_decision(v_fee("NONE"), STARTED, [])["send"] == 505_000
    assert rr.fee_arm_decision(v_fee(f"{tmp_path / 'absent.json'}@{'0' * 64}"), STARTED, [])["send"] == 505_000
    p = fee_rec(tmp_path, verdict_at="2026-11-08T00:00:00Z")  # dated at the read start: not before it
    assert rr.fee_arm_decision(v_fee(f"{p}@{sha(p)}"), STARTED, [])["send"] == 505_000
    p = fee_rec(tmp_path, verdict_at="garbage")
    assert rr.fee_arm_decision(v_fee(f"{p}@{sha(p)}"), STARTED, [])["send"] == 505_000
    probs: list[str] = []
    p = fee_rec(tmp_path)
    assert rr.fee_arm_decision(v_fee(f"{p}@{'0' * 64}"), STARTED, probs)["send"] == 505_000 and probs and "sha256" in probs[0]


def test_fee_arm_sha_mismatch_refuses_the_read(tmp_path, world):
    p = fee_rec(tmp_path)
    refused(mk(tmp_path, world, EXP027_FEE_ARM=f"{p}@{'0' * 64}"), mkdeps(world), "fee-arm record")


def test_k_rounding_and_lag_shift():
    s = np.array([0.2, 0.36, 0.4])
    assert rr.k_for(1.3, s).tolist() == [6, 4, 3]  # 6.5 -> 6 (half to even), 3.61 -> 4, 3.25 -> 3
    assert rr.k_for(1.3, s, 0.3).tolist() == [7, 4, 4]
    assert rr.k_for(1.9, s, 0.0).tolist() == [9, 5, 5]  # 1.9 / 0.2 is 9.499999999999998 in float64 -> 9 (as r4b_frozen.k_slots); 5.28 -> 5, 4.75 -> 5


def test_shifted_entry_equals_mint_states_at_zero_shift():
    rng = np.random.default_rng(3)
    n = 60
    sl = np.sort(rng.integers(1000, 1400, n)) + np.arange(n)
    ib = rng.random(n) < 0.5
    so = rng.integers(1, 5, n) * 1e8
    tok = rng.integers(1, 5, n) * 1e6
    q = 6e10 + np.cumsum(rng.integers(-1, 2, n)) * 1e8
    b = 2.5e14 - np.cumsum(rng.integers(-1, 2, n)) * 1e9
    vq = np.full(n, 1.76e10)
    sd = sl[[10, 20, 30]]
    s_dec = np.array([0.2, 0.36, 0.4])
    st = bk.mint_states(sl, ib, so, tok, q, b, vq, sd, s_dec, lats=(1.3, 1.9))
    qL = q[-1] + so[-1] * (1 - FZ.FINAL_FEE) if ib[-1] else q[-1] - so[-1] / (1 - FZ.FINAL_FEE)
    bL = b[-1] - tok[-1] if ib[-1] else b[-1] + tok[-1]
    QX, BX = np.append(q, qL) + np.append(vq, vq[-1]), np.append(b, bL)
    for lat in (1.3, 1.9):
        Qe, Be, ssb, nb_ = rr.entry_states_shifted(sl, QX, BX, ib, so, sd, s_dec, lat, 0.0)
        assert np.array_equal(Qe, st["Qe"][lat]) and np.array_equal(Be, st["Be"][lat]) and np.array_equal(ssb, st["ssb"][lat]) and np.array_equal(nb_, st["nearby"][lat])
    sh = rr.entry_states_shifted(sl, QX, BX, ib, so, sd, s_dec, 1.3, 2.0)
    assert not (np.array_equal(sh[0], st["Qe"][1.3]) and np.array_equal(sh[2], st["ssb"][1.3]))  # a 2-slot shift moves the landing state


# ---- pieces ---------------------------------------------------------------------------------------------------------------------------------------
def test_slot_s_by_date_uses_median_hourly_sph():
    sph = {"2026-10-18T00": 18000.0, "2026-10-18T01": 18000.0, "2026-10-18T02": 9000.0, "2026-10-19T00": 10000.0}
    ss = rr.slot_s_by_date(sph)
    assert ss["2026-10-18"] == pytest.approx(0.2) and ss["2026-10-19"] == pytest.approx(0.36)


def test_mint_accounts_strand_classes(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"fetch_slot": 7, "mints": {"ok": {"fetch_ok": True, "owner_program": rr.SPL_TOKEN_PROGRAM}, "freeze": {"fetch_ok": True, "owner_program": rr.SPL_TOKEN_PROGRAM, "freeze_authority": "X"},
                                       "ext": {"fetch_ok": True, "owner_program": rr.TOKEN_2022_PROGRAM, "token2022_extensions": ["transferFeeConfig"]},
                                       "harmless": {"fetch_ok": True, "owner_program": rr.TOKEN_2022_PROGRAM, "token2022_extensions": ["metadataPointer"]}, "failed": {"fetch_ok": False}}}))
    probs: list[str] = []
    got, info, t22 = rr.load_mint_accounts({"EXP027_MINT_ACCOUNTS": f"{p}@{sha(p)}"}, probs)
    # census allowlist (metadataPointer + tokenMetadata): harmless is not strand-class, transferFeeConfig is
    assert probs == [] and got == {"ok": False, "freeze": True, "ext": True, "harmless": False, "failed": True}
    assert t22 == {"ok": False, "freeze": False, "ext": True, "harmless": True, "failed": True}  # SPL Token only on a successful SPL fetch; everything else is the higher rent
    assert info["fetch_slot"] == 7 and info["failed_fetch"] == 1 and info["failed_fetch_share"] == pytest.approx(0.2)


def test_mark_lookup_unpriced_exit_is_nan():
    marks = [(np.array([1, 1]), np.array([3600, 7200]), np.array([5.0, 6.0]), np.array([7.0, 8.0]))]
    qx, bx = rr.mark_lookup(marks, np.array([1, 1, 2]), np.array([7200, 10800, 3600]))
    assert qx[0] == 6.0 and bx[0] == 8.0 and np.isnan(qx[1]) and np.isnan(qx[2])
    assert np.isnan(rr.mark_lookup([], np.array([1]), np.array([3600]))[0]).all()


def trip(**kw):
    n = 3
    Q0, B0 = 77e9, 2.5e14
    ent = {1.3: [np.full(n, Q0), np.full(n, B0), np.array([0.0, 2.0, 5.0]), np.array([0.0, 1e9, 3e9])]}
    tr = {"ent": ent, "spot": np.full(n, Q0 / B0), "Qx": np.full(n, Q0), "Bx": np.full(n, B0), "strand_class": np.zeros(n, bool), "rent_t22": np.zeros(n, bool)}
    tr.update(kw)
    return tr


def test_price_variant_strand_unpriced_and_e1_penalty():
    stake = FZ.STAKE_LAMPORTS
    base = rr.price_variant(trip(), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    assert not base["stranded"].any() and base["filled"].all()
    st = rr.price_variant(trip(strand_class=np.array([True, False, False])), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    assert st["nofail"][0] == -stake - 505_000 - FZ.RENT_STRANDED and st["nofail"][1] == base["nofail"][1]
    un = rr.price_variant(trip(Qx=np.array([np.nan, 77e9, 77e9])), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    assert un["stranded"].tolist() == [True, False, False] and un["nofail"][0] == -stake - 505_000 - FZ.RENT_STRANDED  # (e) stake + buy send + rent, no sell send
    assert un["filled"].all()  # the trade is never skipped
    for kw in (dict(Qx=np.array([0.0, 77e9, 77e9])), dict(Bx=np.array([0.0, 2.5e14, 2.5e14])), dict(Bx=np.array([-5.0, 2.5e14, 2.5e14])), dict(Qx=np.array([-1.0, 77e9, 77e9]))):
        u2 = rr.price_variant(trip(**kw), 1.3, send=505_000, rent=0, auto=True, e1_penalty=5e6)
        assert u2["stranded"].tolist() == [True, False, False] and u2["nofail"][0] == -stake - 505_000 - FZ.RENT_STRANDED and len(u2["nofail"]) == 3
    assert (un["flat"][0] == 0.85 * (-stake - 505_000 - FZ.RENT_STRANDED) - 0.15 * 505_000)  # the fail legs still apply to the unpriced trade
    below = rr.price_variant(trip(), 1.3, send=505_000, rent=0, auto=True, e1_penalty=1.0)  # penalty below the haircut: unchanged
    assert np.allclose(below["nofail"], base["nofail"])
    h0, _ = bk.round_trip(np.full(3, float(stake)), *trip()["ent"][1.3][:2], trip()["spot"], trip()["Qx"], trip()["Bx"], 505_000, 0, h_in=0.0, h_out=0.0)
    hair = h0 - base["nofail"]
    assert (hair > 0).all()
    big = rr.price_variant(trip(), 1.3, send=505_000, rent=0, auto=True, e1_penalty=hair.max() + 1_000_000.0)
    assert np.allclose(big["nofail"], h0 - (hair.max() + 1_000_000.0))  # loses the larger of the haircut and the penalty: here the penalty
    rent = rr.price_variant(trip(), 1.3, send=505_000, rent=FZ.RENT_REPORT, auto=True, e1_penalty=0.0)
    assert np.allclose(rent["nofail"], base["nofail"] - FZ.RENT_REPORT)
    cheap = trip(Bx=np.full(3, 1e30))  # a sell into a pool with a huge base side: proceeds below one send
    assert rr.price_variant(cheap, 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)["stranded"].all()
    assert not rr.price_variant(cheap, 1.3, send=505_000, rent=0, auto=False, e1_penalty=0.0)["stranded"].any()


def test_lp_discontinuity_counts():
    ib = np.array([True, False, True, True])
    tok = np.array([10.0, 5.0, 3.0, 2.0])
    b = np.array([1000.0, 990.0, 995.0, 900.0])  # 1000-10 = 990 ok; 990+5 = 995 ok; 995-3 = 992 vs 900 -> discontinuity
    d = rr.lp_discontinuities(ib, tok, b, np.zeros(4, int))
    assert d["pairs"] == 3 and d["gt_1e-06"] == 1 and d["gt_0.01"] == 1
    assert rr.lp_discontinuities(ib[:1], tok[:1], b[:1], np.zeros(1, int))["pairs"] == 0


# ---- the decision: NOT_DECIDABLE paths, PASS and FAIL synthetic books (real constants) ---------------------------------------------------------------
W0 = int(datetime(2026, 10, 18, 1, tzinfo=timezone.utc).timestamp())


def synth_cells(days=22, per_day=8, mean=2_000_000.0, sd=3_000_000.0, seed=5, big_first=0.0):
    """Four cells of one synthetic book: `per_day` attempts per 24 h block (mean/sd in lamports); the cells differ by a small shift."""
    rng = np.random.default_rng(seed)
    T0 = np.array([W0 + d * 86400 + j * 3600 for d in range(days) for j in range(per_day)])
    mid = np.arange(len(T0))
    x = rng.normal(mean, sd, len(T0))
    if big_first:
        x[0] += big_first
    cells = {f"{leg}_{lat}": bk.book_stats(x + off, T0, mid, w0=W0) for (leg, lat), off in zip([("flat", 1.3), ("flat", 1.9), ("press", 1.3), ("press", 1.9)], (0, -1e4, -2e4, -3e4))}
    return cells, len(x), days


def test_pass_synthetic_book(real):
    cells, A, N = synth_cells(mean=3_000_000.0, sd=1_500_000.0)
    d = rr.decide(cells, N, A, True, False, 0.025)
    assert d["verdict"] == "PASS" and d["gate"]["pass"] and d["gate"]["decision_p"] <= 0.025
    assert all(all(v.values()) for v in d["gate"]["cells"].values())


def test_fail_synthetic_books(real):
    cells, A, N = synth_cells(mean=-500_000.0, sd=3_000_000.0)
    assert rr.decide(cells, N, A, True, False, 0.025)["verdict"] == "FAIL"
    # a mean carried by one huge trade: ex-top-3 <= 0 -> FAIL, not PASS
    cells, A, N = synth_cells(mean=0.0, sd=1_000_000.0, big_first=2_000_000_000.0)
    d = rr.decide(cells, N, A, True, False, 0.025)
    assert d["verdict"] == "FAIL" and not d["gate"]["cells"]["flat_1.3"]["ex_top3"]
    # Bonferroni: the pass-book at a tiny alpha (0.025 / k with k huge) fails on the p item alone
    cells, A, N = synth_cells(mean=3_000_000.0, sd=1_500_000.0)
    d = rr.decide(cells, N, A, True, False, 1e-300)
    assert d["verdict"] == "FAIL" and not d["gate"]["p_ok"] and all(all(v.values()) for v in d["gate"]["cells"].values())


def test_larger_p_of_the_four_cells_decides(real):
    cells, A, N = synth_cells(mean=3_000_000.0, sd=1_500_000.0)
    cells["press_1.9"] = dict(cells["press_1.9"], p_block=0.2)
    g = rr.gate(cells, 0.025)
    assert g["decision_p"] == 0.2 and not g["pass"]


def test_not_decidable_paths(real):
    cells, A, N = synth_cells(mean=3_000_000.0, sd=1_500_000.0)
    d = rr.decide(cells, 19, A, True, False, 0.025)
    assert d["verdict"] == "NOT_DECIDABLE" and "19 blocks" in d["reasons"][0]  # N < 20
    d = rr.decide(cells, N, 124, True, False, 0.025)
    assert d["verdict"] == "NOT_DECIDABLE" and "124 attempts" in d["reasons"][0]  # A < 125
    d = rr.decide(cells, N, A, False, False, 0.025)
    assert d["verdict"] == "NOT_DECIDABLE" and "subset" in d["reasons"][0]  # keys not subset of the count job's live keys
    d = rr.decide(cells, N, A, True, True, 0.025)
    assert d["verdict"] == "NOT_DECIDABLE" and "pressure-leg" in d["reasons"][0]
    few, _, _ = synth_cells(days=20, per_day=4)  # 80 trades per cell
    d = rr.decide(few, 20, 125, True, False, 0.025)
    assert d["verdict"] == "NOT_DECIDABLE" and any("fewer than 100 trades" in r for r in d["reasons"])
    assert rr.decide({}, 0, 0, True, False, 0.025)["verdict"] == "NOT_DECIDABLE"


def test_not_decidable_comes_before_the_gate(real):
    cells, A, N = synth_cells(mean=3_000_000.0, sd=1_500_000.0)
    d = rr.decide(cells, N, A, False, False, 0.025)
    assert d["gate"]["pass"] is True and d["verdict"] == "NOT_DECIDABLE"


def test_pinned_constants_are_the_exp_values(real):
    assert (rr.STOP_HOURS, rr.MIN_BLOCKS, rr.MIN_ATTEMPTS, rr.GATE_N, rr.GATE_DATES, rr.ALPHA_FAMILY, rr.EXIT_HORIZON_H) == (480, 20, 125, 100, 5, 0.025, 1)
    assert rr.H0 == "2026-10-16T03" == rc.H0 and rr.UNIVERSE_EXITS_AT_MARKS_ONLY is True and rr.EARLIEST_W0 == "2026-10-18T03" and rr.SEND_FEE_160K == 160_000 and FZ.SEND_FEE_DECIDING == 505_000 and FZ.RENT_DECIDING == 0
    assert FZ.K_ROUNDING == "round" and FZ.LATENCIES_S == (1.3, 1.9) and rr.EXTRA_TRADE_HOURS == 2


# ======================================================================================================================================================
# QP / reviewer round 1 on ae0b32d: write order, report-only isolation, deadline, pinned inputs, structure pre-check, A3, mint accounts
# ======================================================================================================================================================
import subprocess  # noqa: E402


# ---- Rev1 / R2: atomic result, deciding result survives a report-only crash -------------------------------------------------------------------------
def test_write_once_is_atomic_and_no_overwrite(tmp_path):
    p = tmp_path / "x.json"
    with pytest.raises(TypeError):  # serialization happens first: nothing, not even a temp file, is left behind
        rr.write_once(p, {"bad": object()})
    assert list(tmp_path.iterdir()) == []
    rr.write_once(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1} and (p.stat().st_mode & 0o777) == 0o444
    with pytest.raises(FileExistsError):
        rr.write_once(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 1} and [x.name for x in tmp_path.iterdir()] == ["x.json"]  # no temp name left


def test_unserializable_result_leaves_no_result_file_and_aborts(tmp_path, world, monkeypatch):
    real_run = rr.run_deciding

    def run(*a, **k):
        res, ctx = real_run(*a, **k)
        res["poison"] = object()
        return res, ctx

    monkeypatch.setattr(rr, "run_deciding", run)
    paths = mk(tmp_path, world)
    with pytest.raises(TypeError):
        rr.read(paths, mkdeps(world))
    assert [e["event"] for e in rr.reads_events(paths.out_dir)] == ["started", "aborted"]
    assert not (paths.out_dir / rr.RESULT).exists() and not [x for x in paths.out_dir.iterdir() if x.name.endswith(".tmp")]
    assert not (paths.out_dir / "scratch").exists()


def test_dual_run_crash_keeps_the_deciding_result_and_completed(tmp_path, world, monkeypatch):
    def boom(*a, **k):
        raise MemoryError("dual run out of memory")

    monkeypatch.setattr(rr, "dual_rows", boom)
    paths = mk(tmp_path, world)
    res = rr.read(paths, mkdeps(world))
    out = paths.out_dir
    ev = rr.reads_events(out)
    assert [e["event"] for e in ev[:2]] == ["started", "completed"] and not any(e["event"] == "aborted" for e in ev)
    assert (out / rr.RESULT).exists() and ev[1]["result_sha256"] == sha(out / rr.RESULT) and json.loads((out / rr.RESULT).read_text())["verdict"] == res["verdict"]
    failed = [e for e in ev if e["event"] == "report_failed"]
    assert [e["step"] for e in failed] == ["asof_vs_frozen"] and "MemoryError" in failed[0]["error"]
    idx = json.loads((out / "report-only.json").read_text())["report_only"]
    assert idx["asof_vs_frozen"]["status"] == "failed" and "dual run out of memory" in idx["asof_vs_frozen"]["error"]
    assert report(out, "asof_vs_frozen")["status"] == "failed"
    assert all(v["status"] == "ok" for k, v in idx.items() if k != "asof_vs_frozen")  # the other steps are unaffected
    assert not (out / "scratch").exists()


def test_every_report_step_is_isolated(tmp_path, world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("lp broke")

    monkeypatch.setattr(rr, "lp_discontinuities", boom)
    orig_price = rr.price_variant

    def price(tr, lat, **kw):
        if kw.get("rent") == FZ.RENT_REPORT:  # only the report-only rent leg
            raise ValueError("rent leg broke")
        return orig_price(tr, lat, **kw)

    monkeypatch.setattr(rr, "price_variant", price)
    paths = mk(tmp_path, world)
    res = rr.read(paths, mkdeps(world))
    idx = json.loads((paths.out_dir / "report-only.json").read_text())["report_only"]
    assert {k for k, v in idx.items() if v["status"] == "failed"} == {"rent", "lp_discontinuities"}
    assert res["verdict"] == json.loads((paths.out_dir / rr.RESULT).read_text())["verdict"] and "RuntimeError" in idx["lp_discontinuities"]["error"]


def test_report_files_are_not_overwritten(tmp_path):
    idx = rr.run_report_only(tmp_path, None, "tok", steps=[("a", lambda: {"x": 1})])
    assert idx["a"]["status"] == "ok"
    idx2 = rr.run_report_only(tmp_path, None, "tok", steps=[("a", lambda: {"x": 2})])  # same name again: O_EXCL refuses, recorded as failed, first file intact
    assert idx2["a"]["status"] == "failed" and json.loads((tmp_path / "report-a.json").read_text())["data"] == {"x": 1}


# ---- R3: deadline --------------------------------------------------------------------------------------------------------------------------
def test_late_start_is_not_decidable_and_opens_no_outcome_row(tmp_path, world, monkeypatch):
    e = HOUR0 + timedelta(hours=1 + 4)

    def no(*a, **k):
        raise AssertionError("an outcome row was opened")

    monkeypatch.setattr(rr, "run_deciding", no)
    monkeypatch.setattr(rf, "mint_features", no)
    paths = mk(tmp_path, world)
    res = rr.read(paths, mkdeps(world, now_fn=lambda: e + timedelta(hours=rr.DEADLINE_HOURS)))
    assert res["verdict"] == "NOT_DECIDABLE" and "deadline" in res["reasons"][0] and res["cells"] == {}
    assert [x["event"] for x in rr.reads_events(paths.out_dir)] == ["started", "completed"] and (paths.out_dir / rr.RESULT).exists()
    assert json.loads((paths.out_dir / rr.RESULT).read_text())["verdict"] == "NOT_DECIDABLE"


def test_start_just_before_the_deadline_reads_normally(tmp_path, world):
    e = HOUR0 + timedelta(hours=1 + 4)
    res = rr.read(mk(tmp_path, world), mkdeps(world, now_fn=lambda: e + timedelta(hours=rr.DEADLINE_HOURS) - timedelta(seconds=1)))
    assert res["verdict"] in ("PASS", "FAIL") and res["A_attempts"] > 0


def test_clock_is_checked_again_when_the_verdict_is_formed(tmp_path, world, monkeypatch):
    e = HOUR0 + timedelta(hours=1 + 4)
    state = {"now": e + timedelta(hours=1)}
    real_decide = rr.decide

    def decide(*a, **k):
        state["now"] = e + timedelta(hours=rr.DEADLINE_HOURS, seconds=5)  # the deadline passes while the read runs
        return real_decide(*a, **k)

    monkeypatch.setattr(rr, "decide", decide)
    paths = mk(tmp_path, world)
    res = rr.read(paths, mkdeps(world, now_fn=lambda: state["now"]))
    assert res["verdict"] == "NOT_DECIDABLE" and res["reasons"][0].startswith("deadline: the verdict was formed")
    assert [x["event"] for x in rr.reads_events(paths.out_dir)][:2] == ["started", "completed"]


def test_deadline_pin_line_must_say_48h(tmp_path, world):
    refused(mk(tmp_path, world, EXP027_DEADLINE="E + 72 h"), mkdeps(world), "EXP027_DEADLINE does not state E + 48 h")


# ---- R4: the EXP is a tracked, clean repo file; no CLI path flags -------------------------------------------------------------------------------
def run_git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, stdin=subprocess.DEVNULL,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(repo)})


def test_exp_must_be_tracked_and_clean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    (repo / "seed").write_text("x")
    run_git(repo, "add", "seed")
    run_git(repo, "commit", "-q", "-m", "seed")
    exp = repo / "EXP-027.md"
    assert "does not exist" in rr.exp_clean_against_head(repo / "nope.md", repo)[0]  # missing: git diff alone would say rc 0
    exp.write_text("EXP027_K: 1\n")
    assert "not tracked" in rr.exp_clean_against_head(exp, repo)[0]  # untracked: git diff alone would say rc 0
    run_git(repo, "add", "EXP-027.md")
    assert "differs from HEAD" in rr.exp_clean_against_head(exp, repo)[0]  # staged, not committed
    run_git(repo, "commit", "-q", "-m", "exp")
    assert rr.exp_clean_against_head(exp, repo) == []
    exp.write_text("EXP027_K: 2\n")
    assert "differs from HEAD" in rr.exp_clean_against_head(exp, repo)[0]


def test_default_clean_fn_is_the_tracked_check():
    assert rr.Deps().clean_fn is rr.exp_clean_against_head and rr.EXP_FILE == rr.ROOT / "EXP" / "EXP-027-r4b-walk2-prereg.md"


# ---- R5: pinned walk dir ---------------------------------------------------------------------------------------------------------------------------
def test_walk_dir_must_be_the_pinned_dataset(tmp_path, world):
    other = make_walk(tmp_path / "other", stream(NH))
    r = refused(mk(tmp_path, {**world, "walk": other}), mkdeps(world), "is not the pinned dataset")  # data fine, directory not the pinned one
    assert any(str(Path(world["walk"]).resolve()) in x for x in r)
    refused(mk(tmp_path, world, EXP027_WALK_DIR=str(other)), mkdeps(world), "is not the pinned dataset")  # the EXP line must name the pinned dir too
    link = tmp_path / "link"
    link.symlink_to(world["walk"])
    _, _, reasons = rr.preflight(mk(tmp_path, {**world, "walk": link}), mkdeps(world), rr.now_utc())  # a symlink to the pinned dir resolves to it
    assert reasons == []


# ---- R7: sell builder pin ---------------------------------------------------------------------------------------------------------------------------
def test_sell_builder_must_be_pinned_and_match(tmp_path, world):
    rec = world["rec"]
    refused(mk(tmp_path, world, EXP027_SELL_BUILDER=f"{rec / 'sell.py'}@{'0' * 64}"), mkdeps(world), "sell builder")
    refused(mk(tmp_path, world, EXP027_SELL_BUILDER=f"{rec / 'absent.py'}@{'0' * 64}"), mkdeps(world), "sell builder", "does not exist")
    refused(mk(tmp_path, world, EXP027_SELL_BUILDER="TO FILL"), mkdeps(world), "sell builder")
    refused(mk(tmp_path, world, EXP027_SELL_BUILDER=None), mkdeps(world), "EXP027_SELL_BUILDER: matches 0 times")


def test_sell_builder_is_recorded_in_ledger_and_result(tmp_path, world):
    paths = mk(tmp_path, world)
    res = rr.read(paths, mkdeps(world))
    sb = res["sell_builder"]
    assert sb["sha256"] == sha(world["rec"] / "sell.py") and rr.reads_events(paths.out_dir)[0]["sell_builder"] == sb


# ---- R8: E1 scale fixed in code ---------------------------------------------------------------------------------------------------------------------
def test_e1_scale_is_fixed_and_stake_must_match(tmp_path, world):
    assert rr.E1_SCALE == 5.0 == 250_000_000 / 50_000_000
    rec = world["rec"]
    zero = rec / "e1_scale0.json"
    zero.write_text(json.dumps({"n": 25, "stake_lamports": 50_000_000, "scale_to_stake": 0, "pnl_gap_lamports_live_minus_sim_mean": -100000.0}))
    probs: list[str] = []
    e1 = rr.load_e1({"EXP027_E1_RECORD": f"{zero}@{sha(zero)}"}, probs)
    assert probs == [] and e1["scale"] == 5.0 and e1["penalty_lamports"] == 500000.0  # the record's scale 0 is ignored
    for name, body in (("nostake", {"n": 25, "pnl_gap_lamports_live_minus_sim_mean": -1.0}), ("wrongstake", {"n": 25, "stake_lamports": 250_000_000, "pnl_gap_lamports_live_minus_sim_mean": -1.0}),
                       ("boolstake", {"n": 25, "stake_lamports": True, "pnl_gap_lamports_live_minus_sim_mean": -1.0})):
        f = rec / f"e1_{name}.json"
        f.write_text(json.dumps(body))
        refused(mk(tmp_path, world, EXP027_E1_RECORD=f"{f}@{sha(f)}"), mkdeps(world), "E1 record stake_lamports")
        shutil.rmtree(tmp_path / "out", ignore_errors=True)


# ---- R9: structure pre-check before the lock ---------------------------------------------------------------------------------------------------------
def test_structure_precheck_runs_structure_only_over_the_whole_allowlist(tmp_path, world, monkeypatch):
    calls = []
    real_build = rl.build

    def build(walk, h0, h1, out, **k):
        calls.append((h0, h1, bool(k.get("structure_only")), k.get("trade_end")))
        return real_build(walk, h0, h1, out, **k)

    monkeypatch.setattr(rl, "build", build)
    paths = mk(tmp_path, world)
    rr.read(paths, mkdeps(world))
    assert calls[0] == (hkey(0), hkey(8), True, hkey(8)) and calls[1][2] is False  # [H0, E + 3h), trade_end E + 3h; then the full build
    assert [c[2] for c in calls] == [True, False]


@pytest.mark.parametrize("what", ["v0", "mixed", "layout"])
def test_precheck_structure_refusal_refuses_before_the_lock(tmp_path, world, monkeypatch, what):
    real_build = rl.build

    def build(*a, **k):
        if what == "layout":
            raise rl.LayoutRefused("hour x: 2 print(s) of MEMBER pool(s) have a null virtual_quote_reserves")
        st = real_build(*a, **k)
        st["v0_missing_days_over" if what == "v0" else "mixed_null_txi_slots"] = ["2026-10-17"] if what == "v0" else 3
        return st

    monkeypatch.setattr(rl, "build", build)
    reasons = refused(mk(tmp_path, world), mkdeps(world), "structure pre-check (before the lock)")
    assert any({"v0": "missing V0", "mixed": "tx_index", "layout": "null virtual_quote_reserves"}[what] in r for r in reasons)
    assert not (tmp_path / "out" / "scratch").exists()  # the pre-check scratch is removed even on a refusal


def test_scratch_left_by_a_killed_run_is_cleaned_before_the_lock(tmp_path, world):
    paths = mk(tmp_path, world)
    stale = paths.out_dir / "scratch" / "ut"
    stale.mkdir(parents=True)
    (stale / "junk.parquet").write_text("x" * 100)  # an OOM-killed run skipped its `finally`; no READS line exists
    rr.read(paths, mkdeps(world))
    assert not (paths.out_dir / "scratch").exists()


def test_precheck_never_touches_the_scratch_of_a_started_read(tmp_path, world):
    paths = mk(tmp_path, world)
    plan, _, reasons = rr.preflight(paths, mkdeps(world), rr.now_utc())
    assert reasons == []
    rr.ledger_event(paths.out_dir, "started", token="t")
    (paths.out_dir / "scratch").mkdir()
    with pytest.raises(rr.ReadRefused, match="started line exists"):
        rr.structure_precheck(paths, plan, mkdeps(world))
    assert (paths.out_dir / "scratch").exists()


# ---- R10: A3 monitor record ---------------------------------------------------------------------------------------------------------------------
def a3_file(world, name, **d):
    body = {"schema": "r4b_a3_v1", "checked_from": "2026-10-16T00:00:00Z", "checked_through": "2026-10-16T23:00:00Z", "halts": []}
    body.update(d)
    f = world["rec"] / f"a3_{name}.json"
    f.write_text(json.dumps(body))
    return {"EXP027_A3_RECORD": f"{f}@{sha(f)}"}


# W0 = hkey(1) = 2026-10-16T04, E = hkey(5) = 2026-10-16T08
@pytest.mark.parametrize("at", ["2026-10-16T04:00:00Z", "2026-10-16T06:30:00Z", "2026-10-16T08:00:00Z"])
def test_a3_halt_inside_w0_e_is_not_decidable_without_opening_outcome_rows(tmp_path, world, monkeypatch, at):
    def no(*a, **k):
        raise AssertionError("an outcome row was opened")

    monkeypatch.setattr(rr, "run_deciding", no)
    paths = mk(tmp_path, world, **a3_file(world, "in" + at[11:13], halts=[{"at": at, "reason": "structure"}]))
    res = rr.read(paths, mkdeps(world))
    assert res["verdict"] == "NOT_DECIDABLE" and res["reasons"][0].startswith("a3_halt") and res["cells"] == {}
    assert [x["event"] for x in rr.reads_events(paths.out_dir)] == ["started", "completed"]


@pytest.mark.parametrize("at", ["2026-10-16T03:59:59Z", "2026-10-16T08:00:01Z"])
def test_a3_halt_outside_the_window_does_not_matter(tmp_path, world, at):
    res = rr.read(mk(tmp_path, world, **a3_file(world, "out" + at[11:13] + at[17:19], halts=[{"at": at}])), mkdeps(world))
    assert res["verdict"] in ("PASS", "FAIL")


def test_a3_record_must_cover_the_window_and_parse(tmp_path, world):
    refused(mk(tmp_path, world, **a3_file(world, "late_from", checked_from="2026-10-16T05:00:00Z")), mkdeps(world), "A3 record covers")
    refused(mk(tmp_path, world, **a3_file(world, "early_to", checked_through="2026-10-16T07:00:00Z")), mkdeps(world), "A3 record covers")
    refused(mk(tmp_path, world, **a3_file(world, "badhalt", halts=[{"reason": "no time"}])), mkdeps(world), "halt without a parsable")
    refused(mk(tmp_path, world, **a3_file(world, "nohalts", halts=None)), mkdeps(world), "A3 record needs")
    refused(mk(tmp_path, world, EXP027_A3_RECORD=f"{world['rec'] / 'a3.json'}@{'0' * 64}"), mkdeps(world), "A3 record")
    refused(mk(tmp_path, world, EXP027_A3_RECORD=None), mkdeps(world), "EXP027_A3_RECORD: matches 0 times")


# ---- R11: mint accounts, deny by default --------------------------------------------------------------------------------------------------------
def test_mint_strand_class_deny_by_default():
    spl, t22 = rr.SPL_TOKEN_PROGRAM, rr.TOKEN_2022_PROGRAM
    ok = {"fetch_ok": True, "owner_program": spl}
    assert rr.mint_is_strand_class(ok) is False
    assert rr.mint_is_strand_class({**ok, "freeze_authority": ""}) is False  # null / empty authority is none
    assert rr.mint_is_strand_class({**ok, "freeze_authority": "F"}) is True
    assert rr.mint_is_strand_class({**ok, "fetch_ok": False}) is True and rr.mint_is_strand_class({**ok, "fetch_ok": "yes"}) is True
    assert rr.mint_is_strand_class({"fetch_ok": True}) is True  # no owner_program
    assert rr.mint_is_strand_class({"fetch_ok": True, "owner_program": "SomeOtherProgram"}) is True
    assert rr.mint_is_strand_class(None) is True and rr.mint_is_strand_class("x") is True and rr.mint_is_strand_class({}) is True
    t = {"fetch_ok": True, "owner_program": t22}
    assert rr.mint_is_strand_class({**t, "token2022_extensions": []}) is False  # no extensions
    assert rr.mint_is_strand_class(t) is True  # extensions unknown (field missing)
    assert rr.mint_is_strand_class({**t, "token2022_extensions": "metadataPointer"}) is True  # malformed
    assert rr.mint_is_strand_class({**t, "token2022_extensions": ["metadataPointer"]}) is False  # census allowlist: metadataPointer + tokenMetadata
    assert rr.mint_is_strand_class({**t, "token2022_extensions": ["metadataPointer"]}, frozenset({"metadataPointer"})) is False
    assert rr.mint_is_strand_class({**t, "token2022_extensions": ["metadataPointer", "transferHook"]}, frozenset({"metadataPointer"})) is True  # EVERY extension must be allowed
    assert rr.mint_is_strand_class({**t, "token2022_extensions": ["metadataPointer"], "freeze_authority": "F"}, frozenset({"metadataPointer"})) is True
    assert rr.TOKEN2022_EXT_ALLOWLIST == frozenset({"metadataPointer", "tokenMetadata"})  # from the outcome-blind census (295/300 mints)


def test_census_allowlist_default_behaviour():
    t = {"fetch_ok": True, "owner_program": rr.TOKEN_2022_PROGRAM, "freeze_authority": None}
    assert rr.mint_is_strand_class({**t, "token2022_extensions": ["metadataPointer", "tokenMetadata"]}) is False  # the census shape
    assert rr.mint_is_strand_class({**t, "token2022_extensions": ["tokenMetadata", "metadataPointer"]}) is False
    for bad in ("transferFeeConfig", "permanentDelegate", "transferHook", "defaultAccountState", "nonTransferable", "pausableConfig"):
        assert rr.mint_is_strand_class({**t, "token2022_extensions": ["metadataPointer", "tokenMetadata", bad]}) is True
    assert rr.mint_is_strand_class({**t, "token2022_extensions": ["metadataPointer", "tokenMetadata"], "freeze_authority": "F"}) is True
    spl = {"fetch_ok": True, "owner_program": rr.SPL_TOKEN_PROGRAM, "freeze_authority": None, "token2022_extensions": []}
    assert rr.mint_is_strand_class(spl) is False and rr.mint_is_strand_class({**spl, "freeze_authority": "F"}) is True


def test_mint_account_schema_needs_fetch_slot(tmp_path, world):
    f = world["rec"] / "m_noslot.json"
    f.write_text(json.dumps({"mints": {}}))
    refused(mk(tmp_path, world, EXP027_MINT_ACCOUNTS=f"{f}@{sha(f)}"), mkdeps(world), "fetch_slot")


def test_missing_mints_are_strand_class_and_counted_in_the_result(tmp_path, world):
    f = world["rec"] / "m_half.json"
    d = json.loads((world["rec"] / "mints.json").read_text())
    d["mints"] = {m: r for m, r in d["mints"].items() if not m.startswith("M2")}  # three mints absent from the record
    f.write_text(json.dumps(d))
    res = rr.read(mk(tmp_path, world, EXP027_MINT_ACCOUNTS=f"{f}@{sha(f)}"), mkdeps(world))
    ma = res["mint_accounts"]
    assert ma["fetch_slot"] == 1 and ma["mints_in_file"] == len(d["mints"]) and ma["failed_fetch_share"] == pytest.approx(1 / len(d["mints"]))
    assert 0 < ma["traded_mints_missing_from_record"] <= 3 and ma["traded_trades_on_missing_mints"] > 0
    assert res["strand_class_trades"] >= ma["traded_trades_on_missing_mints"]


# ---- R1 (r4b_book.round_trip entry guard is claude/r4b-entry-guard): read-level test that passes once it lands -------------------------------------
def test_non_positive_entry_state_is_a_total_loss_not_a_win():
    stake = FZ.STAKE_LAMPORTS
    t = trip()
    t["ent"] = {1.3: [np.array([-0.9 * 77e9, 0.0, 77e9]), np.array([2.5e14, 2.5e14, -1.0]), np.zeros(3), np.zeros(3)]}
    pv = rr.price_variant(t, 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    full = -stake - 505_000 - FZ.RENT_STRANDED
    assert pv["filled"].all() and pv["stranded"].all() and (pv["nofail"] == full).all()  # not +97 SOL


# ======================================================================================================================================================
# #680 guards (C2/C7/C8) in the deciding path, E1 modes, report-only tie / null-txi counts (QP #681)
# ======================================================================================================================================================
def test_deciding_path_runs_with_c8_on(tmp_path, world, monkeypatch):
    assert FZ.K_TOL == 1e-3
    calls = []
    orig = bk.round_trip

    def spy(*a, **k):
        calls.append(k.get("k_tol", FZ.K_TOL))  # the default is what the book uses when the caller does not pass k_tol
        return orig(*a, **k)

    monkeypatch.setattr(bk, "round_trip", spy)
    res = rr.read(mk(tmp_path, world), mkdeps(world))
    assert calls and all(k == FZ.K_TOL and k is not None for k in calls)
    for name, c in res["cells"].items():  # every cell carries the invalid counts by cause
        assert set(c["exit_invalid_by_cause"]) == {"C2", "C7", "C8"} and "entry_invalid" in c and "exit_invalid" in c, name
        assert c["exit_invalid"] == sum(c["exit_invalid_by_cause"].values())


def test_c8_k_fall_is_exit_invalid_and_stranded_in_price_variant():
    stake = FZ.STAKE_LAMPORTS
    tr = trip(Bx=np.array([2.5e14, 2.5e14 * (1 - 5e-3), 2.5e14 * (1 - 5e-4)]))  # k falls 0.5 % (C8), 0.05 % (inside the tolerance)
    pv = rr.price_variant(tr, 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    assert pv["exit_cause"].tolist() == [0, 8, 0] and pv["exit_invalid"].tolist() == [False, True, False]
    assert pv["nofail"][1] == -stake - 505_000 - FZ.RENT_STRANDED and pv["stranded"][1]
    cs = rr.cell_stats(pv["flat"], np.array([1, 2, 3]) * 3600 + 1_800_000_000, np.arange(3), 1_800_000_000, pv)
    assert cs["exit_invalid_by_cause"] == {"C2": 0, "C7": 0, "C8": 1} and cs["entry_invalid"] == 0 and cs["exit_invalid"] == 1


def test_k_ratio_near_tolerance_counts_the_band():
    from types import SimpleNamespace as NS

    n = 6
    Q0, B0 = 77e9, 2.5e14
    ratios = np.array([1.0, 1 - 5e-4, 1 - 1e-3, 1 - 1e-6, 1 - 2e-6, 1 - 5e-3])  # band is [1 - 1e-3, 1 - 1e-6): rows 1, 2 and 4
    tr = trip()
    tr["ent"] = {1.3: [np.full(n, Q0), np.full(n, B0), np.zeros(n), np.zeros(n)], 1.9: [np.full(n, Q0), np.full(n, B0), np.zeros(n), np.zeros(n)]}
    tr["spot"], tr["strand_class"], tr["rent_t22"] = np.full(n, Q0 / B0), np.zeros(n, bool), np.zeros(n, bool)
    tr["Qx"], tr["Bx"] = np.full(n, Q0), B0 * ratios
    pnl = {lat: rr.price_variant(tr, lat, send=505_000, rent=0, auto=True, e1_penalty=0.0) for lat in rr.LATS}
    ctx = NS(plan=NS(w0=datetime(2026, 10, 18, tzinfo=timezone.utc)), tr=tr, cell_pnl=pnl, send=505_000, e1p=0.0)
    out = dict(rr.report_steps(ctx))["k_ratio_near_tolerance"]()
    assert out["1.3"]["exits_in_window"] == 3 and out["1.3"]["c8_flagged"] == 1 and out["1.3"]["bought_valid_entry_exits"] == 6


@pytest.fixture(scope="module")
def world_ties(world, tmp_path_factory):
    root = tmp_path_factory.mktemp("world_ties")
    hours = stream(NH)
    ms = s_of(1) + 100
    hours[hkey(1)]["trades"] += [trade("M1c", ms + 50, None, 0, sol=1_000_000_000, sig="t1"), trade("M1c", ms + 50, None, 0, sol=2_000_000_000, sig="t2"),
                                 trade("M1c", ms + 70, None, 0, sig="n1")]  # one tie group (differing content) and one lone null-txi print
    walk = make_walk(root / "walk", hours)
    rc.run(rc.Config(walk, root / "counts", hkey(1), n_blocks=1, a_attempts=1, h0=hkey(0)), sph_fn=sph_ok, canonical_fn=pda_of)
    return {**world, "walk": walk, "counts": root / "counts"}


def test_null_txi_and_tie_report_counts_without_removing_anything(tmp_path, world, world_ties):
    (tmp_path / "a").mkdir()
    base = rr.read(mk(tmp_path / "a", world), mkdeps(world))
    (tmp_path / "b").mkdir()
    paths = mk(tmp_path / "b", world_ties)
    res = rr.read(paths, mkdeps(world_ties))
    d = report(paths.out_dir, "null_txi_ties")["data"]

    def tot(k):
        return sum(v[k] for v in d["by_date"].values())

    assert tot("null_txi_prints") == 3 and tot("tie_groups") == 1 and tot("tie_mints") == 1 and d["tie_mints_total"] == 1
    assert tot("decision_rows_on_tie_mints") >= 1 and tot("attempts_on_tie_mints") <= res["A_attempts"] and tot("top10_picks_on_tie_mints") >= tot("attempts_on_tie_mints")
    b = report(tmp_path / "a" / "out", "null_txi_ties")["data"]  # the no-tie world: zeros
    assert all(v["null_txi_prints"] == 0 and v["tie_groups"] == 0 for v in b["by_date"].values()) and b["tie_mints_total"] == 0
    assert base["A_attempts"] > 0


# ---- E1 modes --------------------------------------------------------------------------------------------------------------------------------------
def test_e1_mode_constant_default_and_exp_line(tmp_path, world):
    assert rr.E1_MODE == "record" and rr.E1_FIXED_LAMPORTS == 1_920_110
    refused(mk(tmp_path, world, EXP027_E1_MODE="B"), mkdeps(world), "EXP027_E1_MODE is 'B', the tool is built for 'record'")


@pytest.mark.parametrize("mode", ["A", "B"])
def test_e1_modes_a_and_b_need_no_record_and_pin_the_mode(tmp_path, world, monkeypatch, mode):
    monkeypatch.setattr(rr, "E1_MODE", mode)
    assert "EXP027_E1_RECORD" not in rr.required_keys()
    refused(mk(tmp_path, world, EXP027_E1_RECORD=None, EXP027_E1_MODE="record"), mkdeps(world), f"EXP027_E1_MODE is 'record', the tool is built for '{mode}'")
    res = rr.read(mk(tmp_path, world, EXP027_E1_RECORD=None, EXP027_E1_MODE=mode), mkdeps(world))
    assert res["e1"]["mode"] == mode and res["e1"]["penalty_lamports"] == (0.0 if mode == "A" else 1_920_110.0)


def test_e1_mode_b_charges_max_of_haircut_and_the_fixed_amount():
    stake = FZ.STAKE_LAMPORTS
    base = rr.price_variant(trip(), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)  # mode A: haircut only
    low = rr.price_variant(trip(), 1.3, send=505_000, rent=0, auto=True, e1_penalty=1.0)
    assert np.allclose(low["nofail"], base["nofail"])  # a charge below the haircut changes nothing
    b = rr.price_variant(trip(), 1.3, send=505_000, rent=0, auto=True, e1_penalty=float(rr.E1_FIXED_LAMPORTS))
    pnl0 = bk.round_trip(np.full(3, float(stake)), *trip()["ent"][1.3][:2], trip()["spot"], trip()["Qx"], trip()["Bx"], 505_000, 0, h_in=0.0, h_out=0.0)[0]
    hair = pnl0 - base["nofail"]
    assert np.allclose(b["nofail"], pnl0 - np.maximum(hair, rr.E1_FIXED_LAMPORTS))  # max(haircut, 1,920,110) per filled trip
    s = rr.price_variant(trip(strand_class=np.array([True, False, False])), 1.3, send=505_000, rent=0, auto=True, e1_penalty=float(rr.E1_FIXED_LAMPORTS))
    assert s["nofail"][0] == -stake - 505_000 - FZ.RENT_STRANDED  # a stranded trip is not charged again


def test_stranded_rent_is_priced_by_the_mints_token_program():
    stake, send = FZ.STAKE_LAMPORTS, 505_000
    assert rr.RENT_STRANDED_T22 == 2_074_080 == (170 + 128) * 6960 and rr.RENT_STRANDED_SPL == 2_039_280 == (165 + 128) * 6960
    assert rr.RENT_REPORT_T22 == 2_074_080 and rr.RENT_REPORT_SPL == 2_039_280
    assert FZ.RENT_STRANDED == FZ.RENT_REPORT == 2_039_280  # the frozen constants are untouched
    base = rr.price_variant(trip(), 1.3, send=send, rent=0, auto=True, e1_penalty=0.0)
    # trade 0 strand-class Token-2022, trade 1 strand-class SPL, trade 2 filled (not strand-class, Token-2022)
    pv = rr.price_variant(trip(strand_class=np.array([True, True, False]), rent_t22=np.array([True, False, True])), 1.3, send=send, rent=0, auto=True, e1_penalty=0.0)
    assert pv["nofail"][0] == -stake - send - 2_074_080
    assert pv["nofail"][1] == -stake - send - 2_039_280
    assert pv["nofail"][2] == base["nofail"][2] and not pv["stranded"][2]  # filled: deciding rent stays 0, whatever the program
    # report-only rent leg: filled trips pay the rent of their program, stranded ones the stranded rent of theirs
    rp = rr.price_variant(trip(strand_class=np.array([True, True, False]), rent_t22=np.array([True, False, True])), 1.3, send=send, rent=FZ.RENT_REPORT, auto=True, e1_penalty=0.0)
    assert rp["nofail"][0] == -stake - send - 2_074_080 and rp["nofail"][1] == -stake - send - 2_039_280
    assert rp["nofail"][2] == base["nofail"][2] - 2_074_080
    rs = rr.price_variant(trip(rent_t22=np.array([False, False, True])), 1.3, send=send, rent=FZ.RENT_REPORT, auto=True, e1_penalty=0.0)
    assert rs["nofail"][1] == base["nofail"][1] - 2_039_280 and rs["nofail"][2] == base["nofail"][2] - 2_074_080
    with pytest.raises(ValueError):
        rr.price_variant(trip(), 1.3, send=send, rent=12345, auto=True, e1_penalty=0.0)


def test_failed_fetch_and_missing_mints_are_priced_at_the_higher_rent(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"fetch_slot": 7, "mints": {"spl": {"fetch_ok": True, "owner_program": rr.SPL_TOKEN_PROGRAM}, "t22": {"fetch_ok": True, "owner_program": rr.TOKEN_2022_PROGRAM,
                                                         "token2022_extensions": ["metadataPointer", "tokenMetadata"]}, "failed": {"fetch_ok": False}, "odd": {"fetch_ok": True, "owner_program": "Other"}}}))
    _, info, t22 = rr.load_mint_accounts({"EXP027_MINT_ACCOUNTS": f"{p}@{sha(p)}"}, [])
    assert t22 == {"spl": False, "t22": True, "failed": True, "odd": True} and info["rent_t22_in_file"] == 3
    assert rr.mint_rent_is_t22(None) is True and rr.mint_rent_is_t22({"fetch_ok": "yes", "owner_program": rr.SPL_TOKEN_PROGRAM}) is True
    # a failed-fetch trip: strand-class and Token-2022 rent, as the read builds the arrays (a mint absent from the file defaults to True)
    stake, send = FZ.STAKE_LAMPORTS, 505_000
    strand = np.array([True, True], bool)
    t22a = np.array([{**t22}.get(m, True) for m in ("failed", "never_seen")], bool)
    pv = rr.price_variant(trip(strand_class=np.append(strand, False), rent_t22=np.append(t22a, False)), 1.3, send=send, rent=0, auto=True, e1_penalty=0.0)
    assert pv["nofail"][0] == pv["nofail"][1] == -stake - send - 2_074_080


# ---- window-record tamper evidence (EXP027-TAMPER-SPEC) on a temporary git repo with an origin/main ref ---------------------------------------------
EXP_REL = "EXP/EXP-027-r4b-walk2-prereg.md"
BODY = "# EXP-027 fixture\n\n```\nEXP027_BOOK_ID: r4-b\nEXP027_K: TO FILL\n```\n\n"
T_REG = 1_800_000_000
W0_OK = ef.hour_key(datetime.fromtimestamp(T_REG, timezone.utc))  # T_REG is exactly on an hour: "at or after" is that same hour
W_STARTED = datetime.fromtimestamp(T_REG + 10_000, timezone.utc)


def gitc(repo, *a, when=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(repo), "GIT_CONFIG_GLOBAL": "/dev/null"}
    if when is not None:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = f"{when} +0000"
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, stdin=subprocess.DEVNULL, env=env, text=True).stdout.strip()


def commit_exp(repo, text, when, msg="exp"):
    (repo / EXP_REL).write_text(text)
    gitc(repo, "add", EXP_REL)
    gitc(repo, "commit", "-q", "-m", msg, when=when)


def app_line(key, val="v", ts="2026-10-18T03:05Z"):
    return f"{key}: {val} (appended {ts})\n"


def publish(w):
    gitc(w["repo"], "update-ref", "refs/remotes/origin/main", "HEAD")


PLACEHOLDER = "*(nothing appended yet)*\n"


@pytest.fixture()
def wrepo(tmp_path):
    """repo with the EXP registered at reg (window record holds one italic placeholder line), the ledger committed after it, and origin/main = HEAD."""
    return build_wrepo(tmp_path, "\n" + PLACEHOLDER)


def build_wrepo(tmp_path, window_text):
    repo = tmp_path / "wrepo"
    (repo / "EXP").mkdir(parents=True)
    (repo / "docs").mkdir()
    gitc(repo, "init", "-q", "-b", "main")
    (repo / "seed").write_text("x")
    gitc(repo, "add", "seed")
    gitc(repo, "commit", "-q", "-m", "seed", when=T_REG - 100)
    commit_exp(repo, BODY + "## Window record\n" + window_text, T_REG)
    reg = gitc(repo, "rev-parse", "HEAD")
    prefix_sha = hashlib.sha256(rr.body_prefix((repo / EXP_REL).read_bytes())).hexdigest()
    (repo / "docs" / "HOLDOUT_LEDGER.md").write_text(f"# ledger\n\nEXP027_REG_COMMIT {reg}\nEXP027_PREFIX_SHA256 {prefix_sha}\n")
    gitc(repo, "add", "docs")
    gitc(repo, "commit", "-q", "-m", "ledger", when=T_REG + 1)
    w = {"repo": repo, "reg": reg, "prefix": prefix_sha, "ledger": repo / "docs" / "HOLDOUT_LEDGER.md"}
    publish(w)
    return w


def append_commit(w, line, when, pub=True):
    p = w["repo"] / EXP_REL
    p.write_text(p.read_text() + line)
    gitc(w["repo"], "add", EXP_REL)
    gitc(w["repo"], "commit", "-q", "-m", "append", when=when)
    if pub:
        publish(w)


def guard(w, started=W_STARTED):
    return rr.window_guard(w["repo"] / EXP_REL, started, w["repo"], w["ledger"])


def must_refuse(w, needle, started=W_STARTED):
    probs, _ = guard(w, started)
    assert any(needle in x for x in probs), (needle, probs)
    return probs


def test_window_guard_accepts_an_append_only_history(wrepo):
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    append_commit(wrepo, app_line("EXP027_K", "3"), T_REG + 200)
    probs, info = guard(wrepo)
    assert probs == [] and info["reg_commit"] == wrepo["reg"] and info["prefix_sha256"] == wrepo["prefix"]
    assert sorted(a["key"] for a in info["appended"]) == ["EXP027_K", "EXP027_W0"] and len(info["exp_sha256"]) == 64


def test_window_guard_refuses_when_w0_is_absent(wrepo):
    must_refuse(wrepo, "EXP027_W0 is absent")


def test_window_guard_refuses_a_w0_that_does_not_match_the_recomputed_hour(wrepo):
    append_commit(wrepo, app_line("EXP027_W0", "2026-10-18T03"), T_REG + 100)
    must_refuse(wrepo, f"!= {W0_OK}")


def test_w0_from_commit_time_ceils_to_the_hour_and_floors_at_earliest_w0(monkeypatch):
    monkeypatch.setattr(rr, "EARLIEST_W0", "2026-10-18T03")
    t = int(datetime(2026, 10, 20, 5, tzinfo=timezone.utc).timestamp())
    assert ef.hour_key(rr.w0_from_commit_time(t)) == "2026-10-20T05"  # on the hour: that hour
    assert ef.hour_key(rr.w0_from_commit_time(t + 1)) == "2026-10-20T06"
    assert ef.hour_key(rr.w0_from_commit_time(t - 1)) == "2026-10-20T05"
    assert ef.hour_key(rr.w0_from_commit_time(int(datetime(2026, 10, 16, 23, 30, tzinfo=timezone.utc).timestamp()))) == "2026-10-18T03"  # floor


GOOD_VAL = {"EXP027_K": "3", "EXP027_E1_RECORD": "/x/e1.json@" + "a" * 64}


@pytest.mark.parametrize("key,ok", [("EXP027_MERGE_INSTANT", True), ("EXP027_K", True), ("EXP027_POWER_160K", True), ("EXP027_DEADLINE_ND", True), ("EXP027_BREACH_1", True), ("EXP027_BREACH_9", True),
                                    ("EXP027_WALK2_JOB_3", True), ("EXP027_MEMBERS_PREW0", True), ("EXP027_BREACH_10", False), ("EXP027_BREACH_0", False), ("EXP027_ROUTE", False),
                                    ("EXP027_SHA256_R4B_READ", False), ("EXP027_WALK2_JOB_4", False), ("EXP027_E1_RECORD", True)])
def test_window_key_allowlist(wrepo, key, ok):
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    append_commit(wrepo, app_line(key, GOOD_VAL.get(key, "x")), T_REG + 200)
    probs = guard(wrepo)[0]
    assert (probs == []) if ok else any("closed Window-record key list" in x for x in probs)


def test_e1_record_key_only_in_record_mode(wrepo, monkeypatch):
    monkeypatch.setattr(rr, "E1_MODE", "A")
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    append_commit(wrepo, app_line("EXP027_E1_RECORD", GOOD_VAL["EXP027_E1_RECORD"]), T_REG + 200)
    must_refuse(wrepo, "EXP027_E1_RECORD, which is not in the closed")


def test_allowlist_has_exactly_the_addendum_keys():
    assert len(rr.WINDOW_KEYS) == 22 and "EXP027_BREACH_9" in rr.WINDOW_KEYS


def test_window_guard_refuses_a_malformed_window_line(wrepo):
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    append_commit(wrepo, "exp027_k: 3 (appended 2026-10-18T03:05Z)\n", T_REG + 200)
    must_refuse(wrepo, "not `KEY: value (appended")


def test_window_guard_refuses_a_body_edit_after_reg(wrepo):
    commit_exp(wrepo["repo"], (wrepo["repo"] / EXP_REL).read_text().replace("TO FILL", "7"), T_REG + 50, "edit body")
    publish(wrepo)
    probs = must_refuse(wrepo, "body prefix sha256")
    assert any("removes or changes" in x for x in probs)


def test_window_guard_refuses_a_deletion_inside_the_window_record(wrepo):
    commit_exp(wrepo["repo"], (wrepo["repo"] / EXP_REL).read_text().replace(PLACEHOLDER, ""), T_REG + 50, "delete")
    publish(wrepo)
    must_refuse(wrepo, "removes or changes")


def test_window_guard_refuses_an_appended_line_later_deleted(wrepo):
    append_commit(wrepo, app_line("EXP027_W0"), T_REG + 100)
    commit_exp(wrepo["repo"], (wrepo["repo"] / EXP_REL).read_text().replace(app_line("EXP027_W0"), ""), T_REG + 200, "delete again")
    publish(wrepo)
    must_refuse(wrepo, "must add exactly one non-empty line and remove none")  # the net diff is empty, the commit is not


def test_window_guard_refuses_two_lines_in_one_commit(wrepo):
    append_commit(wrepo, app_line("EXP027_W0") + app_line("EXP027_K"), T_REG + 100)
    must_refuse(wrepo, "adds 2")


def test_window_guard_refuses_a_duplicate_key(wrepo):
    append_commit(wrepo, app_line("EXP027_W0", "a"), T_REG + 100)
    append_commit(wrepo, app_line("EXP027_W0", "b"), T_REG + 200)
    must_refuse(wrepo, "EXP027_W0 is appended more than once")


def test_window_guard_refuses_a_bad_line_format(wrepo):
    append_commit(wrepo, "EXP027_W0: 2026-10-18T03\n", T_REG + 100)
    must_refuse(wrepo, "not `KEY: value (appended")


def test_window_guard_refuses_a_commit_at_or_after_started(wrepo):
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    append_commit(wrepo, app_line("EXP027_K", "3"), int(W_STARTED.timestamp()))  # equal to started: not earlier
    must_refuse(wrepo, "not earlier than the read's start")
    assert guard(wrepo, W_STARTED + timedelta(seconds=1))[0] == []


def test_window_guard_refuses_head_not_descending_from_reg(wrepo):
    repo = wrepo["repo"]
    gitc(repo, "checkout", "-q", "--orphan", "other")
    gitc(repo, "rm", "-rq", "--cached", ".")
    (repo / "o").write_text("o")
    gitc(repo, "add", "o")
    gitc(repo, "commit", "-q", "-m", "unrelated root", when=T_REG + 5)
    must_refuse(wrepo, "does not descend from the registration commit")


@pytest.mark.parametrize("case", ["bad_reg", "bad_prefix", "reg_absent", "reg_disagree", "reg_short", "reg_long", "prefix_disagree"])
def test_window_guard_refuses_ledger_mismatches(wrepo, case):
    led, reg, pre = wrepo["ledger"], wrepo["reg"], wrepo["prefix"]
    good = led.read_text()
    bad, needle = {
        "bad_reg": (good.replace(reg, "a" * 40), "!= ledger EXP027_REG_COMMIT"),
        "bad_prefix": (good.replace(pre, "b" * 64), "!= ledger EXP027_PREFIX_SHA256"),
        "reg_absent": ("# ledger\n", "EXP027_REG_COMMIT has no occurrence"),
        "reg_disagree": (good + f"EXP027_REG_COMMIT {'c' * 40}\n", "EXP027_REG_COMMIT occurrences disagree"),
        "reg_short": (good.replace(reg, reg[:39]), "EXP027_REG_COMMIT has no occurrence"),
        "reg_long": (good.replace(reg, reg + "0"), "EXP027_REG_COMMIT has no occurrence"),
        "prefix_disagree": (good + f"x EXP027_PREFIX_SHA256: `{'d' * 64}`\n", "EXP027_PREFIX_SHA256 occurrences disagree"),
    }[case]
    led.write_text(bad)
    gitc(wrepo["repo"], "commit", "-qam", "ledger edit", when=T_REG + 2)
    must_refuse(wrepo, needle)


def test_ledger_tokens_are_found_anywhere_in_a_line_and_may_repeat(wrepo):
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    reg, pre = wrepo["reg"], wrepo["prefix"]
    wrepo["ledger"].write_text(f"| Status | counted. `EXP027_REG_COMMIT` {reg} |\n> - registered. EXP027_REG_COMMIT `{reg}`, EXP027_PREFIX_SHA256: `{pre}`\nplaceholder EXP027_REG_COMMIT <sha>\nEXP027_PREFIX_SHA256 {pre}\nXEXP027_REG_COMMIT {'e' * 40}\n")
    gitc(wrepo["repo"], "commit", "-qam", "ledger prose", when=T_REG + 2)
    assert guard(wrepo)[0] == []  # a placeholder and a longer-identifier lookalike are not occurrences


def test_window_guard_refuses_a_missing_or_uncommitted_ledger(wrepo):
    wrepo["ledger"].write_text(wrepo["ledger"].read_text() + "\nedited, not committed\n")
    must_refuse(wrepo, "differs from HEAD")
    wrepo["ledger"].unlink()
    must_refuse(wrepo, "does not exist")


def test_window_guard_refuses_exp_blob_differing_from_origin_main(wrepo):
    append_commit(wrepo, app_line("EXP027_W0"), T_REG + 100, pub=False)  # a well-formed append, but origin/main does not have it
    must_refuse(wrepo, "differs from origin/main")


def test_window_guard_refuses_when_origin_never_added_the_exp(wrepo):
    gitc(wrepo["repo"], "update-ref", "refs/remotes/origin/main", f"{wrepo['reg']}~1")
    must_refuse(wrepo, "no commit on origin/main added")


def _fork(w, name="side"):
    gitc(w["repo"], "checkout", "-q", "-b", name)


def _other_commit(w, fname, when):
    (w["repo"] / fname).write_text(fname)
    gitc(w["repo"], "add", fname)
    gitc(w["repo"], "commit", "-q", "-m", fname, when=when)


def test_unrelated_no_ff_merge_after_an_append_is_accepted(wrepo):
    repo = wrepo["repo"]
    _fork(wrepo)  # forked at the ledger commit, before the append
    _other_commit(wrepo, "unrelated.txt", T_REG + 50)
    gitc(repo, "checkout", "-q", "main")
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100, pub=False)
    gitc(repo, "merge", "-q", "--no-ff", "-m", "merge unrelated", "side", when=T_REG + 150)
    publish(wrepo)
    probs, info = guard(wrepo)
    assert probs == [] and [a["key"] for a in info["appended"]] == ["EXP027_W0"]


def test_merge_whose_first_parent_diff_edits_the_body_is_refused(wrepo):
    repo = wrepo["repo"]
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100, pub=False)
    _fork(wrepo)
    commit_exp(repo, (repo / EXP_REL).read_text().replace("TO FILL", "7"), T_REG + 110, "side edits the body")
    gitc(repo, "checkout", "-q", "main")
    _other_commit(wrepo, "m.txt", T_REG + 120)
    gitc(repo, "merge", "-q", "--no-ff", "-m", "merge body edit", "side", when=T_REG + 130)
    publish(wrepo)
    probs = must_refuse(wrepo, "body prefix sha256")
    assert any("removes" in x for x in probs)


def test_merge_whose_first_parent_diff_adds_one_valid_line_is_accepted(wrepo):
    repo = wrepo["repo"]
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100, pub=False)
    _fork(wrepo)
    append_commit(wrepo, app_line("EXP027_K", "3"), T_REG + 110, pub=False)
    gitc(repo, "checkout", "-q", "main")
    _other_commit(wrepo, "m.txt", T_REG + 120)
    gitc(repo, "merge", "-q", "--no-ff", "-m", "merge one line", "side", when=T_REG + 130)
    publish(wrepo)
    probs, info = guard(wrepo)
    assert probs == [] and sorted(a["key"] for a in info["appended"]) == ["EXP027_K", "EXP027_W0"]


def test_window_guard_refuses_an_addition_before_the_heading(wrepo):
    commit_exp(wrepo["repo"], (wrepo["repo"] / EXP_REL).read_text().replace("```\n\n## Window", "```\n" + app_line("EXP027_W0") + "\n## Window"), T_REG + 50, "insert above")
    publish(wrepo)
    probs = must_refuse(wrepo, "body prefix sha256")
    assert any("not an addition after" in x for x in probs)


def test_exp_lines_strip_the_append_suffix_and_count_once():
    text = BODY.replace("EXP027_K: TO FILL\n", "") + "## Window record\n\n" + app_line("EXP027_K", "3")
    v, probs = rr.exp_lines(text)
    assert v["EXP027_K"] == "3" and not any("EXP027_K" in x for x in probs)
    dup = BODY.replace("TO FILL", "2") + "## Window record\n\n" + app_line("EXP027_K", "3")
    assert any("EXP027_K: matches 2 times" in x for x in rr.exp_lines(dup)[1])  # a pin in the body cannot be re-set in the window record


def test_preflight_refuses_on_window_guard(tmp_path, world):
    refused(mk(tmp_path, world), mkdeps(world, window_fn=lambda e, l, st: (["window record: HEAD does not descend from the registration commit"], {})), "window record")


def test_started_line_records_the_exp_sha256_and_registration_commit(tmp_path, world):
    paths = mk(tmp_path, world)
    rr.read(paths, mkdeps(world))
    st = [e for e in rr.reads_events(paths.out_dir) if e["event"] == "started"][0]
    assert st["reg_commit"] == "r" * 40 and st["exp_sha256"] == "e" * 64 and st["prefix_sha256"] == "p" * 64 and st["appended"] == []


def test_default_window_fn_and_ledger_constants():
    assert rr.Paths(Path("w")).ledger == rr.ROOT / "docs" / "HOLDOUT_LEDGER.md"
    probs, _ = rr.Deps().window_fn(Path("/nonexistent/EXP.md"), Path("/nonexistent/ledger.md"), W_STARTED)
    assert probs and "not inside the repo" in probs[0]
    assert rr.LEDGER_REG_RE.pattern == r"(?<![A-Za-z0-9_])EXP027_REG_COMMIT[ :]+`?([0-9a-f]{40})(?![0-9a-f])`?"
    assert rr.LEDGER_PREFIX_RE.pattern == r"(?<![A-Za-z0-9_])EXP027_PREFIX_SHA256[ :]+`?([0-9a-f]{64})(?![0-9a-f])`?"


# ---- exact tries key, repo-root pins ---------------------------------------------------------------------------------------------------------------
def test_tries_key_is_an_exact_match():
    m, k = rr.tries_entry_matches, "r4b_exp027_family"
    assert m(json.dumps({"config": {"key": k}, "tool": "x"}), k)
    assert not m(json.dumps({"key": k}), k) and not m(json.dumps({"data_key": k}), k)  # config.key only
    assert not m(json.dumps({"config": {"key": k + "_x"}}), k) and not m(json.dumps({"note": "see " + k}), k)  # substring of a value
    assert not m(k + " not json", k) and not m("[]", k) and not m(json.dumps({"config": k}), k)


def test_pinned_file_resolves_relative_paths_against_the_repo_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "rec").mkdir(parents=True)
    f = root / "rec" / "a.txt"
    f.write_text("a")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(rr, "ROOT", root)
    monkeypatch.chdir(elsewhere)
    probs: list[str] = []
    assert rr.pinned_file(f"rec/a.txt@{sha(f)}", "x", probs) == f and probs == []  # absent from the current directory, found under the root
    assert rr.pinned_file(f"{f}@{sha(f)}", "x", probs) == f
    (elsewhere / "rec").mkdir()
    (elsewhere / "rec" / "a.txt").write_text("other")  # a same-named file in the cwd must not win
    assert rr.pinned_file(f"rec/a.txt@{sha(f)}", "x", probs) == f and probs == []


# ---- Token-2022 vs SPL rent through every stranded route (#684 nit) -------------------------------------------------------------------------------
def test_rent_split_through_entry_invalid_exit_invalid_and_auto_stranded():
    stake, send, t22 = FZ.STAKE_LAMPORTS, 505_000, np.array([True, False, True])
    want = np.where(t22, rr.RENT_STRANDED_T22, rr.RENT_STRANDED_SPL)
    ent = trip()["ent"][1.3]
    bad_ent = trip(ent={1.3: [np.full(3, 1e9), ent[1], ent[2], ent[3]]}, rent_t22=t22)  # entry_invalid: Qe below V_LO
    pv = rr.price_variant(bad_ent, 1.3, send=send, rent=0, auto=True, e1_penalty=0.0)
    assert pv["entry_invalid"].all() and pv["stranded"].all() and np.allclose(pv["nofail"], -stake - send - want)
    pv = rr.price_variant(trip(Qx=np.full(3, np.nan), rent_t22=t22), 1.3, send=send, rent=0, auto=True, e1_penalty=0.0)  # exit_invalid: a missing exit state (C2)
    assert pv["exit_invalid"].all() and not pv["entry_invalid"].any() and np.allclose(pv["nofail"], -stake - send - want)
    big = 2_000_000_000  # auto-stranded: the simulated proceeds are below one send fee
    pv = rr.price_variant(trip(rent_t22=t22), 1.3, send=big, rent=0, auto=True, e1_penalty=0.0)
    assert pv["stranded"].all() and not pv["entry_invalid"].any() and not pv["exit_invalid"].any() and np.allclose(pv["nofail"], -stake - big - want)
    pr = rr.price_variant(bad_ent, 1.3, send=send, rent=FZ.RENT_REPORT, auto=True, e1_penalty=0.0)  # report-only rent leg: stranded rows pay their program's stranded rent, once
    assert np.allclose(pr["nofail"], -stake - send - want)


def test_rent_t22_trades_count_is_in_the_deciding_record(tmp_path, world):
    res = rr.read(mk(tmp_path, world), mkdeps(world))
    assert isinstance(res["rent_t22_trades"], int) and 0 <= res["rent_t22_trades"] <= res["A_attempts"] and res["A_attempts"] > 0


# ---- Addendum 2: G value formats, H line endings, I reg placeholder, L --check-window ---------------------------------------------------------------
SHA = "a" * 64


@pytest.mark.parametrize("key,val,ok", [
    ("EXP027_W0", "2026-10-18T03:00Z", False), ("EXP027_W0", "2026-10-18T3", False), ("EXP027_W0", "2026-13-18T03", False),
    ("EXP027_K", "3", True), ("EXP027_K", "0", False), ("EXP027_K", "03", False), ("EXP027_K", "k=3", False), ("EXP027_K", "-1", False),
    ("EXP027_A3_RECORD", f"/a/b.json@{SHA}", True), ("EXP027_A3_RECORD", "/a/b.json", False), ("EXP027_A3_RECORD", f"/a/b.json@{SHA[:63]}", False), ("EXP027_A3_RECORD", f"@{SHA}", False),
    ("EXP027_MINT_ACCOUNTS", f"/m.json@{SHA}", True), ("EXP027_MINT_ACCOUNTS", f"/m.json@{SHA} (job 12)", False),
    ("EXP027_E1_RECORD", f"/e.json@{SHA}", True),
    ("EXP027_INPUT_VIEWS", f"/a@{SHA}; /b@{SHA}", True), ("EXP027_INPUT_VIEWS", f"/a@{SHA};", False), ("EXP027_INPUT_VIEWS", f"/a@{SHA}, /b@{SHA}", False),
    ("EXP027_FEE_ARM", "NONE", True), ("EXP027_FEE_ARM", f"/f.json@{SHA}", True), ("EXP027_FEE_ARM", "none", False), ("EXP027_FEE_ARM", "/f.json", False),
])
def test_window_value_formats(wrepo, key, val, ok):
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    append_commit(wrepo, app_line(key, val), T_REG + 200)
    probs = guard(wrepo)[0]
    assert (probs == []) if ok else any(key in x and "value" in x for x in probs), probs


def test_window_guard_refuses_a_carriage_return(wrepo):
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    p = wrepo["repo"] / EXP_REL
    with p.open("a", newline="") as f:
        f.write("EXP027_K: 3 (appended 2026-10-18T03:05Z)\r\n")
    gitc(wrepo["repo"], "commit", "-qam", "crlf", when=T_REG + 200)
    publish(wrepo)
    must_refuse(wrepo, "carriage return")


def test_window_guard_refuses_a_missing_final_lf(wrepo):
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    p = wrepo["repo"] / EXP_REL
    p.write_text(p.read_text() + "EXP027_K: 3 (appended 2026-10-18T03:05Z)")
    gitc(wrepo["repo"], "commit", "-qam", "no lf", when=T_REG + 200)
    publish(wrepo)
    probs = must_refuse(wrepo, "does not end with a line feed")
    assert any("end-of-file newline" in x for x in probs)


@pytest.mark.parametrize("text", ["\nsome plain text\n", "\n" + PLACEHOLDER + "EXP027_W0: 2026-10-18T03 (appended 2026-10-16T23:00Z)\n", "\n*open italic\n", "\n*ok*\n\ntext after\n"])
def test_window_guard_refuses_non_placeholder_text_at_reg(tmp_path, text):
    must_refuse(build_wrepo(tmp_path, text), "not an italic placeholder")


REAL_PLACEHOLDER = '*(Empty until registration. The last section of this file; nothing follows it. The manager appends one line per commit, in the form `EXP027_KEY: value (appended YYYY-MM-DDTHH:MMZ)`, dated with `date -u`, from the merge record, the count file and the job records only. Mechanism and read-tool checks: section 0.5.)*\n'  # the one line at the Window record of EXP-027 on claude/exp027-r4b-prereg 3d3406a


def test_window_guard_accepts_the_real_exp027_placeholder_line(tmp_path):
    w = build_wrepo(tmp_path, "\n" + REAL_PLACEHOLDER)
    append_commit(w, app_line("EXP027_W0", W0_OK), T_REG + 100)
    assert guard(w)[0] == []


def test_window_guard_accepts_blank_lines_and_one_multi_line_italic_paragraph_at_reg(tmp_path):
    w = build_wrepo(tmp_path, "\n*(one)*\n*(two)*\n\n")
    append_commit(w, app_line("EXP027_W0", W0_OK), T_REG + 100)
    assert guard(w)[0] == []


def test_window_guard_refuses_an_inner_key_line_inside_an_italic_paragraph_at_reg(tmp_path):
    w = build_wrepo(tmp_path, "\n*(prose\nEXP027_K: 7 (appended 2026-10-16T23:00Z)\nmore)*\n")
    must_refuse(w, "not an italic placeholder line")


def test_window_guard_refuses_two_italic_paragraphs_at_reg(tmp_path):
    w = build_wrepo(tmp_path, "\n*(one)*\n\n*(two)*\n")
    must_refuse(w, "more than one placeholder paragraph")


def test_read_refuses_when_head_is_not_origin_main_but_check_window_does_not(wrepo, tmp_path, world):
    assert rr.head_matches_origin(wrepo["repo"]) == []
    _other_commit(wrepo, "later.txt", T_REG + 300)  # HEAD moves past origin/main
    probs = rr.head_matches_origin(wrepo["repo"])
    assert probs and "!= origin/main" in probs[0]
    refused(mk(tmp_path, world), mkdeps(world, sync_fn=lambda: probs), "!= origin/main")
    assert rr.Deps().sync_fn.__name__ == "<lambda>"
    lines: list[str] = []
    assert rr.check_window_cli(wrepo["repo"] / EXP_REL, wrepo["ledger"], wrepo["repo"], W_STARTED, lines.append) in (0, 1)  # the dry run does not apply the HEAD check
    assert not any("!= origin/main" in x for x in lines)


def test_check_window_cli_pending_violations_and_ok(wrepo):
    lines: list[str] = []
    run = lambda: rr.check_window_cli(wrepo["repo"] / EXP_REL, wrepo["ledger"], wrepo["repo"], W_STARTED, lines.append)
    assert run() == 0 and any(x.startswith("PENDING (1") for x in lines) and "EXP027_W0 is absent" in "\n".join(lines) and not any("VIOLATIONS" in x for x in lines)
    assert any("EXP027_W0 is absent" in x for x in guard(wrepo)[0])  # the read treats PENDING as a refusal
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    lines.clear()
    assert run() == 0 and lines[0].startswith("OK")
    append_commit(wrepo, app_line("EXP027_K", "k=3"), T_REG + 200)
    lines.clear()
    assert run() == 1 and lines[0].startswith("VIOLATIONS (1)")


def test_check_window_cli_ledger_values_pending_before_the_ledger_commit(wrepo):
    wrepo["ledger"].write_text("# ledger\nEXP027_REG_COMMIT <sha>\nEXP027_PREFIX_SHA256 <sha256>\n")
    gitc(wrepo["repo"], "commit", "-qam", "placeholders", when=T_REG + 2)
    append_commit(wrepo, app_line("EXP027_W0", W0_OK), T_REG + 100)
    lines: list[str] = []
    assert rr.check_window_cli(wrepo["repo"] / EXP_REL, wrepo["ledger"], wrepo["repo"], W_STARTED, lines.append) == 0
    assert any(x.startswith("PENDING (2") for x in lines)
    assert guard(wrepo)[0]  # and the read refuses


def test_check_window_flag_is_wired_and_opens_nothing_else(monkeypatch):
    called = []
    monkeypatch.setattr(rr, "check_window_cli", lambda: called.append(1) or 0)
    assert rr.main(["--check-window"]) == 0 and called == [1]
