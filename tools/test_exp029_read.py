"""exp029_read tests. Synthetic ledgers in tmp dirs only: no real ledger, decision log, bridge log or host.

  /data/mal/venv/bin/python -m pytest tools/test_exp029_read.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from tools import exp029_read as E

S, C, R = E.S_MS, E.C_MS, E.R_MS
STAKE = 30_000_000
BOT = "floor-lane-1"
MINT_A = "MintAAAA1111111111111111111111111111111111"
DAY = 86_400_000


def row(ev, ts, mint, bot=BOT, **kw):
    d = {"schema": "lane.v1", "bot_id": bot, "row_id": f"r{row.n}", "ev": ev, "ts_ms": ts, "mint": mint, "signature": None, "sol_in_lamports": 0,
         "sol_out_lamports": 0, "estimated": False}
    row.n += 1
    d.update(kw)
    return d


row.n = 0


def send(ts, mint, bot=BOT, stake=STAKE, oid=None):
    row.n += 1
    return row("send_intent", ts, mint, bot, stake_lamports=stake, signature=f"intent:{oid or 'o' + str(row.n)}")


def trade_rows(ts, mint, out, back=0, bot=BOT, stake=STAKE):
    """send, landed fill (cash out), exit (cash back)."""
    return [send(ts, mint, bot, stake), row("fill", ts + 1000, mint, bot, ok=True, sol_out_lamports=out), row("exit", ts + 5000, mint, bot, sol_in_lamports=back)]


def write_ledger(root: Path, rows, tail=b""):
    root.mkdir(parents=True, exist_ok=True)
    (root / E.LEDGER_NAME).write_bytes(b"".join(json.dumps(r, sort_keys=True).encode() + b"\n" for r in rows) + tail)


def write_host(root: Path, rows=None):
    d = root / "decisions" / E.ARM
    d.mkdir(parents=True, exist_ok=True)
    rows = rows if rows is not None else [{"seq": 0, "arm": E.ARM, "ms": 1794299521000, "prev": "0", "kind": "start", "binding": {"git_sha": "f" * 40, "host_sha256": "ab" * 32},
                                          "frozen_sha": "5e12f8f5" + "0" * 32, "rules": [E.ARM], "sha": "x"},
                                         {"seq": 1, "arm": E.ARM, "ms": 1794299600000, "prev": "x", "kind": "entry", "pool": "SecretPool", "sha": "y"}]
    (d / "2026-11-08.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


class World:
    def __init__(self, tmp: Path, rows, tail=b"", bridge=None):
        self.tmp = tmp
        self.led, self.host, self.out = tmp / "led", tmp / "host", tmp / "out"
        self.out.mkdir(parents=True)
        write_ledger(self.led, rows, tail)
        write_host(self.host)
        (tmp / "disc.txt").write_text("DISCLOSURE LINE ONE\nline two\n")
        self.bridge = None
        if bridge is not None:
            (tmp / "bl").mkdir()
            self.bridge = tmp / "bl" / "bridge-log-live.jsonl"
            self.bridge.write_text("".join(json.dumps(b) + "\n" for b in bridge))

    def args(self, now=R, **over):
        a = ["--lane-ledger-root", str(self.led), "--host-decisions-root", str(self.host), "--disclosures", str(self.tmp / "disc.txt"),
             "--out-json", str(self.out / "r.json"), "--out-md", str(self.out / "r.md")]
        if self.bridge:
            a += ["--bridge-log", str(self.bridge)]
        if now is not None:
            a += ["--now-ms", str(now)]
        return a

    def run(self, monkeypatch, now=R):
        monkeypatch.setenv(E.TEST_NOW_ENV, "1")
        rc = E.main(self.args(now))
        return rc

    def rep(self):
        return json.loads((self.out / "r.json").read_text())


def assemble(rows, bridge=None):
    return E.assemble(rows, 0, [{"instant_utc": "x", "frozen_sha": "a", "host_sha256": "b"}], bridge, "D", R)


# ------------------------------------------------------------- constants

def test_constants():
    assert E.iso(S) == "2026-11-09T06:00:00.000Z"
    assert E.iso(E.EVENT_MS) == "2026-11-12T00:46:04.584Z"
    assert E.iso(C) == "2026-11-13T00:00:00.000Z"
    assert E.iso(R) == "2026-11-14T01:00:00.000Z"
    assert R == C + 25 * 3_600_000
    assert E.BOT_ID == "floor-lane-1" and E.ARM == "l1-floor-v2"
    assert E.STAKES == {30_000_000: "0.03", 100_000_000: "0.10"}
    assert (E.N_MIN, E.DAYS_MIN, E.BOOT_DRAWS, E.BOOT_SEED, E.BOOT_PCT, E.TOP_REMOVED) == (100, 5, 1000, 1, 5, 3)
    assert E.HEADER == "EXP-029 read at R = 2026-11-14T01:00:00Z: FAIL (procedural, Erratum 1g)"


# ------------------------------------------------------------- trade unit

def trades_of(rows):
    return assemble(rows)["trades"]


def test_not_sent_excluded():
    rows = [send(S + 1000, "m1"), row("writeoff", S + 1007, "m1", reason="not_sent")] + trade_rows(S + 2000, "m2", 31_000_000, 40_000_000)
    rep = assemble(rows)
    assert len(rep["trades"]) == 1 and rep["names"]["counts"]["not_sent_S_C"] == 1


def test_buy_expired_counted_at_its_cash():
    rows = [send(S + 1000, "m1"), row("fee", S + 1500, "m1", sol_out_lamports=5000), row("writeoff", S + 2000, "m1", reason="buy_expired", sol_out_lamports=7000)]
    t = trades_of(rows)
    assert len(t) == 1 and t[0]["pnl_lamports"] == -12000 and t[0]["closing_row"] == "writeoff"
    assert assemble(rows)["names"]["counts"]["zero_cash_trades_S_C"] == 0


def test_zero_cash_trade_counted():
    rows = [send(S + 1000, "m1"), row("writeoff", S + 2000, "m1", reason="buy_expired")]
    rep = assemble(rows)
    assert len(rep["trades"]) == 1 and rep["names"]["counts"]["zero_cash_trades_S_C"] == 1


def test_errored_fill_closes():
    rows = [send(S + 1000, "m1"), row("fill", S + 2000, "m1", ok=False, sol_out_lamports=9000),
            row("fee", S + 3000, "m1", sol_out_lamports=4000)]  # after the close: belongs to no trade
    t = trades_of(rows)
    assert t[0]["pnl_lamports"] == -9000 and t[0]["closing_row"] == "fill_error" and t[0]["open_at_R"] is False


def test_fee_rows_of_failed_sells_included():
    rows = [send(S + 1000, "m1"), row("fill", S + 2000, "m1", ok=True, sol_out_lamports=31_000_000), row("fee", S + 3000, "m1", sol_out_lamports=11_000),
            row("fee", S + 4000, "m1", sol_out_lamports=12_000, fee_network_lamports=12_000), row("exit", S + 5000, "m1", sol_in_lamports=40_000_000)]
    assert trades_of(rows)[0]["pnl_lamports"] == 40_000_000 - 31_000_000 - 23_000


def test_second_send_same_mint_is_second_trade():
    rows = trade_rows(S + 1000, "m1", 31_000_000, 20_000_000) + trade_rows(S + 60_000, "m1", 31_000_000, 45_000_000)
    t = trades_of(rows)
    assert [x["pnl_lamports"] for x in t] == [-11_000_000, 14_000_000]


def test_open_at_R_and_cut():
    rows = [send(S + 1000, "m1"), row("fill", S + 2000, "m1", ok=True, sol_out_lamports=31_000_000)]
    t = trades_of(rows)
    assert t[0]["open_at_R"] is True and t[0]["pnl_lamports"] == -31_000_000 and t[0]["closing_row"] == "none"
    late = rows + [row("exit", R + 5, "m1", sol_in_lamports=50_000_000)]  # at or after R: ignored, still open at R
    rep = assemble(late)
    assert rep["trades"][0]["open_at_R"] is True and rep["trades"][0]["pnl_lamports"] == -31_000_000 and rep["ledger"]["rows_at_or_after_R_ignored"] == 1


def test_estimated_counted_and_listed():
    rows = [send(S + 1000, "m1"), row("fill", S + 2000, "m1", ok=True, sol_out_lamports=31_000_000, estimated=True), row("exit", S + 3000, "m1", sol_in_lamports=1)]
    rep = assemble(rows)
    assert rep["trades"][0]["estimated"] is True and rep["trades"][0]["pnl_lamports"] == 1 - 31_000_000
    assert rep["names"]["counts"]["estimated_trades_S_C"] == 1 and rep["names"]["counts"]["estimated_rows_in_S_C_trades"] == 1


def test_snap_and_dry_ignored():
    rows = [row("snap", S, "x", day="2026-11-09", pnl_lamports=0, pnl_cost_lamports=0, sol_out_lamports=777), row("would_send", S + 1, "m9"),
            row("send_intent", S + 2, "m8", mode="dry", stake_lamports=STAKE)] + trade_rows(S + 1000, "m1", 31_000_000, 32_000_000)
    rep = assemble(rows)
    assert len(rep["trades"]) == 1 and rep["trades"][0]["pnl_lamports"] == 1_000_000


def test_other_bots_listed_not_in_trades():
    rows = trade_rows(S + 1000, "m1", 31_000_000, 32_000_000) + trade_rows(S + 2000, "m2", 5_000_000, 0, bot="dsl-x") + [send(C + 10, "m3", bot="dsl-x")]
    rep = assemble(rows)
    assert len(rep["trades"]) == 1
    names = {b["bot_id"]: b for b in rep["names"]["lane_bots"]}
    assert set(names) == {BOT, "dsl-x"} and names["dsl-x"]["trades_S_C"] == 1 and names["dsl-x"]["total_lamports_S_C"] == -5_000_000 and names["dsl-x"]["sends_C_R"] == 1
    assert names[BOT]["result"] == E.RESULT


def test_pre_S_listed_apart_and_boundaries():
    rows = (trade_rows(S - 1000, "p1", 31_000_000, 30_000_000) + trade_rows(S, "m1", 31_000_000, 32_000_000) + trade_rows(C - 1, "m2", 31_000_000, 31_000_000)
            + trade_rows(C, "m3", 31_000_000, 31_000_000))
    rep = assemble(rows)
    assert [t["send_utc"] for t in rep["trades"]] == [E.iso(S), E.iso(C - 1)]  # exactly S counted, exactly C not
    assert rep["pre_S"]["count"] == 1 and rep["pre_S"]["sends_utc"] == [E.iso(S - 1000)] and rep["pre_S"]["total_lamports"] == -1_000_000
    assert rep["post_event_sends_C_R"] == 1


def test_duplicate_row_id_is_one_row(tmp_path):
    rows = trade_rows(S + 1000, "m1", 31_000_000, 32_000_000)
    write_ledger(tmp_path, rows + [rows[1]])
    assert len(E.load_ledger(tmp_path)[0]) == 3


# ------------------------------------------------------------- tests of section 4

def mk_trades(pnls, days=None):
    t = []
    for i, p in enumerate(pnls):
        ts = S + i * 1000 + (days[i] * DAY if days else 0)
        t.append({"ts_ms": ts, "mint": f"m{i:04d}", "pnl": p, "day": E.day_of(ts), "stake": STAKE})
    return sorted(t, key=lambda x: (x["ts_ms"], x["mint"]))


def ref_bound(pnls_sorted):
    x = np.asarray(pnls_sorted, dtype=np.float64)
    rng = np.random.default_rng(1)
    idx = rng.integers(0, len(x), size=(1000, len(x)))
    return float(np.percentile(x[idx].mean(axis=1), 5))


def test_bootstrap_recipe_matches_reference():
    pnls = [(i * 7919) % 2000003 - 900000 for i in range(137)]
    t = mk_trades(pnls)
    res = E.run_tests(t)
    assert res["t3_bound"]["lower_90_bound_mean_lamports"] == ref_bound([x["pnl"] for x in t])
    # sorted by (ts_ms, mint): same ts, order decided by mint
    a = [{"ts_ms": S, "mint": "b", "pnl": 5, "day": "d", "stake": 1}, {"ts_ms": S, "mint": "a", "pnl": -9, "day": "d", "stake": 1}]
    rep = assemble([send(S, "b", stake=1), row("exit", S + 1, "b", sol_in_lamports=5), send(S, "a", stake=1), row("exit", S + 1, "a", sol_out_lamports=9)])
    assert [x["pnl_lamports"] for x in rep["trades"]] == [-9, 5]
    assert a  # fixture shape only


def test_n_threshold():
    assert E.run_tests(mk_trades([1] * 99))["t1_n"]["holds"] is False
    assert E.run_tests(mk_trades([1] * 100))["t1_n"]["holds"] is True


def test_day_rule_strictly_more_than_half():
    def res(day_totals):
        pn = []
        dd = []
        for d, v in enumerate(day_totals):
            pn.append(v)
            dd.append(d)
        return E.run_tests(mk_trades(pn, dd))["t2_days"]

    assert res([1, 1, -1, -1, 1, -1])["holds"] is False  # 3 of 6: not strictly more than half
    assert res([1, 1, 1, -1, -1, -1, 1])["holds"] is True  # 4 of 7
    assert res([1, 1, 1, 1])["holds"] is False  # fewer than 5 days
    assert res([1, 1, 1, 0, 0])["holds"] is True  # 3 of 5; a zero day is not positive
    assert res([1, 1, 0, 0, 0])["holds"] is False


def test_top3_removed():
    r = E.run_tests(mk_trades([100, 90, 80, 10, 5, -1]))["t4_top3"]
    assert r["total_after_removing_top3_lamports"] == 14 and r["holds"] is True
    assert E.run_tests(mk_trades([100, 90, 80, -5, 2]))["t4_top3"]["holds"] is False
    small = E.run_tests(mk_trades([5, 4, 3]))["t4_top3"]
    assert small["note"] == "not defined for n <= 3" and small["holds"] is False


def test_split_by_stake():
    rows = trade_rows(S + 1000, "m1", 31_000_000, 40_000_000) + trade_rows(S + 2000, "m2", 101_000_000, 90_000_000, stake=100_000_000) + trade_rows(S + 3000, "m3", 7, 0, stake=50_000_000)
    rep = assemble(rows)
    t = rep["tests"]
    assert t["pooled"]["n"] == 3 and t["stake_0.03_sol"]["n"] == 1 and t["stake_0.10_sol"]["n"] == 1 and t["stake_other"]["n"] == 1
    assert t["stake_0.10_sol"]["t3_bound"]["lower_90_bound_mean_lamports"] == -11_000_000.0


def test_empty_split_says_no_trades(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    assert w.run(monkeypatch) == 0
    assert w.rep()["tests"]["stake_0.10_sol"] == {"label": "descriptive", "n": 0, "note": "no trades"}
    assert "no trades" in (w.out / "r.md").read_text()


# ------------------------------------------------------------- the run

def test_refuses_before_R(tmp_path, monkeypatch, capsys):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    assert w.run(monkeypatch, now=R - 1) == 2
    assert "before R" in capsys.readouterr().err
    assert not (w.out / "r.json.done").exists() and not (w.out / "r.json").exists()


def test_now_ms_refused_without_env(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    monkeypatch.delenv(E.TEST_NOW_ENV, raising=False)
    assert E.main(w.args(R)) == 2
    assert not (w.out / "r.json.done").exists()


def test_one_look_marker(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    assert w.run(monkeypatch) == 0
    assert (w.out / "r.json.done").exists()
    first = (w.out / "r.json").read_bytes()
    assert w.run(monkeypatch) == 2  # refuses again
    assert (w.out / "r.json").read_bytes() == first
    assert "one look: this tool writes a marker file next to --out-json and refuses to run again if it exists" in (w.out / "r.md").read_text()


def test_failed_run_leaves_no_marker(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    (w.led / E.LEDGER_NAME).write_text("{not json}\n" + (w.led / E.LEDGER_NAME).read_text())
    assert w.run(monkeypatch) == 2
    assert not (w.out / "r.json.done").exists()


def test_missing_rows_refused(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000, bot="other"))
    assert w.run(monkeypatch) == 2  # no floor-lane-1 send_intent
    w2 = World(tmp_path / "b", trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    (w2.led / (E.BACKLOG_PREFIX + BOT)).write_text("{}\n")
    assert w2.run(monkeypatch) == 2  # a non-empty backlog: rows not in the ledger yet
    w3 = World(tmp_path / "c", trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    (w3.host / "decisions" / E.ARM / "2026-11-08.jsonl").write_text("")
    assert w3.run(monkeypatch) == 2  # no host start row
    w4 = World(tmp_path / "d", trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    (w4.led / E.LEDGER_NAME).unlink()
    assert w4.run(monkeypatch) == 2


def test_torn_last_line_tolerated(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000), tail=b'{"ev": "exit", "bot_id": "floor-lane-1", "mi')
    assert w.run(monkeypatch) == 0
    assert w.rep()["ledger"]["torn_last_lines_ignored"] == 1 and len(w.rep()["trades"]) == 1


def test_path_guard(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    a = w.args()
    a[a.index("--out-md") + 1] = str(w.led / "r.md")
    monkeypatch.setenv(E.TEST_NOW_ENV, "1")
    assert E.main(a) == 2
    a = w.args()
    a[a.index("--out-json") + 1] = str(w.host / "decisions" / "r.json")
    assert E.main(a) == 2
    assert not (w.led / "r.md").exists()


def test_readonly_on_inputs(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    before = {p: p.read_bytes() for p in list(w.led.rglob("*")) + list(w.host.rglob("*")) if p.is_file()}
    names = sorted(str(p) for p in tmp_path.rglob("*") if p.parent in (w.led, w.host))
    os.chmod(w.led / E.LEDGER_NAME, 0o444)
    assert w.run(monkeypatch) == 0
    assert {p: p.read_bytes() for p in before} == before
    assert names == sorted(str(p) for p in tmp_path.rglob("*") if p.parent in (w.led, w.host))


def test_deterministic_bytes(tmp_path, monkeypatch):
    rows = [x for i in range(12) for x in trade_rows(S + i * 7000 + (i % 4) * DAY, f"m{i}", 31_000_000, 20_000_000 + i * 1_500_000)]
    a, b = World(tmp_path / "a", rows), World(tmp_path / "b", rows)
    assert a.run(monkeypatch) == 0 and b.run(monkeypatch) == 0
    assert (a.out / "r.json").read_bytes() == (b.out / "r.json").read_bytes()
    assert (a.out / "r.md").read_bytes() == (b.out / "r.md").read_bytes()


# ------------------------------------------------------------- outputs

def test_forbidden_words_and_header(tmp_path, monkeypatch):
    rows = [x for i in range(120) for x in trade_rows(S + i * 3_000_000, f"m{i}", 31_000_000, 40_000_000)]
    w = World(tmp_path, rows)
    assert w.run(monkeypatch) == 0
    md = (w.out / "r.md").read_text()
    js = (w.out / "r.json").read_text()
    assert md.startswith("# EXP-029 read at R = 2026-11-14T01:00:00Z: FAIL (procedural, Erratum 1g)")
    for text in (md, js):
        low = text.lower()
        for bad in ("pass", "verdict", "makes money", "profitable", "wins"):
            assert bad not in low, bad
    assert "holds: yes" in md
    assert "descriptive" in md
    assert "DISCLOSURE LINE ONE\nline two" in md
    assert "SecretPool" not in md and "SecretPool" not in js and "f" * 40 not in md  # the host row's other fields are not printed
    assert "5e12f8f5" in md and ("ab" * 32) in md


def test_no_mint_pool_signature_in_outputs(tmp_path, monkeypatch):
    mint = MINT_A
    rows = [send(S + 1000, mint, oid="abc123"), row("fill", S + 2000, mint, ok=True, sol_out_lamports=31_000_000, signature="SigZZZ" + "9" * 40, price=1.23456, pool="PoolQQQ", slot=424242),
            row("exit", S + 3000, mint, sol_in_lamports=1, signature="SigYYY" + "8" * 40)]
    w = World(tmp_path, rows, bridge=[{"kind": "entry_refused", "why": "lane_full", "ts_ms": S + 10, "mode": "live", "mint": mint, "pool": "PoolQQQ"}])
    assert w.run(monkeypatch) == 0
    for name in ("r.json", "r.md", "r.json.done"):
        b = (w.out / name).read_bytes()
        for s in (mint, "SigZZZ", "SigYYY", "PoolQQQ", "1.23456", "424242"):
            assert s.encode() not in b, (name, s)
    t = w.rep()["trades"][0]
    assert set(t) == {"order", "send_utc", "trade_day", "stake_lamports", "pnl_lamports", "pnl_sol", "closing_row", "open_at_R", "estimated"} and t["order"] == "abc123"

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                assert k not in ("mint", "pool", "signature", "price", "slot", "reserves"), k
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(w.rep())


def test_names_counts_and_lane_full_groups(tmp_path, monkeypatch):
    held = [send(S - 500, "pre", oid="p"), row("fill", S - 400, "pre", ok=True, sol_out_lamports=1)]  # pre-S floor-lane-1 position, still open
    rows = held + [send(S + 100, "o1", bot="dsl-x"), row("fill", S + 200, "o1", bot="dsl-x", ok=True, sol_out_lamports=5)] + trade_rows(S + 50_000, "m1", 31_000_000, 20_000_000)
    rows += [row("exit", S + 90_000, "o1", bot="dsl-x", sol_in_lamports=1), row("exit", S + 99_000, "pre", sol_in_lamports=1)]
    rows += [send(S + 300_000, "m2"), row("fill", S + 300_100, "m2", ok=True, sol_out_lamports=7)]
    mk = lambda ts, why="lane_full", mode="live": {"kind": "entry_refused", "why": why, "ts_ms": ts, "mode": mode}
    bridge = [mk(S + 150), mk(S + 60_000), mk(S + 95_000), mk(S + 400_000), mk(S + 150, mode="dry"), mk(C + 1), mk(S + 5, "limit:max_open"), {"kind": "latch", "latch": "stuck_sell", "ts_ms": S + 7, "mode": "live"}]
    w = World(tmp_path, rows, bridge=bridge)
    assert w.run(monkeypatch) == 0
    nm = w.rep()["names"]
    assert nm["bridge"]["lane_full_in_S_C"] == 4
    assert nm["bridge"]["lane_full_groups"] == {"i_other_lane_bot_holds_slot": 2, "ii_only_pre_S_floor_lane_1_holds_slot": 1, "iii_only_in_read_floor_lane_1_positions": 1}
    assert nm["counts"]["lane_full"] == 4 and nm["bridge"]["entry_refused_by_label_S_R"]["limit:max_open"] == 1
    assert nm["bridge"]["latch_rows"] == [{"instant_utc": E.iso(S + 7), "latch": "stuck_sell"}]
    assert nm["host_start_rows"] == [{"frozen_sha": "5e12f8f5" + "0" * 32, "host_sha256": "ab" * 32, "instant_utc": E.iso(1794299521000)}]
    assert {b["bot_id"] for b in nm["lane_bots"]} == {BOT, "dsl-x"}


def test_no_bridge_log_says_so(tmp_path, monkeypatch):
    w = World(tmp_path, trade_rows(S + 1000, "m1", 31_000_000, 40_000_000))
    assert w.run(monkeypatch) == 0
    assert w.rep()["names"]["counts"]["lane_full"] == "bridge log not provided"


def test_per_day_totals_by_trade_day(tmp_path, monkeypatch):
    rows = trade_rows(S + 1000, "m1", 31_000_000, 40_000_000) + trade_rows(S + DAY + 1000, "m2", 31_000_000, 30_000_000)
    rep = assemble(rows)
    assert rep["day_totals"] == {"2026-11-09": {"n": 1, "total_lamports": 9_000_000}, "2026-11-10": {"n": 1, "total_lamports": -1_000_000}}
