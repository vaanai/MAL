"""L1 FLOOR-OPTION v2 streaming port: pool table, rule, v2 host, restart, frozen v1 files. Synthetic tape only. Nothing here says the rule makes money."""

from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

import pytest

from tools import arena_pool_table as PT
from tools import arena_rule_l1 as L
from tools.arena_pool_table import PoolTable
from tools.arena_rules import HostConfig, register_rules
from tools.arena_rules_testkit import FakeEngine, write_tape
from tools.arena_rules_v2 import HostV2, load_rules_v2
from tools.arena_state import ArenaState

REPO = Path(__file__).resolve().parent.parent
HOUR = 3_600_000
H0 = 1_792_000_000_000 // HOUR * HOUR  # an hour boundary
WSOL = PT.WSOL
V = 17_600_000_000
SOLX = 1_000_000_000
SHA = "abcdef1234567"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def row(t, pool, side="buy", q=10 * SOLX, sol=200_000_000, v=V, mint=None, quote=WSOL, n=0):
    return {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 1000 + (t - H0) // 400, "t_recv_ms": t, "signature": f"s{t}-{pool}-{n}", "event_index": 0,
            "pool": pool, "mint": mint or "M" + pool, "trader": "tr", "side": side, "sol_lamports": sol, "token_raw": sol * 1000, "quote_reserve": q, "base_reserve": 10**12,
            "virtual_quote_reserves": v, "quote_mint": quote, "event_ts": t // 1000}


def pool_rows(drop_quote=WSOL, with_drop=True, p="P"):
    """Filler pool X at hour 0; P first prints at 4 h (V in band), every 30 min to 29 h, then a drained-pool SELL at 30 h 15 min (age 26.25 h)."""
    rows = [row(H0 + 1000, "X")]
    t = H0 + 4 * HOUR
    while t <= H0 + 29 * HOUR:
        rows.append(row(t, p, "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000
    if with_drop:
        rows.append(row(H0 + 30 * HOUR + 900_000, p, "sell", q=600_000_000, sol=200_000_000, quote=drop_quote))
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def run_host(tmp, rows, hours=70, evict_min=None, table=None):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows)
    rules = load_rules_v2(["l1-floor-v2"])
    reg = tmp / "bots.json"
    if not reg.exists():
        register_rules(rules, str(reg), SHA, H0, now_ms=H0 - 1, verify_sha=False)
    kw = {} if evict_min is None else {"evict_idle_min": evict_min}
    state = ArenaState(tape, start_ms=0, **kw)
    cfg = HostConfig(root=str(tmp / "rules"), registry=str(reg), frozen_sha=SHA, clock_start_ms=H0)
    host = HostV2(cfg, rules, state, FakeEngine(tmp / "rules"), table=table)
    host.run(H0, H0 + hours * HOUR)
    return host


def entries(host):
    return [r for r in host.log.rows("l1-floor-v2") if r.get("kind") == "entry"]


# ------------------------------------------------------------------------------------------------ pool table
def test_table_universe_rules():
    t = PoolTable("first_print")
    rows = [row(H0 + 1000, "X"),  # sets t0 = H0
            row(H0 + 4 * HOUR, "A", mint="MA"), row(H0 + 5 * HOUR, "B", mint="MA"),  # B: second pool of mint MA is not canonical
            row(H0 + 4 * HOUR, "C", v=25 * SOLX), row(H0 + 2 * HOUR, "D"),  # C: V out of band; D: first print < t0 + 3 h
            row(H0 + 6 * HOUR, "E", quote="Other111")]  # not WSOL: never seen
    for r in rows:
        t.update(r)
    assert t.base("A") == (H0 + 4 * HOUR) // 1000 and t.base("B") is None and t.base("C") is None and t.base("D") is None and "E" not in t.seen
    assert t.t0 == H0 // 1000 and set(t.prints) == {"A"}


def test_table_complete_mode():
    t = PoolTable("complete")
    ts = (H0 + 4 * HOUR) // 1000
    t.update({"type": "create", "t_recv_ms": H0, "mint": "MA", "is_mayhem_mode": False, "signature": "c1"})
    t.update({"type": "create", "t_recv_ms": H0, "mint": "MB", "is_mayhem_mode": True, "signature": "c2"})
    for m in ("MA", "MB", "MC"):
        t.update({"type": "complete", "t_recv_ms": H0 + 1, "mint": m, "event_ts": ts - 30, "signature": "k" + m})
    t.update(row(H0 + 4 * HOUR, "A", mint="MA"))
    t.update(row(H0 + 4 * HOUR + 1, "B", mint="MB"))  # mayhem
    t.update(row(H0 + 4 * HOUR + 2, "C", mint="MC"))  # no create row
    assert t.base("A") == ts - 30 and t.base("B") is None and t.base("C") is None


def test_table_checkpoint_roundtrip_and_overlap(tmp_path):
    rows = pool_rows()
    full = PoolTable()
    for r in rows:
        full.update(r)
    half = PoolTable()
    for r in rows[:30]:
        half.update(r)
    half.checkpoint(tmp_path / "t.pkl")
    again = PoolTable.load(tmp_path / "t.pkl")
    for r in rows[25:]:  # the warm-up feeds rows the checkpoint already holds
        again.update(r)
    assert again.to_dict() == full.to_dict()


def test_table_rebuild_from_tape_equals_incremental(tmp_path):
    rows = pool_rows()
    write_tape(tmp_path / "tape", rows)
    inc = PoolTable()
    for r in rows:
        inc.update(r)
    rb = PoolTable()
    rb.rebuild(tmp_path / "tape", H0 + 200 * HOUR)
    assert rb.to_dict() == inc.to_dict()
    cut = PoolTable()
    n = cut.rebuild(tmp_path / "tape", H0 + 10 * HOUR)
    cut2 = PoolTable.from_dict(cut.to_dict())
    cut2.rebuild(tmp_path / "tape", H0 + 200 * HOUR)
    assert n > 0 and cut2.to_dict() == inc.to_dict()


def test_table_windows_trim_and_sweep():
    t = PoolTable()
    for r in pool_rows():
        t.update(r)
    pp = t.pool_prints("P")
    assert min(pp.hidx) >= (H0 + 30 * HOUR + 900_000) // HOUR - (PT.HIST_HOURS - 1)
    t.sweep(H0 + 400 * HOUR)
    assert "P" not in t.prints and t.base("P") is not None  # arrays freed, the pool stays known


# ------------------------------------------------------------------------------------------------ rule through the v2 host
def test_entry_once_with_kit_fields(tmp_path):
    host = run_host(tmp_path, pool_rows(), hours=34)
    e = entries(host)
    assert len(e) == 1 and e[0]["pool"] == "P"
    m = e[0]["meta"]
    assert (m["cell"], m["hold_s"], m["tp"]) == ("l1", 86400.0, 1.5) and m["age_s"] >= L.AGE_LO and m["r6"] < 0.8 and m["vol24_sol"] >= 1.0
    assert not host.hook_errors and host.fatal is None


def test_non_wsol_quote_on_the_deciding_row_is_refused(tmp_path):
    host = run_host(tmp_path, pool_rows(drop_quote="Other111"), hours=34)  # QP C8: the row's own quote mint
    assert entries(host) == []
    host2 = run_host(tmp_path / "n", pool_rows(drop_quote=None), hours=34)
    assert entries(host2) == []


@pytest.mark.parametrize("change,why", [({"v": 25 * SOLX}, "v out of band"), ({"q": 5 * SOLX}, "vault above 0.5 SOL"), ({"side": "buy"}, "not a sell")])
def test_each_entry_condition_blocks(tmp_path, change, why):
    rows = pool_rows()
    rows[-1] = {**row(rows[-1]["t_recv_ms"], "P", "sell", q=600_000_000), **{}}
    r = rows[-1]
    if "v" in change:
        r["virtual_quote_reserves"] = change["v"]
    if "q" in change:
        r["quote_reserve"] = change["q"]
    if "side" in change:
        r["side"] = change["side"]
    assert entries(run_host(tmp_path, rows, hours=34)) == [], why


def test_age_below_24h_and_stale_previous_print_block(tmp_path):
    rows = [r for r in pool_rows() if r["pool"] != "P" or r["t_recv_ms"] > H0 + 12 * HOUR]  # first P print at 12.5 h: age at the drop = 17.75 h
    assert entries(run_host(tmp_path, rows, hours=34)) == []
    rows = [r for r in pool_rows() if not (H0 + 24 * HOUR < r["t_recv_ms"] < H0 + 30 * HOUR)]  # nothing in the 6 h before the drop print
    assert entries(run_host(tmp_path / "b", rows, hours=34)) == []


def test_low_volume_blocks(tmp_path):
    rows = pool_rows()
    for r in rows:
        if r["pool"] == "P":
            r["sol_lamports"] = 1_000_000
    rows[-1]["sol_lamports"] = 1_000_000
    assert entries(run_host(tmp_path, rows, hours=34)) == []


def test_table_survives_state_eviction(tmp_path):
    host = run_host(tmp_path, pool_rows(), hours=34, evict_min=20)  # the state forgets a pool idle 20 min; the table does not
    assert len(entries(host)) == 1


def test_time_exit_and_cooldown_reentry(tmp_path):
    host = run_host(tmp_path, pool_rows(), hours=55)
    ex = [r for r in host.log.rows("l1-floor-v2") if r.get("kind") == "exit"]
    e = entries(host)
    assert len(ex) == 1 and ex[0]["reason"] == L.REASON_TIME
    assert ex[0]["ms"] >= e[0]["ms"] + 86_400_000 + 1_900 and ex[0]["ms"] < e[0]["ms"] + 86_400_000 + 4_000
    assert host.table.exit_ms("P") == ex[0]["ms"]


def test_take_profit_on_post_trade_spot(tmp_path):
    rows = pool_rows()
    t = rows[-1]["t_recv_ms"] + 60_000
    rows.append(row(t, "P", "buy", q=60 * SOLX, sol=50_000_000))  # the spot is several times the decision print's
    host = run_host(tmp_path, rows, hours=34)
    ex = [r for r in host.log.rows("l1-floor-v2") if r.get("kind") == "exit"]
    assert len(entries(host)) == 1 and [x["reason"] for x in ex] == [L.REASON_TP]


def test_cooldown_blocks_a_second_entry_within_60s(tmp_path):
    rows = pool_rows()
    t = rows[-1]["t_recv_ms"]
    rows.append(row(t + 60_000, "P", "buy", q=60 * SOLX, sol=50_000_000))  # tp exit at t + 60 s
    rows.append(row(t + 70_000, "P", "sell", q=600_000_000))  # a second qualifying sell 10 s after the exit decision
    host = run_host(tmp_path, rows, hours=34)
    assert len(entries(host)) == 1


# ------------------------------------------------------------------------------------------------ restart
def test_restart_keeps_table_and_positions_in_log(tmp_path):
    first = run_host(tmp_path, pool_rows(), hours=34)
    first.save_table()
    assert len(entries(first)) == 1
    again = PoolTable.load(first.table_path)
    assert again.to_dict() == first.table.to_dict() and again.base("P") == first.table.base("P")
    # a new host over the same logs rebuilds the book from the decision log (the real engine would book the position engine_restart: tools/arena_rules_v2.py docstring)
    rules = load_rules_v2(["l1-floor-v2"])
    state = ArenaState(tmp_path / "tape", start_ms=0)
    cfg = HostConfig(root=str(tmp_path / "rules"), registry=str(tmp_path / "bots.json"), frozen_sha=SHA, clock_start_ms=H0)
    host = HostV2(cfg, rules, state, first.engine)
    host.resume()
    assert host.table.base("P") == first.table.base("P") and len(host.books["l1-floor-v2"].open) == 1 and len(entries(host)) == 1


# ------------------------------------------------------------------------------------------------ report-only K = 4 leg
def test_fifo_cap_k4():
    e = [(i * 10, None) for i in range(6)]
    r = L.fifo_cap(e, 4)
    assert (r["taken"], r["skipped"], r["peak_open"]) == (4, 2, 4)
    e = [(0, 5), (1, 5), (2, 5), (3, 5), (6, 20), (7, 20)]  # the first four exit at 5: the later two fit
    assert L.fifo_cap(e, 4)["taken"] == 6


# ------------------------------------------------------------------------------------------------ frozen v1 files
def test_v1_host_and_rule_files_are_unchanged():
    pins = {"tools/arena_rules.py": "048dd78901c52e0c14fe9b1611f354c49ac6d0bb37fad8e5be3e27c756e3e2af", "tools/arena_paper.py": "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514",
            "tools/arena_rule_h2ps.py": "1e2d3db5f60f987a3221d225f23c9864dffa5b86e161d35f2fbb90ad3eb189d2", "tools/arena_rule_j3.py": "614f082aae28c335a7c93d219d5962ebcc9f6d597c081fde15569e976859a9e7",
            "scripts/research/arena-rules-job.sh": "42ceb0360b1aa6f7d66a3b21b49f97148805a514bfce97986a8ae5198b08da43"}
    for rel, sha in pins.items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == sha, rel


def test_rule_source_has_no_banned_family_and_keeps_wsol_first():
    src = (REPO / "tools" / "arena_rule_l1.py").read_text()
    on_row = src.split("def on_row")[1].split("quote_mint")[0]
    assert "virtual_quote_reserves" not in on_row  # the quote test comes before the V test
    assert json.dumps(L.make_rule().table_mode) == '"first_print"'
