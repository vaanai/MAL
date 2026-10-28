"""tools/hv6_smoke.py: synthetic tape, StubModel, FakeChain, hand-written decision rows. No API call, no key, no RPC, no /data/mal/arena path.

  * report(): STALE share, chunk-latency p95, refusal shares, tokens/spend from the budget ledger, chain calls/h, PASS / FAIL / INCOMPLETE as pre-declared;
  * the report names no pool, card, price (a marker pool id in the rows never reaches the output);
  * run_smoke(): refuses an existing smoke dir, a dir outside <HV6_ROOT>/smoke, a cap above 0.50, a missing key / RPC pin, MAL_HV4_CHAIN_STATE;
    on a synthetic tape it writes only under the smoke dir (decision log, registry, report.json), never the production registry path;
  * main(): the sha is a required argument and must be 40 hex.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import arena_paper as AP
from tools import hv6_agents as A
from tools import hv6_cards as H
from tools import hv6_register as REG
from tools import hv6_runner as RN
from tools import hv6_smoke as S
from tools.arena_declog import DecisionLog
from tools.arena_state import ArenaState
from tools.test_hv4_runner import FakeChain, T0, trade_rows, write_tape
from tools.test_hv6_runner import script

MIN = 60_000
MARK = "PxxMARKERPOOL" + "9" * 30


@pytest.fixture(autouse=True)
def _env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(H, "READ_ROOTS", (base,))
    monkeypatch.setattr(H, "WRITE_ROOTS", (base,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.delenv("MAL_HV4_CHAIN_STATE", raising=False)


R = SimpleNamespace(AP=AP, A=A, H=H, REG=REG, RN=RN, ArenaState=ArenaState, repo=str(Path(__file__).resolve().parent.parent))


def fill(root: Path, *, takes=2, stale=0, other=10, chunk_ms=3000, n_chunks=40, ok_calls=40, cards=100, asof=0, chain_ref=0):
    """Hand-written decision rows. Every LLM arm gets the same counts."""
    log = DecisionLog(root)
    for arm in S.LLM_ARMS:
        for i in range(takes):
            log.append(arm, T0 + i, {"kind": "entry", "pool": MARK, "card": {"id": MARK}})
        for i in range(stale):
            log.append(arm, T0 + i, {"kind": "skip", "pool": MARK, "reasons": ["STALE", "stale"]})
        for i in range(other):
            log.append(arm, T0 + i, {"kind": "skip", "pool": MARK, "reasons": ["SKIP"]})
        log.append(arm, T0, {"kind": "watch_add", "watch": {"pool": MARK}})
        log.append(arm, T0, {"kind": "watch_end", "id": "x", "why": "expired"})
        for c in range(n_chunks):
            as_of = T0 + c * MIN
            log.append(arm, as_of + chunk_ms, {"kind": "llm_call", "call": "entry", "ids": [MARK, MARK + "b"], "as_of_ms": as_of, "latency_ms": 900.0,
                                               "error": None if c < ok_calls else "http_500"})
    log.append("SCAN", T0, {"kind": "scan", "n_cards": cards, "counts": {}})
    for _ in range(asof):
        log.append("SCAN", T0, {"kind": "as_of_refusal", "pool": MARK, "detail": "x"})
    for _ in range(chain_ref):
        log.append("SCAN", T0, {"kind": "card_refused", "pool": MARK, "reasons": ["chain_stale"]})


def led(root: Path, n=3):
    d = root / "ledgers" / "L"
    d.mkdir(parents=True)
    rows = [{"cost_usd": 0.01, "input_tokens": 1000, "output_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "http_status": 200,
             "latency_ms": 800.0} for _ in range(n)]
    (d / "2026-10-26.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_pass_report_and_no_pool_names(tmp_path):
    fill(tmp_path)
    led(tmp_path)
    rep = S.report(tmp_path, samples_calls_h=[400, 800], chain_credits={"credits": 12, "nested": {"n": 3}, "name": "x"})
    assert rep["verdict"] == "PASS" and rep["stale"] == 0
    assert rep["takes_total"] == 8 and rep["watch_total"] == 4 and rep["watches"]["expired"] == 4
    assert rep["chunk_latency_ms"]["p95"] == 3000.0
    assert rep["tokens"]["input"] == 3000 and abs(rep["spend_usd"] - 0.03) < 1e-9
    assert rep["chain_calls_per_h"] == {"samples": 2, "max": 800, "mean": 600.0, "cap": 1000, "above_cap": False}
    assert rep["helius_credits"]["counter_snapshot"] == {"credits": 12, "nested": {"n": 3}}
    assert MARK not in S.render(rep)


def test_stale_above_5pct_fails(tmp_path):
    fill(tmp_path, takes=2, stale=1, other=7)  # 1 of 10 verdicts + 2 watches per arm
    rep = S.report(tmp_path)
    assert rep["verdict"] == "FAIL" and "stale_share_above_5pct" in rep["fail_reasons"] and rep["stale"] == 4


def test_stale_at_5pct_passes(tmp_path):
    fill(tmp_path, takes=2, stale=1, other=17)  # 1 / 21 per arm = 4.76%
    rep = S.report(tmp_path)
    assert rep["verdict"] == "PASS" and 0 < rep["stale_share"] < 0.05


def test_chunk_p95_above_8s_fails(tmp_path):
    fill(tmp_path, chunk_ms=8500)
    rep = S.report(tmp_path)
    assert rep["verdict"] == "FAIL" and "chunk_latency_p95_above_8s" in rep["fail_reasons"]


def test_chunk_latency_is_the_chunk_maximum_across_arms(tmp_path):
    log = DecisionLog(tmp_path)
    for i, arm in enumerate(S.LLM_ARMS):
        log.append(arm, T0 + (i + 1) * 1000, {"kind": "llm_call", "call": "entry", "ids": [MARK], "as_of_ms": T0, "latency_ms": 100.0, "error": None})
    rep = S.report(tmp_path)
    assert rep["chunk_latency_ms"]["n"] == 1 and rep["chunk_latency_ms"]["max"] == 4000.0


def test_refusals_over_5pct_fail(tmp_path):
    fill(tmp_path, cards=100, asof=6)
    assert "card_refusals_above_5pct_of_attempted" in S.report(tmp_path)["fail_reasons"]
    t2 = tmp_path / "b"
    fill(t2, cards=100, chain_ref=6)
    assert "card_refusals_above_5pct_of_attempted" in S.report(t2)["fail_reasons"]
    t3 = tmp_path / "c"
    fill(t3, cards=100, asof=2, chain_ref=3)  # 5 / 105 = 4.76%: combined, under the limit
    assert "card_refusals_above_5pct_of_attempted" not in S.report(t3)["fail_reasons"]


def test_few_ok_calls_is_incomplete_and_run_error_is_fail(tmp_path):
    fill(tmp_path, n_chunks=5, ok_calls=5)
    assert S.report(tmp_path)["verdict"] == "INCOMPLETE"
    assert S.report(tmp_path, run_error="RuntimeError")["verdict"] == "FAIL"


def test_spend_above_cap_fails(tmp_path):
    fill(tmp_path)
    led(tmp_path, n=60)  # 0.60 USD
    assert "spend_above_cap" in S.report(tmp_path)["fail_reasons"]


def args(smk, **kw):
    kw.setdefault("regime_dir", str(Path(str(smk)).parent / "no-such-regime-dir"))
    return argparse.Namespace(smk=str(smk), hours=1.0, cap_usd=0.5, feed_delay_slots=2, frozen_sha="abcdef1234567", **kw)


@pytest.fixture
def smoke_parent(tmp_path, monkeypatch):
    par = tmp_path / "smoke"
    par.mkdir()
    monkeypatch.setattr(S, "SMOKE_PARENT", str(par))
    monkeypatch.setattr(REG, "register_all", lambda *a, **k: _reg(*a, **k))
    return par


_orig_reg = REG.register_all


def _reg(frozen_sha, clock, path, **k):
    k["verify_sha"] = False
    return _orig_reg(frozen_sha, clock, path, **k)


def test_refusals(tmp_path, smoke_parent, monkeypatch):
    with pytest.raises(S.Refused, match="not_a_child"):
        S.run_smoke(R, args(tmp_path / "elsewhere" / "x"), model=lambda g: None)
    ex = smoke_parent / "old"
    ex.mkdir()
    with pytest.raises(S.Refused, match="smk_exists"):
        S.run_smoke(R, args(ex), model=lambda g: None)
    with pytest.raises(S.Refused, match="probe"):
        S.run_smoke(R, args(smoke_parent / "my-probe"), model=lambda g: None)
    a = args(smoke_parent / "x")
    a.cap_usd = 0.51
    with pytest.raises(S.Refused, match="cap_above"):
        S.run_smoke(R, a, model=lambda g: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(S.Refused, match="api_key"):
        S.run_smoke(R, args(smoke_parent / "y"), chain=FakeChain())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("MAL_HV4_RPC", raising=False)
    with pytest.raises(S.Refused, match="helius"):
        S.run_smoke(R, args(smoke_parent / "z"))
    monkeypatch.setenv("MAL_HV4_RPC", "helius")
    monkeypatch.setenv("MAL_HV4_CHAIN_STATE", "/x")
    with pytest.raises(S.Refused, match="CHAIN_STATE"):
        S.run_smoke(R, args(smoke_parent / "w"))
    assert not (smoke_parent / "x").exists() and not (smoke_parent / "w").exists()


def test_run_on_synthetic_tape_writes_only_under_smk(tmp_path, smoke_parent):
    tape = tmp_path / "tape"
    write_tape(tape, trade_rows())
    smk = smoke_parent / "t1"
    rep = S.run_smoke(R, args(smk), model=lambda g: A.StubModel(script, guard=g, latency_ms=50), chain=FakeChain(), tape_dir=str(tape), live=False, state_start_ms=0,
                      start_ms=T0 + 6 * MIN, end_ms=T0 + 60 * MIN)
    assert rep["kind"] == "hv6_smoke_report" and rep["run_error"] is None, rep
    assert rep["scans"] > 0 and rep["takes_total"] >= 1 and rep["llm_calls"]["n"] >= 1
    assert (smk / "report.json").is_file() and (smk / "smoke-registry.json").is_file() and (smk / "root" / "decisions").is_dir()
    assert sorted(p.name for p in smk.iterdir()) == ["engine-ledger", "report.json", "root", "smoke-registry.json", "smoke-registry.json.lock"]
    assert not any(tmp_path.glob("bots.json"))
    assert MARK not in (smk / "report.json").read_text()


def test_regime_dir_is_linked_read_only_and_counted(tmp_path, smoke_parent):
    tape = tmp_path / "tape"
    write_tape(tape, trade_rows())
    rd = tmp_path / "regime"
    rd.mkdir()
    smk = smoke_parent / "t2"
    rep = S.run_smoke(R, args(smk, regime_dir=str(rd)), model=lambda g: A.StubModel(script, guard=g, latency_ms=50), chain=FakeChain(), tape_dir=str(tape), live=False,
                      state_start_ms=0, start_ms=T0 + 6 * MIN, end_ms=T0 + 20 * MIN)
    assert rep["regime_blocks_visible"] == 0 and (smk / "root" / "regime").is_symlink()
    assert list(rd.iterdir()) == []


def test_main_refuses_a_bad_sha(monkeypatch, capsys):
    assert S.main(["--repo", "x", "--smk", "/x", "--feed-delay-slots", "7", "--expect-sha", "abc"]) == 2
    assert "40 hex" in capsys.readouterr().err


def test_pinned_constants():
    assert not hasattr(S, "FINAL_SHA") and S.CAP_USD_MAX == 0.50
    assert (S.STALE_FAIL_FRAC, S.CHUNK_P95_FAIL_MS, S.RPC_CALLS_PER_H) == (0.05, 8000, 1000)


# --------------------------------------------------------------------------------------------------------------------- production setup path (job #1769 crashed here)

def _origin_clone(tmp_path):
    """A local bare origin with one commit on main, and a clone of it: verify_sha_on_origin runs for real (git fetch of a local path, no network)."""
    import subprocess

    bare, work = tmp_path / "origin.git", tmp_path / "work"
    git = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run([*git, "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    (work / "f").write_text("1")
    subprocess.run([*git, "-C", str(work), "add", "f"], check=True)
    subprocess.run([*git, "-C", str(work), "commit", "-qm", "c"], check=True)
    subprocess.run([*git, "-C", str(work), "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
    return work, subprocess.run([*git, "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def test_production_setup_path_with_the_test_env_unset(tmp_path, monkeypatch):
    """No now_ms, no verify_sha=False, MAL_ARENA_TEST unset (as the wrapper leaves it): the real clock and the real sha verification must work end to end."""
    import json
    import time

    monkeypatch.delenv("MAL_ARENA_TEST", raising=False)
    work, head = _origin_clone(tmp_path)
    par = tmp_path / "smoke"
    par.mkdir()
    monkeypatch.setattr(S, "SMOKE_PARENT", str(par))
    tape = tmp_path / "tape"
    write_tape(tape, trade_rows())
    Rr = SimpleNamespace(**{**vars(R), "repo": str(work)})
    a = args(par / "p1")
    a.frozen_sha = head
    rep = S.run_smoke(Rr, a, model=lambda g: A.StubModel(script, guard=g, latency_ms=50), chain=FakeChain(), tape_dir=str(tape), live=False, state_start_ms=0,
                      end_ms=int(time.time() * 1000) + 5 * MIN)
    lines = [json.loads(x) for x in (par / "p1" / "smoke-registry.json").read_text().splitlines()]
    regs = [r for r in lines if r.get("kind") == "register"]
    assert regs and all(r["time_source"] == "wall_clock" and r["verified_sha"] == head for r in regs)       # the production line, not a test_override
    assert rep["run_error"] is None, rep["run_error"]


def test_production_refusal_is_active_for_the_test_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("MAL_ARENA_TEST", raising=False)
    par = tmp_path / "smoke"
    par.mkdir()
    monkeypatch.setattr(S, "SMOKE_PARENT", str(par))
    with pytest.raises(AP.ArenaRefused, match="test-only overrides"):
        S.run_smoke(R, args(par / "p2"), model=lambda g: None, chain=FakeChain(), tape_dir=str(tmp_path), live=False, start_ms=T0)
