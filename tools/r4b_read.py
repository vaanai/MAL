#!/usr/bin/env python3
"""r4-b walk-2 SEALED READ TOOL (EXP-027, DEC-030 slot 5, push-family book 1; PLAN r4b-readtool section 4-5, builder task 7).

One read, no interim looks. This file is part of the pre-registration: its sha256 is pinned in the EXP (EXP027_SHA256_R4B_READ). NOTHING HERE IS EVIDENCE OF EDGE. A PASS
would be "forward simulated paper, not money made".

Order of events (the order is part of the contract; tests pin it)
  1. PREFLIGHT, before any outcome row is opened. Every reason is collected; on any reason: one REFUSED line on stderr, exit 2, NOTHING written.
       EXP pins (each EXP027_* line matches exactly once, file clean vs HEAD, route not PENDING, W0 / k / stop rule), code sha256 of every r4b module, model md5/sha256,
       E-a..E-d md5 records, decoder blobs (walk 2's job ref), E1 record (sha256, n >= 20), input views and the data/tries.jsonl entry, the mint-account fetch file (sha256),
       the fee-arm record (sha256 if pinned), the calendar (E = W0 + 480 h has passed), every hour of the allowlist [H0, E + 3h) sealed / strict-verified / sha256- and
       row-count-matched, counts.jsonl covering every counted hour with equal hashes, the count job's aggregate precount refusals (PDA match < 95 %, < 100 marks with >= 10 live,
       a date with graduations and 0 decisions), mixed null tx_index, unmeasurable SPH, no earlier started / completed / aborted line, no lock file.
  2. LOCK: O_EXCL READ.lock, then a `started` line in READS.jsonl. From here a failure writes an `aborted` line (the read is spent: a second run refuses).
  3. BUILD the intermediates with r4b_layout (event V; null V on a member print and v0-missing share > 2 % refuse), compute forward-mode features
     (grads_rule asof_pda_first_print, forward_order), drop decision rows with T < block_time(s0) + 600 s (count reported), score with the 5 boosters (float64),
     run the xs10 book from W0 marks to the last entry mark <= E - 1h, sell everything at mark E, price the cost stack (section 5), build the 4 cells, gate (section 6).
  4. VERDICT to stderr, THEN the result file (O_EXCL), THEN the `completed` line. The verdict is never produced after a file write.

Universe exits (f): the book is hourly; UNIVERSE_EXITS_AT_MARKS_ONLY = True. A token that leaves the universe between marks (dead, 48 h passed) is not ranked at the next mark and is
sold there by xs_book's rank rule (or its gap rule), as in harness.book_xs. Unpriced exit (e): a held token whose exit reserve state is <= 0 or missing is a total loss of the stake plus
the buy send plus RENT_STRANDED (2,039,280; no sell send), exactly as a strand-class mint (QP ruling iii), marked stranded, and stays in the book. Forward features (a): mint_features(grads_rule="asof_pda_first_print",
mint_filters="asof"); the training defaults are frozen. Allowlist (g): [H0 = 2026-10-16T03, E + 1h) for decisions plus the 2 extra trade hours (E + 3h exclusive, as r4b_count's trade_end).

Deciding stack (EXP section 5): 505,000-lamport sends (160,000 and the buy-lag shift only if the pinned fee-arm record says Test A and Test B both PASS), rent 0 per filled trip,
auto-stranded (simulated proceeds below one send fee), strand-class mints from the pinned mint-account fetch file (a missing or failed fetch is strand-class), VERIFY haircut
or the larger E1 penalty max(0, -5 * rbar) per filled trip, flat 15 % and pressure legs (intercept refit per cell, mean p 0.289), latencies 1.3 s and 1.9 s,
k = np.round(lat / slot_s [+ delta]), slot_s per UTC date = 3600 / median hourly SPH. Gate: all 4 cells n >= 100, >= 5 dates with a positive majority, trade CI90 lo > 0
(1,000 draws, seed 1, order (T0, mid)), ex-top-3 > 0, ex-best-24h-block > 0, ex-best-UTC-date > 0, and the t over per-attempt 24 h block means: the larger p of the
4 cells <= 0.025 / k. NOT_DECIDABLE: fewer than 20 blocks with attempts or 125 attempts, fewer than 100 trades, decision keys not a subset of the count job's live keys
(or its live md5 differs), a pressure-leg gap. There is no CLI override of any pinned value; the CLI takes only the PATHS of the pinned inputs.

Needs duckdb (/data/mal/audit-1008/venv), lightgbm and solders (/data/mal/venv). Seals: this tool opens only allowlisted hours of the walk-2 directory it is pointed at.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

if __package__ in (None, ""):  # `python tools/r4b_read.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import exp012_forward as ef  # noqa: E402
from tools import r4b_book as bk  # noqa: E402
from tools import r4b_count as rc  # noqa: E402
from tools import r4b_features as rf  # noqa: E402
from tools import r4b_frozen as FZ  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools import r4b_score as rs  # noqa: E402
from tools.latency_curve import _hour_file  # noqa: E402

SCHEMA = "r4b_read_v1"
ROOT = Path(__file__).resolve().parents[1]
EXP_FILE = ROOT / "EXP" / "EXP-027-r4b-walk2-prereg.md"
DEFAULT_OUT = Path("/data/mal/r4b")
READS = "READS.jsonl"
LOCK = "READ.lock"
RESULT = "read-result.json"
TRIES = ROOT / "data" / "tries.jsonl"

# ---- pins (changing one changes the registered read) -------------------------------------------------------------------------------------
STOP_HOURS = 480  # E = W0 + 480 h
MIN_BLOCKS = 20  # N: 24 h blocks from W0 holding an attempt
MIN_ATTEMPTS = 125  # A
EXTRA_TRADE_HOURS = 2  # hours after E's exit hour that are sealed and read (allowlist end = E + 3h)
EXIT_HORIZON_H = 1
H0 = "2026-10-16T03"  # the count job's creates start (tools/r4b_count.H0); 10-16T01/T02 are never opened by the read
UNIVERSE_EXITS_AT_MARKS_ONLY = True  # (f) the book is hourly: a token that leaves the universe between marks is sold at the NEXT mark by the rank / gap rule (xs_book), never in between
EARLIEST_W0 = "2026-10-18T01"
BLOCK_S = 86400
MARK_S = 3600
D3_DROP_S = 600  # decision rows with T < block_time(s0) + 600 s are dropped
GATE_N = 100
GATE_DATES = 5
ALPHA_FAMILY = 0.025
SEND_FEE_160K = 160_000
LP_TOLS = (1e-6, 1e-2)
CLOSE_BLOCKING_EXT = frozenset({"transferFeeConfig", "transferHook", "confidentialTransferMint", "nonTransferable", "permanentDelegate", "mintCloseAuthority",
                                "defaultAccountState", "pausableConfig"})  # report-only list is in the result; pinned here, QP to confirm
LATS = FZ.LATENCIES_S
LAT_REPORT_LONG = 4.0
CODE_MODULES = ("r4b_frozen", "r4b_score", "r4b_book", "r4b_features", "r4b_layout", "r4b_count", "r4b_read")
ARTIFACT_NAMES = tuple(FZ.ARTIFACT_MD5)

REQUIRED_KEYS = (
    "EXP027_BOOK_ID", "EXP027_FAMILY", "EXP027_ROUTE", "EXP027_COUNT_START", "EXP027_LAST_W0", "EXP027_STOP_RULE", "EXP027_EXIT_HORIZON_H", "EXP027_CAP_END",
    "EXP027_DEADLINE", "EXP027_ALPHA", "EXP027_DECISION_P", "EXP027_SEND_FEE_LAMPORTS", "EXP027_RENT_DECIDING",
    # lines the manager adds at registration / before the read (the draft has them only as TO FILL prose)
    "EXP027_W0", "EXP027_K", "EXP027_DECODER_REF", "EXP027_TIERS_SHA256", "EXP027_E1_RECORD", "EXP027_INPUT_VIEWS", "EXP027_TRIES_ENTRY", "EXP027_MINT_ACCOUNTS",
    "EXP027_FEE_ARM", "EXP027_E_A_MD5", "EXP027_E_B_MD5", "EXP027_E_C_MD5", "EXP027_E_D_MD5",
) + tuple(f"EXP027_SHA256_{m.upper()}" for m in CODE_MODULES)
HEX64, HEX32 = re.compile(r"^[0-9a-f]{64}$"), re.compile(r"^[0-9a-f]{32}$")


class CountBuildDivergence(Exception):
    """The read's own build contradicts counts.jsonl (a structure check the count job passed fails on the same hours): NOT_DECIDABLE, a real anomaly."""

    def __init__(self, reasons: Sequence[str]):
        self.reasons = list(reasons)
        super().__init__("count_build_divergence: " + "; ".join(self.reasons))


class ReadRefused(Exception):
    def __init__(self, reasons: Sequence[str] | str):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("r4b_read refused: " + "; ".join(self.reasons))


# ---- small helpers -------------------------------------------------------------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p: Path) -> str:
    return FZ.file_sha256(str(p))


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def fee_tiers_sha256() -> str:
    return hashlib.sha256(json.dumps([list(t) for t in FZ.FEE_TIERS], separators=(",", ":")).encode()).hexdigest()


def split_pair(val: str) -> tuple[str, str]:
    """'path@sha256' -> (path, sha256)."""
    p, _, h = val.rpartition("@")
    return p, h


# ---- EXP pins ---------------------------------------------------------------------------------------------------------------------------------
def exp_lines(text: str) -> tuple[dict[str, str], list[str]]:
    """Each EXP027_* key must match `^KEY: value$` exactly once. Returns (values, problems)."""
    out, probs = {}, []
    for k in REQUIRED_KEYS:
        m = re.findall(rf"^{k}: (.*\S)\s*$", text, flags=re.M)
        if len(m) != 1:
            probs.append(f"EXP pin {k}: matches {len(m)} times (need exactly 1)")
        else:
            out[k] = m[0]
    return out, probs


def exp_clean_against_head(exp_file: Path = EXP_FILE) -> list[str]:
    r = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", str(exp_file)], stdin=subprocess.DEVNULL, timeout=60)
    return [] if r.returncode == 0 else [f"{exp_file.name} differs from HEAD"]


def parse_hour(s: str) -> datetime | None:
    try:
        return ef.hour_dt(s)
    except ValueError:
        return None


def pin_problems(v: Mapping[str, str], code_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """Semantic checks of the pinned values. Returns (problems, parsed)."""
    probs: list[str] = []
    P: dict[str, Any] = {}
    if v.get("EXP027_ROUTE") not in ("R1", "R2"):
        probs.append(f"EXP027_ROUTE is {v.get('EXP027_ROUTE')!r} (PENDING or unknown): the route is not decided")
    if v.get("EXP027_BOOK_ID") != "r4-b":
        probs.append("EXP027_BOOK_ID is not r4-b")
    if v.get("EXP027_EXIT_HORIZON_H") != str(EXIT_HORIZON_H):
        probs.append(f"EXP027_EXIT_HORIZON_H is {v.get('EXP027_EXIT_HORIZON_H')!r}, the tool pins {EXIT_HORIZON_H}")
    if f"{STOP_HOURS} h" not in v.get("EXP027_STOP_RULE", "") or str(MIN_BLOCKS) not in v.get("EXP027_STOP_RULE", "") or str(MIN_ATTEMPTS) not in v.get("EXP027_STOP_RULE", ""):
        probs.append(f"EXP027_STOP_RULE does not state E = W0 + {STOP_HOURS} h with N = {MIN_BLOCKS} and A = {MIN_ATTEMPTS}")
    if not v.get("EXP027_ALPHA", "").startswith(f"{ALPHA_FAMILY}/k"):
        probs.append(f"EXP027_ALPHA is not {ALPHA_FAMILY}/k")
    if "505000" not in v.get("EXP027_SEND_FEE_LAMPORTS", "") or "160000" not in v.get("EXP027_SEND_FEE_LAMPORTS", ""):
        probs.append("EXP027_SEND_FEE_LAMPORTS does not state 505000 / 160000")
    if not v.get("EXP027_RENT_DECIDING", "").startswith("0 "):
        probs.append("EXP027_RENT_DECIDING is not 0 per filled trip (rent deciding is not what this tool prices)")
    w0 = parse_hour(v.get("EXP027_W0", ""))
    last = parse_hour(v.get("EXP027_LAST_W0", ""))
    if w0 is None:
        probs.append(f"EXP027_W0 {v.get('EXP027_W0')!r} is not YYYY-MM-DDTHH")
    else:
        P["w0"] = w0
        if w0 < ef.hour_dt(EARLIEST_W0):
            probs.append(f"W0 {v['EXP027_W0']} is before {EARLIEST_W0}")
        if last is not None and w0 > last:
            probs.append(f"W0 {v['EXP027_W0']} is after EXP027_LAST_W0 {v['EXP027_LAST_W0']}")
    try:
        P["k"] = int(v.get("EXP027_K", ""))
        if P["k"] < 1:
            raise ValueError
    except ValueError:
        probs.append(f"EXP027_K {v.get('EXP027_K')!r} is not an integer >= 1")
    # code sha256, E-md5, tiers
    shas = {}
    for m in CODE_MODULES:
        want = v.get(f"EXP027_SHA256_{m.upper()}", "")
        got = sha256_file(code_dir / f"{m}.py") if (code_dir / f"{m}.py").exists() else None
        shas[m] = got
        if not HEX64.match(want):
            probs.append(f"EXP027_SHA256_{m.upper()} is not a sha256 (TO FILL?)")
        elif got != want:
            probs.append(f"code sha256 of {m}.py is {got}, the EXP pins {want}")
    P["code_sha256"] = shas
    for e in ("A", "B", "C", "D"):
        if not HEX32.match(v.get(f"EXP027_E_{e}_MD5", "")):
            probs.append(f"E-{e.lower()} md5 record is missing or not an md5 (EXP027_E_{e}_MD5)")
    if v.get("EXP027_TIERS_SHA256") != fee_tiers_sha256():
        probs.append(f"FEE_TIERS sha256 {fee_tiers_sha256()} != EXP027_TIERS_SHA256 {v.get('EXP027_TIERS_SHA256')}")
    return probs, P


def pinned_file(val: str, label: str, probs: list[str]) -> Path | None:
    p, h = split_pair(val)
    if not p or not HEX64.match(h):
        probs.append(f"{label}: {val!r} is not 'path@sha256'")
        return None
    path = Path(p)
    if not path.exists():
        probs.append(f"{label}: {p} does not exist")
        return None
    if sha256_file(path) != h:
        probs.append(f"{label}: sha256 of {p} is {sha256_file(path)}, pinned {h}")
        return None
    return path


# ---- records: E1, input views, tries, mint accounts, fee arm -------------------------------------------------------------------------------
E1_MIN_N = 20


def load_e1(v: Mapping[str, str], probs: list[str]) -> dict[str, Any] | None:
    p = pinned_file(v["EXP027_E1_RECORD"], "E1 record", probs)
    if p is None:
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    n, rbar = d.get("n"), d.get("pnl_gap_lamports_live_minus_sim_mean")
    if not isinstance(n, int) or n < E1_MIN_N:
        probs.append(f"E1 record n is {n!r} (need an integer >= {E1_MIN_N})")
    if not isinstance(rbar, (int, float)) or isinstance(rbar, bool) or not math.isfinite(rbar):
        probs.append("E1 record has no numeric pnl_gap_lamports_live_minus_sim_mean")
        return None
    scale = float(d.get("scale_to_stake", 5.0))  # 0.05 SOL measured, linear to 0.25 SOL (x5), QP-4
    return {"n": n, "rbar_lamports": float(rbar), "scale": scale, "penalty_lamports": max(0.0, -scale * float(rbar)), "path": str(p), "sha256": split_pair(v["EXP027_E1_RECORD"])[1]}


def check_inputs(v: Mapping[str, str], tries_path: Path, probs: list[str]) -> dict[str, str]:
    out = {}
    for item in [x for x in v["EXP027_INPUT_VIEWS"].split(";") if x.strip()]:
        p = pinned_file(item.strip(), "input view", probs)
        if p is not None:
            out[str(p)] = split_pair(item.strip())[1]
    if not out:
        probs.append("EXP027_INPUT_VIEWS lists no input view")
    key = v["EXP027_TRIES_ENTRY"]
    ok = False
    if tries_path.exists():
        for ln in tries_path.read_text(encoding="utf-8").splitlines():
            if ln.strip() and key in ln:
                ok = True
    if not ok:
        probs.append(f"{tries_path} has no entry containing {key!r}")
    return out


def load_mint_accounts(v: Mapping[str, str], probs: list[str]) -> dict[str, bool] | None:
    """{mint: strand_class}. A mint absent from the file, a failed fetch, a freeze authority or a close-blocking Token-2022 extension is strand-class."""
    p = pinned_file(v["EXP027_MINT_ACCOUNTS"], "mint-account file", probs)
    if p is None:
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for mint, r in (d.get("mints") or {}).items():
        ext = set(r.get("token2022_extensions") or [])
        out[mint] = (not r.get("fetch_ok", False)) or bool(r.get("freeze_authority")) or bool(ext & CLOSE_BLOCKING_EXT)
    return out


def fee_arm_decision(v: Mapping[str, str], started: datetime, probs: list[str]) -> dict[str, Any]:
    """EXP section 5.3. 160k + lag shift only if the pinned record says Test A and Test B both PASS; anything else is 505k."""
    base = {"send": FZ.SEND_FEE_DECIDING, "delta_slots": 0.0, "source": "505k"}
    raw = v["EXP027_FEE_ARM"].strip()
    if raw.upper() == "NONE":
        return {**base, "reason": "no fee-arm record pinned"}
    p, h = split_pair(raw)
    path = Path(p)
    if not path.exists():
        return {**base, "reason": f"fee-arm record {p} missing: fallback 505k"}
    if not HEX64.match(h) or sha256_file(path) != h:
        probs.append(f"fee-arm record {p}: sha256 does not equal the pinned value")
        return base
    d = json.loads(path.read_text(encoding="utf-8"))
    at = d.get("verdict_at")
    try:
        at_dt = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return {**base, "reason": "fee-arm record has no verdict_at: fallback 505k"}
    if at_dt >= started:
        return {**base, "reason": "fee-arm verdict is dated at or after the read start: fallback 505k"}
    a, b = d.get("test_a_pass"), d.get("test_b_pass")
    ub = d.get("test_b_upper95_slots")
    if a is True and b is True and isinstance(ub, (int, float)) and not isinstance(ub, bool) and math.isfinite(ub):
        return {"send": SEND_FEE_160K, "delta_slots": max(0.0, float(ub)), "source": "160k", "record": p, "sha256": h, "test_b_upper95_slots": float(ub)}
    return {**base, "reason": f"fee-arm Test A {a!r}, Test B {b!r}: not both PASS: 505k"}


# ---- slot time ----------------------------------------------------------------------------------------------------------------------------
def slot_s_by_date(sph_by_hour: Mapping[str, float]) -> dict[str, float]:
    """slot_s(date) = 3600 / median over the date's hours of SPH_h (QP-4 section 2)."""
    by: dict[str, list[float]] = {}
    for h, s in sph_by_hour.items():
        by.setdefault(h[:10], []).append(float(s))
    return {d: 3600.0 / float(np.median(v)) for d, v in by.items()}


def k_for(lat: float, slot_s: np.ndarray, delta: float = 0.0) -> np.ndarray:
    """Entry slots k = np.round(lat / slot_s + delta) (delta: the fee-arm buy-lag shift, 0 unless the 160k leg applies)."""
    return np.round(lat / np.asarray(slot_s, float) + float(delta)).astype(np.int64)


def entry_states_shifted(sl, QX, BX, ib, so, sd, s_dec, lat: float, delta: float, nearby_s: float = FZ.NEARBY_WINDOW_S):
    """The entry block of r4b_book.mint_states with k = round(lat / s + delta); at delta = 0 it equals mint_states (a test pins it)."""
    sl = np.asarray(sl)
    k = k_for(lat, s_dec, delta)
    x = np.asarray(sd) + k
    je = np.searchsorted(sl, x + 1, "left")
    ix = np.searchsorted(sl, x, "left")
    lo2 = np.searchsorted(sl, x - np.round(nearby_s / np.asarray(s_dec, float)).astype(np.int64), "left")
    cib = np.concatenate([[0], np.cumsum(ib)])
    cbv = np.concatenate([[0], np.cumsum(np.where(ib, so, 0.0))])
    return QX[je], BX[je], (cib[je] - cib[ix]).astype(float), cbv[je] - cbv[lo2]


# ---- ledger and lock ----------------------------------------------------------------------------------------------------------------------
def reads_events(out_dir: Path) -> list[dict[str, Any]]:
    p = out_dir / READS
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def ledger_event(out_dir: Path, event: str, **kw: Any) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / READS).open("a", encoding="utf-8") as f:
        f.write(stable_json({"event": event, "at": iso(now_utc()), **kw}) + "\n")
        f.flush()
        os.fsync(f.fileno())


def take_lock(out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(out_dir / LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
    except FileExistsError as exc:
        raise ReadRefused(f"{out_dir / LOCK} exists: the read was already started (no second run, no resume)") from exc
    token = os.urandom(8).hex()
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"token": token, "at": iso(now_utc())}) + "\n")
    return token


# ---- config and deps ----------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Paths:
    walk_dir: Path
    out_dir: Path = DEFAULT_OUT
    counts_dir: Path = DEFAULT_OUT
    exp_file: Path = EXP_FILE
    tries: Path = TRIES
    models_dir: Path | None = None


@dataclass
class Deps:
    """Everything the tests replace. The defaults are the production behaviour."""

    now_fn: Callable[[], datetime] = now_utc
    clean_fn: Callable[[Path], list[str]] = exp_clean_against_head
    decoder_fn: Callable[[str], list[str]] = lambda ref: rl.decoder_pin_problems(ref)
    problems_fn: rl.ProblemsFn | None = None
    verify: Mapping[str, Mapping[str, Any]] | None = None
    sph_fn: rc.SphFn | None = None
    canonical_fn: rl.CanonFn | None = None
    boosters_fn: Callable[[str | None], list] = rs.load_boosters
    models_check_fn: Callable[[str | None], list[str]] | None = None
    head_fn: Callable[[], str] = lambda: subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout.strip()
    stderr: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True)
    memory_limit: str = "6GB"
    threads: int = 4
    code_dir: Path = Path(__file__).resolve().parent
    keep_scratch: bool = False


def default_models_check(models_dir: str | None) -> list[str]:
    probs = []
    for n in ARTIFACT_NAMES:
        try:
            rs.check_artifact(n, models_dir)
        except rs.PinMismatch as exc:
            probs.append(str(exc))
    try:
        rs.check_features(models_dir)
    except rs.PinMismatch as exc:
        probs.append(str(exc))
    return probs


@dataclass
class Plan:
    """What preflight establishes; the read uses only this."""

    v: dict[str, str]
    w0: datetime
    e: datetime
    k: int
    hours: list[str]            # the allowlist [H0, E + 3h)
    count_cfg: rc.Config
    counts: list[dict[str, Any]]
    e1: dict[str, Any]
    strand: dict[str, bool]
    inputs: dict[str, str]
    sph: dict[str, float] = field(default_factory=dict)


# ---- preflight (nothing is written; no outcome row is opened) ---------------------------------------------------------------------------
def preflight(paths: Paths, deps: Deps, started: datetime) -> tuple[Plan | None, dict[str, Any], list[str]]:
    """All refusals that can be decided before any trade row is parsed. Returns (plan, fee_arm, reasons)."""
    probs: list[str] = []
    out_dir = Path(paths.out_dir)
    if (out_dir / LOCK).exists():
        probs.append(f"{out_dir / LOCK} exists: the read was already started (no second run)")
    ev = reads_events(out_dir)
    if ev:
        probs.append(f"{out_dir / READS} already holds {[e.get('event') for e in ev]}: no second run")
    if not paths.exp_file.exists():
        return None, {}, probs + [f"{paths.exp_file} does not exist"]
    text = paths.exp_file.read_text(encoding="utf-8")
    v, p1 = exp_lines(text)
    probs += p1
    probs += deps.clean_fn(paths.exp_file)
    if len(v) != len(REQUIRED_KEYS):
        return None, {}, probs
    p2, P = pin_problems(v, deps.code_dir)
    probs += p2
    probs += (deps.models_check_fn or default_models_check)(str(paths.models_dir) if paths.models_dir else None)
    probs += deps.decoder_fn(v["EXP027_DECODER_REF"])
    e1 = load_e1(v, probs)
    inputs = check_inputs(v, paths.tries, probs)
    strand = load_mint_accounts(v, probs)
    fee = fee_arm_decision(v, started, probs)
    if "w0" not in P:
        return None, fee, probs
    w0 = P["w0"]
    e = w0 + timedelta(hours=STOP_HOURS)
    if deps.now_fn() < e:
        probs.append(f"the calendar stop E = {ef.hour_key(e)} has not passed")
    end = e + timedelta(hours=EXIT_HORIZON_H + EXTRA_TRADE_HOURS)
    hours = rl.hour_range(H0, ef.hour_key(end))
    walk = Path(paths.walk_dir)
    try:
        rl.check_hours(walk, hours, deps.problems_fn, deps.verify)
        table = deps.verify if deps.verify is not None else ef.verified_hours(walk, strict_bad_lines=True)
    except rl.LayoutRefused as exc:
        probs += exc.reasons
        table = {}
    counts, count_cfg = [], None
    cdir = Path(paths.counts_dir)
    cfgp = cdir / rc.CONFIG
    if not cfgp.exists():
        probs.append(f"{cfgp} is missing: the count job has not run")
    else:
        pin = json.loads(cfgp.read_text(encoding="utf-8"))
        if pin.get("w0") != ef.hour_key(w0):
            probs.append(f"count job W0 {pin.get('w0')} != EXP W0 {ef.hour_key(w0)}")
        for m in ("r4b_count", "r4b_layout"):
            if (pin.get("code_sha256") or {}).get(f"{m}.py") != P["code_sha256"].get(m):
                probs.append(f"count job ran {m}.py at {(pin.get('code_sha256') or {}).get(f'{m}.py')}, the read pins {P['code_sha256'].get(m)}")
        want_pin = {"w0": ef.hour_key(w0), "stop_mode": rc.STOP_MODE, "h0": H0, "a_attempts": MIN_ATTEMPTS, "calendar_stop": ef.hour_key(e), "exit_horizon_h": EXIT_HORIZON_H}
        bad_pin = [f"{k}: count config {pin.get(k)!r} != EXP/read {v_!r}" for k, v_ in want_pin.items() if pin.get(k) != v_]
        if int(pin.get("n_blocks", -1)) * rc.BLOCK_S != STOP_HOURS * 3600:
            bad_pin.append(f"n_blocks: count config {pin.get('n_blocks')!r} blocks != the {STOP_HOURS} h stop")
        if bad_pin:
            probs.append("count config.json pins differ from the EXP's: " + "; ".join(bad_pin))
        count_cfg = rc.Config(Path(paths.walk_dir), cdir, ef.hour_key(w0), n_blocks=int(pin.get("n_blocks", rc.N_BLOCKS)), a_attempts=int(pin.get("a_attempts", rc.A_ATTEMPTS)), h0=H0)
        counts = rc.read_counts(cdir)
        rpath = cdir / rc.REFUSED
        if rpath.exists() and rpath.read_text(encoding="utf-8").strip():
            first = json.loads(rpath.read_text(encoding="utf-8").splitlines()[0])
            probs.append(f"the count job recorded refusal(s) in {rpath} (first: {'; '.join(first.get('reasons', []))[:300]}); structure checks (v0-missing, null-V member, mixed null tx_index, no-create) did not all pass")
        by_hour = {ln["hour"]: ln for ln in counts}
        need = rl.hour_range(H0, ef.hour_key(e + timedelta(hours=1)))  # [H0, E] inclusive
        miss = [h for h in need if h not in by_hour]
        if miss:
            probs.append(f"counts.jsonl lacks {len(miss)} hour(s) of [H0, E], first {miss[:3]}")
        for h in need:
            ln = by_hour.get(h)
            if ln is None:
                continue
            want_x = 0 if h == ef.hour_key(e) else (1 if h == ef.hour_key(e - timedelta(hours=1)) else rc.EXTRA_HOURS)
            if ln.get("extra_hours_loaded") != want_x:
                probs.append(f"hour {h}: extra_hours_loaded is {ln.get('extra_hours_loaded')!r}, the rule needs {want_x}")
        for h in need:
            ln = by_hour.get(h)
            if ln is None or h not in table:
                continue
            if any(ln["sha256"].get(s) != table[h]["sha256"].get(s) for s in rl.SUBS):
                probs.append(f"hour {h}: counts.jsonl sha256 differs from verify.jsonl")
        if not miss:
            probs += rc.aggregate_problems([by_hour[h] for h in need], count_cfg, int(e.timestamp()))
            mixed = sum(by_hour[h].get("mixed_null_txi_slots") or 0 for h in need)
            if mixed:
                probs.append(f"{mixed} (mint, slot) pair(s) mixing null and non-null tx_index in the count lines")
    sph: dict[str, float] = {}
    sph_fn = deps.sph_fn or rc.default_sph
    by_hour = {ln["hour"]: ln for ln in counts}
    for h in hours:
        if h in by_hour and by_hour[h].get("sph"):
            sph[h] = float(by_hour[h]["sph"])
            continue
        if h not in table:
            continue
        f = _hour_file(walk / "trades", "trades", h)
        s = sph_fn(f) if f is not None else None
        if s is None:
            probs.append(f"hour {h}: slots-per-hour is unmeasurable")
        else:
            sph[h] = float(s)
    if probs or e1 is None or strand is None or count_cfg is None:
        return None, fee, probs
    return Plan(v, w0, e, P["k"], hours, count_cfg, counts, e1, strand, inputs, sph), fee, probs


# ---- the computation -----------------------------------------------------------------------------------------------------------------------
def lp_discontinuities(isbuy, tok, b, mid) -> dict[str, int]:
    """Report-only (EXP 3.5): consecutive prints of one pool where the next print's pre-trade BASE reserve differs from the previous print's implied post-trade base reserve."""
    ib = np.asarray(isbuy, bool)
    tk = np.asarray(tok, float)
    bb = np.asarray(b, float)
    m = np.asarray(mid)
    if len(bb) < 2:
        return {"pairs": 0, **{f"gt_{t:g}": 0 for t in LP_TOLS}}
    post = np.where(ib, bb - tk, bb + tk)[:-1]
    same = m[1:] == m[:-1]
    rel = np.abs(bb[1:] - post) / np.maximum(np.abs(post), 1.0)
    out = {"pairs": int(same.sum())}
    for t in LP_TOLS:
        out[f"gt_{t:g}"] = int(((rel > t) & same).sum())
    return out


def live_keys(scratch: Path, marks: Sequence[tuple[int, int]], meta_mint: Mapping[int, str], mbt_by_mid: Mapping[int, int], con: Any) -> dict[int, dict[str, int]]:
    """The count job's live set per mark, recomputed with the same predicate as r4b_count.structure_hour_lines: T >= mbt + 600, T <= mbt + 48h, the latest bt of the
    canonical-pool prints with slot < sd lies in [T - 600, T). Returns {mark_t: {mint: prints before sd}}. mbt_by_mid holds only mints with mbt >= UNIVERSE_MIN_MBT."""
    out = {}
    con.sql(f"create or replace view utv as select mid, slot, bt from read_parquet('{scratch}/ut/*.parquet')")
    for t, sd in marks:
        rows = con.sql(f"""select mid, count(*) from utv where slot < {sd} group by mid having max(bt) >= {t - rc.LIVE_WINDOW_S} and max(bt) < {t}""").fetchall()
        s_ = {}
        for mid, n in rows:
            mb = mbt_by_mid.get(mid)
            if mb is not None and mb + rc.LIVE_LO_S <= t <= mb + rc.LIVE_HI_S:
                s_[meta_mint[mid]] = int(n)
        out[t] = s_
    return out


def gap_by_cause(live: Mapping[str, int], dec: set, d3: set, mint_mid: Mapping[str, int], t: int) -> dict[str, int]:
    """Live tokens at a mark that have no decision row, by cause: fewer than 3 prints before sd; dropped by the s0 + 600 s rule; no feature row (reserve state <= 0,
    asof filter, grid)."""
    out = {"lt3_prints": 0, "d3_drop": 0, "no_feature_row": 0}
    for m, n in live.items():
        if m in dec:
            continue
        if n < rc.MIN_PRINTS_DECISION:
            out["lt3_prints"] += 1
        elif (mint_mid[m], t) in d3:
            out["d3_drop"] += 1
        else:
            out["no_feature_row"] += 1
    return out


@dataclass
class Rows:
    mid: list = field(default_factory=list)
    T: list = field(default_factory=list)
    sd: list = field(default_factory=list)
    X: list = field(default_factory=list)
    sdec: list = field(default_factory=list)
    spot: list = field(default_factory=list)
    ent: dict = field(default_factory=dict)  # lat -> list of (Qe, Be, ssb, nearby) arrays
    marks: list = field(default_factory=list)  # (mid, t, Qx, Bx)


def ut_chunks(con: Any, scratch: Path, rows_per_chunk: int = 3_000_000):
    """Yield (mid_lo, mid_hi) ranges of ut with about rows_per_chunk rows."""
    cnt = con.sql(f"select mid, count(*) from read_parquet('{scratch}/ut/*.parquet') group by 1 order by 1").fetchall()
    lo, acc, last = None, 0, None
    for mid, n in cnt:
        if lo is None:
            lo = mid
        acc += n
        last = mid
        if acc >= rows_per_chunk:
            yield lo, mid
            lo, acc = None, 0
    if lo is not None:
        yield lo, last


def fnum(x: Any) -> float:
    return float("nan") if x is None or x is getattr(__import__("pandas"), "NA", object()) or (isinstance(x, float) and math.isnan(x)) else float(x)


def build_rows(scratch: Path, plan: Plan, fee: Mapping[str, Any], slot_s: Mapping[str, float], deps: Deps, universe_min_mbt: int) -> tuple[Rows, dict[str, Any], Any]:
    """Features -> hour-mark decision rows with entry states (per latency) and the mark table, mint by mint. Returns (Rows, report, duckdb con)."""
    import duckdb
    import pandas as pd

    work = scratch / "work"
    rl.prep_work(scratch, work, threads=deps.threads, memory_limit=deps.memory_limit)
    con = duckdb.connect()
    con.sql(f"SET memory_limit='{deps.memory_limit}'; SET threads={int(deps.threads)}; SET enable_progress_bar=false; SET preserve_insertion_order=true")
    ckb, cks = np.load(work / "clock_bt.npy"), np.load(work / "clock_slot.npy")
    mk = np.load(work / "mkt.npz")
    meta = pd.read_parquet(scratch / "meta.parquet")
    meta = meta[meta["mbt"] >= universe_min_mbt].reset_index(drop=True)
    mcn = pd.read_pickle(work / "mintconst.pkl").set_index("mid")
    prep = rf.build_prep(ckb, cks, mk["mnt"], {k: mk[k] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}, meta["mbt"].to_numpy(np.int64),
                         meta["s0"].to_numpy(np.float64))
    w0, e = int(plan.w0.timestamp()), int(plan.e.timestamp())
    last_entry = e - MARK_S
    R = Rows()
    R.ent = {lat: [] for lat in (*LATS, LAT_REPORT_LONG)}
    rep = {"d3_keys": set(), "d3_dropped": 0, "d3_kept_pre_filter": 0, "mints": int(len(meta)), "mints_with_rows": 0, "lp": {"pairs": 0, **{f"gt_{t:g}": 0 for t in LP_TOLS}}}
    members = set(meta["mid"].tolist())
    dates_s = lambda T: np.array([slot_s[d] for d in bk.utc_dates(T)])  # noqa: E731
    delta = float(fee["delta_slots"])
    for lo, hi in ut_chunks(con, scratch):
        U = con.sql(f"""select mid, slot, txi, evi, bt, isbuy, sol, tok, q, b, th, vq from read_parquet('{scratch}/ut/*.parquet', filename=true, file_row_number=true)
                        where mid >= {lo} and mid <= {hi} order by mid, slot, txi, evi, filename, file_row_number""").fetchnumpy()
        mids = np.asarray(U["mid"], np.int64)
        U = {k: (np.ma.filled(a.astype(float), np.nan) if np.ma.isMaskedArray(a) and k in ("bt", "q", "b", "tok", "sol", "vq") else a) for k, a in U.items()}
        bounds = np.flatnonzero(np.diff(mids)) + 1
        starts, ends = np.concatenate([[0], bounds]), np.concatenate([bounds, [len(mids)]])
        for a, b in zip(starts, ends):
            mid = int(mids[a])
            if mid not in members:
                continue
            r = mcn.loc[mid]
            const = rf.MintConst(mid=mid, mbt=int(r.mbt), mslot=int(r.mslot), s0=int(r.s0), v=float(r.v), cbt=fnum(r.cbt), has_creator=not pd.isna(r.ch), nb=fnum(r.nb),
                                 ntr=fnum(r.ntr), bbuy=fnum(r.bbuy), bsell=fnum(r.bsell), cre_sell=fnum(r.cre_sell))
            sl = U["slot"][a:b]
            res = rf.mint_features(prep, const, None if pd.isna(r.ch) else r.ch, sl, U["bt"][a:b], U["isbuy"][a:b], U["sol"][a:b], U["tok"][a:b], U["q"][a:b],
                                   U["b"][a:b], U["th"][a:b], vq=U["vq"][a:b], seg_end=None, grads_rule="asof_pda_first_print", mint_filters="asof", return_state=True)
            lpd = lp_discontinuities(U["isbuy"][a:b], U["tok"][a:b], U["b"][a:b], np.full(b - a, mid))
            for kk in rep["lp"]:
                rep["lp"][kk] += lpd[kk]
            if res is None:
                continue
            T = res.I[:, 1]
            sel = (T % MARK_S == 0) & (T >= w0) & (T <= last_entry)
            if not sel.any():
                continue
            bt0 = U["bt"][a:b]
            s0bt = float(bt0[0]) if np.isfinite(bt0[0]) else (float(bt0[np.isfinite(bt0)][0]) if np.isfinite(bt0).any() else float(r.mbt))
            rep["d3_kept_pre_filter"] += int(sel.sum())
            keep = sel & (T >= s0bt + D3_DROP_S)
            rep["d3_dropped"] += int((sel & ~keep).sum())
            rep["d3_keys"] |= {(mid, int(t_)) for t_ in T[sel & ~keep]}
            if not keep.any():
                continue
            rep["mints_with_rows"] += 1
            Tk, sdk = T[keep], res.I[keep, 2]
            s_dec = dates_s(Tk)
            st = res.state
            st_args = dict(sl=sl, isbuy=U["isbuy"][a:b], sol=U["sol"][a:b], tok=U["tok"][a:b], q=U["q"][a:b], b=U["b"][a:b], vq=U["vq"][a:b])
            tm = np.arange(((max(w0, int(r.mbt) + 600) + MARK_S - 1) // MARK_S) * MARK_S, min(e, int(r.mbt) + rl.HORIZON_S + 2 * MARK_S) + 1, MARK_S, dtype=np.int64)
            sm = dates_s(tm)
            cs = bk.clock_slot(tm, ckb, cks)
            S = bk.mint_states(**st_args, sd=sdk, slot_s_dec=s_dec, t_marks=tm, slot_s_marks=sm, clock_slot_marks=cs, lats=(*LATS, LAT_REPORT_LONG))
            R.mid.append(np.full(len(Tk), mid, np.int64))
            R.T.append(Tk)
            R.sd.append(sdk)
            R.X.append(res.X[keep])
            R.sdec.append(s_dec)
            R.spot.append(S["spot"])
            for lat in R.ent:
                if delta > 0 and lat in LATS:
                    Qe, Be, ssb, nb_ = entry_states_shifted(sl, st["QX"], st["BX"], st["ib"], np.asarray(U["sol"][a:b], float), sdk, s_dec, lat, delta)
                else:
                    Qe, Be, ssb, nb_ = S["Qe"][lat], S["Be"][lat], S["ssb"][lat], S["nearby"][lat]
                R.ent[lat].append((Qe, Be, ssb, nb_))
            R.marks.append((np.full(len(tm), mid, np.int64), tm, S["Qx"], S["Bx"]))
    return R, rep, con


def concat(parts: list, dtype=None) -> np.ndarray:
    return np.concatenate(parts) if parts else np.zeros(0, dtype or float)


def mark_lookup(marks: list, mid: np.ndarray, t1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Qx, Bx at (mid, T1); NaN where the mint has no mark at T1 (an unpriced exit)."""
    if not marks or len(mid) == 0:
        return np.full(len(mid), np.nan), np.full(len(mid), np.nan)
    mm, tt, qx, bx = (np.concatenate([m[i] for m in marks]) for i in range(4))
    key = (mm.astype(np.int64) << 32) | tt.astype(np.int64)
    o = np.argsort(key, kind="stable")
    ks = key[o]
    kk = (np.asarray(mid, np.int64) << 32) | np.asarray(t1, np.int64)
    pos = np.minimum(np.searchsorted(ks, kk), len(ks) - 1)
    found = ks[pos] == kk
    j = o[pos]
    return np.where(found, qx[j], np.nan), np.where(found, bx[j], np.nan)


def price_variant(tr: Mapping[str, np.ndarray], lat: float, *, send: int, rent: int, auto: bool, e1_penalty: float, stake: int = FZ.STAKE_LAMPORTS) -> dict[str, Any]:
    """Deciding or report-only variant of one latency cell: no-fail, flat and pressure legs (intercept refit on this cell's fills). The E1 penalty is the larger of the
    haircut and max(0, -5 rbar) per filled, non-stranded trip."""
    Qe, Be, ssb, nb_ = (tr["ent"][lat][i] for i in range(4))
    Qx, Bx = tr["Qx"], tr["Bx"]
    unpriced = ~(np.isfinite(Qx) & np.isfinite(Bx) & (Qx > 0) & (Bx > 0))  # (e) reserve state <= 0 or missing
    Qx_, Bx_ = np.where(unpriced, 1.0, Qx), np.where(unpriced, 1.0, Bx)
    stranded = tr["strand_class"]
    stk = np.full(len(Qe), float(stake))
    pnl, filled, st = bk.round_trip(stk, Qe, Be, tr["spot"], Qx_, Bx_, send, rent, stranded=stranded, auto_stranded=auto, return_stranded=True)
    un = unpriced & filled  # a guarded (unfilled) buy has nothing to sell
    pnl = np.where(un, -(stk + send) - FZ.RENT_STRANDED, pnl)  # stake + buy send + rent, no sell send (QP ruling iii, same as a strand-class mint); the trade stays in the book
    st = st | un
    if e1_penalty > 0:
        pnl0, _ = bk.round_trip(stk, Qe, Be, tr["spot"], Qx_, Bx_, send, rent, h_in=0.0, h_out=0.0, stranded=stranded, auto_stranded=auto)
        pnl0 = np.where(un, pnl, pnl0)
        hair = pnl0 - pnl
        extra = np.where(filled & ~st, np.maximum(0.0, e1_penalty - hair), 0.0)
        pnl = pnl - extra
    flat, press, c, p = bk.fail_legs(pnl, filled, ssb, nb_, send)
    return {"nofail": pnl, "flat": flat, "press": press, "filled": filled, "stranded": st, "intercept": c, "p": p}


def cell_stats(pnl, T0, mid, w0: int) -> dict[str, Any]:
    return bk.book_stats(pnl, T0, mid, w0=w0)


def gate(cells: Mapping[str, Mapping[str, Any]], alpha: float) -> dict[str, Any]:
    """Section 6.3 on all cells. Returns {'cells': {name: {item: bool}}, 'decision_p', 'pass'}."""
    out, ps = {}, []
    for name, s in cells.items():
        p = s.get("p_block")
        ps.append(1.0 if p is None or (isinstance(p, float) and math.isnan(p)) else float(p))
        out[name] = {
            "n": s.get("n", 0) >= GATE_N,
            "dates": s.get("dates", 0) >= GATE_DATES and bool(s.get("days_pos_majority")),
            "ci90_lo": (s.get("ci90_lo_sol") or 0) > 0,
            "ex_top3": s.get("ex_top3_sol", 0) > 0,
            "ex_best_block": s.get("ex_best_block_sol", 0) > 0,
            "ex_best_date": s.get("ex_best_date_sol", 0) > 0,
        }
    dp = max(ps) if ps else 1.0
    ok_items = all(all(v.values()) for v in out.values())
    return {"cells": out, "decision_p": dp, "alpha": alpha, "p_ok": dp <= alpha, "pass": bool(ok_items and dp <= alpha)}


def decide(cells: Mapping[str, Mapping[str, Any]], N: int, A: int, keys_ok: bool, press_gap: bool, alpha: float) -> dict[str, Any]:
    """PASS / FAIL / NOT_DECIDABLE (EXP 6.4, 7.1). NOT_DECIDABLE never comes from a seal; it comes from these structure conditions."""
    nd = []
    if N < MIN_BLOCKS:
        nd.append(f"{N} blocks hold an attempt (need {MIN_BLOCKS})")
    if A < MIN_ATTEMPTS:
        nd.append(f"{A} attempts (need {MIN_ATTEMPTS})")
    if any(s.get("n", 0) < GATE_N for s in cells.values()):
        nd.append(f"a cell has fewer than {GATE_N} trades")
    if not keys_ok:
        nd.append("decision keys are not a subset of the count job's live keys (or its live md5 differs)")
    if press_gap:
        nd.append("a counted trade lacks its pressure-leg P&L")
    g = gate(cells, alpha) if cells else {"pass": False, "decision_p": 1.0, "alpha": alpha, "cells": {}}
    if nd:
        return {"verdict": "NOT_DECIDABLE", "reasons": nd, "gate": g}
    return {"verdict": "PASS" if g["pass"] else "FAIL", "reasons": [], "gate": g}


def diverged_result(plan: Plan, fee: Mapping[str, Any], reasons: Sequence[str]) -> dict[str, Any]:
    return {"schema": SCHEMA, "verdict": "NOT_DECIDABLE", "reasons": ["count_build_divergence: " + r for r in reasons], "gate": {"pass": False, "decision_p": 1.0}, "N_blocks_with_attempts": 0,
            "A_attempts": 0, "decision_p": 1.0, "alpha": ALPHA_FAMILY / plan.k, "k": plan.k, "w0": ef.hour_key(plan.w0), "e": ef.hour_key(plan.e), "cells": {}, "fee_arm": dict(fee),
            "report_only": {}, "claim": "forward simulated paper; not money made; leads only to the DEC-018 path and the owner's yes"}


def dual_rows(scratch: Path, plan: Plan, deps: Deps, universe_min_mbt: int) -> tuple[list, list]:
    """(d) report-only, run AFTER the verdict is computed and printed: rows that exist only under the asof filters and only under the frozen (training) filters,
    hour marks in [W0, E - 1h]. Cannot alter the verdict (nothing it returns is read by the decision)."""
    import duckdb
    import pandas as pd

    work = scratch / "work"
    con = duckdb.connect()
    con.sql(f"SET memory_limit='{deps.memory_limit}'; SET threads={int(deps.threads)}; SET enable_progress_bar=false; SET preserve_insertion_order=true")
    ckb, cks = np.load(work / "clock_bt.npy"), np.load(work / "clock_slot.npy")
    mk = np.load(work / "mkt.npz")
    meta = pd.read_parquet(scratch / "meta.parquet")
    meta = meta[meta["mbt"] >= universe_min_mbt].reset_index(drop=True)
    mcn = pd.read_pickle(work / "mintconst.pkl").set_index("mid")
    prep = rf.build_prep(ckb, cks, mk["mnt"], {k: mk[k] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}, meta["mbt"].to_numpy(np.int64), meta["s0"].to_numpy(np.float64))
    w0, e = int(plan.w0.timestamp()), int(plan.e.timestamp())
    members = set(meta["mid"].tolist())
    inwin = lambda T_: set(T_[(T_ % MARK_S == 0) & (T_ >= w0) & (T_ <= e - MARK_S)].tolist())  # noqa: E731
    ao, fo = [], []
    for lo, hi in ut_chunks(con, scratch):
        U = con.sql(f"""select mid, slot, txi, evi, bt, isbuy, sol, tok, q, b, th, vq from read_parquet('{scratch}/ut/*.parquet', filename=true, file_row_number=true)
                        where mid >= {lo} and mid <= {hi} order by mid, slot, txi, evi, filename, file_row_number""").fetchnumpy()
        mids = np.asarray(U["mid"], np.int64)
        U = {k: (np.ma.filled(a.astype(float), np.nan) if np.ma.isMaskedArray(a) and k in ("bt", "q", "b", "tok", "sol", "vq") else a) for k, a in U.items()}
        bounds = np.flatnonzero(np.diff(mids)) + 1
        for a, b in zip(np.concatenate([[0], bounds]), np.concatenate([bounds, [len(mids)]])):
            mid = int(mids[a])
            if mid not in members:
                continue
            r = mcn.loc[mid]
            const = rf.MintConst(mid=mid, mbt=int(r.mbt), mslot=int(r.mslot), s0=int(r.s0), v=float(r.v), cbt=fnum(r.cbt), has_creator=not pd.isna(r.ch), nb=fnum(r.nb),
                                 ntr=fnum(r.ntr), bbuy=fnum(r.bbuy), bsell=fnum(r.bsell), cre_sell=fnum(r.cre_sell))
            args = (prep, const, None if pd.isna(r.ch) else r.ch, U["slot"][a:b], U["bt"][a:b], U["isbuy"][a:b], U["sol"][a:b], U["tok"][a:b], U["q"][a:b], U["b"][a:b], U["th"][a:b])
            ra = rf.mint_features(*args, vq=U["vq"][a:b], seg_end=None, grads_rule="asof_pda_first_print", mint_filters="asof")
            rf_ = rf.mint_features(*args, vq=U["vq"][a:b], seg_end=None, grads_rule="frozen", mint_filters="frozen")
            Ta_ = inwin(ra.I[:, 1]) if ra is not None else set()
            Tf_ = inwin(rf_.I[:, 1]) if rf_ is not None else set()
            ao += [(mid, t_) for t_ in sorted(Ta_ - Tf_)]
            fo += [(mid, t_) for t_ in sorted(Tf_ - Ta_)]
    con.close()
    return ao, fo


def dual_report(ao: list, fo: list, tr_mid, tr_T0, cell_pnl) -> dict[str, Any]:
    asof_only = set(ao)
    entered = np.array([(int(m_), int(t_)) in asof_only for m_, t_ in zip(tr_mid, tr_T0)], bool)
    dual = {"asof_only_rows": len(ao), "frozen_only_rows": len(fo), "by_mark": {}, "by_date": {}, "asof_only_entered_top10": int(entered.sum()),
            "pnl_asof_only_trades": {}, "pnl_other_trades": {},
            "note": "report only, computed after E and after the verdict was printed; asof-only rows are NOT removed from the deciding book"}
    for key, lst in (("asof_only", ao), ("frozen_only", fo)):
        for m_, t_ in lst:
            dual["by_mark"].setdefault(str(t_), {"asof_only": 0, "frozen_only": 0})[key] += 1
            dual["by_date"].setdefault(str(bk.utc_dates([t_])[0]), {"asof_only": 0, "frozen_only": 0})[key] += 1
    for lat in LATS:
        for leg in ("flat", "press"):
            x = cell_pnl[lat][leg] / 1e9
            dual["pnl_asof_only_trades"][f"{leg}_{lat}"] = {"n": int(entered.sum()), "total_sol": float(x[entered].sum()), "mean_sol": float(x[entered].mean()) if entered.any() else None}
            dual["pnl_other_trades"][f"{leg}_{lat}"] = {"n": int((~entered).sum()), "total_sol": float(x[~entered].sum()), "mean_sol": float(x[~entered].mean()) if (~entered).any() else None}
    return dual


def run_read(paths: Paths, plan: Plan, fee: Mapping[str, Any], deps: Deps, on_verdict: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """Post-lock: build, score, book, price, stats, decision. Returns the result dict (nothing is written here except scratch)."""
    out_dir = Path(paths.out_dir)
    scratch = out_dir / "scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    w0, e = int(plan.w0.timestamp()), int(plan.e.timestamp())
    slot_s = slot_s_by_date(plan.sph)
    try:
        try:
            stats = rl.build(Path(paths.walk_dir), plan.hours[0], ef.hour_key(ef.hour_dt(plan.hours[-1]) + timedelta(hours=1)), scratch, v_source="event", canonical_fn=deps.canonical_fn,
                             problems_fn=deps.problems_fn, verify=deps.verify, memory_limit=deps.memory_limit, threads=deps.threads, tmp_dir=scratch / "tmp",
                             trade_end=ef.hour_key(ef.hour_dt(plan.hours[-1]) + timedelta(hours=1)))
        except rl.LayoutRefused as exc:
            raise CountBuildDivergence(exc.reasons) from exc
        struct_probs = []
        if stats.get("v0_missing_days_over"):
            struct_probs.append(f"v0-missing share above {rl.V0_MISSING_REFUSE_SHARE} on {stats['v0_missing_days_over']}")
        if stats.get("mixed_null_txi_slots"):
            struct_probs.append(f"{stats['mixed_null_txi_slots']} mixed null / non-null tx_index (mint, slot) pair(s)")
        if struct_probs:
            raise CountBuildDivergence(struct_probs)
        R, rep, con = build_rows(scratch, plan, fee, slot_s, deps, rc.UNIVERSE_MIN_MBT)
        boosters = deps.boosters_fn(str(paths.models_dir) if paths.models_dir else None)
        mid = concat(R.mid, np.int64)
        T = concat(R.T, np.int64)
        sd = concat(R.sd, np.int64)
        X = np.vstack(R.X) if R.X else np.zeros((0, FZ.N_FEATURES), np.float32)
        score = rs.bag_score(boosters, X) if len(X) else np.zeros(0)
        # (b) per count mark: recompute the live set exactly as r4b_count does, compare live_md5 / live_tokens / decision_rows, decision keys must be a subset
        marks = sorted({(int(ln["mark_t"]), int(ln["sd"])) for ln in plan.counts})
        meta = [m for m in con.sql(f"select mid, mint, mbt from read_parquet('{scratch}/meta.parquet')").fetchall() if m[2] >= rc.UNIVERSE_MIN_MBT]
        mint_of = {m[0]: m[1] for m in meta}
        mid_of = {m[1]: m[0] for m in meta}
        lk = live_keys(scratch, marks, mint_of, {m[0]: m[2] for m in meta}, con)
        dkeys: dict[int, set] = {}
        for mi, t in zip(mid.tolist(), T.tolist()):
            dkeys.setdefault(t, set()).add(mint_of[mi])
        by_hour = {ln["mark_t"]: ln for ln in plan.counts}
        notsub = sorted(t for t, ks in dkeys.items() if not ks <= set(lk.get(t, {})))
        md5_bad = [t for t in lk if rc.keys_md5([(m_, t) for m_ in lk[t]]) != by_hour[t]["live_md5"] or len(lk[t]) != by_hour[t]["live_tokens"]
                   or sum(1 for n_ in lk[t].values() if n_ >= rc.MIN_PRINTS_DECISION) != by_hour[t]["decision_rows"]]
        keys_ok = not notsub and not md5_bad
        gap = {}
        for t in sorted(lk):
            if w0 <= t <= e - MARK_S:
                g = gap_by_cause(lk[t], dkeys.get(t, set()), rep["d3_keys"], mid_of, t)
                gap[ef.hour_key(datetime.fromtimestamp(t, tz=timezone.utc))] = {"live": len(lk[t]), "decision_rows": len(dkeys.get(t, ())), "gap": len(lk[t]) - len(dkeys.get(t, ())), **g}
        null_bt = {}
        for h in plan.hours:
            f = scratch / "ut" / f"{h}.parquet"
            if f.exists():
                null_bt[h] = int(con.sql(f"select count(*) from read_parquet('{f}') where bt is null").fetchone()[0])
        # the book
        bkr = bk.xs_book(T, mid, score)
        t1 = np.where(bkr["reason"] == "end", e, bkr["T1"])
        row = bkr["row"]
        tr_mid, tr_T0 = bkr["mid"], bkr["T0"]
        Qx, Bx = mark_lookup(R.marks, tr_mid, t1)
        strand_class = np.array([plan.strand.get(mint_of[int(m)], True) for m in tr_mid], bool)
        tr = {"ent": {lat: [concat([p[i] for p in R.ent[lat]])[row] for i in range(4)] for lat in R.ent}, "spot": concat(R.spot)[row], "Qx": Qx, "Bx": Bx, "strand_class": strand_class}
        send = int(fee["send"])
        e1p = float(plan.e1["penalty_lamports"])
        variants = {"deciding": dict(send=send, rent=FZ.RENT_DECIDING, auto=True)}
        report_variants = {"rent": dict(send=send, rent=FZ.RENT_REPORT, auto=True), "no_auto_stranded": dict(send=send, rent=FZ.RENT_DECIDING, auto=False),
                           "send_55k": dict(send=FZ.SEND_FEE_REPORT, rent=FZ.RENT_DECIDING, auto=True)}
        cells, cell_pnl, rep_cells = {}, {}, {}
        for lat in LATS:
            pv = price_variant(tr, lat, e1_penalty=e1p, **variants["deciding"])
            cell_pnl[lat] = pv
            for leg in ("flat", "press"):
                cells[f"{leg}_{lat}"] = cell_stats(pv[leg], tr_T0, tr_mid, w0)
        press_gap = any(not np.isfinite(cell_pnl[lat]["press"]).all() for lat in LATS)
        for name, kw in report_variants.items():
            for lat in LATS:
                pv = price_variant(tr, lat, e1_penalty=e1p, **kw)
                for leg in ("flat", "press"):
                    rep_cells[f"{name}:{leg}_{lat}"] = cell_stats(pv[leg], tr_T0, tr_mid, w0)
        pv4 = price_variant(tr, LAT_REPORT_LONG, e1_penalty=e1p, **variants["deciding"])
        for leg in ("flat", "press"):
            rep_cells[f"lat4.0:{leg}_{LAT_REPORT_LONG}"] = cell_stats(pv4[leg], tr_T0, tr_mid, w0)
        blocks_with = int(len(np.unique((tr_T0 - w0) // BLOCK_S))) if len(tr_T0) else 0
        A = int(len(tr_T0))
        alpha = ALPHA_FAMILY / plan.k
        dec = decide(cells, blocks_with, A, keys_ok, press_gap, alpha)
        con.close()
        stranded = {f"{leg}_{lat}": int(cell_pnl[lat]["stranded"].sum()) for lat in LATS for leg in ("flat",)}
        res = {
            "schema": SCHEMA, "verdict": dec["verdict"], "reasons": dec["reasons"], "gate": dec["gate"], "N_blocks_with_attempts": blocks_with, "A_attempts": A,
            "decision_p": dec["gate"]["decision_p"], "alpha": alpha, "k": plan.k, "w0": ef.hour_key(plan.w0), "e": ef.hour_key(plan.e), "cells": cells,
            "fee_arm": dict(fee), "e1": plan.e1, "stack": {"send": send, "rent": FZ.RENT_DECIDING, "auto_stranded": True, "latencies": list(LATS), "k_rounding": FZ.K_ROUNDING},
            "keys": {"marks": len(marks), "not_subset_marks": len(notsub), "live_md5_mismatch_marks": len(md5_bad), "ok": keys_ok, "live_vs_decision_gap_by_mark": gap},
            "d3": {"dropped_rows": rep["d3_dropped"], "kept_pre_filter": rep["d3_kept_pre_filter"]}, "stranded_counts": stranded,
            "strand_class_trades": int(strand_class.sum()), "mints": rep["mints"], "mints_with_rows": rep["mints_with_rows"],
            "report_only": {"asof_vs_frozen": None, "null_block_time_prints_by_hour": null_bt, "variants": rep_cells, "lp_discontinuities": rep["lp"], "slot_s_by_date": slot_s,
                            "note": "rent, no-auto-stranded, 55k, 4.0 s, block sums (block_sum_* inside every cell), LP discontinuity counts: report only, never deciding"},
            "claim": "forward simulated paper; not money made; leads only to the DEC-018 path and the owner's yes",
        }
        if on_verdict is not None:
            on_verdict(res)  # the verdict is printed BEFORE the report-only dual run starts (and before any result file)
        ao, fo = dual_rows(scratch, plan, deps, rc.UNIVERSE_MIN_MBT)
        res["report_only"]["asof_vs_frozen"] = dual_report(ao, fo, tr_mid, tr_T0, cell_pnl)
        return res
    except CountBuildDivergence as exc:
        res = diverged_result(plan, fee, exc.reasons)
        if on_verdict is not None:
            on_verdict(res)
        return res
    finally:
        if not deps.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)


# ---- orchestration ------------------------------------------------------------------------------------------------------------------------
def emit_verdict(res: Mapping[str, Any], deps: Deps) -> None:
    c = res.get("cells", {})
    lines = [f"VERDICT: {res['verdict']}  (decision p {res.get('decision_p')} vs alpha {res.get('alpha')}; N blocks {res.get('N_blocks_with_attempts')}, A {res.get('A_attempts')})"]
    lines += [f"  reason: {r}" for r in res.get("reasons", [])]
    for name, s in c.items():
        lines.append(f"  {name}: n {s.get('n')} mean_sol {s.get('mean_sol')} ci90_lo {s.get('ci90_lo_sol')} ex_top3 {s.get('ex_top3_sol')} p_block {s.get('p_block')}")
    lines.append("  " + res["claim"])
    deps.stderr("\n".join(lines))


def write_result(out_dir: Path, res: Mapping[str, Any]) -> Path:
    p = out_dir / RESULT
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(res, sort_keys=True, indent=1, default=_json_default) + "\n")
    return p


def read(paths: Paths, deps: Deps | None = None) -> dict[str, Any]:
    """The whole read. Raises ReadRefused (nothing written) on a preflight refusal. After the lock, an exception writes an `aborted` line and re-raises."""
    deps = deps or Deps()
    out_dir = Path(paths.out_dir)
    started = deps.now_fn()
    plan, fee, reasons = preflight(paths, deps, started)
    if reasons or plan is None:
        raise ReadRefused(reasons or ["preflight failed"])
    token = take_lock(out_dir)
    ledger_event(out_dir, "started", token=token, head=deps.head_fn(), job=os.environ.get("MISCUSI_JOB_ID", ""), w0=ef.hour_key(plan.w0), e=ef.hour_key(plan.e), k=plan.k,
                 send=fee["send"], fee_arm=fee.get("source"), code_sha256=plan.v and {m: plan.v[f"EXP027_SHA256_{m.upper()}"] for m in CODE_MODULES}, inputs=plan.inputs)
    try:
        res = run_read(paths, plan, fee, deps, on_verdict=lambda r: emit_verdict(r, deps))  # prints the verdict BEFORE any result file and before the dual run
        res["token"] = token
        rp = write_result(out_dir, res)
        ledger_event(out_dir, "completed", token=token, verdict=res["verdict"], result=str(rp), result_sha256=sha256_file(rp), N=res["N_blocks_with_attempts"], A=res["A_attempts"])
        return res
    except BaseException as exc:  # noqa: BLE001 - the read is spent whatever happened
        reasons = exc.reasons if isinstance(exc, ReadRefused) else [f"{type(exc).__name__}: {exc}"]
        ledger_event(out_dir, "aborted", token=token, reasons=reasons)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--walk-dir", required=True, help="Forward walk 2 dataset dir (D/): PATH of a pinned input")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT), help="READS.jsonl, READ.lock, result file")
    ap.add_argument("--counts-dir", default=str(DEFAULT_OUT), help="the count job's dir (counts.jsonl, config.json)")
    ap.add_argument("--exp-file", default=str(EXP_FILE))
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--memory-limit", default="6GB")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)  # no flag changes a pinned value
    paths = Paths(Path(a.walk_dir), Path(a.out_dir), Path(a.counts_dir), Path(a.exp_file), TRIES, Path(a.models_dir) if a.models_dir else None)
    deps = Deps(memory_limit=a.memory_limit, threads=a.threads)
    try:
        res = read(paths, deps)
    except ReadRefused as exc:
        print("REFUSED: " + " | ".join(exc.reasons), file=sys.stderr)
        return 2
    except rl.LayoutRefused as exc:
        print("ABORTED (layout): " + " | ".join(exc.reasons), file=sys.stderr)
        return 3
    return 0 if res["verdict"] in ("PASS", "FAIL", "NOT_DECIDABLE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
