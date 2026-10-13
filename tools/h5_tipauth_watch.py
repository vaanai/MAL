#!/usr/bin/env python3
"""H5 tipauth WATCH: the offline check that must run before the executor's OFF-by-default `tipauth_gate` path may ever be enabled
(quant-proof, PR #614 comment 6121641942, section 3 (c) "Ruling D item 3, the offline check", with R4 (how the executor reads state.json), R12 (one
predicate) and section 6; comment 6121865997 section 3: the watcher imports `would_pass`, it does not write a third copy).

THIS FILE IS A SAFETY DEVICE FOR REAL MONEY. Every doubt resolves to `hold` or `off`, never to `ok`. The executor reads `state.json` and refuses the
path unless it says `ok`, is fresh (`now - ts_ms` in [0, 180 s]), names the pinned shadow sha and has schema `h5_tipauth_watch_v1`.

  python3 -m tools.h5_tipauth_watch --shadow-dir D --tip-dir D --out-dir D/tipauth-watch --shadow-sha SHA40 --from-ms T --rpc-url https://api.mainnet-beta.solana.com
  python3 -m tools.h5_tipauth_watch --out-dir O --force-off "reason"     (manager: writes OFF, never removed)
  python3 -m tools.h5_tipauth_watch --out-dir O --hold | --release        (manager: create / remove the manual HOLD marker)

WHAT IT READS (nothing else): the shadow's hour files h5-shadow-<UTC hour>.jsonl (records `start`, `trigger`, `pool` close; `hb` and `gap` lines are
skipped unread), only decision-time and bookkeeping keys of them; the tip tape's trades-<UTC hour>.jsonl (only lines holding a watched signature, plus
the last complete row of the newest hour for the tip high-water); the public RPC at --rpc-url (getTransaction, finalized; no key). It never reads the
executor ledger, an outcome, a fill or a P&L, and writes none. Standard library only.

WATCHED SET W: every unsealed pv `trigger` record with t_detect_ms >= --from-ms for which `tools.h5_tipauth_item4.would_pass` is true (the one shared
predicate; that file is not edited here). A superset of the fills, so stricter than "a tagged fill" and it needs no ledger.

OFF EVENTS (n = 1; each appended to events.jsonl with its evidence; the first writes the OFF marker):
  1. trigger_print_not_on_tip   once the tip high-water minus 4 (TIPAUTH_MARGIN_SLOTS) is at or above the trigger slot: the tip has no row for the
                                trigger's (pool, signature, event_index), or a row's pre-trade base / quote differs from the record's base_pre /
                                real_quote_pre. Matching follows tools/h5_tipauth_trigger_print.py `_outcome` (exact integer equality; every row with the
                                key must be equal; hours: previous, own, next two, from the trigger's t_ms, `wanted_hours`).
  2. dropped_on_tip             an entry of the trigger's `dropped` whose signature gets a tip row in that pool (any event_index), checked every cycle
                                until the pool's close record, then by item 10's rule on that close record ("left D"): tools/h5_tipauth_item4.py
                                `classify_margin` (first close of the pool with t_ms >= the trigger's; usable only with base_tipauth.status ok and
                                integer cover_hi >= the trigger slot; in the close's list -> still dropped; not in it and (close dropped_n <= 20 or the
                                entry's slot < the close list's last slot) -> left D; otherwise undetermined).
  3. dropped_on_chain           getTransaction (finalized) finds a signature of `dropped`. Not found is asked again at least 10 minutes later; two
                                not-founds (at least 10 minutes apart) clear it; an error, a timeout or a malformed answer is NEVER a not-found
                                (the asking pattern is tools/h5_tipauth_census.py `interpret` / `classify`).
HOLD (not off): no definitive RPC answer for a signature 60 minutes after its trigger; the tip not past a watched trigger 10 minutes after it; the
newest `start` record's code_sha (newest first, at most 400 hour files, as scripts/mal-fast/h5-daily-check.py does) is not --shadow-sha; a manual HOLD
marker. The doubts the spec leaves open also hold (see INTERPRETATIONS below).

OUTPUT in --out-dir: state.json = {"schema": "h5_tipauth_watch_v1", "ts_ms", "verdict": "ok" | "hold" | "off", "reason", "shadow_sha" (the sha observed in
the newest start record, null if none), "watched_n", "clear_n", "pending_n"}, rewritten by atomic rename EVERY cycle (30 s), RPC or not, also when a
phase raises (then verdict hold). OFF (marker, written at the first off event, never removed by this tool; while it exists every cycle writes off), HOLD
(manual), events.jsonl (append only), rpc_log.jsonl and resolved.jsonl (append only; the restart state), lock (flock: one watch per out-dir).
RESUME: a restart rebuilds W from the hour files, the RPC answers and the cleared checks from the files above; OFF survives (it is also recreated from
an off row of events.jsonl if the marker file was lost).

INTERPRETATIONS (the spec leaves these open; each is the reading that produces hold over ok):
  I1  The tip high-water is the slot of the last complete row of the newest tape hour file (now's hour, else the previous hour), read each cycle before
      the scan and never cached: a lower bound of the follower's, never later than the tape allows (rows are committed strictly in slot order).
  I2  `ok` needs the shadow reader caught up to the end of every complete line of every hour file at or after (hour of --from-ms - 1 h). Otherwise hold
      `shadow_read_incomplete` (a missing shadow directory, an unreadable file, a backlog after a restart).
  I3  A complete shadow line that does not parse and mentions "trigger" holds (`bad_trigger_line`): a trigger could be hidden in it.
  I4  A watched trigger whose print cannot be matched (pool / signature / event_index / base_pre / real_quote_pre unusable, or a signature that is not
      alphanumeric, which the byte search cannot match safely) holds, for good (`trigger_unverifiable`).
  I5  Event 1 is final only on a COMPLETE scan: the own-hour tape file exists and every wanted file opened and was scanned to its end this cycle. A row that
      differs fires at once; "no row" and "all equal" need the complete scan. A tape check blocked for more than 10 minutes while the tip is past the
      trigger holds (`trigger_print_unverified`).
  I6  A trigger whose event 2 is unresolved (no usable close, or all entries not classified) 60 minutes after its t_detect_ms holds (`event2_unresolved_60m`);
      the tape check for it also continues for 2 hours after the close. t_detect_ms not an integer: t_ms is used; neither: the trigger counts as old.
  I7  Ages are `now - t_detect_ms` of the record, not the instant the watch first saw it, so a restart never gives a trigger more time.
  I8  Dropped entries without an integer event_index (None) are matched by signature and pool only.
  I9  A tip directory or shadow directory that is not a directory holds.
  I10 --rpc-url must be https with no user info, query, fragment or path, and no "helius" in it: a keyed endpoint is refused before the first cycle.
  I11 `pending_n` = watched_n - clear_n. A trigger is clear only when event 1 is clear, event 2 is clear and every dropped signature is not-found-twice.
"""
from __future__ import annotations

if __package__ in (None, ""):  # `python tools/h5_tipauth_watch.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import calendar
import fcntl
import json
import mmap
import os
import re
import signal
import stat as stat_mod
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from tools.h5_tipauth_item4 import DROPPED_LIST_MAX, TIPAUTH_MARGIN_SLOTS, entry_ok, flag, isint, would_pass

TOOL = "h5_tipauth_watch"
SCHEMA = "h5_tipauth_watch_v1"
STATE_KEYS = ("schema", "ts_ms", "verdict", "reason", "shadow_sha", "watched_n", "clear_n", "pending_n")
HOUR_MS = 3_600_000
CYCLE_S = 30.0  # a cycle starts every 30 s (start to start)
# Every phase has a deadline, so a cycle ends within SHADOW + START + TAPE + RPC budgets + one RPC call (at most RPC_TIMEOUT_S + RPC_DEADLINE_GRACE_S, enforced by a
# worker thread, not by the transport) + one pacing sleep + a chunk of disk work (about 25 s). A cycle that follows a fast one is then at most 30 + 25 s after the previous
# write: state.json is never more than 60 s apart (a test pins the sum, and a test runs a real slow socket).
SHADOW_BUDGET_MS, START_BUDGET_MS, TAPE_BUDGET_MS, RPC_BUDGET_MS = 6_000, 3_000, 5_000, 5_000
RPC_TIMEOUT_S = 4.0  # the transport's total budget for one call (connect plus every read), and the per-operation socket timeout
RPC_DEADLINE_GRACE_S = 1.0  # the worker thread running a call is waited for RPC_TIMEOUT_S + this; past it the call is an error and is never waited for again
RPC_BODY_MAX = 8 * 1024 * 1024
RPC_PACE_S = 0.5  # at most 2 asks a second
RPC_MAX_ASKS = 10  # per cycle
RPC_REASK_MS = 600_000  # a not-found is asked again at least 10 minutes later
RPC_ERROR_RETRY_MS = 20_000
RPC_HOLD_MS = 3_600_000  # no definitive answer after 60 minutes: hold
TIP_HOLD_MS = 600_000  # the tip not past a watched trigger after 10 minutes: hold
E2_HOLD_MS = 3_600_000
E2_TAPE_MAX_MS = 2 * HOUR_MS
START_SCAN_FILES = 400  # as the daily check: newest hour files searched for a start record
START_LINE_MAX = 1_000_000
READ_CHUNK = 4 * 1024 * 1024
SCAN_CHUNK = 32 * 1024 * 1024
TIP_TAIL_BYTES = 262_144
TIP_HOURS_BEFORE, TIP_HOURS_AHEAD = 1, 2  # as tools/h5_tipauth_trigger_print.py
COMPRESSED = (".gz", ".zst", ".xz", ".bz2")
SHADOW_RE = re.compile(r"^h5-shadow-(\d{4})-(\d{2})-(\d{2})T(\d{2})\.jsonl$")
SIG_SAFE = re.compile(r"[A-Za-z0-9]{1,100}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
SKIP_PREFIXES = (b'{"type":"hb"', b'{"type": "hb"', b'{"type":"gap"', b'{"type": "gap"')
POOL_PREFIXES = (b'{"type":"pool"', b'{"type": "pool"')
START_MARKS = (b'{"type":"start"', b'{"type": "start"')
_UNREADABLE = object()
E1, E2, E3 = "trigger_print_not_on_tip", "dropped_on_tip", "dropped_on_chain"
FILES = {"state": "state.json", "off": "OFF", "hold": "HOLD", "events": "events.jsonl", "rpc": "rpc_log.jsonl", "resolved": "resolved.jsonl", "lock": "lock"}


# ---- small helpers ----------------------------------------------------------------------------------------------------------------------------

def hour_name(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ms // 1000))


def wanted_hours(t_ms: int) -> list[str]:
    """The tip tape hours searched for a trigger: the previous hour, its own and the next two (trigger_print.wanted_hours)."""
    return [hour_name(t_ms + k * HOUR_MS) for k in range(-TIP_HOURS_BEFORE, TIP_HOURS_AHEAD + 1)]


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def append_jsonl(path: Path, row: dict) -> bool:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_jsonl(path: Path) -> list[dict]:
    """Complete, parseable object lines only (a torn or foreign line is skipped)."""
    out: list[dict] = []
    try:
        data = path.read_bytes()
    except OSError:
        return out
    for raw in data.split(b"\n")[:-1] if data and not data.endswith(b"\n") else data.split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            r = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def validate_rpc_url(url: str) -> str | None:
    """None if --rpc-url is a plain public endpoint, else the refusal (I10): https, host only, no user info / query / fragment / path, no helius."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return "unparseable"
    if u.scheme != "https" or not u.hostname:
        return "not https"
    if u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
        return "has user info, path, query or fragment (a keyed endpoint?)"
    if "helius" in url.lower():
        return "helius endpoint (keyed)"
    return None


# ---- the public RPC (the census's pattern; tests inject a fake) -----------------------------------------------------------------------------------

def request_payload(sig: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [sig, {"commitment": "finalized", "maxSupportedTransactionVersion": 0, "encoding": "json"}]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:  # a redirect is an error, never followed
        return None


def http_transport(url: str, payload: dict, timeout: float) -> Any:
    """One getTransaction call with a TOTAL deadline of `timeout` seconds (R-B): the socket timeout is per operation, so the body is read in chunks against a monotonic
    deadline and a size cap. A server that trickles bytes ends in TimeoutError, never in an answer. (The caller also runs this in a thread it stops waiting for.)"""
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "User-Agent": "mal-h5-tipauth-watch/1"})
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 (https only, checked by validate_rpc_url)
        buf = bytearray()
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("rpc total deadline")
            chunk = resp.read1(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > RPC_BODY_MAX:
                raise ValueError("rpc answer too large")
        if time.monotonic() > deadline:
            raise TimeoutError("rpc total deadline")
    return json.loads(bytes(buf).decode("utf-8"))


def interpret(resp: Any) -> dict:
    """found / null / error (h5_tipauth_census.interpret). An error, a timeout, a malformed answer is NEVER a null."""
    if not isinstance(resp, dict) or "error" in resp or "result" not in resp:
        return {"result": "error", "err": "rpc_error" if isinstance(resp, dict) and "error" in resp else "malformed"}
    r = resp["result"]
    if r is None:
        return {"result": "null"}
    if isinstance(r, dict) and isint(r.get("slot")):
        return {"result": "found", "slot": r["slot"]}
    return {"result": "error", "err": "malformed_result"}


def rpc_class(answers: list[dict]) -> str:
    """none | found | not_found | pending | error (h5_tipauth_census.classify, plus `none` for never asked)."""
    if not answers:
        return "none"
    if any(a.get("result") == "found" for a in answers):
        return "found"
    nulls = sorted(a["asked_ms"] for a in answers if a.get("result") == "null" and isint(a.get("asked_ms")))
    if len(nulls) >= 2 and nulls[-1] - nulls[0] >= RPC_REASK_MS:
        return "not_found"
    return "pending" if nulls else "error"


# ---- tip tape ---------------------------------------------------------------------------------------------------------------------------------

def open_regular(path: Path) -> Any:
    """Open read-only WITHOUT blocking, and only a regular file (a FIFO would block the whole cycle, a device would lie). Raises OSError otherwise."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat_mod.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"not a regular file: {path.name}")
        return os.fdopen(fd, "rb")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def last_complete_row_slot(path: Path) -> int | None:
    """The slot of the last complete, parseable row of a tape hour file, from its last TIP_TAIL_BYTES. None if there is none."""
    try:
        with open_regular(path) as fh:
            size = fh.seek(0, 2)
            if size == 0:
                return None
            start = max(0, size - TIP_TAIL_BYTES)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return None
    if start > 0:
        nl = data.find(b"\n")
        if nl < 0:
            return None
        data = data[nl + 1:]
    cut = data.rfind(b"\n")
    if cut < 0:
        return None
    for raw in reversed(data[:cut].split(b"\n")[-50:]):
        try:
            row = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict) and isint(row.get("slot")):
            return row["slot"]
    return None


def scan_tape_file(path: Path, sigs: list[str], deadline_ms: int, now_ms: Callable[[], int]) -> tuple[dict[str, list[dict]], str, set[str]]:
    """One pass over one tape hour file for all wanted signatures (mmap, a byte search, a JSON parse only of a line holding one: the approach of
    trigger_print._scan_file). Returns (rows by signature, status, signatures whose only match was a partial last line). status: ok | incomplete
    (deadline) | unreadable. The file is opened read-only; the size is taken once and nothing beyond it is read."""
    rows: dict[str, list[dict]] = {}
    partial: set[str] = set()
    try:
        with open_regular(path) as fh:
            size = os.fstat(fh.fileno()).st_size
            if size == 0:
                return rows, "ok", partial
            mm = mmap.mmap(fh.fileno(), size, access=mmap.ACCESS_READ)
    except (OSError, ValueError):
        return rows, "unreadable", partial
    try:
        rx = re.compile(b'"signature"\\s*:\\s*"(' + b"|".join(re.escape(s.encode("ascii")) for s in sorted(sigs, key=len, reverse=True)) + b')"')
        wanted = set(sigs)
        seen: set[int] = set()
        pos = 0
        while pos < size:
            if now_ms() > deadline_ms:
                return rows, "incomplete", partial
            end = min(size, pos + SCAN_CHUNK)
            if end < size:
                nl = mm.find(b"\n", end)
                end = size if nl < 0 else nl + 1
            for m in rx.finditer(mm, pos, end):
                s = mm.rfind(b"\n", 0, m.start()) + 1
                if s in seen:
                    continue
                seen.add(s)
                e = mm.find(b"\n", m.end())
                if e < 0:
                    partial.add(m.group(1).decode("ascii"))
                    continue
                try:
                    row = json.loads(mm[s:e])
                except (ValueError, RecursionError):
                    continue
                own = row.get("signature") if isinstance(row, dict) else None
                if isinstance(own, str) and own in wanted:
                    rows.setdefault(own, []).append({"pool": row.get("pool"), "event_index": row.get("event_index"), "slot": row.get("slot"),
                                                     "base_reserve": row.get("base_reserve"), "quote_reserve": row.get("quote_reserve")})
            pos = end
        return rows, "ok", partial
    finally:
        mm.close()


# ---- the watch --------------------------------------------------------------------------------------------------------------------------------

class Watch:
    def __init__(self, shadow_dir: Path, tip_dir: Path, out_dir: Path, shadow_sha: str, from_ms: int, rpc_url: str, *,
                 rpc: Callable[[str, dict, float], Any] = http_transport, now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] | None = None) -> None:
        self.shadow_dir, self.tip_dir, self.out_dir = Path(shadow_dir), Path(tip_dir), Path(out_dir)
        self.shadow_sha, self.from_ms, self.rpc_url = shadow_sha, from_ms, rpc_url
        self.rpc, self.now, self.sleep, self.log = rpc, now_ms, sleep, log or (lambda m: None)
        self.p = {k: self.out_dir / v for k, v in FILES.items()}
        self.trig: dict[str, dict] = {}  # W, by tid
        self.pool_tids: dict[str, list[str]] = {}
        self.pool_bytes: list[bytes] = []  # the watched pool addresses; a pool-close line with none of them in it is not parsed (a watched pool's close always holds its address)
        self.closes: dict[str, list[dict]] = {}
        self.offsets: dict[str, int] = {}
        self.bad_trigger_lines = 0
        self.start_cache: dict[str, tuple[int, int, dict | None, bool, bool]] = {}
        self.resolved: dict[str, set[str]] = {"e1": set(), "e2": set()}
        self.rpc_rows: dict[str, list[dict]] = {}
        self.event_keys: set[str] = set()
        self.off_info: dict | None = None
        self.last_error: list[str] = []
        self.observed_sha: str | None = None
        self.caught_up = False
        self.start_complete = False
        self.start_unreadable = False
        self.hw: int | None = None
        self._rpc_thread: threading.Thread | None = None  # the one outstanding RPC call, if a past one has not returned
        self._load()

    # -- persisted state --
    def _load(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for r in read_jsonl(self.p["resolved"]):
            if r.get("kind") in ("e1", "e2") and isinstance(r.get("tid"), str):
                self.resolved[r["kind"]].add(r["tid"])
        for r in read_jsonl(self.p["rpc"]):
            if isinstance(r.get("sig"), str) and r.get("result") in ("found", "null", "error") and isint(r.get("asked_ms")):
                self.rpc_rows.setdefault(r["sig"], []).append(r)
        off_rows = []
        for r in read_jsonl(self.p["events"]):
            if isinstance(r.get("dedupe"), str):
                self.event_keys.add(r["dedupe"])
            if r.get("off") is True:
                off_rows.append(r)
        if os.path.lexists(self.p["off"]):
            self.off_info = self._read_off()
        elif off_rows:  # the marker was lost but the log remembers: OFF survives
            self.off_info = {"ts_ms": off_rows[0].get("ts_ms"), "event": off_rows[0].get("event"), "dedupe": off_rows[0].get("dedupe"), "recreated": True}
            self._ensure_off()

    def _read_off(self) -> dict:
        try:
            d = json.loads(self.p["off"].read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except (OSError, ValueError):
            pass
        return {"event": "marker"}

    def _ensure_off(self) -> None:
        """Create the OFF marker if it does not exist (exclusive create; never overwritten, never removed here)."""
        if os.path.lexists(self.p["off"]) or self.off_info is None:
            return
        try:
            fd = os.open(self.p["off"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return
        except OSError:
            return  # retried next cycle; the in-memory off_info already makes the verdict off
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(self.off_info, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def off_event(self, name: str, key: str, evidence: dict) -> None:
        """An off event: the OFF marker first, then the log row with its evidence (once per key, also across restarts)."""
        now = self.now()
        if self.off_info is None:
            self.off_info = {"ts_ms": now, "event": name, "dedupe": key}
        self._ensure_off()
        if key not in self.event_keys:
            if append_jsonl(self.p["events"], {"ts_ms": now, "off": True, "event": name, "dedupe": key, "evidence": evidence}):
                self.event_keys.add(key)

    def _mark(self, kind: str, tid: str) -> None:
        if tid not in self.resolved[kind]:
            self.resolved[kind].add(tid)
            append_jsonl(self.p["resolved"], {"kind": kind, "tid": tid, "ts_ms": self.now()})

    # -- the shadow's hour files --
    def _shadow_files(self) -> list[str]:
        out = []
        for e in os.scandir(self.shadow_dir):
            m = SHADOW_RE.match(e.name)
            if not m:
                continue
            y, mo, d, h = (int(g) for g in m.groups())
            if calendar.timegm((y, mo, d, h, 0, 0)) * 1000 + HOUR_MS <= self.from_ms - HOUR_MS:
                continue
            out.append(e.name)
        return sorted(out)

    def poll_shadow(self, deadline_ms: int) -> bool:
        """Read the new complete lines of every hour file at or after the hour of --from-ms. True only if every file was read to its last complete line."""
        try:
            names = self._shadow_files()
        except OSError:
            return False
        caught = True
        for name in names:
            path = self.shadow_dir / name
            try:
                with open_regular(path) as fh:
                    size = os.fstat(fh.fileno()).st_size
            except OSError:  # a FIFO, a directory, a vanished file: an unread file is a hold
                caught = False
                continue
            off = self.offsets.get(name, 0)
            if size < off:
                off = 0  # truncated or replaced: read again (W is keyed, nothing is counted twice)
            while off < size:
                if self.now() > deadline_ms:
                    caught = False
                    break
                try:
                    with open_regular(path) as fh:
                        fh.seek(off)
                        data = fh.read(min(size - off, READ_CHUNK))
                except OSError:
                    caught = False
                    break
                cut = data.rfind(b"\n")
                if cut < 0:
                    if off + len(data) < size:  # a line longer than a whole chunk: unreadable here, skip it and hold
                        self.bad_trigger_lines += 1
                        off += len(data)
                        continue
                    break  # a partial last line: wait for its newline
                for line in data[:cut].split(b"\n"):
                    self._line(line)
                off += cut + 1
            self.offsets[name] = off
        return caught

    def _line(self, line: bytes) -> None:
        line = line.strip()
        if not line or line.startswith(SKIP_PREFIXES):
            return
        if line.startswith(POOL_PREFIXES) and not any(b in line for b in self.pool_bytes):
            return
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            if b'"trigger"' in line:
                self.bad_trigger_lines += 1
            return
        if not isinstance(rec, dict):
            return
        typ = rec.get("type")
        if typ == "trigger":
            self._on_trigger(rec)
        elif typ == "pool":
            self._on_pool(rec)

    def _on_trigger(self, rec: dict) -> None:
        if rec.get("variant") != "pv" or flag(rec.get("sealed")) or flag(rec.get("h5_look2_sealed")):
            return
        td, tm = rec.get("t_detect_ms"), rec.get("t_ms")
        t_eff = td if isint(td) else (tm if isint(tm) else None)
        if t_eff is not None and t_eff < self.from_ms:
            return
        if not would_pass(rec)[0]:
            return
        pool, sig, ei, slot = rec.get("pool"), rec.get("signature"), rec.get("event_index"), rec.get("slot")
        tid = f"{pool}|{sig}|{ei}|{slot}"
        if tid in self.trig:
            return
        dropped = [(e[0], e[1], e[2]) for e in rec["base_tipauth"]["dropped"]]  # would_pass guarantees a list of well-formed entries
        fields_ok = (isinstance(pool, str) and bool(pool) and isinstance(sig, str) and SIG_SAFE.fullmatch(sig) is not None and isint(ei) and isint(rec.get("base_pre"))
                     and isint(rec.get("real_quote_pre")) and isint(tm) and all(SIG_SAFE.fullmatch(d[0]) is not None for d in dropped))
        self.trig[tid] = {"tid": tid, "pool": pool, "signature": sig, "event_index": ei, "slot": slot, "base_pre": rec.get("base_pre"), "real_quote_pre": rec.get("real_quote_pre"),
                          "t_ms": tm, "t_eff": t_eff, "dropped": dropped, "fields_ok": fields_ok}
        if isinstance(pool, str):
            self.pool_tids.setdefault(pool, []).append(tid)
            if pool.encode("utf-8") not in self.pool_bytes:
                self.pool_bytes.append(pool.encode("utf-8"))

    def _on_pool(self, rec: dict) -> None:
        pool = rec.get("pool")
        if not isinstance(pool, str) or pool not in self.pool_tids or flag(rec.get("sealed")) or flag(rec.get("h5_look2_sealed")):
            return
        bt = rec.get("base_tipauth")
        bt = bt if isinstance(bt, dict) else {}
        dl = bt.get("dropped")
        self.closes.setdefault(pool, []).append({"t_ms": rec.get("t_ms"), "status": bt.get("status"), "cover_hi": bt.get("cover_hi"), "dropped_n": bt.get("dropped_n"),
                                                 "dropped": list(dl) if isinstance(dl, list) else None})

    # -- the shadow's newest start record --
    def find_start(self, deadline_ms: int) -> tuple[dict | None, bool, bool]:
        """(newest start record, search complete, unreadable). Newest hour file first, at most START_SCAN_FILES files, a per-file cache so an unchanged file is not read twice.
        UNREADABLE (R-A): a `start` marker (either spacing) newer than the newest valid start record, in its file or in any newer file, that is not a complete, parseable start
        line at a line start. The search then stops: it never falls back to an older record's sha. Only a later valid start record (a shadow restart) clears it."""
        try:
            names = sorted((e.name for e in os.scandir(self.shadow_dir) if SHADOW_RE.match(e.name)), reverse=True)[:START_SCAN_FILES]
        except OSError:
            return None, False, False
        for name in names:
            if self.now() > deadline_ms:
                return None, False, False
            res = self._start_in(self.shadow_dir / name, name)
            if res is _UNREADABLE:  # never fall back to an older file's start record
                return None, False, False
            rec, bad = res
            if bad:
                return None, True, True
            if rec is not None:
                return rec, True, False
        return None, True, False

    def _start_in(self, path: Path, name: str) -> Any:
        """(newest valid start record of the file or None, a marker newer than it could not be read) or _UNREADABLE (the file itself could not be read)."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # NONBLOCK: a FIFO in the directory must not stop the cycle
        except OSError:
            return _UNREADABLE
        try:
            st = os.fstat(fd)
            if not stat_mod.S_ISREG(st.st_mode):
                return _UNREADABLE
            if st.st_size == 0:
                self.start_cache.pop(name, None)
                return None, False
            prev_to, seen, prev_rec, prev_bad, prev_total = self.start_cache.get(name, (0, 0, None, False, False))
            if st.st_size < seen:
                prev_to, seen, prev_rec, prev_bad, prev_total = 0, 0, None, False, False
            if st.st_size == seen:
                return prev_rec, prev_total
            with mmap.mmap(fd, st.st_size, access=mmap.ACCESS_READ) as mm:
                size = len(mm)
                lo = (mm.rfind(b"\n", 0, prev_to) + 1) if prev_to else 0
                end, found, scanned_to = size, None, size
                bad_pos: list[int] = []
                while True:
                    pos = max(mm.rfind(mk, lo, end) for mk in START_MARKS)
                    if pos < 0:
                        break
                    end = pos
                    eol = mm.find(b"\n", pos)
                    if eol < 0:  # no newline yet (a torn or a still-written line): not a start record, and read again from here
                        scanned_to = min(scanned_to, pos)
                        bad_pos.append(pos)
                        continue
                    if (pos != 0 and mm[pos - 1:pos] != b"\n") or eol - pos > START_LINE_MAX:  # inside another record / over the line cap
                        bad_pos.append(pos)
                        continue
                    try:
                        rec = json.loads(mm[pos:eol])
                    except (ValueError, RecursionError):
                        bad_pos.append(pos)
                        continue
                    if isinstance(rec, dict) and rec.get("type") == "start":
                        found = rec
                        break
                    bad_pos.append(pos)
            # Newest first and stopped at the first valid record: every bad marker seen is newer than the result. Markers before scanned_to are final (cached);
            # those at or after it are looked at again next time.
            result = found if found is not None else prev_rec
            total = bool(bad_pos) or (found is None and prev_bad)
            perm = any(p < scanned_to for p in bad_pos) or (found is None and prev_bad)
            self.start_cache[name] = (scanned_to, size, result, perm, total)
            return result, total
        except (OSError, ValueError):
            return _UNREADABLE
        finally:
            os.close(fd)

    # -- the tip tape --
    def tip_hw(self) -> int | None:
        now = self.now()
        for h in (hour_name(now), hour_name(now - HOUR_MS)):
            slot = last_complete_row_slot(self.tip_dir / f"trades-{h}.jsonl")
            if slot is not None:
                return slot
        return None

    @staticmethod
    def classify_entry(t: dict, e: Any, closes: list[dict]) -> str:
        """Item 10's rule on the close record (tools/h5_tipauth_item4.classify_margin): open | still_dropped | left_D | undetermined."""
        tm = t["t_ms"]
        close = next((c for c in closes if isint(c.get("t_ms")) and isint(tm) and c["t_ms"] >= tm), None)
        if close is None:
            return "open"
        usable = close["status"] == "ok" and isint(close["cover_hi"]) and isint(t["slot"]) and close["cover_hi"] >= t["slot"]
        cl, cdn = (close["dropped"], close["dropped_n"]) if usable else (None, None)
        if not entry_ok(list(e)) or not usable or not isinstance(cl, list) or not isint(cdn):
            return "undetermined"
        if (e[0], e[1]) in {(x[0], x[1]) for x in cl if entry_ok(x)}:
            return "still_dropped"
        if cdn <= DROPPED_LIST_MAX or (cl and entry_ok(cl[-1]) and e[2] < cl[-1][2]):
            return "left_D"
        return "undetermined"

    def eval_closes(self) -> None:
        for tid, t in self.trig.items():
            if tid in self.resolved["e2"] or not t["fields_ok"]:
                continue
            classes = [self.classify_entry(t, e, self.closes.get(t["pool"], [])) for e in t["dropped"]]
            left = [e for e, c in zip(t["dropped"], classes) if c == "left_D"]
            if left:
                self.off_event(E2, f"{E2}|{tid}|close", {"via": "close_record_left_D", "tid": tid, "pool": t["pool"], "slot": t["slot"], "left_D_entries": [list(e) for e in left],
                                                          "close_t_ms": next((c["t_ms"] for c in self.closes.get(t["pool"], []) if isint(c.get("t_ms")) and isint(t["t_ms"]) and c["t_ms"] >= t["t_ms"]), None)})
            elif classes and all(c == "still_dropped" for c in classes):
                self._mark("e2", tid)

    def age(self, t: dict) -> float:
        return float("inf") if t["t_eff"] is None else max(0, self.now() - t["t_eff"])

    def tape_phase(self, deadline_ms: int) -> None:
        hw = self.hw
        e1_due = [t for t in self.trig.values() if t["fields_ok"] and t["tid"] not in self.resolved["e1"] and hw is not None and hw - TIPAUTH_MARGIN_SLOTS >= t["slot"]]
        e2_due = [t for t in self.trig.values() if t["fields_ok"] and t["tid"] not in self.resolved["e2"] and self.age(t) <= E2_TAPE_MAX_MS]
        want: dict[str, set[str]] = {}
        for t in e1_due:
            for h in wanted_hours(t["t_ms"]):
                want.setdefault(h, set()).add(t["signature"])
        for t in e2_due:
            for h in wanted_hours(t["t_ms"]):
                want.setdefault(h, set()).update(d[0] for d in t["dropped"])
        if not want:
            return
        rows: dict[str, list[dict]] = {}
        status: dict[str, str] = {}
        partial: set[str] = set()
        for h in sorted(want):
            p = self.tip_dir / f"trades-{h}.jsonl"
            if not p.is_file():
                status[h] = "compressed" if any((self.tip_dir / f"trades-{h}.jsonl{c}").exists() for c in COMPRESSED) else "missing"
                continue
            r, st, pt = scan_tape_file(p, sorted(want[h]), deadline_ms, self.now)
            status[h] = st
            partial |= pt
            for sig, lst in r.items():
                rows.setdefault(sig, []).extend(dict(x, hour=h) for x in lst)
        for t in e2_due:  # event 2 by the tape: a tip row, in this pool, for a signature the trigger dropped. A hit is valid on a partial scan.
            for d in t["dropped"]:
                hit = [x for x in rows.get(d[0], []) if x["pool"] == t["pool"]]
                if hit:
                    self.off_event(E2, f"{E2}|{t['tid']}|{d[0]}", {"via": "tip_tape", "tid": t["tid"], "pool": t["pool"], "signature": d[0], "dropped_slot": d[2], "tip_rows": hit[:3]})
        for t in e1_due:  # event 1
            own = hour_name(t["t_ms"])
            match = [x for x in rows.get(t["signature"], []) if x["pool"] == t["pool"] and isint(x["event_index"]) and x["event_index"] == t["event_index"]]
            differs = [x for x in match if not (isint(x["base_reserve"]) and x["base_reserve"] == t["base_pre"] and isint(x["quote_reserve"]) and x["quote_reserve"] == t["real_quote_pre"])]
            ev = {"tid": t["tid"], "pool": t["pool"], "signature": t["signature"], "event_index": t["event_index"], "slot": t["slot"], "t_ms": t["t_ms"], "tip_hw_slot": hw,
                  "record_base_pre": t["base_pre"], "record_real_quote_pre": t["real_quote_pre"], "hours_searched": wanted_hours(t["t_ms"])}
            if differs:
                self.off_event(E1, f"{E1}|{t['tid']}", dict(ev, kind="pre_trade_differs", tip_rows=match[:3]))
                continue
            hours = wanted_hours(t["t_ms"])
            complete = (status.get(own) == "ok" and all(status.get(h) in ("ok", "missing") for h in hours) and t["signature"] not in partial)
            if not complete:
                continue
            if not match:
                self.off_event(E1, f"{E1}|{t['tid']}", dict(ev, kind="no_row"))
            else:
                self._mark("e1", t["tid"])

    # -- the public RPC --
    def rpc_phase(self, deadline_ms: int) -> None:
        due: dict[str, tuple[int, list[str]]] = {}
        for t in self.trig.values():
            for d in t["dropped"]:
                t_eff = t["t_eff"] if t["t_eff"] is not None else 0
                cur = due.get(d[0])
                due[d[0]] = (min(cur[0], t_eff) if cur else t_eff, (cur[1] if cur else []) + [t["tid"]])
        asks = 0
        for sig in sorted(due, key=lambda s: (due[s][0], s)):
            cls = rpc_class(self.rpc_rows.get(sig, []))
            if cls == "found":
                self.off_event(E3, f"{E3}|{sig}", {"signature": sig, "tids": due[sig][1], "answers": self.rpc_rows.get(sig, [])[-5:],
                                                   "found_slot": next((a.get("slot") for a in self.rpc_rows.get(sig, []) if a.get("result") == "found"), None)})
                continue
            if cls == "not_found" or asks >= RPC_MAX_ASKS or self.now() > deadline_ms:
                continue
            ans = self.rpc_rows.get(sig, [])
            now = self.now()
            nulls = sorted(a["asked_ms"] for a in ans if a["result"] == "null")
            if nulls and now - nulls[0] < RPC_REASK_MS:
                continue  # a not-found is asked again no earlier than 10 minutes after the first
            if ans and now - max(a["asked_ms"] for a in ans) < RPC_ERROR_RETRY_MS:
                continue  # an error is retried, but not every few milliseconds
            if asks:
                self.sleep(RPC_PACE_S)
            asked_ms = self.now()
            ans_new = self._ask(sig)
            if ans_new is None:  # a past call is still stuck: ask nothing more this cycle
                break
            asks += 1
            row = {"sig": sig, "asked_ms": asked_ms, **ans_new}
            self.rpc_rows.setdefault(sig, []).append(row)
            append_jsonl(self.p["rpc"], row)
            if row["result"] == "found":
                self.off_event(E3, f"{E3}|{sig}", {"signature": sig, "tids": due[sig][1], "answers": [row], "found_slot": row.get("slot")})

    def _ask(self, sig: str) -> dict | None:
        """One RPC call in a daemon worker thread, waited for at most RPC_TIMEOUT_S + RPC_DEADLINE_GRACE_S (R-B). A call past that is an `error` (never a null), its late
        answer is dropped, and no second call starts while it is still running (None: nothing was asked)."""
        prev = self._rpc_thread
        if prev is not None and prev.is_alive():
            return None
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["r"] = interpret(self.rpc(self.rpc_url, request_payload(sig), RPC_TIMEOUT_S))
            except Exception as e:  # noqa: BLE001  (timeouts, HTTP errors, bad JSON, anything a transport raises)
                box["r"] = {"result": "error", "err": type(e).__name__}

        th = threading.Thread(target=work, name="h5-tipauth-rpc", daemon=True)
        self._rpc_thread = th
        th.start()
        th.join(RPC_TIMEOUT_S + RPC_DEADLINE_GRACE_S)
        if th.is_alive():
            return {"result": "error", "err": "deadline"}
        r = box.get("r")
        return r if isinstance(r, dict) else {"result": "error", "err": "no_result"}

    # -- verdict --
    def counts(self) -> tuple[int, int, int]:
        clear = 0
        for tid, t in self.trig.items():
            if (t["fields_ok"] and tid in self.resolved["e1"] and tid in self.resolved["e2"]
                    and all(rpc_class(self.rpc_rows.get(d[0], [])) == "not_found" for d in t["dropped"])):
                clear += 1
        return len(self.trig), clear, len(self.trig) - clear

    def holds(self) -> list[str]:
        now, h = self.now(), []
        if os.path.lexists(self.p["hold"]):
            h.append("manual_hold")
        h += self.last_error
        if self.start_unreadable:
            h.append("start_record_unreadable")
        elif self.observed_sha is None:
            h.append("no_start_record" if self.start_complete else "start_search_incomplete")
        elif self.observed_sha != self.shadow_sha:
            h.append("shadow_sha_mismatch")
        if not self.shadow_dir.is_dir():
            h.append("shadow_dir_missing")
        if not self.tip_dir.is_dir():
            h.append("tip_dir_missing")
        if not self.caught_up:
            h.append("shadow_read_incomplete")
        if self.bad_trigger_lines:
            h.append("bad_trigger_line")
        ws = list(self.trig.values())
        if any(not t["fields_ok"] for t in ws):
            h.append("trigger_unverifiable")
        hw = self.hw
        pending1 = [t for t in ws if t["fields_ok"] and t["tid"] not in self.resolved["e1"] and self.age(t) > TIP_HOLD_MS]
        if any(hw is None or hw - TIPAUTH_MARGIN_SLOTS < t["slot"] for t in pending1):
            h.append("tip_not_past_trigger")
        if any(hw is not None and hw - TIPAUTH_MARGIN_SLOTS >= t["slot"] for t in pending1):
            h.append("trigger_print_unverified")
        sigs: dict[str, int] = {}
        for t in ws:
            for d in t["dropped"]:
                sigs[d[0]] = min(sigs.get(d[0], 1 << 62), t["t_eff"] if t["t_eff"] is not None else 0)
        if any(rpc_class(self.rpc_rows.get(s, [])) not in ("found", "not_found") and now - due > RPC_HOLD_MS for s, due in sigs.items()):
            h.append("rpc_no_answer_60m")
        if any(t["fields_ok"] and t["tid"] not in self.resolved["e2"] and self.age(t) > E2_HOLD_MS for t in ws):
            h.append("event2_unresolved_60m")
        return h

    def verdict(self) -> tuple[str, str]:
        if os.path.lexists(self.p["off"]) and self.off_info is None:
            self.off_info = self._read_off()
        if self.off_info is not None:
            self._ensure_off()
            return "off", f"off:{self.off_info.get('event') or 'marker'}"
        h = self.holds()
        return ("hold", ";".join(h)) if h else ("ok", "ok")

    def state(self, verdict: str, reason: str) -> dict:
        w, c, p = self.counts()
        return {"schema": SCHEMA, "ts_ms": int(self.now()), "verdict": verdict, "reason": reason, "shadow_sha": self.observed_sha, "watched_n": w, "clear_n": c, "pending_n": p}

    def write_state(self, verdict: str, reason: str) -> dict:
        st = self.state(verdict, reason)
        atomic_write(self.p["state"], json.dumps(st, sort_keys=True) + "\n")
        return st

    def _guard(self, name: str, fn: Callable, *a: Any, default: Any = None) -> Any:
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001  (a phase that raises is a hold, never an ok, and never a missed state write)
            self.last_error.append(f"internal_error:{name}:{type(e).__name__}")
            return default

    def cycle(self) -> dict:
        """One cycle. ALWAYS rewrites state.json, whatever raised."""
        t0 = self.now()
        self.last_error = []
        self.caught_up = bool(self._guard("shadow", self.poll_shadow, t0 + SHADOW_BUDGET_MS, default=False))
        self._guard("closes", self.eval_closes)
        rec = self._guard("start", self.find_start, self.now() + START_BUDGET_MS, default=(None, False, False))
        sha = rec[0].get("code_sha") if rec and rec[0] is not None else None
        self.observed_sha = sha if isinstance(sha, str) else None
        self.start_complete = bool(rec and rec[1])
        self.start_unreadable = bool(rec and rec[2])
        self.hw = self._guard("tip_hw", self.tip_hw)
        self._guard("tape", self.tape_phase, self.now() + TAPE_BUDGET_MS)
        self._guard("rpc", self.rpc_phase, self.now() + RPC_BUDGET_MS)
        try:
            v, r = self.verdict()
        except Exception as e:  # noqa: BLE001
            v, r = "hold", f"internal_error:verdict:{type(e).__name__}"
        try:
            st = self.write_state(v, r)
        except Exception:  # noqa: BLE001  (the disk: nothing more can be done; the executor sees a stale file and refuses)
            st = self.state(v, r)
        self.log(f"cycle ts_ms={st['ts_ms']} verdict={st['verdict']} reason={st['reason']} watched={st['watched_n']} clear={st['clear_n']} pending={st['pending_n']}")
        return st

    def run(self, once: bool = False, max_cycles: int | None = None, stop: Callable[[], bool] = lambda: False) -> None:
        n = 0
        while True:
            t0 = self.now()
            self.cycle()
            n += 1
            if once or stop() or (max_cycles is not None and n >= max_cycles):
                return
            rest = CYCLE_S - (self.now() - t0) / 1000.0
            while rest > 0 and not stop():
                step = min(1.0, rest)
                self.sleep(step)
                rest -= step
            if stop():
                return


# ---- manager commands -------------------------------------------------------------------------------------------------------------------------

def _prev_state(out_dir: Path) -> dict:
    try:
        d = json.loads((out_dir / FILES["state"]).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def force_off(out_dir: Path, reason: str, now_ms: int) -> None:
    """Write the OFF marker (exclusive create; an existing one is left as it is), log it, and write state.json as off at once."""
    out_dir.mkdir(parents=True, exist_ok=True)
    info = {"ts_ms": now_ms, "event": "manager_force_off", "dedupe": f"manager_force_off|{now_ms}", "reason": reason}
    try:
        fd = os.open(out_dir / FILES["off"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(info, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError:
        pass
    append_jsonl(out_dir / FILES["events"], {"ts_ms": now_ms, "off": True, "event": "manager_force_off", "dedupe": info["dedupe"], "evidence": {"reason": reason}})
    prev = _prev_state(out_dir)
    num = lambda k: prev[k] if isint(prev.get(k)) else 0  # noqa: E731
    atomic_write(out_dir / FILES["state"], json.dumps({"schema": SCHEMA, "ts_ms": now_ms, "verdict": "off", "reason": "off:manager_force_off",
                                                       "shadow_sha": prev.get("shadow_sha") if isinstance(prev.get("shadow_sha"), str) else None, "watched_n": num("watched_n"),
                                                       "clear_n": num("clear_n"), "pending_n": num("pending_n")}, sort_keys=True) + "\n")


def set_hold(out_dir: Path, now_ms: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / FILES["hold"]).write_text(json.dumps({"ts_ms": now_ms, "event": "manager_hold"}) + "\n", encoding="utf-8")
    append_jsonl(out_dir / FILES["events"], {"ts_ms": now_ms, "off": False, "event": "manager_hold", "dedupe": f"manager_hold|{now_ms}"})


def release(out_dir: Path, now_ms: int) -> bool:
    """Remove the manual HOLD marker. Never touches OFF. Returns False if OFF exists (a release does not undo an off)."""
    try:
        (out_dir / FILES["hold"]).unlink()
    except FileNotFoundError:
        pass
    append_jsonl(out_dir / FILES["events"], {"ts_ms": now_ms, "off": False, "event": "manager_release", "dedupe": f"manager_release|{now_ms}"})
    return not os.path.lexists(out_dir / FILES["off"])


def main(argv: list[str] | None = None, *, rpc: Callable[[str, dict, float], Any] = http_transport, now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
         sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="Exit: 0 ran; 2 bad arguments; 3 another watch holds this out-dir.")
    ap.add_argument("--shadow-dir")
    ap.add_argument("--tip-dir")
    ap.add_argument("--out-dir")
    ap.add_argument("--shadow-sha", help="40 lowercase hex: the sha the newest start record must show")
    ap.add_argument("--from-ms", type=int, help="the enable instant: triggers with t_detect_ms below it are not watched")
    ap.add_argument("--rpc-url", help="a plain public https endpoint (no key)")
    ap.add_argument("--force-off", metavar="REASON", help="manager: write the OFF marker now")
    ap.add_argument("--hold", action="store_true", help="manager: create the manual HOLD marker")
    ap.add_argument("--release", action="store_true", help="manager: remove the manual HOLD marker (never OFF)")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    a = ap.parse_args(argv)
    if sum((a.force_off is not None, a.hold, a.release)) > 1:
        ap.error("--force-off, --hold and --release are separate commands")
    if not a.out_dir:
        ap.error("--out-dir is required")
    out = Path(a.out_dir)
    if a.force_off is not None:
        if not a.force_off.strip():
            ap.error("--force-off needs a reason")
        force_off(out, a.force_off, now_ms())
        return 0
    if a.hold:
        set_hold(out, now_ms())
        return 0
    if a.release:
        if not release(out, now_ms()):
            sys.stderr.write("released HOLD; OFF still exists and is never removed by this tool\n")
        return 0
    for name, val in (("--shadow-dir", a.shadow_dir), ("--tip-dir", a.tip_dir), ("--shadow-sha", a.shadow_sha), ("--from-ms", a.from_ms), ("--rpc-url", a.rpc_url)):
        if val is None:
            ap.error(f"{name} is required")
    if not SHA_RE.fullmatch(a.shadow_sha):
        ap.error("--shadow-sha must be 40 lowercase hex characters")
    bad = validate_rpc_url(a.rpc_url)
    if bad:
        ap.error(f"--rpc-url refused: {bad}")
    out.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(out / FILES["lock"], os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.stderr.write("another watch holds this out-dir\n")
        return 3
    stopping = {"v": False}
    for sg in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sg, lambda *_: stopping.update(v=True))  # no final state is written: a stopped watch must go stale
    w = Watch(Path(a.shadow_dir), Path(a.tip_dir), out, a.shadow_sha, a.from_ms, a.rpc_url, rpc=rpc, now_ms=now_ms, sleep=sleep, log=lambda m: sys.stderr.write(m + "\n"))
    w.run(once=a.once, stop=lambda: stopping["v"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
