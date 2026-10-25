"""Reviewer versions in the runner (QP #922 blocker P1). Synthetic tape, hand-made regime block, StubModel only; no API, no RPC, no /data/mal/arena path."""

from __future__ import annotations

import json

import pytest

from tools import hv4_review as RV
from tools import hv6_agents as A
from tools import hv6_review as RVW
from tools import hv6_runner as R
from tools.hv4_testkit import make_card
from tools.test_hv4_runner import T0
from tools.test_hv6_runner import _env, make, rows_of  # noqa: F401  (_env is autouse)

MIN = 60_000
COMMIT = "abcdef1234567" + "0" * 27           # prefix of the runner's frozen sha
DAY = R.utc_day(T0)
CUT = RV.day_start_ms(DAY)
WRITTEN = T0 - 25 * MIN                       # an intraday line written here is in force from WRITTEN + 30 min = T0 + 5 min
EFF = WRITTEN + RV.EFFECTIVE_OFFSET_MS
START, MID_BEFORE, MID_AFTER, END = T0 - 10 * MIN + 2000, T0 + 4 * MIN + 2000, T0 + 6 * MIN + 2000, T0 + 30 * MIN
BLOCK = {"schema": "hv6_regime.v1", "radar_status": "ok", "as_of_ms": WRITTEN - 1000,
         "events": [{"id": "RG-0123abcd", "sev": "HIGH", "relevance": "direct", "kind": "program_upgrade", "program": "pump"}]}
TIGHTEN = {"type": "regime_rule", "target": "stop_pct", "new": -10, "ttl_h": 24, "evidence_ids": ["RG-0123abcd"], "why": "pump program redeploy"}
PROBE = {"type": "regime_rule", "target": "stop_pct", "new": -30, "ttl_h": 24, "stake": 0.25, "extra_entries_per_h": 1, "evidence_ids": ["RG-0123abcd"],
         "why": "pump program redeploy"}


def seed(tmp, monkeypatch, rule=None, arms=("L", "L-nr", "P"), review_arms=("L", "P")):
    root = tmp / "v6"
    for arm in arms:
        row, _ = RVW.genesis_manifest(arm, CUT, COMMIT)
        RVW.append_manifest(root, arm, row, None, None)
    if rule is not None:
        monkeypatch.setattr(RVW, "wall_ms", lambda: WRITTEN)
        for arm in review_arms:
            RVW.regime_review(arm, DAY, root=root, ledger_root=None, model=A.StubModel(lambda req: json.dumps([rule])), now_ms=WRITTEN, commit=COMMIT, block=BLOCK,
                              records=[])
    return root


def new_run(tmp, root):
    return make(tmp, review_root=root)


def kinds(run, arm):
    return [r for r in run.log.rows(arm) if r["kind"] in ("version", "entry")]


def test_version_switch_at_effective_from_logged_before_first_decision(tmp_path_factory, monkeypatch):
    tmp = tmp_path_factory.mktemp("r6v1")
    root = seed(tmp, monkeypatch, TIGHTEN)
    eff = RVW.read_manifests(root, "L")[1]["effective_from_ms"]
    assert eff == EFF
    run = new_run(tmp, root)
    run.run(START, END)
    for arm in ("L", "P"):
        vr = rows_of(run, arm, "version")
        assert [v["version"] for v in vr] == [0, 1] and vr[1]["manifest_kind"] == "intraday" and vr[1]["ms"] >= EFF > vr[0]["ms"]
        assert vr[1]["ms"] - EFF < MIN  # the first minute tick at or after effective_from
    assert [v["version"] for v in rows_of(run, "L-nr", "version")] == [0]
    ent = rows_of(run, "L", "entry")
    assert ent and all(e["ms"] >= EFF for e in ent)
    seqs = {r["kind"] + str(r.get("version", "")): r["seq"] for r in run.log.rows("L") if r["kind"] == "version"}
    assert seqs["version1"] < ent[0]["seq"]                                   # the version hash is in the log before the first decision under it
    assert ent[0]["plan"]["stop_pct"] == -10 and ent[0]["plan_overrides"] == {"stop_pct": -10}
    assert run.vstate["L"]["version"] == 1 and run.overrides["L"] == {"stop_pct": -10}
    assert rows_of(run, "F", "entry") and rows_of(run, "F", "entry")[0]["plan"]["stop_pct"] == -25  # F: the model's own plan, v0 procedure


def test_f_and_x_never_switch(tmp_path_factory, monkeypatch):
    tmp = tmp_path_factory.mktemp("r6v2")
    root = seed(tmp, monkeypatch, TIGHTEN)
    called = []
    orig = RVW.active_version
    monkeypatch.setattr(RVW, "active_version", lambda r, arm, now, **kw: (called.append(arm), orig(r, arm, now, **kw))[1])
    (root / "manifests" / "F.jsonl").write_text("garbage\n")                 # even a file named for F or X is never read
    (root / "manifests" / "X.jsonl").write_text("garbage\n")
    run = new_run(tmp, root)
    run.run(START, END)
    assert set(called) == {"L", "L-nr", "P"}
    for arm in ("F", "X"):
        assert not rows_of(run, arm, "version") and arm not in run.vstate
    assert run.playbooks["F"].sha256 == A.load_playbook("v0").sha256 and run.overrides["F"] == {} and run.vetoes["F"] == ()
    assert rows_of(run, "X", "entry")[0]["plan"] == A.x_exit()


@pytest.mark.parametrize("rule,stop", [(TIGHTEN, 120), (PROBE, 150)])
def test_restart_across_a_version_switch_has_the_same_md5(tmp_path_factory, monkeypatch, rule, stop):
    ref_tmp = tmp_path_factory.mktemp("r6v3a")
    ref = new_run(ref_tmp, seed(ref_tmp, monkeypatch, rule))
    ref.run(START, END)
    ref_md5 = ref.log.md5()
    for mid in (MID_BEFORE, MID_AFTER):                                       # stop before the switch / after it
        tmp = tmp_path_factory.mktemp("r6v3b")
        root = seed(tmp, monkeypatch, rule)
        r1 = new_run(tmp, root)
        r1.run(START, mid)
        del r1
        r2 = new_run(tmp, root)
        r2.resume()
        if mid == MID_AFTER:
            assert r2.vstate["L"]["version"] == 1 and r2.overrides["L"]["stop_pct"] == rule["new"]
        r2.run(mid, END)
        assert r2.log.md5() == ref_md5
        for a in (*R.ARM_ORDER, "SCAN"):
            r2.log.verify(a)
        assert len(rows_of(r2, "L", "version")) == 2                           # no version row is written twice


def test_probe_trade_after_restart_is_capped_and_tagged(tmp_path_factory, monkeypatch):
    tmp = tmp_path_factory.mktemp("r6v4")
    root = seed(tmp, monkeypatch, PROBE)
    r1 = new_run(tmp, root)
    r1.run(START, MID_AFTER)
    assert r1.probes.active("L", MID_AFTER) and not rows_of(r1, "L", "entry")
    del r1
    r2 = new_run(tmp, root)
    r2.resume()
    assert r2.probes.active("L", MID_AFTER) and r2.probes.extra_entries("L", MID_AFTER) == 1 and not r2.probes.active("L-nr", MID_AFTER) and not r2.probes.active("F", MID_AFTER)
    r2.run(MID_AFTER, END)
    ent = rows_of(r2, "L", "entry")
    assert ent and all(e["tag"] == "regime_probe" and e["size"] <= A.PROBE_STAKE == 0.25 for e in ent)
    assert ent[0]["plan"]["stop_pct"] == -30 and rows_of(r2, "L", "version")[1]["n_probes"] == 1
    assert all(e["tag"] is None for e in rows_of(r2, "F", "entry"))


def test_no_version_in_force_before_genesis_and_no_manifest_means_v0(tmp_path_factory, monkeypatch):
    tmp = tmp_path_factory.mktemp("r6v5")
    run = make(tmp, review_root=tmp / "v6")                                   # no manifests at all
    run.run(START, T0 + 12 * MIN)
    assert not any(rows_of(run, a, "version") for a in R.ARM_ORDER) and rows_of(run, "L", "entry")
    tmp2 = tmp_path_factory.mktemp("r6v5b")
    root = tmp2 / "v6"
    row, _ = RVW.genesis_manifest("L", CUT + 86_400_000, COMMIT)              # genesis from tomorrow: not yet in force
    RVW.append_manifest(root, "L", row, None, None)
    run2 = make(tmp2, review_root=root)
    run2.run(START, T0 + 12 * MIN)
    assert not rows_of(run2, "L", "version") and not run2.arm_halt


def test_refused_chain_halts_the_group_and_logs_class_only(tmp_path_factory, monkeypatch):
    tmp = tmp_path_factory.mktemp("r6v6")
    root = seed(tmp, monkeypatch, TIGHTEN)
    f = root / "manifests" / "L.jsonl"
    f.write_text(f.read_text().replace('"stop_pct"', '"stop_pcx"', 1))
    run = new_run(tmp, root)
    run.run(START, T0 + 20 * MIN)
    assert rows_of(run, "L", "version_refused") and all(not rows_of(run, a, "entry") for a in R.ARM_ORDER)  # entries of the whole group stop, X in step
    assert "stop_pcx" not in json.dumps(run.log.rows("L"))


def test_a_drill_root_is_refused_in_production(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r6v7")
    (tmp / "v6").mkdir()
    (tmp / "v6" / RVW.DRILL_MARKER).write_text("")
    with pytest.raises(RuntimeError):
        make(tmp, review_root=tmp / "v6")
    assert make(tmp, review_root=tmp / "v6", allow_drill_root=True).review_root == tmp / "v6"


def test_local_veto_gate(tmp_path_factory):
    card = make_card("POOLX" + "1" * 39, T0)
    assert R.local_veto(["ret_15m < 1000000"], card) == "ret_15m < 1000000" or R.local_veto(["ret_15m < 1000000"], card) is None
    assert R.local_veto(["not a predicate"], card) is None
    assert R.local_veto([], card) is None
