"""tools/hv6_review_smoke.py: StubModel only. No API call, no key, no RPC, no /data/mal/arena path.

  * five calls (nightly L, L-nr, P; regime L, P) on a synthetic smoke dir: PASS with a valid empty reply; counts only; no reply text, no pool id in the report;
  * an unparseable reply is a FAIL (a prompt / code fix), a validator REJECT is only a count;
  * INCOMPLETE without an entry card; refusals: dir outside <HV6_ROOT>/smoke, no runner-smoke root, a second run, no key;
  * the production regime directory behind a symlink is never written (the link is removed first);
  * the synthetic block is accepted by the real loader and carries none of the refused keys.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import hv6_agents as A
from tools import hv6_cards as H
from tools import hv6_review as RV
from tools import hv6_review_smoke as S
from tools.haiku_caller import ledger_summary
from tools.hv4_testkit import make_card

COMMIT = "a" * 40
R = SimpleNamespace(A=A, H=H, HRV=RV, ledger_summary=ledger_summary, repo=str(Path(__file__).resolve().parent.parent))
MARK = "PoolMARKER" + "7" * 34


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    real = os.path.realpath(tmp_path)
    monkeypatch.setattr(H, "READ_ROOTS", (real,))
    monkeypatch.setattr(H, "WRITE_ROOTS", (real,))
    par = tmp_path / "smoke"
    par.mkdir()
    monkeypatch.setattr(S, "SMOKE_PARENT", str(par))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def smk_dir(tmp_path, tag="t1", cards=3):
    d = tmp_path / "smoke" / tag
    (d / "root" / "decisions" / "L").mkdir(parents=True)
    rows = [{"seq": i + 1, "arm": "L", "ms": 1, "kind": "entry", "card": make_card(MARK if i == 0 else f"Pool{i:04d}" + "1" * 34, 1_000 + i)} for i in range(cards)]
    (d / "root" / "decisions" / "L" / "2026-10-26.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def args(d):
    return argparse.Namespace(smk=str(d), frozen_sha=COMMIT)


def stub(text="[]"):
    return lambda g: A.StubModel(lambda req: text, guard=g, latency_ms=20)


def test_five_calls_pass_counts_only(tmp_path):
    d = smk_dir(tmp_path)
    rep = S.run_review_smoke(R, args(d), model=stub())
    assert rep["verdict"] == "PASS", rep
    kinds = sorted((c["arm"], c["kind"]) for c in rep["calls"])
    assert kinds == [("L", "nightly"), ("L", "regime"), ("L-nr", "nightly"), ("P", "nightly"), ("P", "regime")]
    assert all(c["accepted"] == 0 and c["rejected"] == 0 and c["exception"] is None for c in rep["calls"])
    assert rep["synthetic_block_accepted_by_loader"] is True
    txt = S.render(rep)
    assert MARK not in txt and "why" not in json.dumps(rep["calls"])


def test_unparseable_reply_fails_but_reject_is_a_count(tmp_path):
    d = smk_dir(tmp_path)
    rep = S.run_review_smoke(R, args(d), model=stub("this is not json"))
    assert rep["verdict"] == "FAIL" and rep["not_ok"]
    d2 = smk_dir(tmp_path, "t2")
    bad_change = json.dumps([{"type": "knob", "target": "min_conviction", "new": 99, "predicate": "ret_15m < -5", "claim": "worse", "evidence_ids": ["t0000"], "why": "x"}])
    rep2 = S.run_review_smoke(R, args(d2), model=stub(bad_change))
    assert rep2["verdict"] == "PASS"
    nightly = [c for c in rep2["calls"] if c["kind"] == "nightly"]
    assert all(c["rejected"] == 1 and c["accepted"] == 0 and c["reject_codes"] for c in nightly)
    assert "min_conviction" not in S.render(rep2)


def test_incomplete_without_cards(tmp_path):
    d = smk_dir(tmp_path, cards=0)
    rep = S.run_review_smoke(R, args(d), model=stub())
    assert rep["verdict"] == "INCOMPLETE" and rep["calls"] == []


def test_refusals(tmp_path, monkeypatch):
    with pytest.raises(S.Refused, match="not_a_child"):
        S.run_review_smoke(R, args(tmp_path / "elsewhere" / "x"), model=stub())
    (tmp_path / "smoke" / "bare").mkdir()
    with pytest.raises(S.Refused, match="no_runner_smoke_root"):
        S.run_review_smoke(R, args(tmp_path / "smoke" / "bare"), model=stub())
    d = smk_dir(tmp_path)
    S.run_review_smoke(R, args(d), model=stub())
    with pytest.raises(S.Refused, match="already_ran"):
        S.run_review_smoke(R, args(d), model=stub())
    d3 = smk_dir(tmp_path, "t3")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(S.Refused, match="api_key"):
        S.run_review_smoke(R, args(d3))


def test_production_regime_dir_is_never_written(tmp_path):
    prod = tmp_path / "prod-regime"
    prod.mkdir()
    d = smk_dir(tmp_path)
    os.symlink(prod, d / "root" / "regime")
    S.run_review_smoke(R, args(d), model=stub())
    assert list(prod.iterdir()) == [] and not (d / "root" / "regime").is_symlink()
    assert len(list((d / "root" / "regime").glob("block-*.json"))) == 1


def test_synthetic_block_has_no_refused_keys_and_one_direct_high():
    b = S.synthetic_block(int(time.time() * 1000) - 10_800_000)
    assert not any(k in b for k in RV.PROBE_KEYS_IN_BLOCK)
    assert RV.direct_high_ids(b) == [b["events"][0]["id"]]


def test_main_refuses_a_bad_sha(capsys):
    assert S.main(["--repo", "x", "--smk", "/x", "--expect-sha", "abc"]) == 2
    assert "expect_sha_not_40_hex" in capsys.readouterr().err
