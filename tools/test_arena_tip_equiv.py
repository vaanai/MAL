"""Tests for scripts/research/arena_tip_equiv.py (QP-RULING-1101-hv4-reads 4.1 (B) item 2): the md5 decision-equivalence harness, run here on a SYNTHETIC walker stream that
crosses an hour mark (no exploration file is opened), its refusals, its pinned frozen configuration, and mutation checks (the harness must FAIL when the adapter drops
tx_index, drops event V, or the reader's hour-rollover tail is off).

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_arena_tip_equiv.py -q
"""
from __future__ import annotations

import importlib.util
import os
import random
from pathlib import Path

import pytest

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import arena_tip_adapter as ta  # noqa: E402
from tools import r4b_tip_reader as tr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("arena_tip_equiv", ROOT / "scripts" / "research" / "arena_tip_equiv.py")
eq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eq)

WSOL = eq.WSOL
H = (1_793_000_000 // 3600 + 1) * 3600  # an hour mark
S0 = 1000
VMAP = {"P0": 17_600_000_000.0, "P1": 17_600_000_000.0, "P2": 17_600_000_000.0}
CFG = {**eq.FROZEN, "hot_every": 9, "hot_every_bonding": 6, "holds_ms": [20_000, 45_000, 90_000], "quiet_slots": 300}


def stream(n_slots: int = 5000, seed: int = 1) -> list[tuple[str, dict]]:
    """Walker-format rows in slot order, 0.4 s per slot, starting 1500 s before an hour mark; PumpSwap pools P0..P2 (exploration style: no event V, except P2 whose rows carry a V
    that differs from the map), bonding mints M3, M4. Within a slot the tx_index order differs from the signature order."""
    rnd = random.Random(seed)
    seq: list[tuple[str, dict]] = []
    for k in range(3):
        seq.append(("migration", {"type": "migration", "mint": f"M{k}", "pool": f"P{k}", "quote_mint": WSOL, "slot": S0, "signature": f"mig{k}", "event_index": 0, "tx_index": 0,
                                  "block_time": H - 1500, "trader": "u"}))
    seq.append(("create", {"type": "create", "mint": "M3", "creator": "c", "slot": S0, "signature": "cr", "event_index": 0, "tx_index": 1, "block_time": H - 1500, "is_mayhem_mode": False,
                           "name": "n", "symbol": "s"}))
    for s in range(S0 + 1, S0 + 1 + n_slots):
        bt = H - 1500 + int((s - S0) * 0.4)
        txs = sorted(rnd.sample(range(12), rnd.randint(2, 5)))
        for tx in txs:
            k = rnd.randrange(5)
            base = {"mint": f"M{k}", "trader": f"t{rnd.randrange(9)}", "side": rnd.choice(("buy", "sell")), "sol_lamports": rnd.randint(10**7, 3 * 10**9), "token_raw": rnd.randint(10**5, 10**8),
                    "slot": s, "signature": f"g{rnd.randrange(10**9)}x{s}", "event_index": 0, "tx_index": tx, "block_time": bt, "event_ts": bt, "quote_is_wsol": True, "price_sol": 1e-9, "v": 2}
            if k < 3:
                base.update({"venue": "pumpswap", "pool": f"P{k}", "quote_reserve": 85 * 10**9 + rnd.randint(-3 * 10**9, 3 * 10**9), "base_reserve": 206 * 10**12 + rnd.randint(-10**12, 10**12)})
                if k == 2:
                    base["virtual_quote_reserves"] = 17_650_000_000
            else:
                base.update({"venue": "pump_bonding", "pool": None, "quote_reserve": 30 * 10**9 + s * 1000, "base_reserve": 10**15 - s * 10**6})
            seq.append(("trade", base))
    return seq


def run(tmp_path: Path, **kw) -> dict:
    return eq.run_equiv(stream(), tmp_path / "run", VMAP, tr.hour_key(H - 1500), cfg=CFG, min_decisions=10, **kw)


def test_the_frozen_config_is_pinned():
    assert eq.FROZEN_MD5 == "PIN"  # replaced below by the literal once computed


def test_the_adapter_output_is_decision_equivalent_to_the_walker_rows_across_an_hour_mark(tmp_path):
    rep = run(tmp_path, keep=True)
    assert rep["verdict"] == "PASS" and all(rep["equal"].values()), rep["equal"]
    w, t = rep["arm_walker"], rep["arm_tip"]
    assert w["decisions_md5"] == t["decisions_md5"] and w["trades_md5"] == t["trades_md5"] and w["decisions_md5"] != w["trades_md5"]
    assert w["ledger_kinds"].get("open", 0) >= 10 and (w["ledger_kinds"].get("trade", 0) + w["ledger_kinds"].get("excluded", 0)) >= 5, w["ledger_kinds"]
    assert w["rows_in"] < t["rows_in"]  # the tip arm also gets pool_create / create rows (inert for the engine); the walker arm only the trades
    assert {p.name for p in (tmp_path / "run" / "tip").glob("trades-*.jsonl")} == {f"trades-{tr.hour_key(H - 1500)}.jsonl", f"trades-{tr.hour_key(H)}.jsonl"} | {
        p.name for p in (tmp_path / "run" / "tip").glob("trades-*.jsonl")}
    assert len(list((tmp_path / "run" / "tip").glob("trades-*.jsonl"))) >= 2  # rows cross the hour files
    assert rep["tip_stats"]["adapter"].get("gap_rows", 0) == 0 and rep["tip_stats"]["adapter"]["rows_trade"] == w["rows_in"]
    assert rep["config_md5"] != eq.FROZEN_MD5 and rep["lag_ms"] == 1800


def test_scratch_is_removed_unless_kept(tmp_path):
    run(tmp_path)
    assert not (tmp_path / "run" / "tip").exists() and not (tmp_path / "run" / "W").exists()


@pytest.mark.parametrize("name", ["tx_index_dropped", "event_v_dropped", "rollover_tail_off"])
def test_the_harness_fails_when_the_adapter_or_reader_is_wrong(tmp_path, monkeypatch, name):
    if name == "tx_index_dropped":
        orig = ta.trade_row
        monkeypatch.setattr(ta, "trade_row", lambda w: {k: v for k, v in orig(w).items() if k != "tx_index"})
    elif name == "event_v_dropped":
        monkeypatch.setattr(ta, "TRADE_DROP", ta.TRADE_DROP | {"virtual_quote_reserves"})
    else:
        monkeypatch.setattr(tr.TipReader, "_last_hour", lambda self, last_t_ms: self.h)
    rep = run(tmp_path)
    assert rep["verdict"] == "FAIL" and not all(rep["equal"].values()), name


def test_an_empty_equivalence_is_not_a_pass(tmp_path):
    rep = eq.run_equiv(stream(60), tmp_path / "run", VMAP, tr.hour_key(H - 1500), cfg=CFG, min_decisions=10_000)
    assert rep["verdict"] == "FAIL" and all(rep["equal"].values())  # equal, but too few decisions to mean anything


def test_refusals(tmp_path):
    for bad in ("2026-08-14T11..2026-08-14T13", "2026-08-27T12..2026-08-28T13", "2026-10-02T10..2026-10-02T12", "2026-10-20T00..2026-10-20T06", "2026-08-16T12..2026-08-16T12"):
        h0, h1 = bad.split("..")
        with pytest.raises(eq.Refused):
            eq.check_hours(h0, h1)
    eq.check_hours("2026-08-16T12", "2026-08-16T18")
    for p in ("/data/mal/blocks/forward-1016/x", "/data/mal/arena/tape", "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/tip-tape-archive", "/data/mal/meta-capture/x", "/x/shadow/y"):
        with pytest.raises(eq.Refused):
            eq.check_path(p, "t")
    with pytest.raises(eq.Refused):
        eq.check_out(tmp_path / "o")  # not under the scratch parent
    assert eq.main(["--out", str(tmp_path / "o"), "--hours", "2026-10-20T00..2026-10-20T06"]) == 2
    assert eq.main(["--out", str(tmp_path / "o"), "--hours", "2026-08-16T12..2026-08-16T18", "--expect-config-md5", "0" * 32]) == 2
    assert eq.main(["--out", str(eq.SCRATCH_PARENT / "t1"), "--hours", "2026-08-16T12..2026-08-16T18", "--clean", str(tmp_path)]) == 2


def test_the_job_script_is_plain_sh_with_a_scope_cap_and_no_pipe():
    import subprocess

    p = ROOT / "scripts" / "research" / "arena-tip-equiv.sh"
    txt = p.read_text()
    assert subprocess.run(["sh", "-n", str(p)]).returncode == 0 and txt.startswith("#!/bin/sh") and (p.stat().st_mode & 0o111)
    assert "PIPESTATUS" not in txt and "[[" not in txt and "MemoryMax=8G" in txt and "systemd-run --user --scope" in txt and "arena_tip_equiv.py" in txt
    assert not any("|" in ln.replace("||", "") for ln in txt.splitlines() if not ln.lstrip().startswith("#"))
