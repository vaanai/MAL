#!/usr/bin/env python3
"""P98: counts-only census of synthetic-migration completers (library row p95-synthetic-completer). Paper only. No keys.

WHAT IT COUNTS, per closed UTC day: completions per day; synthetic (PostCompleteBuyEvent present in the completion tx) vs normal; the
completer's own buy size (quote_in / base_out of that event, pool part only); the completer signer; the top-5 signer share.

SOURCE (QP R1): the arena tape /data/mal/arena/tape (tools/arena_feed.py, schema tools/arena_tape_schema.md). Completions are the `complete`
rows. Each completion tx is then fetched ONCE by signature from public RPC getTransaction (completion tx only, never the migrate tx).

EXPLICIT ALLOWLIST. Every tape row is wrapped in GuardedRow; any key outside ROW_ALLOW[type] raises ForbiddenFieldError, as do .items(),
.values(), .keys() and iteration. Reading is by type:
  complete : type, signature, t_recv_ms (UTC date only), mint (Mayhem lookup inside the tape and de-dup; never printed), quote_mint
  create   : type, mint, is_mayhem_mode (Mayhem exclusion only)
  gap      : type, prog, data_loss        (coverage: a data_loss gap makes the day NOT_DECIDABLE)
  hb       : type, prog                   (coverage: an hour needs >= HB_MIN_PER_HOUR pumpfun hb rows)
  any other row type: `type` only, then skipped.
The RPC response is read only at result.meta.err, meta.logMessages, meta.innerInstructions, meta.loadedAddresses and
transaction.message.accountKeys (the B1 reader's inputs; --verify-tx adds balances and fee). The event is found as the Am.4 B1 reader
(tools/pump_structure_monitor.tx_event_blobs) finds it: "Program data:" logs and emit_cpi self-CPI inner instructions; a tx whose event source
cannot be determined is unclassified, never "normal". Only the PostCompleteBuyEvent presence and the
bytes at offsets 8..40 (completer), 144..152 (base_out) and 152..160 (quote_in) are decoded. NO reserve, price, first-print slot, slot of any
kind, migrate_v2 timing, BOOST datum or outcome field is read or decoded (the event's reserve/fee/ts bytes are never unpacked).

NO JOIN. Before 2026-11-14 nothing is joined by mint to any Q50/HV4/arena-book/shadow/canary/A3/BOOST record and no later tx of a completer
is fetched. The output has no mint, signature or wallet: only per-day counts, shares and one median.

COVERAGE. Non-Mayhem completions are the denominator. A completion is unclassified when its tx fails to fetch or decode, its logs are
truncated without the event, its Mayhem flag is unknown (no create row in the scanned window), or it has two events. A day with more than 1%
unclassified, a missing hour file, a bad hour (data_loss gap or fewer than 60 pumpfun hb rows) or no completions is NOT_DECIDABLE.

KILL LINES (pre-stated in the row): synthetic share < 10% on the median decidable day (NOT blind, disclosed), top-5 signers >= 80% of the
pooled synthetic completions, median pooled synthetic quote_in < 0.05 SOL.

RUN CONDITIONS (QP ruling on #1059, E1-E4, H1, V1; they bind the manager's job):
  E1. Count exactly 3 consecutive closed UTC days, starting the first full UTC day after the PR merges. The days are pinned in the job
      command. Never count 2026-10-19..10-23 or any day before the merge; never add a day later to replace a NOT_DECIDABLE one.
  E2. Never join the output to HV4, Q50, arena-book, shadow, canary or Haiku-bot inputs, rules or outputs before their reads, and never use
      it as a prompt or regime signal. A rule priced on these in-clock counts is in-sample.
  E3. This tool's bad hours are never an input to any book's excluded hours. A disagreement with a book's table of record goes to QP.
  E4. MiScusi job under MemoryMax; a notebook decision entry first (head sha, exact command, RPC host).
  H1. getTransaction is paced at >= 1.0 s per call, enforced in code (MIN_INTERVAL_S): research-0's arena feed pool resolver shares the IP.
  V1. Before the first count, --verify-tx on two synthetic completion txs (one buy_v3, one buy_exact_quote_in_v3) from days before the counted
      days. A FAIL changes the kill-line definition on V1 alone, never after count output exists.
  LOOKBACK. Mayhem flags come from `create` rows of the counted day and the LOOKBACK_DAYS = 2 days before it. This is fixed and must never be
      widened. A coin created more than 48 h before it completes has no create row in the window and is UNCLASSIFIED (disclosed; conservative).
GuardedRow is a guard against accidental reads, not a sandbox: Python can always reach the wrapped dict.

Run only after the QP ruling on the source. Fixture tests: tools/test_p98_synth_completer_count.py.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import binascii
import collections
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlparse

from observe.trade_decode import b58encode
from tools import pump_structure_monitor as M
from tools.tape_lines import _open_lines

POST_COMPLETE_BUY_DISC = bytes.fromhex("6fb06d8b316cd5fb")
WSOL = "So11111111111111111111111111111111111111112"
MIN_EVENT_LEN = 232  # same minimum as observe.trade_decode; only bytes 8..40 and 144..160 are used
UNCLASSIFIED_MAX = 0.01
# arena_feed.py:94 HB_S = 60.0 and :770 `time.monotonic() - self.last_hb >= HB_S` checked on a >= 5 s tick, so hb spacing is 60..65 s:
# a healthy hour holds between 55 (3600 / 65) and 60 rows. 55 is the floor with no margin for a lost row; fixed, never changed after output.
HB_MIN_PER_HOUR = 55
LOOKBACK_DAYS = 2  # fixed; never widened (QP #1059). Older coins are unclassified.
MIN_INTERVAL_S = 1.0  # hard floor between getTransaction calls (QP #1059 H1)
TAPE_FLOOR_DAY = date(2026, 10, 20)  # tape before this is partial / redeploy day; refused
KILL_SHARE = 0.10
KILL_TOP5 = 0.80
KILL_MEDIAN_SOL = 0.05
FEED_HOST = "api.mainnet-beta.solana.com"  # the arena feed's own host: refused (QP #1059 H1)

ROW_ALLOW: dict[str, frozenset[str]] = {
    "complete": frozenset({"type", "signature", "t_recv_ms", "mint", "quote_mint"}),
    "create": frozenset({"type", "mint", "is_mayhem_mode"}),
    "gap": frozenset({"type", "prog", "data_loss"}),
    "hb": frozenset({"type", "prog"}),
}
OTHER_ALLOW = frozenset({"type"})


class ForbiddenFieldError(KeyError):
    pass


class GuardedRow:
    """A tape row that only answers for the allowlisted keys of its type. Everything else raises."""

    __slots__ = ("__d", "__allow")  # name-mangled: a guard against accidental reads, not a sandbox

    def __init__(self, d: dict[str, Any]):
        t = d.get("type") if isinstance(d, dict) else None
        object.__setattr__(self, "_GuardedRow__d", d)
        object.__setattr__(self, "_GuardedRow__allow", ROW_ALLOW.get(t, OTHER_ALLOW) if isinstance(t, str) else OTHER_ALLOW)

    def _check(self, key: str) -> None:
        if key not in self.__allow:
            raise ForbiddenFieldError(f"field {key!r} is not on the P98 allowlist for this row type")

    def __getitem__(self, key: str) -> Any:
        self._check(key)
        return self.__d[key]

    def get(self, key: str, default: Any = None) -> Any:
        self._check(key)
        return self.__d.get(key, default)

    def __contains__(self, key: object) -> bool:  # membership would leak which keys exist
        raise ForbiddenFieldError("membership test is not allowed")

    def __iter__(self):
        raise ForbiddenFieldError("iteration is not allowed")

    def keys(self):
        raise ForbiddenFieldError("keys() is not allowed")

    def values(self):
        raise ForbiddenFieldError("values() is not allowed")

    def items(self):
        raise ForbiddenFieldError("items() is not allowed")

    def __len__(self):
        raise ForbiddenFieldError("len() is not allowed")

    def __setattr__(self, k, v):
        raise AttributeError("read only")

    def __repr__(self) -> str:
        return "<GuardedRow>"


def utc_day_of_ms(t_ms: int) -> date:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).date()


def hour_files(tape_dir: Path, day: date) -> tuple[list[Path], list[str]]:
    """The 24 hour files of a UTC day (prefer .zst, else .jsonl) and the list of missing hours."""
    found: list[Path] = []
    missing: list[str] = []
    for h in range(24):
        stem = f"tape-{day.isoformat()}T{h:02d}.jsonl"
        z, p = tape_dir / (stem + ".zst"), tape_dir / stem
        if z.exists():
            found.append(z)
        elif p.exists():
            found.append(p)
        else:
            missing.append(f"{h:02d}")
    return found, missing


def iter_rows(path: Path) -> Iterator[GuardedRow]:
    for line in _open_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line, strict=False)
        except ValueError:
            continue
        if isinstance(d, dict):
            yield GuardedRow(d)


# ---- tx side ------------------------------------------------------------------------------------------------
TRADE_DISC = bytes.fromhex("bddb7fd34ee661ee")  # pump TradeEvent; used only by --verify-tx


def classify_tx(tx: Any) -> dict[str, Any]:
    """Class of a completion tx: {"cls": synthetic|normal|unclassified, quote_in, base_out, signer, why}.

    `tx` is the mini tx from tx_from_response (B1 reader shape). Events are read the way the Am.4 B1 reader reads them
    (pump_structure_monitor.tx_event_blobs: "Program data:" logs plus emit_cpi self-CPI inner instructions, de-duplicated). If no event is
    found, the tx is "normal" only when its event sources were fully readable; otherwise it is unclassified.
    Decodes ONLY the discriminator, the completer pubkey (8..40), base_out (144..152) and quote_in (152..160)."""
    if not isinstance(tx, dict):
        return {"cls": "unclassified", "why": "no_tx"}
    meta = tx.get("meta")
    logs = meta.get("logMessages") if isinstance(meta, dict) else None
    if not isinstance(logs, list) or not all(isinstance(x, str) for x in logs):
        return {"cls": "unclassified", "why": "no_logs"}
    try:
        blobs = M.tx_event_blobs(tx)
    except Exception:  # noqa: BLE001 - an undecodable tx is unclassified, never normal
        return {"cls": "unclassified", "why": "event_parse_error"}
    hits = [b for b in blobs if b[:8] == POST_COMPLETE_BUY_DISC]
    if len(hits) > 1:
        return {"cls": "unclassified", "why": "two_events"}
    if len(hits) == 1:
        raw = hits[0]
        if len(raw) < MIN_EVENT_LEN:
            return {"cls": "unclassified", "why": "short_event"}
        return {
            "cls": "synthetic",
            "signer": b58encode(raw[8:40]),
            "base_out": int.from_bytes(raw[144:152], "little"),
            "quote_in": int.from_bytes(raw[152:160], "little"),
        }
    # no event seen: say "normal" only if no event source was hidden from us
    if any("Log truncated" in ln for ln in logs):
        return {"cls": "unclassified", "why": "log_truncated"}
    inner = meta.get("innerInstructions")
    if not isinstance(inner, list):
        return {"cls": "unclassified", "why": "inner_instructions_missing"}
    keys = M.tx_keys(tx)
    for group in inner:
        for ix in (group.get("instructions") or []) if isinstance(group, dict) else []:
            pid = ix.get("programIdIndex") if isinstance(ix, dict) else None
            if not isinstance(pid, int) or pid >= len(keys):
                return {"cls": "unclassified", "why": "inner_program_unresolved"}
            if keys[pid] in (M.PUMP_PROGRAM, M.PUMPSWAP_PROGRAM):
                try:
                    M.b58decode(ix.get("data") or "")
                except ValueError:
                    return {"cls": "unclassified", "why": "inner_data_undecodable"}
    return {"cls": "normal"}


def tx_from_response(resp: Any, *, balances: bool = False) -> Any:
    """Copy out only the fields the classifier needs (and, for --verify-tx, the balances and fee); drop the rest of the response.
    Returns None when the tx is missing, failed or has no meta."""
    if not isinstance(resp, dict):
        return None
    res = resp.get("result")
    if not isinstance(res, dict):
        return None
    meta = res.get("meta")
    if not isinstance(meta, dict) or meta.get("err") is not None:
        return None
    msg = (res.get("transaction") or {}).get("message") if isinstance(res.get("transaction"), dict) else None
    keys = msg.get("accountKeys") if isinstance(msg, dict) else None
    m: dict[str, Any] = {"err": None, "logMessages": meta.get("logMessages"), "innerInstructions": meta.get("innerInstructions"),
                         "loadedAddresses": meta.get("loadedAddresses")}
    if balances:
        for k in ("fee", "preBalances", "postBalances", "preTokenBalances", "postTokenBalances"):
            m[k] = meta.get(k)
    return {"meta": m, "transaction": {"message": {"accountKeys": keys}}}


def check_rpc_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.query or u.username or u.password or u.path not in ("", "/"):
        raise SystemExit("refused: rpc url must be https, host only (no path, query or userinfo)")
    if u.hostname == FEED_HOST or "solanavibestation" in u.hostname:
        raise SystemExit("refused: the arena feed's public host is off limits (QP #1059 H1)")
    if "helius" in u.hostname:
        raise SystemExit("refused: no Helius endpoint (0 credits by rule)")
    return url


def make_rpc_fetch(url: str, *, min_interval_s: float = MIN_INTERVAL_S, retries: int = 3, timeout_s: float = 20.0, balances: bool = False,
                   clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                   opener: Callable[..., Any] | None = None) -> Callable[[str], Any]:
    """fetch(signature) -> mini tx or None. One getTransaction per signature, at least MIN_INTERVAL_S between ANY two calls (retries included)."""
    if min_interval_s < MIN_INTERVAL_S:
        raise SystemExit(f"refused: min interval {min_interval_s}s is below the {MIN_INTERVAL_S}s floor")
    check_rpc_url(url)
    last: list[float | None] = [None]
    op = opener or urllib.request.urlopen

    def fetch(sig: str) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                           "params": [sig, {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}]}).encode()
        for attempt in range(retries):
            if last[0] is not None:
                wait = min_interval_s - (clock() - last[0])
                if wait > 0:
                    sleep(wait)
            last[0] = clock()
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with op(req, timeout=timeout_s) as r:  # noqa: S310 - https host checked above
                    resp = json.loads(r.read())
            except (urllib.error.URLError, TimeoutError, ValueError, OSError):
                continue
            if isinstance(resp, dict) and isinstance(resp.get("error"), dict):
                continue
            return tx_from_response(resp, balances=balances)
        return None

    return fetch


# ---- tape side ----------------------------------------------------------------------------------------------
def scan_day_coverage_and_completions(files: Iterable[Path], day: date, late_files: Iterable[Path] = ()) -> dict[str, Any]:
    """One pass over a day's hour files: completions (deduped by signature), coverage flags. Mayhem sets are filled by scan_creates."""
    comps: dict[str, tuple[str, str]] = {}  # signature -> (mint, quote_mint)
    bad_hours: list[str] = []
    for p in files:
        hb = 0
        data_loss = False
        for row in iter_rows(p):
            t = row["type"]
            if t == "complete":
                tm = row["t_recv_ms"]
                if not isinstance(tm, int) or isinstance(tm, bool) or utc_day_of_ms(tm) != day:
                    continue
                sig = row["signature"]
                if isinstance(sig, str) and sig and sig not in comps:
                    comps[sig] = (row["mint"], row["quote_mint"])
            elif t == "hb":
                if row["prog"] == "pumpfun":
                    hb += 1
            elif t == "gap":
                if row["data_loss"] is True and row["prog"] in ("pumpfun", "all"):
                    data_loss = True
        if data_loss or hb < HB_MIN_PER_HOUR:
            bad_hours.append(p.name[16:18] if p.name.startswith("tape-") else p.name)
    # rows_late_to_current_hour can put a day-D completion into the D+1 T00 file: read `complete` rows of day D there, nothing else
    for p in late_files:
        for row in iter_rows(p):
            if row["type"] != "complete":
                continue
            tm = row["t_recv_ms"]
            sig = row["signature"]
            if isinstance(tm, int) and not isinstance(tm, bool) and utc_day_of_ms(tm) == day and isinstance(sig, str) and sig and sig not in comps:
                comps[sig] = (row["mint"], row["quote_mint"])
    return {"completions": comps, "bad_hours": bad_hours}


def scan_creates(files: Iterable[Path], mayhem: set[str], normal: set[str]) -> None:
    for p in files:
        for row in iter_rows(p):
            if row["type"] != "create":
                continue
            m = row["mint"]
            if not isinstance(m, str):
                continue
            flag = row["is_mayhem_mode"]
            if flag is True:
                mayhem.add(m)
            elif flag is False:
                normal.add(m)


# ---- day report ---------------------------------------------------------------------------------------------
def top5_share(counter: collections.Counter) -> float | None:
    tot = sum(counter.values())
    return None if tot == 0 else sum(c for _, c in counter.most_common(5)) / tot


def count_day(day: date, tape_dir: Path, fetch: Callable[[str], Any], *, tape_floor: date = TAPE_FLOOR_DAY) -> dict[str, Any]:
    """Counts for one UTC day. Returns the public record (counts only) plus `_signers` / `_quotes` for pooling (stripped before printing)."""
    if day < tape_floor:
        raise SystemExit(f"refused: {day} is before the tape floor {tape_floor}")
    files, missing = hour_files(tape_dir, day)
    rec: dict[str, Any] = {"day": day.isoformat(), "missing_hours": missing}
    nxt, _ = hour_files(tape_dir, day + timedelta(days=1))
    late = [f for f in nxt if f.name[16:18] == "00"]
    if not late:
        missing = missing + ["next_day_00"]
        rec["missing_hours"] = missing
    cov = scan_day_coverage_and_completions(files, day, late)
    rec["bad_hours"] = cov["bad_hours"]
    mayhem: set[str] = set()
    normal: set[str] = set()
    for back in range(LOOKBACK_DAYS, -1, -1):
        d = day - timedelta(days=back)
        if d < tape_floor:
            continue
        f, _ = hour_files(tape_dir, d)
        scan_creates(f, mayhem, normal)
    comps = cov["completions"]
    n_mayhem = n_unknown = n_normal = n_syn = n_unc = n_nonwsol = 0
    signers: collections.Counter = collections.Counter()
    quotes: list[int] = []
    why: collections.Counter = collections.Counter()
    for sig, (mint, qmint) in comps.items():
        if mint in mayhem:
            n_mayhem += 1
            continue
        if mint not in normal:
            n_unknown += 1
            n_unc += 1
            why["mayhem_flag_unknown"] += 1
            continue
        c = classify_tx(fetch(sig)) if fetch is not None else {"cls": "unclassified", "why": "no_fetch"}
        if c["cls"] == "unclassified":
            n_unc += 1
            why[c.get("why", "?")] += 1
        elif c["cls"] == "normal":
            n_normal += 1
        else:
            n_syn += 1
            signers[c["signer"]] += 1
            if qmint == WSOL:
                quotes.append(c["quote_in"])
            else:
                n_nonwsol += 1
    denom = len(comps) - n_mayhem
    rec.update({
        "completions": len(comps), "mayhem": n_mayhem, "non_mayhem": denom,
        "synthetic": n_syn, "normal": n_normal, "unclassified": n_unc, "unclassified_why": dict(why),
        "unclassified_frac": (n_unc / denom) if denom else None,
        "synthetic_share": (n_syn / denom) if denom else None,
        "synthetic_nonwsol_quote": n_nonwsol,
        "quote_in_median_sol": (statistics.median(quotes) / 1e9) if quotes else None,
        "quote_in_n": len(quotes),
        "distinct_signers": len(signers), "top5_share": top5_share(signers),
    })
    reasons = []
    if missing:
        reasons.append("missing_hour_file")
    if cov["bad_hours"]:
        reasons.append("bad_hour")
    if denom == 0:
        reasons.append("no_non_mayhem_completions")
    elif n_unc / denom > UNCLASSIFIED_MAX:
        reasons.append("unclassified_over_1pct")
    rec["status"] = "NOT_DECIDABLE" if reasons else "DECIDABLE"
    rec["not_decidable_reasons"] = reasons
    rec["_signers"] = signers
    rec["_quotes"] = quotes
    return rec


def kill_review(days: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [d for d in days if d["status"] == "DECIDABLE"]
    out: dict[str, Any] = {"decidable_days": len(ok), "days": len(days)}
    if not ok:
        out["verdict"] = "NOT_DECIDABLE"
        return out
    pooled: collections.Counter = collections.Counter()
    quotes: list[int] = []
    for d in ok:
        pooled.update(d["_signers"])
        quotes.extend(d["_quotes"])
    med_share = statistics.median(d["synthetic_share"] for d in ok)
    top5 = top5_share(pooled)
    med_q = (statistics.median(quotes) / 1e9) if quotes else None
    kills = []
    if med_share < KILL_SHARE:
        kills.append("share_lt_10pct (not blind, disclosed)")
    if top5 is not None and top5 >= KILL_TOP5:
        kills.append("top5_ge_80pct")
    if med_q is not None and med_q < KILL_MEDIAN_SOL:
        kills.append("median_quote_in_lt_0.05_sol")
    if top5 is None or med_q is None:
        kills.append("no_synthetic_completions_to_measure")
    out.update({"median_day_synthetic_share": med_share, "pooled_synthetic": sum(pooled.values()),
                "pooled_top5_share": top5, "pooled_median_quote_in_sol": med_q, "kill_lines_hit": kills,
                "verdict": "KILL" if kills else "NO_KILL_LINE_HIT (counts only, not an edge claim)"})
    return out


def public(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def parse_days(spec: str, now: datetime | None = None) -> list[date]:
    now = now or datetime.now(timezone.utc)
    out = []
    for s in spec.split(","):
        d = date.fromisoformat(s.strip())
        if d >= now.date():
            raise SystemExit(f"refused: {d} is not a closed UTC day")
        out.append(d)
    return sorted(set(out))


def _u64(raw: bytes, off: int) -> int:
    return int.from_bytes(raw[off:off + 8], "little")


def verify_identity(tx: Any, variant: str) -> dict[str, Any]:
    """V1 pinned, lamport-exact check that the event's quote_in/base_out are the POOL part only, from the tx's own bytes and balances.

    Inputs (integers only; no reserve bytes 200..232, no pool vault or pool account, no price):
      event (PostCompleteBuyEvent): quote_in@152, base_out@144, u64@160, fee@168, creator_fee@184, buyback_fee@192.
      curve part: the pump TradeEvent with user == completer and is_buy: mint@8, sol_lamports@40, token_amount@48, protocol_fee@169, creator_fee@217.
      balances: signer (account 0) lamport debit, meta.fee, rent of new accounts (sum of rises of accounts with pre == 0, other than the
      signer), and the completer-owned token balance delta of the trade mint.
    Hypotheses, fixed before any value was seen:
      POOL_ONLY      token_delta == trade.token_amount + base_out   and
                     debit - fee - new_rent == (sol + protocol_fee + creator_fee) + quote_in + ev.fee + ev.creator_fee + ev.buyback_fee
      CURVE_PLUS_POOL token_delta == base_out  and
                     debit - fee - new_rent == quote_in + ev.fee + ev.creator_fee + ev.buyback_fee
    PASS only when exactly one hypothesis is exact on BOTH sides and the other misses on SOL by >= the curve sol_lamports.
    Anything else is FAIL (inconclusive included). The result prints integers and the variant label only."""
    out: dict[str, Any] = {"variant": variant}
    c = classify_tx(tx)
    if c["cls"] != "synthetic":
        return {**out, "verdict": "FAIL", "why": f"not_synthetic:{c.get('why', c['cls'])}"}
    meta = tx["meta"]
    ev = next(b for b in M.tx_event_blobs(tx) if b[:8] == POST_COMPLETE_BUY_DISC)
    if len(ev) < 200:
        return {**out, "verdict": "FAIL", "why": "short_event"}
    completer = ev[8:40]
    trades = [b for b in M.tx_event_blobs(tx) if b[:8] == TRADE_DISC and len(b) >= 225 and b[56] == 1 and b[57:89] == completer]
    if len(trades) != 1:
        return {**out, "verdict": "FAIL", "why": f"curve_trade_events:{len(trades)}"}
    tr = trades[0]
    pre, post, fee = meta.get("preBalances"), meta.get("postBalances"), meta.get("fee")
    if not (isinstance(pre, list) and isinstance(post, list) and len(pre) == len(post) and pre and isinstance(fee, int)):
        return {**out, "verdict": "FAIL", "why": "balances_missing"}
    keys = M.tx_keys(tx)
    if not keys or keys[0] != b58encode(completer):
        return {**out, "verdict": "FAIL", "why": "completer_is_not_fee_payer"}
    debit = pre[0] - post[0]
    new_rent = sum(post[i] - pre[i] for i in range(1, len(pre)) if pre[i] == 0 and post[i] > 0)
    mint = b58encode(tr[8:40])
    owner = b58encode(completer)

    def tok(rows: Any) -> int:
        return sum(int(r["uiTokenAmount"]["amount"]) for r in (rows or []) if r.get("owner") == owner and r.get("mint") == mint)

    token_delta = tok(meta.get("postTokenBalances")) - tok(meta.get("preTokenBalances"))
    curve_sol, curve_tok = _u64(tr, 40), _u64(tr, 48)
    curve_total = curve_sol + _u64(tr, 169) + _u64(tr, 217)
    q_in, b_out = _u64(ev, 152), _u64(ev, 144)
    ev_fees = _u64(ev, 168) + _u64(ev, 184) + _u64(ev, 192)
    net = debit - fee - new_rent
    res_pool = {"tok": token_delta - (curve_tok + b_out), "sol": net - (curve_total + q_in + ev_fees)}
    res_both = {"tok": token_delta - b_out, "sol": net - (q_in + ev_fees)}
    exact_pool = res_pool["tok"] == 0 and res_pool["sol"] == 0
    exact_both = res_both["tok"] == 0 and res_both["sol"] == 0
    verdict = "FAIL"
    if exact_pool and not exact_both and abs(res_both["sol"]) >= curve_sol:
        verdict = "PASS_POOL_ONLY"
    elif exact_both and not exact_pool and abs(res_pool["sol"]) >= curve_sol:
        verdict = "PASS_CURVE_PLUS_POOL"
    return {**out, "verdict": verdict, "quote_in": q_in, "base_out": b_out, "ev_u64_160": _u64(ev, 160), "ev_fee": _u64(ev, 168),
            "ev_creator_fee": _u64(ev, 184), "ev_buyback_fee": _u64(ev, 192), "curve_sol_lamports": curve_sol, "curve_token_amount": curve_tok,
            "curve_protocol_fee": _u64(tr, 169), "curve_creator_fee": _u64(tr, 217), "signer_debit": debit, "meta_fee": fee,
            "new_account_rent": new_rent, "token_delta": token_delta, "residual_pool_only": res_pool, "residual_curve_plus_pool": res_both}


VARIANTS = ("buy_v3", "buy_exact_quote_in_v3")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--days", help="comma list of closed UTC days, YYYY-MM-DD")
    ap.add_argument("--rpc-url", required=True, help="public archival https host the arena feed does not use (QP H1)")
    ap.add_argument("--out", help="write the counts JSON here")
    ap.add_argument("--verify-tx", nargs=2, metavar=("BUY_V3_SIG", "BUY_EXACT_QUOTE_IN_V3_SIG"),
                    help="V1: signatures of one buy_v3 and one buy_exact_quote_in_v3 completion tx from days before the counted days")
    a = ap.parse_args(argv)
    if a.verify_tx:
        vf = make_rpc_fetch(a.rpc_url, balances=True)
        print(json.dumps([verify_identity(vf(sig), v) for v, sig in zip(VARIANTS, a.verify_tx)], indent=1, sort_keys=True))
        return 0
    fetch = make_rpc_fetch(a.rpc_url)
    if not a.days:
        ap.error("--days is required")
    recs = [count_day(d, Path(a.tape_dir), fetch) for d in parse_days(a.days)]
    result = {"tool": "p98_synth_completer_count", "days": [public(r) for r in recs], "review": kill_review(recs)}
    text = json.dumps(result, indent=1, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
