#!/usr/bin/env python3
"""EXP-003 v0 producer — RPC historical outcome_mark ticks (local, no sealed rewrite).

Reads sealed observe JSONL, subsamples bonding creates, fetches Solana RPC
signatures + transactions for bondingCurveKey (else mint), appends side marks JSONL.

Stdlib only. Public RPC via SOLANA_RPC_URL (no secrets in repo).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools.exp001_mislabel import LoadedRow, load_jsonl_files, t_ws_missing
from tools.exp002_paper_runner import HORIZON_SECONDS, is_bonding_create
from tools.marks import (
    MARK_SCHEMA_VERSION,
    MARK_TYPE,
    mark_void_reason,
    parse_iso_ts,
    select_last_as_of,
)

DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_SAMPLE_N = 300
DEFAULT_SEED = 1
DEFAULT_WINDOW_S = 60
DEFAULT_COMMITMENT = "confirmed"
DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"
ALLOWED_COMMITMENTS = frozenset({"confirmed", "finalized"})
HORIZON_LOG_KEYS = ("1s", "5s", "15s", "30s", "60s")

LAMPORTS_PER_SOL = 1_000_000_000
PUMP_TOKEN_DECIMALS = 6
# Anchor event discriminators: sha256("event:<Name>")[:8] (pump.fun IDL).
_PUMP_TRADE_EVENT_DISC = bytes.fromhex("bddb7fd34ee661ee")
_PUMP_CREATE_EVENT_DISC = bytes.fromhex("1b72a94ddeeb6376")

LOG = logging.getLogger("exp003_rpc_backfill")


def resolve_rpc_url() -> str:
    return os.environ.get("SOLANA_RPC_URL", DEFAULT_RPC_URL).strip() or DEFAULT_RPC_URL


def bonding_curve_address(row: Mapping[str, Any]) -> str | None:
    payload = row.get("ws_payload") if isinstance(row.get("ws_payload"), dict) else {}
    for key in ("bondingCurveKey",):
        val = row.get(key)
        if val is None and payload:
            val = payload.get(key)
        if isinstance(val, str) and val not in ("", "UNK"):
            return val
    mint = row.get("mint")
    if isinstance(mint, str) and mint not in ("", "UNK"):
        return mint
    return None


def t_ws_to_unix(t_ws: str) -> int | None:
    dt = parse_iso_ts(t_ws)
    if dt is None:
        return None
    return int(dt.timestamp())


def block_time_in_window(block_time: int, t_unix: int, window_s: int) -> bool:
    return t_unix < block_time <= t_unix + window_s


def subsample_creates(
    creates: Sequence[LoadedRow],
    *,
    sample_n: int,
    seed: int,
) -> list[LoadedRow]:
    if sample_n <= 0 or len(creates) <= sample_n:
        return list(creates)
    rng = random.Random(seed)
    indices = sorted(rng.sample(range(len(creates)), sample_n))
    return [creates[i] for i in indices]


def collect_bonding_creates(loaded: Sequence[LoadedRow]) -> list[LoadedRow]:
    out: list[LoadedRow] = []
    for item in loaded:
        if not is_bonding_create(item.row):
            continue
        if t_ws_missing(item.row):
            continue
        out.append(item)
    return out


def marks_path_for_t_ws(t_ws: str, output_dir: Path) -> Path:
    dt = parse_iso_ts(t_ws)
    if dt is None:
        day = "unknown"
    else:
        day = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return output_dir / f"marks-{day}.jsonl"


def _walk_for_reserve_fields(obj: Any, found: dict[str, float]) -> None:
    if isinstance(obj, dict):
        for key in ("marketCapSol", "vSolInBondingCurve"):
            if key in obj and key not in found:
                try:
                    val = float(obj[key])
                except (TypeError, ValueError):
                    continue
                if val > 0:
                    found[key] = val
        for v in obj.values():
            _walk_for_reserve_fields(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_reserve_fields(item, found)


def _borsh_read_string(data: bytes, offset: int) -> tuple[str | None, int]:
    if offset + 4 > len(data):
        return None, offset
    length = int.from_bytes(data[offset : offset + 4], "little")
    offset += 4
    end = offset + length
    if end > len(data):
        return None, offset
    try:
        text = data[offset:end].decode("utf-8")
    except UnicodeDecodeError:
        return None, end
    return text, end


def _parse_pump_trade_event(payload: bytes) -> dict[str, Any] | None:
    """Fixed-prefix decode for pump.fun TradeEvent (Anchor CPI log)."""
    if len(payload) < 113 or payload[:8] != _PUMP_TRADE_EVENT_DISC:
        return None
    off = 8 + 32  # mint
    if off + 8 + 8 + 1 + 32 + 8 + 8 + 8 > len(payload):
        return None
    sol_amount = int.from_bytes(payload[off : off + 8], "little")
    off += 8
    token_amount = int.from_bytes(payload[off : off + 8], "little")
    off += 8
    is_buy = payload[off] != 0
    off += 1 + 32 + 8  # user, timestamp
    virtual_sol = int.from_bytes(payload[off : off + 8], "little")
    off += 8
    virtual_token = int.from_bytes(payload[off : off + 8], "little")
    if virtual_sol <= 0 or virtual_token <= 0:
        return None
    return {
        "virtual_sol_reserves": virtual_sol,
        "virtual_token_reserves": virtual_token,
        "sol_amount": sol_amount,
        "token_amount": token_amount,
        "is_buy": is_buy,
    }


def _parse_pump_create_event(payload: bytes) -> dict[str, Any] | None:
    """Borsh decode for pump.fun CreateEvent (name/symbol/uri strings then fixed tail)."""
    if len(payload) < 8 or payload[:8] != _PUMP_CREATE_EVENT_DISC:
        return None
    off = 8
    for _ in range(3):
        _, off = _borsh_read_string(payload, off)
    # mint, bonding_curve, user, creator
    if off + 32 * 4 + 8 + 8 * 4 > len(payload):
        return None
    off += 32 * 4
    off += 8  # timestamp i64
    virtual_token = int.from_bytes(payload[off : off + 8], "little")
    off += 8
    virtual_sol = int.from_bytes(payload[off : off + 8], "little")
    off += 8
    off += 8  # real_token_reserves
    token_total_supply = int.from_bytes(payload[off : off + 8], "little")
    if virtual_sol <= 0 or virtual_token <= 0:
        return None
    return {
        "virtual_sol_reserves": virtual_sol,
        "virtual_token_reserves": virtual_token,
        "token_total_supply": token_total_supply,
    }


def _pump_market_cap_sol(
    virtual_sol_lamports: int,
    virtual_token_reserves: int,
    token_total_supply: int,
) -> float | None:
    if virtual_token_reserves <= 0 or token_total_supply <= 0:
        return None
    cap_lamports = (virtual_sol_lamports * token_total_supply) // virtual_token_reserves
    if cap_lamports <= 0:
        return None
    return cap_lamports / LAMPORTS_PER_SOL


def _price_from_pump_log_messages(
    log_messages: Sequence[Any],
) -> tuple[float, str, dict[str, Any]] | None:
    """Decode pump.fun CreateEvent / TradeEvent from meta.logMessages Program data lines."""
    create_ev: dict[str, Any] | None = None
    trade_ev: dict[str, Any] | None = None
    for line in log_messages:
        if not isinstance(line, str) or not line.startswith("Program data: "):
            continue
        blob = line[len("Program data: ") :].strip()
        if not blob:
            continue
        try:
            raw = base64.b64decode(blob, validate=False)
        except (ValueError, binascii.Error):
            continue
        trade = _parse_pump_trade_event(raw)
        if trade is not None:
            trade_ev = trade
            continue
        create = _parse_pump_create_event(raw)
        if create is not None:
            create_ev = create

    state = trade_ev or create_ev
    if state is None:
        return None

    virtual_sol = int(state["virtual_sol_reserves"])
    virtual_token = int(state["virtual_token_reserves"])
    v_sol_sol = virtual_sol / LAMPORTS_PER_SOL
    extra: dict[str, Any] = {
        "vSolInBondingCurve": v_sol_sol,
        "vTokensInBondingCurve": virtual_token / (10**PUMP_TOKEN_DECIMALS),
    }
    supply: int | None = None
    if create_ev is not None:
        supply = int(create_ev.get("token_total_supply") or 0)
    if trade_ev is not None:
        extra["txType"] = "buy" if trade_ev.get("is_buy") else "sell"
        extra["solAmount"] = int(trade_ev["sol_amount"]) / LAMPORTS_PER_SOL
        extra["tokenAmount"] = int(trade_ev["token_amount"]) / (10**PUMP_TOKEN_DECIMALS)
    elif create_ev is not None:
        extra["txType"] = "create"

    if supply and supply > 0:
        mcap = _pump_market_cap_sol(virtual_sol, virtual_token, supply)
        if mcap is not None and mcap > 0:
            extra["marketCapSol"] = mcap
            return mcap, "marketCapSol", extra

    if v_sol_sol > 0:
        return v_sol_sol, "vSolInBondingCurve", extra
    return None


def price_from_transaction(
    tx_response: Mapping[str, Any],
) -> tuple[float, str, dict[str, Any]] | None:
    """Price proxy from getTransaction: pump.fun program logs, else legacy test fields."""
    meta = tx_response.get("meta")
    if isinstance(meta, dict) and meta.get("err") is not None:
        return None
    if isinstance(meta, dict):
        logs = meta.get("logMessages")
        if isinstance(logs, list) and logs:
            parsed = _price_from_pump_log_messages(logs)
            if parsed is not None:
                return parsed
    found: dict[str, float] = {}
    _walk_for_reserve_fields(tx_response, found)
    extra_legacy = {k: found[k] for k in found}
    if "marketCapSol" in found:
        return found["marketCapSol"], "marketCapSol", extra_legacy
    if "vSolInBondingCurve" in found:
        return found["vSolInBondingCurve"], "vSolInBondingCurve", extra_legacy
    return None


def block_time_from_transaction(tx_response: Mapping[str, Any]) -> int | None:
    bt = tx_response.get("blockTime")
    if isinstance(bt, int):
        return bt
    if isinstance(bt, float):
        return int(bt)
    return None


def iso_from_block_time(block_time: int) -> str:
    return datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat()


def build_outcome_mark_row(
    *,
    create: Mapping[str, Any],
    tx_signature: str,
    block_time: int,
    price: float,
    price_field: str,
    commitment: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    mint = create.get("mint")
    parent = create.get("signature")
    row: dict[str, Any] = {
        "schema_version": MARK_SCHEMA_VERSION,
        "type": MARK_TYPE,
        "mint": mint,
        "parent_signature": parent,
        "t_mark": iso_from_block_time(block_time),
        "source": "rpc_tx",
        "price_proxy": price,
        "t_decision": create.get("t_ws"),
        "signature": tx_signature,
        "commitment": commitment,
        "price_field": price_field,
    }
    if isinstance(extra, dict):
        for key in (
            "txType",
            "solAmount",
            "tokenAmount",
            "vSolInBondingCurve",
            "vTokensInBondingCurve",
            "marketCapSol",
        ):
            if key in extra:
                row[key] = extra[key]
    return row


def horizon_coverage_log(
    t_decision: datetime,
    ticks: Sequence[tuple[datetime, float]],
) -> str:
    parts: list[str] = []
    for name in HORIZON_LOG_KEYS:
        offset = HORIZON_SECONDS.get(name)
        if offset is None:
            continue
        chosen = select_last_as_of(ticks, t_decision, float(offset))
        if chosen is None:
            parts.append(f"{name}=na")
        else:
            parts.append(f"{name}=ok@{chosen[0].isoformat()}")
    return " ".join(parts)


@dataclass
class RpcError(Exception):
    message: str
    code: int | None = None
    http_status: int | None = None


@dataclass
class SolanaRpcClient:
    url: str
    commitment: str = DEFAULT_COMMITMENT
    min_interval_s: float = 0.05
    max_retries: int = 6
    _last_request_at: float = field(default=0.0, repr=False)
    _post_fn: Callable[[str, str, list[Any]], Any] | None = field(
        default=None, repr=False
    )

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self.min_interval_s - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def post(self, method: str, params: list[Any]) -> Any:
        if self._post_fn is not None:
            return self._post_fn(method, params)
        self._throttle()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(
            "utf-8"
        )
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        delay = 1.0
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    if resp.status == 429:
                        raise urllib.error.HTTPError(
                            self.url, 429, "Too Many Requests", resp.headers, None
                        )
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt + 1 < self.max_retries:
                    LOG.warning("rpc 429; backoff %.1fs (attempt %d)", delay, attempt + 1)
                    time.sleep(delay)
                    delay = min(delay * 2, 32.0)
                    continue
                raise RpcError(f"HTTP {exc.code}", http_status=exc.code) from exc
            except urllib.error.URLError as exc:
                if attempt + 1 < self.max_retries:
                    LOG.warning("rpc network error; backoff %.1fs", delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 32.0)
                    continue
                raise RpcError(str(exc.reason)) from exc

            if "error" in payload:
                err = payload["error"]
                code = err.get("code") if isinstance(err, dict) else None
                msg = err.get("message") if isinstance(err, dict) else str(err)
                if code == 429 or (isinstance(msg, str) and "429" in msg):
                    if attempt + 1 < self.max_retries:
                        LOG.warning("rpc error 429; backoff %.1fs", delay)
                        time.sleep(delay)
                        delay = min(delay * 2, 32.0)
                        continue
                raise RpcError(str(msg), code=code if isinstance(code, int) else None)
            return payload.get("result")

        raise RpcError("max retries exceeded")

    def get_signatures_for_address(
        self,
        address: str,
        *,
        before: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        cfg: dict[str, Any] = {"limit": limit, "commitment": self.commitment}
        if before:
            cfg["before"] = before
        result = self.post("getSignaturesForAddress", [address, cfg])
        if not isinstance(result, list):
            return []
        return [x for x in result if isinstance(x, dict)]

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        result = self.post(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "json",
                    "commitment": self.commitment,
                    "maxSupportedTransactionVersion": 1,
                },
            ],
        )
        return result if isinstance(result, dict) else None


def collect_signatures_in_window(
    client: SolanaRpcClient,
    address: str,
    *,
    t_unix: int,
    window_s: int,
    max_pages: int = 50,
    page_limit: int = 1000,
) -> list[dict[str, Any]]:
    """Paginate getSignaturesForAddress (newest first) until blockTime < t_unix."""
    kept: list[dict[str, Any]] = []
    before: str | None = None
    for _ in range(max_pages):
        page = client.get_signatures_for_address(address, before=before, limit=page_limit)
        if not page:
            break
        stop = False
        for item in page:
            bt = item.get("blockTime")
            if bt is None:
                continue
            if not isinstance(bt, int):
                try:
                    bt = int(bt)
                except (TypeError, ValueError):
                    continue
            if bt <= t_unix:
                stop = True
                break
            if block_time_in_window(bt, t_unix, window_s):
                if item.get("err") is None:
                    kept.append(item)
        if stop:
            break
        if len(page) < page_limit:
            break
        last_sig = page[-1].get("signature")
        if not isinstance(last_sig, str) or last_sig == before:
            break
        before = last_sig
    return kept


def process_create(
    client: SolanaRpcClient,
    create: Mapping[str, Any],
    *,
    window_s: int,
    commitment: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: dict[str, Any] = {
        "mint": create.get("mint"),
        "signature": create.get("signature"),
        "address": None,
        "skipped": None,
        "signatures_n": 0,
        "marks_n": 0,
        "tx_errors_n": 0,
        "no_price_n": 0,
    }
    t_ws = create.get("t_ws")
    if not isinstance(t_ws, str):
        stats["skipped"] = "no_t_ws"
        return [], stats
    t_unix = t_ws_to_unix(t_ws)
    if t_unix is None:
        stats["skipped"] = "bad_t_ws"
        return [], stats
    address = bonding_curve_address(create)
    if address is None:
        stats["skipped"] = "no_address"
        return [], stats
    stats["address"] = address

    sig_infos = collect_signatures_in_window(
        client, address, t_unix=t_unix, window_s=window_s
    )
    stats["signatures_n"] = len(sig_infos)
    marks: list[dict[str, Any]] = []
    tick_pairs: list[tuple[datetime, float]] = []

    for info in sig_infos:
        sig = info.get("signature")
        if not isinstance(sig, str):
            continue
        tx = client.get_transaction(sig)
        if tx is None:
            stats["tx_errors_n"] += 1
            continue
        bt = block_time_from_transaction(tx)
        if bt is None:
            bt = info.get("blockTime")
            if isinstance(bt, int):
                pass
            else:
                stats["tx_errors_n"] += 1
                continue
        if not block_time_in_window(int(bt), t_unix, window_s):
            continue
        priced = price_from_transaction(tx)
        if priced is None:
            stats["no_price_n"] += 1
            continue
        price, price_field, extra = priced
        extra = dict(extra)
        row = build_outcome_mark_row(
            create=create,
            tx_signature=sig,
            block_time=int(bt),
            price=price,
            price_field=price_field,
            commitment=commitment,
            extra=extra,
        )
        reason = mark_void_reason(row)
        if reason is not None:
            stats["no_price_n"] += 1
            continue
        marks.append(row)
        t_mark = parse_iso_ts(row["t_mark"])
        if t_mark is not None:
            tick_pairs.append((t_mark, price))

    stats["marks_n"] = len(marks)
    t_decision = parse_iso_ts(t_ws)
    if t_decision is not None and tick_pairs:
        stats["horizons"] = horizon_coverage_log(t_decision, tick_pairs)
    return marks, stats


def append_marks(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")


def run_backfill(
    *,
    observe_paths: Sequence[Path],
    output_dir: Path,
    sample_n: int,
    seed: int,
    window_s: int,
    commitment: str,
    rpc_url: str,
    client: SolanaRpcClient | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if commitment not in ALLOWED_COMMITMENTS:
        raise ValueError(f"commitment must be one of {sorted(ALLOWED_COMMITMENTS)}")
    loaded, malformed = load_jsonl_files(observe_paths)
    creates = collect_bonding_creates(loaded)
    sampled = subsample_creates(creates, sample_n=sample_n, seed=seed)
    rpc = client or SolanaRpcClient(url=rpc_url, commitment=commitment)

    summary: dict[str, Any] = {
        "exp": "EXP-003",
        "producer": "rpc_backfill_v0",
        "observe_paths": [str(p) for p in observe_paths],
        "output_dir": str(output_dir),
        "observe_malformed_n": malformed,
        "creates_total_n": len(creates),
        "creates_sampled_n": len(sampled),
        "sample_n": sample_n,
        "seed": seed,
        "window_s": window_s,
        "commitment": commitment,
        "rpc_url": rpc_url,
        "dry_run": dry_run,
        "marks_written_n": 0,
        "creates_with_marks_n": 0,
        "output_files": {},
        "skipped_reasons": {},
    }

    for idx, item in enumerate(sampled, start=1):
        create = item.row
        mint = create.get("mint", "?")
        LOG.info(
            "[%d/%d] mint=%s parent_sig=%s",
            idx,
            len(sampled),
            mint,
            create.get("signature"),
        )
        if dry_run:
            continue
        marks, stats = process_create(
            rpc, create, window_s=window_s, commitment=commitment
        )
        skipped = stats.get("skipped")
        if skipped:
            summary["skipped_reasons"][str(skipped)] = (
                summary["skipped_reasons"].get(str(skipped), 0) + 1
            )
            LOG.info("  skipped: %s", skipped)
            continue
        if stats.get("horizons"):
            LOG.info("  %s", stats["horizons"])
        LOG.info(
            "  sigs_in_window=%d marks=%d no_price=%d tx_err=%d",
            stats["signatures_n"],
            stats["marks_n"],
            stats.get("no_price_n", 0),
            stats.get("tx_errors_n", 0),
        )
        if not marks:
            continue
        t_ws = create.get("t_ws")
        if not isinstance(t_ws, str):
            continue
        out_path = marks_path_for_t_ws(t_ws, output_dir)
        append_marks(out_path, marks)
        summary["marks_written_n"] += len(marks)
        summary["creates_with_marks_n"] += 1
        rel = str(out_path)
        summary["output_files"][rel] = summary["output_files"].get(rel, 0) + len(marks)

    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.exp003_rpc_backfill",
        description=(
            "EXP-003 RPC historical producer: sealed bonding creates -> outcome_mark "
            "side JSONL (no observe mutation)."
        ),
    )
    p.add_argument(
        "jsonl",
        nargs="+",
        type=Path,
        help="Sealed observe JSONL path(s)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for marks-YYYY-MM-DD.jsonl (default: data/observe)",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE_N,
        help=f"Subsample N bonding creates (default {DEFAULT_SAMPLE_N}; 0 = all)",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"RNG seed for subsample (default {DEFAULT_SEED})",
    )
    p.add_argument(
        "--window-s",
        type=int,
        default=DEFAULT_WINDOW_S,
        help=f"Post-create window seconds (default {DEFAULT_WINDOW_S})",
    )
    p.add_argument(
        "--commitment",
        choices=sorted(ALLOWED_COMMITMENTS),
        default=DEFAULT_COMMITMENT,
        help="RPC commitment for getTransaction (not processed)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="List sampled creates only; no RPC or file writes",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG logging",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    missing = [p for p in args.jsonl if not p.is_file()]
    if missing:
        print(f"error: JSONL not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 1
    if args.window_s < 1:
        print("error: --window-s must be >= 1", file=sys.stderr)
        return 1
    rpc_url = resolve_rpc_url()
    try:
        summary = run_backfill(
            observe_paths=args.jsonl,
            output_dir=args.output_dir,
            sample_n=args.sample,
            seed=args.seed,
            window_s=args.window_s,
            commitment=args.commitment,
            rpc_url=rpc_url,
            dry_run=args.dry_run,
        )
    except RpcError as exc:
        print(f"error: RPC failed: {exc.message}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        "EXP-003 RPC backfill complete\n"
        f"  creates_total={summary['creates_total_n']} "
        f"sampled={summary['creates_sampled_n']}\n"
        f"  marks_written={summary['marks_written_n']} "
        f"creates_with_marks={summary['creates_with_marks_n']}\n"
        f"  window_s={summary['window_s']} commitment={summary['commitment']}\n"
        f"  rpc_url={summary['rpc_url']}",
        file=sys.stdout,
    )
    if summary["output_files"]:
        print("  output_files:", file=sys.stdout)
        for path, count in sorted(summary["output_files"].items()):
            print(f"    {path} (+{count} lines)", file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
