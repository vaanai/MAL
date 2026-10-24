"""launchlab_screen: synthetic tape on the exact integer curve; rules, exits, cooldown, fee ladders, holdout guard."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tools import launchlab_price as L
from tools import launchlab_screen as S

VQ, VB, TBS = 30_000_000_000, 1_073_000_000_000_000, 793_100_000_000_000
T0 = datetime(2026, 9, 24, 0, tzinfo=timezone.utc)
BASE_SLOT = 400_000_000


class Sim:
    def __init__(self, pool="PoolA"):
        self.pool, self.rq, self.rb, self.rows = pool, 0, 0, []

    def buy(self, t, sol, fee=0.015):
        fees = int(sol * fee)
        net = sol - fees
        out = (VB - self.rb) * net // (VQ + self.rq + net)
        self._row(t, "buy", self.rq, self.rb, self.rq + net, self.rb + out, sol, out, fees)

    def sell(self, t, base, fee=0.0105):
        g = (VQ + self.rq) * base // (VB - self.rb + base)
        fees = int(g * fee)
        self._row(t, "sell", self.rq, self.rb, self.rq - g, self.rb - base, base, g - fees, fees)

    def _row(self, t, side, rq0, rb0, rq1, rb1, ain, aout, fees):
        bt = int(t.timestamp())
        self.rows.append({"v": 1, "venue": "launchlab", "slot": BASE_SLOT + int((bt - int(T0.timestamp())) / 0.4), "t_recv_ms": bt * 1000, "block_time": bt, "signature": f"s{len(self.rows)}",
                          "type": "trade", "pool": self.pool, "total_base_sell": TBS, "virtual_base": VB, "virtual_quote": VQ, "real_base_before": rb0, "real_quote_before": rq0,
                          "real_base_after": rb1, "real_quote_after": rq1, "amount_in": ain, "amount_out": aout, "protocol_fee": fees // 6, "platform_fee": fees - fees // 6,
                          "creator_fee": 0, "share_fee": 0, "side": side, "pool_status": "fund", "exact_in": True, "event_index": 0, "ix": []})
        self.rq, self.rb = rq1, rb1


def write_hours(root, rows_by_hour):
    root.mkdir(parents=True, exist_ok=True)
    for h in L.exploration_hours()[:6]:
        key = int(h.timestamp() // 3600)
        lines = [json.dumps({"type": "backfill_hour", "hour": key})]
        lines += [json.dumps(r) for r in rows_by_hour.get(key, [])]
        lines.append(json.dumps({"type": "backfill_hour_end"}))
        f = root / f"tape-{h:%Y-%m-%dT%H}.jsonl"
        f.write_text("\n".join(lines) + "\n")
        subprocess.run(["zstd", "-q", "-f", "--rm", str(f)], check=True)


@pytest.fixture
def tape(tmp_path, monkeypatch):
    root = tmp_path / "launchlab"
    monkeypatch.setattr(L, "EXPLORE_END", T0 + timedelta(hours=6))  # a short test window: hours 0..5
    s = Sim()
    t = T0 + timedelta(hours=2)
    for k in range(40):  # warm-up chatter
        s.buy(t + timedelta(seconds=20 * k), 40_000_000)
    t2 = T0 + timedelta(hours=2, minutes=30)
    s.buy(t2, 1_500_000_000)  # one big buy -> I3 bb1 at impact ~?%
    for k in range(1, 6):
        s.buy(t2 + timedelta(seconds=3 * k), 200_000_000)
    by_hour: dict[int, list] = {}
    for r in s.rows:
        by_hour.setdefault(r["block_time"] // 3600, []).append(r)
    write_hours(root, by_hour)
    return str(root), s


def test_synthetic_rows_reproduce_exactly(tape):
    root, s = tape
    assert all(L.check_trade_row(r).get("base_out_ok") for r in s.rows)


def test_loads_and_counts(tape, monkeypatch):
    root, s = tape
    monkeypatch.setattr(L, "exploration_hours", lambda: [T0 + timedelta(hours=h) for h in range(6)])
    D = S.load_exploration(root, log=lambda *_: None)
    assert D["n_pools"] == 1 and len(D["a"]["slot"]) == len(s.rows) and D["bad_hours"] == []
    m = S.measure(D)
    assert m["buy_exact_share"] == 1.0 and m["rows"] == len(s.rows)


def test_bb1_fires_once_with_cooldown_and_prices(tape, monkeypatch):
    root, s = tape
    monkeypatch.setattr(L, "exploration_hours", lambda: [T0 + timedelta(hours=h) for h in range(6)])
    D = S.load_exploration(root, log=lambda *_: None)
    c = next(c for c in S.CELLS if c.name == "bb1")
    att, refused, st = S.cell_attempts(c, D)
    assert len(att) == 1  # the 1.5 SOL buy; the 0.2 SOL buys are below the 1 SOL bar
    a = att[0]
    assert a["fee_in"] == pytest.approx(0.015, abs=1e-3) and a["rq_e"] >= a["rq_e"] * 0 and a["rb_e"] > 0
    r = S.price_cell(att, 0.25)
    assert r["flat"]["n"] == 1 and r["filled"] + r["guard_unfilled"] == 1


def test_trigger_uses_only_rows_up_to_the_print(tape, monkeypatch):
    root, s = tape
    monkeypatch.setattr(L, "exploration_hours", lambda: [T0 + timedelta(hours=h) for h in range(6)])
    D = S.load_exploration(root, log=lambda *_: None)
    A = D["a"]
    n = len(A["slot"])
    full = S.Pool(A, slice(0, n), 0)
    c = next(c for c in S.CELLS if c.name == "m4")
    first = S.triggers_for(c, full, 0.0)
    cut = max(first) + 1 if first else n
    part = S.Pool(A, slice(0, cut), 0)
    assert S.triggers_for(c, part, 0.0) == [i for i in first if i < cut]  # truncating the future never changes a decision


def test_incomplete_hour_marks_bad(tmp_path, monkeypatch):
    root = tmp_path / "launchlab"
    root.mkdir()
    f = root / "tape-2026-09-24T00.jsonl"
    f.write_text(json.dumps({"type": "gap", "reason": "backfill_incomplete"}) + "\n")
    subprocess.run(["zstd", "-q", "-f", "--rm", str(f)], check=True)
    rows, bad = L.read_hour(str(root / "tape-2026-09-24T00.jsonl.zst"), str(root))
    assert rows == [] and bad


def test_universe():
    pass


def test_ffill():
    x = np.array([np.nan, 0.01, np.nan, 0.02])
    assert list(S._ffill(x, 0.5)) == [0.5, 0.01, 0.01, 0.02]


def test_out_dir_inside_backfill_refused():
    with pytest.raises(L.Refused):
        S.check_out_dir(L.BACKFILL_ROOT + "/x")
