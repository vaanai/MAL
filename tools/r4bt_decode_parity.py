"""r4bt_decode_parity: the adapter-path parity function of the r4-b-T DECODE CHECK (QP-RULING-1102-r4bt Q2 item 4 (c), second decode path). PAPER ONLY. Pure: no I/O, no
network, no key. NEW FILE (r4bt_*): it imports the pinned decoder and mapping modules read-only and edits none of them (Q8: W stays blob-identical to bc65c088 plus additions).

WHAT IT IS. `tools/r4bt_decode_check.check_write(..., adapter_check=...)` takes a callable `adapter_check(sample) -> list[str]` (failure strings, [] for none). `sample` is the
post-redeploy LOG SAMPLE of `a3_redeploy_check.fetch_sample`: a list of transactions, each the list of its log lines. `parity_failures` is that callable.

WHAT IT COMPARES. For the same sample, the two decode paths r4-b-T reads, on the code at W (imported, never copied):
  walker path   `observe.trade_decode.records_from_logs(..., event_v=True)` (the decoder the tip follower runs at the three EXP027_DECODER_REF blobs) plus the pinned
                `tools.pump_history_backfill.lifecycle_from_logs` and the CreatePool decode;
  mapping path  `tools.arena_feed.rows_from_notice` (the Helius window's own tape-row builder, via `tools.r4b_feed_merge.notice_rows`) -> `tools.r4b_feed_adapter.FeedAdapter`
                (the walker-schema rows `tools.r4b_live.Engine` ingests).
PASS only on EXACT structural parity, all of:
  1. per transaction, the walker's trade records (of a venue the tape knows) and the tape `trade` rows are the same events in the same order: equal counts, equal values of
     every key the tape keeps, equal slot / signature / event_index / t_recv_ms, and the tape row carries no key the walker row lacks except the seven bonding fee / real-reserve
     fields of arena_tape_schema.md (pump.fun only), all seven present (their values are the TradeEvent's own fields, which the feed writes over the walker's protocol_fee /
     creator_fee by design, so they are checked for presence, not for equality with the walker); no walker key (not in the tape's drop list) is missing from the tape row;
  2. every tape `trade` row maps to exactly one adapter row (`trade`, or `market` for a PumpSwap row whose pool is not resolved yet), and every column of
     `r4b_layout.TRADE_COLS` is present with its column type (nullable only: tx_index; pool and virtual_quote_reserves on pump.fun; mint on an unresolved PumpSwap row);
     the adapter's block_time is the tape row's event_ts and its other columns equal the tape row's;
  3. every `create`, `complete` and `pool_create` row maps to one adapter row with the columns of `CREATE_COLS` / `MIG_COLS` (create: is_mayhem_mode a bool);
  4. the tape's create / complete / pool_create row counts equal the walker-side counts (lifecycle_from_logs, CreatePool decode);
  5. ZERO adapter rejects (any reason) and zero transactions on which a decode raised;
  6. evidence: at least `min_txs` transactions (default `a3_redeploy_check.SAMPLE_MIN_TXS`) and at least one mapped trade of EACH venue (pumpswap, pump_bonding).
Anything else is a failure string. FAIL CLOSED: an exception inside a decode is a failure (class name only), never a skipped transaction; an empty sample fails.

OUTPUT DISCIPLINE. Failure strings and the report carry counts and column / key / reason NAMES only: no signature, mint, wallet, pool or amount. (The sample is public chain data;
the discipline keeps the report usable in notebooks and PR text.)

This file does not decide the verdict; `r4bt_decode_check.check_write` adds the other terms ((c) walker parity vs the IDL, (d), (f), (g), (h)) and `tools/r4bt_a3.py classify` reads
the result. No book has cleared the promotion gate; nothing here says r4-b-T makes money.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import collections
from typing import Any, Mapping, Sequence

from observe import trade_decode as td
from observe.trade_source import RawNotice
from tools import a3_redeploy_check as ARC
from tools import arena_feed as AF
from tools import r4b_feed_adapter as FA
from tools import r4b_layout as L
from tools.pump_history_backfill import lifecycle_from_logs

SCHEMA = "r4bt_decode_parity_v1"
COMMITMENT = "confirmed"
FEED = "r4bt_decode_parity"
SLOT0 = 1_000_000  # synthetic: the sample carries log lines only, and parity does not depend on a slot, a signature or a receive time
T0_MS = 1_794_000_000_000
COMMON_KEYS = frozenset({"v", "type", "prog", "slot", "t_recv_ms", "signature", "event_index"})
BONDING_EXTRA_KEYS = frozenset({"real_quote_reserve", "real_base_reserve", "protocol_fee_bps", "protocol_fee", "creator", "creator_fee_bps", "creator_fee"})
INT_COLS = frozenset(k for k, t in L.TRADE_COLS.items() if t in ("BIGINT", "INTEGER"))
NULLABLE_ALWAYS = frozenset({"tx_index"})


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_str(x: Any) -> bool:
    return isinstance(x, str) and x != ""


def _bump(c: collections.Counter, key: str, n: int = 1) -> None:
    c[key] += n


def _trade_type_failures(m: Mapping[str, Any], venue: str, kind: str, c: collections.Counter) -> None:
    for col, typ in L.TRADE_COLS.items():
        if col not in m:
            _bump(c, f"trade.{col}:missing")
            continue
        v = m[col]
        nullable = col in NULLABLE_ALWAYS or (venue == "pump_bonding" and col in ("pool", "virtual_quote_reserves")) or (kind == "market" and col == "mint")
        if v is None:
            if not nullable:
                _bump(c, f"trade.{col}:null")
        elif col in INT_COLS:
            if not _is_int(v):
                _bump(c, f"trade.{col}:not_int")
        elif not _is_str(v):
            _bump(c, f"trade.{col}:not_str")


def _row_type_failures(m: Mapping[str, Any], cols: Mapping[str, str], label: str, c: collections.Counter, nullable: Sequence[str] = ()) -> None:
    for col, typ in cols.items():
        if col not in m:
            _bump(c, f"{label}.{col}:missing")
            continue
        v = m[col]
        if v is None:
            if col not in nullable:
                _bump(c, f"{label}.{col}:null")
        elif typ == "BOOLEAN":
            if not isinstance(v, bool):
                _bump(c, f"{label}.{col}:not_bool")
        elif typ in ("BIGINT", "INTEGER"):
            if not _is_int(v):
                _bump(c, f"{label}.{col}:not_int")
        elif not _is_str(v):
            _bump(c, f"{label}.{col}:not_str")


class _State:
    def __init__(self) -> None:
        self.wcache: dict[str, tuple[str, str]] = {}
        self.tcache: dict[str, tuple[str, str]] = {}
        self.adapter = FA.FeedAdapter()
        self.counters: collections.Counter = collections.Counter()
        self.n = collections.Counter()  # observed counts
        self.bad = collections.Counter()  # failure name -> count


def _check_trades(walker: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], st: _State) -> list[Mapping[str, Any]]:
    """Walker trade records vs tape trade rows of one transaction; returns the tape trade rows that were compared."""
    exp = [r for r in walker if AF.VENUE_PROG.get(str(r.get("venue")))]
    st.bad["trade.venue_not_in_tape"] += len(walker) - len(exp)
    tr = [r for r in rows if r.get("type") == "trade"]
    if len(tr) != len(exp):
        st.bad["trade.count_walker_vs_tape"] += abs(len(tr) - len(exp))
        return []
    for w, t in zip(exp, tr):
        prog = AF.VENUE_PROG[str(w["venue"])]
        if t.get("prog") != prog:
            st.bad["trade.prog"] += 1
        body = {k for k in w if k not in AF.TRADE_DROP}
        keys = set(t)
        for k in sorted(keys - body - COMMON_KEYS - (BONDING_EXTRA_KEYS if prog == "pumpfun" else frozenset())):
            st.bad[f"trade.key_extra:{k}"] += 1
        if prog == "pumpfun":
            for k in sorted(BONDING_EXTRA_KEYS - keys):
                st.bad[f"trade.key_missing:{k}"] += 1
        for k in sorted(body - keys):
            st.bad[f"trade.key_missing:{k}"] += 1
        skip = BONDING_EXTRA_KEYS if prog == "pumpfun" else frozenset()  # the feed overwrites protocol_fee / creator_fee with the TradeEvent's own fields by design
        for k in sorted(((body & keys) | {"slot", "signature", "event_index", "t_recv_ms"}) - skip):
            if t.get(k) != w.get(k):
                st.bad[f"trade.value:{k}"] += 1
    return tr


def _feed_row(row: Mapping[str, Any], st: _State) -> list[FA.Mapped]:
    outs = st.adapter.feed(row)
    for o in outs:
        if o.kind == "reject":
            st.bad[f"adapter.reject:{o.row.reason}"] += o.row.n
    return outs


def _check_tx(i: int, logs: Sequence[str], st: _State) -> None:
    lines = tuple(x for x in logs if isinstance(x, str))
    sig, slot, t = f"parity-tx-{i:06d}", SLOT0 + i, T0_MS + i
    walker = td.records_from_logs(lines, slot=slot, signature=sig, t_recv_ms=t, commitment=COMMITMENT, feed=FEED, pool_mints=st.wcache, event_v=True)
    rows = AF.rows_from_notice(RawNotice(slot, sig, False, lines, t, COMMITMENT, FEED), st.tcache, st.counters)
    creates, lifecycle = lifecycle_from_logs(lines)
    n_cp = sum(1 for ln in lines if (raw := td._program_data_bytes(ln)) is not None and (ev := td.decode_program_data(raw)) is not None and ev["kind"] == "create_pool")
    want = {"create": len(creates), "complete": sum(1 for e in lifecycle if e.get("type") == "complete"), "pool_create": n_cp}
    for typ, n in want.items():
        got = sum(1 for r in rows if r.get("type") == typ)
        if got != n:
            st.bad[f"{typ}.count_walker_vs_tape"] += abs(got - n)
        st.n[f"tape_{typ}"] += got
    compared = _check_trades(walker, rows, st)
    ids = {id(r) for r in compared}
    for r in walker:
        if AF.VENUE_PROG.get(str(r.get("venue"))):
            st.n[f"walker_trade_{r['venue']}"] += 1
    for row in rows:
        typ = row.get("type")
        outs = _feed_row(row, st)
        if typ == "trade":
            st.n["tape_trade"] += 1
            sel = [o for o in outs if o.kind in ("trade", "market")]
            if len(sel) != 1:
                st.bad["trade.adapter_rows_per_tape_row"] += 1
                continue
            m, kind, venue = sel[0].row, sel[0].kind, str(row.get("venue"))
            st.n[f"adapter_{kind}_{venue}"] += 1
            _trade_type_failures(m, venue, kind, st.bad)
            if id(row) in ids:
                exp = {"venue": venue, "trader": row.get("trader"), "side": row.get("side"), "sol_lamports": row.get("sol_lamports"), "token_raw": row.get("token_raw"),
                       "quote_reserve": row.get("quote_reserve"), "base_reserve": row.get("base_reserve"), "slot": row.get("slot"), "signature": row.get("signature"),
                       "event_index": row.get("event_index"), "tx_index": None, "block_time": row.get("event_ts"),
                       "pool": row.get("pool") if venue == "pumpswap" else None, "virtual_quote_reserves": row.get("virtual_quote_reserves") if venue == "pumpswap" else None}
                for k, v in exp.items():
                    if m.get(k) != v:
                        st.bad[f"adapter.value:{k}"] += 1
                if row.get("mint") is not None and m.get("mint") != row.get("mint"):
                    st.bad["adapter.value:mint"] += 1
                if kind == "market" and m.get("mint") is not None:
                    st.bad["adapter.value:mint"] += 1
        elif typ == "create":
            sel = [o for o in outs if o.kind == "create"]
            if len(sel) != 1:
                st.bad["create.adapter_rows_per_tape_row"] += 1
                continue
            _row_type_failures(sel[0].row, L.CREATE_COLS, "create", st.bad, nullable=())
        elif typ == "complete":
            sel = [o for o in outs if o.kind == "migration" and o.row.get("type") == "complete"]
            if len(sel) != 1:
                st.bad["complete.adapter_rows_per_tape_row"] += 1
                continue
            _row_type_failures(sel[0].row, {k: L.MIG_COLS[k] for k in ("type", "mint", "slot", "block_time")}, "complete", st.bad)
        elif typ == "pool_create":
            sel = [o for o in outs if o.kind == "migration" and o.row.get("type") == "migration"]
            if len(sel) != 1:
                st.bad["pool_create.adapter_rows_per_tape_row"] += 1
                continue
            _row_type_failures(sel[0].row, {k: L.MIG_COLS[k] for k in ("type", "mint", "slot", "pool", "quote_mint")}, "pool_create", st.bad)
        else:
            st.bad[f"tape.unexpected_row_type:{typ}"] += 1


def parity_report(sample: Sequence[Sequence[str]], *, min_txs: int | None = None) -> dict[str, Any]:
    """Counts and failure names for the sample. report["failures"] is [] only on exact structural parity."""
    need = ARC.SAMPLE_MIN_TXS if min_txs is None else int(min_txs)
    st = _State()
    errors: collections.Counter = collections.Counter()
    for i, logs in enumerate(sample):
        try:
            _check_tx(i, logs, st)
        except Exception as exc:  # noqa: BLE001 - fail closed: a raising decode is a failure, never a skipped transaction
            errors[type(exc).__name__] += 1
    fails: list[str] = []
    if not sample:
        fails.append("no evidence: the sample is empty")
    elif len(sample) < need:
        fails.append(f"no evidence: {len(sample)} transactions, need {need}")
    for venue in ("pumpswap", "pump_bonding"):
        if st.n[f"adapter_trade_{venue}"] + st.n[f"adapter_market_{venue}"] == 0:
            fails.append(f"no evidence: no {venue} trade reached the adapter")
    for name, n in sorted(errors.items()):
        fails.append(f"decode raised {name} on {n} transactions")
    for name, n in sorted(st.bad.items()):
        if n:
            fails.append(f"{name} x{n}")
    return {"schema": SCHEMA, "n_txs": len(sample), "min_txs": need, "counts": dict(sorted(st.n.items())), "adapter_stats": dict(sorted(st.adapter.stats.items())),
            "feed_counters": dict(sorted(st.counters.items())), "tx_errors": dict(sorted(errors.items())), "failures": fails, "verdict": "FAIL" if fails else "PASS"}


def parity_failures(sample: Sequence[Sequence[str]], *, min_txs: int | None = None) -> list[str]:
    """The `adapter_check` callable of `r4bt_decode_check.check_write`: [] only on exact structural parity (see the module docstring)."""
    return parity_report(sample, min_txs=min_txs)["failures"]
