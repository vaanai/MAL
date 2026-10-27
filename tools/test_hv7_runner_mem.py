"""HV7 runner memory bound (Hv7Config.bound_memory): decision-neutral, held across open positions, and bounded. SYNTHETIC tape built here (pool ids pass the card's
safe-id check; 12 pools, so the arms really enter), StubModel only. No real tape, no API, no network. Nothing here says HV7 makes money."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from tools import hv7_runner as R
from tools.test_hv4_runner import MIN, T0, V, WSOL, price, write_tape
from tools.test_hv7_runner import HOUR, SOL, T_E1, T_E2, _env, make, rows_of  # noqa: F401  (_env is autouse)

LETTERS = "ABCDEFGHJKLM"      # base58 only
NP = len(LETTERS)
P = {k: "PxxH" + LETTERS[k] + "1" * 39 for k in range(NP)}
MT = {k: "MintH" + LETTERS[k] + "1" * 39 for k in range(NP)}


def tape(last_min=260):
    rows, n = [], 0
    for k in range(NP):
        rows.append({"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 399_000_000, "t_recv_ms": T0 - 3 * HOUR, "signature": f"pc{k}", "event_index": 0,
                     "pool": P[k], "mint": MT[k], "quote_mint": WSOL})
        for mi in range(-80, last_min + 1):
            for j in range(12):
                n += 1
                t = T0 + 10 * MIN + mi * MIN + j * 5000 + 500 + k * 300
                p = price(k % 2, min(mi, 90) + j / 12)
                buy = j % 4 != 3
                sol = int((0.15 if buy else 0.07) * (1 + 0.3 * k) * SOL)
                q, b = 60 * SOL, int((60 * SOL + V) / SOL / p * 1_000_000)
                rows.append({"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 400_000_000 + (t - (T0 - 3 * HOUR)) // 400, "t_recv_ms": t,
                             "signature": f"s{n}", "event_index": 0, "pool": P[k], "mint": MT[k], "trader": f"B{k}{n % 10}" if buy else f"S{k}{(n // 4) % 4}",
                             "side": "buy" if buy else "sell", "sol_lamports": sol, "token_raw": int(sol / SOL / p * 1_000_000), "quote_reserve": q, "base_reserve": b,
                             "virtual_quote_reserves": V, "quote_mint": WSOL, "event_ts": t // 1000})
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


def script(req):
    order = sorted(req.ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())
    return json.dumps([{"id": i, "why": "f.vol_60m is highest"} for i in order])


def ledger_digest(run):
    """Every engine ledger row (fills included) with the write instant removed, in file order."""
    out = []
    for f in sorted(Path(run.engine.ledger.root).glob("*/trades-*.jsonl")):
        for ln in f.read_bytes().splitlines():
            r = json.loads(ln)
            if str(r.get("trade_id", "")).startswith("start-"):
                continue                    # the engine's start rows carry the wall-clock instant of each run
            r.pop("written_ms", None)
            r.pop("engine_id", None)        # derived from the engine's own directory, which differs between the two runs
            out.append((f.parent.name, f.name, json.dumps(r, sort_keys=True)))
    return hashlib.sha256(json.dumps(out).encode()).hexdigest(), len(out)


def drive(tmp, bound, rows):
    run = make(tmp, script=script, rows=rows, cfg_kw={"bound_memory": bound})
    trims = []
    orig = run.engine.trim_history

    def spy():
        n = orig()
        trims.append((run.sim_now, n, len(run.engine.positions)))
        return n

    run.engine.trim_history = spy
    run.run(T0 + HOUR, T_E1 + 75 * MIN)
    return run, trims


def test_bound_changes_no_decision_with_positions_held_across_trims(tmp_path_factory):
    rows = tape()
    on, trims = drive(tmp_path_factory.mktemp("h7m_on"), True, rows)
    off, _ = drive(tmp_path_factory.mktemp("h7m_off"), False, rows)
    # the arms really entered, on a card that passed (no bad_pool_id), >= 3 candidates in the period
    per = rows_of(on, "SCAN", "period")[0]
    assert not per["no_trade"] and len(per["screen"]) >= 3
    entries = {a: rows_of(on, a, "entry") for a in R.ARM_ORDER}
    assert all(entries[a] for a in R.ARM_ORDER) and len([r for r in entries["U"] if r["ms"] == T_E1]) == 8
    assert not on.hook_errors and not off.hook_errors
    # trims that dropped prints while positions were open (U holds 8 pools; the other 4 are trimmable)
    assert on.counters["engine_prints_trimmed"] > 0
    assert any(n > 0 and pos > 0 for _, n, pos in trims), trims[-3:]
    assert off.counters.get("engine_trims", 0) == 0
    # a 60-min time exit priced AFTER a trim
    times = [r for a in R.ARM_ORDER for r in rows_of(on, a, "sell") if r["reason"] == "max_hold" or "time" in r["reason"]]
    assert times, {r["reason"] for a in R.ARM_ORDER for r in rows_of(on, a, "sell")}
    first_exit = min(r["ms"] for r in times)
    assert any(t < first_exit and n > 0 for t, n, _ in trims) and any(t <= first_exit for t, _, _ in trims)
    # all four equivalence checks
    assert on.decision_md5() == off.decision_md5()
    d_on, n_on = ledger_digest(on)
    d_off, n_off = ledger_digest(off)
    assert n_on == n_off and n_on > 0 and d_on == d_off                       # byte-identical engine ledger (fills)
    for k in ("engine_rows_below_trim_floor", "engine_refused_history_trimmed"):
        assert on.counters[k] == 0 and off.counters[k] == 0, k
    assert on.counters["engine_hist_trimmed_prints"] > 0 and off.counters["engine_hist_trimmed_prints"] == 0
    held = lambda r: sum(len(h.slot) for h in r.engine.pools.values())  # noqa: E731
    assert held(on) < held(off)
    print("MEM", {"held_on": held(on), "held_off": held(off), "trims": len(trims), "ledger_rows": n_on, "md5": on.decision_md5()})


def test_revived_pool_after_sweep_keeps_only_post_revival_quotes():
    """The documented bound_memory caveat: a pool idle past the eviction horizon that the state no longer holds loses its quote history (as in HV6)."""
    q = R.QuoteHistory()
    q.add("p", 1_000, 60.0)
    assert q.sweep(7 * HOUR, 6 * HOUR, held=lambda k: False) == 1
    assert q.ref("p", 7 * HOUR) is None and q.peak("p", 7 * HOUR) is None
    q.add("p", 7 * HOUR + 1000, 61.0)
    assert q.t["p"] == [7 * HOUR + 1000]
