"""Tests for tools/r4b_read.py on SYNTHETIC walker-layout hours in tmp dirs and stubs (boosters, decoder pins, fee-arm record). Nothing here opens /data/mal sealed data.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages:. /data/mal/venv/bin/python -m pytest tools/test_r4b_read.py

Scale: the module patches the pinned constants down (E = W0 + 4 h, N = A = 1, gate n 3, 1 date) so a whole read runs on 8 synthetic hours; the pure tests that need the real
constants (NOT_DECIDABLE N < 20 / A < 125, PASS / FAIL books) restore them with the `real` fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    (rec / "e1.json").write_text(json.dumps({"n": 25, "pnl_gap_lamports_live_minus_sim_mean": -100000.0}))
    (rec / "view.txt").write_text("input view\n")
    mints = {f"M{i}{c}": {"fetch_ok": True} for i in range(NH) for c in "abc"}
    mints["M1a"] = {"fetch_ok": True, "freeze_authority": "FRZ"}  # strand-class
    mints["M1b"] = {"fetch_ok": False}  # a failed fetch is strand-class too
    (rec / "mints.json").write_text(json.dumps({"fetch_slot": 1, "mints": mints}))
    tries = rec / "tries.jsonl"
    tries.write_text(json.dumps({"book": "r4-b", "exp": "EXP-027"}) + "\n")
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
        "EXP027_TRIES_ENTRY": "EXP-027", "EXP027_MINT_ACCOUNTS": f"{rec / 'mints.json'}@{sha(rec / 'mints.json')}", "EXP027_FEE_ARM": "NONE",
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
    d = dict(now_fn=lambda: HOUR0 + timedelta(hours=1 + 4 + 24), clean_fn=lambda p: [], decoder_fn=lambda ref: [], sph_fn=sph_ok, canonical_fn=pda_of,
             boosters_fn=lambda md: [FakeBooster()], models_check_fn=lambda md: [], head_fn=lambda: "deadbeef", stderr=log.append, memory_limit="2GB", threads=2)
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
    assert [e["event"] for e in ev] == ["started", "completed"] and ev[0]["token"] == ev[1]["token"] == res["token"]
    assert (paths.out_dir / rr.RESULT).exists() and ev[1]["result_sha256"] == sha(paths.out_dir / rr.RESULT)
    assert not (paths.out_dir / "scratch").exists()
    assert res["A_attempts"] > 0 and res["N_blocks_with_attempts"] == 1
    assert set(res["cells"]) == {"flat_1.3", "flat_1.9", "press_1.3", "press_1.9"} and all(c["n"] == res["A_attempts"] for c in res["cells"].values())
    assert res["keys"]["ok"] is True and res["keys"]["not_subset_marks"] == 0
    assert res["stack"]["send"] == 505_000 and res["stack"]["rent"] == 0 and res["fee_arm"]["source"] == "505k"
    assert "report_only" in res and "lp_discontinuities" in res["report_only"] and any(k.startswith("rent:") for k in res["report_only"]["variants"])
    assert any(k.startswith("lat4.0:") for k in res["report_only"]["variants"]) and any(k.startswith("send_55k:") for k in res["report_only"]["variants"])
    assert any(k.startswith("no_auto_stranded:") for k in res["report_only"]["variants"])
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
    assert calls.count(("asof_pda_first_print", "asof")) == calls.count(("frozen", "frozen")) == res["mints"]


def test_report_only_asof_vs_frozen_null_bt_and_gap(tmp_path, world):
    res = rr.read(mk(tmp_path, world), mkdeps(world))
    ro = res["report_only"]
    d = ro["asof_vs_frozen"]
    assert "NOT removed" in d["note"] or "NOT" in d["note"]
    assert d["asof_only_entered_top10"] <= res["A_attempts"]
    for name, c in d["pnl_asof_only_trades"].items():
        assert c["n"] + d["pnl_other_trades"][name]["n"] == res["A_attempts"]
    assert set(ro["null_block_time_prints_by_hour"]) <= set(rl.hour_range(rr.H0, hkey(8))) and all(v == 0 for v in ro["null_block_time_prints_by_hour"].values())
    gap = res["keys"]["live_vs_decision_gap_by_mark"]
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

    base = rr.read(mk(tmp_path / "a", world) if (tmp_path / "a").mkdir() is None else None, mkdeps(world))
    monkeypatch.setattr(rf, "mint_features", spy)
    (tmp_path / "b").mkdir()
    res = rr.read(mk(tmp_path / "b", world), mkdeps(world))
    assert res["report_only"]["asof_vs_frozen"]["asof_only_rows"] >= 1 and res["report_only"]["asof_vs_frozen"]["frozen_only_rows"] == 0
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

    monkeypatch.setattr(rr, "run_read", boom)
    with pytest.raises(RuntimeError):
        rr.read(paths, mkdeps(world))
    ev = rr.reads_events(paths.out_dir)
    assert [e["event"] for e in ev] == ["started", "aborted"] and "disk full" in ev[1]["reasons"][0]
    assert not (paths.out_dir / rr.RESULT).exists()
    with pytest.raises(rr.ReadRefused, match="no second run"):
        rr.read(paths, mkdeps(world))
    assert len(rr.reads_events(paths.out_dir)) == 2


def test_structure_refusal_after_lock_is_aborted(tmp_path, world, monkeypatch):
    real_build = rl.build

    def build(*a, **k):
        st = real_build(*a, **k)
        st["v0_missing_days_over"] = ["2026-10-16"]
        return st

    monkeypatch.setattr(rl, "build", build)
    with pytest.raises(rr.ReadRefused, match="v0-missing"):
        rr.read(mk(tmp_path, world), mkdeps(world))
    assert [e["event"] for e in rr.reads_events(tmp_path / "out")] == ["started", "aborted"]


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
    refused(mk(tmp_path, world, EXP027_TRIES_ENTRY="EXP-999"), mkdeps(world), "no entry containing")


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
    r = refused(paths, mkdeps(world), "not sealed")
    assert any(hkey(7) in x and "rows=" in x for x in r)


@pytest.mark.parametrize("bad", [5, 6, 7])
def test_exit_hour_and_extra_trade_hours_must_be_verified(tmp_path, world, bad):
    # E = hkey(5); the allowlist runs to E + 3h: hours 5, 6, 7 (the exit hour and the two extra trade hours) must all be sealed and verified
    walk = make_walk(tmp_path / "w", stream(NH), no_verify=[hkey(bad)])
    refused(mk(tmp_path, {**world, "walk": walk}), mkdeps(world), hkey(bad))


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
    assert flags - {"-h", "--help"} <= {"--walk-dir", "--out-dir", "--counts-dir", "--exp-file", "--models-dir", "--memory-limit", "--threads"}
    for bad in ("--send-fee", "--k", "--w0", "--stop-hours", "--alpha", "--rent", "--latency"):
        with pytest.raises(SystemExit) as ei:
            rr.main(["--walk-dir", str(tmp_path), bad, "1"])
        assert ei.value.code == 2


def test_main_exit_code_on_refusal(tmp_path, world, capsys):
    paths = mk(tmp_path, world, EXP027_ROUTE="PENDING")
    code = rr.main(["--walk-dir", str(world["walk"]), "--out-dir", str(paths.out_dir), "--counts-dir", str(world["counts"]), "--exp-file", str(paths.exp_file)])
    assert code == 2 and "REFUSED:" in capsys.readouterr().err and not paths.out_dir.exists()


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
    p.write_text(json.dumps({"mints": {"ok": {"fetch_ok": True}, "freeze": {"fetch_ok": True, "freeze_authority": "X"}, "ext": {"fetch_ok": True, "token2022_extensions": ["transferFeeConfig"]},
                                       "harmless": {"fetch_ok": True, "token2022_extensions": ["metadataPointer"]}, "failed": {"fetch_ok": False}}}))
    probs: list[str] = []
    got = rr.load_mint_accounts({"EXP027_MINT_ACCOUNTS": f"{p}@{sha(p)}"}, probs)
    assert probs == [] and got == {"ok": False, "freeze": True, "ext": True, "harmless": False, "failed": True}


def test_mark_lookup_unpriced_exit_is_nan():
    marks = [(np.array([1, 1]), np.array([3600, 7200]), np.array([5.0, 6.0]), np.array([7.0, 8.0]))]
    qx, bx = rr.mark_lookup(marks, np.array([1, 1, 2]), np.array([7200, 10800, 3600]))
    assert qx[0] == 6.0 and bx[0] == 8.0 and np.isnan(qx[1]) and np.isnan(qx[2])
    assert np.isnan(rr.mark_lookup([], np.array([1]), np.array([3600]))[0]).all()


def trip(**kw):
    n = 3
    Q0, B0 = 77e9, 2.5e14
    ent = {1.3: [np.full(n, Q0), np.full(n, B0), np.array([0.0, 2.0, 5.0]), np.array([0.0, 1e9, 3e9])]}
    tr = {"ent": ent, "spot": np.full(n, Q0 / B0), "Qx": np.full(n, Q0), "Bx": np.full(n, B0), "strand_class": np.zeros(n, bool)}
    tr.update(kw)
    return tr


def test_price_variant_strand_unpriced_and_e1_penalty():
    stake = FZ.STAKE_LAMPORTS
    base = rr.price_variant(trip(), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    assert not base["stranded"].any() and base["filled"].all()
    st = rr.price_variant(trip(strand_class=np.array([True, False, False])), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    assert st["nofail"][0] == -stake - 505_000 - FZ.RENT_STRANDED and st["nofail"][1] == base["nofail"][1]
    un = rr.price_variant(trip(Qx=np.array([np.nan, 77e9, 77e9])), 1.3, send=505_000, rent=0, auto=True, e1_penalty=0.0)
    assert un["stranded"].tolist() == [True, False, False] and un["nofail"][0] == -stake - 505_000  # (e) stake + buy send, no sell send, no rent
    assert rr.UNPRICED_EXIT_RENT_LAMPORTS == 0 and un["filled"].all()  # the trade is never skipped
    for kw in (dict(Qx=np.array([0.0, 77e9, 77e9])), dict(Bx=np.array([0.0, 2.5e14, 2.5e14])), dict(Bx=np.array([-5.0, 2.5e14, 2.5e14])), dict(Qx=np.array([-1.0, 77e9, 77e9]))):
        u2 = rr.price_variant(trip(**kw), 1.3, send=505_000, rent=0, auto=True, e1_penalty=5e6)
        assert u2["stranded"].tolist() == [True, False, False] and u2["nofail"][0] == -stake - 505_000 and len(u2["nofail"]) == 3
    assert (un["flat"][0] == 0.85 * (-stake - 505_000) - 0.15 * 505_000)  # the fail legs still apply to the unpriced trade
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
    assert rr.H0 == "2026-10-16T03" == rc.H0 and rr.UNIVERSE_EXITS_AT_MARKS_ONLY is True and rr.EARLIEST_W0 == "2026-10-18T01" and rr.SEND_FEE_160K == 160_000 and FZ.SEND_FEE_DECIDING == 505_000 and FZ.RENT_DECIDING == 0
    assert FZ.K_ROUNDING == "round" and FZ.LATENCIES_S == (1.3, 1.9) and rr.EXTRA_TRADE_HOURS == 2
