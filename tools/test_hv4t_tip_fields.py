"""HV4-T state fields (DEC-031 Amendment 23 item 8, "State fields"): which ArenaState snapshot keys and hv4_cards features an HV4 rule reads are present on both
sources, None only on the fast-0 tip source, or different in value on it, and whether any of that changes an HV4 X-arm entry decision.

HV4's frozen sha is 9ed4c5ff63935696b2a89150e13565e123047754; tools/arena_state.py, arena_paper.py, hv4_cards.py and hv4_runner.py on main are byte-equal to it
(test_main_equals_the_frozen_hv4_files). SYNTHETIC rows only: one small market is built ONCE and written twice, as public-feed-shaped rows (the FEED_ONLY_KEYS present,
no tx_index / block_time) and through the REAL tools.arena_tip_adapter.ArenaTipAdapter (those keys absent, tx_index / block_time added). Both are written in the arena
tape layout (tape-<hour>.jsonl) and read by the real ArenaState; the end-to-end check runs the REAL tools.hv4_runner.Runner (tools.test_hv4_runner harness: StubModel,
fake chain). No real tip file, tape, mirror, ledger, bots.json or decision row is opened, no network, no key. Nothing here says a book makes money; the synthetic prices
are a pullback path, not a measurement.

  /data/mal/venv/bin/python -m pytest -q tools/test_hv4t_tip_fields.py tools/test_arena_tip_adapter.py
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import arena_tip_adapter as ta  # noqa: E402
from tools import hv4_cards as C  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.test_hv4_runner import (H, MIN, MINTS, POOLS, SOL, T0, T_END, T_START, V, WSOL, X, make, rows_of, trade_rows, write_tape)  # noqa: E402

FROZEN_SHA = "9ed4c5ff63935696b2a89150e13565e123047754"
FROZEN_FILES = ("tools/arena_state.py", "tools/arena_paper.py", "tools/hv4_cards.py", "tools/hv4_runner.py")
REPO = Path(__file__).resolve().parent.parent
T_SCAN = X + 2000  # the scanner ticks at second :02 (hv4_runner.SCAN_SECOND_MS); the state is stepped to T - 1
CREATORS = {0: "CrtrA" + "1" * 39, 1: "CrtrB" + "1" * 39}  # base58-safe, 44 chars
FEED_ONLY_ALL = ta.FEED_ONLY_KEYS["pumpswap"] | ta.FEED_ONLY_KEYS["pumpfun"]


# ------------------------------------------------------------------------------------------------------------------------------------ the market, built once
@dataclass
class Ev:
    kind: str  # the reader item kind the adapter takes: trade | create | migration
    w: dict  # the walker row (tip format)
    extra: dict = field(default_factory=dict)  # keys only the public feed writes (FEED_ONLY_KEYS) for this row
    in_tip: bool = True
    in_feed: bool = True


def _walker(r: dict, venue: str) -> dict:
    w = {k: v for k, v in r.items() if k not in ("v", "type", "prog")}
    w.update({"venue": venue, "tx_index": 0, "block_time": r["t_recv_ms"] // 1000, "source": "tip"})
    return w


def _pumpswap_extra(w: dict) -> dict:
    sol = w["sol_lamports"]
    a = sol * 10_000 // 10_030 if w["side"] == "buy" else sol * 10_000 // 9_970  # pool_quote_amount: the constant-product leg; fees 20 + 5 + 5 bps of it
    out = {"pool_quote_amount": a, "lp_fee": a * 20 // 10_000, "protocol_fee": a * 5 // 10_000, "creator_fee": a * 5 // 10_000, "mint_source": "create_pool"}
    if w["side"] == "sell":
        out["supply_raw"] = 10**15  # on sells only; not in FEED_ONLY_KEYS (the adapter docstring names it), unread by every HV4 module
    return out


def build_market(*, creator_route: str = "create_row", pool_origin: str = "migration", n_sellers: int = 4, ks=(0, 1)) -> list[Ev]:
    """creator_route: "create_row" (the mint's create row is in the history) | "bonding_trades_only" (it is not; the curve's bonding trades are).
    pool_origin: "migration" (the walker emits the migration event, so the tip has pool_create) | "create_pool_event" (a pool created outside a migration: only the feed has a pool_create).
    n_sellers: distinct sellers per pool; seller 0 is the creator."""
    evs: list[Ev] = []
    for k in ks:
        cr, mint, pool = CREATORS[k], MINTS[k], POOLS[k]
        base = T0 - 6 * H + k * 1000
        if creator_route == "create_row":
            w = {"type": "create", "mint": mint, "trader": cr, "creator": cr, "event_ts": base // 1000, "quote_reserve": 30 * SOL, "base_reserve": 1_073 * 10**12, "real_token_reserves": 793 * 10**12,
                 "token_raw": 10**15, "quote_mint": WSOL, "is_mayhem_mode": False, "name": "Synth", "symbol": "SYN", "slot": 397_999_999, "signature": f"cr{k}", "event_index": 0, "tx_index": 0,
                 "block_time": base // 1000, "t_recv_ms": base, "source": "tip"}
            evs.append(Ev("create", w))
        vs, vt = 30 * SOL, 1_073 * 10**12
        for j in range(6):  # the bonding curve's own trades: the creator buys first
            sol, t = (2 if j == 0 else 1) * SOL // 2, base + (j + 1) * MIN
            tok = vt * sol // (vs + sol)
            vs, vt = vs + sol, vt - tok
            w = {"venue": "pump_bonding", "mint": mint, "pool": None, "trader": cr if j % 3 == 0 else f"Bn{k}{j}", "side": "buy", "sol_lamports": sol, "token_raw": tok, "quote_reserve": vs,
                 "base_reserve": vt, "slot": 398_000_000 + j, "signature": f"bt{k}{j}", "event_index": 0, "tx_index": j, "block_time": t // 1000, "event_ts": t // 1000, "t_recv_ms": t, "quote_mint": WSOL,
                 "source": "tip"}
            extra = {"real_quote_reserve": vs - 30 * SOL, "real_base_reserve": vt - 280 * 10**12, "protocol_fee_bps": 100, "protocol_fee": sol // 100, "creator": cr, "creator_fee_bps": 30,
                     "creator_fee": sol * 30 // 10_000}
            evs.append(Ev("trade", w, extra))
        t = T0 - 3 * H - MIN
        evs.append(Ev("migration", {"type": "complete", "mint": mint, "trader": cr, "bonding_curve": f"BC{k}", "event_ts": t // 1000, "quote_mint": WSOL, "slot": 398_999_990, "signature": f"cp{k}",
                                    "event_index": 0, "tx_index": 0, "block_time": t // 1000, "t_recv_ms": t, "source": "tip"}))
        t = T0 - 3 * H
        pc = {"type": "migration", "mint": mint, "pool": pool, "quote_mint": WSOL, "slot": 399_000_000, "signature": f"pc{k}", "event_index": 1, "tx_index": 0, "block_time": t // 1000, "t_recv_ms": t,
              "source": "tip"}
        evs.append(Ev("migration", pc, in_tip=(pool_origin == "migration")))  # the feed always has the pool_create row of the transaction
        for r in trade_rows((k,)):
            if r["type"] != "trade":
                continue
            w = _walker(r, "pumpswap")
            if r["side"] == "sell":
                i = int(r["trader"][2:]) % n_sellers
                w["trader"] = cr if i == 0 else f"S{k}{i}"
            evs.append(Ev("trade", w, _pumpswap_extra(w)))
    evs.sort(key=lambda e: (e.w["slot"], e.w["t_recv_ms"], e.w["signature"]))
    return evs


def _strip_tip_only(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("tx_index", "block_time")}


def feed_rows(evs: list[Ev]) -> list[dict]:
    """Public-feed-shaped rows: the common keys are the adapter's own row mapping (so the two sources differ only by the documented delta), minus tx_index / block_time, plus the feed-only keys."""
    out = []
    for e in evs:
        if not e.in_feed:
            continue
        if e.kind == "trade":
            row = ta.trade_row(e.w)
        elif e.kind == "create":
            row = ta.create_row(e.w)
        else:
            (row,) = ta.migration_rows(e.w)
        out.append({**_strip_tip_only(row), **e.extra})
    return out


def tip_rows(evs: list[Ev]) -> list[dict]:
    adapter = ta.ArenaTipAdapter()
    rows = adapter.feed([(e.kind, e.w) for e in evs if e.in_tip])
    assert [r for r in rows if r["type"] == "gap"] == [] and not any(k.startswith("rows_dropped") for k in adapter.stats), dict(adapter.stats)
    return rows


def state_of(rows: list[dict], d: Path, until: int = T_SCAN - 1) -> ArenaState:
    write_tape(d, rows)  # the arena tape layout: tape-<hour>.jsonl
    st = ArenaState(d, start_ms=0)
    st.poll(until_ms=until)
    assert not st.counters["apply_errors"] and not st.counters["bad_lines"]
    return st


# ------------------------------------------------------------------------------------------------------------------------------------ the field listing
def _flat(snap: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def walk(prefix: str, x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif prefix == "candles":
            for fld in ("t_min_ms", "o", "h", "l", "c", "vol_sol", "n", "buy_sol", "sell_sol"):
                out[f"candles.{fld}"] = tuple(c[fld] for c in x)
        elif prefix in ("top_net.buyers", "top_net.sellers"):
            out[prefix] = tuple((d["trader"], d["net_sol"]) for d in x)
        else:
            out[prefix] = x

    walk("", snap)
    return out


def classify(feed: dict[str, Any], tip: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k in sorted(set(feed) | set(tip)):
        f, t = feed.get(k), tip.get(k)
        out[k] = ("both_none" if f is None else "same") if f == t else "none_only_on_tip" if (t is None and f is not None) else "none_only_on_feed" if f is None else "differs_on_tip"
    return out


def listing(evs: list[Ev], tmp: Path, pool_k: int = 0) -> dict[str, dict[str, str]]:
    """{"snapshot": {path: class}, "features": {field: class}} for pool k at T_SCAN, from the same market on the two sources."""
    sf, st_ = state_of(feed_rows(evs), tmp / "feed"), state_of(tip_rows(evs), tmp / "tip")
    pid = POOLS[pool_k]
    snaps = [s.snapshot(pid) for s in (sf, st_)]
    feats = [C.features(sn, list(s.pool(pid).events), as_of_ms=T_SCAN) for sn, s in zip(snaps, (sf, st_))]
    return {"snapshot": classify(_flat(snaps[0]), _flat(snaps[1])), "features": classify(feats[0], feats[1])}


def of_class(lst: dict[str, str], cls: str) -> set[str]:
    return {k for k, v in lst.items() if v == cls}


# ------------------------------------------------------------------------------------------------------------------------------------ the decision
def chain_facts(T: int, slot: int) -> dict:
    """Chain facts are RPC facts, not tape facts: the SAME dict goes to both sources, so any decision difference below comes from the state fields."""
    return {"as_of_ms": T - 1000, "as_of_slot": slot, "mint_authority": None, "freeze_authority": None, "token_program": C.SPL_TOKEN_PROGRAM, "extensions": [], "mayhem": False,
            "top1_frac": 0.05, "top10_frac": 0.30, "holders_as_of_ms": T - 1000, "quote_sol_15m_ago": 62.0, "quote_sol_15m_ago_as_of_ms": T - 15 * MIN}


def x_decision(st: ArenaState, pid: str, T: int = T_SCAN) -> dict[str, Any]:
    """The X-arm entry decision up to the book gates, with the hv4_cards calls Runner._scan / _card / _x_round make for one pool: filters, features, setups, code_vetoes, build_card.
    An X arm (no LLM) enters iff the pool has a setup tag and its card is built; the gates (open slots, rate, cooldown, day loss) read the book, not the tape."""
    snap = st.snapshot(pid)
    prints = list(st.pool(pid).events)
    slot = snap["last_slot"]
    fr = C.filters(snap, as_of_ms=T)
    feats = C.features(snap, prints, as_of_ms=T)
    tags = C.setups(feats, pool_id=pid, as_of_ms=T)
    chain = chain_facts(T, slot)
    vr = C.code_vetoes(snap, prints, chain, as_of_ms=T, as_of_slot=slot, feats=feats)
    sr = C.sell_route(snap, prints, chain, as_of_ms=T)
    try:
        card = C.build_card(snap, prints, chain, as_of_ms=T, as_of_slot=slot, setup=tags[0] if tags else None, feats=feats)
        built = True
    except C.CardRefused as e:
        card, built = None, tuple(e.reasons)
    return {"filters_passed": fr.passed, "filters_failed": fr.failed, "age_tier": fr.age_tier, "age_known": fr.age_known, "max_stake": fr.max_stake, "tags": tuple(t.type for t in tags),
            "veto_reasons": vr.reasons, "sell_route_ok": sr["ok"], "sell_route_sellers": sr["noncreator_sellers_60m"], "card": built, "card_sha": C.card_sha256(card) if card else None,
            "x_entry": bool(tags) and card is not None}


def both_decisions(evs: list[Ev], tmp: Path, pool_k: int = 0) -> tuple[dict, dict]:
    pid = POOLS[pool_k]
    return (x_decision(state_of(feed_rows(evs), tmp / "feed"), pid), x_decision(state_of(tip_rows(evs), tmp / "tip"), pid))


# ------------------------------------------------------------------------------------------------------------------------------------ the real Runner, end to end
def run_view(evs: list[Ev], tmp: Path, source: str) -> dict[str, Any]:
    """The X arms' entry decisions from the REAL Runner on one source: its scan rows and, per X arm, the entry / skip rows (pool, size, decision slot, setup, reasons)."""
    run = make(tmp, rows=(feed_rows(evs) if source == "feed" else tip_rows(evs)))
    run.run(T_START, T_END)
    assert not run.hook_errors and run.counters.get("leakage_refused", 0) == 0
    v: dict[str, Any] = {"scan": [(r["n_filter_pass"], tuple(sorted(r["n_tags"].items())), r["n_cards"]) for r in rows_of(run, "SCAN", "scan")],
                         "refused": [(r["pool"], tuple(r["reasons"])) for r in rows_of(run, "SCAN", "card_refused")], "counters": {k: n for k, n in run.counters.items() if k.startswith("filter_")}}
    for arm in ("CON-X", "BIG-X"):
        v[arm] = [(r["kind"], r["pool"], r.get("size"), r.get("decision_slot"), r.get("setup"), tuple(r.get("reasons") or ())) for r in rows_of(run, arm) if r["kind"] in ("entry", "skip")]
    v["card_shas"] = sorted(r["card_sha256"] for r in rows_of(run, "CON-X", "entry"))
    return v
