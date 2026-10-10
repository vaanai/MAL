#!/usr/bin/env python3
"""Outcome-blind P7 line-1 check on the tip follower's event-V stamp (docs/runbooks/tip-follower-event-v.md, step 7, last item), under the
AMENDED buy rule of the quant-proof ruling of 2026-10-10 (/data/mal/hunt-1008/c1nf-verify/QP-P7-1010.md, sha256 bf298d8a..., items 1 and 2).

    python -m tools.tip_event_v_p7 --start <H> --end <H+1h> --out-dir <new dir> [--rps 5] [--dry-run]

Question: on a stride sample of stamped canonical-pool PumpSwap prints written by `tools/fast_tip_follower.py --trade-event-v`, does the
integer constant-product law hold within tolerance (1 bp of actual OR 2 units, lamports on both sides) on at least 99% of the comparable sells
and 99% of the comparable buys? That is EXP-025 Amendment 1, part 2 ("P7 line 1"), with its buy side amended (ruling item 2), run on tip rows
instead of a look's adapter column.

Amended acceptance (ruling item 1, one run). Before H, #570 and the amendment module are pushed and the window [H, H+1h) is declared in the PR
and a notebook entry; H is the first full UTC hour at least 10 minutes after the push. The run on that window passes only if
  - sells >= 99% and buys >= 99% under within_tolerance (1 bp or 2 units);
  - there are at least 100 comparable buys (this tool's `pass` includes it: `acceptance.buy_n_at_least_100`), and the window is one full
    UTC hour (`acceptance.one_full_utc_hour`; that it was declared before H is checked in the PR, not here);
  - over the window the follower's mismatch, errors and missing_pumpswap counters are all 0, with no new backlog_jump or unfetchable rows
    (the follower's counters, read beside this output; this tool does not see them);
  - the output shows n and hits per ix_name, and exclusions per cause and per name (`by_ix_name`, `excluded_by`, `excluded_by_name`).
If it fails, roll back (runbook); one re-run on the next declared hour only if the failure is from fetch_failed alone. No window shopping.
Re-scoring #576 under the amended rule is a diagnostic only, never the acceptance.

Amended buy side (ARTIFACTS/exp025/p7_buy_amend.py; event_v_map.py is unchanged and still pinned):
  - Comparable buys: no `zero_sol` and `ix_name` exactly `buy` or `buy_v2`. Every other buy leaves both denominators with cause
    `buy_exact_quote_in` (prefix, v1 and v2), `no_ix_name` (missing, null, empty) or `ix_not_listed` (any other name, `multi_hop_swap`
    included), decided from the sampled tape row before any fetch. Sells are unchanged and need no name.
  - Law: the raw event's `pool_quote_amount` within tolerance of ceil(Q * token_raw / (base_reserve - token_raw)), in integers
    -((-Q * token_raw) // (base_reserve - token_raw)); base_reserve <= token_raw is a miss. It REPLACES the forward law for buys.
  - Q for this tip check stays vault + the print's own virtual_quote_reserves, V0 = 0 (see step 5).

What it does, in order (every line-1 decision goes through ARTIFACTS/exp025/p7_buy_amend.py, which imports the unchanged parts of
ARTIFACTS/exp025/event_v_map.py; both files are sha-pinned in ARTIFACTS/exp025/SHA256SUMS and loaded, not edited or copied):
  1. Frame. Reads trades-<hour>.jsonl in --trades-dir for [--start, --end) by the row's t_recv_ms (the follower buckets files by it). Keeps
     PumpSwap rows whose `pool` is the canonical pool of the row's `mint` (tools.exp025_adapter.canonical_pool, stdlib only) and whose
     `virtual_quote_reserves` is an int (the stamp). Canonical PumpSwap rows with no stamp are counted, not framed.
  2. Key. The natural order is (slot, signature, event_index). It is NOT (slot, tx_index, event_index): the follower writes `tx_index` only on
     rows that came straight out of rows_from_block; a row that came back through resolve_unresolved (a pool the follower had not seen yet)
     has none (tools/pump_history_backfill.py backfill_trade_row; tools/test_tip_event_v_p7.py shows it on the follower's own fixtures). The
     event_map helpers key their top-up on row["tx_index"], so each frame row carries its signature in that field: the helpers' key is then
     (slot, signature, event_index) for every row, and unique. The real tx_index, when the row has one, is not used.
  3. Draw. p7_raw_main_draw(frame, 1000) then the amended p7_raw_buy_topup(frame, main) (candidates are whitelisted buys only). Both are done
     before the first fetch, from the tape rows alone.
  4. Fetch. One getTransaction per distinct signature of a comparable sampled print, up to P7_RAW_TX_ATTEMPTS attempts, encoding json,
     commitment confirmed, maxSupportedTransactionVersion 1 (the walker's getBlock settings, tools/pump_history_backfill.py _getblock_params;
     Amendment 1, "Fetch"). One credit is counted per call made (a retry is a call).
  5. Decode and judge. observe.trade_decode.records_from_logs(..., event_v=True), keyed by event_index = the position in the transaction's decoded
     trade list, which is how the follower and the walker assign it. The decision is the amended p7_raw_check(tape_row, raw, adapter_row, v0=0):
     there is no look adapter here, so adapter_row is built from the tape row with quote_reserve = vault + V (the print's own pre-trade
     event V), and v0 = 0. That is the EXP-025 identity q_mapped + V0 = vault + V(t), and tools/c1nf_shadow.py's "Q = vault quote + this print's V".
     It tests vault + V(t) against vault alone, not against vault + V0; only the reads' adapter-column check covers that convention.
     Reasons: fetch_failed, no_record, slot_mismatch, identity_mismatch, field_missing. no_adapter_row cannot occur. Unresolved prints are misses
     and stay in the denominator.
  6. Line 2 (fee tier), reported, NOT scored here. On the same 1,000-print main draw (no top-up, no fetch: the tape row's sol_lamports,
     token_raw, quote_reserve, base_reserve and stamp), outcome-blind, per side and per ix_name:
       line2_exp025: EXP-025 section 10 P7 line 2 with EXP-025's helpers: Q = (vault + V) + V0 with V0 = 0, the tier of the pinned pass A
                     (ARTIFACTS/exp025/scripts/common2.py fee_frac, sha-checked), sells' sol within event_v_map.within_bp(P7_TOLERANCE_BP) of
                     Q tok / (b + tok) (1 - tier), buys' implied fee 1 - net / sol (net = tok Q / (b - tok)) within P7_TOLERANCE_BP of the tier,
                     bars P7_SELL_MIN / P7_BUY_MIN and event_v_map.p7_pass.
       line2_exp024: tools.boostfloor_inputs.tier_lines as written, V = the print's own stamp.
     A zero buy (sol <= 0, tok <= 0 or tok >= b) is skipped, as tier_lines does. Line 2 does not change `pass`: acceptance is line 1.
     Line 2 runs after the prints file is written; a rule whose helper raises is reported as {"error": <exception type>, "scored": false}.

Counts only. stdout shows no signature, pool, mint, amount, price or reserve. Per-print outcomes (signature, ix_name, line, outcome, reason) go to
<out-dir>/p7-tip-prints.jsonl and the summary to <out-dir>/p7-tip.json, which carries that file's sha256. The RPC URL and key are held in
memory and never printed, logged or written; errors are reduced to a type or a status code.

Refuses (exit 2) when the window is not closed (no row with t_recv_ms >= --end on disk: the follower writes it non-decreasing, so one later row
proves every earlier row is written; pick an --end a few minutes in the past) and when the frame passes --max-frame-rows (default 1,200,000, about
1.1 GB at ~923 B per row; checked while reading, before the sort). Also refuses unless observe/trade_decode.py is git blob 238942a6b3c5425389eddfde4d11268c300acbec (job #433's decoder), and
ARTIFACTS/exp025/event_v_map.py, ARTIFACTS/exp025/p7_buy_amend.py and ARTIFACTS/exp025/scripts/common2.py each match ARTIFACTS/exp025/SHA256SUMS,
and the line-2 helpers import (numpy; run with /data/mal/venv/bin/python). Run it from a full checkout: the follower's own source tree holds only
tools/ and observe/. Exit 0 means the check ran to the end; the verdict is `pass` in the JSON. No file under /var/lib/mal is written. Paper only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
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
_T_RECV_RE = re.compile(rb'"t_recv_ms"\s*:\s*(\d+)')


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
_AMEND: Any = None
_FEE_FRAC: Any = None
BUY_AMEND_FILE = "p7_buy_amend.py"  # ARTIFACTS/exp025/p7_buy_amend.py: the amended buy side (quant-proof ruling 2026-10-10, item 2)
TIER_FILE = "scripts/common2.py"    # ARTIFACTS/exp025/scripts/common2.py: the pinned pass A tier (fee_frac), line 2 only


def _pinned_module(rel: str, name: str) -> tuple[Any, str]:
    """ARTIFACTS/exp025/<rel>, loaded by path after its sha256 is checked against its line in ARTIFACTS/exp025/SHA256SUMS. Refused otherwise."""
    art = REPO / "ARTIFACTS" / "exp025"
    path, sums = art / rel, art / "SHA256SUMS"
    if not path.is_file() or not sums.is_file():
        raise Refused(f"ARTIFACTS/exp025/{rel} or SHA256SUMS not found: run from a full checkout, not the follower's source tree")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    pinned = None
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == rel:
            pinned = parts[0]
    if pinned != digest:
        raise Refused(f"ARTIFACTS/exp025/{rel} does not match its SHA256SUMS entry")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:  # p7_buy_amend.py refuses an event_v_map.py that is not its pinned digest with ImportError
        raise Refused(f"ARTIFACTS/exp025/{rel} did not load: {type(exc).__name__}") from None
    return mod, digest


def event_v_map() -> Any:
    """ARTIFACTS/exp025/event_v_map.py (unchanged constants, sell law, fee-line helpers), sha-checked against ARTIFACTS/exp025/SHA256SUMS."""
    global _EV
    if _EV is None:
        mod, digest = _pinned_module("event_v_map.py", "exp025_event_v_map")
        mod.SHA256 = digest
        _EV = mod
    return _EV


def buy_amend() -> Any:
    """ARTIFACTS/exp025/p7_buy_amend.py (the amended line-1 rule: exclusion, line, check, tally, top-up), sha-checked against
    ARTIFACTS/exp025/SHA256SUMS. It loads event_v_map.py itself against its own pinned digest; the two must name the same file."""
    global _AMEND
    if _AMEND is None:
        ev = event_v_map()
        mod, digest = _pinned_module(BUY_AMEND_FILE, "exp025_p7_buy_amend")
        if mod.EVENT_V_MAP_SHA256 != ev.SHA256:
            raise Refused("ARTIFACTS/exp025/p7_buy_amend.py pins a different event_v_map.py than SHA256SUMS")
        mod.SHA256 = digest
        _AMEND = mod
    return _AMEND


def exp025_tier() -> Callable[[int, int], float]:
    """(Q including V, base_reserve) -> fee fraction: ARTIFACTS/exp025/scripts/common2.py fee_frac (the pinned pass A tier), sha-checked."""
    global _FEE_FRAC
    if _FEE_FRAC is None:
        mod, _ = _pinned_module(TIER_FILE, "exp025_common2_for_tip_p7")  # needs numpy; a missing module is refused
        _FEE_FRAC = lambda q, b: float(mod.fee_frac(q, b))  # noqa: E731
    return _FEE_FRAC


def exp024_tier_lines() -> Callable[..., dict]:
    """tools.boostfloor_inputs.tier_lines (EXP-024 section 10 P7 tier lines), imported lazily: it needs numpy."""
    try:
        from tools.boostfloor_inputs import tier_lines
    except ImportError as exc:
        raise Refused(f"tools.boostfloor_inputs did not import ({type(exc).__name__}): run with /data/mal/venv/bin/python") from None
    return tier_lines


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
    # The follower writes t_recv_ms non-decreasing, so one row at or after end_ms on disk proves every earlier row is on disk: the window is
    # closed. The hour that holds end_ms is read too (end_ms + 1 pulls in the next file when end_ms is on the hour), only to look for such a row.
    end_hour = hours_in(end_ms, end_ms + 1)[0]
    closed = False
    for hour in hours_in(start_ms, end_ms + 1):
        path = trades_dir / f"trades-{hour}.jsonl"
        if not path.is_file():
            missing.append(hour)
            continue
        stats["files_read"] += 1
        with open(path, "rb") as fh:
            for raw in fh:
                stats["lines"] += 1
                if b"pumpswap" not in raw:
                    if not closed and hour >= end_hour:  # a bonding row closes the window too; earlier hours cannot hold one
                        m = _T_RECV_RE.search(raw)
                        closed = bool(m) and int(m.group(1)) >= end_ms
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
                if t >= end_ms:
                    closed = True
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
        "bad_key": stats["bad_key"], "duplicate_keys_dropped": stats["duplicate_keys_dropped"], "window_closed": closed,
        "frame_n": len(unique), "frame_with_tx_index_n": stats["frame_with_tx_index_n"],  # counted before duplicate removal
    }
    return unique, info


def draw(frame: list[dict]) -> tuple[list[dict], list[dict]]:
    """(main, topup), both from the frame alone: the unchanged main draw, then the amended top-up (whitelisted buys only)."""
    am = buy_amend()
    main = am.p7_raw_main_draw(frame, am.P7_SAMPLE)
    return main, am.p7_raw_buy_topup(frame, main)


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
    """(line, outcome, reason) of one sampled print: the amended p7_raw_check (p7_buy_amend.py) with the adapter row built from the tape row
    and V0 = 0."""
    return buy_amend().p7_raw_check(dict(tape), None if raw is None else dict(raw), adapter_row_from_tape(tape), Q_V0, fetch_failed=fetch_failed)


def run_check(sampled: Sequence[tuple[str, dict]], fetch: Callable[[str], dict | None], progress: Callable[[int, int], None] | None = None
              ) -> tuple[list[tuple], dict]:
    """sampled: (set name, frame row) in draw order. Fetches each distinct signature of a comparable print once, then judges every print.
    Returns (results, extras): results[i] = (line, outcome, reason) for sampled[i]; extras counts transactions and decoder errors."""
    ev = buy_amend()
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


# ---- per-name counts -------------------------------------------------------------------------------------------------------------------
NO_NAME = "(none)"  # the ix_name key of a print with no string ix_name (sells carry none)


def _name(row: Mapping[str, Any]) -> str:
    n = row.get("ix_name")
    return n if isinstance(n, str) and n else NO_NAME


def by_ix_name(sampled: Sequence[tuple[str, dict]], results: Sequence[tuple]) -> dict:
    """Comparable prints per line and ix_name: {line: {ix_name: {"n", "hits", "share"}}}. Every comparable print is counted once."""
    out: dict[str, dict[str, dict]] = {"sell": {}, "buy": {}}
    for (_, row), (line, outcome, _reason) in zip(sampled, results):
        if line is None:
            continue
        c = out[line].setdefault(_name(row), {"n": 0, "hits": 0})
        c["n"] += 1
        c["hits"] += int(outcome == "hit")
    for per in out.values():
        for c in per.values():
            c["share"] = _share(c["hits"], c["n"])
    return {line: dict(sorted(per.items())) for line, per in out.items()}


def excluded_by_name(sampled: Sequence[tuple[str, dict]], results: Sequence[tuple]) -> dict:
    """Excluded prints per cause and ix_name: {cause: {ix_name: count}}, every cause of P7_RAW_EXCLUSIONS present."""
    out: dict[str, dict[str, int]] = {c: {} for c in buy_amend().P7_RAW_EXCLUSIONS}
    for (_, row), (line, _outcome, reason) in zip(sampled, results):
        if line is None:
            out[reason][_name(row)] = out[reason].get(_name(row), 0) + 1
    return {c: dict(sorted(per.items())) for c, per in out.items()}


# ---- line 2 (fee tier): reported, not scored ------------------------------------------------------------------------------------------
_L2_FIELDS = ("sol_lamports", "token_raw", "quote_reserve", "base_reserve", "virtual_quote_reserves")


def _l2_rows(main: Sequence[Mapping[str, Any]]) -> tuple[list, int]:
    """The main draw's buys and sells with every line-2 field an int, and how many lacked one (counted, not judged)."""
    rows, missing = [], 0
    for r in main:
        if r.get("side") not in ("buy", "sell"):
            continue
        if not all(_is_int(r.get(k)) for k in _L2_FIELDS):
            missing += 1
            continue
        rows.append(r)
    return rows, missing


def _l2_side_block(n: int, match: int, skipped: int, need: float) -> dict:
    return {"n": n, "match": match, "skipped": skipped, "share": _share(match, n), "need": need}


def line2_exp025_counts(rows: Sequence[Mapping[str, Any]], tier: Callable[[int, int], float]) -> dict:
    """EXP-025 P7 line 2 on tape rows with event_v_map's fee helpers. Q = (vault + V) + V0, V0 = 0 (integers).
    sell: within_bp(sol, round(Q tok / (b + tok) (1 - tier(Q, b))), P7_TOLERANCE_BP), relative to the actual sol.
    buy : |1 - net / sol - tier(Q, b)| <= P7_TOLERANCE_BP / 1e4, net = tok Q / (b - tok); a zero buy (sol <= 0, tok <= 0, tok >= b) is skipped."""
    ev = event_v_map()
    bp = ev.P7_TOLERANCE_BP
    c = {"sell": [0, 0, 0], "buy": [0, 0, 0]}  # n, match, skipped
    for r in rows:
        side = r["side"]
        sol, tok, b = r["sol_lamports"], r["token_raw"], r["base_reserve"]
        q = r["quote_reserve"] + r["virtual_quote_reserves"] + Q_V0
        if side == "buy" and (sol <= 0 or tok <= 0 or tok >= b):
            c[side][2] += 1
            continue
        c[side][0] += 1
        f = tier(q, b)
        if side == "sell":
            ok = b + tok > 0 and ev.within_bp(sol, int(round(q * tok / (b + tok) * (1 - f))), bp)
        else:
            ok = abs((1 - tok * q / (b - tok) / sol) - f) * 10_000 <= bp
        c[side][1] += int(bool(ok))
    sell, buy = _l2_side_block(*c["sell"], ev.P7_SELL_MIN), _l2_side_block(*c["buy"], ev.P7_BUY_MIN)
    return {"sell": sell, "buy": buy, "line_pass": bool(ev.p7_pass(sell["match"], sell["n"], buy["match"], buy["n"]))}


def line2_exp024_counts(rows: Sequence[Mapping[str, Any]], tier_lines: Callable[..., dict]) -> dict:
    """tools.boostfloor_inputs.tier_lines on tape rows, V = the print's own stamp. Its record: key = (slot, pool, sol, tok, q, b), isbuy."""
    sample = [{"key": (r["slot"], r["pool"], r["sol_lamports"], r["token_raw"], r["quote_reserve"], r["base_reserve"]),
               "isbuy": r["side"] == "buy", "v": r["virtual_quote_reserves"]} for r in rows]
    t = tier_lines(sample, lambda _k, rec: float(rec["v"]))
    out = {}
    for side in ("sell", "buy"):
        x = t[side]
        out[side] = {"n": x["n"], "match": x["match"], "skipped": x["skipped"], "no_v": x["no_v"], "share": x["share"], "need": x["need"]}
    out["line_pass"] = bool(t["sell"]["pass"] and t["buy"]["pass"])
    return out


def line2_report(main: Sequence[Mapping[str, Any]], rule: str) -> dict:
    """Line 2 by `rule` ('exp025' or 'exp024') on the main draw: per side, and per ix_name per side. Outcome-blind, tape rows only, no fetch.
    `line_pass` is the line's own bar; it does not enter this tool's `pass`."""
    rows, missing = _l2_rows(main)
    if rule == "exp025":
        tier = exp025_tier()

        def count(rs):
            return line2_exp025_counts(rs, tier)
    elif rule == "exp024":
        tl = exp024_tier_lines()

        def count(rs):
            return line2_exp024_counts(rs, tl)
    else:
        raise ValueError(rule)
    total = count(rows)
    names: dict[str, list] = {}
    for r in rows:
        names.setdefault(_name(r), []).append(r)
    per = {}
    for name in sorted(names):
        x = count(names[name])
        per[name] = {side: {k: v for k, v in x[side].items() if k != "need"} for side in ("sell", "buy") if x[side]["n"] or x[side]["skipped"]}
    return {"sample": "main draw (no top-up)", "prints": len(rows), "fields_missing": missing, "sell": total["sell"], "buy": total["buy"],
            "line_pass": total["line_pass"], "by_ix_name": per, "scored": False}


def _line2_or_error(main: Sequence[Mapping[str, Any]], rule: str) -> dict:
    """line2_report, but an exception in a line-2 helper is reported by type only and never stops line 1, `pass` or the output files
    (e.g. tools.boostfloor_inputs.tier_fee divides by a sampled sell's base_reserve; 0 raises ZeroDivisionError)."""
    try:
        return line2_report(main, rule)
    except Exception as exc:  # noqa: BLE001 - type only: the message could carry tape values
        return {"error": type(exc).__name__, "scored": False}


# ---- output ----------------------------------------------------------------------------------------------------------------------------
def _share(ok: int, n: int) -> float | None:
    return ok / n if n else None


def summarize(tally: Mapping[str, Any], *, window: tuple[int, int], frame_info: Mapping[str, Any], main_n: int, topup_n: int,
              extras: Mapping[str, Any], credits: int, errors: Mapping[str, int], prints_sha256: str | None, blob: str | None,
              per_name: Mapping[str, Any] | None = None, excl_names: Mapping[str, Any] | None = None,
              line2: Mapping[str, Any] | None = None) -> dict:
    ev, am = event_v_map(), buy_amend()
    unresolved = {r: n for r, n in tally["unresolved"].items() if r != "no_adapter_row"}
    assert tally["unresolved"]["no_adapter_row"] == 0  # the adapter row is built from the tape row, so it always exists
    line1 = bool(am.p7_raw_pass(dict(tally)))
    enough = tally["buy_n"] >= am.P7_RAW_BUY_MIN_COMPARABLE
    one_hour = window[0] % _HOUR_MS == 0 and window[1] - window[0] == _HOUR_MS  # the declared form [H, H+1h)
    line2 = line2 or {}
    return {
        "tool": "tools/tip_event_v_p7.py",
        "check": "EXP-025 Amendment 1 P7 line 1, buy side amended (quant-proof ruling 2026-10-10), on tip follower rows (outcome-blind, counts only)",
        "buy_rule": "ix_name in {buy, buy_v2}, no zero_sol; pool_quote_amount vs ceil(Q token_raw / (base_reserve - token_raw)), within 1 bp or 2 lamports",
        "window": {"start": iso_utc(window[0]), "end": iso_utc(window[1]), "field": "t_recv_ms"},
        "key": KEY_NAME,
        "q_identity": "Q = quote_reserve + virtual_quote_reserves of the print itself, V0 = 0",
        "decoder_blob": blob, "decoder_blob_pinned": blob == DECODER_BLOB,
        "event_v_map_sha256": ev.SHA256,
        "p7_buy_amend_sha256": am.SHA256,
        "getTransaction": dict(TX_CONFIG, attempts=ev.P7_RAW_TX_ATTEMPTS),
        "frame_n": frame_info["frame_n"],
        "unstamped_canonical_n": frame_info["unstamped_canonical_n"],
        "sample_n": main_n,
        "topup_n": topup_n,
        "tx_n": extras["tx_n"],
        "comparable": {"sell_n": tally["sell_n"], "buy_n": tally["buy_n"]},
        "excluded_n": tally["excluded"],
        "excluded_by": dict(tally["excluded_by"]),
        "excluded_by_name": dict(excl_names or {}),
        "by_ix_name": dict(per_name or {}),
        "hits": {"sell": tally["sell_ok"], "buy": tally["buy_ok"]},
        "shares": {"sell": _share(tally["sell_ok"], tally["sell_n"]), "buy": _share(tally["buy_ok"], tally["buy_n"])},
        "bars": {"sell": ev.P7_CP_SELL_MIN, "buy": ev.P7_CP_BUY_MIN, "min_comparable_buys": am.P7_RAW_BUY_MIN_COMPARABLE},
        "unresolved": unresolved,
        "unresolved_n": sum(unresolved.values()),
        "decode_errors": extras["decode_errors"],
        "credits": credits,
        "fetch_errors": dict(errors),
        "acceptance": {"line1_pass": line1, "buy_n_at_least_100": enough, "one_full_utc_hour": one_hour,
                       "declared_before_h": "not checked here: the PR and notebook entry must name [H, H+1h) before H",
                       "not_seen_here": "follower counters mismatch/errors/missing_pumpswap = 0, no new backlog_jump or unfetchable rows"},
        "pass": line1 and enough and one_hour,
        "line2_exp025": line2.get("exp025"),
        "line2_exp024": line2.get("exp024"),
        "prints_file": OUT_PRINTS,
        "prints_sha256": prints_sha256,
        "frame": dict(frame_info),
    }


def print_rows(sampled: Sequence[tuple[str, dict]], results: Sequence[tuple]) -> bytes:
    lines = []
    for (name, row), (line, outcome, reason) in zip(sampled, results):
        lines.append(json.dumps({"set": name, "slot": row["slot"], "signature": row["signature"], "event_index": row["event_index"],
                                 "ix_name": _name(row), "line": line, "outcome": outcome, "reason": reason}, sort_keys=True, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode() if lines else b""


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ---- driver ----------------------------------------------------------------------------------------------------------------------------
def _plan(sampled: Sequence[tuple[str, dict]]) -> dict:
    """What the draw alone decides: the comparable populations (per ix_name), the exclusions per cause and per name, the transactions to fetch."""
    am = buy_amend()
    lines = Counter(am.p7_raw_line(r) for _, r in sampled)
    causes = Counter(am.p7_raw_exclusion(r) for _, r in sampled if am.p7_raw_line(r) is None)
    by_name: dict[str, Counter] = {"sell": Counter(), "buy": Counter()}
    excl: dict[str, Counter] = {c: Counter() for c in am.P7_RAW_EXCLUSIONS}
    for _, r in sampled:
        line = am.p7_raw_line(r)
        if line is None:
            excl[am.p7_raw_exclusion(r)][_name(r)] += 1
        else:
            by_name[line][_name(r)] += 1
    sigs = {r["signature"] for _, r in sampled if am.p7_raw_line(r) is not None}
    return {"comparable": {"sell_n": lines["sell"], "buy_n": lines["buy"]}, "excluded_n": lines[None],
            "excluded_by": {c: causes[c] for c in am.P7_RAW_EXCLUSIONS},
            "excluded_by_name": {c: dict(sorted(v.items())) for c, v in excl.items()},
            "comparable_by_ix_name": {k: dict(sorted(v.items())) for k, v in by_name.items()},
            "tx_n": len(sigs), "max_credits": len(sigs) * am.EV.P7_RAW_TX_ATTEMPTS}


def run(args: argparse.Namespace, *, call: Callable[[str], Any] | None = None, canonical: Callable[[str], str] | None = None,
        sleep: Callable[[float], None] = time.sleep, out=None) -> int:
    out = out or sys.stdout
    ev = event_v_map()
    buy_amend()
    exp025_tier()  # line 2's helpers load before any fetch: a missing or changed helper refuses here, not after the credits are spent
    exp024_tier_lines()
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
    if not info["window_closed"]:
        raise Refused("the window is not closed: no row at or after --end is on disk yet, so the last rows of the window may not be written. "
                      "Use an --end a few minutes in the past")
    main_rows, topup_rows = draw(frame)  # both before any fetch
    sampled = [("main", r) for r in main_rows] + [("topup", r) for r in topup_rows]
    plan = _plan(sampled)
    head = {"frame_n": info["frame_n"], "unstamped_canonical_n": info["unstamped_canonical_n"], "sample_n": len(main_rows),
            "topup_n": len(topup_rows)}
    if args.dry_run:  # counts the draw decides only: no law, no line 2, no RPC call, nothing written
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
    tally = buy_amend().p7_raw_tally([(row, res) for (_, row), res in zip(sampled, results)])
    out_dir.mkdir(parents=True, exist_ok=True)
    prints = print_rows(sampled, results)
    _write_atomic(out_dir / OUT_PRINTS, prints)  # the credits are spent: the line-1 prints are on disk before line 2 runs
    line2 = {rule: _line2_or_error(main_rows, rule) for rule in ("exp025", "exp024")}
    summary = summarize(tally, window=(start_ms, end_ms), frame_info=info, main_n=len(main_rows), topup_n=len(topup_rows), extras=extras,
                        credits=fetch.calls, errors=fetch.errors, prints_sha256=hashlib.sha256(prints).hexdigest(), blob=blob,
                        per_name=by_ix_name(sampled, results), excl_names=excluded_by_name(sampled, results), line2=line2)
    text = json.dumps(summary, indent=1, sort_keys=True) + "\n"
    _write_atomic(out_dir / OUT_SUMMARY, text.encode())
    out.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Outcome-blind P7 line-1 check on the tip follower's event-V stamp (counts only).")
    ap.add_argument("--trades-dir", default=DEFAULT_TRADES_DIR, help="the follower's --out directory (trades-<hour>.jsonl)")
    ap.add_argument("--start", required=True, help="window start H, UTC, inclusive (t_recv_ms); the acceptance run is the declared [H, H+1h)")
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
