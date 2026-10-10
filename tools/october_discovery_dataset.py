#!/usr/bin/env python3
"""October-regime discovery dataset: one structure row per graduation. No outcomes.

Built so it can run on October walk hours (forward-1002ev layout, walk-2 layout) the hour the ledger frees them, and
tested now only on fixtures and on pre-October exploration tape. It reads walker output as written by
`tools/pump_history_backfill.py` (`trades/`, `creates/`, `migrations/` and, on `--event-v` walks, `events/`, one
`<stream>-<YYYY-MM-DDTHH>.jsonl[.zst]` file per hour).

What it writes, per graduation (`complete` row) in the read range:
  * lifecycle: create, complete, migrate (CompletePumpAmmMigrationEvent) slots and times, and the gaps between them;
  * the synthetic-migration class from the tape (PostCompleteBuyEvent rows in `events/`), with the PostCompleteBuy
    size, fees, pool reserves and delay. The tape can only mark a pool synthetic; it never settles non-synthetic
    (EXP-024 Amendment 4 B2), so the classes are `synthetic`, `pcb_other_tx`, `not_seen` and `no_event_stream`;
  * the canonical PumpSwap pool, its first print s0, s0 minus migrate and minus complete (slots and seconds), V0 and
    its source, the seed reserves, the seed market cap including V and the fee tier at the first print;
  * BOOST: slices by the boost-vault authority PDA (the tape trader of a BOOST slice), by `boost_buy_and_burn` events
    when the walk has them, and by the H5 RULE's wallet heuristic (on a time window, not a slot window), with counts,
    SOL and first/last slice times after s0, after migrate and after complete;
  * first-minute flows on the canonical pool (BOOST split out): SOL by side, counts, distinct wallets, largest print;
  * microstructure counts in the first 360 s: prints per active slot, same-slot buy counts, seconds per slot;
  * multi-hop: prints of the canonical pool inside transactions that trade two or more mints, and rotation-shaped
    ones (the same trader sells one mint and buys another in one transaction), plus `ix_name` counts;
  * a reserve-chain check on the canonical pool (base reserve of print i+1 equals print i's base minus/plus its token
    amount) and, per hour, event_index disorder and gap counts. These are the MULTIHOP prerequisite checks.

Per hour it also writes counts: rows by venue, multi-mint transactions, rotation transactions, event_index disorder
and gaps, event-V coverage, non-WSOL quote rows, extra-event types and the slot span.

What it never writes: any price after s0, return, P&L, fill, exit, mark or label. `assert_structure_only` refuses to
write a row with an outcome-like key. The synthetic class is a structure field; it is never joined to an outcome here,
and a caller that joins it to one breaks EXP-024 Amendment 4 D1 for counted-window pools.

Read guard, before any data file is opened:
  1. `tools.mal_catalog.check_read(role="exploration")` over every hour read, on `--ledger-host`. Today the ledger
     denies every October hour (the forward walk rows say "never exploration"), so this tool cannot read October
     data until the manager edits docs/HOLDOUT_LEDGER.md.
  2. Roots under a reserved confirmation block (fresh-0802, fresh-0808, fresh-0828), the tip-tape archive or the
     fast-0 live tape are refused whatever the ledger says.
  3. A forward-1002 / forward-1002ev root also needs the EXP-012 FINAL marker (`tools.forward_v_join.final_marker`).
  4. Every opened file must match its `VIEW.sha256` line (clean views) or the hour must be sealed and verified in
     the walk's checkpoint.json / verify.jsonl (`tools.forward_v_join.hour_state`). `--allow-unverified` exists for
     fixtures only.

Usage (exploration example; run from inside /data/mal/hunt-1008 so the guards apply):
  python3 -m tools.october_discovery_dataset --root /data/mal/clean-view/explore-0814/w4 \
      --start 2026-08-20T12 --end 2026-08-21T12 --ledger-host research \
      --vmap /data/mal/pumpswap-virtual/pool_v_0909.json --out /data/mal/hunt-1008/hunt-r3/october/smoke
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import resource
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

SCHEMA = "october_discovery.v1"
WSOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000
PUMP_SUPPLY_RAW = 1_000_000_000 * 10**6

V_BAND = (17_500_000_000, 17_700_000_000)  # the H5 / CAP-PICK universe band, lamports
BOOST_BUDGET_LAMPORTS = 17_585_000_000  # RULE H5-BOOSTFLOOR v1
HEUR_MIN_BUYS = 3  # RULE H5 BOOST wallet heuristic: >= 3 buys, each 0.2..2.0 SOL, total <= 17.7 SOL, buy-only
HEUR_MIN_SLICE = 200_000_000
HEUR_MAX_SLICE = 2_000_000_000
HEUR_MAX_TOTAL = 17_700_000_000
HEUR_WINDOW_S = 450  # the RULE used 1,600 slots (about 427 s at 267 ms); a time window survives the 200 ms switch
DEFAULT_WINDOW_S = 900
S0_LAG_MAX_S = 300  # rows are kept to complete + S0_LAG_MAX_S + window; a pool whose first print lags more is cut short
FINALIZE_MARGIN_S = 120  # a graduation is written once the hours read so far end this long after its last kept second
FIRST_MIN_S = 60
MICRO_S = 360
MAX_WORKERS = 2  # resource rule: at most 2 worker processes per agent
STREAMS = ("trades", "creates", "migrations", "events")

# Path parts that are never read by this tool, whatever the ledger says (sealed confirmation blocks, live tapes).
NEVER_READ_PARTS = ("fresh-0802", "fresh-0808", "fresh-0828", "tip-tape-archive")
NEVER_READ_PREFIXES = ("/var/lib/mal/sealed",)
FINAL_GATED_PARTS = ("forward-1002",)  # forward-1002 and forward-1002ev: also need the EXP-012 FINAL marker

# Output keys are split on "_"; a key carrying any of these tokens is an outcome-like field and is refused.
FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "pnl", "profit", "ret", "rets", "return", "returns", "exit", "exits", "fill", "fills", "filled", "price",
        "prices", "mark", "marks", "outcome", "outcomes", "label", "labels", "drawdown", "high", "low", "close",
        "win", "wins", "loss", "losses", "gross", "net", "mout", "tp", "sl", "payoff",
    }
)


class Refused(Exception):
    """A read the guard does not allow. The CLI exits 3."""


# ---------------------------------------------------------------------------------------------------------------
# hours, files, integrity


def _parse_hour(h: str) -> datetime:
    return datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def hour_list(start: str, end: str) -> list[str]:
    a, b = _parse_hour(start), _parse_hour(end)
    if b <= a:
        raise ValueError(f"--end {end} must be after --start {start}")
    out = []
    while a < b:
        out.append(a.strftime("%Y-%m-%dT%H"))
        a += timedelta(hours=1)
    return out


def hour_start_s(h: str) -> int:
    return int(_parse_hour(h).timestamp())


def stream_file(root: Path, stream: str, hour: str) -> Path | None:
    for ext in (".jsonl.zst", ".jsonl"):
        p = root / stream / f"{stream}-{hour}{ext}"
        if p.is_file():
            return p
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def load_view_hashes(root: Path) -> dict[str, str] | None:
    """VIEW.sha256 (`<sha>  ./<stream>/<file>` lines) of a clean view, or None when the root has none."""
    p = root / "VIEW.sha256"
    if not p.is_file():
        return None
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        rel = parts[1][2:] if parts[1].startswith("./") else parts[1]
        out[rel] = parts[0]
    return out


def check_root_path(root: Path) -> None:
    text = str(root.resolve())
    parts = set(Path(text).parts)
    for bad in NEVER_READ_PARTS:
        if bad in parts or any(p.startswith(bad) for p in parts):
            raise Refused(f"root {root} is under {bad}: never read by this tool")
    for pre in NEVER_READ_PREFIXES:
        if text.startswith(pre):
            raise Refused(f"root {root} is under {pre}: never read by this tool")


def needs_final_marker(root: Path) -> bool:
    return any(any(p.startswith(g) for g in FINAL_GATED_PARTS) for p in Path(str(root.resolve())).parts)


def check_final_marker(final_ledger: Path) -> dict[str, Any]:
    from tools.forward_v_join import Refused as FjRefused, final_marker

    try:
        return final_marker(final_ledger)
    except FjRefused as exc:
        raise Refused(str(exc)) from None


def ledger_check(ledger: Path, host: str, start: str, end: str) -> list[str]:
    from tools.mal_catalog import check_read, parse_ledger

    blocks = parse_ledger(ledger.read_text(encoding="utf-8"))
    ok, reasons = check_read(blocks, "exploration", host, start, end)
    if not ok:
        raise Refused("ledger denies role=exploration: " + "; ".join(reasons))
    return []


@dataclass
class HourSource:
    hour: str
    root: Path | None
    files: dict[str, Path] = field(default_factory=dict)
    integrity: str = "missing"
    sha256: dict[str, str] = field(default_factory=dict)


def locate_hours(roots: Sequence[Path], hours: Sequence[str]) -> list[HourSource]:
    out = []
    for h in hours:
        found = [(r, stream_file(r, "trades", h)) for r in roots]
        found = [(r, p) for r, p in found if p is not None]
        if len(found) > 1:
            raise Refused(f"hour {h} has a trades file under two roots ({found[0][0]}, {found[1][0]}); pass one")
        if not found:
            out.append(HourSource(hour=h, root=None))
            continue
        root = found[0][0]
        files = {s: p for s in STREAMS if (p := stream_file(root, s, h)) is not None}
        out.append(HourSource(hour=h, root=root, files=files))
    return out


def verify_hour(src: HourSource, view_cache: dict[Path, dict[str, str] | None], *, allow_unverified: bool) -> None:
    """Sets src.integrity and src.sha256, or raises Refused. Reads file bytes for hashing only, no row."""
    assert src.root is not None
    if src.root not in view_cache:
        view_cache[src.root] = load_view_hashes(src.root)
    view = view_cache[src.root]
    if view is not None:
        for stream, path in src.files.items():
            rel = f"{stream}/{path.name}"
            want = view.get(rel)
            got = sha256_file(path)
            src.sha256[stream] = got
            if want is None:
                raise Refused(f"{rel} is not listed in {src.root}/VIEW.sha256")
            if want != got:
                raise Refused(f"{rel} does not match {src.root}/VIEW.sha256")
        src.integrity = "view_ok"
        return
    if (src.root / "checkpoint.json").is_file() and (src.root / "verify.jsonl").is_file():
        from tools.forward_v_join import hour_state

        _path, state = hour_state(src.root, src.hour, strict=True)
        if state != "ok":
            raise Refused(f"hour {src.hour} under {src.root} is not usable: {state}")
        pinned = _last_verify_sha(src.root, src.hour)
        for stream, path in src.files.items():
            got = sha256_file(path)
            src.sha256[stream] = got
            want = pinned.get(stream)
            if want is not None and stream != "trades" and want != got:
                raise Refused(f"{stream}/{path.name} does not match its verify.jsonl sha256")
        src.integrity = "walk_verified"
        return
    if not allow_unverified:
        raise Refused(f"{src.root} has neither VIEW.sha256 nor checkpoint.json + verify.jsonl; refusing (fixtures: --allow-unverified)")
    for stream, path in src.files.items():
        src.sha256[stream] = sha256_file(path)
    src.integrity = "unverified"


def _last_verify_sha(root: Path, hour: str) -> dict[str, str]:
    """The sha256 map of the hour's last verify.jsonl line (trades is checked by hour_state; other streams here)."""
    last: dict[str, Any] = {}
    for line in (root / "verify.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("hour") == hour:
            last = rec
    sha = last.get("sha256")
    return {k: v for k, v in sha.items() if isinstance(v, str)} if isinstance(sha, dict) else {}


def iter_rows(path: Path) -> Iterator[dict[str, Any]]:
    """JSON rows of one hour file. A zstd stream that does not end clean raises."""
    if path.name.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                if raw.strip():
                    yield json.loads(raw)
        finally:
            proc.stdout.close()
            rc = proc.wait()
        if rc != 0:
            raise ValueError(f"zstd failed on {path} (exit {rc})")
        return
    with path.open("rb") as fh:
        for raw in fh:
            if raw.strip():
                yield json.loads(raw)


# ---------------------------------------------------------------------------------------------------------------
# small pure helpers


def load_vmap(paths: Sequence[Path]) -> dict[str, int]:
    """pool -> V lamports from one or more maps, first map wins. Accepts {"v": {...}} or a flat {pool: v}.
    Values outside [0, 30 SOL] are dropped (the maps hold garbage for some non-pump pools)."""
    out: dict[str, int] = {}
    for p in paths:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        m = doc.get("v") if isinstance(doc, dict) and isinstance(doc.get("v"), dict) else doc
        for pool, v in (m or {}).items():
            if pool in out or not isinstance(v, (int, float)):
                continue
            if 0 <= v <= 30 * LAMPORTS:
                out[pool] = int(v)
    return out


def seed_mcap_sol(quote_lamports: int | None, v_lamports: int | None, base_raw: int | None) -> float | None:
    if quote_lamports is None or v_lamports is None or not base_raw or base_raw <= 0:
        return None
    q = int(quote_lamports) + int(v_lamports)
    if q <= 0:
        return None
    return (q * PUMP_SUPPLY_RAW / int(base_raw)) / LAMPORTS


def fee_tier(mcap_sol: float | None) -> tuple[int | None, int | None, float | None]:
    """(total fee ppm, creator fee ppm, tier lower bound in SOL) of the canonical PumpSwap WSOL tiers."""
    if mcap_sol is None:
        return None, None, None
    from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS, pumpswap_sol_fee_ppm, pumpswap_sol_fee_split

    floor = None
    for lo, _p in PUMPSWAP_SOL_FEE_TIERS:
        if mcap_sol + 1e-9 >= lo:
            floor = float(lo)
    return pumpswap_sol_fee_ppm(mcap_sol), pumpswap_sol_fee_split(mcap_sol)[0], floor


def boost_authority(pool: str | None) -> str | None:
    if not pool:
        return None
    try:
        from tools.pump_structure_monitor import boost_vault_authority

        return boost_vault_authority(pool)
    except Exception:  # not a valid 32-byte key (fixtures, garbage)
        return None


def _q(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def order_key(r: Mapping[str, Any]) -> tuple[int, int, int, int]:
    tx = r.get("tx_index")
    return (int(r["slot"]), int(tx) if tx is not None else 1 << 30, int(r.get("event_index") or 0), int(r.get("_ord") or 0))


def assert_structure_only(row: Mapping[str, Any]) -> None:
    for key in row:
        bad = FORBIDDEN_KEY_TOKENS.intersection(key.lower().split("_"))
        if bad:
            raise ValueError(f"output key {key!r} carries outcome-like token(s) {sorted(bad)}; this dataset is structure only")


# ---------------------------------------------------------------------------------------------------------------
# pass 1: lifecycle and extra events (small streams)


@dataclass
class Lifecycle:
    creates: dict[str, dict[str, Any]] = field(default_factory=dict)
    completes: dict[str, dict[str, Any]] = field(default_factory=dict)
    migrations: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    pcb: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    boost_events: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    init_boost: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    hours_with_events: set[str] = field(default_factory=set)
    event_types: dict[str, Counter] = field(default_factory=dict)
    duplicate_completes: int = 0


def read_lifecycle(sources: Sequence[HourSource]) -> Lifecycle:
    lc = Lifecycle()
    for src in sources:
        if src.root is None:
            continue
        if "creates" in src.files:
            for r in iter_rows(src.files["creates"]):
                m = r.get("mint")
                if m and m not in lc.creates:
                    lc.creates[m] = {k: r.get(k) for k in ("slot", "block_time", "creator", "is_mayhem_mode")}
        if "migrations" in src.files:
            for r in iter_rows(src.files["migrations"]):
                m = r.get("mint")
                if not m:
                    continue
                if r.get("type") == "complete":
                    if m in lc.completes:
                        lc.duplicate_completes += 1
                        continue
                    lc.completes[m] = r
                elif r.get("type") == "migration":
                    lc.migrations[m].append(r)
        types: Counter = Counter()
        if "events" in src.files:
            lc.hours_with_events.add(src.hour)
            for r in iter_rows(src.files["events"]):
                t = r.get("type")
                types[str(t)] += 1
                if t == "post_complete_buy" and r.get("mint"):
                    lc.pcb[r["mint"]].append(r)
                elif t == "boost_buy_and_burn" and r.get("pool"):
                    lc.boost_events[r["pool"]].append(r)
                elif t == "init_boost":
                    lc.init_boost[str(r.get("signature"))].append(r)
        lc.event_types[src.hour] = types
    return lc


# ---------------------------------------------------------------------------------------------------------------
# pass 2: trades, one hour at a time (parallel over hours, at most 2 workers)

_KEEP = (
    "slot", "tx_index", "event_index", "block_time", "side", "trader", "sol_lamports", "token_raw", "quote_reserve",
    "base_reserve", "virtual_quote_reserves", "ix_name", "mint", "quote_mint", "pool", "signature",
)


def _flush_tx(group: list[dict[str, Any]], st: Counter, ix_counts: Counter) -> None:
    """Multi-hop and event_index facts of one transaction's trade rows (contiguous in the walker's file).

    Rows whose mint is wrapped SOL (PumpSwap pools with WSOL as the base side) are SOL legs, not tokens, so they never
    make a transaction multi-mint or a rotation. event_index: `disorder` = not strictly increasing in file order (a
    decoder or writer bug); `gap` = increasing but not 0..k-1 (rows the walker could not resolve and left out)."""
    idx = [int(r.get("event_index") or 0) for r in group]
    if any(b <= a for a, b in zip(idx, idx[1:])):
        st["tx_event_index_disorder"] += 1
    elif idx != list(range(len(group))):
        st["tx_event_index_gap"] += 1
    tokens = [r for r in group if r.get("mint") not in (None, WSOL_MINT)]
    mints = {r["mint"] for r in tokens}
    pools = {r.get("pool") for r in tokens if r.get("pool")}
    rot = False
    if len(mints) >= 2:
        st["tx_multi_mint"] += 1
        for r in tokens:
            r["_multi"] = True
            tr, side, mint = r.get("trader"), r.get("side"), r.get("mint")
            want = "sell" if side == "buy" else "buy"
            if any(o is not r and o.get("trader") == tr and o.get("side") == want and o.get("mint") != mint for o in tokens):
                r["_rot"] = "in" if side == "buy" else "out"
                rot = True
    if rot:
        st["tx_rotation"] += 1
    if len(tokens) >= 2 and len(mints) == 1 and len(pools) >= 2:
        st["tx_multi_pool_same_mint"] += 1
    if len(group) >= 2:
        st["tx_multi_row"] += 1
    if len(tokens) < len(group):
        st["tx_with_wsol_base_leg"] += 1
    for r in group:
        name = r.get("ix_name")
        if name:
            ix_counts[str(name)] += 1


def scan_trades_hour(args: tuple[str, str, dict[str, int], int]) -> tuple[str, dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """(hour, rows kept per pool, hour stats). Keeps PumpSwap rows of graduated mints up to complete + window."""
    hour, path, grad_until, _window = args
    keep: dict[str, list[dict[str, Any]]] = defaultdict(list)
    st: Counter = Counter()
    ix_counts: Counter = Counter()
    group: list[dict[str, Any]] = []
    cur_sig: Any = None
    slot_min = slot_max = None
    ordinal = 0
    for r in iter_rows(Path(path)):
        ordinal += 1
        st["rows"] += 1
        venue = r.get("venue")
        st[f"rows_{venue}"] += 1
        if venue == "pumpswap":
            if r.get("virtual_quote_reserves") is not None:
                st["rows_pumpswap_event_v"] += 1
            if r.get("quote_is_wsol") is False:
                st["rows_pumpswap_nonwsol_quote"] += 1
        s = r.get("slot")
        if isinstance(s, int):
            slot_min = s if slot_min is None else min(slot_min, s)
            slot_max = s if slot_max is None else max(slot_max, s)
        sig = r.get("signature")
        if sig != cur_sig:
            if group:
                _flush_tx(group, st, ix_counts)
            group = []
            cur_sig = sig
        slim = {k: r.get(k) for k in _KEEP}
        slim["venue"] = venue
        slim["_ord"] = ordinal
        group.append(slim)
        mint = r.get("mint")
        if venue == "pumpswap" and mint in grad_until and r.get("pool"):
            bt = r.get("block_time")
            if isinstance(bt, int) and bt <= grad_until[mint]:
                keep[r["pool"]].append(slim)  # same dict as in `group`, so _flush_tx's flags land on it
    if group:
        _flush_tx(group, st, ix_counts)
    stats: dict[str, Any] = dict(st)
    stats["hour"] = hour
    stats["slot_min"] = slot_min
    stats["slot_max"] = slot_max
    stats["slot_span"] = (slot_max - slot_min + 1) if slot_min is not None else None
    stats["sec_per_slot_hour"] = round(3600.0 / stats["slot_span"], 6) if stats["slot_span"] else None
    stats["ix_name_top"] = dict(ix_counts.most_common(25))
    out_keep = {pool: [{k: v for k, v in row.items()} for row in rows] for pool, rows in keep.items()}
    return hour, out_keep, stats


# ---------------------------------------------------------------------------------------------------------------
# per-graduation row


def _bt(r: Mapping[str, Any] | None) -> int | None:
    v = r.get("block_time") if r else None
    return int(v) if isinstance(v, (int, float)) else None


def _diff(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a - b


def choose_pool(mint: str, complete: Mapping[str, Any], migs: Sequence[Mapping[str, Any]], mint_pools: Mapping[str, list],
                vmap: Mapping[str, int]) -> tuple[str | None, str | None, dict[str, Any]]:
    """Canonical pool: the migrate event's pool if the tape has it, else the first V-band pool (event V or map V) after
    complete, else the first pool with the graduation's quote mint after complete."""
    c_slot = int(complete["slot"])
    cands = []
    for pool, rows in mint_pools.items():
        rows_m = [r for r in rows if r.get("mint") == mint and int(r["slot"]) >= c_slot]
        if rows_m:
            first = min(rows_m, key=order_key)
            cands.append((order_key(first), pool, first))
    cands.sort()
    info = {
        "n_pumpswap_pools": len(cands),
        "n_wsol_pools": sum(1 for _k, _p, f in cands if f.get("quote_mint") == WSOL_MINT),
        "n_nonwsol_pools": sum(1 for _k, _p, f in cands if f.get("quote_mint") not in (None, WSOL_MINT)),
    }
    mig_pools = [m.get("pool") for m in migs if m.get("pool")]
    if mig_pools:
        return mig_pools[0], "migration", info
    for _k, pool, first in cands:
        v = first.get("virtual_quote_reserves")
        v = int(v) if v is not None else vmap.get(pool)
        if v is not None and V_BAND[0] <= v <= V_BAND[1]:
            return pool, "first_vband_pool", info
    q = complete.get("quote_mint") or WSOL_MINT
    for _k, pool, first in cands:
        if first.get("quote_mint") == q:
            return pool, "first_pool_same_quote", info
    return None, None, info


def boost_heuristic(rows: Sequence[Mapping[str, Any]], s0_bt: int) -> tuple[str | None, list[Mapping[str, Any]]]:
    """RULE H5's BOOST wallet on a time window: buy-only, >= 3 buys of 0.2..2.0 SOL each, total <= 17.7 SOL, most buys wins."""
    per: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    sold: set[str] = set()
    for r in rows:
        bt = _bt(r)
        if bt is None or bt - s0_bt > HEUR_WINDOW_S or bt < s0_bt:
            continue
        tr = r.get("trader")
        if r.get("side") == "sell":
            sold.add(tr)
        else:
            per[tr].append(r)
    best: tuple[int, int, str] | None = None
    for tr, buys in per.items():
        if tr in sold or len(buys) < HEUR_MIN_BUYS:
            continue
        if not all(HEUR_MIN_SLICE <= int(b.get("sol_lamports") or 0) <= HEUR_MAX_SLICE for b in buys):
            continue
        if sum(int(b.get("sol_lamports") or 0) for b in buys) > HEUR_MAX_TOTAL:
            continue
        first = min(order_key(b) for b in buys)
        key = (-len(buys), first[0], tr)
        if best is None or key < best:
            best = key
    if best is None:
        return None, []
    tr = best[2]
    return tr, sorted(per[tr], key=order_key)


CHAIN_KEYS = ("chain_links", "chain_breaks", "chain_links_multi", "chain_breaks_multi", "chain_unknown",
              "chain_breaks_same_tx", "chain_breaks_same_slot", "chain_breaks_cross_slot")


def chain_check(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Base-reserve chain on one pool's prints in (slot, tx_index, event_index) order. PumpSwap rows carry PRE-trade
    reserves: next.base == base - token_raw after a buy, base + token_raw after a sell. Counts only. A break is also
    classed by where it sits: inside one transaction, between two transactions of one slot, or across slots."""
    out = Counter()
    for a, b in zip(rows, rows[1:]):
        if a.get("base_reserve") is None or b.get("base_reserve") is None or a.get("token_raw") is None:
            out["chain_unknown"] += 1
            continue
        want = int(a["base_reserve"]) - int(a["token_raw"]) if a.get("side") == "buy" else int(a["base_reserve"]) + int(a["token_raw"])
        out["chain_links"] += 1
        multi = bool(a.get("_multi") or b.get("_multi"))
        if multi:
            out["chain_links_multi"] += 1
        if int(b["base_reserve"]) != want:
            out["chain_breaks"] += 1
            if multi:
                out["chain_breaks_multi"] += 1
            if a.get("signature") is not None and a.get("signature") == b.get("signature"):
                out["chain_breaks_same_tx"] += 1
            elif a.get("slot") == b.get("slot"):
                out["chain_breaks_same_slot"] += 1
            else:
                out["chain_breaks_cross_slot"] += 1
    return {k: int(out.get(k, 0)) for k in CHAIN_KEYS}


def slot_micro(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_slot: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_slot[int(r["slot"])].append(r)
    per_slot = [len(v) for v in by_slot.values()]
    buys_in_slot = {s: sum(1 for r in v if r.get("side") == "buy") for s, v in by_slot.items()}
    same_slot = [buys_in_slot[int(r["slot"])] for r in rows if r.get("side") == "buy"]
    slots = sorted(by_slot)
    bts = [b for b in (_bt(r) for r in rows) if b is not None]
    sps = None
    if len(slots) >= 2 and bts and slots[-1] > slots[0]:
        span_s = max(bts) - min(bts)
        if span_s > 0:
            sps = round(span_s / (slots[-1] - slots[0]), 6)
    return {
        "ms_n_prints": len(rows),
        "ms_n_slots_active": len(by_slot),
        "ms_slot_span": (slots[-1] - slots[0] + 1) if slots else None,
        "ms_prints_per_active_slot_mean": round(sum(per_slot) / len(per_slot), 6) if per_slot else None,
        "ms_prints_per_active_slot_max": max(per_slot) if per_slot else None,
        "ms_same_slot_buys_mean": round(sum(same_slot) / len(same_slot), 6) if same_slot else None,
        "ms_same_slot_buys_p90": _q(same_slot, 0.9),
        "ms_same_slot_buys_max": max(same_slot) if same_slot else None,
        "ms_sec_per_slot": sps,
    }


def _slices_summary(prefix: str, slices: Sequence[Mapping[str, Any]], amount_key: str, s0_bt: int | None, mig_bt: int | None,
                    c_bt: int | None, s0_slot: int | None) -> dict[str, Any]:
    bts = [b for b in (_bt(r) for r in slices) if b is not None]
    gaps = [b - a for a, b in zip(bts, bts[1:])]
    last = slices[-1] if slices else None
    return {
        f"{prefix}_n": len(slices),
        f"{prefix}_sol": round(sum(int(r.get(amount_key) or 0) for r in slices) / LAMPORTS, 9) if slices else 0.0,
        f"{prefix}_first_s_from_s0": _diff(bts[0], s0_bt) if bts else None,
        f"{prefix}_last_s_from_s0": _diff(bts[-1], s0_bt) if bts else None,
        f"{prefix}_last_s_from_migrate": _diff(bts[-1], mig_bt) if bts else None,
        f"{prefix}_last_s_from_complete": _diff(bts[-1], c_bt) if bts else None,
        f"{prefix}_last_slots_from_s0": _diff(int(last["slot"]), s0_slot) if last is not None and s0_slot is not None else None,
        f"{prefix}_median_gap_s": _median(gaps),
    }


def graduation_row(mint: str, lc: Lifecycle, mint_pools: Mapping[str, list], vmap: Mapping[str, int], *,
                   window_s: int, read_end_s: int, missing_hours: set[str]) -> dict[str, Any]:
    c = lc.completes[mint]
    c_slot, c_bt, c_sig = int(c["slot"]), _bt(c), c.get("signature")
    migs = sorted(lc.migrations.get(mint, []), key=order_key)
    mig = migs[0] if migs else None
    mig_bt = _bt(mig)
    cr = lc.creates.get(mint)
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "mint": mint,
        "quote_mint": c.get("quote_mint"),
        "create_seen": cr is not None,
        "create_slot": cr.get("slot") if cr else None,
        "create_bt": _bt(cr),
        "creator": cr.get("creator") if cr else None,
        "is_mayhem": cr.get("is_mayhem_mode") if cr else None,
        "complete_slot": c_slot,
        "complete_bt": c_bt,
        "complete_tx_index": c.get("tx_index"),
        "complete_sig": c_sig,
        "migrate_seen": mig is not None,
        "migrate_slot": mig.get("slot") if mig else None,
        "migrate_bt": mig_bt,
        "migrate_sig": mig.get("signature") if mig else None,
        "migrate_event_source": mig.get("event_source") if mig else None,
        "migrate_init_boost": mig.get("init_boost") if mig else None,
        "migrate_sol_lamports": mig.get("sol_lamports") if mig else None,
        "migrate_token_raw": mig.get("token_raw") if mig else None,
        "migrate_fee_lamports": mig.get("migration_fee") if mig else None,
        "n_migrate_rows": len(migs),
        "complete_to_migrate_slots": _diff(int(mig["slot"]), c_slot) if mig else None,
        "complete_to_migrate_s": _diff(mig_bt, c_bt),
        "complete_migrate_same_tx": (mig.get("signature") == c_sig) if mig else None,
    }
    # synthetic class (tape can only mark synthetic)
    pcbs = sorted(lc.pcb.get(mint, []), key=order_key)
    sigs = {c_sig} | ({mig.get("signature")} if mig else set())
    in_tx = [p for p in pcbs if p.get("signature") in sigs]
    c_hour = datetime.fromtimestamp(c_bt, tz=timezone.utc).strftime("%Y-%m-%dT%H") if c_bt is not None else None
    if in_tx:
        cls = "synthetic"
    elif pcbs:
        cls = "pcb_other_tx"
    elif c_hour in lc.hours_with_events:
        cls = "not_seen"
    else:
        cls = "no_event_stream"
    p0 = (in_tx or pcbs or [None])[0]
    row.update({
        "synth_class": cls,
        "pcb_n": len(pcbs),
        "pcb_slot": p0.get("slot") if p0 else None,
        "pcb_delay_slots_from_complete": _diff(int(p0["slot"]), c_slot) if p0 else None,
        "pcb_delay_s_from_complete": _diff(_bt(p0), c_bt) if p0 else None,
        "pcb_same_tx_complete": (p0.get("signature") == c_sig) if p0 else None,
        "pcb_same_tx_migrate": (mig is not None and p0.get("signature") == mig.get("signature")) if p0 else None,
        "pcb_quote_in_lamports": p0.get("quote_in") if p0 else None,
        "pcb_base_out_raw": p0.get("base_out") if p0 else None,
        "pcb_fee_lamports": p0.get("fee") if p0 else None,
        "pcb_creator_fee_lamports": p0.get("creator_fee") if p0 else None,
        "pcb_pool_quote_before": p0.get("pool_quote_reserves_before") if p0 else None,
        "pcb_pool_quote_after": p0.get("pool_quote_reserves_after") if p0 else None,
        "pcb_pool_base_before": p0.get("pool_base_reserves_before") if p0 else None,
        "pcb_pool_base_after": p0.get("pool_base_reserves_after") if p0 else None,
        "pcb_event_source": p0.get("event_source") if p0 else None,
    })
    # canonical pool and s0
    pool, pool_src, pinfo = choose_pool(mint, c, migs, mint_pools, vmap)
    row.update({"pool": pool, "pool_src": pool_src, **pinfo})
    prow = sorted([r for r in mint_pools.get(pool, []) if r.get("mint") == mint and int(r["slot"]) >= c_slot], key=order_key) if pool else []
    s0 = prow[0] if prow else None
    s0_bt, s0_slot = _bt(s0), (int(s0["slot"]) if s0 else None)
    v0, v0_src = None, None
    if s0 is not None and s0.get("virtual_quote_reserves") is not None:
        v0, v0_src = int(s0["virtual_quote_reserves"]), "event"
    elif any(r.get("virtual_quote_reserves") is not None for r in prow):
        v0 = int(next(r["virtual_quote_reserves"] for r in prow if r.get("virtual_quote_reserves") is not None))
        v0_src = "event_later"
    elif pool in vmap:
        v0, v0_src = vmap[pool], "map"
    mcap = seed_mcap_sol(s0.get("quote_reserve") if s0 else None, v0, s0.get("base_reserve") if s0 else None)
    ppm, creator_ppm, tier_floor = fee_tier(mcap)
    ib = []
    if mig is not None:
        ib = [e for e in lc.init_boost.get(str(mig.get("signature")), []) if e.get("pool") in (None, pool)]
    row.update({
        "s0_seen": s0 is not None,
        "pool_quote_mint": s0.get("quote_mint") if s0 else None,
        "s0_slot": s0_slot,
        "s0_bt": s0_bt,
        "s0_tx_index": s0.get("tx_index") if s0 else None,
        "s0_event_index": s0.get("event_index") if s0 else None,
        "s0_side": s0.get("side") if s0 else None,
        "s0_ix_name": s0.get("ix_name") if s0 else None,
        "s0_same_tx_migrate": (mig is not None and s0 is not None and s0.get("signature") == mig.get("signature")) if s0 else None,
        "s0_minus_migrate_slots": _diff(s0_slot, int(mig["slot"])) if (mig and s0) else None,
        "s0_minus_migrate_s": _diff(s0_bt, mig_bt),
        "s0_minus_complete_slots": _diff(s0_slot, c_slot),
        "s0_minus_complete_s": _diff(s0_bt, c_bt),
        "v0_lamports": v0,
        "v0_src": v0_src,
        "v_band": (V_BAND[0] <= v0 <= V_BAND[1]) if v0 is not None else None,
        "seed_quote_lamports": s0.get("quote_reserve") if s0 else None,
        "seed_base_raw": s0.get("base_reserve") if s0 else None,
        "seed_mcap_sol": round(mcap, 6) if mcap is not None else None,
        "seed_fee_ppm": ppm,
        "seed_creator_fee_ppm": creator_ppm,
        "seed_tier_floor_sol": tier_floor,
        "seed_above_420": (mcap >= 420.0) if mcap is not None else None,
        "init_boost_event_n": len(ib),
        "init_boost_v_lamports": ib[0].get("virtual_quote_reserves") if ib else None,
    })
    # windows
    win = [r for r in prow if s0_bt is not None and _bt(r) is not None and 0 <= _bt(r) - s0_bt <= window_s]
    span_end = (s0_bt if s0_bt is not None else (c_bt or 0)) + window_s
    win_hours = set()
    if c_bt is not None:
        t = c_bt - c_bt % 3600
        while t <= span_end:
            win_hours.add(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H"))
            t += 3600
    row["span_end_bt"] = span_end
    row["s0_lag_cut"] = bool(s0_bt is not None and c_bt is not None and s0_bt - c_bt > S0_LAG_MAX_S)
    row["censored_read_end"] = span_end >= read_end_s
    row["censored_missing_hour"] = bool(win_hours & missing_hours)
    # BOOST
    auth = boost_authority(pool)
    pda = [r for r in win if auth is not None and r.get("trader") == auth and r.get("side") == "buy"]
    heur_wallet, heur = boost_heuristic(win, s0_bt) if s0_bt is not None else (None, [])
    bev = sorted([e for e in lc.boost_events.get(pool, [])], key=order_key) if pool else []
    row.update(_slices_summary("boost_pda", pda, "sol_lamports", s0_bt, mig_bt, c_bt, s0_slot))
    row.update(_slices_summary("boost_heur", heur, "sol_lamports", s0_bt, mig_bt, c_bt, s0_slot))
    row.update(_slices_summary("boost_ev", bev, "quote_amount_in_used", s0_bt, mig_bt, c_bt, s0_slot))
    row["boost_authority_known"] = auth is not None
    row["boost_heur_is_pda"] = (heur_wallet == auth) if (heur_wallet is not None and auth is not None) else None
    row["boost_ev_vault_remaining_last"] = bev[-1].get("boost_vault_remaining") if bev else None
    if pda:
        src, slices, amt = "pda", pda, "sol_lamports"
    elif bev:
        src, slices, amt = "event", bev, "quote_amount_in_used"
    elif heur:
        src, slices, amt = "heuristic", heur, "sol_lamports"
    else:
        src, slices, amt = None, [], "sol_lamports"
    spent = sum(int(r.get(amt) or 0) for r in slices)
    row["boost_src"] = src
    row["boost_budget_spent_share"] = round(spent / BOOST_BUDGET_LAMPORTS, 6) if slices else 0.0
    row["boost_budget_complete"] = spent >= 0.999 * BOOST_BUDGET_LAMPORTS
    boost_trader = auth if pda else heur_wallet
    # first-minute flows (BOOST split out)
    fm = [r for r in win if _bt(r) - s0_bt < FIRST_MIN_S] if s0_bt is not None else []
    org = [r for r in fm if r.get("trader") != boost_trader]
    bst = [r for r in fm if boost_trader is not None and r.get("trader") == boost_trader]
    buys = [r for r in org if r.get("side") == "buy"]
    sells = [r for r in org if r.get("side") == "sell"]
    sells_bt = [_bt(r) for r in sells if _bt(r) is not None]
    row.update({
        "fm_n_prints": len(fm),
        "fm_buy_sol": round(sum(int(r.get("sol_lamports") or 0) for r in buys) / LAMPORTS, 9),
        "fm_sell_sol": round(sum(int(r.get("sol_lamports") or 0) for r in sells) / LAMPORTS, 9),
        "fm_n_buys": len(buys),
        "fm_n_sells": len(sells),
        "fm_n_buyers": len({r.get("trader") for r in buys}),
        "fm_n_sellers": len({r.get("trader") for r in sells}),
        "fm_max_buy_sol": round(max((int(r.get("sol_lamports") or 0) for r in buys), default=0) / LAMPORTS, 9),
        "fm_max_sell_sol": round(max((int(r.get("sol_lamports") or 0) for r in sells), default=0) / LAMPORTS, 9),
        "fm_first_sell_s_from_s0": _diff(min(sells_bt), s0_bt) if sells_bt else None,
        "fm_boost_n": len(bst),
        "fm_boost_sol": round(sum(int(r.get("sol_lamports") or 0) for r in bst) / LAMPORTS, 9),
    })
    row["fm_flow_sol"] = round(row["fm_buy_sol"] - row["fm_sell_sol"], 9)
    # microstructure and multi-hop, first MICRO_S seconds
    micro = [r for r in win if _bt(r) - s0_bt < MICRO_S] if s0_bt is not None else []
    row.update(slot_micro(micro))
    ix = Counter(str(r.get("ix_name")) for r in micro if r.get("ix_name"))
    row.update({
        "mh_n_prints_multi_tx": sum(1 for r in micro if r.get("_multi")),
        "mh_n_rot_in": sum(1 for r in micro if r.get("_rot") == "in"),
        "mh_rot_in_sol": round(sum(int(r.get("sol_lamports") or 0) for r in micro if r.get("_rot") == "in") / LAMPORTS, 9),
        "mh_n_rot_out": sum(1 for r in micro if r.get("_rot") == "out"),
        "mh_rot_out_sol": round(sum(int(r.get("sol_lamports") or 0) for r in micro if r.get("_rot") == "out") / LAMPORTS, 9),
        "mh_n_ix_multihop": sum(n for k, n in ix.items() if "multi" in k.lower() or "hop" in k.lower()),
        "mh_ix_names": dict(ix.most_common(8)),
    })
    row.update(chain_check(win))
    assert_structure_only(row)
    return row


# ---------------------------------------------------------------------------------------------------------------
# driver


def build(roots: Sequence[Path], start: str, end: str, *, ledger: Path, ledger_host: str, out_dir: Path,
          vmap_paths: Sequence[Path] = (), window_s: int = DEFAULT_WINDOW_S, workers: int = 1,
          allow_unverified: bool = False, allow_missing: bool = False,
          final_ledger: Path = Path("/data/mal/exp012-forward/FINAL_READS.jsonl")) -> dict[str, Any]:
    if workers < 1 or workers > MAX_WORKERS:
        raise Refused(f"--workers must be 1..{MAX_WORKERS}")
    hours = hour_list(start, end)
    for r in roots:
        check_root_path(r)
    ledger_check(ledger, ledger_host, start, end)  # before any data file is opened
    final = None
    if any(needs_final_marker(r) for r in roots):
        final = check_final_marker(final_ledger)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise Refused(f"--out {out_dir} exists and is not empty; this tool never overwrites")
    sources = locate_hours(roots, hours)
    missing = {s.hour for s in sources if s.root is None}
    if missing and not allow_missing:
        raise Refused(f"{len(missing)} hour(s) have no trades file under the roots (first {sorted(missing)[0]}); --allow-missing flags them instead")
    view_cache: dict[Path, dict[str, str] | None] = {}
    for s in sources:
        if s.root is not None:
            verify_hour(s, view_cache, allow_unverified=allow_unverified)
    vmap = load_vmap(vmap_paths) if vmap_paths else {}

    lc = read_lifecycle(sources)
    grad_until = {m: int(_bt(c) or 0) + S0_LAG_MAX_S + window_s for m, c in lc.completes.items()}
    jobs = [(s.hour, str(s.files["trades"]), grad_until, window_s) for s in sources if s.root is not None]
    pools_by_mint: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    hour_stats: list[dict[str, Any]] = []
    read_end_s = hour_start_s(end)
    pending = sorted(lc.completes, key=lambda m: order_key(lc.completes[m]))  # grad_until is monotone in this order
    grads: list[dict[str, Any]] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    part = out_dir / "graduations.jsonl.partial"
    gfh = part.open("x", encoding="utf-8")

    def finalize(upto_s: int | None) -> None:
        """Write and free every graduation whose kept rows are all in (the hours read so far end after upto_s)."""
        nonlocal pending
        n = 0
        while n < len(pending) and (upto_s is None or grad_until[pending[n]] + FINALIZE_MARGIN_S < upto_s):
            mint = pending[n]
            row = graduation_row(mint, lc, pools_by_mint.pop(mint, {}), vmap, window_s=window_s, read_end_s=read_end_s, missing_hours=missing)
            gfh.write(json.dumps(row, separators=(",", ":")) + "\n")
            grads.append(row)
            n += 1
        pending = pending[n:]

    if workers == 1:
        results: Iterable = map(scan_trades_hour, jobs)
    else:
        pool = multiprocessing.get_context("spawn").Pool(workers)
        results = pool.imap(scan_trades_hour, jobs)  # imap keeps hour order
    try:
        for hour, keep, stats in results:
            for p, rws in keep.items():
                for r in rws:
                    pools_by_mint[r["mint"]][p].append(r)
            stats["events_types"] = dict(lc.event_types.get(hour, {}))
            stats["has_event_stream"] = hour in lc.hours_with_events
            hour_stats.append(stats)
            finalize(hour_start_s(hour) + 3600)
        finalize(None)
    finally:
        if workers > 1:
            pool.close()
            pool.join()
        gfh.close()
    part.rename(out_dir / "graduations.jsonl")
    with (out_dir / "hour_stats.jsonl").open("x", encoding="utf-8") as fh:
        for h in hour_stats:
            fh.write(json.dumps(h, separators=(",", ":")) + "\n")
    summary = summarize(grads, hour_stats)
    manifest = {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "structure_only": True,
        "outcome_columns": "none (assert_structure_only on every row)",
        "notes": [
            "fm_* and ms_* and mh_* are measured AFTER s0: they are not decision-time features for a decision at s0.",
            "synth_class 'not_seen' is not 'non-synthetic': the tape never settles non-synthetic (EXP-024 Am.4 B2).",
            "The synthetic class must never be joined to an outcome of a pool with s0 in a counted window before EXP-024 Look 2 is read (EXP-024 Am.4 D1).",
        ],
        "code": {"file": "tools/october_discovery_dataset.py", "sha256": sha256_file(Path(__file__))},
        "args": {"roots": [str(r) for r in roots], "start": start, "end": end, "ledger": str(ledger), "ledger_host": ledger_host,
                 "vmap": [str(p) for p in vmap_paths], "window_s": window_s, "workers": workers,
                 "allow_unverified": allow_unverified, "allow_missing": allow_missing},
        "ledger_check": {"role": "exploration", "host": ledger_host, "start": start, "end": end, "result": "ALLOW"},
        "final_marker": {k: final.get(k) for k in ("utc_time",)} if final else None,
        "hours": [{"hour": s.hour, "root": str(s.root) if s.root else None, "integrity": s.integrity, "sha256": s.sha256} for s in sources],
        "duplicate_completes": lc.duplicate_completes,
        "peak_rss_mb": {"self": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                        "children": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)},
        "summary": summary,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def summarize(grads: Sequence[Mapping[str, Any]], hour_stats: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts and medians of structure fields only."""
    def cnt(key: str, rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        return dict(sorted(Counter("none" if r.get(key) is None else str(r.get(key)) for r in rows).items()))

    def med(key: str, rows: Iterable[Mapping[str, Any]]) -> float | None:
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
        return _median(vals)

    ok = [g for g in grads if g.get("s0_seen") and not g.get("censored_read_end") and not g.get("censored_missing_hour") and not g.get("s0_lag_cut")]
    vb = [g for g in ok if g.get("v_band")]
    vb_boost = [g for g in vb if g.get("boost_src") is not None]
    hs = Counter()
    for h in hour_stats:
        for k, v in h.items():
            if isinstance(v, int) and not isinstance(v, bool) and k.startswith(("rows", "tx_")):
                hs[k] += v
    return {
        "graduations": len(grads),
        "uncensored_with_s0": len(ok),
        "v_band": len(vb),
        "by_synth_class": cnt("synth_class", grads),
        "by_pool_src": cnt("pool_src", grads),
        "by_v0_src": cnt("v0_src", grads),
        "by_boost_src_vband": cnt("boost_src", vb),
        "vband_boost_pda_n_median": med("boost_pda_n", vb_boost),
        "vband_boost_pda_last_s_from_s0_median": med("boost_pda_last_s_from_s0", vb_boost),
        "vband_boost_pda_last_s_from_migrate_median": med("boost_pda_last_s_from_migrate", vb_boost),
        "vband_boost_heur_is_pda_share": (sum(1 for g in vb if g.get("boost_heur_is_pda")) / len(vb)) if vb else None,
        "vband_seed_mcap_sol_median": med("seed_mcap_sol", vb),
        "vband_seed_above_420": sum(1 for g in vb if g.get("seed_above_420")),
        "vband_s0_minus_migrate_s_median": med("s0_minus_migrate_s", vb),
        "vband_s0_minus_complete_s_median": med("s0_minus_complete_s", vb),
        "vband_ms_same_slot_buys_mean_median": med("ms_same_slot_buys_mean", vb),
        "vband_ms_sec_per_slot_median": med("ms_sec_per_slot", vb),
        "vband_pools_with_multi_tx_prints": sum(1 for g in vb if (g.get("mh_n_prints_multi_tx") or 0) > 0),
        "vband_pools_with_rot_in": sum(1 for g in vb if (g.get("mh_n_rot_in") or 0) > 0),
        **{k: sum(int(g.get(k) or 0) for g in ok) for k in CHAIN_KEYS},
        "hour_totals": dict(hs),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", required=True, type=Path, help="walker output root (repeatable; one root per hour)")
    ap.add_argument("--start", required=True, help="first hour read, YYYY-MM-DDTHH (inclusive)")
    ap.add_argument("--end", required=True, help="end hour, YYYY-MM-DDTHH (exclusive)")
    ap.add_argument("--ledger-host", required=True, choices=("research", "fast", "oracle"), help="host column the ledger check uses")
    ap.add_argument("--ledger", type=Path, default=Path(__file__).resolve().parents[1] / "docs" / "HOLDOUT_LEDGER.md")
    ap.add_argument("--out", required=True, type=Path, help="new or empty output directory")
    ap.add_argument("--vmap", action="append", type=Path, default=[], help="pool V map JSON (fallback when rows carry no event V)")
    ap.add_argument("--window-s", type=int, default=DEFAULT_WINDOW_S)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--allow-unverified", action="store_true", help="fixtures only: roots without VIEW.sha256 or verify.jsonl")
    ap.add_argument("--allow-missing", action="store_true", help="flag graduations near missing hours instead of refusing")
    ap.add_argument("--final-ledger", type=Path, default=Path("/data/mal/exp012-forward/FINAL_READS.jsonl"))
    a = ap.parse_args(argv)
    try:
        man = build(a.root, a.start, a.end, ledger=a.ledger, ledger_host=a.ledger_host, out_dir=a.out, vmap_paths=a.vmap,
                    window_s=a.window_s, workers=a.workers, allow_unverified=a.allow_unverified, allow_missing=a.allow_missing,
                    final_ledger=a.final_ledger)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    s = man["summary"]
    print(json.dumps({k: s[k] for k in ("graduations", "uncensored_with_s0", "v_band", "by_synth_class", "by_boost_src_vband")}, sort_keys=True))
    print(f"wrote {a.out}/graduations.jsonl, hour_stats.jsonl, manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
