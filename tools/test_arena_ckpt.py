"""P35 PR 4 (B-T1): engine and ArenaState checkpoint / restore. Synthetic rows only; no tape, no network.

The test of record is decision equivalence. One continuous engine (or state) is run over a tape. A second is checkpointed at an instant, then
restored into a NEW object, and continued over the same rows. The ledger rows (minus engine id, pid and write clocks) and the state digests
must be identical. One restore runs in a fresh process (subprocess).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import arena_paper_ckpt as EC
from tools import arena_state_ckpt as SC_
from tools.arena_paper import StackConfig
from tools.test_arena_paper import _plenty_of_disk, _row, _tape, register_bot, tms  # noqa: F401 - _plenty_of_disk is an autouse fixture

REPO = Path(__file__).resolve().parent.parent
VOLATILE = ("written_ms", "engine_id", "pid", "code_head", "code_dirty")


def mk(tmp: Path, cfg=None, bots=("botA",)) -> EC.EngineCkpt:
    reg = tmp / "bots.json"
    if not reg.exists():
        for b in bots:
            register_bot(b, "abc1234", 0, reg, now_ms=0)
    eng = EC.EngineCkpt(tmp / "ledger", reg, cfg or StackConfig(feed_delay_slots=0, strand_rule="total"), sps_fn=lambda: 0.4,
                        code_state_fn=lambda: ("abc1234", False))
    eng.now_fn = lambda: eng.hw_ms or 0
    return eng


def drive(eng, rows, *, buy=100, sell=200, pool="P1", pid=None):
    """Feed rows; buy right after the row at slot `buy`, sell right after the row at slot `sell` (as test_arena_paper.run_trade). Returns the pid."""
    for r in rows:
        eng.on_row(r)
        if r["slot"] == buy and pid is None and r.get("pool") == pool:
            pid = eng.buy("botA", pool, 0.25, tms(buy), buy, {"why": "t"})
        if pid is not None and r["slot"] == sell and pid in eng.positions and eng.positions[pid].exit_slot is None:
            eng.sell("botA", pid, 1.0, tms(sell), sell, "t")
    return pid


def ledger_md5(tmp: Path) -> tuple[str, list[str]]:
    rows = []
    for p in sorted((tmp / "ledger").rglob("*.jsonl")):
        if p.name.startswith("incidents"):
            continue
        for ln in p.read_text().splitlines():
            r = json.loads(ln)
            if r.get("kind") == "start":
                continue
            rows.append(json.dumps({k: v for k, v in r.items() if k not in VOLATILE}, sort_keys=True))
    return hashlib.md5("\n".join(rows).encode()).hexdigest(), [json.loads(x)["kind"] for x in rows]


def handoff(tmp: Path, rows, cut_slot, **kw):
    """seg 1 over rows before the first row with slot >= cut_slot; checkpoint; a NEW engine restores it and runs the rest."""
    k = next(i for i, r in enumerate(rows) if r["slot"] >= cut_slot)
    e1 = mk(tmp)
    pid = drive(e1, rows[:k], **kw)
    info = e1.checkpoint(tmp / "engine.ckpt", tape_cursor_slot=cut_slot)
    e2 = mk(tmp)
    d = e2.restore(tmp / "engine.ckpt", expect_sha256=info["sha256"])
    assert d == info["state_digest"]
    drive(e2, rows[k:], pid=pid, **kw)
    return e1, e2, info, pid


def continuous(tmp: Path, rows, **kw):
    e = mk(tmp)
    pid = drive(e, rows, **kw)
    return e, pid


def test_open_position_across_the_checkpoint_is_priced_identically(tmp_path):
    rows = _tape(90, 260)
    a, pid_a = continuous(tmp_path / "a", rows)
    e1, e2, info, pid_b = handoff(tmp_path / "b", rows, 150)
    assert info["positions"] == 1  # one open position at the hand-off
    assert pid_a == pid_b and not e2.positions
    ma, kinds = ledger_md5(tmp_path / "a")
    mb, _ = ledger_md5(tmp_path / "b")
    assert ma == mb and kinds.count("trade") == 3 and "excluded" not in kinds
    assert EC.state_digest(a) == EC.state_digest(e2)


def test_pending_sell_across_the_checkpoint(tmp_path):
    rows = _tape(90, 260)
    continuous(tmp_path / "a", rows)
    _, e2, info, _ = handoff(tmp_path / "b", rows, 201)  # the sell was decided at 200; it is priced only once the needed slot arrives
    assert info["positions"] == 1
    assert ledger_md5(tmp_path / "a")[0] == ledger_md5(tmp_path / "b")[0]


def test_late_prints_and_gaps_across_the_checkpoint(tmp_path):
    rows = _tape(90, 260)
    gap = {"type": "gap", "kind": "silence", "prog": "pumpswap", "data_loss": True, "slot": 230, "t_recv_ms": tms(230), "silent_ms": 1_000}
    late = [_row(500, 110, sig="late1"), gap, _row(501, 111, sig="late2"), _row(502, 300, sig="fine")]
    a, _ = continuous(tmp_path / "a", rows)
    for r in late:
        a.on_row(r)
    e1 = mk(tmp_path / "b")
    pid = drive(e1, rows)
    info = e1.checkpoint(tmp_path / "b" / "engine.ckpt")
    e2 = mk(tmp_path / "b")
    e2.restore(tmp_path / "b" / "engine.ckpt", expect_sha256=info["sha256"])
    for r in late:
        e2.on_row(r)
    ma, kinds = ledger_md5(tmp_path / "a")
    assert kinds.count("late_print") == 2 and pid
    assert ma == ledger_md5(tmp_path / "b")[0]
    assert EC.state_digest(a) == EC.state_digest(e2)


def test_data_loss_gap_before_the_checkpoint_still_excludes_after_it(tmp_path):
    rows = _tape(90, 260)
    g = {"type": "gap", "kind": "socket_reconnect", "prog": "pumpswap", "data_loss": True, "slot": 140, "t_recv_ms": tms(140), "down_ms": 2_000}
    rows = rows[:51] + [g] + rows[51:]
    continuous(tmp_path / "a", rows)
    handoff(tmp_path / "b", rows, 160)
    ma, kinds = ledger_md5(tmp_path / "a")
    assert kinds.count("excluded") == 3 and ma == ledger_md5(tmp_path / "b")[0]


def test_trimmed_pools_and_two_pools(tmp_path):
    rows = sorted(_tape(90, 260) + _tape(95, 255, pool="P2"), key=lambda r: (r["slot"], r["pool"]))
    a = mk(tmp_path / "a")
    drive(a, rows)
    a.trim_history()
    e1 = mk(tmp_path / "b")
    pid = drive(e1, [r for r in rows if r["slot"] < 180])
    e1.trim_history()
    a1 = mk(tmp_path / "c")  # the continuous twin trims at the same instant
    drive(a1, [r for r in rows if r["slot"] < 180])
    a1.trim_history()
    info = e1.checkpoint(tmp_path / "b" / "e.ckpt")
    e2 = mk(tmp_path / "b")
    e2.restore(tmp_path / "b" / "e.ckpt")
    rest = [r for r in rows if r["slot"] >= 180]
    drive(e2, rest, pid=pid)
    drive(a1, rest, pid=pid)
    assert EC.state_digest(a1) == EC.state_digest(e2) and info["pools"] == 2
    assert ledger_md5(tmp_path / "c")[0] == ledger_md5(tmp_path / "b")[0]


def test_fresh_process_restore_reproduces_the_digest(tmp_path):
    rows = _tape(90, 260)
    e1 = mk(tmp_path)
    drive(e1, [r for r in rows if r["slot"] < 150])
    info = e1.checkpoint(tmp_path / "ck" / "engine.ckpt")
    p = subprocess.run([sys.executable, "-m", "tools.arena_paper_ckpt", "digest", str(tmp_path / "ck" / "engine.ckpt")], capture_output=True, text=True,
                       cwd=str(REPO))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["state_digest"] == info["state_digest"] and out["positions"] == 1


def test_tampered_or_mismatched_checkpoints_are_refused(tmp_path):
    rows = _tape(90, 260)
    e1 = mk(tmp_path)
    drive(e1, [r for r in rows if r["slot"] < 150])
    path = tmp_path / "engine.ckpt"
    info = e1.checkpoint(path)
    with pytest.raises(EC.CheckpointInvalid, match="sha256"):
        mk(tmp_path).restore(path, expect_sha256="0" * 64)
    with pytest.raises(EC.CheckpointInvalid, match="stack config"):
        mk(tmp_path, cfg=StackConfig(feed_delay_slots=7, strand_rule="total")).restore(path)
    data = path.read_bytes()
    bad = tmp_path / "bad.ckpt"
    bad.write_bytes(data.replace(b'"hw_slot":', b'"hw_slot":1', 1))
    with pytest.raises(EC.CheckpointInvalid):
        mk(tmp_path).restore(bad)
    used = mk(tmp_path)
    used.on_row(rows[0])
    with pytest.raises(EC.CheckpointInvalid, match="already seen"):
        used.restore(path)
    assert mk(tmp_path).restore(path, expect_sha256=info["sha256"]) == info["state_digest"]


def test_recover_after_restore_fails_closed_only_for_positions_the_checkpoint_lacks(tmp_path):
    rows = _tape(90, 260)
    e1 = mk(tmp_path)
    for r in rows[:5]:
        e1.on_row(r)
    info = e1.checkpoint(tmp_path / "early.ckpt")  # before the buy
    drive(e1, rows[5:60])  # seg 1 buys at 100 after the checkpoint, then "crashes" without a new one
    e2 = mk(tmp_path)
    e2.restore(tmp_path / "early.ckpt", expect_sha256=info["sha256"])
    assert e2.recover() == 1 and all(p.restarted for p in e2.positions.values())


def test_continuous_position_stays_continuous_and_recover_adds_nothing(tmp_path):
    _, e2, info, pid = handoff(tmp_path, _tape(90, 140), 120)
    assert pid in e2.positions and e2.positions[pid].restarted is False and e2.recover() == 0


# ---- ArenaState --------------------------------------------------------------------------------------------------------
H0 = 1_794_441_600_000  # 2026-11-12T00:00:00Z
WSOL = "So11111111111111111111111111111111111111112"


def tape_rows() -> list[dict]:
    """Three hours of a synthetic arena tape: two PumpSwap pools (one created in view), a bonding curve, gaps, hb rows."""
    out = [{"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 1, "t_recv_ms": H0 + 1_000, "signature": "pc1", "event_index": 0, "pool": "PA",
            "mint": "MA", "quote_mint": WSOL},
           {"v": 1, "type": "create", "prog": "pumpfun", "slot": 1, "t_recv_ms": H0 + 1_500, "signature": "cr1", "event_index": 0, "mint": "MB",
            "trader": "w0", "creator": "w0", "quote_reserve": 30_000_000_000, "base_reserve": 1_073_000_000_000_000, "name": "b", "symbol": "B"}]
    for i in range(3 * 3600 // 7):
        t = H0 + 2_000 + i * 7_000
        buy = i % 3 != 0
        out.append({"v": 1, "type": "trade", "prog": "pumpswap", "slot": 10 + i * 17, "t_recv_ms": t, "signature": f"sa{i}", "event_index": 0,
                    "venue": "pumpswap", "pool": "PA" if i % 2 else "PZ", "mint": "MA" if i % 2 else None, "quote_mint": WSOL if i % 2 else None,
                    "side": "buy" if buy else "sell", "trader": f"w{i % 11}", "sol_lamports": 100_000_000 + i, "token_raw": 1_000_000_000 + i,
                    "quote_reserve": 50_000_000_000 + i * 1000, "base_reserve": 500_000_000_000_000 - i * 1000, "virtual_quote_reserves": 17_500_000_000,
                    "pool_quote_amount": 99_000_000, "lp_fee": 200_000})
        if i % 5 == 0:
            out.append({"v": 1, "type": "trade", "prog": "pumpfun", "slot": 10 + i * 17, "t_recv_ms": t + 300, "signature": f"sb{i}", "event_index": 0,
                        "venue": "pump_bonding", "mint": "MB", "side": "buy", "trader": f"w{i % 7}", "sol_lamports": 50_000_000, "token_raw": 1_000_000_000,
                        "quote_reserve": 30_000_000_000 + i * 50_000_000, "base_reserve": 1_000_000_000_000_000 - i * 10_000_000})
        if i % 60 == 0:
            out.append({"v": 1, "type": "hb", "prog": "pumpswap", "slot": 10 + i * 17, "t_recv_ms": t + 1, "signature": None, "event_index": None})
    out.append({"v": 1, "type": "gap", "prog": "pumpswap", "slot": 0, "t_recv_ms": H0 + 4_000_000, "signature": None, "event_index": None, "kind": "silence",
                "data_loss": True, "from_ms": H0 + 3_970_000, "to_ms": H0 + 4_000_000, "closed": False})
    out.append({"v": 1, "type": "gap", "prog": "pumpswap", "slot": 0, "t_recv_ms": H0 + 4_010_000, "signature": None, "event_index": None, "kind": "silence",
                "data_loss": True, "from_ms": H0 + 3_970_000, "to_ms": H0 + 4_010_000, "closed": True})
    return sorted(out, key=lambda r: r["t_recv_ms"])


def write_tape(d: Path, compact_hours: int) -> None:
    from datetime import datetime, timezone

    from tools.arena_zst import compact_file

    d.mkdir(parents=True, exist_ok=True)
    for r in tape_rows():
        name = "tape-" + datetime.fromtimestamp(r["t_recv_ms"] // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"
        with open(d / name, "a") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    for p in sorted(d.glob("tape-*.jsonl"))[:compact_hours]:
        compact_file(p)


def step_to(st, until: int, step_ms: int = 60_000, t: int = H0) -> None:
    while t <= until:
        st.poll(until_ms=t)
        t += step_ms


END = H0 + 3 * 3_600_000 + 60_000


@pytest.mark.parametrize("cut", [H0 + 1_800_000 + 30_000, H0 + 5_400_000 + 30_000, H0 + 9_000_000 + 30_000])
def test_state_checkpoint_mid_zst_and_mid_jsonl_resumes_identically(tmp_path, cut):
    pytest.importorskip("tools.arena_zst")
    if not __import__("shutil").which("zstd"):
        pytest.skip("zstd CLI not on PATH")
    d = tmp_path / "tape"
    write_tape(d, compact_hours=2)
    a = SC_.StateCkpt(d, start_ms=0)
    step_to(a, END)
    b1 = SC_.StateCkpt(d, start_ms=0)
    step_to(b1, cut)
    info = b1.checkpoint(tmp_path / "state.ckpt", cut_ms=cut)
    b2 = SC_.StateCkpt(d, start_ms=0)
    assert b2.restore(tmp_path / "state.ckpt", expect_sha256=info["sha256"]) == info["state_digest"]
    step_to(b2, END, t=cut + 60_000)
    assert b2.state_digest() == a.state_digest()
    assert b2.snapshot("PA") == a.snapshot("PA") and b2.snapshot("MB") == a.snapshot("MB")
    assert a.loss_spans["pumpswap"] == 1 and a.counters["rows"] > 1000


def test_state_fresh_process_restore_and_tamper(tmp_path):
    if not __import__("shutil").which("zstd"):
        pytest.skip("zstd CLI not on PATH")
    d = tmp_path / "tape"
    write_tape(d, compact_hours=1)
    s = SC_.StateCkpt(d, start_ms=0)
    step_to(s, H0 + 4_000_000)
    info = s.checkpoint(tmp_path / "s.ckpt")
    p = subprocess.run([sys.executable, "-m", "tools.arena_state_ckpt", "digest", str(tmp_path / "s.ckpt"), "--tape-dir", str(d)], capture_output=True,
                       text=True, cwd=str(REPO))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["state_digest"] == info["state_digest"]
    with pytest.raises(EC.CheckpointInvalid, match="another tape dir"):
        SC_.StateCkpt(tmp_path / "other", start_ms=0).restore(tmp_path / "s.ckpt")
    raw = (tmp_path / "s.ckpt").read_bytes().replace(b'"now_ms":', b'"now_ms":9', 1)
    (tmp_path / "bad.ckpt").write_bytes(raw)
    with pytest.raises(EC.CheckpointInvalid):
        SC_.StateCkpt(d, start_ms=0).restore(tmp_path / "bad.ckpt")
    with pytest.raises(EC.CheckpointInvalid, match="sha256"):
        SC_.StateCkpt(d, start_ms=0).restore(tmp_path / "s.ckpt", expect_sha256="1" * 64)


def test_warm_up_rebuild_differs_where_the_checkpoint_does_not(tmp_path):
    """Why QP-RULING-1026 Q3 asked for a checkpoint: a state rebuilt from a later start (a warm-up) loses never-pruned counters and whole-life fields."""
    if not __import__("shutil").which("zstd"):
        pytest.skip("zstd CLI not on PATH")
    d = tmp_path / "tape"
    write_tape(d, compact_hours=2)
    a = SC_.StateCkpt(d, start_ms=0)
    step_to(a, END)
    w = SC_.StateCkpt(d, start_ms=H0 + 2 * 3_600_000)  # a 'warm-up' over the last hour only
    step_to(w, END)
    assert w.snapshot("PA")["n_trades"] < a.snapshot("PA")["n_trades"]
    assert w.loss_spans["pumpswap"] == 0 and a.loss_spans["pumpswap"] == 1


# ---- pins --------------------------------------------------------------------------------------------------------------
PINNED_BLOBS = {  # git blob sha1 at origin/main 6a72511c (2026-10-26): the modules registered books are pinned to; P35 never edits them
    "arena_paper.py": "5eef156bb617138a4c04fca6779f9ff4c5215b13",
    "arena_state.py": "38e4c608115c99a52d202a6c82cd2a92c214879e",
    "arena_rules.py": "a107a677b0d70d7363543478de4e681cb0e7c3bc",
    "arena_rules_v2.py": "818d9be5cad74a90e5282a2927bbfffae1c365fd",
    "arena_rule_l1.py": "cf4cf1c4b08476b96162f735ce9ec730625b402a",
    "arena_pool_table.py": "daa22d3ab1a6c446c3ea44003ae9cdb6f53548cd",
    "arena_feed.py": "d6969658f9d2f23f45a29b3ee785fc0d7b746774",
}


@pytest.mark.parametrize("name,blob", sorted(PINNED_BLOBS.items()))
def test_pinned_arena_modules_unchanged(name, blob):
    data = (REPO / "tools" / name).read_bytes()
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == blob
