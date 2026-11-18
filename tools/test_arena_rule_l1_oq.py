"""l1-floor-oq-v1 (FLOOR on other-quote PumpSwap pools): trigger parity with scripts/research/floor_other_quote_funnel.py, conversion arithmetic, per-pool fees, refusals, WSOL pools
never entering, the unchanged WSOL rule. Synthetic rows only; no tape, ledger, mark or book is opened. Nothing here says the rule makes money."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import arena_rule_l1_oq as O
from tools.arena_pool_table import WSOL, PoolTable
from tools.arena_rules import Buy, Sell

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "research"))
import floor_other_quote_funnel as FUN  # noqa: E402

HOUR = 3_600_000
H0 = 1_792_000_000_000 // HOUR * HOUR
V = 17_600_000_000
SOLX = 1_000_000_000
Q1 = "QuoteMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
QW_Q, QW_B = 100 * SOLX, 100 * SOLX  # the quote's WSOL pool: Qw = Bw = 100e9 -> mid exactly 1 lamport per raw quote unit
FEE_W = 0.0025   # lp 2.0e6 + protocol 0.5e6 over 1e9
FEE_P = 0.01     # lp 7e6 + protocol 3e6 over 1e9 (not the 1.25% constant)


def fee_fields(f):
    amt = 1_000_000_000
    return {"pool_quote_amount": amt, "lp_fee": int(amt * f * 0.8), "protocol_fee": int(amt * f * 0.2)}


def row(t, pool, side="buy", q=10 * SOLX, sol=200_000_000, v=V, mint=None, quote=Q1, fee=FEE_P, b=10**12):
    r = {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 1000 + (t - H0) // 400, "t_recv_ms": t, "signature": f"s{t}-{pool}", "event_index": 0,
         "pool": pool, "mint": mint or "M" + pool, "trader": "tr", "side": side, "sol_lamports": sol, "token_raw": sol * 1000, "quote_reserve": q, "base_reserve": b,
         "virtual_quote_reserves": v, "quote_mint": quote, "event_ts": t // 1000}
    if fee is not None:
        r.update(fee_fields(fee))
    return r


def qrow(t, fee=FEE_W):
    """a print of the quote's own WSOL pool (base mint = Q1): its pre-trade state is the quote price source"""
    return row(t, "QW", "buy", q=QW_Q, sol=1_000_000, v=0, mint=Q1, quote=WSOL, fee=fee, b=QW_B)


def stream(drop_quote=Q1, quote_rows=True, drop_q=600_000_000, pool_fee=FEE_P, v_drop=V, quote_until=None, p="P"):
    """a filler WSOL pool, the quote's WSOL pool printing every 5 min (price source), pool P: first print at 4 h, every 30 min to 29 h, then a drained SELL at 30 h 15 min"""
    rows = [qrow(H0 + 1000)] if quote_rows else []
    end = H0 + 31 * HOUR if quote_until is None else quote_until
    t = H0 + 5 * 60_000
    while quote_rows and t <= end:
        rows.append(qrow(t))
        t += 300_000
    t = H0 + 4 * HOUR
    while t <= H0 + 29 * HOUR:
        rows.append(row(t + 1, p, "buy" if (t // 1800000) % 2 else "sell", fee=pool_fee))
        t += 1_800_000
    rows.append(row(H0 + 30 * HOUR + 900_000, p, "sell", q=drop_q, sol=200_000_000, quote=drop_quote, v=v_drop, fee=pool_fee))
    return sorted(rows, key=lambda r: r["t_recv_ms"])


class FakeCtx:
    def __init__(self, tab, positions=(), now_ms=0):
        self.table, self.positions, self.now_ms = tab, list(positions), now_ms


def play(rows, tab=None, rule=None):
    """feed every row to the table then the rule, as HostV2 does; returns (table, [(row, orders)])"""
    tab = tab or O.OQTable()
    rule = rule or O.make_rule()
    out = []
    for r in rows:
        tab.update(r)
        out.append((r, rule.on_row(FakeCtx(tab, now_ms=r["t_recv_ms"]), r)))
    return tab, out


def buys(out):
    return [(r, o) for r, os_ in out for o in os_ if isinstance(o, Buy)]


# ------------------------------------------------------------------------------------------------ trigger parity with the funnel module
def _variants():
    yield "base", stream()
    yield "no_quote_price", stream(quote_rows=False)
    yield "v_out_of_band", stream(v_drop=2 * V)
    yield "vault_too_big", stream(drop_q=5 * SOLX)
    yield "wsol_drop_row", stream(drop_quote=WSOL)
    r = stream()
    yield "young_pool", [x for x in r if not (x["pool"] == "P" and x["t_recv_ms"] < H0 + 20 * HOUR)]
    yield "usdc_class", [dict(x, quote_mint=FUN.USDC) if x["pool"] != "QW" else x for x in stream()]


@pytest.mark.parametrize("name,rows", list(_variants()), ids=[n for n, _ in _variants()])
def test_trigger_parity_with_funnel(name, rows):
    qrows = [x for x in rows if x["pool"] == "QW"]
    if name == "usdc_class":  # the funnel's price file is keyed by the quote mint of the OQ rows
        qrows = [dict(x, mint=FUN.USDC) for x in qrows]
        rows = sorted([x for x in rows if x["pool"] != "QW"] + qrows, key=lambda x: x["t_recv_ms"])
    qm = FUN.USDC if name == "usdc_class" else Q1
    c = FUN.Counter({qm: 1.0})
    # the funnel has a static price for the whole run; the missing-price variant is the one where mine has none (staleness has no funnel equivalent: its own test below), so the funnel gets no price file entry then
    if name == "no_quote_price":
        c = FUN.Counter({})
    tab, seen, n_trig = O.OQTable(), {}, 0
    for r in rows:
        c.row(r)
        tab.update(r)
        stage, info = O.OQRule.trigger(tab, r)
        if stage is not None:
            i = O.STAGES.index(stage)
            seen.setdefault(r["pool"], set()).update(range(i + 1))
            n_trig += stage == O.STAGES[-1]
    theirs = {}
    for cell, reach in c.reach.items():
        for si, s in enumerate(FUN.STAGES):
            for pool in reach.get(s, ()):
                theirs.setdefault(pool, set()).add(si)
    assert O.STAGES == FUN.STAGES
    assert seen == theirs
    assert n_trig == sum(c.trig_rows.values())
    if name == "base":
        assert n_trig == 1  # the fixture really triggers, so the parity above is not vacuous


# ------------------------------------------------------------------------------------------------ rule output, conversion arithmetic, fees
def test_entry_once_with_quote_pricing_in_meta():
    tab, out = play(stream())
    b = buys(out)
    assert len(b) == 1 and b[0][1].pool == "P"
    m = b[0][1].meta
    assert m["rule"] == "l1-floor-oq-v1" and m["quote_class"] == "other" and m["quote_mint"] == Q1
    assert m["quote_mid_lamports_per_raw"] == 1.0 and m["pool_fee"] == pytest.approx(FEE_P) and m["quote_pool_fee"] == pytest.approx(FEE_W)
    assert m["age_s"] >= O.AGE_LO and m["r6"] < 0.8 and m["vol24_sol"] >= 1.0
    assert tab.refused == {} and tab.trig_rows == 1


def test_conversion_arithmetic_entry_and_exit():
    qs = {"Qw": float(QW_Q), "Bw": float(QW_B), "fee": FEE_W}
    ps = {"Qe": 20e9, "Be": 4e12, "fee": FEE_P}
    S = 0.25e9
    e = O.price_entry(S, qs, ps)
    net = S * (1 - FEE_W)
    qu = QW_B * net / (QW_Q + net)
    assert e["quote_units"] == pytest.approx(qu)
    assert e["tokens"] == pytest.approx(4e12 * qu * (1 - FEE_P) / (20e9 + qu * (1 - FEE_P)))
    assert e["conv_cost_lamports"] == pytest.approx(S - qu)  # mid is exactly 1: cost = stake - SOL value of the quote units
    assert e["conv_cost_lamports"] > S * FEE_W  # fee plus impact
    x = O.price_exit(e["tokens"], qs, ps)
    qo = 20e9 * e["tokens"] / (4e12 + e["tokens"]) * (1 - FEE_P)
    assert x["quote_units"] == pytest.approx(qo)
    assert x["sol_lamports"] == pytest.approx(QW_Q * qo / (QW_B + qo) * (1 - FEE_W))
    assert x["conv_cost_lamports"] == pytest.approx(qo - x["sol_lamports"])
    pnl = O.trip_pnl(S, e, x)
    assert pnl < 0 and -pnl > S * 2 * (FEE_W + FEE_P)  # a round trip at an unmoved pool loses at least both fee legs of both pools


def test_per_pool_fee_is_used_not_the_wsol_default():
    ps_lo, ps_hi = {"Qe": 20e9, "Be": 4e12, "fee": 0.001}, {"Qe": 20e9, "Be": 4e12, "fee": 0.03}
    qs = {"Qw": float(QW_Q), "Bw": float(QW_B), "fee": FEE_W}
    assert O.price_entry(0.25e9, qs, ps_lo)["tokens"] > O.price_entry(0.25e9, qs, ps_hi)["tokens"]
    _, out = play(stream(pool_fee=0.03))
    assert buys(out)[0][1].meta["pool_fee"] == pytest.approx(0.03)
    assert O.row_fee(row(1, "P", fee=0.03)) == pytest.approx(0.03)
    assert O.row_fee(row(1, "P", fee=None)) is None


# ------------------------------------------------------------------------------------------------ refusals
def test_no_quote_price_is_refused_and_counted():
    tab, out = play(stream(quote_rows=False))
    assert buys(out) == [] and tab.refused["no_quote_price"] >= 1 and tab.trig_rows == 0


def test_stale_quote_price_is_refused_and_counted():
    tab, out = play(stream(quote_until=H0 + 30 * HOUR))  # the quote's pool last printed 15 min before the drop: older than QUOTE_STALE_MS
    assert buys(out) == [] and tab.refused["stale_quote_price"] >= 1


def test_quote_pool_row_without_fee_fields_gives_no_price():
    rows = [dict(x) for x in stream()]
    for x in rows:
        if x["pool"] == "QW":
            for k in ("lp_fee", "protocol_fee", "pool_quote_amount"):
                x.pop(k)
    tab, out = play(rows)
    assert buys(out) == [] and tab.refused["no_quote_price"] >= 1


def test_pool_without_fee_fields_is_refused_not_defaulted():
    tab, out = play(stream(pool_fee=None))
    assert buys(out) == [] and tab.refused["no_pool_fee"] == 1


def test_deepest_quote_pool_is_the_price_source():
    rows = stream()
    shallow = dict(qrow(H0 + 30 * HOUR + 800_000), pool="QW2", quote_reserve=SOLX, base_reserve=10**12, signature="x2")
    rows.insert(-1, shallow)
    tab, out = play(rows)
    assert tab.qstate[Q1]["pool"] == "QW" and len(buys(out)) == 1


# ------------------------------------------------------------------------------------------------ WSOL pools never enter
def test_wsol_pool_never_enters_this_rule():
    tab, out = play(stream(drop_quote=WSOL))
    assert buys(out) == [] and "P" in tab.first  # P's earlier prints are other-quote; the deciding row is WSOL: refused first
    rows = [dict(x, quote_mint=WSOL) if x["pool"] == "P" else x for x in stream()]
    tab, out = play(rows)
    assert buys(out) == [] and "P" not in tab.first and "P" not in tab.prints


# ------------------------------------------------------------------------------------------------ exits
def _open_position(tab, out):
    r, b = buys(out)[0]
    return SimpleNamespace(position_id="pid1", pool=b.pool, meta=b.meta, exiting=False, decision_ms=r["t_recv_ms"], decision_slot=r["slot"])


def test_take_profit_and_time_exit_are_floors():
    tab, out = play(stream())
    pos = _open_position(tab, out)
    rule = O.make_rule()
    t = pos.decision_ms
    land = row(t + 10_000, "P", "sell", q=400_000_000, sol=1_000_000)  # the landing print (>= decision slot + 7 + k + 1): the mark is its pre-trade state with our buy
    tab.update(land)
    assert rule.on_row(FakeCtx(tab, [pos], land["t_recv_ms"]), land) == []
    big = row(t + 60_000, "P", "buy", q=60 * SOLX, sol=50_000_000)  # spot several times the mark
    tab.update(big)
    o = rule.on_row(FakeCtx(tab, [pos], big["t_recv_ms"]), big)
    assert o == [Sell("pid1", O.REASON_TP)]
    pos2 = SimpleNamespace(position_id="p2", pool="P", meta={}, exiting=False, decision_ms=1000, decision_slot=1)
    assert rule.on_tick(FakeCtx(tab, [pos2]), 1000 + 86_400_000 + 1_899) == []
    assert rule.on_tick(FakeCtx(tab, [pos2]), 1000 + 86_400_000 + 1_900) == [Sell("p2", O.REASON_TIME)]


def test_cooldown_blocks_reentry_within_60s():
    tab, out = play(stream()[:-1])
    drop = stream()[-1]
    tab.note_exit("P", drop["t_recv_ms"] - 1000)
    tab.update(drop)
    assert O.make_rule().on_row(FakeCtx(tab, now_ms=drop["t_recv_ms"]), drop) == []


# ------------------------------------------------------------------------------------------------ table and host plumbing
def test_checkpoint_roundtrip(tmp_path):
    tab, _ = play(stream())
    tab.checkpoint(tmp_path / "t.pkl")
    t2 = O.OQTable.load(tmp_path / "t.pkl")
    assert t2.first == tab.first and t2.qstate == tab.qstate and t2.pfee == tab.pfee and len(t2.prints["P"]) == len(tab.prints["P"]) and t2.mode == "other_quote"


def test_wsol_table_still_drops_other_quote_rows_and_default_is_unchanged():
    t = PoolTable("first_print")
    assert t.update(row(H0 + 4 * HOUR, "P")) is False and "P" not in t.seen and "P" not in t.prints  # arena_pool_table still drops every non-WSOL row
    from tools import arena_rules_v2 as R2
    assert R2.RULE_MODULES_V2["l1-floor-v2"] == "tools.arena_rule_l1"
    assert not hasattr(__import__("tools.arena_rule_l1", fromlist=["x"]).make_rule(), "table_cls")


def test_load_rules_v2_builds_the_oq_rule():
    from tools.arena_rules_v2 import load_rules_v2

    r = load_rules_v2(["l1-floor-oq-v1"])[0]
    assert r.bot_id == "l1-floor-oq-v1" and r.table_mode == "other_quote" and r.table_cls is O.OQTable and r.universe == "pumpswap_other_quote"


def test_host_v2_builds_the_oq_table_and_the_universe_fence_blocks_the_buy(tmp_path, monkeypatch):
    """HostV2 builds the rule's own table class and feeds it; the host's universe fence blocks the entry (FakeEngine: no engine pricing is exercised)."""
    import collections
    from tools.arena_rules import HostConfig, register_rules
    from tools.arena_rules_testkit import FakeEngine, write_tape
    from tools.arena_rules_v2 import HostV2, load_rules_v2
    from tools.arena_state import ArenaState

    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
    tape = tmp_path / "tape"
    write_tape(tape, stream())
    rules = load_rules_v2(["l1-floor-oq-v1"])
    reg = tmp_path / "bots.json"
    register_rules(rules, str(reg), "abcdef1234567", H0, now_ms=H0 - 1, verify_sha=False)
    cfg = HostConfig(root=str(tmp_path / "rules"), registry=str(reg), frozen_sha="abcdef1234567", clock_start_ms=H0)
    host = HostV2(cfg, rules, ArenaState(tape, start_ms=0), FakeEngine(tmp_path / "rules"))
    assert isinstance(host.table, O.OQTable)
    host.run(H0, H0 + 34 * HOUR)
    e = [r for r in host.log.rows("l1-floor-oq-v1") if r.get("kind") == "entry"]
    assert not host.hook_errors and host.fatal is None
    # the v1 host's universe fence (arena_rules.in_universe: only pumpswap_wsol, a pinned file not edited here) still blocks the buy: no book can run this rule by accident.
    # Lifting it, and pricing in the engine, is the follow-up named in the PR body.
    assert e == [] and host.counters.get("blocked_universe") == 1
