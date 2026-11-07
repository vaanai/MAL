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

from tools.pump_history_backfill import (
    _MIGRATE_DISC,
    RateLimiter,
    cpi_events_from_tx,
    init_boost_from_tx,
    rpc_call,
    rows_from_block,
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
)

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


def count_txs(txs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Counts over fetched getTransaction results (each carries slot and blockTime). No identifiers kept."""
    lines: list[int] = []
    nbytes: list[int] = []
    c = dict.fromkeys(COUNT_KEYS, 0)
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


def run(rpc: RpcFn, max_tx: int, floor_ts: int) -> dict[str, int]:
    check_limits(max_tx, floor_ts)
    sigs = find_signatures(rpc, max_tx, floor_ts)
    return count_txs(fetch_txs(rpc, sigs))


def main(argv: Sequence[str] | None = None, rpc: RpcFn | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--max-tx", type=int, default=MAX_TX_CAP)
    ap.add_argument("--floor", default=FLOOR_ISO, help="ISO time, not earlier than " + FLOOR_ISO)
    ap.add_argument("--out", default=None)
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
        counts = run(rpc or make_rpc(args.url), args.max_tx, floor_ts)
    except RuntimeError:
        print("rpc error (details withheld: may carry identifiers)", file=sys.stderr)
        return 1
    text = json.dumps({k: counts[k] for k in COUNT_KEYS}, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
