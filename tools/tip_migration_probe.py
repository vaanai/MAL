"""Public-RPC, counts-only structure check of recent successful pump migrate transactions.

QP PHASE2 OK (QP-RULING-1107-tip-migration-crossbook section 3; r4bt-p0-fail section 4(c)).
Run only as a manager MiScusi job. 0 Helius credits: a URL containing "helius" is refused.

Prints counts only (see COUNT_KEYS). It never prints or writes a signature, mint, pool, amount,
reserve, price or V. Signatures live in memory only. It reads no tip tape, archive or sealed path
and writes nothing except the optional --out file (refused under probe/shadow/sealed/tape/arena/tip).
Transactions are at or after 2026-11-01T00:00Z, at most 50. Walk-2-hour structure, disclosed as such.
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from observe.trade_decode import ANCHOR_EVENT_IX_TAG, PUMP_BONDING_PROGRAM, PUMPSWAP_PROGRAM, b58decode
from tools.a3_redeploy_check import canonical_pool
from tools.migration_stream_probe import _MIGRATE_IX
from tools.pump_history_backfill import (
    _COMPLETE_DISC,
    _MIGRATE_DISC,
    RateLimiter,
    _getblock_params,
    _program_data,
    cpi_events_from_tx,
    decode_complete_event,
    decode_migration_event,
    init_boost_from_tx,
    lifecycle_from_logs,
    rpc_call,
    rows_from_block,
    tx_account_keys,
)

MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"  # same constant as migration_stream_probe
DEFAULT_URL = "https://api.mainnet-beta.solana.com"
FLOOR_ISO = "2026-11-01T00:00:00Z"
FLOOR_TS = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())
MAX_TX_CAP = 50
MAX_RPS = 4.0
PAGE = 100
MAX_PAGES = 400
FORBIDDEN_OUT_WORDS = ("probe", "shadow", "sealed", "tape", "arena", "tip")
COUNT_KEYS = (
    "n_tx",
    "log_lines_p50", "log_lines_max",
    "log_bytes_p50", "log_bytes_max",
    "n_log_truncated",
    "n_disc_in_logs", "n_disc_inner_only", "n_disc_neither",
    "n_init_boost",
    "legacy_migration_rows", "legacy_txs_with_row",
    "event_v_migration_rows", "event_v_txs_with_row",
    "neither_pump_ix_disc_hist",
)
GRAD_KEYS = (
    "mode", "n_complete_events", "n_blocks_scanned", "n_block_errors", "n_no_creation_tx", "n_creation_history_capped",
    "n_creation_tx", "n_top_pump", "n_top_pumpswap", "n_top_other",
    "pump_ix_class_hist", "n_disc_in_logs", "n_disc_inner_only", "n_disc_neither",
    "other_program_data_disc_hist", "n_signer_is_migration_account", "n_same_tx_as_complete",
    "secs_complete_to_creation_p50", "secs_complete_to_creation_max",
    "legacy_migration_row_yield", "decode_migration_event_reason_hist",
)
MAX_BLOCKS_CAP = 20000
MAX_POOL_PAGES = 10

RpcFn = Callable[[str, list], Any]


class Refusal(ValueError):
    pass


def check_url(url: str) -> None:
    low = url.lower()
    if "helius" in low or "api-key" in low or "api_key" in low:
        raise Refusal("refused: URL must be a public RPC with no key (no helius)")
    if not low.startswith("https://"):
        raise Refusal("refused: https URL required")


def check_out(path: str) -> None:
    low = str(path).lower()
    for w in FORBIDDEN_OUT_WORDS:
        if w in low:
            raise Refusal(f"refused: --out path may not contain {w!r}")


def check_limits(max_tx: int, floor_ts: int) -> None:
    if max_tx > MAX_TX_CAP:
        raise Refusal(f"refused: --max-tx above {MAX_TX_CAP}")
    if max_tx < 1:
        raise Refusal("refused: --max-tx must be >= 1")
    if floor_ts < FLOOR_TS:
        raise Refusal(f"refused: floor earlier than {FLOOR_ISO}")


def find_signatures(rpc: RpcFn, max_tx: int, floor_ts: int) -> list[tuple[str, int]]:
    """Newest-first successful (signature, slot) at or after floor_ts. In memory only."""
    out: list[tuple[str, int]] = []
    before: str | None = None
    for _ in range(MAX_PAGES):
        cfg: dict[str, Any] = {"limit": PAGE, "commitment": "finalized"}
        if before:
            cfg["before"] = before
        page = rpc("getSignaturesForAddress", [MIGRATION_ACCOUNT, cfg])
        if not page:
            break
        for ent in page:
            bt = ent.get("blockTime")
            if isinstance(bt, int) and bt < floor_ts:
                return out
            if ent.get("err") is None and isinstance(bt, int) and isinstance(ent.get("signature"), str):
                out.append((ent["signature"], int(ent.get("slot") or 0)))
                if len(out) >= max_tx:
                    return out
        before = page[-1].get("signature")
        if not before:
            break
    return out


def _logs(tx: Mapping[str, Any]) -> list[str]:
    meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}
    logs = meta.get("logMessages") or []
    return [l for l in logs if isinstance(l, str)]


def _disc_in_program_data(logs: Sequence[str]) -> bool:
    for line in logs:
        if "Program data: " not in line:
            continue
        blob = line.split("Program data: ", 1)[1].strip()
        try:
            raw = base64.b64decode(blob, validate=False)
        except ValueError:
            continue
        if raw[:8] == _MIGRATE_DISC:
            return True
    return False


def _pctl(vals: list[int]) -> tuple[int, int]:
    if not vals:
        return 0, 0
    return int(statistics.median_low(vals)), max(vals)


def count_txs(txs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts over fetched getTransaction results (each carries slot and blockTime). No identifiers kept."""
    lines: list[int] = []
    nbytes: list[int] = []
    c: dict[str, Any] = dict.fromkeys(COUNT_KEYS, 0)
    neither: list[Mapping[str, Any]] = []
    for tx in txs:
        logs = _logs(tx)
        lines.append(len(logs))
        nbytes.append(sum(len(l.encode("utf-8", "replace")) for l in logs))
        if any("Log truncated" in l for l in logs):
            c["n_log_truncated"] += 1
        in_logs = _disc_in_program_data(logs)
        in_inner = any(b[:8] == _MIGRATE_DISC for b in cpi_events_from_tx(tx))
        if in_logs:
            c["n_disc_in_logs"] += 1
        elif in_inner:
            c["n_disc_inner_only"] += 1
        else:
            c["n_disc_neither"] += 1
            neither.append(tx)
        if init_boost_from_tx(tx, logs) is not None:
            c["n_init_boost"] += 1
        # One-tx pseudo-block: the comparison is over exactly these txs, no getBlock needed.
        block = {"blockTime": tx.get("blockTime"), "slot": tx.get("slot"), "transactions": [tx]}
        legacy = rows_from_block(block, {}, event_v=False)["migrations"]
        evv = rows_from_block(block, {}, event_v=True)["migrations"]
        c["legacy_migration_rows"] += len(legacy)
        c["legacy_txs_with_row"] += 1 if legacy else 0
        c["event_v_migration_rows"] += len(evv)
        c["event_v_txs_with_row"] += 1 if evv else 0
    c["n_tx"] = len(txs)
    c["neither_pump_ix_disc_hist"] = {}
    for tx in neither:
        for k, v in pump_ix_disc_hist(tx).items():
            c["neither_pump_ix_disc_hist"][k] = c["neither_pump_ix_disc_hist"].get(k, 0) + v
    c["log_lines_p50"], c["log_lines_max"] = _pctl(lines)
    c["log_bytes_p50"], c["log_bytes_max"] = _pctl(nbytes)
    return c


def fetch_txs(rpc: RpcFn, sigs: Sequence[tuple[str, int]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sig, _slot in sigs:
        tx = None
        for ver in (0, 1):  # 0 as briefed; 1 only if the node rejects a v1 tx at 0
            try:
                tx = rpc("getTransaction", [sig, {"encoding": "json", "commitment": "finalized",
                                                 "maxSupportedTransactionVersion": ver}])
            except RuntimeError:
                tx = None
                continue
            break
        if isinstance(tx, dict):
            out.append(tx)
    return out


# ------------------------------------------------------------------ graduations mode


def _tx_ixs(tx: Mapping[str, Any], keys: Sequence[str]) -> tuple[list[tuple[str, bytes]], list[tuple[str, bytes]]]:
    """(top-level, inner) lists of (program id, instruction data bytes). Account ids are not kept."""
    body = tx.get("transaction")
    msg = body.get("message") if isinstance(body, dict) else None
    meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}

    def conv(ins: Any) -> tuple[str, bytes] | None:
        if not isinstance(ins, dict):
            return None
        idx = ins.get("programIdIndex")
        if not isinstance(idx, int) or idx >= len(keys):
            return None
        try:
            data = b58decode(ins["data"]) if isinstance(ins.get("data"), str) else b""
        except ValueError:
            data = b""
        return keys[idx], data

    top_raw = (msg.get("instructions") if isinstance(msg, dict) else None) or []
    top = [x for x in (conv(i) for i in top_raw) if x]
    inner: list[tuple[str, bytes]] = []
    for g in meta.get("innerInstructions") or []:
        if isinstance(g, dict):
            inner += [x for x in (conv(i) for i in g.get("instructions") or []) if x]
    return top, inner


def pump_ix_disc_hist(tx: Mapping[str, Any]) -> dict[str, int]:
    """Histogram of 8-byte discriminators (hex, a schema id) of pump instructions, 'top:'/'inner:' prefixed.
    Anchor emit_cpi event self-calls are skipped."""
    top, inner = _tx_ixs(tx, tx_account_keys(tx))
    out: dict[str, int] = {}
    for tag, lst in (("top:", top), ("inner:", inner)):
        for pid, data in lst:
            if pid != PUMP_BONDING_PROGRAM or len(data) < 8 or data[:8] == ANCHOR_EVENT_IX_TAG:
                continue
            k = tag + data[:8].hex()
            out[k] = out.get(k, 0) + 1
    return out


def completes_in_block(block: Mapping[str, Any]) -> list[tuple[str, str, int]]:
    """(signature, base mint, block_time) of each successful tx carrying a pump `complete` event."""
    bt = block.get("blockTime")
    out: list[tuple[str, str, int]] = []
    if not isinstance(bt, int):
        return out
    for tx in block.get("transactions") or []:
        if not isinstance(tx, dict):
            continue
        meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}
        if meta.get("err") is not None:
            continue
        body = tx.get("transaction")
        sigs = body.get("signatures") if isinstance(body, dict) else None
        if not (isinstance(sigs, list) and sigs and isinstance(sigs[0], str)):
            continue
        ev = None
        _creates, moved = lifecycle_from_logs(_logs(tx))
        for m in moved:
            if m.get("type") == "complete":
                ev = m
        if ev is None:
            for blob in cpi_events_from_tx(tx):
                if blob[:8] == _COMPLETE_DISC:
                    ev = decode_complete_event(blob)
        if ev is not None and ev.get("mint"):
            out.append((sigs[0], ev["mint"], bt))
    return out


def find_completes(rpc: RpcFn, max_tx: int, floor_ts: int, max_blocks: int,
                   stats: dict[str, Any]) -> list[tuple[str, str, int]]:
    """Walk finalized blocks backwards from the tip until max_tx distinct mints or max_blocks. In memory only."""
    slot = rpc("getSlot", [{"commitment": "finalized"}])
    found: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    consecutive_err = 0
    for _ in range(max_blocks):
        if not isinstance(slot, int) or slot < 0:
            break
        try:
            block = rpc("getBlock", _getblock_params(slot, full=True))
            consecutive_err = 0
        except RuntimeError:
            stats["n_block_errors"] += 1
            consecutive_err += 1
            if consecutive_err >= 20:
                break
            slot -= 1
            continue
        slot -= 1
        if not isinstance(block, dict):
            continue  # skipped slot
        stats["n_blocks_scanned"] += 1
        bt = block.get("blockTime")
        if isinstance(bt, int) and bt < floor_ts:
            break
        for sig, mint, t in completes_in_block(block):
            if mint not in seen:
                seen.add(mint)
                found.append((sig, mint, t))
                if len(found) >= max_tx:
                    return found
    return found


def earliest_ok_signature(rpc: RpcFn, account: str) -> tuple[tuple[str, int] | None, bool]:
    """Oldest successful (signature, blockTime) of an account, and whether paging was capped."""
    before: str | None = None
    last: tuple[str, int] | None = None
    for _ in range(MAX_POOL_PAGES):
        cfg: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            cfg["before"] = before
        page = rpc("getSignaturesForAddress", [account, cfg])
        if not page:
            return last, False
        for e in page:
            if e.get("err") is None and isinstance(e.get("signature"), str) and isinstance(e.get("blockTime"), int):
                last = (e["signature"], e["blockTime"])
        if len(page) < 1000:
            return last, False
        before = page[-1].get("signature")
        if not before:
            return last, False
    return last, True


def _inc(d: dict[str, int], k: str, n: int = 1) -> None:
    d[k] = d.get(k, 0) + n


def classify_creation(tx: Mapping[str, Any], complete_sig: str | None, complete_bt: int, g: dict[str, Any]) -> None:
    """Fold one pool-creation tx into the count dict g. Nothing identifying is kept."""
    keys = tx_account_keys(tx)
    top, _inner = _tx_ixs(tx, keys)
    labels = {"pump" if p == PUMP_BONDING_PROGRAM else "pumpswap" if p == PUMPSWAP_PROGRAM else "other" for p, _ in top}
    for lab in labels:
        g["n_top_" + lab] += 1
    pump_top = [d for p, d in top if p == PUMP_BONDING_PROGRAM and d[:8] != ANCHOR_EVENT_IX_TAG]
    if not pump_top:
        _inc(g["pump_ix_class_hist"], "no_top_level_pump_ix")
    for d in pump_top:
        _inc(g["pump_ix_class_hist"], _MIGRATE_IX.get(bytes(d[:8]), "unknown"))
    logs = _logs(tx)
    blobs: list[bytes] = []
    for line in logs:
        raw = _program_data(line)
        if raw is not None and len(raw) >= 8:
            blobs.append(raw)
    inner_blobs = cpi_events_from_tx(tx)
    in_logs = any(b[:8] == _MIGRATE_DISC for b in blobs)
    in_inner = any(b[:8] == _MIGRATE_DISC for b in inner_blobs)
    if in_logs:
        g["n_disc_in_logs"] += 1
    elif in_inner:
        g["n_disc_inner_only"] += 1
    else:
        g["n_disc_neither"] += 1
    for b in blobs:
        if b[:8] != _MIGRATE_DISC:
            _inc(g["other_program_data_disc_hist"], b[:8].hex())
    if in_logs or in_inner:
        for b in [b for b in blobs + inner_blobs if b[:8] == _MIGRATE_DISC]:
            if decode_migration_event(b) is not None:
                _inc(g["decode_migration_event_reason_hist"], "ok")
            elif len(b) < 200:
                _inc(g["decode_migration_event_reason_hist"], "len_lt_200")
            elif not (1_700_000_000 <= int.from_bytes(b[128:136], "little", signed=True) <= 1_900_000_000):
                _inc(g["decode_migration_event_reason_hist"], "ts_out_of_window")
            else:
                _inc(g["decode_migration_event_reason_hist"], "other")
    body = tx.get("transaction")
    msg = body.get("message") if isinstance(body, dict) else {}
    hdr = msg.get("header") if isinstance(msg, dict) else None
    n_sig = hdr.get("numRequiredSignatures") if isinstance(hdr, dict) and isinstance(hdr.get("numRequiredSignatures"), int) else 1
    if MIGRATION_ACCOUNT in keys[:n_sig]:
        g["n_signer_is_migration_account"] += 1
    sigs = body.get("signatures") if isinstance(body, dict) else None
    if complete_sig and isinstance(sigs, list) and sigs and sigs[0] == complete_sig:
        g["n_same_tx_as_complete"] += 1
    bt = tx.get("blockTime")
    if isinstance(bt, int):
        g["_secs"].append(bt - complete_bt)
    block = {"blockTime": bt, "slot": tx.get("slot"), "transactions": [tx]}
    g["legacy_migration_row_yield"] += sum(
        1 for r in rows_from_block(block, {}, event_v=False)["migrations"] if r.get("type") == "migration"
    )


def run_graduations(rpc: RpcFn, max_tx: int, floor_ts: int, max_blocks: int) -> dict[str, Any]:
    check_limits(max_tx, floor_ts)
    if not 1 <= max_blocks <= MAX_BLOCKS_CAP:
        raise Refusal(f"refused: --max-blocks must be 1..{MAX_BLOCKS_CAP}")
    g: dict[str, Any] = {
        "mode": "graduations", "n_complete_events": 0, "n_blocks_scanned": 0, "n_block_errors": 0,
        "n_no_creation_tx": 0, "n_creation_history_capped": 0, "n_creation_tx": 0,
        "n_top_pump": 0, "n_top_pumpswap": 0, "n_top_other": 0, "pump_ix_class_hist": {},
        "n_disc_in_logs": 0, "n_disc_inner_only": 0, "n_disc_neither": 0, "other_program_data_disc_hist": {},
        "n_signer_is_migration_account": 0, "n_same_tx_as_complete": 0,
        "legacy_migration_row_yield": 0, "decode_migration_event_reason_hist": {}, "_secs": [],
    }
    completes = find_completes(rpc, max_tx, floor_ts, max_blocks, g)
    g["n_complete_events"] = len(completes)
    for csig, mint, cbt in completes:
        first, capped = earliest_ok_signature(rpc, canonical_pool(mint))
        if capped:
            g["n_creation_history_capped"] += 1
        if first is None:
            g["n_no_creation_tx"] += 1
            continue
        tx = None
        for ver in (0, 1):
            try:
                tx = rpc("getTransaction", [first[0], {"encoding": "json", "commitment": "finalized",
                                                      "maxSupportedTransactionVersion": ver}])
            except RuntimeError:
                tx = None
                continue
            break
        if not isinstance(tx, dict):
            g["n_no_creation_tx"] += 1
            continue
        g["n_creation_tx"] += 1
        classify_creation(tx, csig, cbt, g)
    secs = g.pop("_secs")
    g["secs_complete_to_creation_p50"], g["secs_complete_to_creation_max"] = _pctl(secs)
    return {k: g[k] for k in GRAD_KEYS}


def make_rpc(url: str, rps: float = MAX_RPS) -> RpcFn:
    limiter = RateLimiter(min(rps, MAX_RPS))

    def rpc(method: str, params: list) -> Any:
        try:
            result, _wire, _code = rpc_call(url, method, params, limiter, timeout=60.0)
        except RuntimeError:
            # Drop the message: it could carry a request detail. Method name only.
            raise RuntimeError(f"{method} failed") from None
        return result

    return rpc


def run(rpc: RpcFn, max_tx: int, floor_ts: int) -> dict[str, Any]:
    check_limits(max_tx, floor_ts)
    sigs = find_signatures(rpc, max_tx, floor_ts)
    return count_txs(fetch_txs(rpc, sigs))


def main(argv: Sequence[str] | None = None, rpc: RpcFn | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--max-tx", type=int, default=MAX_TX_CAP)
    ap.add_argument("--floor", default=FLOOR_ISO, help="ISO time, not earlier than " + FLOOR_ISO)
    ap.add_argument("--out", default=None)
    ap.add_argument("--mode", choices=("migrations", "graduations"), default="migrations")
    ap.add_argument("--max-blocks", type=int, default=4000,
                    help="graduations mode: finalized blocks to walk back looking for complete events")
    args = ap.parse_args(argv)
    try:
        check_url(args.url)
        if args.out:
            check_out(args.out)
        try:
            floor_ts = int(datetime.fromisoformat(args.floor.replace("Z", "+00:00")).timestamp())
        except ValueError:
            raise Refusal("refused: unparseable --floor") from None
        check_limits(args.max_tx, floor_ts)
    except Refusal as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        if args.mode == "graduations":
            counts = run_graduations(rpc or make_rpc(args.url), args.max_tx, floor_ts, args.max_blocks)
        else:
            counts = run(rpc or make_rpc(args.url), args.max_tx, floor_ts)
    except Refusal as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError:
        print("rpc error (details withheld: may carry identifiers)", file=sys.stderr)
        return 1
    keys = GRAD_KEYS if args.mode == "graduations" else COUNT_KEYS
    text = json.dumps({k: counts[k] for k in keys}, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
