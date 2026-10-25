#!/usr/bin/env python3
"""P23 change radar: a daily digest of what changed on the venues MAL trades or could trade. Paper only, no keys, 0 Helius credits.

Design, thresholds and playbook: docs/research/p23-change-radar/DESIGN.md. Watch list: tools/change_radar_watch.json.

Stages (each one fails alone; a failed stage is an error in the record, never a change):
  W1/W2 programs  public RPC. Program account -> programdata address; programdata slice (45 bytes) -> last deploy slot and upgrade authority.
                  A moved slot is a program_upgrade change; for those programs only, getSignaturesForAddress(programdata) counts the writes
                  since the previous slot, and getBlockTime gives the deploy instant.
  W3 accounts     public RPC. sha256 and length of each watched config account (fields are not decoded here).
  W4 vocab        public RPC. Per program with "vocab": true: up to --vocab-txs successful recent txs; collects the instruction names it logs in
                  its own invoke frame, its `Program data:` discriminators, its outer/inner instruction discriminators and its emit_cpi event
                  discriminators. A key never seen before for that program is a new_vocab change (the first run only seeds).
  W5/W6 tapes     local files. Rows per (program, type) per closed UTC day from the arena tape (tape-<hour>.jsonl[.zst]) and per (venue, type,
                  instruction name, undecoded discriminator) from the venue tape. Counts only: no price, amount, reserve, mint, pool or wallet
                  value is copied into any output. Shift / new key / gone key / share rules from DESIGN section 2.
                  Sealed windows (tools/change_radar_seals.json, pinned, fail closed): a day overlapping a window is never counted, and a
                  source with disabled_until is not touched before that instant (none set since QP-RULING-1025-seal-lift 2d).
  W7 monitor      local file. The newest pump_structure_monitor record's true HALT (high) and WARN (low) flags, once per monitor run.

Outputs under --out-dir: radar.jsonl (one record per run), state.json (baseline), daily-counts.jsonl (one record per source and day),
digest-<date>.md/.json, console-events-<date>.json (data/console.json `events` shape; the radar never edits the repo). Stdout ends with
`CHANGES ...` / `CHANGE ...` lines for the MiScusi notebook.

Refuses: a Helius RPC URL, an RPC URL with credentials, a query or a path; a tape directory whose path contains a sealed-data token.
Exit codes: 0 done (also when stages failed: status "partial"); 1 outputs could not be written; 2 bad arguments.

Run: python -m tools.change_radar --out-dir /data/mal/change-radar
"""
from __future__ import annotations

if __package__ in (None, ""):  # `python tools/change_radar.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import json
import os
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlparse

from tools.pump_structure_monitor import (
    EVENT_CPI_TAG,
    ITEM_ERRORS,
    CallBudgetExceeded,
    RpcClient,
    RpcUnreachable,
    b58decode,
    b58encode,
    decode_program_account,
    decode_programdata_slot,
    fetch_tx,
    sha256_hex,
)

SCHEMA = "change_radar.v1"
COUNTS_SCHEMA = "change_radar_counts.v1"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_OUT = "/data/mal/change-radar"
DEFAULT_ARENA = "/data/mal/arena/tape"
DEFAULT_VENUE = "/data2/mal/venue-tape"
DEFAULT_MONITOR = "/data/mal/structure-monitor/daily.jsonl"
DEFAULT_WATCH = Path(__file__).with_name("change_radar_watch.json")
DEFAULT_SEALS = Path(__file__).with_name("change_radar_seals.json")  # pinned sealed windows; a missing or bad file refuses the run

# A tape dir whose path holds any of these is refused (sealed, owned or bot-output data is never an input).
FORBIDDEN_PATH_TOKENS = ("holdout", "fwdv1", "shadow", "probe", "sealed", "ledger", "bots.json", "backfill",
                         "hv4", "q50", "decision", "score")  # HV4/Q50 ledgers, decision logs, bots.json and score output are never an input
TAPE_NAME = re.compile(r"^tape-(\d{4}-\d{2}-\d{2})T(\d{2})\.jsonl(\.zst)?$")
ARENA_PREFIX = re.compile(rb'^\{"v":\d+,"type":"([A-Za-z0-9_]+)","prog":"([A-Za-z0-9_]+)"')
SAFE_KEY = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")

# ---- thresholds (DESIGN section 2; first guesses, changed only by a dated amendment there) ----------------------------------------
MIN_HOURS = 20  # a source-day with fewer hour files is coverage_short: never alerts, never a baseline
BASELINE_DAYS = 7
MIN_BASELINE_DAYS = 3
SHIFT_MED, SHIFT_HIGH = 1.5, 3.0
SHIFT_MIN_ROWS = 50
NEW_KEY_MIN = 5
GONE_KEY_BASELINE = 100
SHARE_PP = 0.10
DAY_CLOSE_GRACE_S = 900  # a UTC day is counted once it ended at least 15 min ago
SEVERITY_ORDER = {"high": 0, "med": 1, "low": 2}
TIER_SEVERITY = {"core": "high", "venue": "med", "router": "low"}
VOCAB_MAX_KEYS = 400  # per program, so a noisy program cannot grow the state without bound
VOCAB_WARMUP_RUNS = 3  # a program's sampled vocabulary only seeds (no alert) on its first 3 successful runs
# Process / bookkeeping row types of the tape writers: never a count rule (coverage is judged by hour files instead).
INTERNAL_TYPES = frozenset({"hb", "gap", "gap_end", "start", "stop", "pool_info", "disk_pause", "disk_resume"})
LOADER_UPGRADEABLE = "BPFLoaderUpgradeab1e11111111111111111111111"


class RadarError(Exception):
    """Bad arguments or refused inputs (exit 2)."""


# =============================================================================================================== small helpers
def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_rpc_url(url: str) -> str:
    """Return host[:port]. Refuse Helius, credentials, a query string or a path (any of them can carry a key)."""
    try:
        p = urlparse(url)
    except ValueError as exc:
        raise RadarError("unparseable RPC URL") from exc
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host:
        raise RadarError("RPC URL must be http(s)://host")
    if "helius" in host:
        raise RadarError("Helius URLs are refused: the radar runs on public RPC only (0 credits)")
    if p.username or p.password or p.query or p.fragment or p.path not in ("", "/"):
        raise RadarError("RPC URL with credentials, query or path is refused")
    return f"{host}:{p.port}" if p.port else host


def check_tape_dir(path: str) -> Path:
    low = str(path).lower()
    resolved = str(Path(path).resolve()).lower() if Path(path).exists() else low
    for tok in FORBIDDEN_PATH_TOKENS:
        if tok in low or tok in resolved:
            raise RadarError(f"tape dir refused (path contains '{tok}')")
    return Path(path)


def change(sev: str, kind: str, subject: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"sev": sev, "kind": kind, "subject": subject, "detail": detail, **extra}


def _account_bytes(entry: Mapping[str, Any] | None) -> bytes | None:
    if not entry:
        return None
    data = entry.get("data")
    if not isinstance(data, list) or not data:
        return None
    return base64.b64decode(data[0])


def decode_programdata_authority(data: bytes | None) -> tuple[bool, str | None]:
    """(readable, upgrade authority or None when the program is frozen) from a ProgramData slice of >= 45 bytes."""
    if data is None or len(data) < 13:
        return False, None
    if data[12] == 0:
        return True, None
    if len(data) < 45:
        return False, None
    return True, b58encode(data[13:45])


# =============================================================================================================== W1/W2 programs
def stage_programs(client: Any, watch: Mapping[str, Any], prev: Mapping[str, Any], errors: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    progs: Mapping[str, Mapping[str, Any]] = watch.get("programs") or {}
    names = list(progs)
    obs: dict[str, dict[str, Any]] = {n: {"id": progs[n]["id"], "tier": progs[n].get("tier", "venue"), "status": "unread"} for n in names}
    changes: list[dict[str, Any]] = []
    if not names:
        return obs, changes
    res = client.call("getMultipleAccounts", [[progs[n]["id"] for n in names], {"encoding": "base64", "commitment": "finalized"}])
    values = (res or {}).get("value") or []
    if len(values) != len(names):
        raise RuntimeError(f"getMultipleAccounts returned {len(values)} of {len(names)} program accounts")
    pd_names: list[str] = []
    for n, v in zip(names, values):
        if v is None:
            obs[n]["status"] = "missing"
            continue
        obs[n]["executable"] = bool(v.get("executable"))
        obs[n]["owner"] = v.get("owner")
        if v.get("owner") != LOADER_UPGRADEABLE:
            obs[n]["status"] = "not_upgradeable_loader"
            continue
        pda = decode_program_account(_account_bytes(v))
        if pda is None:
            obs[n]["status"] = "undecodable"
            continue
        obs[n]["programdata"] = pda
        pd_names.append(n)
    if pd_names:
        res2 = client.call("getMultipleAccounts", [[obs[n]["programdata"] for n in pd_names],
                                                   {"encoding": "base64", "dataSlice": {"offset": 0, "length": 45}, "commitment": "finalized"}])
        vals2 = (res2 or {}).get("value") or []
        if len(vals2) != len(pd_names):
            raise RuntimeError(f"getMultipleAccounts returned {len(vals2)} of {len(pd_names)} programdata slices")
        for n, v in zip(pd_names, vals2):
            data = _account_bytes(v)
            slot = decode_programdata_slot(data)
            readable, auth = decode_programdata_authority(data)
            if slot is None or not readable:
                obs[n]["status"] = "programdata_undecodable"
                continue
            obs[n].update(status="ok", deploy_slot=slot, authority=auth)
    for n in names:
        o, p = obs[n], (prev.get(n) or {})
        sev = TIER_SEVERITY.get(o["tier"], "med")
        if o["status"] != "ok":
            if p.get("deploy_slot") is not None and o["status"] in ("missing", "not_upgradeable_loader"):
                changes.append(change("high", "program_gone", n, f"{n} was upgradeable at slot {p.get('deploy_slot')}, now {o['status']}"))
            continue
        if p.get("deploy_slot") is None:
            o["first_seen"] = True
            o["deploy_utc"] = _block_time(client, o["deploy_slot"], errors)
            continue
        if o["deploy_slot"] != p["deploy_slot"]:
            o["deploy_utc"] = _block_time(client, o["deploy_slot"], errors)
            o["writes_since"] = _writes_since(client, o["programdata"], p["deploy_slot"], errors)
            when = f" ({o['deploy_utc']})" if o.get("deploy_utc") else ""
            ws = o["writes_since"]
            changes.append(change(sev, "program_upgrade", n, f"{n} deploy_slot {p['deploy_slot']} -> {o['deploy_slot']}{when} writes_since="
                                  + ("unread" if ws is None else str(ws)), at_utc=o.get("deploy_utc")))
        else:
            o["deploy_utc"] = p.get("deploy_utc")
        if o.get("authority") != p.get("authority") and "authority" in p:
            what = "frozen (no upgrade authority)" if o.get("authority") is None else "changed"
            changes.append(change("high", "authority_change", n, f"{n} upgrade authority {what}"))
    return obs, changes


def _block_time(client: Any, slot: int, errors: list[str]) -> str | None:
    try:
        bt = client.call("getBlockTime", [slot])
    except ITEM_ERRORS as exc:
        errors.append(f"programs: getBlockTime {type(exc).__name__}")
        return None
    return iso(bt) if isinstance(bt, int) else None


def _writes_since(client: Any, programdata: str, prev_slot: int, errors: list[str]) -> int | None:
    """Successful writes to the programdata account with slot > prev_slot (one page of 100; None if the page does not reach prev_slot)."""
    try:
        sigs = client.call("getSignaturesForAddress", [programdata, {"limit": 100, "commitment": "finalized"}]) or []
    except ITEM_ERRORS as exc:
        errors.append(f"programs: getSignaturesForAddress {type(exc).__name__}")
        return None
    newer = [s for s in sigs if isinstance(s, dict) and int(s.get("slot") or 0) > prev_slot]
    if len(newer) == len(sigs) and len(sigs) >= 100:
        return None
    return sum(1 for s in newer if s.get("err") is None)


# =============================================================================================================== W3 accounts
def stage_accounts(client: Any, watch: Mapping[str, Any], prev: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    accts: Mapping[str, Mapping[str, Any]] = watch.get("accounts") or {}
    names = list(accts)
    obs: dict[str, dict[str, Any]] = {}
    changes: list[dict[str, Any]] = []
    if not names:
        return obs, changes
    res = client.call("getMultipleAccounts", [[accts[n]["address"] for n in names], {"encoding": "base64", "commitment": "finalized"}])
    values = (res or {}).get("value") or []
    if len(values) != len(names):
        raise RuntimeError(f"getMultipleAccounts returned {len(values)} of {len(names)} accounts")
    for n, v in zip(names, values):
        data = _account_bytes(v)
        o = {"address": accts[n]["address"], "exists": data is not None, "len": None if data is None else len(data),
             "sha256": None if data is None else sha256_hex(data)}
        obs[n] = o
        p = prev.get(n)
        if not p:
            o["first_seen"] = True
            continue
        if p.get("exists") and not o["exists"]:
            changes.append(change("high", "account_closed", n, f"{n} account no longer exists"))
        elif o["exists"] and (o["sha256"] != p.get("sha256") or o["len"] != p.get("len")):
            changes.append(change("high", "config_change", n, f"{n} sha256 {str(p.get('sha256'))[:12]} -> {o['sha256'][:12]} len {p.get('len')} -> {o['len']}"))
    return obs, changes


# =============================================================================================================== W4 vocabulary
_INVOKE = re.compile(r"^Program (\S+) invoke \[\d+\]")
_DONE = re.compile(r"^Program (\S+) (?:success|failed)")
_INSTR = re.compile(r"^Program log: Instruction: (\w+)")


def vocab_from_tx(tx: Mapping[str, Any], program_id: str) -> set[str]:
    """Vocabulary keys of one tx for one program: ix:<logged name>, data:<Program data disc>, ixd:<instruction disc>, evt:<emit_cpi disc>.
    Discriminators are the first 8 bytes (hex) and are read only from frames / instructions of `program_id`."""
    keys: set[str] = set()
    stack: list[str] = []
    for line in (tx.get("meta") or {}).get("logMessages") or []:
        if not isinstance(line, str):
            continue
        m = _INVOKE.match(line)
        if m:
            stack.append(m.group(1))
            continue
        if stack and stack[-1] == program_id:
            m = _INSTR.match(line)
            if m:
                keys.add("ix:" + m.group(1))
                continue
            if line.startswith("Program data: "):
                try:
                    raw = base64.b64decode(line.split("Program data: ", 1)[1].strip())
                except ValueError:
                    raw = b""
                if len(raw) >= 8:
                    keys.add("data:" + raw[:8].hex())
                continue
        if _DONE.match(line) and stack:
            stack.pop()
    msg = (tx.get("transaction") or {}).get("message") or {}
    acct = [k if isinstance(k, str) else (k or {}).get("pubkey") for k in msg.get("accountKeys") or []]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    acct += list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    ixs = list(msg.get("instructions") or [])
    for grp in (tx.get("meta") or {}).get("innerInstructions") or []:
        ixs += list(grp.get("instructions") or [])
    for ix in ixs:
        idx = ix.get("programIdIndex")
        if idx is None or idx >= len(acct) or acct[idx] != program_id:
            continue
        try:
            data = b58decode(ix.get("data") or "")
        except ValueError:
            continue
        if data[:8] == EVENT_CPI_TAG and len(data) >= 16:
            keys.add("evt:" + data[8:16].hex())
        elif len(data) >= 8:
            keys.add("ixd:" + data[:8].hex())
    return keys


def stage_vocab(client: Any, watch: Mapping[str, Any], prev: Mapping[str, Any], prog_obs: Mapping[str, Any], n_txs: int,
                errors: list[str], runs: Mapping[str, int] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """`prev` = {program: seen keys}; `runs` = {program: successful earlier runs}. Alerts only after VOCAB_WARMUP_RUNS runs."""
    runs = runs or {}
    progs: Mapping[str, Mapping[str, Any]] = watch.get("programs") or {}
    obs: dict[str, Any] = {}
    changes: list[dict[str, Any]] = []
    unreachable = 0
    for n, spec in progs.items():
        if not spec.get("vocab"):
            continue
        pid = spec["id"]
        seen: Counter = Counter()
        n_ok = 0
        try:
            sigs = client.call("getSignaturesForAddress", [pid, {"limit": 50, "commitment": "finalized"}]) or []
            for s in [s for s in sigs if isinstance(s, dict) and s.get("err") is None][:n_txs]:
                try:
                    tx = fetch_tx(client, s["signature"])
                except ITEM_ERRORS as exc:
                    errors.append(f"vocab {n}: getTransaction {type(exc).__name__}")
                    if isinstance(exc, RpcUnreachable):
                        unreachable += 1
                    if unreachable >= 3 or isinstance(exc, CallBudgetExceeded):
                        raise
                    continue
                if tx:
                    n_ok += 1
                    seen.update(vocab_from_tx(tx, pid))
        except ITEM_ERRORS as exc:
            errors.append(f"vocab {n}: {type(exc).__name__}")
            obs[n] = {"status": "unread", "txs": n_ok}
            if unreachable >= 3 or isinstance(exc, CallBudgetExceeded):
                break
            continue
        p = prev.get(n)
        o = {"status": "ok" if n_ok else "empty", "txs": n_ok, "keys": sorted(seen)[:VOCAB_MAX_KEYS], "n_keys": len(seen)}
        obs[n] = o
        if p is None or runs.get(n, 0) < VOCAB_WARMUP_RUNS:
            o["warmup"] = True
            continue
        new = sorted(k for k in seen if k not in set(p))
        if new:
            sev = "high" if (prog_obs.get(n) or {}).get("upgraded") else "med"
            changes.append(change(sev, "new_vocab", n, f"{n} new in {n_ok} sampled txs: " + ", ".join(new[:8]) + (" ..." if len(new) > 8 else ""),
                                  keys=new[:32]))
    return obs, changes


def merge_vocab(prev: Mapping[str, Sequence[str]], prev_runs: Mapping[str, int], obs: Mapping[str, Any]) -> tuple[dict[str, list[str]], dict[str, int]]:
    out = {k: list(v) for k, v in prev.items()}
    runs = dict(prev_runs)
    for n, o in obs.items():
        if o.get("status") != "ok":
            continue
        merged = sorted(set(out.get(n, [])) | set(o.get("keys") or []))
        out[n] = merged[:VOCAB_MAX_KEYS]
        runs[n] = runs.get(n, 0) + 1
    return out, runs


# =============================================================================================================== W5 tapes
def _iter_lines(path: Path) -> Iterator[bytes]:
    if path.name.endswith(".zst"):
        p = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert p.stdout is not None
        try:
            yield from p.stdout
        finally:
            p.stdout.close()
            rc = p.wait()
        if rc != 0:
            raise OSError(f"zstd -dc exited {rc} for {path.name}")
    else:
        with open(path, "rb") as fh:
            yield from fh


def _safe(key: str) -> str:
    return key if SAFE_KEY.match(key) else "other"


def count_arena_file(path: Path) -> Counter:
    """Rows per '<prog>/<type>' (plus 'bad_line'). Only the two leading keys are parsed on the fast path. Rows of INTERNAL_TYPES (gap, hb,
    pool_info, ...) are never counted: a feed-health aggregate is not a structure count (QP-RULING-1024-seal2 r2)."""
    c: Counter = Counter()
    for ln in _iter_lines(path):
        m = ARENA_PREFIX.match(ln)
        if m:
            t = m.group(1).decode()
            if t not in INTERNAL_TYPES:
                c[f"{_safe(m.group(2).decode())}/{_safe(t)}"] += 1
            continue
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            c["bad_line"] += 1
            continue
        if not isinstance(r, dict):
            c["bad_line"] += 1
            continue
        if str(r.get("type")) in INTERNAL_TYPES:
            continue
        c[f"{_safe(str(r.get('prog')))}/{_safe(str(r.get('type')))}"] += 1
    return c


def count_venue_file(path: Path) -> Counter:
    """Rows per type; per instruction name on rows not failed (ix:<name>, once per row); failed rows; undecoded rows by discriminator."""
    c: Counter = Counter()
    for ln in _iter_lines(path):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            c["bad_line"] += 1
            continue
        if not isinstance(r, dict):
            c["bad_line"] += 1
            continue
        if str(r.get("type")) in INTERNAL_TYPES:
            continue  # gap / hb / start / stop rows are never counted (QP-RULING-1024-seal2 r2)
        t = _safe(str(r.get("type")))
        c[t] += 1
        if r.get("failed"):
            c["failed"] += 1
        elif t in ("ix", "trade", "create") and isinstance(r.get("ix"), list):
            for name in set(x for x in r["ix"] if isinstance(x, str)):
                c["ix:" + _safe(name)] += 1
        if t == "event_undecoded":
            c["undecoded:" + _safe(str(r.get("disc") or r.get("reason") or "none"))] += 1
    return c


def tape_days(root: Path) -> dict[str, dict[int, Path]]:
    """{day: {hour: file}} for tape-<day>T<hh>.jsonl[.zst] in `root`; the .zst wins when both exist."""
    out: dict[str, dict[int, Path]] = {}
    if not root.is_dir():
        return out
    for p in root.iterdir():
        m = TAPE_NAME.match(p.name)
        if not m or not p.is_file():
            continue
        day, hour, zst = m.group(1), int(m.group(2)), bool(m.group(3))
        cur = out.setdefault(day, {}).get(hour)
        if cur is None or (zst and not cur.name.endswith(".zst")):
            out[day][hour] = p
    return out


def closed_days(days: Iterable[str], now: float) -> list[str]:
    out = []
    for d in days:
        end = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        if end.timestamp() + DAY_CLOSE_GRACE_S <= now:
            out.append(d)
    return sorted(out)


def tape_sources(arena: Path | None, venue_root: Path | None) -> dict[str, tuple[Path, Callable[[Path], Counter]]]:
    src: dict[str, tuple[Path, Callable[[Path], Counter]]] = {}
    if arena is not None:
        src["arena"] = (arena, count_arena_file)
    if venue_root is not None and venue_root.is_dir():
        for d in sorted(venue_root.iterdir()):
            if d.is_dir() and re.fullmatch(r"[a-z0-9_]{1,40}", d.name):
                src[f"venue:{d.name}"] = (d, count_venue_file)
    return src


def _ts(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def load_seals(path: Path) -> dict[str, Any]:
    """Pinned sealed windows. Fail closed: a missing file, bad JSON or a malformed window is a RadarError (exit 2)."""
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise RadarError(f"seals file unreadable ({type(exc).__name__}): refusing to count tapes") from exc
    return load_seals_dict(cfg)


def load_seals_dict(cfg: Any) -> dict[str, Any]:
    seals = cfg.get("seals") if isinstance(cfg, dict) else None
    dis = cfg.get("disabled_until", {}) if isinstance(cfg, dict) else None
    if not isinstance(seals, list) or not isinstance(dis, dict):
        raise RadarError("seals file malformed")
    out = []
    try:
        for w in seals:
            lo, hi = _ts(w["start"]), _ts(w["end"])
            if not (isinstance(w["source"], str) and lo < hi):
                raise ValueError("window")
            out.append({"source": w["source"], "lo": lo, "hi": hi})
        disabled = {str(k): _ts(v) for k, v in dis.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise RadarError("seals file malformed") from exc
    return {"seals": out, "disabled_until": disabled}


def _applies(pattern: str, source: str) -> bool:
    return pattern in ("*", source) or (pattern.endswith(":*") and source.startswith(pattern[:-1]))


def source_disabled(seals: Mapping[str, Any], source: str, now: float) -> bool:
    return any(_applies(k, source) and now < t for k, t in (seals.get("disabled_until") or {}).items())


def day_sealed(seals: Mapping[str, Any], source: str, day: str) -> bool:
    """True when the UTC day [00Z, 24Z) of `source` overlaps any sealed window (permanent: also after the window ends)."""
    lo = _ts(day + "T00:00:00Z")
    hi = lo + 86_400
    return any(_applies(w["source"], source) and lo < w["hi"] and w["lo"] < hi for w in seals.get("seals") or [])


def stage_tapes(sources: Mapping[str, tuple[Path, Callable[[Path], Counter]]], done: set[tuple[str, str]], now: float, lookback_days: int,
                errors: list[str], seals: Mapping[str, Any] | None = None, skipped: list[str] | None = None) -> list[dict[str, Any]]:
    """Count every closed, not yet counted, unsealed day that started within the last `lookback_days` days (oldest first). One record per
    (source, day). A disabled source is not listed; a sealed day is never opened and leaves no record (only its name in `skipped`)."""
    if seals is None:
        raise RadarError("stage_tapes needs the sealed-window list")
    skipped = [] if skipped is None else skipped
    recs: list[dict[str, Any]] = []
    cutoff = (datetime.fromtimestamp(now, tz=timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    for name, (root, counter) in sources.items():
        if source_disabled(seals, name, now):
            skipped.append(f"{name} disabled")
            continue
        days = tape_days(root)
        todo = []
        for d in closed_days(days, now):
            if (name, d) in done or d < cutoff:
                continue
            if day_sealed(seals, name, d):
                skipped.append(f"{name} {d} sealed")
                continue
            todo.append(d)
        for d in todo:
            total: Counter = Counter()
            bad_files = 0
            for h, p in sorted(days[d].items()):
                try:
                    total.update(counter(p))
                except OSError as exc:
                    bad_files += 1
                    errors.append(f"tape {name} {p.name}: {type(exc).__name__}")
            hours = len(days[d]) - bad_files
            recs.append({"schema": COUNTS_SCHEMA, "source": name, "day": d, "hours": hours, "bad_files": bad_files,
                         "coverage_short": hours < MIN_HOURS, "counts": dict(sorted(total.items()))})
    return recs


# =============================================================================================================== W5/W6 rules
def venue_metrics(rec: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """Launches / trades / graduations per venue from one counts record (raw counts, not scaled)."""
    c: Mapping[str, int] = rec.get("counts") or {}
    src = rec.get("source")
    if src == "arena":
        return {"pumpfun": {"launches": c.get("pumpfun/create", 0), "trades": c.get("pumpfun/trade", 0), "grads": c.get("pumpfun/complete", 0)},
                "pumpswap": {"trades": c.get("pumpswap/trade", 0), "pools": c.get("pumpswap/pool_create", 0)}}
    if src == "venue:launchlab":
        return {"launchlab": {"launches": c.get("create", 0), "trades": c.get("trade", 0),
                              "grads": sum(v for k, v in c.items() if k.startswith("ix:Migrate"))}}
    if src == "venue:meteora_dbc":
        return {"meteora_dbc": {"launches": sum(v for k, v in c.items() if k.startswith("ix:InitializeVirtualPool")),
                                "trades": sum(v for k, v in c.items() if k.startswith("ix:Swap")),
                                "grads": sum(v for k, v in c.items() if k.startswith("ix:Migration"))}}
    if isinstance(src, str) and src.startswith("venue:"):
        return {src.split(":", 1)[1]: {"trades": c.get("trade", 0), "launches": c.get("create", 0)}}
    return {}


def _rate(n: int, hours: int) -> float:
    return n * 24.0 / hours if hours else 0.0


def _baseline(history: Sequence[Mapping[str, Any]], source: str, day: str) -> list[Mapping[str, Any]]:
    prior = [r for r in history if r.get("source") == source and r.get("day", "") < day and not r.get("coverage_short")]
    return sorted(prior, key=lambda r: r["day"])[-BASELINE_DAYS:]


def _shift_change(subject: str, label: str, day: str, rate: float, base: float) -> dict[str, Any] | None:
    if max(rate, base) < SHIFT_MIN_ROWS:
        return None
    ratio = rate / base if base > 0 else float("inf")
    if ratio >= SHIFT_HIGH or ratio <= 1 / SHIFT_HIGH:
        sev = "high"
    elif ratio >= SHIFT_MED or ratio <= 1 / SHIFT_MED:
        sev = "med"
    else:
        return None
    r = "inf" if ratio == float("inf") else f"{ratio:.2f}"
    return change(sev, "count_shift", subject, f"{subject} {label} day {day} {rate:.0f}/day vs baseline median {base:.0f}/day (x{r})", day=day)


def tape_changes(new_recs: Sequence[Mapping[str, Any]], history: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rules of DESIGN section 2 for each newly counted, covered source-day against the earlier covered days in `history`."""
    out: list[dict[str, Any]] = []
    for rec in new_recs:
        if rec.get("coverage_short"):
            continue
        src, day, hours = rec["source"], rec["day"], rec["hours"]
        base = _baseline(history, src, day)
        if len(base) < MIN_BASELINE_DAYS:
            continue
        counts: Mapping[str, int] = rec.get("counts") or {}
        keys = set(counts) | {k for b in base for k in (b.get("counts") or {})}
        for k in sorted(keys):
            if k.rsplit("/", 1)[-1] in INTERNAL_TYPES:
                continue
            n = counts.get(k, 0)
            brates = [_rate((b.get("counts") or {}).get(k, 0), b["hours"]) for b in base]
            if n >= NEW_KEY_MIN and all(x == 0 for x in brates):
                out.append(change("high", "new_key", f"{src} {k}", f"{src} {k} day {day} n={n} baseline 0 on {len(base)} days", day=day))
                continue
            med = statistics.median(brates)
            if n == 0 and med >= GONE_KEY_BASELINE:
                out.append(change("high", "gone_key", f"{src} {k}", f"{src} {k} day {day} n=0 baseline median {med:.0f}/day", day=day))
                continue
            c = _shift_change(f"{src} {k}", "rows", day, _rate(n, hours), med)
            if c:
                out.append(c)
    return out


def share_changes(day_recs: Sequence[Mapping[str, Any]], history: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Launch and trade share per venue on each newly counted day, vs the median share on earlier days with the same covered venues."""
    out: list[dict[str, Any]] = []

    def shares(recs: Iterable[Mapping[str, Any]], metric: str) -> dict[str, float]:
        tot: dict[str, float] = {}
        for r in recs:
            for v, m in venue_metrics(r).items():
                if metric in m:
                    tot[v] = tot.get(v, 0.0) + _rate(m[metric], r["hours"])
        s = sum(tot.values())
        return {v: x / s for v, x in tot.items()} if s > 0 else {}

    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for r in history:
        if not r.get("coverage_short"):
            by_day.setdefault(r["day"], []).append(r)
    for day in sorted({r["day"] for r in day_recs if not r.get("coverage_short")}):
        cur = by_day.get(day, [])
        srcs = {r["source"] for r in cur}
        if len(srcs) < 2:
            continue
        prior = [d for d in sorted(by_day) if d < day and {r["source"] for r in by_day[d]} == srcs][-BASELINE_DAYS:]
        if len(prior) < MIN_BASELINE_DAYS:
            continue
        for metric in ("launches", "trades"):
            now_s = shares(cur, metric)
            base_s = [shares(by_day[d], metric) for d in prior]
            for v, s in sorted(now_s.items()):
                med = statistics.median([b.get(v, 0.0) for b in base_s])
                if abs(s - med) >= SHARE_PP:
                    out.append(change("med", "share_shift", f"{v} {metric}", f"{v} share of {metric} day {day} {s:.1%} vs baseline median {med:.1%}", day=day))
    return out


# =============================================================================================================== W7 monitor
def stage_monitor(path: Path, last_seen: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.is_file():
        return {"status": "absent"}, []
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 262_144))
        tail = fh.read().splitlines()
    rec = None
    for ln in reversed(tail):
        try:
            rec = json.loads(ln)
            break
        except ValueError:
            continue
    if not isinstance(rec, dict):
        return {"status": "unreadable"}, []
    run = str(rec.get("run_utc"))
    info = {"status": "ok", "run_utc": run}
    if last_seen is not None and run <= last_seen:
        info["already_reported"] = True
        return info, []
    out: list[dict[str, Any]] = []
    for name, f in sorted(((rec.get("halt") or {}).get("flags") or {}).items()):
        if isinstance(f, dict) and f.get("halt"):
            out.append(change("high", "monitor_halt", name, f"structure monitor {run} HALT {name}: {str(f.get('reason'))[:160]}"))
    for name, f in sorted(((rec.get("warn") or {}).get("flags") or {}).items()):
        if isinstance(f, dict) and f.get("warn"):
            out.append(change("low", "monitor_warn", name, f"structure monitor {run} WARN {name}: {str(f.get('reason'))[:160]}"))
    return info, out


# =============================================================================================================== state / io
def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for ln in path.read_text().splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def append_jsonl(path: Path, recs: Iterable[Mapping[str, Any]]) -> None:
    with open(path, "a") as fh:
        for r in recs:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def sort_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(changes, key=lambda c: (SEVERITY_ORDER.get(c["sev"], 9), c["kind"], c["subject"]))


def digest_lines(rec: Mapping[str, Any]) -> list[str]:
    ch = rec["changes"]
    n = Counter(c["sev"] for c in ch)
    st = rec["stages"]
    progs = st.get("programs") or {}
    p_ok = sum(1 for o in progs.values() if o.get("status") == "ok")
    accts = st.get("accounts") or {}
    a_ok = sum(1 for o in accts.values() if o.get("exists"))
    voc = st.get("vocab") or {}
    v_ok = sum(1 for o in voc.values() if o.get("status") == "ok")
    head = (f"CHANGES {rec['run_date']} high={n['high']} med={n['med']} low={n['low']} (programs {p_ok}/{len(progs)} read, accounts {a_ok}/{len(accts)}, "
            f"vocab {v_ok}/{len(voc)}, tape days {len(st.get('tape_days_counted') or [])}, tape skipped {len(st.get('tape_skipped') or [])}, status {rec['status']})")
    if rec.get("baseline_run"):
        head += " baseline run"
    if not ch:
        return [head, "CHANGES none"]
    return [head] + [f"CHANGE {c['sev'].upper()} {c['kind']} {c['detail']}" for c in ch]


def console_events(rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, c in enumerate(x for x in rec["changes"] if x["sev"] in ("high", "med")):
        out.append({"id": f"radar-{rec['run_date']}-{i + 1}", "kind": "finding", "title": f"Change radar {c['sev'].upper()}: {c['kind']} {c['subject']}"[:120],
                    "detail": c["detail"][:400] + " Structure count only; no book has cleared the promotion gate.", "atUtc": rec["run_utc"]})
    return out


def markdown(rec: Mapping[str, Any], counts: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"# Change radar {rec['run_date']}", "", f"Run {rec['run_utc']}, status {rec['status']}, RPC host {rec['rpc_host']}, "
             f"calls {rec.get('rpc_calls')}. Counts and structure only; nothing here says a book makes money.", ""]
    lines += ["```"] + digest_lines(rec) + ["```", ""]
    progs = rec["stages"].get("programs") or {}
    if progs:
        lines += ["| program | tier | status | deploy slot | deploy utc |", "| --- | --- | --- | --- | --- |"]
        for n, o in progs.items():
            lines.append(f"| {n} | {o.get('tier')} | {o.get('status')} | {o.get('deploy_slot', '')} | {o.get('deploy_utc') or ''} |")
        lines.append("")
    for r in counts:
        m = venue_metrics(r)
        cov = "SHORT" if r.get("coverage_short") else "ok"
        lines.append(f"- {r['source']} {r['day']}: hours {r['hours']} ({cov}); " + "; ".join(
            f"{v} " + ", ".join(f"{k} {x}" for k, x in mm.items()) for v, mm in m.items()))
    if rec.get("errors"):
        lines += ["", "Errors: " + "; ".join(rec["errors"][:20])]
    return "\n".join(lines) + "\n"


# =============================================================================================================== main
def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--out-dir", default=DEFAULT_OUT)
    p.add_argument("--watch", default=str(DEFAULT_WATCH))
    p.add_argument("--seals", default=str(DEFAULT_SEALS), help="pinned sealed-window list (fail closed)")
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--arena-tape", default=DEFAULT_ARENA, help="'' to skip")
    p.add_argument("--venue-tape", default=DEFAULT_VENUE, help="'' to skip")
    p.add_argument("--monitor", default=DEFAULT_MONITOR, help="'' to skip")
    p.add_argument("--vocab-txs", type=int, default=20)
    p.add_argument("--max-calls", type=int, default=300)
    p.add_argument("--lookback-days", type=int, default=8, help="only closed tape days that started within this many days are counted")
    p.add_argument("--no-rpc", action="store_true", help="skip W1-W4")
    return p.parse_args(argv)


def run(a: argparse.Namespace, *, client: Any = None, now: float | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    now = time.time() if now is None else now
    host = check_rpc_url(a.rpc)
    arena = check_tape_dir(a.arena_tape) if a.arena_tape else None
    venue = check_tape_dir(a.venue_tape) if a.venue_tape else None
    seals = load_seals(Path(a.seals))
    watch = load_json(Path(a.watch), None)
    if not isinstance(watch, dict) or not isinstance(watch.get("programs"), dict):
        raise RadarError(f"watch file unreadable: {a.watch}")
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    state = load_json(out / "state.json", {})
    history = load_jsonl(out / "daily-counts.jsonl")
    errors: list[str] = []
    changes: list[dict[str, Any]] = []
    stages: dict[str, Any] = {}
    new_state = dict(state)
    if not a.no_rpc:
        client = client or RpcClient(a.rpc, max_calls=a.max_calls)
        try:
            pobs, pch = stage_programs(client, watch, state.get("programs") or {}, errors)
            stages["programs"] = pobs
            changes += pch
            upgraded = {c["subject"] for c in pch if c["kind"] == "program_upgrade"}
            for n in upgraded:
                pobs[n]["upgraded"] = True
            new_state["programs"] = {**(state.get("programs") or {}),
                                     **{n: {k: o.get(k) for k in ("id", "programdata", "deploy_slot", "authority", "deploy_utc")} for n, o in pobs.items() if o.get("status") == "ok"}}
        except (*ITEM_ERRORS, RuntimeError, ValueError) as exc:
            errors.append(f"programs: {type(exc).__name__}: {str(exc)[:160]}")
            pobs = {}
        try:
            aobs, ach = stage_accounts(client, watch, state.get("accounts") or {})
            stages["accounts"] = aobs
            changes += ach
            new_state["accounts"] = {**(state.get("accounts") or {}), **{n: {k: o[k] for k in ("address", "exists", "len", "sha256")} for n, o in aobs.items()}}
        except (*ITEM_ERRORS, RuntimeError, ValueError) as exc:
            errors.append(f"accounts: {type(exc).__name__}: {str(exc)[:160]}")
        vobs, vch = stage_vocab(client, watch, state.get("vocab") or {}, stages.get("programs") or {}, a.vocab_txs, errors, state.get("vocab_runs") or {})
        stages["vocab"] = {n: {k: v for k, v in o.items() if k != "keys"} for n, o in vobs.items()}
        changes += vch
        new_state["vocab"], new_state["vocab_runs"] = merge_vocab(state.get("vocab") or {}, state.get("vocab_runs") or {}, vobs)
    sources = tape_sources(arena, venue)
    done = {(r.get("source"), r.get("day")) for r in history}
    skipped: list[str] = []
    new_counts = stage_tapes(sources, done, now, a.lookback_days, errors, seals, skipped)
    stages["tape_days_counted"] = [f"{r['source']} {r['day']}" for r in new_counts]
    stages["tape_skipped"] = skipped
    all_hist = history + new_counts
    changes += tape_changes(new_counts, all_hist) + share_changes(new_counts, all_hist)
    if a.monitor:
        minfo, mch = stage_monitor(check_tape_dir(a.monitor), state.get("monitor_last_run_utc"))
        stages["monitor"] = minfo
        changes += mch
        if minfo.get("status") == "ok":
            new_state["monitor_last_run_utc"] = minfo["run_utc"]
    run_utc = iso(now)
    rec = {"schema": SCHEMA, "run_utc": run_utc, "run_date": run_utc[:10], "rpc_host": None if a.no_rpc else host,
           "rpc_calls": getattr(client, "total_calls", None), "baseline_run": not state, "stages": stages,
           "changes": sort_changes(changes), "errors": errors, "status": "partial" if errors else "ok"}
    new_state["updated_utc"] = run_utc
    return rec, new_counts, new_state


def write_outputs(out: Path, rec: Mapping[str, Any], new_counts: Sequence[Mapping[str, Any]], new_state: Mapping[str, Any]) -> None:
    append_jsonl(out / "daily-counts.jsonl", new_counts)
    append_jsonl(out / "radar.jsonl", [rec])
    d = rec["run_date"]
    atomic_write(out / f"digest-{d}.json", json.dumps({"run_utc": rec["run_utc"], "lines": digest_lines(rec), "changes": rec["changes"]}, indent=1) + "\n")
    atomic_write(out / f"digest-{d}.md", markdown(rec, new_counts))
    atomic_write(out / f"console-events-{d}.json", json.dumps(console_events(rec), indent=1) + "\n")
    atomic_write(out / "state.json", json.dumps(new_state, indent=1, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None, *, client: Any = None, now: float | None = None) -> int:
    a = parse_args(argv)
    try:
        rec, new_counts, new_state = run(a, client=client, now=now)
    except RadarError as exc:
        print(f"change_radar: {exc}", file=sys.stderr)
        return 2
    lines = digest_lines(rec)
    try:
        write_outputs(Path(a.out_dir), rec, new_counts, new_state)
    except OSError as exc:
        print("\n".join(lines))
        print(f"change_radar: cannot write outputs: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
