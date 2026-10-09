#!/usr/bin/env python3
"""Synthetic-migration class of one PumpSwap pool, by the read-time procedure of EXP-024 Amendment 4 (B1 to B4).

One function, `classify_pool`, for the formal read tool (PR #476) and for the DEC-024 Amendment 2 daily audit
(`tools/h5_synthetic_audit.py`). It reads chain structure from a public RPC and nothing else: no price, size, fill, exit or P&L.

The class (B1)
  A pool is `synthetic` iff a PostCompleteBuyEvent discriminator is seen in the transaction that carries the mint's CompleteEvent
  (the curve-completing tx) OR in the pool's migrate tx. It is the monitor's `post_complete_buy_seen` on the monitor's
  `tx_event_blobs` of BOTH transactions, discriminator alone (no mint-match refinement). If either transaction cannot be found or
  read, the pool is `unclassified`. Only a transaction located and read here decides; a tape row only locates (B2, B4 item 3).

Locating the transactions (B4; `getTransaction` json, maxSupportedTransactionVersion 1, finalized)
  Migrate tx (carries the PumpSwap CreatePoolEvent for the pool and pump's CompletePumpAmmMigrationEvent for the mint):
    1. `tape_migrate_sig`, if given;
    2. the s0 print's tx (`s0_sig`), if given;
    3. `getSignaturesForAddress(pool, before = s0_sig or before_sig)`, newest-first, paginated to `cap` signatures. The migrate tx
       is the OLDEST successful tx in that set that carries a CreatePoolEvent for the pool.
  Completing tx (carries the mint's CompleteEvent):
    1. the migrate tx itself, if it carries the mint's CompleteEvent;
    2. `tape_complete_sig`, if given;
    3. `getSignaturesForAddress(curve_pda, before = migrate_sig)`, newest-first, paginated to `cap` signatures. The completing tx
       is the NEWEST successful (err null) tx in that set that carries a CompleteEvent for the mint.
  Defining-event check: a located migrate tx must carry CompletePumpAmmMigrationEvent for the mint, and a located completing tx
  must carry CompleteEvent for the mint. A tx that fails the check (or failed on chain, or is missing) is not used and the next
  source is tried. Inside a signature walk a missing tx or a failed fetch cannot be told from the tx being sought, so it ends
  the pool as unclassified (reason `tx_missing:*` / `fetch_failed:*`).

The helpers (`tx_event_blobs`, `post_complete_buy_seen`, `fetch_tx`, the event decoders, `find_program_address`) are the monitor's
own; none of their semantics is copied here. The CreatePoolEvent decoder is `observe.trade_decode.decode_program_data`.
"""
from __future__ import annotations

import struct
from typing import Any, Mapping, Sequence

from observe.trade_decode import decode_program_data
from tools import pump_structure_monitor as M

CLASS_SYNTHETIC = "synthetic"
CLASS_NON_SYNTHETIC = "non_synthetic"
CLASS_UNCLASSIFIED = "unclassified"
CLASSES = (CLASS_SYNTHETIC, CLASS_NON_SYNTHETIC, CLASS_UNCLASSIFIED)

CAP_DEFAULT = 1000  # EXP-024 Am.4 B4: signatures per search, per address
PAGE_DEFAULT = 1000  # getSignaturesForAddress maximum `limit`
RESULT_KEYS = ("class", "migrate_sig", "complete_sig", "reason")


def curve_pda_for_mint(mint: str) -> str:
    """Bonding-curve PDA: ["bonding-curve", mint] under the pump program. Same derivation as `tools/fast_grad_stream.py`
    (`Pubkey.find_program_address`), through the monitor's pure-Python `find_program_address` (no solders needed)."""
    return M.find_program_address([b"bonding-curve", M.b58decode(mint)], M.PUMP_PROGRAM)[0]


# ---- event tests (all on the monitor's tx_event_blobs) ---------------------------------------------------


def _carries_create_pool(blobs: Sequence[bytes], pool: str) -> bool:
    for b in blobs:
        try:
            row = decode_program_data(b)
        except (ValueError, IndexError, KeyError, struct.error):
            continue
        if row is not None and row.get("kind") == "create_pool" and row.get("pool") == pool:
            return True
    return False


def _carries_migration(blobs: Sequence[bytes], mint: str) -> bool:
    return any(e is not None and e["mint"] == mint for e in (M.decode_migration_event(b) for b in blobs))


def _carries_complete(blobs: Sequence[bytes], mint: str) -> bool:
    return any(e is not None and e["mint"] == mint for e in (M.decode_complete_event(b) for b in blobs))


def _succeeded(tx: Mapping[str, Any]) -> bool:
    return (tx.get("meta") or {}).get("err") is None


class _Unclassified(Exception):
    """Internal: stop the search and report the pool as unclassified, with `reason`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _result(cls: str, migrate_sig: str | None, complete_sig: str | None, reason: str) -> dict[str, Any]:
    return {"class": cls, "migrate_sig": migrate_sig, "complete_sig": complete_sig, "reason": reason}


# ---- RPC reads --------------------------------------------------------------------------------------------


def _fetch(rpc: Any, sig: str, stage: str) -> dict[str, Any] | None:
    """The monitor's `fetch_tx`. None for a tx the node does not have; a failed fetch (retries exhausted, RPC error, call cap) ends the pool."""
    try:
        return M.fetch_tx(rpc, sig)
    except M.ITEM_ERRORS as exc:
        raise _Unclassified(f"fetch_failed:{stage}:{type(exc).__name__}") from exc


def signatures_before(rpc: Any, address: str, before: str | None, cap: int, page_size: int, stage: str) -> list[dict[str, Any]]:
    """Newest-first `getSignaturesForAddress`, paginated by `before`, at most `cap` entries in all (the cap is on signatures, not on calls)."""
    out: list[dict[str, Any]] = []
    cursor = before
    while len(out) < cap:
        limit = min(page_size, cap - len(out))
        opts: dict[str, Any] = {"limit": limit, "commitment": "finalized"}
        if cursor:
            opts["before"] = cursor
        try:
            page = rpc.call("getSignaturesForAddress", [address, opts]) or []
        except M.ITEM_ERRORS as exc:
            raise _Unclassified(f"fetch_failed:{stage}:{type(exc).__name__}") from exc
        page = [s for s in page if isinstance(s, dict) and s.get("signature")][:limit]
        out.extend(page)
        if len(page) < limit:
            break
        cursor = page[-1]["signature"]
    return out[:cap]


# ---- the two searches -------------------------------------------------------------------------------------


def _migrate_ok(tx: Mapping[str, Any] | None, mint: str, pool: str | None) -> bool:
    """Defining-event check for a migrate tx: successful, and CompletePumpAmmMigrationEvent for the mint.
    `pool` is given only for the s0 test and the search, which look for the CreatePoolEvent of the pool as well."""
    if tx is None or not _succeeded(tx):
        return False
    blobs = M.tx_event_blobs(tx)
    if pool is not None and not _carries_create_pool(blobs, pool):
        return False
    return _carries_migration(blobs, mint)


def _locate_migrate(rpc: Any, *, mint: str, pool: str, s0_sig: str | None, before_sig: str | None, tape_migrate_sig: str | None,
                    cap: int, page_size: int) -> tuple[str, dict[str, Any]]:
    if tape_migrate_sig:
        tx = _fetch(rpc, tape_migrate_sig, "migrate")
        if _migrate_ok(tx, mint, None):
            return tape_migrate_sig, tx  # type: ignore[return-value]
    if s0_sig:
        tx = _fetch(rpc, s0_sig, "migrate")
        if _migrate_ok(tx, mint, pool):
            return s0_sig, tx  # type: ignore[return-value]
    sigs = signatures_before(rpc, pool, s0_sig or before_sig, cap, page_size, "signatures:pool")
    for entry in reversed(sigs):  # oldest first: the first hit is the oldest successful tx with a CreatePoolEvent for the pool
        if entry.get("err") is not None:
            continue
        tx = _fetch(rpc, entry["signature"], "migrate")
        if tx is None:
            raise _Unclassified("tx_missing:migrate")
        if _succeeded(tx) and _carries_create_pool(M.tx_event_blobs(tx), pool):
            if _migrate_ok(tx, mint, None):
                return entry["signature"], tx
            break  # the oldest CreatePool tx does not carry the mint's migration event: no source is left
    raise _Unclassified("migrate_tx_not_found")


def _complete_ok(tx: Mapping[str, Any] | None, mint: str) -> bool:
    return tx is not None and _succeeded(tx) and _carries_complete(M.tx_event_blobs(tx), mint)


def _locate_complete(rpc: Any, *, mint: str, curve_pda: str, migrate_sig: str, migrate_tx: Mapping[str, Any], tape_complete_sig: str | None,
                     cap: int, page_size: int) -> tuple[str, Mapping[str, Any]]:
    if _complete_ok(migrate_tx, mint):
        return migrate_sig, migrate_tx
    if tape_complete_sig:
        tx = _fetch(rpc, tape_complete_sig, "complete")
        if _complete_ok(tx, mint):
            return tape_complete_sig, tx  # type: ignore[return-value]
    sigs = signatures_before(rpc, curve_pda, migrate_sig, cap, page_size, "signatures:curve")
    for entry in sigs:  # newest first: the first hit is the newest successful tx with a CompleteEvent for the mint
        if entry.get("err") is not None:
            continue
        tx = _fetch(rpc, entry["signature"], "complete")
        if tx is None:
            raise _Unclassified("tx_missing:complete")
        if _complete_ok(tx, mint):
            return entry["signature"], tx
    raise _Unclassified("complete_tx_not_found")


# ---- public ------------------------------------------------------------------------------------------------


def classify_pool(
    rpc: Any,
    *,
    mint: str,
    pool: str,
    curve_pda: str | None = None,
    s0_sig: str | None = None,
    before_sig: str | None = None,
    tape_migrate_sig: str | None = None,
    tape_complete_sig: str | None = None,
    cap: int = CAP_DEFAULT,
    page_size: int = PAGE_DEFAULT,
) -> dict[str, Any]:
    """EXP-024 Am.4 B1/B4. Returns {"class": synthetic | non_synthetic | unclassified, "migrate_sig", "complete_sig", "reason"}.

    `rpc` is anything with `.call(method, params)` that behaves like the monitor's `RpcClient` (public RPC, paced, retrying, raising
    `RpcError` / `RpcUnreachable` / `CallBudgetExceeded`). `curve_pda` defaults to the PDA derived from `mint`. `s0_sig` is the pool's
    s0 print; `before_sig` stands in for it when only a later signature is known (a superset window: the migrate tx is older than
    both). With neither, the pool search starts from the newest signature, which only works for a young pool inside `cap`.
    The tape signatures only locate; the class always comes from the transactions read here.
    """
    curve = curve_pda or curve_pda_for_mint(mint)
    migrate_sig: str | None = None
    complete_sig: str | None = None
    try:
        migrate_sig, migrate_tx = _locate_migrate(
            rpc, mint=mint, pool=pool, s0_sig=s0_sig, before_sig=before_sig, tape_migrate_sig=tape_migrate_sig, cap=cap, page_size=page_size)
        complete_sig, complete_tx = _locate_complete(
            rpc, mint=mint, curve_pda=curve, migrate_sig=migrate_sig, migrate_tx=migrate_tx, tape_complete_sig=tape_complete_sig,
            cap=cap, page_size=page_size)
    except _Unclassified as exc:
        return _result(CLASS_UNCLASSIFIED, migrate_sig, complete_sig, exc.reason)
    same = complete_sig == migrate_sig  # the migrate tx also carried the CompleteEvent: one transaction to test
    in_migrate = M.post_complete_buy_seen(M.tx_event_blobs(migrate_tx), mint)[0]
    in_complete = in_migrate if same else M.post_complete_buy_seen(M.tx_event_blobs(complete_tx), mint)[0]
    if in_migrate and in_complete:
        return _result(CLASS_SYNTHETIC, migrate_sig, complete_sig, "post_complete_buy_in_migrate_and_complete_tx" if same else "post_complete_buy_in_both_txs")
    if in_complete:
        return _result(CLASS_SYNTHETIC, migrate_sig, complete_sig, "post_complete_buy_in_complete_tx")
    if in_migrate:
        return _result(CLASS_SYNTHETIC, migrate_sig, complete_sig, "post_complete_buy_in_migrate_tx")
    return _result(CLASS_NON_SYNTHETIC, migrate_sig, complete_sig, "no_post_complete_buy")
