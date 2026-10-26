"""HV6 regime evidence block (`hv6_regime.v1`) and the regime-day list. Paper only; no network, no API call.

Builds the machine-written block that goes into HV6's nightly reviewer input from the P23 change radar's append-only `radar.jsonl`, read AS OF
the block time (records with run_utc strictly before as_of_ms). Design: docs/research/hv6/DESIGN.md section 3 (branch claude/hv5-design);
QP-RULING-1025-hv5-design R1-R8, ADDENDUM 1 (V2-4 subject mapping, regime-day rule).

What the block holds: enums, hex ids, UTC instants and small integers, all produced here. What it never holds: `detail`, `title`, `subject`,
`keys`, `ratio`, `n_writes`, rpc_host, rpc_calls, errors, stages, or any string copied from a radar record. A radar string is only ever used as a
lookup key in an exact-match table (hit -> table value, miss -> OTHER / other) or after a strict regex check (instants, day). So no
free text reaches the reviewer and a hostile string in a radar field has no path into the block.

Block spec (hv6_regime.v1). Top-level keys: schema, as_of_ms, as_of_utc, pins, pin_mismatch, input, counts, events, summary, radar_status.
Event fields (all enums, RG ids or strict UTC instants): id, kind (12 real radar kinds), sev (HIGH/MED/LOW), tier (core/venue/router/none),
program (14 watch-file names or OTHER), relevance (direct/adjacent), change_utc, change_basis (at_utc/day/run_utc), key_class (swap/create/
complete/pool_create/other), first_run_utc, hours_since. Beyond DESIGN s3's field table: the fourth tier value `none` (OTHER programs and
monitor_*), `change_basis`, and `first_run_utc`.
Bad-line policy (QP accepts): a malformed or wrong-schema line BEFORE the first record with run_utc >= as_of makes radar_status `absent` for
that block (and every later block); a bad line at or after that point never changes the block. A line whose run_utc cannot be parsed is placed
by position. A torn last line (no newline) is ignored.
Event ids: `RG-` + sha256(kind|program|when)[:8]; when = at_utc, else `day`, else the date of the run that first carried a condition. A
condition with neither (program_gone, authority_change, account_closed, config_change, new_vocab, monitor_*) that fires on consecutive radar
run days keeps ONE id (the first date of the unbroken run; QP rule (i)). A record dated between two sightings that lacks the condition breaks
the run; a day with no record does not. A standing monitor HALT is therefore one event, not a new regime day every day.
first_run_utc / t_e = the first run_utc at which the id is carried with its final (highest) severity.
Pins: the registration pins the radar job's own code, seals and watch PATHS and shas, the out-dir and venue tape. All three paths and the
out-dir are required arguments. A radar record carries no code sha, so a change made and reverted between two blocks is not visible.

Commands:
  block        build the block for --as-of-ms, snapshot the consumed bytes, write block-<as_of_ms>.json beside the copy.
  regime-days  from HV6's saved copies, write the E list and regime-day list once (write-once file).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

BLOCK_SCHEMA = "hv6_regime.v1"
DAYS_SCHEMA = "hv6_regime_days.v1"
RADAR_SCHEMA = "change_radar.v1"

# ---- pins (R3) ------------------------------------------------------------------------------------------------------------------------
PINNED_SEALS_SHA256 = "5a2f8979c8493f7a3b200dd879f6f54c92a3bf383054af432c2fc35a8e10ca61"
PINNED_OUT_DIR = "/data/mal/change-radar-arena1"
PINNED_VENUE_TAPE = ""
PIN_KEYS = ("radar_code_path", "radar_code_sha256", "seals_path", "seals_sha256", "watch_path", "watch_sha256", "out_dir", "venue_tape")

STALE_HOURS = 36  # latest record older than this before as_of_ms -> stale
LOOKBACK_DAYS = 7  # events listed in the block: first record carrying them within this window
REGIME_HOURS = 72
DEGRADED_HOURS = 12

# ---- real radar kinds (R1), pinned to tools/change_radar.py at b4a0f0d5. test_kinds_match_radar_source checks this against the literals. ----
KINDS = (
    "program_upgrade", "program_gone", "authority_change", "account_closed", "config_change", "new_vocab",
    "count_shift", "new_key", "gone_key", "share_shift", "monitor_halt", "monitor_warn",
)
# How the program of each kind is found (subject is only ever a lookup key):
#   program : subject is a watch-file program name
#   account : subject is a watch-file account name; the program is that account's `program`
#   tape    : subject is "<src> <prog>/<type>" (new_key, gone_key, count_shift) -> SUBJECT_TABLE
#   share   : subject is "<venue> <metric>" (share_shift) -> SUBJECT_TABLE
#   monitor : subject is a monitor flag name -> program OTHER, relevance direct by rule
SUBJECT_SOURCE = {
    "program_upgrade": "program", "program_gone": "program", "authority_change": "program", "new_vocab": "program",
    "account_closed": "account", "config_change": "account",
    "count_shift": "tape", "new_key": "tape", "gone_key": "tape", "share_shift": "share",
    "monitor_halt": "monitor", "monitor_warn": "monitor",
}
SEVS = ("HIGH", "MED", "LOW")
SEV_RANK = {"HIGH": 0, "MED": 1, "LOW": 2}
KEY_CLASSES = ("swap", "create", "complete", "pool_create", "other")
DIRECT_PROGRAMS = frozenset({"pump", "pumpswap", "pump_fees"})

# ---- exact-match table over `subject` (V2-4): subject -> (program, key_class). Anything else -> (OTHER, other), counted. -----------------
SUBJECT_TABLE: dict[str, tuple[str, str]] = {
    # arena tape keys "<prog>/<type>" (count_arena_file); source "arena"
    "arena pumpfun/create": ("pump", "create"),
    "arena pumpfun/trade": ("pump", "swap"),
    "arena pumpfun/complete": ("pump", "complete"),
    "arena pumpswap/trade": ("pumpswap", "swap"),
    "arena pumpswap/pool_create": ("pumpswap", "pool_create"),
    # venue tape keys (count_venue_file); only reachable if a radar ran with a venue tape, which the pin forbids (kept so a stray
    # record still maps to a known program rather than a free-text lookup miss)
    "venue:launchlab create": ("raydium_launchlab", "create"),
    "venue:launchlab trade": ("raydium_launchlab", "swap"),
    "venue:meteora_dbc create": ("meteora_dbc", "create"),
    "venue:meteora_dbc trade": ("meteora_dbc", "swap"),
    # share_shift subjects "<venue> <metric>" (venue_metrics)
    "pumpfun launches": ("pump", "create"),
    "pumpfun trades": ("pump", "swap"),
    "pumpfun grads": ("pump", "complete"),
    "pumpswap trades": ("pumpswap", "swap"),
    "pumpswap pools": ("pumpswap", "pool_create"),
    "launchlab launches": ("raydium_launchlab", "create"),
    "launchlab trades": ("raydium_launchlab", "swap"),
    "launchlab grads": ("raydium_launchlab", "complete"),
    "meteora_dbc launches": ("meteora_dbc", "create"),
    "meteora_dbc trades": ("meteora_dbc", "swap"),
    "meteora_dbc grads": ("meteora_dbc", "complete"),
}
# new_vocab: key_class from the radar's `keys` by a fixed exact-match table (keys themselves are never emitted). Several keys: the first
# class in this precedence order that any key hits. Hex discriminators (data:/ixd:/evt:) are `other`.
VOCAB_KEY_TABLE: dict[str, str] = {
    "ix:Create": "create", "ix:CreateV2": "create", "ix:Buy": "swap", "ix:Sell": "swap", "ix:BuyExactQuoteIn": "swap",
    "ix:Swap": "swap", "ix:Migrate": "complete", "ix:CreatePool": "pool_create",
}
KEY_CLASS_PRECEDENCE = ("create", "pool_create", "complete", "swap", "other")

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---- small helpers ----------------------------------------------------------------------------------------------------------------------
def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def parse_ts(s: Any) -> datetime | None:
    """Strict 'YYYY-MM-DDTHH:MM:SSZ' only (the radar's iso()). Anything else -> None."""
    if not isinstance(s, str) or not _TS.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_day(s: Any) -> datetime | None:
    if not isinstance(s, str) or not _DAY.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fmt_ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def load_watch(text: bytes) -> tuple[dict[str, str], dict[str, str]]:
    """({program: tier}, {account: program}) from the watch file bytes. Only names present as keys are used, as lookup keys."""
    w = json.loads(text)
    progs = {n: str(p.get("tier", "")) for n, p in (w.get("programs") or {}).items() if isinstance(p, dict)}
    accts = {n: str(a.get("program", "")) for n, a in (w.get("accounts") or {}).items() if isinstance(a, dict)}
    return progs, accts


# ---- mapping one radar change to a block event -----------------------------------------------------------------------------------------
def map_subject(kind: str, subject: Any, keys: Any, programs: Mapping[str, str], accounts: Mapping[str, str]) -> tuple[str, str, bool]:
    """(program, key_class, was_other). `subject` and `keys` are lookup keys only; the return values are enum members."""
    src = SUBJECT_SOURCE[kind]
    s = subject if isinstance(subject, str) else None
    if src == "monitor":
        return "OTHER", "other", False  # by design: monitor flags carry no program
    if src == "program":
        prog = s if s is not None and s in programs else None
        kc = "other"
        if kind == "new_vocab" and isinstance(keys, list):
            hit = {VOCAB_KEY_TABLE[k] for k in keys if isinstance(k, str) and k in VOCAB_KEY_TABLE}
            kc = next((c for c in KEY_CLASS_PRECEDENCE if c in hit), "other")
        return (prog or "OTHER"), kc, prog is None
    if src == "account":
        prog = accounts.get(s) if s is not None else None
        if prog is not None and prog not in programs:
            prog = None
        return (prog or "OTHER"), "other", prog is None
    hit2 = SUBJECT_TABLE.get(s) if s is not None else None
    if hit2 is None:
        return "OTHER", "other", True
    return hit2[0], hit2[1], False


def event_when(ch: Mapping[str, Any], run_utc: str) -> tuple[str, str]:
    """(change_utc, basis). at_utc if it passes the strict instant check, else `day` 00:00Z, else the record's run_utc."""
    at = ch.get("at_utc")
    if parse_ts(at) is not None:
        return at, "at_utc"
    d = parse_day(ch.get("day"))
    if d is not None:
        return fmt_ts(d), "day"
    return run_utc, "run_utc"


def make_id(kind: str, program: str, change_utc: str, basis: str) -> str:
    """`RG-` + first 8 hex of sha256(kind, program, at_utc or day). When neither exists the record's run_date stands in (see ambiguity note)."""
    when = change_utc if basis == "at_utc" else change_utc[:10]
    return "RG-" + sha256_hex(f"{kind}|{program}|{when}".encode())[:8]


def parse_records(raw: bytes, as_of: datetime | None) -> tuple[list[tuple[bytes, dict[str, Any]]], int]:
    """Whole, newline-terminated lines only (a torn last line is not yet written). Walks the file in order and STOPS at the first valid record
    with run_utc >= as_of: nothing at or after that point can change the block. Returns ([(line_bytes, rec)] in file order, n_bad) where n_bad
    counts only malformed / wrong-schema lines before that stop. With as_of None the whole file is read."""
    out: list[tuple[bytes, dict[str, Any]]] = []
    bad = 0
    end = raw.rfind(b"\n")
    if end < 0:
        return out, 0
    for ln in raw[: end + 1].split(b"\n")[:-1]:
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            bad += 1
            continue
        t = parse_ts(rec.get("run_utc")) if isinstance(rec, dict) else None
        if (not isinstance(rec, dict) or rec.get("schema") != RADAR_SCHEMA or t is None or not isinstance(rec.get("changes"), list)):
            bad += 1
            continue
        if as_of is not None and t >= as_of:
            break
        out.append((ln + b"\n", rec))
    return out, bad


def extract_events(records: Sequence[tuple[bytes, Mapping[str, Any]]], programs: Mapping[str, str], accounts: Mapping[str, str]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Deduplicated events by RG id and drop counters. Highest sev wins; first_run_utc is the first run at which the id is carried with that
    final sev. Conditions with neither at_utc nor day that fire on consecutive radar run days share one id (see module docstring)."""
    records = sorted(records, key=lambda t: t[1]["run_utc"])  # stable: ties keep file order
    record_dates = sorted({r["run_utc"][:10] for _, r in records})
    events: dict[str, dict[str, Any]] = {}
    first_sev: dict[tuple[str, str], str] = {}
    streak: dict[tuple, tuple[str, str]] = {}  # key -> (last date seen, first run_utc of the unbroken run)
    stats = {"n_dropped_unknown": 0, "n_other_mapped": 0}
    for _, rec in records:
        run_utc = rec["run_utc"]
        for ch in rec["changes"]:
            if not isinstance(ch, dict):
                stats["n_dropped_unknown"] += 1
                continue
            kind, sev = ch.get("kind"), ch.get("sev")
            sev_u = sev.upper() if isinstance(sev, str) else None
            if not isinstance(kind, str) or kind not in SUBJECT_SOURCE or sev_u not in SEVS:
                stats["n_dropped_unknown"] += 1
                continue
            program, key_class, other = map_subject(kind, ch.get("subject"), ch.get("keys"), programs, accounts)
            if other:
                stats["n_other_mapped"] += 1
            change_utc, basis = event_when(ch, run_utc)
            if basis == "run_utc":
                subj = ch.get("subject") if isinstance(ch.get("subject"), str) else None  # dict key only, never emitted
                sk = (kind, program, key_class, subj)
                d = run_utc[:10]
                last = streak.get(sk)
                if last is not None and (last[0] == d or not any(last[0] < x < d for x in record_dates)):
                    change_utc = last[1]
                streak[sk] = (d, change_utc)
            rid = make_id(kind, program, change_utc, basis)
            tier = programs.get(program, "none") if program != "OTHER" else "none"
            if tier not in ("core", "venue", "router"):
                tier = "none"
            first_sev.setdefault((rid, sev_u), run_utc)
            ev = {
                "id": rid, "kind": kind, "sev": sev_u, "tier": tier, "program": program,
                "relevance": "direct" if (program in DIRECT_PROGRAMS or kind.startswith("monitor_")) else "adjacent",
                "change_utc": change_utc, "change_basis": basis, "key_class": key_class, "first_run_utc": run_utc,
            }
            cur = events.get(rid)
            if cur is None or SEV_RANK[sev_u] < SEV_RANK[cur["sev"]]:
                events[rid] = ev
    for rid, ev in events.items():
        ev["first_run_utc"] = first_sev[(rid, ev["sev"])]
    return events, stats


# ---- the block ------------------------------------------------------------------------------------------------------------------------
def check_pins(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> list[str]:
    """Names of pins that differ. Seals sha and venue tape are also held to the module constants, so a registration cannot pin the wrong ones."""
    bad = [k for k in PIN_KEYS if expected.get(k) != observed.get(k)]
    if expected.get("seals_sha256") != PINNED_SEALS_SHA256 or observed.get("seals_sha256") != PINNED_SEALS_SHA256:
        bad.append("seals_sha256")
    if expected.get("venue_tape") != PINNED_VENUE_TAPE or observed.get("venue_tape") != PINNED_VENUE_TAPE:
        bad.append("venue_tape")
    return sorted(set(bad))


def _counted_venue(rec: Mapping[str, Any]) -> bool:
    st = rec.get("stages")
    days = st.get("tape_days_counted") if isinstance(st, dict) else None
    return isinstance(days, list) and any(isinstance(x, str) and x.startswith("venue:") for x in days)


def build_block(radar_bytes: bytes, as_of_ms: int, watch_bytes: bytes, pins_expected: Mapping[str, Any], pins_observed: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    """(block, consumed_bytes). consumed_bytes is the exact input snapshot: the selected lines in file order (empty when absent by pin)."""
    as_of = ms_to_dt(as_of_ms)
    pin_diff = check_pins(pins_expected, pins_observed)
    block: dict[str, Any] = {
        "schema": BLOCK_SCHEMA, "as_of_ms": as_of_ms, "as_of_utc": fmt_ts(as_of),
        "pins": {k: pins_observed.get(k) for k in PIN_KEYS}, "pin_mismatch": pin_diff,
        "input": {"sha256": None, "n_bytes": 0, "n_records": 0, "n_bad_records": 0, "latest_run_utc": None},
        "counts": {"n_dropped_unknown": 0, "n_other_mapped": 0, "n_events": 0},
        "events": [], "summary": {"n_high_core_7d": 0, "hours_since_last_high_direct": None}, "radar_status": "absent",
    }
    if pin_diff:
        return block, b""  # a changed radar is never consumed
    programs, accounts = load_watch(watch_bytes)
    recs, bad = parse_records(radar_bytes, as_of)
    consumed = b"".join(b for b, _ in recs)
    block["input"].update(sha256=sha256_hex(consumed), n_bytes=len(consumed), n_records=len(recs), n_bad_records=bad)
    venue_hit = any(_counted_venue(r) for _, r in recs)  # a record that counted a venue tape ran without `--venue-tape ''`
    if venue_hit:
        block["pin_mismatch"] = ["venue_tape"]
        return block, b""  # not consumed
    if not recs or bad:
        return block, consumed
    latest = max(parse_ts(r["run_utc"]) for _, r in recs)
    block["input"]["latest_run_utc"] = fmt_ts(latest)
    events, stats = extract_events(recs, programs, accounts)
    lo = as_of - timedelta(days=LOOKBACK_DAYS)
    listed = sorted((e for e in events.values() if parse_ts(e["first_run_utc"]) >= lo), key=lambda e: (e["first_run_utc"], e["id"]))
    for e in listed:
        e["hours_since"] = round(max(0.0, (as_of - parse_ts(e["change_utc"])).total_seconds()) / 3600.0, 1)
    hd = [e for e in listed if e["sev"] == "HIGH" and e["relevance"] == "direct"]
    block["events"] = listed
    block["counts"] = {**stats, "n_events": len(listed)}
    block["summary"] = {
        "n_high_core_7d": sum(1 for e in listed if e["sev"] == "HIGH" and e["tier"] == "core"),
        "hours_since_last_high_direct": min(e["hours_since"] for e in hd) if hd else None,
    }
    if (as_of - latest) > timedelta(hours=STALE_HOURS):
        block["radar_status"] = "stale"
    elif stats["n_dropped_unknown"]:
        block["radar_status"] = "partial"
    else:
        block["radar_status"] = "ok"
    return block, consumed


def observe_pins(radar_code: Path, seals: Path, watch: Path, out_dir: str, venue_tape: str) -> dict[str, Any]:
    """Observe the radar job's own files (absolute paths as given; never a default beside this checkout)."""
    ap = os.path.abspath
    return {"radar_code_path": ap(radar_code), "radar_code_sha256": sha256_hex(radar_code.read_bytes()),
            "seals_path": ap(seals), "seals_sha256": sha256_hex(seals.read_bytes()),
            "watch_path": ap(watch), "watch_sha256": sha256_hex(watch.read_bytes()),
            "out_dir": os.path.abspath(out_dir), "venue_tape": venue_tape}


def block_for_use(block: Mapping[str, Any], use_ms: int) -> Mapping[str, Any]:
    """The block as a CONSUMER must read it at `use_ms`: a block whose as_of_ms is more than STALE_HOURS before the use instant is `stale` (R1), even if it was `ok`
    when it was built, so a stopped producer cannot leave an old `ok` block in force. The input block is not changed; a usable block that is too old comes back as
    a copy with radar_status = "stale". Used by the runner's trigger, the reviewer's REGIME EVIDENCE and (as spans) the regime-day computation."""
    ms = block.get("as_of_ms")
    if isinstance(ms, int) and not isinstance(ms, bool) and use_ms - ms > STALE_HOURS * 3_600_000 and block.get("radar_status") in ("ok", "partial"):
        return {**block, "radar_status": "stale"}
    return block


def write_once(path: Path, data: bytes) -> None:
    """Create `path` with `data`; an existing file with identical bytes is fine, different bytes refuse (copies and lists are never rewritten)."""
    if path.exists():
        if path.read_bytes() != data:
            raise SystemExit(f"refuse: {path} exists with different bytes")
        return
    create_exclusive_ro(path, data)


def create_exclusive_ro(path: Path, data: bytes) -> None:
    """O_EXCL create, then mode 0444. Never overwrites."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(fd, data)
        os.fchmod(fd, 0o444)
    finally:
        os.close(fd)


def run_block(a: argparse.Namespace) -> int:
    expected = json.loads(Path(a.pins).read_text())
    observed = observe_pins(Path(a.radar_code), Path(a.seals), Path(a.watch), a.out_dir, a.venue_tape)
    radar_path = Path(a.out_dir) / "radar.jsonl"
    raw = radar_path.read_bytes() if radar_path.is_file() else b""
    block, consumed = build_block(raw, int(a.as_of_ms), Path(a.watch).read_bytes(), expected, observed)
    cdir = Path(a.copy_dir)
    if block["input"]["sha256"] is not None and consumed:
        write_once(cdir / f"radar-{block['as_of_ms']}.jsonl", consumed)
    write_once(cdir / f"block-{block['as_of_ms']}.json", dumps(block).encode())
    sys.stdout.write(dumps(block))
    return 0


# ---- regime-day list (ADDENDUM 1 section 3) -----------------------------------------------------------------------------------------------
def load_saved(copy_dir: Path) -> list[tuple[int, dict[str, Any], bytes]]:
    """(as_of_ms, block, copy_bytes) for every saved block, in as_of order. The copy must hash to the block's input sha256."""
    out = []
    for p in sorted(copy_dir.glob("block-*.json")):
        blk = json.loads(p.read_text())
        ms = int(blk["as_of_ms"])
        cp = copy_dir / f"radar-{ms}.jsonl"
        data = cp.read_bytes() if cp.is_file() else b""
        if blk["input"]["sha256"] is not None and sha256_hex(data) != blk["input"]["sha256"]:
            raise SystemExit(f"refuse: {cp} does not match the sha256 in {p.name}")
        out.append((ms, blk, data))
    return sorted(out, key=lambda t: t[0])


def degraded_days(blocks: Sequence[tuple[int, Mapping[str, Any]]], t0: datetime, n_days: int) -> list[str]:
    """Days where the block was absent or stale for more than DEGRADED_HOURS. Each block applies from its as_of to the NEXT block's as_of but at most
    STALE_HOURS (36 h): after as_of + 36 h with no newer block the status is `stale` (a producer gap counts). Before the first block the status is absent.
    Report-only: these days never change the regime-day set."""
    out = []
    for i in range(n_days):
        d0 = t0 + timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        bad = 0.0
        pts = sorted([(ms_to_dt(ms), b["radar_status"]) for ms, b in blocks], key=lambda x: x[0])
        # a block's span ends at the next block or at as_of + STALE_HOURS: add an explicit `stale` point at as_of + 36 h unless a newer block arrives first
        pts2 = []
        for i, (t, s) in enumerate(pts):
            pts2.append((t, s))
            end = t + timedelta(hours=STALE_HOURS)
            if s in ("ok", "partial") and (i + 1 == len(pts) or pts[i + 1][0] > end):
                pts2.append((end, "stale"))
        pts = sorted(pts2, key=lambda x: x[0])
        cur = "absent"
        edges = [(d0, None)] + [(t, s) for t, s in pts if d0 < t < d1]
        for t, s in pts:
            if t <= d0:
                cur = s
        for j, (t, s) in enumerate(edges):
            if s is not None:
                cur = s
            nxt = edges[j + 1][0] if j + 1 < len(edges) else d1
            if cur in ("absent", "stale"):
                bad += (nxt - t).total_seconds()
        if bad > DEGRADED_HOURS * 3600:
            out.append(fmt_ts(d0)[:10])
    return out


def regime_days(copy_dir: Path, t0: datetime, n_days: int, programs: Mapping[str, str], accounts: Mapping[str, str],
                pins_expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    saved = load_saved(copy_dir)
    if pins_expected is not None:
        for ms_, blk, _ in saved:
            if blk["pins"] != {k: pins_expected.get(k) for k in PIN_KEYS} and blk["radar_status"] != "absent":
                raise SystemExit(f"refuse: block {ms_} carries pins that differ from the registered pins but is not absent")
    eligible_records: list[tuple[bytes, dict[str, Any]]] = []
    for _, blk, data in saved:
        if blk["radar_status"] in ("ok", "partial") and data:
            recs, _ = parse_records(data, None)
            eligible_records += recs
    # copies overlap (each is a superset of the last); events are deduplicated by id, t_e is the earliest run_utc carrying the id
    ordered = sorted(eligible_records, key=lambda t: t[1]["run_utc"])  # stable: ties keep file order
    events, _ = extract_events(ordered, programs, accounts)
    w1 = t0 + timedelta(days=n_days)
    e_list = []
    for e in events.values():
        if e["sev"] != "HIGH" or e["relevance"] != "direct":
            continue
        te = parse_ts(e["first_run_utc"])  # run_utc of the first record that carried the id
        if te is None or not (t0 <= te < w1):
            continue
        e_list.append({"id": e["id"], "t_e": e["first_run_utc"], "at_utc": e["change_utc"], "change_basis": e["change_basis"], "kind": e["kind"], "program": e["program"]})
    e_list.sort(key=lambda x: (x["t_e"], x["id"]))
    days = []
    for i in range(n_days):
        d0 = t0 + timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        if any(d0 < parse_ts(x["t_e"]) + timedelta(hours=REGIME_HOURS) and d1 > parse_ts(x["t_e"]) for x in e_list):
            days.append(fmt_ts(d0)[:10])
    return {
        "schema": DAYS_SCHEMA, "t0": fmt_ts(t0), "n_days": n_days, "regime_hours": REGIME_HOURS,
        "inputs": [{"as_of_ms": ms, "radar_status": b["radar_status"], "input_sha256": b["input"]["sha256"]} for ms, b, _ in saved],
        "E": e_list, "regime_days": days,
        "degraded_days_report_only": degraded_days([(ms, b) for ms, b, _ in saved], t0, n_days),
    }


def run_regime_days(a: argparse.Namespace) -> int:
    t0 = parse_ts(a.t0)
    if t0 is None:
        raise SystemExit("refuse: --t0 must be YYYY-MM-DDTHH:MM:SSZ")
    programs, accounts = load_watch(Path(a.watch).read_bytes())
    doc = regime_days(Path(a.copy_dir), t0, int(a.days), programs, accounts, json.loads(Path(a.pins).read_text()))
    data = dumps(doc).encode()
    out = Path(a.out)
    try:
        create_exclusive_ro(out, data)  # truly write-once: no overwrite, not even identical bytes
    except FileExistsError:
        raise SystemExit(f"refuse: {out} already exists (the regime-day list is written once)")
    print(sha256_hex(data))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("block")
    b.add_argument("--as-of-ms", required=True)
    b.add_argument("--out-dir", required=True, help="the radar job's out-dir (must equal the pinned one)")
    b.add_argument("--venue-tape", default=PINNED_VENUE_TAPE)
    b.add_argument("--copy-dir", required=True)
    b.add_argument("--pins", required=True, help="JSON with every key in PIN_KEYS (paths, shas, out_dir, venue_tape)")
    b.add_argument("--radar-code", required=True, help="path the radar job runs; never defaulted to this checkout")
    b.add_argument("--seals", required=True)
    b.add_argument("--watch", required=True)
    b.set_defaults(fn=run_block)
    r = sub.add_parser("regime-days")
    r.add_argument("--copy-dir", required=True)
    r.add_argument("--t0", required=True)
    r.add_argument("--days", required=True)
    r.add_argument("--watch", required=True)
    r.add_argument("--pins", required=True)
    r.add_argument("--out", required=True)
    r.set_defaults(fn=run_regime_days)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
