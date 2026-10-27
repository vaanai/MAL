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
  hb       : type, prog                   (coverage: an hour needs >= 60 pumpfun hb rows)
  any other row type: `type` only, then skipped.
The RPC response is read only at result.meta.err and result.meta.logMessages. From the logs only the PostCompleteBuyEvent presence and the
bytes at offsets 8..40 (completer), 144..152 (base_out) and 152..160 (quote_in) are decoded. NO reserve, price, first-print slot, slot of any
kind, migrate_v2 timing, BOOST datum or outcome field is read or decoded (the event's reserve/fee/ts bytes are never unpacked).

NO JOIN. Before 2026-11-14 nothing is joined by mint to any Q50/HV4/arena-book/shadow/canary/A3/BOOST record and no later tx of a completer
is fetched. The output has no mint, signature or wallet: only per-day counts, shares and one median.

COVERAGE. Non-Mayhem completions are the denominator. A completion is unclassified when its tx fails to fetch or decode, its logs are
truncated without the event, its Mayhem flag is unknown (no create row in the scanned window), or it has two events. A day with more than 1%
unclassified, a missing hour file, a bad hour (data_loss gap or fewer than 60 pumpfun hb rows) or no completions is NOT_DECIDABLE.

KILL LINES (pre-stated in the row): synthetic share < 10% on the median decidable day (NOT blind, disclosed), top-5 signers >= 80% of the
pooled synthetic completions, median pooled synthetic quote_in < 0.05 SOL.

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
from tools.tape_lines import _open_lines

POST_COMPLETE_BUY_DISC = bytes.fromhex("6fb06d8b316cd5fb")
WSOL = "So11111111111111111111111111111111111111112"
MIN_EVENT_LEN = 232  # same minimum as observe.trade_decode; only bytes 8..40 and 144..160 are used
UNCLASSIFIED_MAX = 0.01
HB_MIN_PER_HOUR = 60
TAPE_FLOOR_DAY = date(2026, 10, 20)  # tape before this is partial / redeploy day; refused
KILL_SHARE = 0.10
KILL_TOP5 = 0.80
KILL_MEDIAN_SOL = 0.05
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"

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

    __slots__ = ("_d", "_allow")

    def __init__(self, d: dict[str, Any]):
        t = d.get("type") if isinstance(d, dict) else None
        object.__setattr__(self, "_d", d)
        object.__setattr__(self, "_allow", ROW_ALLOW.get(t, OTHER_ALLOW) if isinstance(t, str) else OTHER_ALLOW)

    def _check(self, key: str) -> None:
        if key not in self._allow:
            raise ForbiddenFieldError(f"field {key!r} is not on the P98 allowlist for this row type")

    def __getitem__(self, key: str) -> Any:
        self._check(key)
        return self._d[key]

    def get(self, key: str, default: Any = None) -> Any:
        self._check(key)
        return self._d.get(key, default)

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
def program_data_bytes(line: str) -> bytes | None:
    if not isinstance(line, str) or "Program data: " not in line:
        return None
    blob = line.split("Program data: ", 1)[1].strip()
    try:
        return base64.b64decode(blob, validate=False) if blob else None
    except (ValueError, binascii.Error):
        return None


def classify_logs(logs: Any) -> dict[str, Any]:
    """Class of a completion tx from its log lines: {"cls": synthetic|normal|unclassified, quote_in, base_out, signer, why}.

    Decodes ONLY the discriminator, the completer pubkey (8..40), base_out (144..152) and quote_in (152..160)."""
    if not isinstance(logs, list) or not all(isinstance(x, str) for x in logs):
        return {"cls": "unclassified", "why": "no_logs"}
    hits: list[bytes] = []
    for ln in logs:
        raw = program_data_bytes(ln)
        if raw is not None and raw[:8] == POST_COMPLETE_BUY_DISC:
            hits.append(raw)
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
    if any("Log truncated" in ln for ln in logs):
        return {"cls": "unclassified", "why": "log_truncated"}
    return {"cls": "normal"}


def logs_from_response(resp: Any) -> Any:
    """Read result.meta.err and result.meta.logMessages and nothing else. Returns the list, or None when the tx is missing or failed."""
    if not isinstance(resp, dict):
        return None
    res = resp.get("result")
    if not isinstance(res, dict):
        return None
    meta = res.get("meta")
    if not isinstance(meta, dict) or meta.get("err") is not None:
        return None
    logs = meta.get("logMessages")
    return logs if isinstance(logs, list) else None


def check_rpc_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.query or u.username or u.password or u.path not in ("", "/"):
        raise SystemExit("refused: rpc url must be https, host only (no path, query or userinfo)")
    if "helius" in u.hostname:
        raise SystemExit("refused: no Helius endpoint (0 credits by rule)")
    return url


def make_rpc_fetch(url: str, *, min_interval_s: float = 0.25, retries: int = 3, timeout_s: float = 20.0) -> Callable[[str], Any]:
    """fetch(signature) -> log list or None. One getTransaction per signature; the whole response is dropped except the logs."""
    check_rpc_url(url)
    last = [0.0]

    def fetch(sig: str) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                           "params": [sig, {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}]}).encode()
        for attempt in range(retries):
            wait = min_interval_s - (time.monotonic() - last[0])
            if wait > 0:
                time.sleep(wait)
            last[0] = time.monotonic()
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310 - https host checked above
                    resp = json.loads(r.read())
            except (urllib.error.URLError, TimeoutError, ValueError, OSError):
                time.sleep(1.0 + attempt)
                continue
            if isinstance(resp, dict) and isinstance(resp.get("error"), dict):
                time.sleep(1.0 + attempt)
                continue
            return logs_from_response(resp)
        return None

    return fetch


# ---- tape side ----------------------------------------------------------------------------------------------
def scan_day_coverage_and_completions(files: Iterable[Path], day: date) -> dict[str, Any]:
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


def count_day(day: date, tape_dir: Path, fetch: Callable[[str], Any], *, lookback_days: int = 2,
              tape_floor: date = TAPE_FLOOR_DAY) -> dict[str, Any]:
    """Counts for one UTC day. Returns the public record (counts only) plus `_signers` / `_quotes` for pooling (stripped before printing)."""
    if day < tape_floor:
        raise SystemExit(f"refused: {day} is before the tape floor {tape_floor}")
    files, missing = hour_files(tape_dir, day)
    rec: dict[str, Any] = {"day": day.isoformat(), "missing_hours": missing}
    cov = scan_day_coverage_and_completions(files, day)
    rec["bad_hours"] = cov["bad_hours"]
    mayhem: set[str] = set()
    normal: set[str] = set()
    for back in range(lookback_days, -1, -1):
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
        c = classify_logs(fetch(sig)) if fetch is not None else {"cls": "unclassified", "why": "no_fetch"}
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


def verify_one_tx(sig: str, fetch: Callable[[str], Any]) -> dict[str, Any]:
    """R2 one-tx check: print only the class, quote_in and base_out (raw units) of one public completion tx."""
    c = classify_logs(fetch(sig))
    return {k: c[k] for k in ("cls", "quote_in", "base_out", "why") if k in c}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--days", help="comma list of closed UTC days, YYYY-MM-DD")
    ap.add_argument("--rpc-url", default=DEFAULT_RPC)
    ap.add_argument("--out", help="write the counts JSON here")
    ap.add_argument("--verify-tx", help="one public completion signature: print class, quote_in, base_out only")
    a = ap.parse_args(argv)
    fetch = make_rpc_fetch(a.rpc_url)
    if a.verify_tx:
        print(json.dumps(verify_one_tx(a.verify_tx, fetch)))
        return 0
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
