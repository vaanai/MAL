#!/usr/bin/env python3
"""Kalshi LIP-F2 cohort wrapper (hunt18 / kalshi-lip).  Paper only; public data only.

Runs the FROZEN LIP-F1 simulator (``tools/kalshi_lip_sim.py``, md5 pinned by DECLARE-FORWARD-PINS-1) for exactly
TWO gated variants, as quant-proof ruled (QP-REVIEW-F2.md, sha256 965a41bc...):

  F2-A SIDE-CAP  a side is posted with size 0 when that side's best bid is ABOVE 7000 bp (exactly 70c is kept).
                 The zero is applied together with the Q5 inventory sizes (min of the two), and Q4 (rival depth
                 >= T - s on each side) is then re-checked with s = 0 on the zeroed side.  A zeroed side has
                 phi = 0 but both sides must still be valid, so the reward comes from the OTHER side only
                 (v_k = phi of the side we post).  Held inventory is never touched.  Both sides cannot exceed
                 7000 bp at once (Q2 needs b_y + b_n < 10000).  I_max = 50, S = 25 as in LIP-F1.
  F2-B INV25     I_max = 25 (= S) instead of 50.  S = 25.  Nothing else differs from LIP-F1's rule.

What is shared with LIP-F1 (inherited, nothing loosened): the reward model, fills, fees, the $1.00 minimum, cells
U0 / U15 / BQ, G1-G7 + G4b, the kill condition (K1 = -8 x S = -$200), bootstrap seed 1 with 1,000 draws, the
coverage block (obrec-1019c rounds at 222.7 s, lipsnap polls at 900 s) and the frozen source-gap rule (obrec-1019d
for a gas unit when it has no gap over CAP, else obrec-1019c).

What differs from LIP-F1 (so F2 is NOT "LIP-F1 plus one diff"; quant-proof L2):
  * the universe: LIP-F1's eight gas series only (the seven other series are not admitted);
  * the first-seen rule uses the F2 cohort freeze instant (one common instant for both variants, read from the
    frozen DECLARE-F2.md);
  * the read window: R is a 00:00Z instant from 2026-10-26 (NOT_DECIDABLE at 2026-11-02T00:00Z);
  * the deciding bound of G4 and G4b: the multiplicity-adjusted 5/m percentile with m = 3 gated books (LIP-F1 Book
    A, F2-A, F2-B), i.e. the 1.6667th percentile of the SAME 1,000-draw bootstrap matrix (RandomState(1)), with the
    unadjusted p05 printed beside it.  A variant that passes only on the unadjusted bound is reported as
    "FAIL (multiplicity-adjusted)", never as a pass.

How the override works: ``f2_cohort()`` is a context manager that, for the duration of ONE call into the simulator,
rebinds module attributes of ``tools.kalshi_lip_sim`` and restores them in a ``finally``:
  constants  FREEZE_ISO, FREEZE_US, GAS_SERIES (the same eight), OTHER_SERIES (empty), SERIES_ADMITTED (the eight),
             READ_FROM_ISO (2026-10-26T00:00:00Z), NOT_DECIDABLE_ISO (2026-11-02T00:00:00Z)
  functions  cluster_bootstrap and evaluate_gate (the multiplicity-adjusted bound; the unadjusted p05 and every other
             number are the simulator's own), and for F2-A only decide_quote (the side cap).
The simulator file, its tests and its output code are never edited.  The context refuses to start unless the
simulator is exactly the frozen one (md5) and holds exactly its LIP-F1 constants and functions, so it cannot nest.

Process rules this tool enforces (QP L1; the full text is in DECLARE-F2.md):
  * an outcome-computing run (``--read-instant`` or ``--scan``) is refused unless (i) a LIP-F1 DECIDING gate.json path
    is given and is one, and (ii) a Book C run-done marker path is given and exists;
  * ``--dry-run`` prints loader counts ONLY (units by status and source, snapshot consistency, unsealed parts, metadata
    counts, store states).  It never applies the F2 rule and never prints a quote, share, pause, cap, fill, reward or
    P&L count;
  * every output is relabelled F2-A / F2-B and goes only to its own ``--out`` with a marker file.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

try:  # run as ``python -m tools.kalshi_lip_f2`` (repo root) or as a script / from tools/ in tests
    from tools import kalshi_lip_sim as sim
    from tools import kalshi_public as kp
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kalshi_lip_sim as sim
    import kalshi_public as kp

# ------------------------------------------------------------------------------------------------ the pins
SIM_MD5_PINNED = "3dc4134aa575752927bea18db0e6a341"            # DECLARE-FORWARD-PINS-1: tools/kalshi_lip_sim.py
SIM_TESTS_MD5_PINNED = "1f5db2f15043f4f8451c647a863f741a"      # DECLARE-FORWARD-PINS-1: tools/test_kalshi_lip_sim.py
QP_REVIEW_SHA256 = "965a41bc757e8ec5aa1c89953dfa8ca20de1533091af97a45c10207fb5367761"
LIP_F1_FREEZE_ISO = "2026-10-19T03:33:16Z"
LIP_F1_NOT_DECIDABLE_ISO = "2026-11-02T00:00:00Z"
# DECLARE-FORWARD-PINS-1: the inherited LIP-F1 texts (file name, sha256)
LIP_F1_TEXTS = (
    ("declare", "DECLARE-FORWARD.md", "977fa41c78517cad11359121030c81555d9700aa0f66204afc7bc9333f0af796"),
    ("conditions", "DECLARE-FORWARD-CONDITIONS.md", "35ee7e652d470d03ae5ea0de7274c2da0caf8ab518baa4afa4dadeafc3dee63b"),
    ("verify1", "DECLARE-FORWARD-VERIFY-1.md", "8ce3e30f88bebd784e87f450bb846b11df334cdfb5f7e94cbad68aa1bc8b8bb3"),
    ("conditions2", "DECLARE-FORWARD-CONDITIONS-2.md", "a34bc6df87f823ea09a10164ac295f82f969e75928522df6ed9471647513d117"),
    ("process1", "DECLARE-FORWARD-PROCESS-1.md", "4cc45c27317560ecf2a08d5d65b73c9f02afd32deb0488ad3ce63f9f62de78f8"),
    ("process2", "DECLARE-FORWARD-PROCESS-2.md", "cf635192e8105632790a0cf5081f662aa8f346fb720391ed3fe9e8573419ddb0"),
)
# DECLARE-FORWARD-PINS-2 (run-2 sections appended): the NEW registry sha256 for --registry-sha256
REGISTRY_PINS = {
    "obrec-1019c": "d6affcff6abcdd5dce07af8d786ad6e33828c0b4a762d7cd85627ab80371da3a",
    "obrec-1019d": "3984c1c68bf0d51f52d06226771fd08f8ae33a174dcd14a646cd9221e86e5236",
}
ALLOWED_RECORDERS = ("obrec-1019c", "obrec-1019d")             # never obrec-1019e (Book C)

# ------------------------------------------------------------------------------------------------ the rule
VARIANTS = ("A", "B")
LABELS = {"A": "F2-A", "B": "F2-B"}
NAMES = {"A": "SIDE-CAP", "B": "INV25"}
SIZE = 25.0
IMAX = {"A": 50.0, "B": 25.0}
SIDE_CAP_BP = 7000                                              # F2-A: zero a side whose best bid is ABOVE this
M_GATED = 3                                                     # LIP-F1 Book A, F2-A, F2-B
ADJ_PCT = 5.0 / M_GATED                                         # the deciding percentile: 1.6667
READ_FROM_ISO = "2026-10-26T00:00:00Z"
NOT_DECIDABLE_ISO = "2026-11-02T00:00:00Z"
MARKER = "LIP-F2-VARIANT.json"
BOOKC_START_MARKER = "BOOK-C-REPORT-ONLY.json"                 # written at the START of a Book C run: not a done marker
SIM_OUTPUT_FILES = ("units.parquet", "fills.parquet", "days.parquet", "gate.json", "table.txt", "snaps.parquet")
FINAL_LIP_F1_VERDICTS = ("FORMULA_SCREEN_PASSED", "FAIL", "DEAD_AS_FROZEN")
# The simulator's own wording of the coverage reason (run_sim): coverage over [freeze, R) is monotone in R.
COVERAGE_ND_REASON = "recorder/poller coverage < 70% on two consecutive days"
VERDICT_PASS = "FORMULA_SCREEN_PASSED (multiplicity-adjusted)"
VERDICT_FAIL_ADJ = "FAIL (multiplicity-adjusted)"

DEFAULT_BOOKS = ["/data/mal/kalshi/obrec-1019c", "/data/mal/kalshi/obrec-1019d"]
DEFAULT_PROGRAMS = "/data/mal/kalshi/lipsnap-1019"
DEFAULT_META = "/data/mal/kalshi/meta-1019"
DEFAULT_RUNS = "/data/mal/kalshi/lipdl/run-*"                   # trades and settled markets, per-run stores
DEFAULT_SERIES = "/data/mal/kalshi"
DEFAULT_LIP_TEXTS_DIR = "/data/mal/hunt-1008/push-1016/hunt18/kalshi-lip"

# The simulator attributes the context manager rebinds, and their LIP-F1 values, snapshotted at import (before
# anything of ours ran).  ``f2_cohort`` refuses to start unless the simulator still holds exactly these.
PATCHED_CONSTANTS = ("FREEZE_ISO", "FREEZE_US", "GAS_SERIES", "OTHER_SERIES", "SERIES_ADMITTED", "READ_FROM_ISO",
                     "NOT_DECIDABLE_ISO")
PATCHED_FUNCTIONS = ("decide_quote", "cluster_bootstrap", "evaluate_gate")
F1_CONSTANTS = {n: getattr(sim, n) for n in PATCHED_CONSTANTS}
F1_FUNCS = {n: getattr(sim, n) for n in PATCHED_FUNCTIONS}
F2_SERIES = tuple(F1_CONSTANTS["GAS_SERIES"])                   # LIP-F1's eight gas series
F1_DRAWS, F1_SEED = sim.DRAWS, sim.SEED
# Values F2 inherits and never rebinds; checked at run time (a different value would make a number unattributable).
INHERITED = {"PRIMARY_RUN": "obrec-1019c", "BASELINE_GAP_S": {"obrec-1019c": 222.7, "lipsnap": 900.0},
             "DRAWS": 1_000, "SEED": 1, "CAP_US": 450 * sim.US, "L_US": 2 * sim.US, "BAND_BP": 1_000,
             "MIN_PAYOUT": 1.00, "COVERAGE_MIN": 0.70, "CELLS": ("U0", "U15", "BQ")}

# ------------------------------------------------------------------------------------------------ statements (QP L3)
STATEMENT_COHORT = (
    "A cohort F2 (2 gated variants of LIP-F1, frozen {instant}) exists on the same 8 gas series and gas days from "
    "{first_day}. No F2 number existed when this read ran. This verdict is not adjusted for F2 because LIP-F1 was "
    "declared first, and it is final.")
STATEMENT_F2_READ = (
    "Family declared before any F2 number: LIP-F1 Book A (gated, final at R = {lip_r}, not changed by this read); "
    "Book B and Book C (report-only); F2-A and F2-B (gated screens); F1-rule control (report-only). m = 3 gated "
    "books. F2's population is a subset of LIP-F1's: the same 8 gas series, unit-days {days}, G4b clusters = "
    "{clusters}. The deciding G4/G4b bounds are the 1.667th percentile; the 5th is shown. A pass is 'formula screen "
    "passed (multiplicity-adjusted)', never a positive book. It needs replication on a later-start window plus "
    "Option A and the owner. An F2 pass does not rescue or condition LIP-F1.")
STATEMENT_CONTROL = ("REPORT_ONLY_F1_CONTROL: LIP-F1's rule on F2's window. Not a LIP-F1 read and never cited as "
                     "LIP-F1 evidence (DECLARE-FORWARD section 9).")
RULE_TEXT = {
    "A": ("F2-A SIDE-CAP: a side is posted with size 0 when that side's best bid is above 7000 bp (exactly 70c is "
          "kept). The zero is applied together with the Q5 inventory sizes and Q4 is re-checked with size 0 on that "
          "side. The reward then comes from the other side only: a zeroed side has phi = 0, both sides must still be "
          "valid, v_k = phi of the side we post. Held inventory is never touched. S = 25, I_max = 50."),
    "B": "F2-B INV25: I_max = 25 (= S) instead of 50. S = 25. Nothing else differs from LIP-F1's rule.",
}
NOTES = [
    "F2-A and F2-B are gated FORMULA SCREENS (m = 3 with LIP-F1 Book A): a pass is never a positive book; it needs "
    "replication on a later-start window plus Option A (credited rewards) and the owner. Nothing here is a trading key.",
    "F2 is not 'LIP-F1 plus one diff': it also changes the universe (the 8 gas series of LIP-F1's 15), the first-seen "
    "rule (the F2 cohort freeze), the read window (R from 2026-10-26) and the deciding G4/G4b bound.",
    "The F2 population is a subset of LIP-F1's population: the same 8 gas series, snapshots, prints and results. An F2 "
    "number is never LIP-F1 evidence, never Book C evidence and never capacity evidence (DECLARE-FORWARD section 9; "
    "CAPACITY-1 section 2). An F2 pass does not rescue or condition LIP-F1.",
    "Deciding bound: G4 and G4b decide at the 5/m = 1.6667th percentile (m = 3) of the SAME 1,000-draw bootstrap matrix "
    "(RandomState(1)) as the unadjusted p05, which is printed beside it. A pass on the unadjusted bound only is "
    "reported as 'FAIL (multiplicity-adjusted)'.",
    "Kill condition unchanged: K1 = -8 x S = -$200, K2 as in LIP-F1 (the bootstrap p95; the adjusted bound does not "
    "enter it).",
    "Coverage: obrec-1019c rounds at 222.7 s and lipsnap polls at 900 s, as LIP-F1. obrec-1019d is admissible for gas "
    "units by the frozen gap rule; a unit with a hole over CAP falls back to obrec-1019c.",
]


class F2Refusal(Exception):
    """An F2 run was refused before the simulator ran (wrong pin, missing gate, bad --out, nested patch ...)."""


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _dump(d):
    return json.dumps(d, sort_keys=True, indent=1, default=str) + "\n"


# ------------------------------------------------------------------------------------------------ pins
def check_sim_pin():
    """The simulator file must be the pinned one; an F2 number from another file would be unattributable."""
    got = sim.file_md5(sim.__file__)
    if got != SIM_MD5_PINNED:
        raise F2Refusal(f"tools/kalshi_lip_sim.py md5 {got} is not the pinned {SIM_MD5_PINNED}")
    return got


def check_inherited():
    """The simulator values F2 inherits (cadence baseline, bootstrap draws and seed, CAP ...) are the frozen ones."""
    for n, want in INHERITED.items():
        got = getattr(sim, n)
        if got != want:
            raise F2Refusal(f"the simulator's {n} is {got!r}, not the inherited {want!r}")
    if F1_CONSTANTS["FREEZE_ISO"] != LIP_F1_FREEZE_ISO:
        raise F2Refusal("the simulator's freeze is not LIP-F1's")


def sim_is_lip_f1():
    """True iff every attribute the context rebinds holds exactly its import-time LIP-F1 object."""
    return (all(getattr(sim, n) is v for n, v in F1_CONSTANTS.items())
            and all(getattr(sim, n) is v for n, v in F1_FUNCS.items()))


def verify_lip_texts(texts_dir):
    """Check the six inherited LIP-F1 texts against their PINS-1 sha256.  -> ({file name: sha256}, VERIFY-1 path)."""
    if not texts_dir:
        raise F2Refusal("--lip-texts-dir is required")
    d = Path(texts_dir)
    got, verify1 = {}, None
    for key, fname, want in LIP_F1_TEXTS:
        p = d / fname
        if not p.is_file():
            raise F2Refusal(f"inherited LIP-F1 text {fname} not found in {d}")
        h = _sha256_bytes(p.read_bytes())
        if h != want:
            raise F2Refusal(f"{fname} sha256 {h} does not match the pinned {want} (DECLARE-FORWARD-PINS-1)")
        got[fname] = h
        if key == "verify1":
            verify1 = str(p)
    return got, verify1


# ------------------------------------------------------------------------------------------------ the declaration
_FREEZE_LINE = re.compile(r"^COHORT FREEZE INSTANT = (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)(?=\s|$)", re.M)


def parse_f2_declaration(text):
    """-> the cohort freeze instant (ISO).  DECLARE-F2.md's FIRST line is ``COHORT FREEZE INSTANT = <ISO> ...``
    (the output of the one stamping command's ``date -u``); it is the only such line; both variants are named."""
    found = _FREEZE_LINE.findall(text)
    if len(found) != 1:
        raise F2Refusal(f"expected exactly one 'COHORT FREEZE INSTANT = <ISO>' line in DECLARE-F2.md, found {len(found)}")
    first = text.splitlines()[0] if text.splitlines() else ""
    if not _FREEZE_LINE.match(first):
        raise F2Refusal("the 'COHORT FREEZE INSTANT' line must be the first line of DECLARE-F2.md")
    for v in LABELS.values():
        if v not in text:
            raise F2Refusal(f"DECLARE-F2.md does not name {v}")
    return found[0]


def load_f2_declaration(path, pin):
    """sha256-pin DECLARE-F2.md (the pin is given on the command line and recorded in the F2 pins record), parse the
    cohort freeze instant out of it.  -> (sha256, freeze iso)."""
    if not path:
        raise F2Refusal("--f2-declare is required")
    if not pin or not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise F2Refusal("--f2-declare-sha256 (64 hex digits) is required")
    p = Path(path)
    if not p.is_file():
        raise F2Refusal(f"--f2-declare {path} is not a file")
    got = _sha256_bytes(p.read_bytes())
    if got != pin:
        raise F2Refusal(f"DECLARE-F2.md sha256 {got} does not match the given pin {pin}")
    freeze = parse_f2_declaration(p.read_text())
    if sim.iso_to_us(freeze) <= sim.iso_to_us(LIP_F1_FREEZE_ISO):
        raise F2Refusal(f"the F2 cohort freeze {freeze} must be after LIP-F1's freeze {LIP_F1_FREEZE_ISO}")
    return got, freeze


def first_unit_day(freeze_iso):
    """The unit-day (date of E) of the first gas period that starts at or after the freeze: periods run 12:00Z to
    03:59Z of the next day, so a freeze of 2026-10-20T11:00Z gives 2026-10-21."""
    f = datetime.fromtimestamp(sim.iso_to_us(freeze_iso) / sim.US, tz=timezone.utc)
    s0 = f.replace(hour=12, minute=0, second=0, microsecond=0)
    if s0 < f:
        s0 += timedelta(days=1)
    return (s0 + timedelta(days=1)).strftime("%m-%d")


# ------------------------------------------------------------------------------------------------ the process gates
def _read_json_dict(p, what):
    try:
        d = json.loads(Path(p).read_text())
    except (OSError, ValueError) as e:
        raise F2Refusal(f"{what} {p} is not readable JSON: {type(e).__name__}") from e
    if not isinstance(d, dict):
        raise F2Refusal(f"{what} {p} is not a JSON object")
    return d


def check_lip_f1_gate(path):
    """Process rule (i): an outcome-computing F2 run needs LIP-F1's DECIDING gate.json: Book A, a ``--read-instant`` run
    (no scan), the LIP-F1 freeze, and a verdict that is final: PASS, FAIL, DEAD_AS_FROZEN; NOT_DECIDABLE only if the
    counts are met in all three cells, or R >= 2026-11-02T00:00Z, or the reason is the coverage rule (an exclusion-share
    NOT_DECIDABLE at an early candidate is not final); COUNTS_NOT_MET only at the 2026-11-02T00:00Z read.  The verdict
    and every number in it stay unprinted: F2 never conditions on LIP-F1.  -> {path, sha256, read_instant}."""
    if not path:
        raise F2Refusal("skipped: LIP-F1 not final: --lip-f1-gate (LIP-F1's deciding gate.json) is required")
    p = Path(path)
    if not p.is_file():
        raise F2Refusal(f"skipped: LIP-F1 not final: {path} does not exist")
    d = _read_json_dict(p, "--lip-f1-gate")
    if d.get("book") != "A" or d.get("deciding") is not True or "scan" in d or d.get("freeze") != LIP_F1_FREEZE_ISO:
        raise F2Refusal(f"{path} is not a LIP-F1 deciding gate.json (Book A, --read-instant run, LIP-F1 freeze)")
    verdict, r = d.get("verdict"), d.get("read_instant")
    at_nd_date = False
    if r:
        try:
            at_nd_date = sim.iso_to_us(r) >= sim.iso_to_us(LIP_F1_NOT_DECIDABLE_ISO)
        except ValueError:
            at_nd_date = False
    final = verdict in FINAL_LIP_F1_VERDICTS
    if verdict == "COUNTS_NOT_MET":
        final = at_nd_date
    elif verdict == "NOT_DECIDABLE":
        # decide_verdict returns NOT_DECIDABLE before it looks at the counts, and the exclusion-share reason is not
        # monotone in R, so an exclusion-share NOT_DECIDABLE at an early candidate is NOT final (QP R1).  Final only if
        # (a) the counts are met in all three cells, (b) the read is at/after the NOT_DECIDABLE date, or (c) the reason
        # is the coverage rule.
        cells = d.get("cells") or {}
        counts_met = all(((cells.get(c) or {}).get("gate") or {}).get("counts_met") is True for c in sim.CELLS)
        final = counts_met or at_nd_date or COVERAGE_ND_REASON in (d.get("nd_reasons") or [])
    if not final:
        raise F2Refusal(f"skipped: LIP-F1 not final: {path} is not LIP-F1's deciding gate.json (its verdict is not final)")
    return {"path": str(p), "sha256": _sha256_bytes(p.read_bytes()), "read_instant": r}


def check_bookc_done(path):
    """Process rule (ii): Book C's single run (CAPACITY-2 P2) is done.  The marker is a file that exists: the manager's
    run-done marker or Book C's gate.json.  BOOK-C-REPORT-ONLY.json is written when a Book C run STARTS and is refused."""
    if not path:
        raise F2Refusal("Book C is not done: --bookc-done (the Book C run-done marker) is required")
    p = Path(path)
    if not p.is_file():
        raise F2Refusal(f"Book C is not done: {path} does not exist")
    if p.name == BOOKC_START_MARKER:
        raise F2Refusal(f"{p.name} is written when a Book C run starts; give the run-done marker or Book C's gate.json")
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        d = None
    if isinstance(d, dict) and "book" in d and (d.get("book") != "C" or d.get("report_only") is not True):
        raise F2Refusal(f"{path} is not a Book C file (book / report_only)")
    return {"path": str(p), "sha256": _sha256_bytes(p.read_bytes())}


# ------------------------------------------------------------------------------------------------ the override
def bootstrap_stat(cluster_sum, cluster_n, draws, seed):
    """The simulator's draw matrix and statistic (kalshi_lip_sim.cluster_bootstrap, same lines): resample clusters with
    replacement, statistic = sum(net) / count(units) of the resample.  Returns the ``draws`` statistics."""
    s, n = np.asarray(cluster_sum, float), np.asarray(cluster_n, float)
    m = len(s)
    idx = np.random.RandomState(seed).randint(0, m, size=(draws, m))
    return s[idx].sum(axis=1) / n[idx].sum(axis=1)


def _bootstrap_with_adj(orig):
    """cluster_bootstrap + the multiplicity-adjusted percentile of the SAME draw matrix.  The simulator's own function
    supplies p05, p95, mean and clusters unchanged; ``p_adj`` is read from the recomputed matrix, which must
    reproduce the simulator's p05 exactly (else refuse)."""
    def cluster_bootstrap(cluster_sum, cluster_n, draws=F1_DRAWS, seed=F1_SEED):
        res = orig(cluster_sum, cluster_n, draws, seed)
        if not res.get("clusters"):
            return dict(res, p_adj=None, adj_pct=ADJ_PCT)
        stat = bootstrap_stat(cluster_sum, cluster_n, draws, seed)
        if float(np.percentile(stat, 5)) != res["p05"]:
            raise F2Refusal("the recomputed bootstrap matrix does not reproduce the simulator's p05")
        return dict(res, p_adj=float(np.percentile(stat, ADJ_PCT)), adj_pct=ADJ_PCT)
    return cluster_bootstrap


def _gate_with_adj(orig):
    """evaluate_gate with G4 and G4b deciding at the adjusted bound.  The simulator's unadjusted verdicts are kept as
    ``pass_unadjusted`` / ``all_pass_unadjusted``; ``pass`` / ``all_pass`` (which the simulator's verdict reads)
    become the adjusted ones."""
    def evaluate_gate(rows, *, boot=True):
        g = orig(rows, boot=boot)
        for k in ("G4", "G4b"):
            gk = g[k]
            padj = gk.get("p_adj")
            gk["pass_unadjusted"] = gk["pass"]
            gk["adj_pct"] = ADJ_PCT
            gk["pass"] = padj is not None and padj > 0
        g["all_pass_unadjusted"] = g["all_pass"]
        g["all_pass"] = all(g[k]["pass"] for k in ("G1", "G2", "G3", "G4", "G4b", "G5", "G6", "G7"))
        return g
    return evaluate_gate


def _side_cap(orig):
    """F2-A: the frozen Q1-Q5, then a side whose best bid is above SIDE_CAP_BP is posted with size 0 (applied with
    the Q5 sizes: the minimum of the two), and Q4 is re-checked with that size 0 (rival depth >= T on that side).
    A zeroed side takes no fill and has phi = 0 in the reward (the other side alone earns v_k)."""
    def decide_quote(unit, snap, Y, N, S, imax):
        q, why = orig(unit, snap, Y, N, S, imax)
        if q is None:
            return q, why
        sy = 0.0 if q.by > SIDE_CAP_BP else q.sy
        sn = 0.0 if q.bn > SIDE_CAP_BP else q.sn
        if sy == q.sy and sn == q.sn:
            return q, why
        T = unit.pset0[1]
        if snap.depth_y + sim.EPS < T - sy or snap.depth_n + sim.EPS < T - sn:
            return None, "Q4_SIDECAP"
        return sim.Quote(q.by, q.bn, sy, sn, q.dy, q.dn), why
    return decide_quote


@contextlib.contextmanager
def f2_cohort(freeze_iso, variant=None):
    """Rebind the simulator's universe / freeze / read-window constants to the F2 cohort for the duration of the block and
    restore them on exit (also on an exception).  ``variant`` None: that and nothing else (the loader dry-run).
    "A" / "B": also the multiplicity-adjusted bound, and for "A" the side cap.  Not thread-safe; not re-entrant
    (refuses to nest, and refuses to start on a simulator that is not exactly LIP-F1's)."""
    check_sim_pin()
    if variant not in (None,) + VARIANTS:
        raise F2Refusal(f"unknown variant {variant!r}")
    freeze_us = sim.iso_to_us(freeze_iso)
    if freeze_us <= sim.iso_to_us(LIP_F1_FREEZE_ISO):
        raise F2Refusal("the F2 cohort freeze must be after LIP-F1's freeze")
    if not sim_is_lip_f1():
        raise F2Refusal("the simulator is not exactly LIP-F1's (already overridden or mutated?): refusing to nest")
    check_inherited()
    consts = {"FREEZE_ISO": freeze_iso, "FREEZE_US": freeze_us, "GAS_SERIES": F2_SERIES, "OTHER_SERIES": (),
              "SERIES_ADMITTED": F2_SERIES, "READ_FROM_ISO": READ_FROM_ISO, "NOT_DECIDABLE_ISO": NOT_DECIDABLE_ISO}
    assert set(consts) == set(PATCHED_CONSTANTS)
    funcs = {}
    if variant is not None:
        funcs["cluster_bootstrap"] = _bootstrap_with_adj(F1_FUNCS["cluster_bootstrap"])
        funcs["evaluate_gate"] = _gate_with_adj(F1_FUNCS["evaluate_gate"])
        if variant == "A":
            funcs["decide_quote"] = _side_cap(F1_FUNCS["decide_quote"])
    try:
        for n, v in consts.items():
            setattr(sim, n, v)
        for n, v in funcs.items():
            setattr(sim, n, v)
        yield
    finally:
        for n, v in F1_CONSTANTS.items():
            setattr(sim, n, v)
        for n, v in F1_FUNCS.items():
            setattr(sim, n, v)


# ------------------------------------------------------------------------------------------------ labels and statements
def labels(variant, ctx):
    """The F2 labels merged into every output (gate.json, the marker, the parquet metadata)."""
    lab = LABELS[variant]
    return {"book": lab, "book_sim_internal": "A", "lip_f2_variant": lab,
            "book_label": f"LIP-F2 {lab} {NAMES[variant]}: gated formula screen (multiplicity-adjusted, m = {M_GATED})",
            "report_only": False, "gated": True, "promotable": False, "f2_rule": RULE_TEXT[variant],
            "f2_m_gated": M_GATED, "f2_adj_pct": ADJ_PCT, "f2_universe": list(F2_SERIES),
            "f2_freeze": ctx["freeze_iso"], "f2_declare_sha256": ctx["declare_sha256"],
            "f2_read_from": READ_FROM_ISO, "f2_not_decidable": NOT_DECIDABLE_ISO,
            "f2_registry_pins": dict(REGISTRY_PINS), "f2_lip_texts_sha256": dict(ctx["texts"]),
            "f2_sim_md5_pinned": SIM_MD5_PINNED, "f2_sim_tests_md5_pinned": SIM_TESTS_MD5_PINNED,
            "f2_sim_tests_md5_matches": sim.file_md5(Path(sim.__file__).with_name("test_kalshi_lip_sim.py")) == SIM_TESTS_MD5_PINNED,
            "f2_tool_md5": sim.file_md5(__file__), "f2_qp_review_sha256": QP_REVIEW_SHA256,
            "f2_lip_f1_gate": ctx.get("lip_f1_gate"), "f2_bookc_done": ctx.get("bookc_done"), "f2_notes": list(NOTES),
            "f2_statement_cohort": STATEMENT_COHORT.format(instant=ctx["freeze_iso"], first_day=first_unit_day(ctx["freeze_iso"]))}


def cells_of(gate):
    return gate.get("cells") or {}


def unit_days(gate):
    return sorted({d for c in cells_of(gate).values() for d in c["gate"]["G3"]["day_sums"]})


def g4b_clusters(gate):
    per = {c: v["gate"]["G4b"].get("clusters") for c, v in cells_of(gate).items()}
    vals = set(per.values())
    return next(iter(vals)) if len(vals) == 1 else per if per else 0


def read_statement(gate, ctx):
    lip_r = (ctx.get("lip_f1_gate") or {}).get("read_instant") or "n/a"
    days = ", ".join(unit_days(gate)) or "none"
    return STATEMENT_F2_READ.format(lip_r=lip_r, days=days, clusters=g4b_clusters(gate))


def final_verdict(gate):
    """The simulator's verdict with the multiplicity wording.  A pass is 'formula screen passed (multiplicity-adjusted)'.
    A variant whose every gate passes on the unadjusted bound but not on the adjusted one is
    'FAIL (multiplicity-adjusted)'.  Every other verdict is the simulator's own."""
    v = gate.get("verdict")
    cells = cells_of(gate)
    if v == "FORMULA_SCREEN_PASSED":
        return VERDICT_PASS
    if v == "FAIL" and cells and all(c["gate"].get("all_pass_unadjusted") for c in cells.values()):
        return VERDICT_FAIL_ADJ
    return v


def f2_table(gate):
    """table.txt: the simulator's table under an F2 header (the simulator prints 'LIP-F1 formula screen | Book A')."""
    base = sim.format_table(dict(gate, book=gate["book"])).split("\n")
    if not base[0].startswith("LIP-F1 formula screen | Book "):
        raise F2Refusal("the simulator's table header changed")
    head = [f"LIP-F2 formula screen | {gate['book']} {NAMES_BY_LABEL[gate['book']]} S={gate['size']:g} "
            f"I_max={gate['i_max']:g} | R={gate.get('read_instant')} | verdict={gate['verdict']} | "
            f"deciding={gate['deciding']}",
            "REPORT HEADER " + gate["book_label"],
            "rule: " + gate["f2_rule"],
            "statement (this read): " + gate["f2_statement_read"],
            "statement (carried by the LIP-F1 and Book C reads): " + gate["f2_statement_cohort"],
            f"deciding bound: G4 and G4b decide at the {ADJ_PCT:.4f}th percentile (5/m, m = {M_GATED}) of the same "
            f"1,000-draw matrix (RandomState(1)); p05 is shown beside it; a pass on p05 only is {VERDICT_FAIL_ADJ}"]
    tail = []
    for c, cg in cells_of(gate).items():
        gt = cg["gate"]
        if not gt["n_units"]:
            continue
        parts = []
        for k in ("G4", "G4b"):
            gk = gt[k]
            fmt = lambda x: "n/a" if x is None else format(x, ".4f")
            parts.append(f"{k} p05={fmt(gk['p05'])} p{ADJ_PCT:.3f}={fmt(gk.get('p_adj'))} (decides) "
                         f"{'pass' if gk['pass'] else 'fail'} (unadjusted {'pass' if gk.get('pass_unadjusted') else 'fail'})")
        tail.append(f"bound {c}: " + "; ".join(parts))
    return "\n".join(head + base[1:] + tail) + "\n"


NAMES_BY_LABEL = {LABELS[v]: NAMES[v] for v in VARIANTS}


# ------------------------------------------------------------------------------------------------ guards
def make_cfg(variant, *, freeze_us, **kw):
    """A ``sim.Cfg`` for an F2 variant (``variant`` None: the loader dry-run).  The F2 identity (size, I_max, freeze,
    registry pins, source rule, stress cells) cannot be passed in."""
    for k in ("book", "freeze_us", "registry_pins", "size", "imax", "force_source", "stress"):
        if k in kw:
            raise F2Refusal(f"{k} is fixed for F2 and cannot be passed")
    return sim.Cfg(book="A", size=SIZE, imax=IMAX[variant or "A"], freeze_us=freeze_us,
                   registry_pins=dict(REGISTRY_PINS), stress=True, **kw)


def check_cfg(cfg, variant, freeze_us):
    if cfg.book != "A" or float(cfg.size) != SIZE or float(cfg.imax) != IMAX[variant or "A"]:
        raise F2Refusal(f"F2 runs at S={SIZE:g}, I_max={IMAX[variant or 'A']:g} (book A internally)")
    if cfg.freeze_us != freeze_us:
        raise F2Refusal("the cfg freeze is not the F2 cohort freeze")
    if cfg.registry_pins != REGISTRY_PINS:
        raise F2Refusal("the registry pins are fixed (DECLARE-FORWARD-PINS-2)")
    if cfg.force_source is not None:
        raise F2Refusal("--force-source is refused for F2 (the frozen gap rule picks the source)")
    if not cfg.stress:
        raise F2Refusal("--no-stress is refused for F2")
    names = [Path(b).name for b in (cfg.books or [])]
    if not names or any(n not in ALLOWED_RECORDERS for n in names) or "obrec-1019c" not in names:
        raise F2Refusal(f"F2 reads obrec-1019c (first) and optionally obrec-1019d only (got {names})")
    if not cfg.dry_run:
        if variant is None:
            raise F2Refusal("--variant A|B is required without --dry-run")
        if not cfg.out:
            raise F2Refusal("--out is required without --dry-run")
        if (cfg.scan and cfg.read_us is not None) or (not cfg.scan and cfg.read_us is None):
            raise F2Refusal("give --read-instant R or --scan [--asof T], not both")
        if cfg.read_us is not None:
            r = cfg.read_us
            if r % (86_400 * sim.US) != 0:
                raise F2Refusal("R must be a 00:00:00Z instant")
            if r < sim.iso_to_us(READ_FROM_ISO) or r > sim.iso_to_us(NOT_DECIDABLE_ISO):
                raise F2Refusal(f"R must lie in [{READ_FROM_ISO}, {NOT_DECIDABLE_ISO}]")
            if r <= freeze_us:
                raise F2Refusal("the read instant must be after the F2 cohort freeze")


def _literal_prefix(item):
    parts = Path(str(item)).parts
    lit = []
    for p in parts:
        if any(c in p for c in "*?["):
            break
        lit.append(p)
    pre = Path(*lit) if lit else None
    return None if pre is None or pre == Path(pre.anchor) else pre


def _is_f2_json(path, variant):
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("lip_f2_variant") == LABELS[variant]


def check_out(out, variant, inputs=()):
    """--out may be new, empty, a dir without simulator files, or a previous output of the SAME variant.  It must not hold
    LIP-F1 / Book C / the other variant's simulator files and must not sit inside an input store."""
    out = Path(out)
    if out.exists() and not out.is_dir():
        raise F2Refusal(f"--out {out} exists and is not a directory")
    ro = out.resolve()
    for spec in inputs:
        for item in ([spec] if isinstance(spec, (str, Path)) else list(spec or [])):
            pre = _literal_prefix(item)
            if pre is not None and (ro == pre.resolve() or pre.resolve() in ro.parents):
                raise F2Refusal(f"--out {out} is inside the input store {pre}")
    if not out.exists():
        return
    marker = out / MARKER
    if marker.is_file() and not _is_f2_json(marker, variant):
        raise F2Refusal(f"--out {out} holds the marker of another F2 variant (or a foreign one)")
    held = [f for f in SIM_OUTPUT_FILES if (out / f).exists()]
    if not held:
        return
    mine = marker.is_file() and _is_f2_json(marker, variant)
    if mine and (out / "gate.json").exists():
        mine = _is_f2_json(out / "gate.json", variant)
    if not mine:
        raise F2Refusal(f"--out {out} already holds simulator files {held} that are not {LABELS[variant]} output; "
                        "each F2 variant writes to its own directory")


# ------------------------------------------------------------------------------------------------ the dry run
DRY_KEYS = ("units_by_status", "excl_no_meta_sub", "units_by_source", "inconsistent_snapshots", "rows_before_freeze_ignored",
            "unsealed_parts_excluded", "meta_captures", "meta_markets", "stores")
DRY_NOTE = ("loader counts only: units by status and source, snapshot consistency, unsealed parts, metadata counts and "
            "store states. No F2 rule is applied in this mode and nothing about the book content or any outcome is "
            "computed or printed.")


def dry_report(rep, ctx):
    """Whitelist of the simulator's dry-run report.  The simulator's 'opened' / 'not_opened' statuses come from the
    frozen quote rule, so they are collapsed into one 'passed_exclusions' count."""
    status = dict(rep.get("units_by_status") or {})
    passed = status.pop("opened", 0) + status.pop("not_opened", 0)
    if passed:
        status["passed_exclusions"] = passed
    out = {k: rep[k] for k in DRY_KEYS if k in rep}
    out["units_by_status"] = dict(sorted(status.items()))
    out.update({"mode": "dry_run", "note": DRY_NOTE, "book": "F2 cohort loader dry-run (no variant rule applied)",
                "f2_freeze": ctx["freeze_iso"], "f2_declare_sha256": ctx["declare_sha256"],
                "f2_universe": list(F2_SERIES), "f2_registry_pins": dict(REGISTRY_PINS),
                "f2_lip_texts_sha256": dict(ctx["texts"]), "f2_sim_md5_pinned": SIM_MD5_PINNED,
                "f2_tool_md5": sim.file_md5(__file__)})
    return out


# ------------------------------------------------------------------------------------------------ the run
def _stamp_parquet(out, lab):
    meta = {b"lip_f2_variant": lab["book"].encode(), b"book": lab["book"].encode(), b"report_only": b"false",
            b"promotable": b"false", b"f2_declare_sha256": lab["f2_declare_sha256"].encode()}
    for f in ("units.parquet", "fills.parquet", "days.parquet", "snaps.parquet"):
        p = Path(out) / f
        if p.exists():
            pq.write_table(pq.read_table(p).replace_schema_metadata(meta), p, compression="snappy")


def run_f2(cfg, variant, ctx):
    """Run the frozen simulator for one F2 variant.  Returns the simulator's gate dict with the F2 labels merged in; the
    files in cfg.out carry the same labels (gate.json, table.txt, the parquet metadata and the marker)."""
    check_sim_pin()
    check_cfg(cfg, variant, sim.iso_to_us(ctx["freeze_iso"]))
    if not cfg.dry_run:
        # the two process gates are checked again here, so no caller can reach the simulator without them
        ctx = dict(ctx, lip_f1_gate=check_lip_f1_gate((ctx.get("lip_f1_gate") or {}).get("path")),
                   bookc_done=check_bookc_done((ctx.get("bookc_done") or {}).get("path")))
    out = Path(cfg.out) if cfg.out else None
    if cfg.dry_run:
        with f2_cohort(ctx["freeze_iso"], None):
            rep = sim.run_sim(cfg)
        return dry_report(rep, ctx)
    lab = labels(variant, ctx)
    check_out(out, variant, [cfg.books, cfg.programs, cfg.meta, cfg.trades, cfg.markets, cfg.series])
    fresh_dir, had_marker = not out.exists(), (out / MARKER).exists()
    try:
        with f2_cohort(ctx["freeze_iso"], variant):
            out.mkdir(parents=True, exist_ok=True)
            (out / MARKER).write_text(_dump(dict(lab, files=list(SIM_OUTPUT_FILES))))
            res = sim.run_sim(cfg)
    except BaseException:
        # a refused / failed run leaves no marker-only directory behind (files the simulator wrote stay, labelled by the marker)
        if not any((out / f).exists() for f in SIM_OUTPUT_FILES):
            if not had_marker:
                (out / MARKER).unlink(missing_ok=True)
            if fresh_dir:
                with contextlib.suppress(OSError):
                    out.rmdir()
        raise
    # ---- relabel every output (outside the patch: the simulator is LIP-F1's again)
    res["verdict_simulator"] = res.get("verdict")
    res.update(lab)
    res["verdict"] = final_verdict(res)
    res["f2_statement_read"] = read_statement(res, ctx)
    g = out / "gate.json"
    if g.exists():
        gate = json.loads(g.read_text())
        gate.update(lab)
        gate["verdict_simulator"] = gate.get("verdict")
        gate["verdict"] = final_verdict(gate)
        gate["f2_statement_read"] = read_statement(gate, ctx)
        g.write_text(_dump(gate))
    (out / "table.txt").write_text(f2_table(res))
    _stamp_parquet(out, lab)
    return res


# ------------------------------------------------------------------------------------------------ CLI
def build_parser():
    p = argparse.ArgumentParser(
        prog="kalshi_lip_f2",
        description="LIP-F2 cohort: the frozen LIP-F1 simulator for F2-A (side cap 70c) or F2-B (I_max 25) on LIP-F1's "
                    "eight gas series. Outcome-computing modes need --lip-f1-gate and --bookc-done; --dry-run prints "
                    "loader counts only.")
    p.add_argument("--variant", choices=VARIANTS, help="A = F2-A SIDE-CAP, B = F2-B INV25 (not needed for --dry-run)")
    p.add_argument("--f2-declare", required=True, help="the frozen DECLARE-F2.md (its first line gives the cohort freeze)")
    p.add_argument("--f2-declare-sha256", required=True, help="sha256 of DECLARE-F2.md, as recorded in the F2 pins record")
    p.add_argument("--freeze", required=True, help="must equal the cohort freeze instant in DECLARE-F2.md")
    p.add_argument("--lip-texts-dir", default=DEFAULT_LIP_TEXTS_DIR,
                   help="directory holding the six inherited LIP-F1 texts (checked against DECLARE-FORWARD-PINS-1)")
    p.add_argument("--lip-f1-gate", help="LIP-F1's DECIDING gate.json (required unless --dry-run)")
    p.add_argument("--bookc-done", help="the Book C run-done marker or Book C's gate.json (required unless --dry-run)")
    p.add_argument("--out", help="output directory, one per variant (required unless --dry-run)")
    p.add_argument("--books", nargs="+", default=None, help="obrec dir(s): obrec-1019c first, optionally obrec-1019d; "
                   "default " + " ".join(DEFAULT_BOOKS))
    p.add_argument("--programs", default=None, help="lipsnap dir; default " + DEFAULT_PROGRAMS)
    p.add_argument("--meta", default=None, help="market metadata capture dir; default " + DEFAULT_META)
    p.add_argument("--trades", nargs="+", action="extend", help="run store(s) with trades; default '" + DEFAULT_RUNS + "'")
    p.add_argument("--markets", nargs="+", action="extend", help="run store(s) with markets; default '" + DEFAULT_RUNS + "'")
    p.add_argument("--series", default=None, help="kalshi root with series/; default " + DEFAULT_SERIES)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--read-instant", help="the read instant R (ISO, a 00:00Z instant from " + READ_FROM_ISO + ")")
    g.add_argument("--scan", action="store_true", help="advisory: find the first 00:00Z >= " + READ_FROM_ISO + " with counts met")
    p.add_argument("--asof", help="--scan: the latest candidate read instant (ISO)")
    p.add_argument("--dry-run", action="store_true", help="print loader counts only")
    p.add_argument("--strict-runs", action="store_true")
    p.add_argument("--mem-cap-mb", type=float, default=1500.0)
    p.add_argument("--debug-snaps", action="store_true")
    return p


def prepare(a):
    """Everything that can refuse before the simulator runs.  -> (variant, ctx, cfg)."""
    check_sim_pin()
    check_inherited()
    variant = a.variant
    if not a.dry_run and variant is None:
        raise F2Refusal("--variant A|B is required without --dry-run")
    texts, verify1 = verify_lip_texts(a.lip_texts_dir)
    declare_sha, freeze_iso = load_f2_declaration(a.f2_declare, a.f2_declare_sha256)
    try:
        given = sim.iso_to_us(a.freeze)
    except ValueError as e:
        raise F2Refusal(f"--freeze: {e}")
    if given != sim.iso_to_us(freeze_iso):
        raise F2Refusal(f"--freeze must be the cohort freeze {freeze_iso}")
    ctx = {"freeze_iso": freeze_iso, "declare_sha256": declare_sha, "texts": texts}
    if a.dry_run:
        if a.read_instant or a.scan or a.asof or a.out:
            raise F2Refusal("--dry-run takes no --read-instant / --scan / --asof / --out")
    else:
        # the process gates: LIP-F1 final, Book C done (QP L1 item 3)
        ctx["lip_f1_gate"] = check_lip_f1_gate(a.lip_f1_gate)
        ctx["bookc_done"] = check_bookc_done(a.bookc_done)
        if not (a.read_instant or a.scan):
            raise F2Refusal("give --read-instant R or --scan [--asof T]")
        if a.asof and not a.scan:
            raise F2Refusal("--asof is for --scan")
        if not a.out:
            raise F2Refusal("--out is required without --dry-run")
    read_us = None if not a.read_instant else sim.iso_to_us(a.read_instant)
    cfg = make_cfg(variant, freeze_us=sim.iso_to_us(freeze_iso),
                   books=a.books or list(DEFAULT_BOOKS), programs=a.programs or DEFAULT_PROGRAMS,
                   meta=a.meta or DEFAULT_META, trades=a.trades or [DEFAULT_RUNS], markets=a.markets or [DEFAULT_RUNS],
                   series=a.series or DEFAULT_SERIES, out=a.out, read_us=read_us,
                   asof_us=None if not a.asof else sim.iso_to_us(a.asof), scan=a.scan, dry_run=a.dry_run,
                   strict_runs=a.strict_runs, mem_cap_mb=a.mem_cap_mb, debug_snaps=a.debug_snaps,
                   units_evidence=verify1)
    check_cfg(cfg, variant, sim.iso_to_us(freeze_iso))
    return variant, ctx, cfg


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        variant, ctx, cfg = prepare(a)
    except (F2Refusal, sim.InputError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    try:
        res = run_f2(cfg, variant, ctx)
    except F2Refusal as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except (sim.InputError, kp.SealError, MemoryError, ValueError, KeyError, OSError) as e:
        print(f"refused: {type(e).__name__}: {e}", file=sys.stderr)
        return 4
    if cfg.dry_run:
        print(json.dumps(res, sort_keys=True, indent=1, default=str))
    else:
        print(f2_table(res), end="")
        print(f"peak RSS {res.get('_peak_rss_mb')} MB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
