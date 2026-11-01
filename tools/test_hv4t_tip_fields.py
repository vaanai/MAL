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


def compare_rows(rows_a: list[dict], rows_b: list[dict], tmp: Path, pool_k: int = 0) -> dict[str, dict[str, str]]:
    """{"snapshot": {path: class}, "features": {field: class}} for pool k at T_SCAN: state A (the "feed") against state B (the "tip"), each read from its own tape dir."""
    sa, sb = state_of(rows_a, tmp / "a"), state_of(rows_b, tmp / "b")
    pid = POOLS[pool_k]
    snaps = [s.snapshot(pid) for s in (sa, sb)]
    feats = [C.features(sn, list(s.pool(pid).events), as_of_ms=T_SCAN) for sn, s in zip(snaps, (sa, sb))]
    return {"snapshot": classify(_flat(snaps[0]), _flat(snaps[1])), "features": classify(feats[0], feats[1])}


def listing(evs: list[Ev], tmp: Path, pool_k: int = 0) -> dict[str, dict[str, str]]:
    return compare_rows(feed_rows(evs), tip_rows(evs), tmp, pool_k)


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


# ------------------------------------------------------------------------------------------------------------------------------------ pins: what main is, what the rows are
S1 = {}
S2 = {"creator_route": "bonding_trades_only"}  # the curve's create row is outside the history; its bonding trades are inside
S2_THREE = {"creator_route": "bonding_trades_only", "n_sellers": 3}  # three sellers in the pool, one of them the creator
S3 = {"pool_origin": "create_pool_event"}  # a pool created outside a migration: the walker emits no pool_create for it

# value differences of the constant-product post-trade price (the source's view, Am.23 item 8): exactly these, in the sum of one LP fee per print
PRICE_SNAP = {"ath", "candles.c", "candles.h", "candles.l", "candles.o", "drawdown_from_ath", "price", "reserves.quote_eff_sol", "reserves.quote_sol"}
PRICE_FEAT = {"close_vs_hi_6h", "dd_ath", "hi_6h", "impact_rt_025", "impact_rt_050", "impact_rt_100", "lo_6h", "price", "quote_eff_sol", "quote_sol", "rebound", "ret_15m", "ret_1m", "ret_5m",
              "ret_60m"}
CREATOR_SNAP = {"creator", "creator_net_sol"}
CREATOR_FEAT_NONE = {"creator_net_sol_60m", "creator_net_sol_life"}
AGE_SNAP_NONE = {"age_s", "created_kind", "created_ms"}
AGE_FEAT_NONE = {"avg_vol_15m_6h", "close_vs_hi_6h", "hi_6h", "lo_6h", "rebound", "vol_6h"}  # the 6 h block needs a known age or 360 min of watching (hv4_cards._covered)
FEED_ONLY_PUMPSWAP = {"pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee", "mint_source"}
FEED_ONLY_BONDING = {"real_quote_reserve", "real_base_reserve", "protocol_fee_bps", "protocol_fee", "creator", "creator_fee_bps", "creator_fee"}

# (state key or row key, reader, file, line on main, text that must be on that line). The doc table copies this list.
READ_SITES = [
    ("prog", "hv4_cards.filters (venue_pumpswap)", "tools/hv4_cards.py", 457, 'snap.get("prog")'),
    ("quote_wsol", "hv4_cards.filters, features, code_vetoes, sell_route", "tools/hv4_cards.py", 459, 'snap.get("quote_wsol") is True'),
    ("now_ms", "hv4_cards._state_lag (filters state_fresh, code_vetoes, sell_route)", "tools/hv4_cards.py", 409, 'snap.get("now_ms")'),
    ("age_s", "hv4_cards._age (filters age_min and age_max, features age_s, code_vetoes mayhem)", "tools/hv4_cards.py", 416, 'snap.get("age_s")'),
    ("created_known", "hv4_cards._age", "tools/hv4_cards.py", 417, 'snap.get("created_known")'),
    ("age_since_first_seen_s", "hv4_cards._age (the lower bound when created_known is false)", "tools/hv4_cards.py", 419, 'snap.get("age_since_first_seen_s")'),
    ("reserves.v_sol", "hv4_cards._v_lamports (filters v_missing / v_band)", "tools/hv4_cards.py", 429, 'res.get("v_sol")'),
    ("reserves.quote_sol", "hv4_cards.filters (quote_floor), features, code_vetoes (quote_below_floor, quote_15m)", "tools/hv4_cards.py", 471, 'snap.get("reserves")'),
    ("reserves.quote_eff_sol", "hv4_cards.filters (impact_025), features (impact_rt_*), sell_route (sim_sell_return)", "tools/hv4_cards.py", 471, 'snap.get("reserves")'),
    ("windows.15m, windows.60m", "hv4_cards.filters (prints_15m, signers_15m, vol_60m), features (vol_*, net_flow_*, n_buys_*, n_sells_*)", "tools/hv4_cards.py", 474, '_win(snap, "15m")'),
    ("last_ms", "hv4_cards.filters (last_print), features (last_print_s)", "tools/hv4_cards.py", 479, 'snap.get("last_ms")'),
    ("price", "hv4_cards.features (price, ret_*, hi_6h, lo_6h, rebound, close_vs_hi_6h)", "tools/hv4_cards.py", 658, 'snap.get("price")'),
    ("drawdown_from_ath", "hv4_cards.features (dd_ath)", "tools/hv4_cards.py", 671, 'snap.get("drawdown_from_ath")'),
    ("top_net", "hv4_cards.features (top_buyer_net_sol, top_seller_net_sol), build_card (tops)", "tools/hv4_cards.py", 672, 'snap.get("top_net")'),
    ("creator_net_sol", "hv4_cards.features (creator_net_sol_life)", "tools/hv4_cards.py", 676, 'snap.get("creator_net_sol")'),
    ("creator", "hv4_cards.features (creator_net_sol_60m, noncreator_sellers_60m)", "tools/hv4_cards.py", 703, 'snap.get("creator")'),
    ("creator", "hv4_cards.sell_route (sellers other than the creator; code_vetoes sell_route)", "tools/hv4_cards.py", 873, 'snap.get("creator")'),
    ("candles", "hv4_cards._series (features ret_*, 6 h block; build_card c1 / c5)", "tools/hv4_cards.py", 619, 'snap.get("candles")'),
    ("candle_retention_min", "hv4_cards.features (which ret_N and 6 h fields exist)", "tools/hv4_cards.py", 682, 'snap.get("candle_retention_min")'),
    ("last_slot", "hv4_cards.assert_as_of", "tools/hv4_cards.py", 371, 'snap.get("last_slot")'),
    ("created_known, age_s, age_since_first_seen_s, gaps_since_first_seen", "hv4_runner.holder_cover (card risk.holder_count_cover)", "tools/hv4_runner.py", 378, 'snap.get("gaps_since_first_seen")'),
    ("Pool.net", "hv4_runner.Runner._tape_facts (card risk.holder_count)", "tools/hv4_runner.py", 923, "p.net.values()"),
    ("Pool.q_real", "hv4_runner.Runner._on_row -> QuoteHistory (code_vetoes quote_15m: quote_sol_15m_ago, quote_sol_peak)", "tools/hv4_runner.py", 682, "p.q_real / 1e9"),
    ("row.pool_quote_amount, row.lp_fee", "arena_state.ArenaState._trade -> pumpswap_post (price, q_real, candles, ath)", "tools/arena_state.py", 426, 'row.get("lp_fee"), row.get("pool_quote_amount")'),
    ("row.creator (bonding trade)", "arena_state.ArenaState._trade (Pool.creator of a curve, only if still unset)", "tools/arena_state.py", 434, 'p.creator = row["creator"]'),
    ("row.creator / row.trader (create row)", "arena_state.ArenaState._create (Pool.creator of a curve)", "tools/arena_state.py", 381, 'row.get("creator") or row.get("trader")'),
    ("curve.creator", "arena_state.ArenaState._link_mint (copied to the pool once, when the pool is linked to its mint)", "tools/arena_state.py", 407, "p.creator = p.creator or curve.creator"),
    ("pool_create row", "arena_state.ArenaState._pool_create (created_ms, created_kind, create_slot)", "tools/arena_state.py", 394, '"pool_create"'),
]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=60)


def test_main_equals_the_frozen_hv4_files():
    """The four modules this listing reads are, in this checkout, byte-equal to HV4's frozen sha (blob ids compared; the working-tree bytes are hashed, not trusted)."""
    if _git("cat-file", "-e", f"{FROZEN_SHA}^{{commit}}").returncode != 0:
        pytest.skip("the frozen commit is not in this clone")
    for f in FROZEN_FILES:
        frozen = _git("rev-parse", f"{FROZEN_SHA}:{f}").stdout.strip()
        here = _git("hash-object", f).stdout.strip()
        assert frozen and frozen == here, f


def test_the_cited_read_sites_are_on_those_lines():
    for key, reader, f, line, needle in READ_SITES:
        text = (REPO / f).read_text().splitlines()[line - 1]
        assert needle in text, (key, f, line, text.strip()[:120])


def test_the_feed_only_keys_are_pinned_and_the_synthetic_feed_rows_carry_exactly_them():
    assert ta.FEED_ONLY_KEYS["pumpswap"] == FEED_ONLY_PUMPSWAP and ta.FEED_ONLY_KEYS["pumpfun"] == FEED_ONLY_BONDING
    evs = build_market(ks=(0,))
    feed, tip = feed_rows(evs), tip_rows(evs)
    for prog, keys in (("pumpswap", FEED_ONLY_PUMPSWAP), ("pumpfun", FEED_ONLY_BONDING)):
        fr = [r for r in feed if r["type"] == "trade" and r["prog"] == prog]
        tr_ = [r for r in tip if r["type"] == "trade" and r["prog"] == prog]
        assert fr and tr_ and len(fr) == len(tr_)
        assert all(keys <= set(r) for r in fr) and all(not (keys & set(r)) for r in tr_)
        assert all(r["tx_index"] is not None and r["block_time"] for r in tr_) and all("tx_index" not in r and "block_time" not in r for r in fr)
    assert {k for r in feed for k in r} & {"supply_raw"} and not any("supply_raw" in r for r in tip)


def test_the_synthetic_feed_key_names_are_names_the_real_decoder_writes():
    from observe.trade_decode import records_from_logs
    from tools.test_fast_tip_follower import _line

    rec = records_from_logs([_line("pumpswap_sell_event.b64")], slot=5, signature="sg", t_recv_ms=1, commitment="confirmed", feed="x", pool_mints={}, event_v=True)[0]
    assert FEED_ONLY_PUMPSWAP <= set(rec), sorted(FEED_ONLY_PUMPSWAP - set(rec))
    assert isinstance(rec["lp_fee"], int) and isinstance(rec["pool_quote_amount"], int)


def test_nothing_but_arena_state_reads_a_feed_only_row_key():
    """Static: the quoted names of the 11 feed-only row keys, in the four frozen modules. Only arena_state reads row keys of this set (lp_fee, pool_quote_amount, creator); the
    `creator` literals in hv4_cards are the SNAPSHOT key. The ablation test below shows the same thing by running the state."""
    allowed = {"tools/arena_state.py": {"pool_quote_amount", "lp_fee", "creator"}, "tools/hv4_cards.py": {"creator"}, "tools/hv4_runner.py": set(), "tools/arena_paper.py": set()}
    for f in FROZEN_FILES:
        text = (REPO / f).read_text()
        found = {k for k in FEED_ONLY_PUMPSWAP | FEED_ONLY_BONDING | {"supply_raw"} if f'"{k}"' in text or f"'{k}'" in text}
        assert found == allowed[f], (f, sorted(found))


# ------------------------------------------------------------------------------------------------------------------------------------ the listing
@pytest.mark.parametrize("name,kw,snap_none,snap_diff,feat_none,feat_diff", [
    ("S1 create row in history, pool from a migration", S1, set(), PRICE_SNAP, set(), PRICE_FEAT),
    ("S2 curve seen by bonding trades only", S2, CREATOR_SNAP, PRICE_SNAP, CREATOR_FEAT_NONE, PRICE_FEAT | {"noncreator_sellers_60m"}),
    ("S3 pool created outside a migration", S3, AGE_SNAP_NONE, PRICE_SNAP | {"age_since_first_seen_s", "created_known"}, AGE_FEAT_NONE,
     (PRICE_FEAT - {"close_vs_hi_6h", "hi_6h", "lo_6h", "rebound"}) | {"age_s"}),
])
def test_the_listing_per_scenario_is_exactly_this(tmp_path, name, kw, snap_none, snap_diff, feat_none, feat_diff):
    L = listing(build_market(**kw), tmp_path)
    for part, none, diff in (("snapshot", snap_none, snap_diff), ("features", feat_none, feat_diff)):
        assert of_class(L[part], "none_only_on_tip") == none, (name, part)
        assert of_class(L[part], "differs_on_tip") == diff, (name, part)
        assert of_class(L[part], "none_only_on_feed") == set(), (name, part)
        assert of_class(L[part], "same") | of_class(L[part], "both_none") == set(L[part]) - none - diff
    # every key a reader cites above is in the snapshot listing or is a Pool attribute: nothing the rule reads is missing from the table
    assert {"price", "reserves.quote_sol", "windows.15m.n_buys", "windows.60m.buy_sol", "top_net.buyers", "creator", "created_known", "age_s", "last_ms", "candles.c"} <= set(L["snapshot"])
    assert set(C.FEATURE_FIELDS) == set(L["features"])


def test_no_state_field_is_none_on_the_tip_in_every_scenario(tmp_path):
    """The always-None-on-tip set, at the level of ArenaState snapshot keys and hv4_cards features, is EMPTY: a key that is None on the tip is None only when the history lacks the
    row that sets it (the mint's create row, the migration), and is set by that row otherwise. The row-level always-absent keys are FEED_ONLY_KEYS (pinned above)."""
    per = [listing(build_market(**kw), tmp_path / f"s{i}") for i, kw in enumerate((S1, S2, S3))]
    for part in ("snapshot", "features"):
        sets = [of_class(L[part], "none_only_on_tip") for L in per]
        assert set.intersection(*sets) == set()
    assert set.union(*(of_class(L["snapshot"], "none_only_on_tip") for L in per)) == CREATOR_SNAP | AGE_SNAP_NONE
    assert set.union(*(of_class(L["features"], "none_only_on_tip") for L in per)) == CREATOR_FEAT_NONE | AGE_FEAT_NONE


def test_stripping_the_feed_only_keys_from_the_feed_rows_gives_the_tip_state(tmp_path):
    """tx_index, block_time, the slot-first order and the adapter's row mapping change nothing in the state: the whole S1 / S2 delta is the removal of the feed-only keys. (Not S3: there
    the delta is a ROW the tip lacks, the pool_create of a pool created outside a migration, not a key.)"""
    for i, kw in enumerate((S1, S2)):
        evs = build_market(**kw)
        stripped = [{k: v for k, v in r.items() if k not in FEED_ONLY_PUMPSWAP | FEED_ONLY_BONDING | {"supply_raw"}} if r["type"] == "trade" else r for r in feed_rows(evs)]
        L = compare_rows(stripped, tip_rows(evs), tmp_path / f"s{i}")
        for part in ("snapshot", "features"):
            assert set(L[part].values()) <= {"same", "both_none"}, (kw, part, [k for k, v in L[part].items() if v not in ("same", "both_none")])


@pytest.mark.parametrize("prog,key,effect", [(p, k, "price") for p, ks in (("pumpswap", ("pool_quote_amount", "lp_fee")),) for k in ks]
                         + [("pumpfun", "creator", "creator")]
                         + [("pumpswap", k, "unread") for k in ("protocol_fee", "creator_fee", "mint_source", "supply_raw")]
                         + [("pumpfun", k, "unread") for k in ("real_quote_reserve", "real_base_reserve", "protocol_fee_bps", "protocol_fee", "creator_fee_bps", "creator_fee")])
def test_ablating_one_feed_only_key_at_a_time_shows_which_ones_the_state_reads(tmp_path, prog, key, effect):
    """On the S2 market (so the bonding trade's creator is the only creator route), delete ONE key from the rows of ONE program of the public-feed-shaped rows and compare with the
    full feed rows. 13 (program, key) cases over 11 key names (protocol_fee and creator_fee are on both programs): 2 move the price family, 1 moves the creator family, 10 change
    nothing the state, the cards or the vetoes can see."""
    evs = build_market(**S2)
    full = feed_rows(evs)
    cut = [{k: v for k, v in r.items() if not (r["type"] == "trade" and r["prog"] == prog and k == key)} for r in full]
    assert cut != full
    L = compare_rows(full, cut, tmp_path)
    if effect == "unread":
        assert set(L["snapshot"].values()) <= {"same", "both_none"} and set(L["features"].values()) <= {"same", "both_none"}
    elif effect == "price":
        assert of_class(L["snapshot"], "differs_on_tip") == PRICE_SNAP and of_class(L["features"], "differs_on_tip") == PRICE_FEAT
        assert not of_class(L["snapshot"], "none_only_on_tip") and not of_class(L["features"], "none_only_on_tip")
    else:
        assert of_class(L["snapshot"], "none_only_on_tip") == CREATOR_SNAP and of_class(L["features"], "none_only_on_tip") == CREATOR_FEAT_NONE
        assert of_class(L["features"], "differs_on_tip") == {"noncreator_sellers_60m"} and not of_class(L["snapshot"], "differs_on_tip")


# ------------------------------------------------------------------------------------------------------------------------------------ the decision effect
def _diff_keys(f: dict, t: dict) -> set[str]:
    return {k for k in f if f[k] != t[k]}


def test_s1_the_price_view_changes_no_entry_decision_but_changes_the_card_bytes(tmp_path):
    f, t = both_decisions(build_market(**S1), tmp_path)
    assert f["x_entry"] and t["x_entry"] and f["filters_passed"] and t["filters_passed"] and f["veto_reasons"] == t["veto_reasons"] == () and f["tags"] == t["tags"] == ("PULLBACK",)
    assert _diff_keys(f, t) == {"card_sha"}  # the card's numbers (6 significant digits) move with the price view; an X arm does not read the card, an LLM arm does


def test_s2_with_four_sellers_the_creator_gap_changes_the_card_but_not_the_entry(tmp_path):
    f, t = both_decisions(build_market(**S2), tmp_path)
    assert f["x_entry"] and t["x_entry"] and _diff_keys(f, t) == {"sell_route_sellers", "card_sha"} and (f["sell_route_sellers"], t["sell_route_sellers"]) == (3, 4)


def test_s2_NEED_with_three_sellers_one_of_them_the_creator_the_tip_enters_where_the_feed_vetoes(tmp_path):
    """A DECISION CHANGE, not hidden: the feed knows the creator from the bonding trade, so sell_route counts 2 non-creator sellers (< 3) and vetoes; the tip does not know the creator
    (the create row is outside its history), counts 3 and passes. The tip is the MORE PERMISSIVE side."""
    f, t = both_decisions(build_market(**S2_THREE), tmp_path)
    assert (f["sell_route_sellers"], t["sell_route_sellers"]) == (2, 3) and f["veto_reasons"] == ("sell_route",) and t["veto_reasons"] == ()
    assert (f["x_entry"], t["x_entry"]) == (False, True) and f["card"] == ("sell_route",) and t["card"] is True
    assert _diff_keys(f, t) == {"veto_reasons", "sell_route_ok", "sell_route_sellers", "card", "card_sha", "x_entry"}


def test_s2_with_the_create_row_in_history_the_same_three_sellers_decide_alike(tmp_path):
    f, t = both_decisions(build_market(creator_route="create_row", n_sellers=3), tmp_path)
    assert (f["sell_route_sellers"], t["sell_route_sellers"]) == (2, 2) and f["x_entry"] is False and t["x_entry"] is False and f["veto_reasons"] == t["veto_reasons"] == ("sell_route",)


def test_s3_NEED_a_pool_created_outside_a_migration_fails_age_min_on_the_tip(tmp_path):
    """A DECISION CHANGE, not hidden (the Am.23 item 3 / item 8 'Age' text): with no pool_create the tip ages the pool from its first print (a lower bound, 90 min < 2 h), so
    filters() fails age_min; the feed has the CreatePool row and the pool is 3 h 10 min old. The tip is the STRICTER side here."""
    f, t = both_decisions(build_market(**S3), tmp_path)
    assert f["filters_failed"] == () and t["filters_failed"] == ("age_min",) and (f["age_known"], t["age_known"]) == (True, False)
    assert (f["x_entry"], t["x_entry"]) == (True, False) and t["card"] == ("age_min",)
    assert _diff_keys(f, t) == {"filters_passed", "filters_failed", "age_known", "card", "card_sha", "x_entry"}


def test_a_value_difference_flips_a_threshold_only_inside_one_lp_fee(tmp_path):
    """The price view (not a NEED, Am.23 item 8): the tip's post-trade real quote is lower than the feed's by exactly lp_fee per print, and re-anchored at the next print. A check
    flips only when the feed's value is within one lp_fee of its threshold. Built here on purpose: one 1 SOL buy that leaves the feed at 30.001 SOL and the tip at 29.999 SOL."""
    a = SOL
    lp = a * 20 // 10_000
    pre = 30 * SOL - a - lp // 2
    base = T0 - 3 * H
    pc = {"type": "migration", "mint": MINTS[0], "pool": POOLS[0], "quote_mint": WSOL, "slot": 399_000_000, "signature": "pc", "event_index": 1, "tx_index": 0, "block_time": base // 1000, "t_recv_ms": base,
          "source": "tip"}
    w = {"venue": "pumpswap", "mint": MINTS[0], "pool": POOLS[0], "trader": "B1", "side": "buy", "sol_lamports": a * 10_030 // 10_000, "token_raw": 10**15 * a // (pre + V + a), "quote_reserve": pre, "base_reserve": 10**15,
         "virtual_quote_reserves": V, "quote_mint": WSOL, "slot": 399_000_100, "signature": "t1", "event_index": 0, "tx_index": 0, "block_time": base // 1000 + 40, "event_ts": base // 1000 + 40,
         "t_recv_ms": base + 40_000, "source": "tip"}
    evs = [Ev("migration", pc), Ev("trade", w, _pumpswap_extra({**w, "sol_lamports": a * 10_030 // 10_000}))]
    assert evs[1].extra["pool_quote_amount"] == a and evs[1].extra["lp_fee"] == lp
    T = base + 41_000
    sf, st_ = (state_of(rows, tmp_path / n, until=T - 1) for n, rows in (("f", feed_rows(evs)), ("t", tip_rows(evs))))
    assert abs((sf.pool(POOLS[0]).q_real - st_.pool(POOLS[0]).q_real) - lp) <= 1  # lp_fee, give or take one lamport of integer / float rounding in the fallback
    out = []
    for st in (sf, st_):
        snap = st.snapshot(POOLS[0])
        fr = C.filters(snap, as_of_ms=T)
        vr = C.code_vetoes(snap, list(st.pool(POOLS[0]).events), chain_facts(T, snap["last_slot"]), as_of_ms=T, as_of_slot=snap["last_slot"])
        out.append((fr.checks["quote_floor"]["ok"], "quote_below_floor" in vr.reasons))
    assert out == [(True, False), (False, True)]


# ------------------------------------------------------------------------------------------------------------------------------------ the REAL runner, end to end
def test_runner_s1_same_entries_on_both_sources(tmp_path):
    evs = build_market(**S1)
    f, t = run_view(evs, tmp_path / "f", "feed"), run_view(evs, tmp_path / "t", "tip")
    assert [k for k in f if f[k] != t[k]] == ["card_shas"]
    assert [e[0] for e in f["CON-X"]] == ["entry", "entry"] and f["CON-X"] == t["CON-X"] and f["BIG-X"] == t["BIG-X"] == []


def test_runner_s2_NEED_three_sellers_the_feed_book_vetoes_and_the_tip_book_enters(tmp_path):
    evs = build_market(**S2_THREE)
    f, t = run_view(evs, tmp_path / "f", "feed"), run_view(evs, tmp_path / "t", "tip")
    assert f["CON-X"] == [] and [e[0] for e in t["CON-X"]] == ["entry", "entry"]
    assert f["refused"] and all(r[1] == ("sell_route",) for r in f["refused"]) and t["refused"] == []


def test_runner_s3_NEED_the_tip_book_has_no_entry_for_a_pool_created_outside_a_migration(tmp_path):
    evs = build_market(**S3)
    f, t = run_view(evs, tmp_path / "f", "feed"), run_view(evs, tmp_path / "t", "tip")
    assert [e[0] for e in f["CON-X"]] == ["entry", "entry"] and t["CON-X"] == [] and t["scan"][0][0] == 0 and f["scan"][0][0] == 2
