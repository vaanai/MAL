#!/usr/bin/env python3
"""EXP-024 read tool, forward mode: H5-BOOSTFLOOR v1, Look 1, on forward-1002 hours [2026-10-09T23, 2026-10-16T01).

Built on #476 (`tools/boostfloor_score.py`, the exploration port of RULE.md). That module keeps its own refusals and its
exploration-only scope; this file is the sealed forward read of EXP/EXP-024-h5-boostfloor-part1-prereg.md (sections 3-8, 10-12,
Amendment 1 for V, Amendment 4 for the population restriction). Section references below are to that file.

    python -m tools.boostfloor_read pins                 # blobs and pin lines only; opens no trade file
    python -m tools.boostfloor_read classify --look 1    # Am.4 B1-B4 class per V-range pool (RPC); counts only
    python -m tools.boostfloor_read precount --look 1    # P6: counts only, no fill, no P&L
    python -m tools.boostfloor_read look --look 1        # the locked read (once)

There is no flag that changes a number. Every rule, leg, cost, threshold, window, hour and path is a constant below. The CLI takes
only the subcommand and the look number.

SEAL. Every mode except `pins` refuses unless the EXP-012 FINAL marker is in the external FINAL ledger
(tools.forward_v_join.final_marker). The tool reads only the extractor's meta/paths for the section 3 hours, the P5 V files and the
P6 class file. It refuses forward-1016, walk-2, fresh-*, forward-paper, runner, shadow, canary and key paths, any hour outside the
allowlist (no `mal_catalog.check_read`: section 12, "Catalog"), any print outside the read hours, and Look 2 (walk-2 mode is a later PR).
`classify` and `precount` compute no fill, exit or P&L: they never call the pricing functions (a test checks it).

INPUT (forward mode; the extractor's meta/paths format, g_reachable_cap_book_rescore columns plus forward columns):
  extract/meta/<day>.parquet   mint, pool, s0, v (V0 lamports, NaN when no source has it), blk == "forward-1002", mbt (block time of
                               the `complete`), pcb (bool: a tape post_complete_buy row was seen), s0_sig, mig_sig, cmp_sig (may be "")
  extract/paths/<day>.parquet  mint, slot, isbuy, sol, tok, q, b, th, bt (PRE-trade tape reserves, q without V), sig, ei (event_index)
V source order (Am.1), per print, by (slot, signature, event_index): 1. forward-1002ev through `forward_v_join join` (only when P5 line A
passed); 2. P5's getTransaction records; 3. the pool account (V0 only: an account read at >= 10-16 cannot give V(t) at a past print);
4. none: the section 4 missing-V rule.

OUTPUT. Verdict and report on stderr first, then (new, empty dir) report.json and rows.csv, then the `completed` ledger line.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from tools import boostfloor_score as bf
from tools.latency_curve import Pressure, fit_curve

SCHEMA = "boostfloor_read_v1"
EXP_ID = "EXP-024"
REPO = Path(__file__).resolve().parents[1]
PREREG = "EXP/EXP-024-h5-boostfloor-part1-prereg.md"
COUNT_START_LINE = "EXP024_COUNT_START: 2026-10-10T00"
P3_PINS_PREFIX = "EXP024_P3_PINS:"
P3_PIN_KEYS = ("read_tool", "score_module", "latency_curve", "paper_curve_math", "extractor", "v_join", "synthetic_class")
P3_PIN_PATHS = {"read_tool": "tools/boostfloor_read.py", "score_module": "tools/boostfloor_score.py", "latency_curve": "tools/latency_curve.py",
                "paper_curve_math": "tools/paper_curve_math.py", "v_join": "tools/forward_v_join.py", "synthetic_class": "tools/synthetic_class.py"}

# ---- frozen inputs (section 2) ----------------------------------------------------------------------------------------
H5_FLOWS = Path("/data/mal/hunt-1008/h5-flows")
FROZEN_SHA256 = {
    "RULE.md": bf.RULE_SHA256,
    "s14_boostdip.py": "70becfb7d7e48b8e24cee9ae807db29200db9990cf96d18ab2dce755301c1ad0",
    "s15_score_dip.py": "b91febe61613c2e94792c627bd5bc0cbe54b52f6a7beb874f7af8ed9f3410e89",
    "common.py": "8061d4c3ebd57d0aee75c0356a23f9048b3272624547670cdf8604a273fb56d9",
}

# ---- fixed locations (no CLI override) -------------------------------------------------------------------------------
ROOT = Path("/data/mal/exp024")
LOOK_READS = ROOT / "LOOK_READS.jsonl"
FINAL_LEDGER = Path("/data/mal/exp012-forward/FINAL_READS.jsonl")
FORWARD_1002 = Path("/data/mal/blocks/forward-1002")
A3_DAILY = Path("/data/mal/structure-monitor/daily.jsonl")


@dataclass(frozen=True)
class Layout:
    """Where one look's inputs and outputs live. Production = Layout.for_look(ROOT, 1). Tests build one on a temp dir."""

    root: Path
    look: int

    @classmethod
    def for_look(cls, root: Path, look: int) -> "Layout":
        return cls(root, look)

    @property
    def look_dir(self) -> Path: return self.root / f"look{self.look}"
    @property
    def extract(self) -> Path: return self.look_dir / "extract"
    @property
    def p5(self) -> Path: return self.root / "p5"
    @property
    def vjoin(self) -> Path: return self.p5 / "vjoin"  # forward_v_join join --out-dir
    @property
    def cross_source(self) -> Path: return self.p5 / "cross_source.json"  # {"line_a_pass": bool, counts...}
    @property
    def gettx_v(self) -> Path: return self.p5 / "gettx_v.jsonl"  # {slot, signature, event_index, virtual_quote_reserves}
    @property
    def account_v0(self) -> Path: return self.p5 / "account_v0.json"  # {pool: V0 lamports} from exp012_forward_vmap (v_base)
    @property
    def classes(self) -> Path: return self.root / "p6" / f"classes-look{self.look}.jsonl"  # {pool, mint, class, reason}
    @property
    def precount(self) -> Path: return self.root / "p6" / f"precount-look{self.look}.json"
    @property
    def p7(self) -> Path: return self.root / "p7" / "pricing_check.json"  # {"pass": bool, ...}
    @property
    def e1(self) -> Path: return self.root / "e1" / "calibration.json"  # E1 output (EXP-022 section 8), copied with its sha256 recorded
    @property
    def out(self) -> Path: return self.look_dir / "out"
    @property
    def lock(self) -> Path: return self.root / f"look{self.look}.lock"
    @property
    def ledger(self) -> Path: return self.root / "LOOK_READS.jsonl"


# ---- windows and hours (section 3) ------------------------------------------------------------------------------------
def _ts(h: str) -> int:
    return int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def _hour(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(int(t)))


def _date(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(t)))


LOOK1 = dict(count_lo="2026-10-10T00", count_hi="2026-10-16T00", read_lo="2026-10-09T23", read_hi="2026-10-16T01",
             alpha=0.020, deadline="2026-10-17T12:00:00Z", a3_run_date="2026-10-16", n_dates=6, min_pos_dates=4)
READ_HOURS = tuple(_hour(t) for t in range(_ts(LOOK1["read_lo"]), _ts(LOOK1["read_hi"]), 3600))
COUNT_HOURS = tuple(_hour(t) for t in range(_ts(LOOK1["count_lo"]), _ts(LOOK1["count_hi"]), 3600))
READ_DAYS = tuple(sorted({h[:10] for h in READ_HOURS}))
BLOCK = "forward-1002"
FORBIDDEN = ("fresh-0802", "fresh-0808", "fresh-0828", "forward-1016", "forward-walk2", "forward_walk2", "walk-2", "walk2", "forward-paper",
             "forward-walk", "oracle-live", "exp012-gate", "arm-audit", "runner-status", "heartbeat", "positions", "/var/lib/mal/paper",
             "h5-shadow", "h5_shadow", "h5-executor", "h5_executor", "probe-final", "canary", ".env", "helius", "keypair", "id.json")

# ---- the rule's numbers and section 4-8 constants (no CLI override) ---------------------------------------------------
STAKE = 100_000_000.0  # deciding stake, 0.1 SOL
PRIO = bf.PRIO_LAMPORTS  # 55,000 per send
EXIT_S = bf.EXIT_AFTER_S0_S  # 330 s
CELLS = {  # name: (entry s, sell lag s, guard)
    "D": (1.9, 0.55, False),
    "B1": (3.0, 1.35, False),
    "B2": (1.9, 0.55, True),
}
REPORT_LEGS = {"R_rule_1p3": (1.3, 0.55, False)}  # section 13: the rule's original entry (report-only)
GUARD = 0.15  # B2 min_out = floor(cp_buy_out(0.1 SOL, Q_i, B_i, tier) / 1.15)
HAIRCUT = 1 - (1 - 0.002608) * (1 - 0.0016)  # correction (a), on sell proceeds after the pool fee
E1_PROBE_STAKE = 50_000_000.0  # r-bar is per 0.05 SOL trip; (b) = max(0, -r-bar) * stake / 0.05 SOL (= -2 r-bar at 0.1 SOL)
E1_MIN_N = 20
RENT = 2_039_280  # charged only when the sell cannot fill the full balance (never under the constant-product END sell)
PENDING_HI = 0.0383  # missing V(t): pending at the exit state = 3.83% of effective quote, 0 at entry (section 4)
V0_UNKNOWN_CASES = (17.5e9, 17.7e9)
SLOT_SWITCH = 454_896_000  # first slot of epoch 1053 (section 4)
FLAT_FAIL = bf.FLAT_FAIL
BOOT_DRAWS, BOOT_SEED = 1_000, 1
REPORT_P_DRAWS = 10_000
MIN_TRIGGERS, MIN_TRIGGER_DATES = 100, 5
MAX_MISSING_V_SHARE = 0.01
MAX_UNCLASSIFIED_SHARE = 0.01
MAX_BAD_HOUR_SHARE = 0.05
LAST_SLICE_MIN_S = 330.0
A3_FLAGS = ("pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed")  # Am.4 C1
DISCLOSURE = ("By the owner's decision (10-08), the live canary's and the shadow detector's outcomes for pools inside Look 1's window were "
              "observed in real time (EXP-024 section 3.1), and canary and shadow outcomes for pools with s0 before 2026-10-10T00 were observed "
              "in real time from about 2026-10-08T21:30Z (shadow) and from the first canary send (canary) (Amendment 3). Concurrent observation "
              "can have influenced later choices such as an owner scale-up. It does not change the verdict. The live canary's own trades are in "
              "the tape as ordinary rows and are not removed (section 3).")


class Refused(Exception):
    pass


# ---- refusals -----------------------------------------------------------------------------------------------------------
def refuse_name(path: str | Path) -> None:
    s = str(path)
    for bad in FORBIDDEN:
        if bad in s:
            raise Refused(f"{s}: refused ({bad!r} is not an EXP-024 Look 1 input)")


def refuse_hour(hour: str) -> None:
    if hour not in READ_HOURS:
        raise Refused(f"hour {hour}: refused (Look 1 reads forward-1002 [{LOOK1['read_lo']}, {LOOK1['read_hi']}) only)")


def refuse_day(day: str) -> None:
    if day not in READ_DAYS:
        raise Refused(f"day {day!r}: refused (not a day of the Look 1 read hours)")


def refuse_look(look: int) -> None:
    if look != 1:
        raise Refused(f"look {look}: refused (this build reads Look 1 only; Look 2 reads walk 2 after EXP-022's read ends and needs its own reviewed mode)")


# ---- integrity: section 0 line, prereg clean, frozen files, P3 pins (section 12, Refusal) -----------------------------
def git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_count_start(text: str) -> None:
    n = len(re.findall(r"(?m)^" + re.escape(COUNT_START_LINE) + r"$", text))
    if n != 1:
        raise Refused(f"section 0: the line {COUNT_START_LINE!r} matches {n} times, not once")


def parse_p3_pins(text: str) -> dict[str, tuple[str, str]]:
    """The P3 amendment's pin line: `EXP024_P3_PINS: key=path@<40-hex blob> ...` for every key in P3_PIN_KEYS, exactly once."""
    lines = [ln for ln in text.splitlines() if ln.startswith(P3_PINS_PREFIX)]
    if len(lines) != 1:
        raise Refused(f"P3: {len(lines)} lines start with {P3_PINS_PREFIX!r} in {PREREG}; the dated P3 amendment must carry exactly one")
    pins: dict[str, tuple[str, str]] = {}
    for tok in lines[0][len(P3_PINS_PREFIX):].split():
        m = re.fullmatch(r"([a-z_0-9]+)=([A-Za-z0-9_./-]+)@([0-9a-f]{40})", tok)
        if not m:
            raise Refused(f"P3: pin token {tok!r} is not key=path@blob")
        pins[m.group(1)] = (m.group(2), m.group(3))
    missing = [k for k in P3_PIN_KEYS if k not in pins]
    if missing:
        raise Refused(f"P3: pin line lacks {missing}")
    for k, p in P3_PIN_PATHS.items():
        if pins[k][0] != p:
            raise Refused(f"P3: pin {k} names {pins[k][0]}, expected {p}")
    return pins


def check_pins(pins: Mapping[str, tuple[str, str]], repo: Path) -> None:
    for k, (rel, blob) in pins.items():
        got = git_blob(repo / rel)
        if got != blob:
            raise Refused(f"P3/P4: {rel} is blob {got}, pinned {blob}")


def check_frozen(h5_flows: Path) -> None:
    for name, want in FROZEN_SHA256.items():
        p = h5_flows / name
        if not p.is_file() or sha256_file(p) != want:
            raise Refused(f"section 2: {p} does not hash to {want[:12]}...")


def check_clean(repo: Path, rel_paths: Iterable[str]) -> None:
    for rel in rel_paths:
        r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", rel], capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or r.stdout.strip():
            raise Refused(f"{rel} is not clean against HEAD")


def integrity(repo: Path = REPO, h5_flows: Path = H5_FLOWS) -> dict[str, Any]:
    text = (repo / PREREG).read_text(encoding="utf-8")
    check_count_start(text)
    pins = parse_p3_pins(text)
    check_clean(repo, [PREREG] + [p for p, _ in pins.values()])
    check_pins(pins, repo)
    check_frozen(h5_flows)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60).stdout.strip()
    return {"head": head, "pins": {k: f"{p}@{b}" for k, (p, b) in sorted(pins.items())}}


# ---- conditions (section 8.1) ---------------------------------------------------------------------------------------------
def read_e1(path: Path) -> tuple[int, float]:
    if not path.is_file():
        raise Refused(f"E1 not recorded: {path} is missing (condition (c))")
    agg = json.loads(path.read_text(encoding="utf-8"))["aggregate"]["faa3192"]["pnl_gap_lamports_live_minus_sim"]
    n, mean = int(agg["n"]), agg["mean"]
    if n < E1_MIN_N or mean is None:
        raise Refused(f"E1 n = {n} < {E1_MIN_N}: no look runs (section 5); a look not run by its deadline is NOT_DECIDABLE")
    return n, float(mean)


def hour_states(walk_dir: Path, hours: Sequence[str]) -> dict[str, str]:
    from tools.forward_v_join import hour_state

    return {h: hour_state(walk_dir, h, strict=True)[1] for h in hours}


def a3_conditions(daily: Path, *, lo: str, run_date: str) -> dict[str, Any]:
    """Five halt flags one by one (Am.4 C1), a core rule unevaluated on two consecutive runs, and the median of the daily last-slice
    medians over the window. Refuses until the run of `run_date` (the 10-16 06:41Z run) is in the file. Reads no outcome."""
    recs = []
    for line in daily.read_text(encoding="utf-8").splitlines() if daily.is_file() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and str(r.get("run_utc", "")) >= lo:
            recs.append(r)
    recs.sort(key=lambda r: str(r["run_utc"]))
    if not any(str(r["run_utc"])[:10] == run_date for r in recs):
        raise Refused(f"condition (f): no A3 run dated {run_date} in {daily} yet")
    halts, unevaluated2, prev_ne = [], [], set()
    meds = []
    for r in recs:
        flags = ((r.get("halt") or {}).get("flags") or {})
        ne = set()
        for f in A3_FLAGS:
            v = flags.get(f) or {}
            if v.get("halt"):
                halts.append(f"{r['run_utc']} {f}")
            if v.get("evaluated") is False or f not in flags:
                ne.add(f)
        unevaluated2 += [f"{r['run_utc']} {f}" for f in sorted(ne & prev_ne)]
        prev_ne = ne
        m = ((r.get("boost") or {}).get("last_slice_after_migrate_s") or {}).get("median")
        if m is not None:
            meds.append(float(m))
    if not meds:
        raise Refused("section 11: no A3 run in the window carries a last-slice median; cannot evaluate the 330 s condition")
    return {"runs": len(recs), "halts": halts, "unevaluated_two_runs": unevaluated2, "last_slice_median_s": float(np.median(meds)),
            "synthetic_share_high": [str(r["run_utc"]) for r in recs if (((r.get("halt") or {}).get("flags") or {}).get("synthetic_share_high") or {}).get("halt")]}


# ---- lock and ledger (section 12) -----------------------------------------------------------------------------------------
def ledger_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise Refused(f"{path} ends with a torn line")
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def ledger_allows(events: Sequence[Mapping[str, Any]], look: int) -> None:
    for e in events:
        if e.get("experiment") != EXP_ID:
            continue
        if e.get("look") == look and e.get("event") == "started":
            raise Refused(f"Look {look} already started at {e.get('utc')}: a look runs once")
        if e.get("event") == "completed" and e.get("verdict") in ("PASS", "FAIL_FUTILITY", "NOT_DECIDABLE_HALT") and look > e.get("look", 0):
            raise Refused(f"Look {look}: refused after Look {e.get('look')} ended {e.get('verdict')}")


def append_event(path: Path, rec: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(dict(rec), sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def take_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        raise Refused(f"{path} exists: the look's O_EXCL lock was already taken") from None
    os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
    os.close(fd)


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


# ---- V sources (Am.1) -----------------------------------------------------------------------------------------------------
Key = tuple[int, str, int]


@dataclass
class VSources:
    ev: dict[Key, int] | None  # None: P5 line A failed (or absent), so forward-1002ev is not used
    gettx: dict[Key, int]
    account_v0: dict[str, int]

    def v(self, key: Key) -> tuple[float, str]:
        if self.ev is not None and key in self.ev:
            return float(self.ev[key]), "ev"
        if key in self.gettx:
            return float(self.gettx[key]), "gettx"
        return math.nan, "none"

    def v0(self, key: Key, pool: str) -> tuple[float, str]:
        v, src = self.v(key)
        if src != "none":
            return v, src
        if pool in self.account_v0:
            return float(self.account_v0[pool]), "account"
        return math.nan, "none"


def _rec_key(r: Mapping[str, Any]) -> Key | None:
    try:
        return int(r["slot"]), str(r["signature"]), int(r["event_index"])
    except (KeyError, TypeError, ValueError):
        return None


def load_vsources(lay: Layout) -> VSources:
    cs = json.loads(lay.cross_source.read_text(encoding="utf-8")) if lay.cross_source.is_file() else None
    if cs is None:
        raise Refused(f"P5 not done: {lay.cross_source} is missing (condition (e))")
    ev: dict[Key, int] | None = None
    if cs.get("line_a_pass") is True:
        from tools.forward_v_join import RowReader, hour_file_named

        ev = {}
        for h in READ_HOURS:
            f = hour_file_named(lay.vjoin, f"v-{h}")
            if f is None:
                continue
            for r in RowReader(f):
                k = _rec_key(r)
                if k is not None and isinstance(r.get("virtual_quote_reserves"), int):
                    ev[k] = int(r["virtual_quote_reserves"])
    gettx: dict[Key, int] = {}
    if lay.gettx_v.is_file():
        for ln in lay.gettx_v.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                k = _rec_key(r)
                if k is not None and isinstance(r.get("virtual_quote_reserves"), int):
                    gettx[k] = int(r["virtual_quote_reserves"])
    acct = json.loads(lay.account_v0.read_text(encoding="utf-8")) if lay.account_v0.is_file() else {}
    return VSources(ev, gettx, {str(k): int(v) for k, v in acct.items() if isinstance(v, int)})


# ---- pools (extractor format) ---------------------------------------------------------------------------------------------
@dataclass
class FwdPool:
    mint: str
    pool: str
    s0: int
    s0_bt: int
    mbt: int
    v0: float  # NaN: unknown
    v0_src: str
    cls: str  # synthetic | non_synthetic | unclassified
    path: bf.PoolPath
    vt: np.ndarray  # V(t) per print (the stored V of the print's event), NaN when no source has it
    vt_src: list[str] = field(default_factory=list)


def classify_from(tape_pcb: bool, rpc_class: str | None) -> str:
    """Am.4 B2: the tape can only mark synthetic; the class otherwise comes from getTransaction (classify); none -> unclassified."""
    if tape_pcb:
        return "synthetic"
    return rpc_class if rpc_class in ("synthetic", "non_synthetic") else "unclassified"


def load_classes(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise Refused(f"P6 classes missing: {path} (run `classify --look 1` after the FINAL)")
    out: dict[str, str] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            out[str(r["pool"])] = str(r["class"])
    return out


def load_pools(lay: Layout, vs: VSources, classes: Mapping[str, str]) -> list[FwdPool]:
    """Every meta row of the read days (pandas, lazily). Refuses a forbidden name, a foreign block, a day or print outside the read hours."""
    import pandas as pd

    refuse_name(lay.extract)
    pools: list[FwdPool] = []
    lo, hi = _ts(LOOK1["read_lo"]), _ts(LOOK1["read_hi"])
    for day in READ_DAYS:
        mp, pp = lay.extract / "meta" / f"{day}.parquet", lay.extract / "paths" / f"{day}.parquet"
        if not mp.is_file() and not pp.is_file():
            continue
        refuse_day(day)
        meta, paths = pd.read_parquet(mp), pd.read_parquet(pp)
        if set(meta.blk.astype(str)) - {BLOCK}:
            raise Refused(f"{mp}: block {sorted(set(meta.blk.astype(str)) - {BLOCK})} refused (forward-1002 only)")
        if len(paths) and (int(paths.bt.min()) < lo or int(paths.bt.max()) >= hi):
            raise Refused(f"{pp}: a print lies outside [{LOOK1['read_lo']}, {LOOK1['read_hi']})")
        for t in meta.mbt:
            if int(t) >= lo:  # an earlier `complete` is not read: the pool fails Coverage below
                refuse_hour(_hour(int(t)))
        groups = {m: g for m, g in paths.groupby("mint", sort=False)}
        for r in meta.itertuples(index=False):
            g = groups.get(r.mint)
            if g is None:
                continue
            g = g[g.slot >= int(r.s0)].sort_values(["slot", "ei"], kind="stable")
            if not len(g):
                continue
            keys = list(zip(g.slot.astype(int), g.sig.astype(str), g.ei.astype(int)))
            vv = [vs.v(k) for k in keys]
            v0, v0_src = (float(r.v), "extract") if r.v == r.v else vs.v0(keys[0], str(r.pool))
            pools.append(FwdPool(
                mint=str(r.mint), pool=str(r.pool), s0=int(r.s0), s0_bt=int(g.bt.iloc[0]), mbt=int(r.mbt), v0=v0, v0_src=v0_src,
                cls=classify_from(bool(getattr(r, "pcb", False)), classes.get(str(r.pool))),
                path=bf.PoolPath(sl=g.slot.values.astype(np.int64), q=g.q.values.astype(float), b=g.b.values.astype(float),
                                 isb=g.isbuy.values.astype(bool), sol=g.sol.values.astype(float), tok=g.tok.values.astype(float),
                                 th=g.th.values.astype(np.uint64), bt=g.bt.values.astype(np.int64)),
                vt=np.array([x[0] for x in vv], float), vt_src=[x[1] for x in vv]))
    return pools


# ---- structure (no prices): universe, trigger, exclusions. Used by precount and look alike -------------------------------
@dataclass
class Trig:
    v0: float
    i: int  # trigger print index
    qpost: np.ndarray  # tape quote + V0, post-trade
    bpost: np.ndarray


@dataclass
class Struct:
    status: str  # outside | v_range | synthetic | unclassified | short | unsorted | sps | coverage | slot_switch | no_trigger | trigger
    date: str = ""
    sps: float = math.nan
    trigs: list[Trig] = field(default_factory=list)  # one per V0 case (one when V0 is known)
    v0_unknown: bool = False


def _in_vrange(v0: float) -> bool:
    return bf.V_LO <= v0 <= bf.V_HI


def structure(p: FwdPool, good_hours: frozenset[str]) -> Struct:
    lo, hi = _ts(LOOK1["count_lo"]), _ts(LOOK1["count_hi"])
    if not (lo <= p.s0_bt < hi):
        return Struct("outside")
    v0_unknown = not (p.v0 == p.v0)
    if not v0_unknown and not _in_vrange(p.v0):
        return Struct("v_range")
    d = _date(p.s0_bt)
    if p.cls != "non_synthetic":
        return Struct(p.cls, d, v0_unknown=v0_unknown)
    sl = p.path.sl
    if len(sl) < bf.MIN_PATH_PRINTS:
        return Struct("short", d, v0_unknown=v0_unknown)
    if np.any(np.diff(sl) < 0):
        return Struct("unsorted", d, v0_unknown=v0_unknown)
    sps = bf.seconds_per_slot(sl, p.path.bt)
    if not bf.sps_ok(sps):
        return Struct("sps", d, sps, v0_unknown=v0_unknown)
    exit_slot = p.s0 + int(round(EXIT_S / sps))
    last_exit = exit_slot + max(math.ceil(lag / sps - 1e-9) for _, lag, _ in list(CELLS.values()) + list(REPORT_LEGS.values()))
    exit_bt = p.s0_bt + (last_exit - p.s0) * sps
    need = [_hour(t) for t in range(_ts(_hour(p.mbt)), int(exit_bt) + 1, 3600)] + [_hour(exit_bt)]
    if any(h not in good_hours for h in need):
        return Struct("coverage", d, sps, v0_unknown=v0_unknown)
    if p.s0 < SLOT_SWITCH <= last_exit:
        return Struct("slot_switch", d, sps, v0_unknown=v0_unknown)
    isb, sol = p.path.isb, np.asarray(p.path.sol, float)
    boost = bf.detect_boost_wallet(sl, p.s0, p.path.th, sol, isb)
    spent = bf.boost_spent(p.path.th, sol, boost)
    trigs = []
    for v0 in (V0_UNKNOWN_CASES if v0_unknown else (p.v0,)):
        qpost, bpost = bf.post_trade_state(p.path.q + v0, p.path.b, isb, sol, p.path.tok)
        i = bf.find_trigger(sl, p.s0, sps, isb, qpost, spent)
        if i is not None:
            trigs.append(Trig(v0, i, qpost, bpost))
    return Struct("trigger" if trigs else "no_trigger", d, sps, trigs, v0_unknown)


def precount(pools: Sequence[FwdPool], good_hours: frozenset[str], bad_hours: Sequence[str]) -> dict[str, Any]:
    """P6 (section 10, Am.4 C2): counts only. No fill, exit or P&L is computed here."""
    per: dict[str, dict[str, int]] = {}
    tot: dict[str, int] = {}
    vrange_in_window = unclassified = 0
    for p in pools:
        s = structure(p, good_hours)
        if s.status in ("outside", "v_range"):
            tot[s.status] = tot.get(s.status, 0) + 1
            continue
        vrange_in_window += 1
        unclassified += s.status == "unclassified"
        row = per.setdefault(s.date, {})
        cls = p.cls if p.cls in ("synthetic", "unclassified") else "non_synthetic"
        for k in sorted({cls, s.status} | ({"null_v0"} if s.v0_unknown else set())):
            row[k] = row.get(k, 0) + 1
            tot[k] = tot.get(k, 0) + 1
    trig_dates = sorted(d for d, r in per.items() if r.get("trigger", 0) > 0)
    n_trig = tot.get("trigger", 0)
    return {"good_hours": len(good_hours & set(COUNT_HOURS)), "bad_count_hours": len(bad_hours), "count_hours": len(COUNT_HOURS),
            "complete_events": len(pools), "vrange_pools_in_window": vrange_in_window, "unclassified": unclassified,
            "totals": dict(sorted(tot.items())), "per_date": {d: dict(sorted(r.items())) for d, r in sorted(per.items())},
            "triggers": n_trig, "trigger_dates": len(trig_dates),
            "refuse_p6": n_trig < MIN_TRIGGERS or len(trig_dates) < MIN_TRIGGER_DATES,
            "unclassified_share": unclassified / vrange_in_window if vrange_in_window else 0.0,
            "bad_hour_share": len(bad_hours) / len(COUNT_HOURS)}


# ---- pricing (look only) --------------------------------------------------------------------------------------------------
def state_index(sl: np.ndarray, slot: int) -> int:
    """END bound: index of the print whose PRE-trade state is the state after every print in slots <= slot; len(sl) = after the last."""
    return int(np.searchsorted(sl, slot, "right"))


def round_trip(qe: float, be: float, qx: float, bx: float, stake: float) -> dict[str, float]:
    """bf.fill_round_trip, returning the pieces the correction and the guard need. Same arithmetic, in the same order."""
    f = bf.tier_fee(qe, be)
    net = stake * (1 - f)
    tk = be * net / (qe + net)
    q2, b2 = qx + net, bx - tk
    proceeds = tk * q2 / (b2 + tk) * (1 - bf.tier_fee(q2, b2 + tk))
    return {"tokens": tk, "proceeds": proceeds, "pnl_raw": proceeds - stake - 2 * PRIO, "gross": (q2 / b2) / (qe / be) - 1}


def cp_buy_out(stake: float, q: float, b: float) -> float:
    net = stake * (1 - bf.tier_fee(q, b))
    return b * net / (q + net)


def correction(proceeds: float, stake: float, rbar: float, which: str = "max") -> float:
    a = proceeds * HAIRCUT
    b = max(0.0, -rbar) * stake / E1_PROBE_STAKE
    return {"max": max(a, b), "a": a, "b": b, "none": 0.0}[which]


def price_cell(p: FwdPool, s: Struct, cell: tuple[float, float, bool], *, rbar: float, stake: float = STAKE, literal_v0: bool = False,
               corr: str = "max") -> dict[str, Any] | None:
    """One pool, one leg. Lower P&L over the V0 cases (unknown V0) and the missing-V(t) cases (section 4). None: no exit after landing."""
    entry_s, lag_s, guard = cell
    sl, q, b = p.path.sl, p.path.q, p.path.b
    sol, isb = np.asarray(p.path.sol, float), p.path.isb
    qtp, btp = bf.post_trade_state(q, b, isb, sol, p.path.tok)  # tape-only post-trade, for the state after the last print
    exit_slot = p.s0 + int(round(EXIT_S / s.sps))
    best: dict[str, Any] | None = None
    for tr in s.trigs:
        landing = int(sl[tr.i]) + math.ceil(entry_s / s.sps - 1e-9)
        xl = exit_slot + math.ceil(lag_s / s.sps - 1e-9)
        if exit_slot <= landing:
            continue

        def st(slot: int) -> tuple[float, float, int]:
            j = state_index(sl, slot)
            return (float(q[j]), float(b[j]), j) if j < len(sl) else (float(qtp[-1]), float(btp[-1]), len(sl) - 1)

        (qe_t, be, je), (qx_t, bx, jx) = st(landing), st(xl)
        missing = False
        if literal_v0:
            cases = [(tr.v0, tr.v0)]
        else:
            ve = p.vt[je] if p.vt[je] == p.vt[je] else tr.v0
            if p.vt[jx] == p.vt[jx]:
                vxs = [float(p.vt[jx])]
            else:
                vxs = [tr.v0, tr.v0 - PENDING_HI * (qx_t + tr.v0) / (1 + PENDING_HI)]
            missing = not (p.vt[je] == p.vt[je]) or len(vxs) > 1
            cases = [(float(ve), vx) for vx in vxs]
        ssb, nb = bf.pressure_at(sl, isb, sol, landing, s.sps)
        min_out = math.floor(cp_buy_out(stake, float(tr.qpost[tr.i]), float(tr.bpost[tr.i])) / (1 + GUARD)) if guard else None
        for ve, vx in cases:
            rt = round_trip(qe_t + ve, be, qx_t + vx, bx, stake)
            reverted = guard and rt["tokens"] < min_out
            if reverted:
                pnl, c, rent = -float(PRIO), 0.0, 0
            else:
                c = correction(rt["proceeds"], stake, rbar, corr)
                rent = RENT if rt["tokens"] >= bx else 0
                pnl = rt["pnl_raw"] - c - rent
            row = dict(pnl_lamports=pnl, reverted=bool(reverted), correction_lamports=c, rent_lamports=rent, gross=rt["gross"],
                       landing_slot=landing, exit_landing_slot=xl, ssb=ssb, nb_lamports=nb, v_missing=bool(missing or s.v0_unknown),
                       v_entry_minus_v0=ve - tr.v0, v_exit_minus_v0=vx - tr.v0, trig_t_s=float((sl[tr.i] - p.s0) * s.sps),
                       q_trig_sol=float(tr.qpost[tr.i] / 1e9), sps=s.sps)
            if best is None or row["pnl_lamports"] < best["pnl_lamports"]:
                best = row
    return best


def apply_fail(rows: list[dict[str, Any]]) -> None:
    """Flat and pressure legs in place. The curve's intercept is refit on this cell's own sends (section 6). A reverted B2 buy is -55,000
    under both legs (section 5)."""
    if not rows:
        return
    curve = fit_curve([Pressure(int(r["ssb"]), int(r["nb_lamports"])) for r in rows])
    for r in rows:
        p = curve.p(Pressure(int(r["ssb"]), int(r["nb_lamports"])))
        r["p_fail"] = p
        if r["reverted"]:
            r["pnl_flat_lamports"] = r["pnl_press_lamports"] = -float(PRIO)
        else:
            r["pnl_flat_lamports"] = (1 - FLAT_FAIL) * r["pnl_lamports"] + FLAT_FAIL * (-PRIO)
            r["pnl_press_lamports"] = (1 - p) * r["pnl_lamports"] + p * (-PRIO)


# ---- statistics (section 7) -----------------------------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float) -> float:
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def t_sf(t: float, df: float) -> float:
    """P(T >= t), Student's t with df degrees of freedom."""
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    p = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return p if t >= 0 else 1.0 - p


def boot_means(x: np.ndarray, draws: int, seed: int) -> np.ndarray:
    """Trade bootstrap: means of `draws` resamples of n of n with replacement, rng.integers(0, n, (draws, n)) read in chunks."""
    n, rng, out = len(x), np.random.default_rng(seed), np.empty(draws)
    step = max(1, 4_000_000 // max(1, n))
    for lo in range(0, draws, step):
        hi = min(draws, lo + step)
        out[lo:hi] = x[rng.integers(0, n, size=(hi - lo, n))].mean(1)
    return out


def day_level(x_sol: np.ndarray, dates: Sequence[str]) -> dict[str, Any]:
    d = np.asarray(dates)
    ud = sorted(set(dates))
    m = np.array([x_sol[d == u].mean() for u in ud])
    w = len(m)
    if w < 2:
        return {"W": w, "t": None, "p": 1.0}
    sd = float(np.std(m, ddof=1))
    if sd == 0:
        return {"W": w, "t": None, "p": 0.0 if m.mean() > 0 else 1.0}
    t = float(m.mean() / (sd / math.sqrt(w)))
    return {"W": w, "t": t, "p": t_sf(t, w - 1)}


def leg_stats(pnl_lamports: Sequence[float], dates: Sequence[str]) -> dict[str, Any]:
    x = np.asarray(pnl_lamports, float) / 1e9
    if len(x) == 0:
        return {"n": 0}
    d = np.asarray(dates)
    ud = sorted(set(dates))
    per_day = {u: float(x[d == u].sum()) for u in ud}
    srt = np.sort(x)[::-1]
    bm = boot_means(x, BOOT_DRAWS, BOOT_SEED)
    return dict(n=int(len(x)), dates=len(ud), dates_pos=sum(v > 0 for v in per_day.values()), mean_sol=float(x.mean()),
                total_sol=float(x.sum()), ci5_sol=float(np.percentile(bm, 5)), ex_top3_sol=float(x.sum() - srt[:3].sum()),
                ex_best_date_sol=float(x.sum() - max(per_day.values())), day_level=day_level(x, dates), per_date_sol=per_day,
                median_sol=float(np.median(x)))


def cell_items(st: dict[str, Any], alpha: float) -> dict[str, bool]:
    if st.get("n", 0) == 0:
        return dict(n_ge_100=False, dates_ge_5_majority_pos=False, ci5_gt_0=False, ex_top3_gt_0=False, ex_best_date_gt_0=False, day_p_le_alpha=False)
    return dict(n_ge_100=st["n"] >= 100, dates_ge_5_majority_pos=st["dates"] >= 5 and 2 * st["dates_pos"] > st["dates"],
                ci5_gt_0=st["ci5_sol"] > 0, ex_top3_gt_0=st["ex_top3_sol"] > 0, ex_best_date_gt_0=st["ex_best_date_sol"] > 0,
                day_p_le_alpha=st["day_level"]["p"] <= alpha)


def verdict(cells: Mapping[str, Mapping[str, Any]], alpha: float) -> dict[str, Any]:
    """Section 7 and 8.3 on the corrected cells. cells[name][leg] = leg_stats, leg in (flat, press)."""
    D = cells["D"]
    items = {leg: cell_items(D[leg], alpha) for leg in ("flat", "press")}
    p_dec = max(D["flat"].get("day_level", {}).get("p", 1.0), D["press"].get("day_level", {}).get("p", 1.0))
    for leg in ("flat", "press"):  # item 6: the larger p decides
        items[leg]["day_p_le_alpha"] = D[leg].get("n", 0) > 0 and p_dec <= alpha
    binding = {c: {leg: bool(cells[c][leg].get("n", 0) > 0 and cells[c][leg]["mean_sol"] > 0 and cells[c][leg]["ex_top3_sol"] > 0)
                   for leg in ("flat", "press")} for c in ("B1", "B2")}
    futility = all(D[leg].get("n", 0) == 0 or D[leg]["mean_sol"] <= 0 for leg in ("flat", "press"))
    ok = all(all(v.values()) for v in items.values()) and all(all(v.values()) for v in binding.values())
    return {"verdict": "PASS" if ok else ("FAIL_FUTILITY" if futility else "FAIL"), "items_D": items, "binding": binding,
            "day_level_p_deciding": p_dec, "alpha": alpha, "futility": futility}


def missing_v_check(cells_rows: Mapping[str, list[dict[str, Any]]]) -> list[str]:
    """Section 4: missing-V trades > 1% of the look's trades (D), or in the top 3 of D or a binding leg under either fail leg."""
    why = []
    D = cells_rows["D"]
    if D and sum(r["v_missing"] for r in D) > MAX_MISSING_V_SHARE * len(D):
        why.append(f"missing V on {sum(r['v_missing'] for r in D)} of {len(D)} D trades (> 1%)")
    for c, rows in cells_rows.items():
        for leg in ("flat", "press"):
            top = sorted(rows, key=lambda r: -r[f"pnl_{leg}_lamports"])[:3]
            if any(r["v_missing"] for r in top):
                why.append(f"a missing-V trade is in the top 3 of {c} ({leg})")
    return why


def rows_md5(cells_rows: Mapping[str, list[dict[str, Any]]]) -> str:
    """Section 8.4 / 12 Fixes: md5 over (cell, mint, pnl rounded to the lamport) of every binding row, in a fixed order."""
    h = hashlib.md5()
    for c in sorted(cells_rows):
        for r in sorted(cells_rows[c], key=lambda r: r["mint"]):
            h.update(f"{c},{r['mint']},{round(r['pnl_lamports'])}\n".encode())
    return h.hexdigest()


# ---- the look -------------------------------------------------------------------------------------------------------------
@dataclass
class LookInputs:
    pools: list[FwdPool]
    good_hours: frozenset[str]
    bad_hours: list[str]
    rbar: float
    e1_n: int
    a3: dict[str, Any]
    p7_pass: bool
    precount_file: dict[str, Any] | None


def not_decidable_reasons(pc: Mapping[str, Any], a3: Mapping[str, Any], p7_pass: bool) -> list[str]:
    """Section 11 and Am.4 B3 conditions that hold before any outcome. Any one makes the look NOT_DECIDABLE with no P&L computed."""
    why = []
    if pc["refuse_p6"]:
        why.append(f"P6: {pc['triggers']} triggers on {pc['trigger_dates']} dates (need >= {MIN_TRIGGERS} and >= {MIN_TRIGGER_DATES})")
    if not p7_pass:
        why.append("P7 pricing check failed")
    if pc["unclassified_share"] > MAX_UNCLASSIFIED_SHARE:
        why.append(f"Am.4 B3: {pc['unclassified']} of {pc['vrange_pools_in_window']} V-range pools unclassified (> 1%)")
    if pc["bad_hour_share"] > MAX_BAD_HOUR_SHARE:
        why.append(f"coverage: {pc['bad_count_hours']} of {pc['count_hours']} window hours bad (> 5%)")
    if a3["halts"]:
        why.append(f"A3 halt: {a3['halts']}")
    if a3["unevaluated_two_runs"]:
        why.append(f"A3 core rule unevaluated on two consecutive runs: {a3['unevaluated_two_runs']}")
    if a3["last_slice_median_s"] < LAST_SLICE_MIN_S:
        why.append(f"BOOST last-slice median {a3['last_slice_median_s']} s < 330 s")
    return why


def compute_look(inp: LookInputs, log: Callable[[str], None]) -> dict[str, Any]:
    """Everything after the lock. Prices only when no pre-outcome NOT_DECIDABLE condition holds."""
    pc = precount(inp.pools, inp.good_hours, inp.bad_hours)
    if inp.precount_file is not None and inp.precount_file != pc:
        raise Refused("P6: the recorded precount differs from the look's own recount (same hours, same files): refusing")
    pre = not_decidable_reasons(pc, inp.a3, inp.p7_pass)
    report: dict[str, Any] = {"schema": SCHEMA, "experiment": EXP_ID, "look": 1, "precount": pc, "a3": inp.a3, "e1": {"n": inp.e1_n, "rbar": inp.rbar},
                              "disclosure": DISCLOSURE}
    if pre:
        report.update(verdict="NOT_DECIDABLE", reasons=pre, outcomes_computed=False)
        return report
    cells_rows: dict[str, list[dict[str, Any]]] = {c: [] for c in CELLS}
    extra: dict[str, list[dict[str, Any]]] = {"R_rule_1p3": [], "D_literal_v0": [], "D_no_correction": [], "D_only_a": [], "D_only_b": []}
    for p in inp.pools:
        s = structure(p, inp.good_hours)
        if s.status != "trigger":
            continue
        base = dict(mint=p.mint, date=s.date)
        for c, cell in CELLS.items():
            r = price_cell(p, s, cell, rbar=inp.rbar)
            if r is not None:
                cells_rows[c].append({**base, **r})
        for name, kw in (("R_rule_1p3", dict(cell=REPORT_LEGS["R_rule_1p3"])), ("D_literal_v0", dict(cell=CELLS["D"], literal_v0=True)),
                         ("D_no_correction", dict(cell=CELLS["D"], corr="none")), ("D_only_a", dict(cell=CELLS["D"], corr="a")),
                         ("D_only_b", dict(cell=CELLS["D"], corr="b"))):
            r = price_cell(p, s, kw.pop("cell"), rbar=inp.rbar, **kw)
            if r is not None:
                extra[name].append({**base, **r})
    for rows in list(cells_rows.values()) + list(extra.values()):
        apply_fail(rows)
    stats = {c: {leg: leg_stats([r[f"pnl_{leg}_lamports"] for r in rows], [r["date"] for r in rows]) for leg in ("flat", "press")}
             for c, rows in cells_rows.items()}
    v = verdict(stats, LOOK1["alpha"])
    mv = missing_v_check(cells_rows)
    if mv:
        v = {**v, "verdict": "NOT_DECIDABLE", "reasons": mv}
    rep_only: dict[str, Any] = {}
    for c, rows in list(cells_rows.items()) + list(extra.items()):
        rep_only[c] = {}
        for leg in ("flat", "press"):
            x = np.asarray([r[f"pnl_{leg}_lamports"] for r in rows], float)
            if len(x):
                bm = boot_means(x / 1e9, REPORT_P_DRAWS, BOOT_SEED)
                cl = bf.gate_stats(x, [r["date"] for r in rows], STAKE)
                rep_only[c][leg] = dict(trade_boot_p_le_0=float((bm <= 0).mean()), date_cluster_ci90_pct=[cl["ci5_pct"], cl["ci95_pct"]],
                                        n=int(len(x)), mean_sol=float(x.mean() / 1e9), median_sol=float(np.median(x) / 1e9))
        rep_only[c]["reverted"] = sum(r["reverted"] for r in rows)
        rep_only[c]["v_missing"] = sum(r["v_missing"] for r in rows)
    report.update(verdict=v["verdict"], decision=v, cells=stats, report_only=rep_only, rows_md5=rows_md5(cells_rows), outcomes_computed=True,
                  n_rows={c: len(r) for c, r in cells_rows.items()})
    report["_rows"] = {**cells_rows, **extra}
    return report


ROW_COLUMNS = ("cell", "date", "mint", "pnl_lamports", "pnl_flat_lamports", "pnl_press_lamports", "p_fail", "reverted", "v_missing",
               "correction_lamports", "rent_lamports", "gross", "landing_slot", "exit_landing_slot", "ssb", "nb_lamports", "trig_t_s",
               "q_trig_sol", "v_entry_minus_v0", "v_exit_minus_v0", "sps")


def rows_csv(rows_by_cell: Mapping[str, list[dict[str, Any]]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ROW_COLUMNS)
    for c in sorted(rows_by_cell):
        for r in rows_by_cell[c]:
            w.writerow([c] + [repr(r.get(k)) if isinstance(r.get(k), float) else r.get(k) for k in ROW_COLUMNS[1:]])
    return buf.getvalue().encode()


def run_look(lay: Layout, inp_fn: Callable[[], LookInputs], *, ident: Mapping[str, Any], now: datetime, log: Callable[[str], None]) -> int:
    """Lock, ledger `started`, compute, stderr report, files, ledger `completed` (or `aborted`)."""
    if now >= datetime.strptime(LOOK1["deadline"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc):
        raise Refused(f"Look 1 deadline {LOOK1['deadline']} has passed: Look 1 is NOT_DECIDABLE (section 8.1)")
    ledger_allows(ledger_events(lay.ledger), lay.look)
    if lay.out.exists() and any(lay.out.iterdir()):
        raise Refused(f"{lay.out} is not empty")
    inp = inp_fn()  # conditions and inputs; still no outcome
    take_lock(lay.lock)
    base = {"experiment": EXP_ID, "look": lay.look, **ident}
    append_event(lay.ledger, {**base, "event": "started", "utc": now.isoformat()})
    try:
        report = compute_look(inp, log)
    except BaseException as e:
        append_event(lay.ledger, {**base, "event": "aborted", "utc": datetime.now(timezone.utc).isoformat(), "error": type(e).__name__})
        raise
    rows = report.pop("_rows", {})
    text = json.dumps(report, indent=1, sort_keys=True, default=float)
    log(f"EXP-024 Look 1 VERDICT: {report['verdict']}")
    log(text)
    write_new(lay.out / "report.json", (text + "\n").encode())
    if rows:
        write_new(lay.out / "rows.csv", rows_csv(rows))
    v = report["verdict"]
    if v == "NOT_DECIDABLE" and report.get("a3", {}).get("halts"):
        v = "NOT_DECIDABLE_HALT"
    append_event(lay.ledger, {**base, "event": "completed", "utc": datetime.now(timezone.utc).isoformat(), "verdict": v,
                              "report_sha256": hashlib.sha256((text + "\n").encode()).hexdigest(), "rows_md5": report.get("rows_md5")})
    return 0


# ---- CLI ------------------------------------------------------------------------------------------------------------------
def _good_bad(walk: Path) -> tuple[frozenset[str], list[str]]:
    st = hour_states(walk, READ_HOURS)
    if any(v in ("not_walked", "not_sealed") for v in st.values()):
        raise Refused("condition (b): some Look 1 read hour is not walked or not sealed yet")
    return frozenset(h for h, v in st.items() if v == "ok"), [h for h in COUNT_HOURS if st[h] != "ok"]


def production_inputs(lay: Layout) -> LookInputs:
    e1_n, rbar = read_e1(lay.e1)
    a3 = a3_conditions(A3_DAILY, lo=LOOK1["count_lo"], run_date=LOOK1["a3_run_date"])
    if not lay.p7.is_file():
        raise Refused(f"P7 not done: {lay.p7} is missing (condition (e))")
    p7 = json.loads(lay.p7.read_text(encoding="utf-8")).get("pass") is True
    if not lay.precount.is_file():
        raise Refused(f"P6 not done: {lay.precount} is missing (condition (e))")
    good, bad = _good_bad(FORWARD_1002)
    pools = load_pools(lay, load_vsources(lay), load_classes(lay.classes))
    return LookInputs(pools, good, bad, rbar, e1_n, a3, p7, json.loads(lay.precount.read_text(encoding="utf-8")))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pins")
    for c in ("classify", "precount", "look"):
        sub.add_parser(c).add_argument("--look", type=int, required=True)
    a = ap.parse_args(argv)
    log = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731
    try:
        if a.cmd == "pins":
            text = (REPO / PREREG).read_text(encoding="utf-8")
            check_count_start(text)
            print(json.dumps({k: f"{p}@{git_blob(REPO / p)}" for k, p in sorted(P3_PIN_PATHS.items())}, indent=1))
            parse_p3_pins(text)
            return 0
        refuse_look(a.look)
        from tools.forward_v_join import final_marker

        final_marker(FINAL_LEDGER)
        lay = Layout.for_look(ROOT, a.look)
        ident = integrity()
        if a.cmd == "classify":
            raise Refused("classify: RPC wiring is the next step (HANDOFF); use synthetic_class.classify_pool per pool into a new classes file")
        if a.cmd == "precount":
            good, bad = _good_bad(FORWARD_1002)
            pc = precount(load_pools(lay, load_vsources(lay), load_classes(lay.classes)), good, bad)
            text = json.dumps(pc, indent=1, sort_keys=True)
            print(text)
            write_new(lay.precount, (text + "\n").encode())
            return 0
        return run_look(lay, lambda: production_inputs(lay), ident=ident, now=datetime.now(timezone.utc), log=log)
    except Refused as e:
        log(f"REFUSED: {e}")
        return 2
    except Exception as e:  # noqa: BLE001 - from forward_v_join (its own Refused)
        if type(e).__name__ == "Refused":
            log(f"REFUSED: {e}")
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
