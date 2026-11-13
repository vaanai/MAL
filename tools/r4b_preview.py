"""r4b_preview: the r4-b PREVIEW PRICER (DEC-031 Amendment 31 items 1 and 2, and Erratum 1 of QP-RULING-1113-open-sims Addendum 1: B5, B1, B3, B4). PAPER ONLY.
No key, no network, no RPC, no wallet.

A daily OPEN preview of r4bt-a2's own decisions priced at four stakes (0.10, 0.25, 0.5, 1.0 SOL) on the registered ArenaEngine stack. It is NOT the registered
pricer, not a registered read and not gate evidence. It makes no statement that any book makes money.

WHERE IT RUNS AND WHAT IT TOUCHES (Addendum 1, B5). One manager MiScusi job on mal-research-0. It READS ONLY
  * its manifest (JSON, pinned by sha256);
  * the declog byte copy `DECLOG_COPY_ROOT/<change_id>/decisions/r4b-exp027/<UTC day>.jsonl` (made by a separate copier job, cut at the last newline);
  * the price keeper `TAPE_DIR` (hard links of closed tape hours, made by a separate keeper job).
It WRITES finished outputs ONLY under `OUT_ROOT`; its temporary engine ledger (pool ids) lives ONLY under `WORK_ROOT` (0700, swept at the start of every run, removed at exit). The three roots are constants, overridable only through the test-only `roots=` argument; a read or write outside them is refused, and an
OUT_ROOT inside /data/mal/arena, a keeper dir, the declog copy, the dry host's root or /var/lib is refused. It re-verifies the hash chain of the copied prefixes on every
run (exit 3 on a break). It never calls flock (the copier may be mid-write): a torn last line is cut, and a file that is inconsistent only at its very tail is read up to
the last consistent complete line.

WHAT IT PRICES. Only entries at marks at or after `CUTOFF_S` (2026-11-16T01:00:00Z). The tape hours of `TAPE_FLOOR` (2026-11-15T22:00:00Z) through the cut-off are fed
to the engine for POOL STATE ONLY (B1): the cut-off filter runs before the replay, no entry or exit before it is priced, and no count, gap, refusal or other figure from
those hours is output. No tape hour before TAPE_FLOOR is ever opened.

THE OPEN OUTPUT carries no pool, mint, signature, price, reserve, V, score or slot (Addendum 1, pool ids). Per trade: series, trade key, entry mark, entry decision
instant, rank, exit mark, exit reason, hold hours, P0 flag, SOL per rung under each cell, fill state, state_src, exclusion reason.

E1 (B4). The headline is PROPORTIONAL (the engine's `e1_floor_per_005` 384,022 x stake / 0.05, the unmodified engine output; it equals the registered stack and equals
1,920,110 lamports at 0.25 SOL). FIXED 1,920,110 lamports per filled trip is printed beside it in every cost-drag and total table. Neither is dropped.

    python -I tools/r4b_preview.py run --manifest M --manifest-sha256 H --as-of 2026-11-17T00:00:00Z [--verify]

Exit codes: 0 written (or byte-equal to what is there; or --verify EQUAL); 4 --verify DIFF; 2 refused (nothing written); 3 the inputs cannot be trusted.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from tools import arena_paper as AP
from tools import arena_score as S
from tools import r4bt_bots as BOTS_T
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools import r4bt_read as RD
from tools.arena_declog import GENESIS, sha256_hex
from tools.arena_paper import ArenaEngine, ArenaRefused, LedgerCorrupt, canon, utc_day

Untrusted = P.Untrusted

# ------------------------------------------------------------------------------------------------------------------------------ constants
LABEL = ("FORWARD PAPER PREVIEW of r4bt-a2's decisions (DEC-031 Amendment 31; declared {declared}). Paper, not money; priced by the preview pricer, not the registered "
         "pricer. Not a registered read and not gate evidence. The 0.25 SOL rung previews r4bt-a2's deciding cell; the 0.10, 0.5 and 1.0 SOL rungs are the same decisions "
         "priced at other stakes and test nothing. Prior from in-sample evidence: P(CI90 lower bound > 0) = 0.165.")
P0_LABEL = "P0 day (pre-clock; not a counted attempt)"
E1_LABEL = ("E1 is a live-minus-sim gap estimate scaled from 0.05 SOL trips; its growth beyond proportional at 0.5 and 1.0 SOL is unmeasured.")
STAKES = (0.10, 0.25, 0.5, 1.0)
K = 10
CUTOFF_S = 1794790800  # 2026-11-16T01:00:00Z
CUTOFF_MS = CUTOFF_S * 1000
TAPE_FLOOR = 1794780000  # 2026-11-15T22:00:00Z: pre-roll hours (22, 23, 00) feed engine state only
TAPE_FLOOR_MS = TAPE_FLOOR * 1000
E1_FIXED = 1_920_110
ARM = "r4b-exp027"
PREVIEW_BOT = "r4b-preview"
DECLOG_COPY_ROOT = "/data2/mal/r4b-preview/declog-copy"
TAPE_DIR = "/data2/mal/scratch/r4b-price-keep"
OUT_ROOT = "/data2/mal/r4b-preview/out"
WORK_ROOT = "/data2/mal/r4b-preview/work"  # the temporary engine ledger and registry (pool ids): sealed, 0700, never under OUT_ROOT
KEEPER_HOURS = ".keeper-hours.jsonl"
REGISTERED_BLOBS = {  # git blob ids of the registered modules at W' 3c12c5fddfa67384dca2e306a8bf6c529e85ebfc: the preview refuses to run on any other bytes
    "tools/r4bt_price.py": "7ed62b1f43ed58da862a56a5bd28a6167136cb39",
    "tools/arena_paper.py": "5eef156bb617138a4c04fca6779f9ff4c5215b13",
    "tools/arena_score.py": "f3adcd69c7ef62392eafc511b40d93a0dc31f13c",
    "tools/r4bt_read.py": "679de3b5622dd7e2bf1deed8cccae079a188f241",
    "tools/r4bt_gate.py": "2c094ad860c1b838399e4274ca7db3ae837f6d9e",
    "tools/arena_declog.py": "2baa782db6b93fe1dc225009176b889785a20763",
    "tools/r4bt_bots.py": "f109187aa8ae18d1cbafb06b6e1b7a2c2174c747",
}
REPO_ROOT = Path(__file__).resolve().parent.parent
HOUR_MS = 3_600_000
LATS = ("1p3", "1p9")
LAT_F = {"1p3": 1.3, "1p9": 1.9}
RULES = ("total", "sc")
MODELS = ("flat", "press")
E1_MODES = ("proportional", "fixed")
CHANGE_ID_RES = (re.compile(r"^r4bt-a2-dry-1113a$"), re.compile(r"^r4bt-a2-\d{4}[a-z]$"), re.compile(r"^r4bt-a3(-dry)?-\d{4}[a-z]$"))
REFUSED_CHANGE_IDS = (re.compile(r"^dry-long-1101a$"), re.compile(r"^r4bt-a1-"))
DECLOG_DENY = ("out", "host.log", "m1-start.txt", "sidecar.jsonl")
NEVER_WRITE = ("/data/mal/arena", "/data2/mal/scratch/r4b-price-keep", "/data2/mal/arena-tip-feed", "/data2/mal/r4b-preview/declog-copy", "/home/claude/mal-data/r4b-dry",
               "/home/claude/mal-data/arena-tipfeed", "/var/lib")
NEVER_WRITE_WORK = NEVER_WRITE + ("/data2/mal/r4b-preview/out",)
SHA_RE = G.SHA_RE
HEX40 = re.compile(r"^[0-9a-f]{40}$")
DAY_FILE_RE = re.compile(r"^(\d{4}-\d\d-\d\d)\.jsonl$")
REASON_RE = re.compile(r"^[a-z0-9_]+$")
OPEN_FIELD_DENY = ("pool", "mint", "signature", "price", "reserve", "v", "score", "slot")  # keys never present in the open output (tests walk the JSON)

DISCLOSURES = (
    "this preview does not apply Amendment 6's hour-health exclusions, the 5% and 10% caps or an A3-stop exclusion (it has no registry stop line)",
    "d is the manifest's single measured value (QP-RULING-1113-open-sims Addendum 1, B3); a different d would be a new series with a new start date",
    "tape hours 2026-11-15T22, 2026-11-15T23 and 2026-11-16T00 feed the engine for pool state only; nothing from them is output (Addendum 1, B1)",
    "the trades listed with a catch-up replay series label are outside every total and statistic",
    "entry and exit decisions are read from the declog COPY (the copier's per-file sha256s are the cross-check), not from the original declogs",
    "the preview runs under /data/mal/venv on mal-research-0; the registered pricer's venv is pinned on mal-fast-0, so float equality of the 0.25 rung with the registered read rests on the equality test, not on an identical venv",
    "the 0.25 rung's deciding_like vector charges rent on V-audit trades as r4bt_read.deciding_cells does; the same rule is applied at every rung",
    "positions with no exit row or still open at as_of are listed and counted and are never priced",
    "the pressure-model intercept is refit per cell on that cell's trades; no confidence interval and no threshold test is computed",
)


# ------------------------------------------------------------------------------------------------------------------------------ roots and guards
@dataclasses.dataclass(frozen=True)
class Env:
    declog_root: Path
    tape_dir: Path
    out_root: Path
    work_root: Path

    def _under(self, path: str | Path, root: Path, what: str) -> Path:
        p = Path(path).resolve()
        if p != root and root not in p.parents:
            raise G.Refused(f"{what} {p} is outside {root}: refused")
        return p

    def check_read_declog(self, path: str | Path) -> Path:
        p = self._under(path, self.declog_root, "a declog read")
        rel = p.relative_to(self.declog_root).parts
        if any(x in DECLOG_DENY for x in rel):
            raise G.Refused(f"declog path component {rel} is denied (out/, host log, M1 file and sidecar are never opened)")
        return p

    def check_read_tape(self, path: str | Path) -> Path:
        p = self._under(path, self.tape_dir, "a tape read")
        m = P.TAPE_RE.match(p.name)
        if not m:
            raise G.Refused(f"{p.name} is not a tape hour file: refused")
        hs = G.utc_ms(m.group(1) + ":00:00Z")
        if hs < TAPE_FLOOR_MS:
            raise G.Refused(f"tape hour {m.group(1)} is before TAPE_FLOOR {G.iso(TAPE_FLOOR_MS)}: refused, never opened")
        return p

    def check_write(self, path: str | Path) -> Path:
        return self._under(path, self.out_root, "a write")

    def check_work(self, path: str | Path) -> Path:
        return self._under(path, self.work_root, "a work write")


def make_env(roots: tuple[str | Path, str | Path, str | Path, str | Path] | None = None) -> Env:
    d, t, o, wk = roots if roots is not None else (DECLOG_COPY_ROOT, TAPE_DIR, OUT_ROOT, WORK_ROOT)
    for part in Path(d).parts:
        if part in DECLOG_DENY:
            raise G.Refused(f"the declog root {d} has the component {part!r}: refused")
    dr, tr, orr = G.check_path(d, "the declog copy root"), G.check_path(t, "the tape dir"), G.check_path(o, "OUT_ROOT")
    wr = G.check_path(wk, "WORK_ROOT")

    def inside(a: Path, b: Path) -> bool:
        return a == b or b in a.parents

    for bad in NEVER_WRITE:
        b = Path(bad)
        if inside(orr, b):
            raise G.Refused(f"OUT_ROOT {orr} is inside {bad}: refused")
    for bad in NEVER_WRITE_WORK:
        if inside(wr, Path(bad)):
            raise G.Refused(f"WORK_ROOT {wr} is inside {bad}: refused")
    trees = (dr, tr, orr, wr)
    for i, a in enumerate(trees):
        for b in trees[i + 1:]:
            if inside(a, b) or inside(b, a):
                raise G.Refused("OUT_ROOT, WORK_ROOT, the declog copy and the keeper dir must be four separate trees")
    return Env(dr, tr, orr, wr)


# ------------------------------------------------------------------------------------------------------------------------------ small helpers
def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def iso_ms(ms: int) -> str:
    s, m = divmod(int(ms), 1000)
    return G.iso(s * 1000)[:-1] + f".{m:03d}Z"


def check_change_id(cid: Any) -> str:
    if not isinstance(cid, str) or any(r.search(cid) for r in REFUSED_CHANGE_IDS) or not any(r.match(cid) for r in CHANGE_ID_RES):
        raise G.Refused(f"change_id {cid!r} is not on the allowlist (r4bt-a2-dry-1113a, r4bt-a2-MMDDx, r4bt-a3[-dry]-MMDDx)")
    return cid


def label_for(declared: str) -> str:
    return LABEL.format(declared=declared)


# ------------------------------------------------------------------------------------------------------------------------------ manifest
def load_manifest(path: str | Path, sha256: str) -> dict:
    p = Path(path)
    if not SHA_RE.match(sha256 or ""):
        raise G.Refused("--manifest-sha256 is not a lowercase sha256")
    raw = p.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise G.Refused("the manifest sha256 differs from the pinned one")
    try:
        m = json.loads(raw)
    except ValueError:
        raise G.Refused("the manifest is not JSON") from None
    if not isinstance(m, dict):
        raise G.Refused("the manifest is not an object")
    if not isinstance(m.get("pin_commit"), str) or not HEX40.match(m["pin_commit"]):
        raise G.Refused("manifest pin_commit must be 40 hex")
    declared = m.get("declared_instant")
    if not isinstance(declared, str):
        raise G.Refused("manifest declared_instant is required")
    G.utc_ms(declared)
    if not _is_int(m.get("d")) or m["d"] < 0:
        raise G.Refused("manifest d must be a whole number of slots >= 0 (measured once, B3; no default)")
    if not isinstance(m.get("d_measurement_sha256"), str) or not SHA_RE.match(m["d_measurement_sha256"]) or not isinstance(m.get("d_window"), str) or not m["d_window"]:
        raise G.Refused("manifest d_measurement_sha256 (sha256) and d_window (text) are required")
    series = m.get("series")
    if not isinstance(series, list) or not series:
        raise G.Refused("manifest series must be a non-empty list")
    roles = Counter()
    seen = set()
    for s in series:
        if not isinstance(s, dict) or s.get("role") not in ("dry", "decision"):
            raise G.Refused("every series needs a role of dry or decision")
        check_change_id(s.get("change_id"))
        if s["change_id"] in seen:
            raise G.Refused("a change_id appears twice in the manifest")
        seen.add(s["change_id"])
        roles[s["role"]] += 1
        if not isinstance(s.get("frozen_sha"), str) or not HEX40.match(s["frozen_sha"]):
            raise G.Refused("every series needs a 40-hex frozen_sha (checked against the declog start rows)")
        if s["role"] == "dry" and s.get("dry_stop_ms") is not None and not _is_int(s["dry_stop_ms"]):
            raise G.Refused("dry_stop_ms must be an int or null")
        if s["role"] == "decision" and not _is_int(s.get("first_live_mark_s")):
            raise G.Refused("a decision series needs an int first_live_mark_s (its first post-catch-up mark)")
    if roles["dry"] > 1 or roles["decision"] > 1 or not roles:
        raise G.Refused("at most one dry and one decision series")
    p0 = m.get("p0_days")
    if not isinstance(p0, list) or not all(isinstance(x, str) and re.match(r"^\d{4}-\d\d-\d\d$", x) for x in p0):
        raise G.Refused("manifest p0_days must be a list of YYYY-MM-DD")
    if any(s["change_id"].startswith("r4bt-a2") for s in series) and tuple(p0) != tuple(BOTS_T.BOTS["r4bt-a2"]["p0_days"]):
        raise G.Refused("manifest p0_days differ from r4bt-a2's named P0 days")
    return m


# ------------------------------------------------------------------------------------------------------------------------------ declog copy
def _read_bytes(path: Path) -> bytes:
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)  # no lock of any kind: the copier may be mid-write
    try:
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_chain_prefix(env: Env, change_id: str, until_ms: int) -> tuple[list[dict], dict[str, str]]:
    """The rows of `<declog copy>/<change_id>/decisions/r4b-exp027/*.jsonl` with ms < until_ms, the hash chain verified over every row read (from genesis; pre-cut-off rows
    take part in the chain and nothing else). Lock-free, O_RDONLY. Each file is cut at its last newline (a torn tail is ignored). Any complete line that fails the chain is Untrusted (the copier cuts at a newline and verifies before installing). Returns (rows, {day: sha256 of the consumed prefix bytes});
    a whole-file sha is never returned (the open day grows)."""
    check_change_id(change_id)
    d = env.check_read_declog(env.declog_root / change_id / "decisions" / ARM)
    if not d.is_dir():
        raise G.Refused(f"the declog copy of {change_id} has no decisions/{ARM} directory")
    files = sorted(f for f in d.iterdir() if DAY_FILE_RE.match(f.name))
    prev, seq = GENESIS, -1
    rows: list[dict] = []
    shas: dict[str, str] = {}
    stop = False
    for fi, f in enumerate(files):
        if stop:
            break
        env.check_read_declog(f)
        data = _read_bytes(f)
        data = data[: data.rfind(b"\n") + 1]
        lines = data.split(b"\n")[:-1]
        consumed = 0
        for i, line in enumerate(lines):
            if not line.strip():
                consumed += len(line) + 1
                continue
            ok = False
            row = None
            try:
                row = json.loads(line)
                sha = row.pop("sha")
                ok = (isinstance(row, dict) and row.get("prev") == prev and row.get("seq") == seq + 1 and row.get("arm") == ARM and _is_int(row.get("ms"))
                      and sha256_hex(prev + canon(row)) == sha)
            except (ValueError, KeyError, AttributeError, TypeError):
                ok = False
            if not ok:  # a complete (newline-terminated) line that fails the chain is a real break, at the tail or anywhere
                raise Untrusted(f"declog copy chain of {change_id} is broken in {f.name}")
            if row["ms"] >= until_ms:
                stop = True
                break
            rows.append(row)
            prev, seq = sha, row["seq"]
            consumed += len(line) + 1
        if consumed:
            shas[f.stem] = hashlib.sha256(data[:consumed]).hexdigest()
    return rows, shas


def check_start_rows(rows: Sequence[Mapping], frozen_sha: str) -> None:
    starts = [r for r in rows if r.get("kind") == "start"]
    if not starts:
        raise G.Refused("a declog copy has no `start` row: its code cannot be tied to the manifest's frozen_sha")
    if any(r.get("frozen_sha") != frozen_sha for r in starts):
        raise G.Refused("a declog `start` row carries a frozen_sha other than the manifest's: the host ran other code")


# ------------------------------------------------------------------------------------------------------------------------------ select and merge
def select(rows: Sequence[Mapping], change_id: str, as_of_ms: int) -> tuple[list[dict], list[dict]]:
    """Entries at marks at or after CUTOFF_S with ms < as_of (order_id suffix == t_mark, a string pool), and the exits of exactly those order ids. Rows before the cut-off
    are dropped WITHOUT being counted: nothing about them is returned."""
    entries: list[dict] = []
    for r in rows:
        if r.get("kind") != "entry":
            continue
        tm = r.get("t_mark")
        if not _is_int(tm):
            raise Untrusted("an entry row without an int t_mark")
        if tm < CUTOFF_S:
            continue
        if not _is_int(r.get("ms")) or r["ms"] >= as_of_ms:
            continue
        oid = r.get("order_id")
        if not isinstance(oid, str) or oid.rsplit(":", 1)[-1] != str(tm):
            raise Untrusted("an entry order_id suffix differs from its t_mark")
        if not isinstance(r.get("pool"), str) or not r["pool"]:
            raise Untrusted("an entry row without a string pool (no fallback to meta.mint)")
        e = dict(r)
        e["_cid"] = change_id
        entries.append(e)
    orders = {e["order_id"] for e in entries}
    exits = []
    for r in rows:
        if r.get("kind") == "exit" and r.get("order_id") in orders and _is_int(r.get("ms")) and r["ms"] < as_of_ms:
            x = dict(r)
            x["_cid"] = change_id
            exits.append(x)
    return entries, exits


def merge_series(entries_by_cid: Mapping[str, list[dict]], manifest: Mapping) -> list[dict]:
    """The once-per-mark rule (Amendment 31 item 2). A dry-host mark counts if T x 1000 < dry_stop_ms; a decision-host mark counts if T >= its first post-catch-up mark;
    a decision-host mark before that is the `catch-up replay` series, never in a total. The same counted mark in both series is Untrusted (exit 3). Each returned entry
    carries `_label` and `_counted`."""
    out: list[dict] = []
    counted_marks: dict[str, set] = {"dry": set(), "decision": set()}
    for s in manifest["series"]:
        for e in entries_by_cid.get(s["change_id"], []):
            e = dict(e)
            T = e["t_mark"]
            if s["role"] == "dry":
                stop = s.get("dry_stop_ms")
                ok = stop is None or T * 1000 < stop
                e["_label"], e["_counted"] = ("dry", ok) if ok else ("dry after stop", False)
            else:
                ok = T >= s["first_live_mark_s"]
                e["_label"], e["_counted"] = ("decision", ok) if ok else ("catch-up replay", False)
            e["_role"] = s["role"]
            if e["_counted"]:
                counted_marks[s["role"]].add(T)
            out.append(e)
    if counted_marks["dry"] & counted_marks["decision"]:
        raise Untrusted("the same counted mark appears in both the dry and the decision series")
    seen: set = set()
    for e in out:
        k = (e["pool"], e["ms"])
        if k in seen:
            raise Untrusted("two entries share a pool and a decision instant across series")
        seen.add(k)
    return out


# ------------------------------------------------------------------------------------------------------------------------------ state, E1, decomposition
def reconstruct_state(calc: Mapping[str, Any]) -> dict:
    """The `st` of arena_paper.price_leg rebuilt from a ledger row's `calc` (landing states per latency, the guard spot, the exit state). Reproduces every stored pnl_nofail."""
    c3, c9 = calc["1p3"], calc["1p9"]
    return {"ref": c9["spot"], "Qe": {1.3: c3["Qe"], 1.9: c9["Qe"]}, "Be": {1.3: c3["Be"], 1.9: c9["Be"]}, "ssb": {1.3: c3["ssb"], 1.9: c9["ssb"]},
            "nearby": {1.3: c3["nearby_lamports"], 1.9: c9["nearby_lamports"]}, "Qx": c9["Qx"], "Bx": c9["Bx"]}


def e1_fixed_leg(prog: str, stake_lamports: float, st: Mapping[str, Any], lat: float, cfg: AP.StackConfig, rule: str) -> dict:
    """price_leg's arithmetic (arena_paper.py price_leg) with the E1 floor FIXED at E1_FIXED lamports per filled trip instead of e1_floor_per_005 x stake / 0.05."""
    pnl, state, filled = AP._trip(prog, stake_lamports, st, lat, cfg, cfg.h_in, cfg.h_out, rule)
    extra = 0.0
    haircut = 0.0
    if state == "filled":
        pnl0, _, _ = AP._trip(prog, stake_lamports, st, lat, cfg, 0.0, 0.0, rule)
        haircut = pnl0 - pnl
        extra = max(0.0, E1_FIXED - haircut)
        pnl -= extra
    strand = rule == "total" and state in AP.STRAND_STATES
    return {"pnl": pnl, "state": state, "filled": bool(filled), "strand": bool(strand), "e1_extra": extra, "haircut": haircut}


def decompose_trip(prog: str, stake: float, st: Mapping[str, Any], lat: float, cfg: AP.StackConfig, rule: str) -> dict | None:
    """Telescoping cost steps of one FILLED trip, lamports: s0 frictionless (the 1e6-lamport trip scaled to the stake), s1 impact at the stake, s2 + haircut, s3 + sends
    (the engine's pnl before E1), s4 = s3 - E1 proportional (the engine's pnl_nofail), s4f = s3 - E1 fixed. None if the 1e6-lamport trip does not fill."""
    zero = dataclasses.replace(cfg, send=0, rent=0, e1_floor_per_005=0)
    tiny, tstate, _ = AP._trip(prog, 1e6, st, lat, zero, 0.0, 0.0, rule)
    if tstate != "filled":
        return None
    s0 = tiny * stake / 1e6
    s1, _, _ = AP._trip(prog, stake, st, lat, zero, 0.0, 0.0, rule)
    s2, _, _ = AP._trip(prog, stake, st, lat, zero, cfg.h_in, cfg.h_out, rule)
    s3, _, _ = AP._trip(prog, stake, st, lat, cfg, cfg.h_in, cfg.h_out, rule)
    pnl0, _, _ = AP._trip(prog, stake, st, lat, cfg, 0.0, 0.0, rule)
    haircut = pnl0 - s3
    e1p = max(0.0, cfg.e1_floor_per_005 * stake / 50_000_000.0 - haircut)
    e1f = max(0.0, E1_FIXED - haircut)
    return {"impact": s0 - s1, "haircut": s1 - s2, "sends": s2 - s3, "e1_proportional": e1p, "e1_fixed": e1f, "total_proportional": s0 - (s3 - e1p), "total_fixed": s0 - (s3 - e1f)}


# ------------------------------------------------------------------------------------------------------------------------------ replay
def replay(entries: Sequence[Mapping], exits: Sequence[Mapping], files: Sequence[Path], d: int, work_dir: Path, *, pin_commit: str,
           code_state_fn: Callable[[], tuple[str | None, bool | None]], env: Env | None = None, keep_bonding: bool = False) -> dict:
    """A copy of r4bt_price.price's replay loop (lines 338-416) on the registered ArenaEngine, with cfg = pinned_config(d, ceil) with only stakes_sol replaced, a private
    registry line (clock CUTOFF) and a ledger in `work_dir`. `entries` are the positions to open (each with `_cid`); exits are matched by (change_id, order_id)."""
    cfg = dataclasses.replace(P.pinned_config(feed_delay_slots=d, k_rounding="ceil"), stakes_sol=STAKES)
    work_dir.mkdir(parents=True, exist_ok=True)
    reg = work_dir / "bots.json"
    reg.write_text(json.dumps({"kind": "register", "bot_id": PREVIEW_BOT, "clock_start_ms": CUTOFF_MS, "frozen_sha": pin_commit, "verified_sha": None,
                               "registered_at_ms": CUTOFF_MS, "deciding_stake": 0.25, "model_ids": [], "time_source": "preview_private"}, sort_keys=True) + "\n")
    events = sorted([{"_ms": r["ms"], "_seq": r.get("seq", 0), "k": "entry", "row": r} for r in entries]
                    + [{"_ms": r["ms"], "_seq": r.get("seq", 0), "k": "exit", "row": r} for r in exits], key=lambda x: (x["_ms"], x["_seq"], x["row"]["_cid"]))
    pools = {r["pool"] for r in entries}
    prints: dict[str, list] = {p: [] for p in pools}
    clock = [G.utc_ms(P.TAPE_RE.match(files[0].name).group(1) + ":00:00Z") if files else CUTOFF_MS - 3 * HOUR_MS]  # the first tape hour's start, as r4bt_price's lo_ms
    eng = ArenaEngine(ledger_root=str(work_dir / "ledger"), registry_path=str(reg), cfg=cfg, sps_fn=None, now_fn=lambda: clock[0], code_state_fn=code_state_fn)
    eng.start_record(PREVIEW_BOT)
    by_order: dict[tuple, str] = {}
    pid_of_entry: dict[tuple, str] = {}
    c: Counter = Counter()
    refused: dict[tuple, str] = {}

    def apply(ev: Mapping[str, Any]) -> None:
        r = ev["row"]
        clock[0] = ev["_ms"]
        key = (r["_cid"], r["order_id"])
        if ev["k"] == "entry":
            meta = {"order_id": r["order_id"], "t_mark": r.get("t_mark"), "rank": r.get("rank"), "declog_seq": r.get("seq"), "cid": r["_cid"]}
            try:
                by_order[key] = eng.buy(PREVIEW_BOT, r["pool"], r.get("stake_sol"), r["ms"], eng.hw_slot, meta)
                c["buys_open"] += 1
            except ArenaRefused as exc:
                why = str(exc).split(":")[0].strip()
                refused[key] = why if REASON_RE.match(why) else "other"
                c["buy_refused:" + refused[key]] += 1
            return
        pid = by_order.get(key)
        if pid is None or pid not in eng.positions:
            c["exit_without_open_position"] += 1
            return
        if r.get("reason") == "restart":
            pos = eng.positions[pid]
            pos.exit_ms, pos.exit_slot, pos.xs, pos.sps_exit, pos.reason = r["ms"], eng.hw_slot, eng.hw_slot, pos.sps_entry, "restart"
            pos.exit_stamp_ms, pos.exit_call_ms = r["ms"], r["ms"]
            eng._write_excluded(pos, "engine_restart", None)
            del eng.positions[pid]
            c["engine_restart_exclusions"] += 1
        else:
            try:
                eng.sell(PREVIEW_BOT, pid, 1.0, r["ms"], eng.hw_slot, str(r.get("reason") or ""))
                c["sells_decided"] += 1
            except ArenaRefused as exc:
                why = str(exc).split(":")[0][:40].strip()
                c["sell_refused:" + (why if REASON_RE.match(why) else "other")] += 1

    i = 0
    try:
        for f in files:
            if env is not None:
                env.check_read_tape(f)
            for row in P.iter_rows(f):
                t = row.get("t_recv_ms")
                while i < len(events) and _is_int(t) and events[i]["_ms"] < t:
                    apply(events[i])
                    i += 1
                if _is_int(t):
                    clock[0] = max(clock[0], t)
                if row.get("type", "trade") == "trade" and row.get("prog") == "pumpswap" and row.get("pool") in prints and _is_int(row.get("slot")):
                    prints[row["pool"]].append((int(row["slot"]), row.get("virtual_quote_reserves"), row.get("quote_mint")))
                eng.on_row(row)
            eng.trim_history()
            if not keep_bonding:
                busy = {p.pool for p in eng.positions.values()}
                for k in [k for k, h in eng.pools.items() if h.venue == "pump_bonding" and k not in busy]:
                    del eng.pools[k]
        while i < len(events):
            apply(events[i])
            i += 1
        eng.flush()
    except LedgerCorrupt as exc:
        raise Untrusted(f"ledger write failed: {exc}") from None
    led = eng.ledger.read_bot(PREVIEW_BOT)
    for p in prints.values():
        p.sort(key=lambda x: x[0])
    return {"rows": led, "prints": prints, "counts": dict(sorted(c.items())), "refused": refused, "by_order": by_order, "cfg": cfg}


def trade_digest(rows: Sequence[Mapping]) -> str:
    keep = [{k: v for k, v in r.items() if k not in ("engine_id", "written_ms", "pid")} for r in rows if r["kind"] in ("trade", "excluded", "late_print")]
    return AP.row_digest(keep)


# ------------------------------------------------------------------------------------------------------------------------------ cells
def _arr(trades: Sequence[Mapping], lat: str, rule: str, pnl: np.ndarray):
    fil = np.array([r["calc"][lat]["by_rule"][rule]["filled"] for r in trades], bool)
    strand = np.array([r["calc"][lat]["by_rule"][rule]["strand"] for r in trades], bool)
    ssb = np.array([r["calc"][lat]["ssb"] for r in trades], float)
    near = np.array([r["calc"][lat]["nearby_lamports"] for r in trades], float)
    sel = np.array([r["calc"][lat]["by_rule"][rule]["state"] == "filled" for r in trades], bool)
    return fil, strand, ssb, near, sel


def _stats(x: np.ndarray, days: Sequence[str], p0: Sequence[str]) -> dict:
    s = S.cell_stats(x, days)
    s.pop("ci90_lo_sol", None)
    s["daily"] = {k: dict(v, p0=k in p0) for k, v in s["daily"].items()}
    return s


def cells_of(trades: Sequence[Mapping], cfg: AP.StackConfig, p0: Sequence[str], audit: frozenset = frozenset()) -> tuple[dict, dict]:
    """Per stake (one trade list): cells[e1_mode][f'{model}_{lat}_{rule}'] = {rent0, rent_charged} stats, plus per-trade SOL vectors at rent 0 (same keys). Pressure refit per
    cell. The rent-charged leg is r4bt_read.deciding_cells' transform (flat minus 0.85 x rent; pressure minus (1 - p) x rent; filled trips only)."""
    out: dict[str, dict] = {m: {} for m in E1_MODES}
    vec: dict[str, dict] = {m: {} for m in E1_MODES}
    if not trades:
        return out, vec
    days = [utc_day(r["decision_ms"]["entry"]) for r in trades]
    aud = np.array([r.get("position_id") in audit for r in trades], bool)  # V-audit trades DECIDE at the rent-charged leg (r4bt_read.deciding_cells, QP Q4)
    send = float(trades[0]["stack"]["send"])
    sts = [reconstruct_state(r["calc"]) for r in trades]
    for mode in E1_MODES:
        for lat in LATS:
            for rule in RULES:
                if mode == "proportional":
                    pnl = np.array([r["calc"][lat]["by_rule"][rule]["pnl_nofail"] for r in trades], float)
                else:
                    pnl = np.array([e1_fixed_leg(r["prog"], float(round(r["stake_sol"] * 1e9)), st, LAT_F[lat], cfg, rule)["pnl"] for r, st in zip(trades, sts)], float)
                fil, strand, ssb, near, sel = _arr(trades, lat, rule, pnl)
                flat, press, c, p = S._legs(rule, pnl, fil, strand, ssb, near, send)
                x1 = {"flat": flat - np.where(sel, RD.RENT_FLAT_SHARE * RD.RENT, 0.0), "press": press - np.where(sel, (1.0 - np.asarray(p, float)) * RD.RENT, 0.0)}
                x0 = {"flat": flat, "press": press}
                for model in MODELS:
                    key = f"{model}_{lat}_{rule}"
                    xd = np.where(aud, x1[model], x0[model])
                    cell = {"rent0": _stats(x0[model], days, p0), "rent_charged": _stats(x1[model], days, p0), "deciding_like": _stats(xd, days, p0)}
                    if model == "press":
                        cell["intercept"] = float(c)
                    out[mode][key] = cell
                    vec[mode][key] = (xd / 1e9).tolist()
    return out, vec


def decompose(trades: Sequence[Mapping], cfg: AP.StackConfig) -> dict:
    """Cost drag as % of stake per (lat, rule), mean over filled trips: impact, haircut, sends, E1 proportional and fixed, and the totals with each E1 column."""
    res: dict[str, dict] = {}
    for lat in LATS:
        for rule in RULES:
            acc: dict[str, list] = {k: [] for k in ("impact", "haircut", "sends", "e1_proportional", "e1_fixed", "total_proportional", "total_fixed")}
            skipped = 0
            for r in trades:
                if r["calc"][lat]["by_rule"][rule]["state"] != "filled":
                    continue
                stake = float(round(r["stake_sol"] * 1e9))
                dc = decompose_trip(r["prog"], stake, reconstruct_state(r["calc"]), LAT_F[lat], cfg, rule)
                if dc is None:
                    skipped += 1
                    continue
                for k, v in dc.items():
                    acc[k].append(v / stake * 100.0)
            n = len(acc["impact"])
            res[f"{lat}_{rule}"] = {"n_filled_decomposed": n, "n_filled_skipped_no_tiny_fill": skipped,
                                    "pct_of_stake_mean": {k: (float(np.mean(v)) if v else None) for k, v in acc.items()}}
    return res


# ------------------------------------------------------------------------------------------------------------------------------ report
def peak_open(intervals: Sequence[tuple[int, int]]) -> int:
    ev = sorted([(a, 1) for a, b in intervals] + [(b, -1) for a, b in intervals], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, dlt in ev:
        cur += dlt
        best = max(best, cur)
    return best


def build_report(*, manifest: Mapping, as_of_ms: int, cfg: AP.StackConfig, entries: Sequence[Mapping], exits: Sequence[Mapping], res: Mapping, prefix_shas: Mapping,
                 priced_entries: Sequence[Mapping], pending: Mapping[str, list]) -> dict:
    """Everything the open output says. `entries` are all merged entries (priced and not), `pending` = {'no_exit_row': [...], 'open_at_as_of': [...]}."""
    declared = manifest["declared_instant"]
    p0 = list(manifest["p0_days"])
    led = res["rows"]
    by_pid: dict[str, list] = {}
    for r in led:
        if r["kind"] in ("trade", "excluded", "late_print"):
            by_pid.setdefault(r["position_id"], []).append(r)
    label_of = {(e["_cid"], e["order_id"]): e for e in entries}
    exit_of = {(x["_cid"], x["order_id"]): x for x in exits}
    ent_pid = {k: pid for k, pid in res["by_order"].items()}
    counted_pids = {ent_pid[k] for k, e in label_of.items() if e["_counted"] and k in ent_pid}
    counted_rows = [r for r in led if r.get("position_id") in counted_pids]
    # ---- per-stake trades that go in totals
    dec_by_stake: dict[float, list] = {}
    partition: dict[str, dict] = {}
    for s in STAKES:
        dec = RD.deciding_trades(counted_rows, CUTOFF_MS, 1 << 62, s)
        dec_by_stake[s] = dec
        allt = [r for r in counted_rows if r["kind"] == "trade" and r["stake_sol"] == s]
        partition[f"{s:g}"] = {"trade_rows": len(allt), "in_cells": len(dec), "removed_late_print_gap_or_v_none": len(allt) - len(dec)}
    cells: dict[str, Any] = {}
    vecs: dict[float, dict] = {}
    costs: dict[str, Any] = {}
    for s in STAKES:
        aud_s = frozenset(P.v_audit([r for r in dec_by_stake[s]], res["prints"])) if dec_by_stake[s] else frozenset()
        cells[f"{s:g}"], vecs[s] = cells_of(dec_by_stake[s], cfg, p0, aud_s)
        costs[f"{s:g}"] = decompose(dec_by_stake[s], cfg)
    # ---- counts
    excl = Counter(r.get("exclusion_reason") for r in counted_rows if r["kind"] == "excluded" and r["stake_sol"] == 0.25)
    refused = Counter(res["refused"][k] for k, e in label_of.items() if e["_counted"] and k in res["refused"])
    audit = P.v_audit([r for r in counted_rows if r["kind"] == "trade" and r["stake_sol"] == 0.25], res["prints"])
    counts = {"entries_selected_counted": sum(1 for e in entries if e["_counted"]), "entries_outside_totals": sum(1 for e in entries if not e["_counted"]),
              "priced_positions_counted": len({r["position_id"] for r in counted_rows if r["kind"] == "trade"}),
              "excluded_positions_by_reason": dict(sorted(excl.items())), "refused_buys_by_reason": dict(sorted(refused.items())),
              "no_exit_row": len(pending["no_exit_row"]), "open_at_as_of": len(pending["open_at_as_of"]), "v_audit_n": len(audit),
              "cell_partition_by_stake": partition}
    # ---- peaks
    iv = []
    priced_pids = {r["position_id"] for r in counted_rows if r["kind"] == "trade"}
    for e in entries:
        if not e["_counted"] or ent_pid.get((e["_cid"], e["order_id"])) not in priced_pids:
            continue  # peak open counts priced positions only; refused buys are printed separately (counts)
        x = exit_of.get((e["_cid"], e["order_id"]))
        iv.append((e["ms"], x["ms"] if x else as_of_ms))
    obs = peak_open(iv)
    peak = {f"{s:g}": {"nominal_k_x_stake_sol": K * s, "observed_max_concurrent": obs, "observed_peak_sol": obs * s} for s in STAKES}
    # ---- every trade
    series_n: Counter = Counter()
    trades_out = []
    for e in sorted(entries, key=lambda x: (x["_cid"], x["ms"], x.get("seq", 0))):
        series_n[e["_cid"]] += 1
        key = (e["_cid"], e["order_id"])
        x = exit_of.get(key)
        day = utc_day(e["ms"])
        tr = {"key": f"{e['_cid']}-{series_n[e['_cid']]:05d}", "series": e["_cid"], "role": e["_label"], "counted": bool(e["_counted"]), "entry_mark_utc": G.iso(e["t_mark"] * 1000),
              "entry_decision_utc": iso_ms(e["ms"]), "rank": (e["rank"] if _is_int(e.get("rank")) else None), "exit_mark_utc": G.iso(x["t_mark"] * 1000) if x and _is_int(x.get("t_mark")) else None,
              "exit_reason": (x.get("reason") if x else None), "hold_hours": (round((x["t_mark"] - e["t_mark"]) / 3600.0, 4) if x and _is_int(x.get("t_mark")) else None),
              "p0": day in p0, "p0_label": P0_LABEL if day in p0 else None, "day": day, "status": None, "rungs": {}}
        if tr["exit_reason"] is not None and not REASON_RE.match(str(tr["exit_reason"])):
            tr["exit_reason"] = "other"
        pid = ent_pid.get(key)
        if key in res["refused"]:
            tr["status"] = "refused"
            tr["exclusion_reason"] = res["refused"][key]
        elif any(key == k for k in pending["no_exit_row"]):
            tr["status"] = "no_exit_row"
        elif any(key == k for k in pending["open_at_as_of"]):
            tr["status"] = "open_at_as_of"
        elif pid is not None:
            tr["status"] = "priced"
            for r in by_pid.get(pid, []):
                if r["kind"] == "late_print" or r["stake_sol"] not in STAKES:
                    continue
                rung = {"fill_state": None, "state_src": r.get("state_src"), "exclusion_reason": r.get("exclusion_reason")}
                if r["kind"] == "trade":
                    rung["fill_state"] = {lat: {rule: r["calc"][lat]["by_rule"][rule]["state"] for rule in RULES} for lat in LATS}
                tr["rungs"][f"{r['stake_sol']:g}"] = rung
        trades_out.append((tr, pid))
    # per-trade SOL under each cell (rent 0), from the cell vectors
    pos_in_cells: dict[float, dict[str, int]] = {s: {r["position_id"]: i for i, r in enumerate(dec_by_stake[s])} for s in STAKES}
    for tr, pid in trades_out:
        if pid is None or not tr["counted"] or tr["status"] != "priced":
            continue
        for s in STAKES:
            i = pos_in_cells[s].get(pid)
            rung = tr["rungs"].get(f"{s:g}")
            if rung is None:
                continue
            if i is None:
                if rung["fill_state"]:
                    rung["not_in_cells"] = "late_print_gap_or_v_none"
                continue
            rung["sol_deciding_like"] = {m: {k: round(v[i], 9) for k, v in vecs[s][m].items()} for m in E1_MODES}
    rep = {
        "tool": "r4b_preview", "label": label_for(declared), "e1_label": E1_LABEL, "declared_instant": declared, "as_of": G.iso(as_of_ms), "stakes_sol": list(STAKES), "k": K,
        "cutoff_utc": G.iso(CUTOFF_MS), "tape_floor_utc": G.iso(TAPE_FLOOR_MS), "d_slots": manifest["d"], "d_measurement_sha256": manifest["d_measurement_sha256"],
        "d_window": manifest["d_window"], "pin_commit": manifest["pin_commit"], "stack": cfg.as_dict(), "e1_fixed_lamports": E1_FIXED, "e1_headline": "proportional",
        "p0_days": p0, "p0_label": P0_LABEL, "series": [{"change_id": s["change_id"], "role": s["role"]} for s in manifest["series"]], "disclosures": list(DISCLOSURES),
        "counts": counts, "peak_open": peak, "cost_drag_pct_of_stake": costs, "cells": cells, "trades": [t for t, _ in trades_out],
        "trade_rows_md5": trade_digest(led), "inputs": {"declog_prefix_sha256": {k: dict(sorted(v.items())) for k, v in prefix_shas.items()}},
    }
    return rep


# ------------------------------------------------------------------------------------------------------------------------------ rendering
def _f(x: Any) -> str:
    return "None" if x is None else (f"{x:.9f}" if isinstance(x, float) else str(x))


def render(rep: Mapping) -> tuple[str, str, str]:
    """(preview.json, preview.md, console.txt). Every md and console line starts with the label; P0-day rows add the P0 label."""
    L = rep["label"]
    js = json.dumps(rep, sort_keys=True, indent=1) + "\n"
    md = [f"as of {rep['as_of']}; d = {rep['d_slots']} slots (measurement {rep['d_measurement_sha256']}, window {rep['d_window']}); pin {rep['pin_commit']}",
          f"stakes {rep['stakes_sol']}; K {rep['k']}; cut-off {rep['cutoff_utc']}; pool-state tape floor {rep['tape_floor_utc']}; trade rows md5 {rep['trade_rows_md5']}",
          rep["e1_label"], f"E1 headline: proportional (engine); fixed {rep['e1_fixed_lamports']} lamports per filled trip printed beside it in every table"]
    md += [f"disclosed: {x}" for x in rep["disclosures"]]
    md.append("counts " + json.dumps(rep["counts"], sort_keys=True))
    for s, v in rep["peak_open"].items():
        md.append(f"peak open at {s} SOL: nominal K x stake {v['nominal_k_x_stake_sol']} SOL; observed max concurrent {v['observed_max_concurrent']} = {v['observed_peak_sol']} SOL")
    for s, per in rep["cost_drag_pct_of_stake"].items():
        for k, v in per.items():
            md.append(f"cost drag at {s} SOL {k}: n {v['n_filled_decomposed']} " + " ".join(f"{a}={_f(b)}" for a, b in v["pct_of_stake_mean"].items()))
    for s, modes in rep["cells"].items():
        for mode, cs in modes.items():
            for k, c in sorted(cs.items()):
                r0, r1 = c["rent0"], c["rent_charged"]
                md.append(f"cell {s} SOL e1 {mode} {k}: rent0 n {r0['n']} mean {_f(r0['mean_sol'])} total {_f(r0['total_sol'])} days {r0['days']} days_pos {r0['days_pos']} "
                          f"ex_top3 {_f(r0['ex_top3_sol'])} ex_best_day {_f(r0['ex_best_day_sol'])}; rent-charged mean {_f(r1['mean_sol'])} total {_f(r1['total_sol'])}")
                for day, dv in sorted(r0["daily"].items()):
                    md.append(f"day {day} {s} SOL e1 {mode} {k}: n {dv['n']} sol {_f(dv['sol'])}" + (f" | {P0_LABEL}" if dv["p0"] else ""))
    for t in rep["trades"]:
        line = (f"trade {t['key']} series {t['series']} ({t['role']}; {'counted' if t['counted'] else 'outside totals'}) entry mark {t['entry_mark_utc']} decision {t['entry_decision_utc']} "
                f"rank {t['rank']} exit mark {t['exit_mark_utc']} reason {t['exit_reason']} hold_h {t['hold_hours']} status {t['status']}")
        for s, rg in sorted(t["rungs"].items()):
            sol = (rg.get("sol_deciding_like") or {}).get("proportional", {})
            line += f" | {s}: " + ",".join(f"{k}={sol[k]:.6f}" for k in sorted(sol) if k.endswith("_1p9_total")) + f" state_src {rg.get('state_src')} excl {rg.get('exclusion_reason')}"
        if t["p0"]:
            line += f" | {P0_LABEL}"
        md.append(line)
    md_text = "".join(f"{L} | {x}\n" for x in md)
    days: dict[str, dict] = {}
    for s, modes in rep["cells"].items():
        for mode in E1_MODES:
            for k in ("flat_1p9_total", "press_1p9_total"):
                c = modes[mode].get(k)
                if not c:
                    continue
                for day, dv in c["deciding_like"]["daily"].items():
                    days.setdefault(day, {}).setdefault(s, {})[(mode, k)] = (dv["n"], dv["sol"], dv["p0"])
    cnt = rep["counts"]
    tail = f" | no exit row {cnt['no_exit_row']}, open at as_of {cnt['open_at_as_of']}"
    cl = []
    for day in sorted(days):
        parts = []
        p0f = False
        for s in sorted(days[day], key=float):
            seg = []
            n = 0
            for mode in E1_MODES:
                f_, p_ = days[day][s].get((mode, "flat_1p9_total")), days[day][s].get((mode, "press_1p9_total"))
                n = f_[0]
                p0f = p0f or f_[2]
                seg.append(f"e1 {mode} flat {_f(f_[1])} press {_f(p_[1])}")
            parts.append(f"{s} SOL: n {n} " + ", ".join(seg))
        cl.append(f"{L} | day {day} (1.9 s, rule total, deciding-like leg; sol per day): " + "; ".join(parts) + (f" | {P0_LABEL}" if p0f else "") + tail)
    if not cl:
        cl.append(f"{L} | as of {rep['as_of']}: no priced day yet" + tail)
    return js, md_text, "\n".join(cl) + "\n"


# ------------------------------------------------------------------------------------------------------------------------------ output
def write_once(env: Env, path: Path, data: bytes) -> bool:
    """tmp -> fsync -> os.link; an existing file with the same bytes is a no-op (False), with other bytes a refusal. Writes only under OUT_ROOT."""
    p = env.check_write(path)
    if p.exists():
        if p.read_bytes() == data:
            return False
        raise G.Refused(f"{p.name} exists with other bytes: an output is never overwritten")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)
    try:
        os.link(tmp, p)
    except FileExistsError:
        raise G.Refused(f"{p.name} appeared while writing: refused") from None
    finally:
        tmp.unlink()
    return True


def first_output(env: Env, as_of: str, sha: str, pin: str) -> bool:
    p = env.check_write(env.out_root / "first-output.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, (json.dumps({"first_output_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "as_of": as_of, "preview_json_sha256": sha, "pin_commit": pin},
                                 sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


# ------------------------------------------------------------------------------------------------------------------------------ the run
def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def check_registered_modules(repo: Path = REPO_ROOT, table: Mapping[str, str] | None = None) -> None:
    """The imported registered modules must be byte-identical to W' (git blob ids); any other bytes refuse, naming the file."""
    for rel, want in (REGISTERED_BLOBS if table is None else table).items():
        got = git_blob_id((Path(repo) / rel).read_bytes())
        if got != want:
            raise G.Refused(f"{rel} is not the registered module (blob {got}, registered {want}): the 0.25 rung would no longer be r4bt-a2's deciding cell")


def load_keeper_rows(env: Env) -> tuple[dict, int]:
    """{hour name: row} from the keeper's append-only `.keeper-hours.jsonl` (the first valid row per hour); unreadable lines are skipped and counted."""
    p = env.tape_dir / KEEPER_HOURS
    rows: dict = {}
    bad = 0
    try:
        data = _read_bytes(p)
    except OSError:
        return rows, 0
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            ok = (isinstance(r, dict) and isinstance(r.get("h"), str) and isinstance(r.get("name"), str) and _is_int(r.get("zst_bytes")) and isinstance(r.get("zst_sha"), str)
                  and SHA_RE.match(r["zst_sha"]))
        except ValueError:
            ok = False
        if not ok:
            bad += 1
            continue
        rows.setdefault(r["name"], r)
    if bad:
        print(f"r4b_preview: {bad} unreadable line(s) skipped in {KEEPER_HOURS}", file=sys.stderr)
    return rows, bad


def check_keeper_hours(env: Env, files: Sequence[Path]) -> None:
    """Every hour fed (the pre-roll hours too) must be the `.jsonl.zst` the keeper recorded: size == zst_bytes and sha256 of its bytes == zst_sha, else Untrusted naming the
    hour only. The hash is streamed (1 MiB chunks) in a pass before any hour is fed."""
    rows, _ = load_keeper_rows(env)
    for f in files:
        env.check_read_tape(f)
        hour = P.TAPE_RE.match(f.name).group(1)
        r = rows.get(f.name)
        if not f.name.endswith(".jsonl.zst") or r is None:
            raise Untrusted(f"tape hour {hour}: no valid keeper row for a .jsonl.zst file of that name")
        h = hashlib.sha256()
        n = 0
        fd = os.open(str(f), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                h.update(b)
                n += len(b)
        finally:
            os.close(fd)
        if n != r["zst_bytes"] or h.hexdigest() != r["zst_sha"]:
            raise Untrusted(f"tape hour {hour}: size or sha256 differs from the keeper's row")


def sweep_work(env: Env) -> int:
    """Empty WORK_ROOT (leftovers of a killed run) before anything else; creates it 0700."""
    env.work_root.mkdir(parents=True, exist_ok=True)
    os.chmod(env.work_root, 0o700)
    n = 0
    for c in env.work_root.iterdir():
        shutil.rmtree(c, ignore_errors=True) if c.is_dir() and not c.is_symlink() else c.unlink()
        n += 1
    return n


def compute(manifest: Mapping, as_of_ms: int, env: Env, *, now_ms: int, code_state_fn: Callable[[], tuple[str | None, bool | None]], keep_bonding: bool = False) -> dict:
    check_registered_modules()
    if as_of_ms % HOUR_MS:
        raise G.Refused("--as-of must be on the hour (the output directory is named by the hour)")
    if as_of_ms < CUTOFF_MS:
        raise G.Refused(f"--as-of {G.iso(as_of_ms)} is before the cut-off {G.iso(CUTOFF_MS)}: nothing is priced")
    if as_of_ms > now_ms:
        raise G.Refused("--as-of is in the future")
    head, dirty = code_state_fn()
    if head != manifest["pin_commit"] or dirty is not False:
        raise G.Refused(f"the process runs code {head} (dirty {dirty}), not a clean checkout at the manifest's pin_commit {manifest['pin_commit']}")
    by_cid: dict[str, list] = {}
    exits_all: list[dict] = []
    shas: dict[str, dict] = {}
    for s in manifest["series"]:
        rows, shas[s["change_id"]] = read_chain_prefix(env, s["change_id"], as_of_ms)
        check_start_rows(rows, s["frozen_sha"])
        e, x = select(rows, s["change_id"], as_of_ms)
        by_cid[s["change_id"]], exits_all = e, exits_all + x
    entries = merge_series(by_cid, manifest)
    exit_keys = {(x["_cid"], x["order_id"]) for x in exits_all}
    stop_of = {s["change_id"]: s.get("dry_stop_ms") for s in manifest["series"] if s["role"] == "dry"}
    pending = {"no_exit_row": [], "open_at_as_of": []}
    priced_entries = []
    for e in entries:
        k = (e["_cid"], e["order_id"])
        if k in exit_keys:
            priced_entries.append(e)
        elif stop_of.get(e["_cid"]) is not None and stop_of[e["_cid"]] <= as_of_ms:
            pending["no_exit_row"].append(k)
        else:
            pending["open_at_as_of"].append(k)
    tail_hi = as_of_ms + P.EXIT_POST_MS
    files = P.tape_hours(env.tape_dir, TAPE_FLOOR_MS, tail_hi, now_ms)
    check_keeper_hours(env, files)
    work = env.check_work(env.work_root / f"work-{as_of_ms}-{os.getpid()}")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        res = replay(priced_entries, exits_all, files, manifest["d"], work, pin_commit=manifest["pin_commit"], code_state_fn=code_state_fn, env=env, keep_bonding=keep_bonding)
        return build_report(manifest=manifest, as_of_ms=as_of_ms, cfg=res["cfg"], entries=entries, exits=exits_all, res=res, prefix_shas=shas, priced_entries=priced_entries,
                            pending=pending)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run(manifest_path: str, manifest_sha256: str, as_of: str, *, roots=None, now_ms: int | None = None, code_state_fn=None, verify: bool = False,
        keep_bonding: bool = False) -> int:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        env = make_env(roots)
        swept = sweep_work(env)  # before anything else: leftovers of a killed run hold pool ids
        manifest = load_manifest(manifest_path, manifest_sha256)
        as_of_ms = G.utc_ms(as_of)
        csf = code_state_fn or (lambda: AP.process_code_state(Path(__file__).resolve().parent.parent))
        rep = compute(manifest, as_of_ms, env, now_ms=now, code_state_fn=csf, keep_bonding=keep_bonding)
        js, md, con = render(rep)
        d = env.out_root / f"as-of-{time.strftime('%Y%m%dT%H', time.gmtime(as_of_ms // 1000))}Z"
        if verify:
            old = env.check_write(d / "preview.json")
            same = old.exists() and old.read_bytes() == js.encode()
            print("EQUAL" if same else "DIFF")
            return 0 if same else 4
        if not (d / "preview.json").exists():
            first_output(env, rep["as_of"], hashlib.sha256(js.encode()).hexdigest(), manifest["pin_commit"])  # before the first link: a crash cannot lose the instant
        wrote = [write_once(env, d / "preview.json", js.encode()), write_once(env, d / "preview.md", md.encode()), write_once(env, d / "console.txt", con.encode())]
        print(json.dumps({"as_of": rep["as_of"], "written": any(wrote), "work_leftovers_removed": swept, "preview_json_sha256": hashlib.sha256(js.encode()).hexdigest(), "counts": rep["counts"]}, sort_keys=True))
        return 0
    except G.Refused as e:
        print(f"r4b_preview: REFUSED: {e}", file=sys.stderr)
        return 2
    except Untrusted as e:
        print(f"r4b_preview: UNTRUSTED: {e}", file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError) as e:
        print(f"r4b_preview: UNTRUSTED: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 3


def main(argv: Sequence[str] | None = None, **kw: Any) -> int:
    ap = argparse.ArgumentParser(description="r4-b preview pricer (paper; open output).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", required=True)
    r.add_argument("--manifest-sha256", required=True)
    r.add_argument("--as-of", required=True)
    r.add_argument("--verify", action="store_true")
    a = ap.parse_args(argv)
    return run(a.manifest, a.manifest_sha256, a.as_of, verify=a.verify, **kw)


if __name__ == "__main__":
    sys.exit(main())
