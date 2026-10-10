#!/usr/bin/env python3
"""Outcome-blind P7 line-1 check on the tip follower's event-V stamp (docs/runbooks/tip-follower-event-v.md, step 7, last item).

    python -m tools.tip_event_v_p7 --end <UTC> --out-dir <new dir> [--start <UTC>] [--rps 5] [--dry-run]

Question: on a stride sample of stamped canonical-pool PumpSwap prints written by `tools/fast_tip_follower.py --trade-event-v`, does the
integer constant-product law hold within 1 bp (or 2 units) on at least 99% of the comparable sells and 99% of the comparable buys? That is
EXP-025 Amendment 1, part 2 ("P7 line 1, amended"), run on tip rows instead of a look's adapter column.

What it does, in order (every decision goes through ARTIFACTS/exp025/event_v_map.py; that file is sha-pinned and is imported, not edited or copied):
  1. Frame. Reads trades-<hour>.jsonl in --trades-dir for [--start, --end) by the row's t_recv_ms (the follower buckets files by it). Keeps
     PumpSwap rows whose `pool` is the canonical pool of the row's `mint` (tools.exp025_adapter.canonical_pool, stdlib only) and whose
     `virtual_quote_reserves` is an int (the stamp). Canonical PumpSwap rows with no stamp are counted, not framed.
  2. Key. The natural order is (slot, signature, event_index). It is NOT (slot, tx_index, event_index): the follower writes `tx_index` only on
     rows that came straight out of rows_from_block; a row that came back through resolve_unresolved (a pool the follower had not seen yet)
     has none (tools/pump_history_backfill.py backfill_trade_row; tools/test_tip_event_v_p7.py shows it on the follower's own fixtures). The
     event_map helpers key their top-up on row["tx_index"], so each frame row carries its signature in that field: the helpers' key is then
     (slot, signature, event_index) for every row, and unique. The real tx_index, when the row has one, is not used.
  3. Draw. p7_raw_main_draw(frame, 1000) then p7_raw_buy_topup(frame, main). Both are done before the first fetch, from the tape rows alone.
  4. Fetch. One getTransaction per distinct signature of a comparable sampled print, up to P7_RAW_TX_ATTEMPTS attempts, encoding json,
     commitment confirmed, maxSupportedTransactionVersion 1 (the walker's getBlock settings, tools/pump_history_backfill.py _getblock_params;
     Amendment 1, "Fetch"). One credit is counted per call made (a retry is a call).
  5. Decode and judge. observe.trade_decode.records_from_logs(..., event_v=True), keyed by event_index = the position in the transaction's decoded
     trade list, which is how the follower and the walker assign it. The decision is p7_raw_check(tape_row, raw, adapter_row, v0=0):
     there is no look adapter here, so adapter_row is built from the tape row with quote_reserve = vault + V (the print's own pre-trade
     event V), and v0 = 0. That is the EXP-025 identity q_mapped + V0 = vault + V(t), and tools/c1nf_shadow.py's "Q = vault quote + this print's V".
     Reasons: fetch_failed, no_record, slot_mismatch, identity_mismatch, field_missing. no_adapter_row cannot occur. Unresolved prints are misses
     and stay in the denominator.

Counts only. stdout shows no signature, pool, mint, amount, price or reserve. Per-print outcomes (signature, line, outcome, reason) go to
<out-dir>/p7-tip-prints.jsonl and the summary to <out-dir>/p7-tip.json, which carries that file's sha256. The RPC URL and key are held in
memory and never printed, logged or written; errors are reduced to a type or a status code.

Refuses (exit 2) unless observe/trade_decode.py is git blob 238942a6b3c5425389eddfde4d11268c300acbec (job #433's decoder) and ARTIFACTS/exp025/
event_v_map.py matches ARTIFACTS/exp025/SHA256SUMS. Run it from a full checkout: the follower's own source tree holds only tools/ and observe/.
Exit 0 means the check ran to the end; the verdict is `pass` in the JSON. No file under /var/lib/mal is written. Paper only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from observe.trade_decode import records_from_logs  # noqa: E402
from tools.fast_tip_follower import git_blob_sha  # noqa: E402
from tools.funding_graph import helius_key_from_file  # noqa: E402
from tools.pump_history_backfill import RateLimiter, helius_http_url  # noqa: E402

DEFAULT_TRADES_DIR = "/var/lib/mal/sealed/fast-trades-tip"
DEFAULT_HELIUS_ENV = "/var/lib/mal/fast-listener/helius.env"
DEFAULT_START = "2026-10-10T13:17:18Z"  # the follower restarted with --trade-event-v at 13:17:17Z
DEFAULT_RPS = 5.0
# The frame is held in memory at about 923 B per row (the reviewer's measurement on #570). 1,200,000 rows is about 1.1 GB, under the 1.2 GB
# budget; 2,000,000 would be about 1.8 GB, too close on mal-fast-0 (one heavy job at a time, user-1002.slice).
DEFAULT_MAX_FRAME_ROWS = 1_200_000
DECODER_BLOB = "238942a6b3c5425389eddfde4d11268c300acbec"  # observe/trade_decode.py at job #433 (DEC-016:397); tests tie it to the follower's pin
# Amendment 1, "Fetch": encoding json, commitment confirmed, maxSupportedTransactionVersion 1 = the walker's getBlock settings
# (tools/pump_history_backfill.py _getblock_params). tools/test_tip_event_v_p7.py asserts these equal the walker's.
TX_CONFIG = {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 1}
FEED = "gettx"
BACKOFF_S = (1.0, 2.0)  # sleep before attempt 2 and 3
Q_V0 = 0  # q_mapped + V0 = vault + V(t): the adapter column is replaced by vault + V, so V0 is 0
OUT_SUMMARY = "p7-tip.json"
OUT_PRINTS = "p7-tip-prints.jsonl"
KEY_NAME = "(slot, signature, event_index)"
# the frame row keeps only what the helpers and the identity test read
_FRAME_FIELDS = ("slot", "signature", "event_index", "pool", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve",
                 "virtual_quote_reserves", "ix_name", "zero_sol")
_HOUR_MS = 3_600_000


class Refused(Exception):
    """A precondition failed. Nothing was fetched or written. main() exits 2."""


class FetchError(Exception):
    """One getTransaction call failed. `kind` is a status code or a type name, never a URL."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


# ---- pinned inputs ---------------------------------------------------------------------------------------------------------------------
_EV: Any = None


def event_v_map() -> Any:
    """ARTIFACTS/exp025/event_v_map.py, loaded by path, after its sha256 is checked against ARTIFACTS/exp025/SHA256SUMS."""
    global _EV
    if _EV is not None:
        return _EV
    art = REPO / "ARTIFACTS" / "exp025"
    path, sums = art / "event_v_map.py", art / "SHA256SUMS"
    if not path.is_file() or not sums.is_file():
        raise Refused("ARTIFACTS/exp025/event_v_map.py or SHA256SUMS not found: run from a full checkout, not the follower's source tree")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    pinned = None
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "event_v_map.py":
            pinned = parts[0]
    if pinned != digest:
        raise Refused("ARTIFACTS/exp025/event_v_map.py does not match its SHA256SUMS entry")
    spec = importlib.util.spec_from_file_location("exp025_event_v_map", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SHA256 = digest
    _EV = mod
    return mod


def decoder_blob() -> str | None:
    """Git blob sha of the observe/trade_decode.py this process imported."""
    import observe.trade_decode as mod

    try:
        return git_blob_sha(Path(mod.__file__).read_bytes())
    except (OSError, TypeError):
        return None


def default_canonical() -> Callable[[str], str]:
    """pool = PDA(["pool", 0u16, PDA(["pool-authority", mint], pump), mint, WSOL], PumpSwap): stdlib only, no solders."""
    from tools.exp025_adapter import canonical_pool

    return canonical_pool


# ---- window and files ------------------------------------------------------------------------------------------------------------------
def parse_utc(text: str) -> int:
    """'YYYY-MM-DDTHH:MM:SSZ' -> ms since the epoch."""
    try:
        return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()) * 1000
    except ValueError:
        raise Refused(f"time must look like 2026-10-10T13:17:18Z, not {text!r}") from None


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours_in(start_ms: int, end_ms: int) -> list[str]:
    """The UTC hour stamps of the follower's trade files that can hold a t_recv_ms in [start_ms, end_ms)."""
    out: list[str] = []
    t = start_ms - start_ms % _HOUR_MS
    while t < end_ms:
        out.append(datetime.fromtimestamp(t // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H"))
        t += _HOUR_MS
    return out


# ---- frame -----------------------------------------------------------------------------------------------------------------------------
def build_frame(trades_dir: Path, start_ms: int, end_ms: int, canonical: Callable[[str], str] | None = None,
                max_rows: int | None = None) -> tuple[list[dict], dict]:
    """The P7 sample frame from the tip follower's trade files, and the counts that describe it.

    Frame: PumpSwap rows with t_recv_ms in [start_ms, end_ms), `pool` == canonical(`mint`), and an int `virtual_quote_reserves`, one per
    (slot, signature, event_index), sorted by that key. Each frame row is a slim dict of the fields the check reads; its `tx_index` is the
    signature (see the module doc). Canonical PumpSwap rows without an int stamp are counted in `unstamped_canonical_n`.

    max_rows: the whole frame is held in memory (about 0.9 KB per row). The moment a row would take the frame past max_rows, while reading and
    before the sort, this raises Refused. None means no ceiling."""
    canonical = canonical or default_canonical()
    canon_of: dict[str, str | None] = {}
    interned: dict[str, str] = {}
    stats: Counter = Counter()
    missing: list[str] = []
    frame: list[dict] = []
    for hour in hours_in(start_ms, end_ms):
        path = trades_dir / f"trades-{hour}.jsonl"
        if not path.is_file():
            missing.append(hour)
            continue
        stats["files_read"] += 1
        with open(path, "rb") as fh:
            for raw in fh:
                stats["lines"] += 1
                if b"pumpswap" not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    stats["bad_lines"] += 1  # e.g. the partial last line of the hour the follower is still writing
                    continue
                if not isinstance(row, dict) or row.get("venue") != "pumpswap":
                    continue
                t = row.get("t_recv_ms")
                if not _is_int(t):
                    stats["no_t_recv_ms"] += 1
                    continue
                if not (start_ms <= t < end_ms):
                    continue
                stats["pumpswap_in_window"] += 1
                mint, pool = row.get("mint"), row.get("pool")
                if not isinstance(mint, str) or not isinstance(pool, str) or not mint or not pool:
                    stats["no_mint_or_pool"] += 1
                    continue
                if mint not in canon_of:
                    try:
                        canon_of[mint] = canonical(mint)
                    except Exception:  # noqa: BLE001 - a mint that is not a pubkey has no canonical pool
                        canon_of[mint] = None
                if canon_of[mint] != pool:
                    stats["non_canonical"] += 1
                    continue
                stats["canonical_pumpswap_n"] += 1
                if not _is_int(row.get("virtual_quote_reserves")):
                    stats["unstamped_canonical_n"] += 1
                    continue
                if not (_is_int(row.get("slot")) and _is_int(row.get("event_index")) and isinstance(row.get("signature"), str)):
                    stats["bad_key"] += 1
                    continue
                slim = {k: row[k] for k in _FRAME_FIELDS if k in row}
                slim["pool"] = interned.setdefault(pool, pool)
                if isinstance(slim.get("ix_name"), str):
                    slim["ix_name"] = interned.setdefault(slim["ix_name"], slim["ix_name"])
                if _is_int(row.get("tx_index")):
                    stats["frame_with_tx_index_n"] += 1
                slim["tx_index"] = slim["signature"]
                frame.append(slim)
                if max_rows is not None and len(frame) > max_rows:
                    raise Refused(f"the frame passed {max_rows} rows while reading (about 0.9 KB per row in memory): "
                                  "narrow --start/--end, or raise --max-frame-rows if the host has the memory")
    frame.sort(key=lambda r: (r["slot"], r["signature"], r["event_index"]))  # stable: the first of two rows with one key stays first
    unique: list[dict] = []
    last = None
    for r in frame:
        k = (r["slot"], r["signature"], r["event_index"])
        if k == last:
            stats["duplicate_keys_dropped"] += 1
            continue
        unique.append(r)
        last = k
    info = {
        "files_read": stats["files_read"], "hours_missing": missing, "lines": stats["lines"], "bad_lines": stats["bad_lines"],
        "no_t_recv_ms": stats["no_t_recv_ms"], "pumpswap_in_window": stats["pumpswap_in_window"],
        "no_mint_or_pool": stats["no_mint_or_pool"], "non_canonical": stats["non_canonical"],
        "canonical_pumpswap_n": stats["canonical_pumpswap_n"], "unstamped_canonical_n": stats["unstamped_canonical_n"],
        "bad_key": stats["bad_key"], "duplicate_keys_dropped": stats["duplicate_keys_dropped"],
        "frame_n": len(unique), "frame_with_tx_index_n": stats["frame_with_tx_index_n"],  # counted before duplicate removal
    }
    return unique, info


def draw(frame: list[dict]) -> tuple[list[dict], list[dict]]:
    """(main, topup), both from the frame alone."""
    ev = event_v_map()
    main = ev.p7_raw_main_draw(frame, ev.P7_SAMPLE)
    return main, ev.p7_raw_buy_topup(frame, main)


# ---- fetch -----------------------------------------------------------------------------------------------------------------------------
def make_call(url: str, limiter: RateLimiter, timeout: float = 30.0) -> Callable[[str], Any]:
    """One getTransaction HTTP call. Returns the result (None when the node returns null) or raises FetchError(kind).
    The URL stays in this closure; nothing it raises or returns carries it."""

    def call(sig: str) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [sig, dict(TX_CONFIG)]}).encode()
        limiter.acquire()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise FetchError(f"http_{exc.code}") from None
        except Exception as exc:  # noqa: BLE001 - URLError and friends can carry the URL in their text; keep the type only
            raise FetchError(f"transport_{type(exc).__name__}") from None
        try:
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            raise FetchError("bad_payload") from None
        if not isinstance(payload, dict):
            raise FetchError("bad_payload")
        err = payload.get("error")
        if err:
            code = err.get("code") if isinstance(err, dict) else None
            raise FetchError(f"rpc_{code}" if _is_int(code) else "rpc_error")
        return payload.get("result")

    return call


class Fetcher:
    """getTransaction with up to P7_RAW_TX_ATTEMPTS attempts per signature. `calls` is the credit count: 1 per call made, retries included."""

    def __init__(self, call: Callable[[str], Any], attempts: int, sleep: Callable[[float], None] = time.sleep) -> None:
        self.call, self.attempts, self.sleep = call, int(attempts), sleep
        self.calls = 0
        self.errors: Counter = Counter()

    def __call__(self, sig: str) -> dict | None:
        """The transaction, or None when every attempt failed or the node had no such transaction (fetch_failed)."""
        for i in range(self.attempts):
            self.calls += 1
            try:
                res = self.call(sig)
            except FetchError as exc:
                self.errors[exc.kind] += 1
            else:
                if isinstance(res, dict):
                    return res
                self.errors["null_result"] += 1
            if i + 1 < self.attempts:
                self.sleep(BACKOFF_S[min(i, len(BACKOFF_S) - 1)])
        return None


# ---- decode and judge ------------------------------------------------------------------------------------------------------------------
def decode_tx(tx: Mapping[str, Any], sig: str) -> dict[int, dict]:
    """event_index -> record of records_from_logs(event_v=True) for one transaction, as the follower decodes it. A failed transaction
    (meta.err) has no records, as in the follower. Raises on a decoder error; the caller counts it as no record."""
    meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}
    logs = meta.get("logMessages")
    if meta.get("err") is not None or not isinstance(logs, list):
        return {}
    slot = tx.get("slot")
    recs = records_from_logs(
        logs, slot=slot if _is_int(slot) else -1, signature=sig, t_recv_ms=0, commitment="confirmed", feed=FEED,
        pool_mints={}, event_v=True,
    )
    return {r["event_index"]: r for r in recs}


def adapter_row_from_tape(tape: Mapping[str, Any]) -> dict:
    """The row P7 prices on, with no look adapter: quote_reserve := vault + the print's own V (None if either is not an int)."""
    q, v = tape.get("quote_reserve"), tape.get("virtual_quote_reserves")
    return {
        "slot": tape.get("slot"), "tx_index": tape.get("tx_index"), "event_index": tape.get("event_index"), "pool": tape.get("pool"),
        "quote_reserve": q + v if _is_int(q) and _is_int(v) else None,
        "base_reserve": tape.get("base_reserve"), "token_raw": tape.get("token_raw"),
    }


def judge(tape: Mapping[str, Any], raw: Mapping[str, Any] | None, *, fetch_failed: bool = False) -> tuple:
    """(line, outcome, reason) of one sampled print: event_v_map.p7_raw_check with the adapter row built from the tape row and V0 = 0."""
    return event_v_map().p7_raw_check(dict(tape), None if raw is None else dict(raw), adapter_row_from_tape(tape), Q_V0, fetch_failed=fetch_failed)


def run_check(sampled: Sequence[tuple[str, dict]], fetch: Callable[[str], dict | None], progress: Callable[[int, int], None] | None = None
              ) -> tuple[list[tuple], dict]:
    """sampled: (set name, frame row) in draw order. Fetches each distinct signature of a comparable print once, then judges every print.
    Returns (results, extras): results[i] = (line, outcome, reason) for sampled[i]; extras counts transactions and decoder errors."""
    ev = event_v_map()
    sigs: list[str] = []
    seen: set[str] = set()
    for _, row in sampled:
        if ev.p7_raw_line(row) is not None and row["signature"] not in seen:
            seen.add(row["signature"])
            sigs.append(row["signature"])
    decoded: dict[str, dict[int, dict] | None] = {}
    decode_errors = 0
    for i, sig in enumerate(sigs):
        tx = fetch(sig)
        if tx is None:
            decoded[sig] = None
        else:
            try:
                decoded[sig] = decode_tx(tx, sig)
            except Exception:  # noqa: BLE001 - a decoder error leaves the print without a record
                decoded[sig] = {}
                decode_errors += 1
        if progress is not None and ((i + 1) % 100 == 0 or i + 1 == len(sigs)):
            progress(i + 1, len(sigs))
    results = []
    for _, row in sampled:
        if ev.p7_raw_line(row) is None:
            results.append(judge(row, None))  # excluded: decided from the tape row, no fetch
            continue
        recs = decoded[row["signature"]]
        if recs is None:
            results.append(judge(row, None, fetch_failed=True))
        else:
            results.append(judge(row, recs.get(row["event_index"])))
    return results, {"tx_n": len(sigs), "decode_errors": decode_errors}


# ---- output ----------------------------------------------------------------------------------------------------------------------------
def _share(ok: int, n: int) -> float | None:
    return ok / n if n else None


def summarize(tally: Mapping[str, Any], *, window: tuple[int, int], frame_info: Mapping[str, Any], main_n: int, topup_n: int,
              extras: Mapping[str, Any], credits: int, errors: Mapping[str, int], prints_sha256: str | None, blob: str | None) -> dict:
    ev = event_v_map()
    unresolved = {r: n for r, n in tally["unresolved"].items() if r != "no_adapter_row"}
    assert tally["unresolved"]["no_adapter_row"] == 0  # the adapter row is built from the tape row, so it always exists
    return {
        "tool": "tools/tip_event_v_p7.py",
        "check": "EXP-025 Amendment 1 P7 line 1 on tip follower rows (outcome-blind, counts only)",
        "window": {"start": iso_utc(window[0]), "end": iso_utc(window[1]), "field": "t_recv_ms"},
        "key": KEY_NAME,
        "q_identity": "Q = quote_reserve + virtual_quote_reserves of the print itself, V0 = 0",
        "decoder_blob": blob, "decoder_blob_pinned": blob == DECODER_BLOB,
        "event_v_map_sha256": ev.SHA256,
        "getTransaction": dict(TX_CONFIG, attempts=ev.P7_RAW_TX_ATTEMPTS),
        "frame_n": frame_info["frame_n"],
        "unstamped_canonical_n": frame_info["unstamped_canonical_n"],
        "sample_n": main_n,
        "topup_n": topup_n,
        "tx_n": extras["tx_n"],
        "comparable": {"sell_n": tally["sell_n"], "buy_n": tally["buy_n"]},
        "excluded_n": tally["excluded"],
        "excluded_by": dict(tally["excluded_by"]),
        "hits": {"sell": tally["sell_ok"], "buy": tally["buy_ok"]},
        "shares": {"sell": _share(tally["sell_ok"], tally["sell_n"]), "buy": _share(tally["buy_ok"], tally["buy_n"])},
        "bars": {"sell": ev.P7_CP_SELL_MIN, "buy": ev.P7_CP_BUY_MIN},
        "unresolved": unresolved,
        "unresolved_n": sum(unresolved.values()),
        "decode_errors": extras["decode_errors"],
        "credits": credits,
        "fetch_errors": dict(errors),
        "pass": bool(ev.p7_raw_pass(dict(tally))),
        "prints_file": OUT_PRINTS,
        "prints_sha256": prints_sha256,
        "frame": dict(frame_info),
    }


def print_rows(sampled: Sequence[tuple[str, dict]], results: Sequence[tuple]) -> bytes:
    lines = []
    for (name, row), (line, outcome, reason) in zip(sampled, results):
        lines.append(json.dumps({"set": name, "slot": row["slot"], "signature": row["signature"], "event_index": row["event_index"],
                                 "line": line, "outcome": outcome, "reason": reason}, sort_keys=True, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode() if lines else b""


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ---- driver ----------------------------------------------------------------------------------------------------------------------------
def _plan(sampled: Sequence[tuple[str, dict]]) -> dict:
    """What the draw alone decides: the comparable populations, the exclusions per cause, the transactions to fetch."""
    ev = event_v_map()
    lines = Counter(ev.p7_raw_line(r) for _, r in sampled)
    causes = Counter(ev.p7_raw_exclusion(r) for _, r in sampled if ev.p7_raw_line(r) is None)
    sigs = {r["signature"] for _, r in sampled if ev.p7_raw_line(r) is not None}
    return {"comparable": {"sell_n": lines["sell"], "buy_n": lines["buy"]}, "excluded_n": lines[None],
            "excluded_by": {c: causes[c] for c in ev.P7_RAW_EXCLUSIONS}, "tx_n": len(sigs), "max_credits": len(sigs) * ev.P7_RAW_TX_ATTEMPTS}


def run(args: argparse.Namespace, *, call: Callable[[str], Any] | None = None, canonical: Callable[[str], str] | None = None,
        sleep: Callable[[float], None] = time.sleep, out=None) -> int:
    out = out or sys.stdout
    ev = event_v_map()
    blob = decoder_blob()
    if blob != DECODER_BLOB:
        raise Refused("observe/trade_decode.py is not job #433's decoder blob; a new pin needs a dated outcome-blind amendment")
    start_ms, end_ms = parse_utc(args.start), parse_utc(args.end)
    if end_ms <= start_ms:
        raise Refused("--end must be after --start")
    if not Path(args.trades_dir).is_dir():
        raise Refused("--trades-dir is not a directory")
    out_dir = Path(args.out_dir) if args.out_dir else None
    url = None
    if not args.dry_run:
        if out_dir is None:
            raise Refused("--out-dir is required unless --dry-run")
        if (out_dir / OUT_SUMMARY).exists() or (out_dir / OUT_PRINTS).exists():
            raise Refused("output files already exist in --out-dir: the sample is drawn once; use a new directory")
        if call is None:
            key = helius_key_from_file(Path(args.helius_env))
            if not key:
                raise Refused("no usable HELIUS_API_KEY in --helius-env")
            url = helius_http_url(key)

    frame, info = build_frame(Path(args.trades_dir), start_ms, end_ms, canonical, args.max_frame_rows)
    main_rows, topup_rows = draw(frame)  # both before any fetch
    sampled = [("main", r) for r in main_rows] + [("topup", r) for r in topup_rows]
    plan = _plan(sampled)
    head = {"frame_n": info["frame_n"], "unstamped_canonical_n": info["unstamped_canonical_n"], "sample_n": len(main_rows),
            "topup_n": len(topup_rows)}
    if args.dry_run:
        report = {"dry_run": True, "window": {"start": iso_utc(start_ms), "end": iso_utc(end_ms)}, "key": KEY_NAME, **head, **plan,
                  "decoder_blob": blob, "frame": info}
        out.write(json.dumps(report, indent=1, sort_keys=True) + "\n")
        return 0

    if call is None:
        call = make_call(url, RateLimiter(float(args.rps)))
    fetch = Fetcher(call, ev.P7_RAW_TX_ATTEMPTS, sleep)

    def progress(done: int, total: int) -> None:
        print(f"tip_event_v_p7: fetched {done}/{total} transactions, credits={fetch.calls}", file=sys.stderr, flush=True)

    results, extras = run_check(sampled, fetch, progress)
    tally = ev.p7_raw_tally(results)
    out_dir.mkdir(parents=True, exist_ok=True)
    prints = print_rows(sampled, results)
    _write_atomic(out_dir / OUT_PRINTS, prints)
    summary = summarize(tally, window=(start_ms, end_ms), frame_info=info, main_n=len(main_rows), topup_n=len(topup_rows), extras=extras,
                        credits=fetch.calls, errors=fetch.errors, prints_sha256=hashlib.sha256(prints).hexdigest(), blob=blob)
    text = json.dumps(summary, indent=1, sort_keys=True) + "\n"
    _write_atomic(out_dir / OUT_SUMMARY, text.encode())
    out.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Outcome-blind P7 line-1 check on the tip follower's event-V stamp (counts only).")
    ap.add_argument("--trades-dir", default=DEFAULT_TRADES_DIR, help="the follower's --out directory (trades-<hour>.jsonl)")
    ap.add_argument("--start", default=DEFAULT_START, help="window start, UTC, inclusive (t_recv_ms)")
    ap.add_argument("--end", required=True, help="window end, UTC, exclusive (t_recv_ms)")
    ap.add_argument("--out-dir", default=None, help="new directory for p7-tip.json and p7-tip-prints.jsonl (required unless --dry-run)")
    ap.add_argument("--helius-env", default=DEFAULT_HELIUS_ENV, help="env file with HELIUS_API_KEY; the key and URL are never printed")
    ap.add_argument("--rps", type=float, default=DEFAULT_RPS, help="getTransaction calls per second")
    ap.add_argument("--max-frame-rows", type=int, default=DEFAULT_MAX_FRAME_ROWS,
                    help="refuse (exit 2) while reading once the frame passes this many rows; ~0.9 KB each, so the default is ~1.1 GB")
    ap.add_argument("--dry-run", action="store_true", help="build the frame and the draw, print the counts, make no RPC call and write nothing")
    return ap


def main(argv: Sequence[str] | None = None, *, _call: Callable[[str], Any] | None = None, _canonical: Callable[[str], str] | None = None,
         _sleep: Callable[[float], None] = time.sleep) -> int:
    args = build_parser().parse_args(argv)
    if args.rps <= 0:
        print("tip_event_v_p7: --rps must be > 0", file=sys.stderr)
        return 2
    try:
        return run(args, call=_call, canonical=_canonical, sleep=_sleep)
    except Refused as exc:
        print(f"tip_event_v_p7: refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - never let a message that might carry the RPC URL reach the log
        print(f"tip_event_v_p7: failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
