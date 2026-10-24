"""Historical Raydium LaunchLab trade tape from Helius RPC (paper/research only; read-only RPC key file, no wallet, no sends).

Output: <out>/launchlab/tape-<YYYY-MM-DDTHH>.jsonl.zst, hourly, SAME row schema as tools/venue_tape.py (the decoder is reused:
rows_for_venue). Differences, all marked on the rows: `src`="backfill", `t_recv_ms` = blockTime*1000 (second resolution, not a receive
time), `block_time`. Each hour file starts with one `backfill_hour` row (txs, pages, credits, status filter) and ends with a `backfill_hour_end`
row; an hour that could not be fetched is a file holding one `gap` row (reason `backfill_incomplete`). Failed transactions are NOT fetched
(status filter `succeeded`), so `failed` rows are absent by design; the hour row says so.

Method (measured in PLAN.md): Helius getTransactionsForAddress (full transactions with logs, blockTime window, asc, paginated) on the program.
Credits are counted per call at --credits-per-call (set from the probe) and a HARD cap stops the run (exit 5) before any call that would exceed it.
State: <out>/checkpoint.json (hours done, credits). Resumable: a rerun skips done hours. One Helius flock slot is held (LOCKDIR), rps <= 10.
The key is read from the env file and never printed or logged.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from tools import venue_tape as vt

PROGRAM = vt.VENUES["launchlab"]["program"]
VENUE = "launchlab"
HELIUS_ENV = "/var/lib/mal/backfill/helius.env"
LOCKDIR = "/data/mal/locks"
DEFAULT_OUT = "/data2/mal/venue-tape-backfill"
HELIUS_HTTP = "https://mainnet.helius-rpc.com"
MAX_RPS = 10.0
EXIT_CAP = 5
EXIT_ENV = 6
_KEY_RE = re.compile(r"(api-key=)[^&\s\"']+", re.IGNORECASE)


class CapReached(RuntimeError):
    pass


class Credits:
    """Hard cap. charge() is called BEFORE the request; a call that would pass the cap raises and is never sent."""

    def __init__(self, cap: int, per_call: int, used: int = 0) -> None:
        self.cap, self.per_call, self.used = int(cap), int(per_call), int(used)

    def charge(self, n: int | None = None) -> None:
        n = self.per_call if n is None else n
        if self.used + n > self.cap:
            raise CapReached(f"credit cap {self.cap} reached at {self.used}")
        self.used += n


def hour_floor(ts: int) -> int:
    return ts - ts % 3600


def hour_label(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def parse_hour(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def hours_between(start: int, end: int) -> list[int]:
    return list(range(hour_floor(start), end, 3600))


def tx_notice(tx: dict[str, Any]) -> Any:
    """getTransaction / getTransactionsForAddress item -> the RawNotice-like object rows_for_venue reads. None if unusable."""
    meta = tx.get("meta") or {}
    sigs = (tx.get("transaction") or {}).get("signatures") or []
    bt = tx.get("blockTime")
    logs = meta.get("logMessages")
    if not sigs or bt is None or logs is None:
        return None
    return SimpleNamespace(slot=int(tx.get("slot") or 0), t_recv_ms=int(bt) * 1000, signature=sigs[0], logs=logs,
                           failed=meta.get("err") is not None)


def rows_from_tx(tx: dict[str, Any]) -> list[dict[str, Any]]:
    n = tx_notice(tx)
    if n is None:
        return [{"v": vt.SCHEMA_V, "venue": VENUE, "type": "tx_unusable", "src": "backfill",
                 "signature": ((tx.get("transaction") or {}).get("signatures") or [None])[0], "slot": int(tx.get("slot") or 0),
                 "t_recv_ms": int(tx.get("blockTime") or 0) * 1000, "event_index": 0,
                 "reason": "no_logs" if (tx.get("meta") or {}).get("logMessages") is None else "no_signature_or_time"}]
    rows = vt.rows_for_venue(VENUE, n)
    for r in rows:
        r["src"] = "backfill"
        r["block_time"] = n.t_recv_ms // 1000
    return rows


def sort_key(r: dict[str, Any]) -> tuple:
    return (r.get("slot", 0), r.get("signature") or "", r.get("event_index") or 0, r.get("type", ""))


# ------------------------------------------------------------------------------------------------------------------------ RPC
def make_url(key: str) -> str:
    key = key.strip()
    if not key or any(c in key for c in "\r\n& #"):
        raise ValueError("empty or unsafe key")
    return f"{HELIUS_HTTP}/?api-key={key}"


def read_key(path: str) -> str:
    """Reads KEY=VALUE lines, returns the Helius key. Never prints. Raises ValueError (no key text in the message)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise ValueError("env file unreadable") from None
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("export "):
            ln = ln[7:]
        if ln.startswith("HELIUS_API_KEY="):
            v = ln.split("=", 1)[1].strip().strip("'\"")
            if v:
                return v
    raise ValueError("env file has no HELIUS_API_KEY")


class Pacer:
    def __init__(self, rps: float, clock=time.monotonic, sleep=time.sleep) -> None:
        if rps <= 0 or rps > MAX_RPS:
            raise ValueError(f"rps must be in (0, {MAX_RPS}]")
        self.iv, self._next, self._clock, self._sleep = 1.0 / rps, 0.0, clock, sleep

    def wait(self) -> None:
        now = self._clock()
        if self._next > now:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self.iv


def http_rpc(url: str, pacer: Pacer, timeout: float = 90.0) -> Callable[[str, list[Any]], Any]:
    import gzip
    import urllib.error
    import urllib.request

    def call(method: str, params: list[Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last = "?"
        for attempt in range(6):
            pacer.wait()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                payload = json.loads(raw)
            except urllib.error.HTTPError as exc:
                last = f"http {exc.code}"
                if exc.code == 429 or exc.code >= 500:
                    time.sleep(0.5 * 2**attempt)
                    continue
                raise RuntimeError(f"{method} http {exc.code}") from None
            except (TimeoutError, urllib.error.URLError, ConnectionError, json.JSONDecodeError, EOFError, OSError) as exc:
                last = type(exc).__name__
                time.sleep(0.5 * 2**attempt)
                continue
            if "error" in payload:
                e = payload["error"] or {}
                last = f"rpc {e.get('code')}"
                if e.get("code") == 429:
                    time.sleep(0.5 * 2**attempt)
                    continue
                msg = _KEY_RE.sub("api-key=X", str(e.get("message"))[:300])
                raise RuntimeError(f"{method} rpc {e.get('code')} {msg}")
            return payload.get("result")
        raise RuntimeError(f"{method} gave up ({last})")

    return call


def gtfa_params(t0: int, t1: int, token: str | None, limit: int) -> list[Any]:
    opts: dict[str, Any] = {"transactionDetails": "full", "sortOrder": "asc", "limit": limit, "encoding": "json",
                            "maxSupportedTransactionVersion": 1,
                            "filters": {"blockTime": {"gte": t0, "lt": t1}, "status": "succeeded"}}
    if token:
        opts["paginationToken"] = token
    return [PROGRAM, opts]


def fetch_hour(call: Callable[[str, list[Any]], Any], credits: Credits, t0: int, limit: int = 100) -> tuple[list[tuple[Any, Any, list[dict[str, Any]]]], int]:
    """All succeeded program transactions with blockTime in [t0, t0+3600), converted to rows as each page arrives (memory: rows, not raw txs).
    Returns ([(signature, blockTime, rows)], pages). Raises CapReached before an unaffordable call."""
    items: list[tuple[Any, Any, list[dict[str, Any]]]] = []
    token: str | None = None
    pages = 0
    while True:
        credits.charge()
        res = call("getTransactionsForAddress", gtfa_params(t0, t0 + 3600, token, limit))
        pages += 1
        data = (res or {}).get("data") or []
        for tx in data:
            items.append((((tx.get("transaction") or {}).get("signatures") or [None])[0], tx.get("blockTime"), rows_from_tx(tx)))
        token = (res or {}).get("paginationToken")
        if not token or not data:
            return items, pages


# ---------------------------------------------------------------------------------------------------------------------- output
def build_hour_rows(t0: int, items: list[tuple[Any, Any, list[dict[str, Any]]]], pages: int, credits_spent: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    dup = 0
    for sig, bt, trs in items:
        if sig in seen or (bt is not None and not (t0 <= int(bt) < t0 + 3600)):
            dup += 1  # repeated across pages, or outside the hour: ignored and counted
            continue
        seen.add(sig)
        rows.extend(trs)
    rows.sort(key=sort_key)
    head = {"v": vt.SCHEMA_V, "venue": VENUE, "type": "backfill_hour", "src": "backfill", "slot": 0, "t_recv_ms": t0 * 1000, "signature": None,
            "event_index": None, "hour": hour_label(t0), "txs": len(seen), "dropped_dup_or_outside": dup, "pages": pages,
            "credits": credits_spent, "status_filter": "succeeded", "failed_txs_absent": True, "program": PROGRAM}
    tail = {**head, "type": "backfill_hour_end", "rows": len(rows)}
    return [head, *rows, tail]


def gap_rows(t0: int, reason: str) -> list[dict[str, Any]]:
    return [{"v": vt.SCHEMA_V, "venue": VENUE, "type": "gap", "src": "backfill", "slot": 0, "t_recv_ms": t0 * 1000, "signature": None,
             "event_index": None, "reason": "backfill_incomplete", "detail": reason[:120], "hour": hour_label(t0)}]


def write_hour(out: Path, t0: int, rows: list[dict[str, Any]], compact: Callable[[Path], Any] | None) -> Path:
    d = out / VENUE
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"tape-{hour_label(t0)}.jsonl"
    tmp = p.with_suffix(".jsonl.part")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":"), allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    if compact is not None:
        compact(p)
        return Path(str(p) + ".zst")
    return p


def load_cp(path: Path) -> dict[str, Any]:
    if path.is_file():
        d = json.loads(path.read_text(encoding="utf-8"))
        d.setdefault("hours", {})
        d.setdefault("credits_used", 0)
        return d
    return {"version": 1, "credits_used": 0, "hours": {}}


def save_cp(path: Path, cp: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run_hours(out: Path, hours: list[int], call: Callable[[str, list[Any]], Any], credits: Credits, compact: Callable[[Path], Any] | None,
              progress: Callable[[str], None] = lambda s: None, max_tries: int = 3) -> int:
    """Newest hour first is NOT used: oldest first, so a stop leaves a contiguous prefix. Returns 0, or EXIT_CAP."""
    out.mkdir(parents=True, exist_ok=True)
    cpf = out / "checkpoint.json"
    cp = load_cp(cpf)
    credits.used = max(credits.used, int(cp["credits_used"]))
    tries: dict[int, int] = {}
    for t0 in hours:
        lab = hour_label(t0)
        if lab in cp["hours"]:
            continue
        while True:
            before = credits.used
            try:
                items, pages = fetch_hour(call, credits, t0)
            except CapReached:
                cp["credits_used"] = credits.used
                save_cp(cpf, cp)
                progress(f"credit cap reached before {lab}")
                return EXIT_CAP
            except RuntimeError as exc:
                tries[t0] = tries.get(t0, 0) + 1
                cp["credits_used"] = credits.used
                if tries[t0] >= max_tries:
                    write_hour(out, t0, gap_rows(t0, str(exc)), compact)
                    cp["hours"][lab] = {"gap": True, "credits": credits.used - before}
                    save_cp(cpf, cp)
                    break
                save_cp(cpf, cp)
                time.sleep(2.0)
                continue
            spent = credits.used - before
            rows = build_hour_rows(t0, items, pages, spent)
            write_hour(out, t0, rows, compact)
            cp["hours"][lab] = {"txs": len(items), "pages": pages, "rows": len(rows) - 2, "credits": spent}
            cp["credits_used"] = credits.used
            save_cp(cpf, cp)
            progress(f"{lab} txs={len(items)} credits={credits.used}")
            break
    return 0


def probe(call: Callable[[str, list[Any]], Any], credits: Credits, t0: int, limit: int, pages: int) -> dict[str, Any]:
    """Dry run: up to `pages` gTFA pages of one hour; reports txs, decoded counts and credits at the assumed per-call cost. Writes nothing."""
    token = None
    txs: list[dict[str, Any]] = []
    n = 0
    for _ in range(pages):
        credits.charge()
        res = call("getTransactionsForAddress", gtfa_params(t0, t0 + 3600, token, limit))
        n += 1
        txs.extend((res or {}).get("data") or [])
        token = (res or {}).get("paginationToken")
        if not token:
            break
    types: dict[str, int] = {}
    for tx in txs:
        for r in rows_from_tx(tx):
            types[r["type"]] = types.get(r["type"], 0) + 1
    bts = [t.get("blockTime") for t in txs if t.get("blockTime")]
    return {"hour": hour_label(t0), "pages": n, "txs": len(txs), "row_types": types, "credits_assumed": credits.used,
            "blocktime_span_s": (max(bts) - min(bts)) if bts else None, "more_pages": bool(token)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LaunchLab historical trade tape from Helius (read-only RPC key; paper only)")
    ap.add_argument("mode", choices=["probe", "run"])
    ap.add_argument("--start", help="first hour UTC, YYYY-MM-DDTHH (run), or the hour to probe")
    ap.add_argument("--end", help="end hour UTC, exclusive (run)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--env-file", default=HELIUS_ENV)
    ap.add_argument("--lockdir", default=LOCKDIR)
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--credit-cap", type=int, default=3_000_000)
    ap.add_argument("--credits-per-call", type=int, default=10, help="assumed credits per gTFA page; set from the probe/docs")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--probe-pages", type=int, default=2)
    ap.add_argument("--no-compress", action="store_true")
    a = ap.parse_args(argv)
    if not a.start:
        print("--start required", file=sys.stderr)
        return 2
    try:
        key = read_key(a.env_file)
        url = make_url(key)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_ENV
    # one Helius flock slot (blocking until one is free), held for the whole run
    held = None
    Path(a.lockdir).mkdir(parents=True, exist_ok=True)
    while held is None:
        for i in (4, 3, 2, 1):
            fh = open(Path(a.lockdir) / f"helius-{i}.lock", "a")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = fh
                print(f"holding helius slot {i}", flush=True)
                break
            except OSError:
                fh.close()
        if held is None:
            time.sleep(15)
    call = http_rpc(url, Pacer(a.rps))
    credits = Credits(a.credit_cap, a.credits_per_call)
    t0 = parse_hour(a.start)
    if a.mode == "probe":
        print(json.dumps(probe(call, credits, t0, a.limit, a.probe_pages), sort_keys=True))
        return 0
    if not a.end:
        print("--end required", file=sys.stderr)
        return 2
    from tools.arena_zst import compact_file, zstd_path
    if not a.no_compress and zstd_path() is None:
        print("zstd CLI missing", file=sys.stderr)
        return 4
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prog = os.environ.get("MISCUSI_PROGRESS")

    def progress(s: str) -> None:
        print(s, flush=True)
        if prog:
            try:
                Path(prog).write_text(json.dumps({"note": s, "credits_used": credits.used}) + "\n")
            except OSError:
                pass

    rc = run_hours(out, hours_between(t0, parse_hour(a.end)), call, credits, None if a.no_compress else compact_file, progress)
    progress(f"done rc={rc} credits_used={credits.used}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
