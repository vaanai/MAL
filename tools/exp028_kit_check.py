"""EXP-028 kit equivalence check (Amendment 1, item 5): is a later `tools/arena_screen.py` kit the same screen-p estimator as the one quant-proof verified at 489fa7e?

NO PROFIT CLAIM. This tool measures nothing about any book. It reads no block, no wave result and no sealed path: its fixture is synthetic (seed 1).

Commands
  reference   Build the FIXED synthetic input set, call the kit's own p functions on it AT 489fa7e (numpy 2.5.3 only) and write the outputs canonically
              (sorted keys, repr floats) to tools/fixtures/exp028/p_reference_489fa7e.json, with numpy.__version__, the git head, git_dirty and the kit sha256.
  check       --sha <kit sha> [--results results.json ...]. A JSON record with a PASS/FAIL verdict for each of
                (A) source identity of the estimator closure at <sha> vs 489fa7e (`ast.dump` per top-level name, fail closed on an unresolved reference);
                (B) the fixed set re-run in a temp worktree at <sha>, compared byte for byte with the reference file, numpy version recorded and required equal;
                (C) with --results: per cell, the 8 first-1,000 flags literally true, B == 10000 and seed == 1 everywhere, and each sub-cell's n, k_dates and xbar
                    exactly equal to the kill's stats n, days and mean. Only booleans and counts are printed; a mean, total, p or kill value is compared silently.
              The exit code is 0 whenever a record is produced (read the verdicts); a refusal (bad sha, wrong numpy for `reference`) exits 2.

Scope notes (read before trusting a PASS)
  * (A) follows kit-defined names transitively through tools/*.py (module attributes such as F.x / P.x and `from tools.x import y`). A referenced name it cannot resolve
    to a binding fails (A) closed. `legs` is the ONE named boundary: quant-proof (#761 comment 6278219338, item 5 (C)) says legs() is pricing, covered by (C), and
    "does not need to be unchanged". By default it is listed in the record (both hashes, `equal`) and not followed; `--strict-pricing` follows it like any other name.
  * (B) calls screen_p_sub / screen_p_check / stat / boot_lo directly and screen_p_cell with `legs` replaced by a stub that returns the fixture's vectors (the real legs()
    is pricing, item (C)); the stub's call arguments are part of the compared output. The kit's own `tools/test_arena_screen.py` screen-p tests (item 5 (B), first bullet)
    are NOT run by this tool.
  * The fixture is generated with the standard-library `random.Random` (string seed per case, only `.random()` and `.randrange()`) and integer lamports, so it does
    not depend on numpy's stream; `inputs_sha256` in the reference detects any drift of the generator itself.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import contextlib
import hashlib
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_REFERENCE = "exp028_kit_reference_v1"
SCHEMA_RECORD = "exp028_kit_check_v1"
REFERENCE_SHA = "489fa7eb5d0f4abdd18dde6acf3bb4f9da6c7ac6"
REFERENCE_ARENA_SCREEN_SHA256 = "00dedd1b8620ab059fb9509e1645e2fc63f44c339b0ee16462c90d10a1bea4bb"  # EXP-028 Amendment 1 item 5, "Reference"
REQUIRED_NUMPY = "2.5.3"
REFERENCE_REL = "tools/fixtures/exp028/p_reference_489fa7e.json"
SEED = 1
B_FIXED = 10_000  # B_E, EXP-028 section 3 item 3

ARENA_SCREEN, ARENA_STACK = "tools/arena_screen.py", "tools/arena_stack.py"
# QP's 22 names, verbatim from EXP-028 Amendment 1 item 5 (A) ("SCREEN_P_B_MIN`/`SCREEN_P_B_MAX" is two names). The test compares this tuple with the amendment text.
LISTED: tuple[tuple[str, tuple[str, ...]], ...] = (
    (ARENA_SCREEN, ("screen_p_check", "_boot_means", "_cluster_ratios", "_tails", "screen_p_sub", "_max_p", "screen_p_cell", "stat", "boot_lo", "SCREEN_P_B",
                    "SCREEN_P_B_MIN", "SCREEN_P_B_MAX", "SCREEN_P_SEED", "STAKE_SOL", "LAT_DECIDING", "RENT_DECIDING", "STRAND_RULES", "FAILS")),
    (ARENA_STACK, ("boot_lo", "BOOT_DRAWS", "BOOT_SEED", "BOOT_PCT")),
)
LISTED_KEYS = frozenset((p, n) for p, ns in LISTED for n in ns)
PRICING_BOUNDARY = frozenset({(ARENA_SCREEN, "legs")})  # QP item 5 (C): pricing, covered by (C); see the module docstring
KIT_FILES = (ARENA_SCREEN, ARENA_STACK)
RULES = ("sc", "total")
FAILS = ("flat", "press")


class ToolError(Exception):
    """A refusal or an unusable input: exit code 2, no record."""


# ------------------------------------------------------------------------------------------------------------------------------ canonical json
def _plain(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if o is None or isinstance(o, (bool, str)):
        return o
    if isinstance(o, int):
        return int(o)
    if isinstance(o, float):
        return float(o) if math.isfinite(o) else repr(float(o))
    if hasattr(o, "item"):  # a numpy scalar
        return _plain(o.item())
    raise TypeError(f"not canonical: {type(o).__name__}")


def canon(o: Any) -> str:
    """Sorted keys, repr floats (Python's json writes float.__repr__), ASCII, one trailing newline."""
    return json.dumps(_plain(o), sort_keys=True, indent=1, ensure_ascii=True) + "\n"


def sha256_hex(b: bytes | str) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ git helpers
def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise ToolError(f"git {' '.join(args)} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def resolve_sha(repo: Path, rev: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise ToolError(f"{rev!r} is not a commit in {repo} (fetch it first: git fetch origin <sha> or pull/<N>/head)")
    return r.stdout.strip()


def git_show(repo: Path, sha: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def git_file_sha256(repo: Path, sha: str, path: str) -> str | None:
    """sha256 of the file's bytes at `sha` (what `git show <sha>:<path> | sha256sum` prints)."""
    r = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{path}"], capture_output=True)
    return hashlib.sha256(r.stdout).hexdigest() if r.returncode == 0 else None


@contextlib.contextmanager
def worktree(repo: Path, sha: str, workdir: str | None = None) -> Iterator[Path]:
    """A detached temp worktree at `sha`, removed on exit."""
    parent = Path(tempfile.mkdtemp(prefix="exp028_kit_", dir=workdir))
    wt = parent / "kit"
    try:
        git(repo, "worktree", "add", "--detach", str(wt), sha)
        yield wt
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        shutil.rmtree(parent, ignore_errors=True)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)


# ------------------------------------------------------------------------------------------------------------------------------ the fixed synthetic input set
def _rng(name: str) -> random.Random:
    return random.Random(f"{SEED}/{name}")  # a string seed is hashed with SHA-512 (seed version 2): stable across Python versions


def _normal(r: random.Random, n: int, mu: float, sd: float) -> list[int]:
    return [int(round(mu + sd * (sum(r.random() for _ in range(12)) - 6.0))) for _ in range(n)]  # Irwin-Hall: + and * only, integer lamports


def _skewed(r: random.Random, n: int, scale: float, shift: float) -> list[int]:
    out = []
    for _ in range(n):
        u = r.random()
        out.append(int(round(scale * (u * u * u * u) - shift)))  # a long right tail, no libm
    return out


def _days(r: random.Random, n: int, k: int, base: int = 20_000) -> list[int]:
    head = [base + i for i in range(min(n, k))]
    return head + [base + r.randrange(k) for _ in range(max(0, n - k))]


def build_inputs() -> dict[str, Any]:
    """Every input of the fixed set as plain Python (ints, lists, strings, None). Pure function of SEED and the numbers below."""
    sub: dict[str, dict] = {}

    def add(name: str, lam: list, day: list, B: int = B_FIXED, ci: str | None = None, note: str = "") -> None:
        sub[name] = {"lam": lam, "day": day, "B": B, "ci": ci, "note": note}

    r = _rng("n0_empty"); add("n0_empty", [], [], note="n = 0: p None")
    add("n1_single", [15_000_000], [20_000], note="n = 1: percentile/null None, date-cluster None")
    r = _rng("nan_attempt"); lam = _normal(r, 120, 2_000_000, 1.0e7); day = _days(r, 120, 5)
    add("nan_attempt", lam[:60] + [None] + lam[61:], day, note="one NaN attempt (None here, NaN in the worker): p None")
    r = _rng("inf_attempt"); lam = _normal(r, 60, 2_000_000, 1.0e7)
    add("inf_attempt", lam[:10] + ["inf"] + lam[11:], _days(r, 60, 4), note="one +inf attempt: p None")
    r = _rng("one_date"); add("one_date", _normal(r, 100, 3_000_000, 1.0e7), [20_000] * 100, note="one UTC date: date-cluster None, trade-level present")
    add("n2_both_positive", [20_000_000, 30_000_000], [20_000, 20_001], note="n = 2, both positive: 1/10001 floor on both resamplers (QP)")
    add("five_equal_positives", [10_000_000] * 5, [20_000 + i for i in range(5)], note="5 equal positives: 1/10001 floor (QP)")
    add("two_equal_one_date", [10_000_000] * 2, [20_000, 20_000], note="constant resample, one date")
    r = _rng("negative_mean_400_8"); add("negative_mean_400_8", _normal(r, 400, -3_000_000, 1.0e7), _days(r, 400, 8), note="negative mean")
    r = _rng("right_skewed_300_7"); add("right_skewed_300_7", _skewed(r, 300, 1.0e8, 1.5e7), _days(r, 300, 7), note="right-skewed 300 attempts / 7 dates (QP)")
    r = _rng("near_bar_300_7"); add("near_bar_300_7", _normal(r, 300, 1_900_000, 1.0e7), _days(r, 300, 7), note="positive mean, tail counts in the tens (near the EXP-028 bar)")
    r = _rng("mid_300_7"); add("mid_300_7", _normal(r, 300, 1_200_000, 1.0e7), _days(r, 300, 7), note="positive mean, mid-range p")
    r = _rng("all_positive_300_6"); add("all_positive_300_6", [abs(v) + 1 for v in _normal(r, 300, 5_000_000, 3.0e6)], _days(r, 300, 6), note="all positive")
    r = _rng("all_negative_300_6"); add("all_negative_300_6", [-abs(v) - 1 for v in _normal(r, 300, 5_000_000, 3.0e6)], _days(r, 300, 6), note="all negative")
    r = _rng("zeros_50_5"); add("zeros_50_5", [0] * 50, _days(r, 50, 5), note="mean exactly 0 (H0 itself), every attempt tied")
    r = _rng("symmetric_zero_mean_200_5"); add("symmetric_zero_mean_200_5", [20_000_000, -20_000_000] * 100, _days(r, 200, 5), note="mean exactly 0, ties in the draws")
    r = _rng("ties_three_values_240_12"); add("ties_three_values_240_12", [(-10_000_000, 0, 0, 10_000_000)[r.randrange(4)] for _ in range(240)], _days(r, 240, 12), note="three values, many ties")
    r = _rng("small_n_20_4"); add("small_n_20_4", _normal(r, 20, 4_000_000, 1.0e7), _days(r, 20, 4), note="n below the gate's MIN_N and k below MIN_DAYS")
    r = _rng("large_n_3000_30"); add("large_n_3000_30", _normal(r, 3000, 600_000, 2.0e7), _days(r, 3000, 30), note="large n, many dates")
    r = _rng("many_dates_240_60"); add("many_dates_240_60", _normal(r, 240, 2_000_000, 1.0e7), _days(r, 240, 60), note="many dates")
    r = _rng("two_dates_30_2"); add("two_dates_30_2", _normal(r, 30, 5_000_000, 1.0e7), _days(r, 30, 2), note="the smallest date-cluster that has a p")
    r = _rng("skewed_B_min"); add("skewed_B_min", _skewed(r, 300, 1.0e8, 1.5e7), _days(r, 300, 7), B=1_000, note="B at its minimum (1,000)")
    r = _rng("ci_match"); lam = _skewed(r, 300, 1.0e8, 1.5e7); add("ci_match", lam, _days(r, 300, 7), ci="match", note="stat()'s own bounds passed: both flags True")
    r = _rng("ci_perturbed"); lam = _skewed(r, 300, 1.0e8, 1.5e7); add("ci_perturbed", lam, _days(r, 300, 7), ci="perturbed", note="stat()'s bounds + 1e-12: both flags False")

    cells: dict[str, dict] = {}

    def vecs(name: str, n: int, k: int, mus: tuple, sd: float = 1.0e7) -> dict:
        r = _rng(name)
        day = _days(r, n, k)
        return {"day": day, "vec": {f"{rule}|{fail}": _normal(r, n, mus[i], sd) for i, (rule, fail) in enumerate((a, b) for a in RULES for b in FAILS)}}

    c = vecs("cell_ok_300_7", 300, 7, (4_000_000, 3_000_000, 5_000_000, 2_000_000)); c.update(stats="match", B=B_FIXED, note="all 8 flags True"); cells["cell_ok_300_7"] = c
    c = vecs("cell_flag_false", 300, 7, (4_000_000, 3_000_000, 5_000_000, 2_000_000)); c.update(stats="perturb_sc_press", B=B_FIXED, note="sc|press trade-level flag False"); cells["cell_flag_false"] = c
    c = vecs("cell_no_stats", 300, 7, (4_000_000, 3_000_000, 5_000_000, 2_000_000)); c.update(stats="none", B=B_FIXED, note="stats None: all 8 flags None"); cells["cell_no_stats"] = c
    cells["cell_n0"] = {"day": [], "vec": None, "stats": "none", "B": B_FIXED, "note": "st None: the no-attempt path, every p None"}
    c = vecs("cell_nan_one_sub", 200, 6, (4_000_000, 3_000_000, 5_000_000, 2_000_000)); c["vec"]["total|press"][7] = None
    c.update(stats="none", B=B_FIXED, note="one NaN in total|press: that sub-cell and the cell p None, the others present"); cells["cell_nan_one_sub"] = c
    c = vecs("cell_small_20_3", 20, 3, (6_000_000, 5_000_000, 7_000_000, 4_000_000)); c.update(stats="match", B=B_FIXED, note="n = 20, 3 dates"); cells["cell_small_20_3"] = c
    c = vecs("cell_one_date", 150, 1, (4_000_000, 3_000_000, 5_000_000, 2_000_000)); c.update(stats="match", B=B_FIXED, note="one date: every date-cluster p None"); cells["cell_one_date"] = c
    c = vecs("cell_B_min", 300, 7, (4_000_000, 3_000_000, 5_000_000, 2_000_000)); c.update(stats="match", B=1_000, note="B = 1,000"); cells["cell_B_min"] = c

    refusals = [[0.25, 10_000], [0.25, 1_000], [0.25, 1_000_000], [0.5, 10_000], [1.0, 10_000], [0.25, 999], [0.25, 1_000_001], [0.25, 10_000.0], [True, 10_000], [0.25, True]]
    return {"seed": SEED, "B": B_FIXED, "sub": sub, "cells": cells, "refusals": refusals}


def inputs_sha256() -> str:
    return sha256_hex(canon(build_inputs()))


# ------------------------------------------------------------------------------------------------------------------------------ the worker: runs INSIDE the kit root
def fixture_outputs(kit_root: Path) -> dict[str, Any]:
    """Import the kit from `kit_root` ONLY and call its own p functions on the fixed set. Returns the outputs plus the environment."""
    import numpy as np  # the kit's own numpy (the interpreter that runs this worker)

    kit_root = kit_root.resolve()
    sys.path.insert(0, str(kit_root))
    import importlib

    S = importlib.import_module("tools.arena_screen")
    F = importlib.import_module("tools.arena_stack")
    for m in (S, F):
        if not Path(m.__file__).resolve().is_relative_to(kit_root):
            raise ToolError(f"{m.__name__} was imported from {m.__file__}, not from {kit_root}")
    inp = build_inputs()

    def arr(v: list) -> Any:
        return np.array([float("nan") if x is None else float(x) for x in v], float)

    def stat_of(lam: Any, day: Any) -> dict | None:
        return S.stat(lam, np.asarray(day, np.int64), np.zeros(len(lam), np.int64), ("blk",))

    out: dict[str, Any] = {}
    out["constants"] = {k: getattr(S, k) for k in ("SCREEN_P_B", "SCREEN_P_B_MIN", "SCREEN_P_B_MAX", "SCREEN_P_SEED", "STAKE_SOL", "LAT_DECIDING", "RENT_DECIDING", "STRAND_RULES", "FAILS")}
    out["constants"].update({"stack." + k: getattr(F, k) for k in ("BOOT_DRAWS", "BOOT_SEED", "BOOT_PCT")})

    chk = []
    for stake, draws in inp["refusals"]:
        try:
            chk.append({"in": [stake, draws], "out": S.screen_p_check(stake, draws)})
        except S.Refused as exc:
            chk.append({"in": [stake, draws], "refused": f"{type(exc).__name__}: {exc}"})
    out["screen_p_check"] = chk

    sub_out: dict[str, Any] = {}
    stat_out: dict[str, Any] = {}
    boot_out: dict[str, Any] = {"arena_stack.boot_lo(empty)": F.boot_lo([]), "arena_screen.boot_lo(empty)": S.boot_lo(np.empty(0), np.empty(0))}
    for name, c in inp["sub"].items():
        lam = arr([float("inf") if x == "inf" else x for x in c["lam"]])
        day = np.array(c["day"], np.int64)
        kw: dict[str, Any] = {}
        if len(lam):
            st = stat_of(lam, day)
            if c["ci"] in ("match", "perturbed"):
                d = 1e-12 if c["ci"] == "perturbed" else 0.0
                kw = {"ci_lo_trade": st["ci_lo_trade"] + d, "ci_lo_date": st["ci_lo"] + d}
            if name in ("right_skewed_300_7", "negative_mean_400_8", "ci_match", "many_dates_240_60"):
                stat_out[name] = st
                boot_out[name] = {"arena_stack.boot_lo": F.boot_lo(lam / 1e9), "arena_screen.boot_lo": st["ci_lo"]}
        sub_out[name] = S.screen_p_sub(lam, day, B=c["B"], **kw)
    stat_out["empty"] = stat_of(np.empty(0), np.empty(0, np.int64))
    out["screen_p_sub"], out["stat"], out["boot_lo"] = sub_out, stat_out, boot_out

    cell_out: dict[str, Any] = {}
    real_legs = S.legs
    for name, c in inp["cells"].items():
        calls: list = []
        vec = None if c["vec"] is None else {k: arr(v) for k, v in c["vec"].items()}

        def legs_stub(st, stake, lat, rule, cfg, intercept=None, _vec=vec, _calls=calls):
            _calls.append([stake, lat, rule, intercept])
            return {"flat": _vec[f"{rule}|flat"], "press": _vec[f"{rule}|press"]}

        S.legs = legs_stub  # screen_p_cell reads the module-global `legs`: the fixture's vectors stand in for the pricing (QP item 5 (C))
        try:
            if vec is None:
                st, stats = None, None
            else:
                day = np.array(c["day"], np.int64)
                st = {"ref": np.zeros(len(day)), "T": day * 86400 + 3600}
                stats = None
                if c["stats"] != "none":
                    stats = {f"{S.LAT_DECIDING}|{rule}|rent0|{fail}": stat_of(vec[f"{rule}|{fail}"], day) for rule in RULES for fail in FAILS}
                    if c["stats"] == "perturb_sc_press":
                        stats[f"{S.LAT_DECIDING}|sc|rent0|press"]["ci_lo_trade"] += 1e-12
            res = S.screen_p_cell(st, None, stats, B=c["B"])
        finally:
            S.legs = real_legs
        cell_out[name] = {"screen_p": res, "legs_calls": calls, "stats": stats}
    out["screen_p_cell"] = cell_out
    try:
        S.screen_p_cell(None, None, None, B=1_000, stake_sol=1.0)
        out["screen_p_cell_refusal"] = "NOT REFUSED"
    except S.Refused as exc:
        out["screen_p_cell_refusal"] = f"{type(exc).__name__}: {exc}"

    descr = {n: {"n": len(c["lam"]), "k_dates": len(set(c["day"])), "B": c["B"], "note": c["note"]} for n, c in inp["sub"].items()}
    descr_cells = {n: {"n": len(c["day"]), "k_dates": len(set(c["day"])), "B": c["B"], "note": c["note"]} for n, c in inp["cells"].items()}
    return {"numpy_version": np.__version__, "python_version": sys.version.split()[0], "inputs_sha256": inputs_sha256(), "outputs": out,
            "fixture": {"seed": SEED, "B": B_FIXED, "sub_cases": descr, "cell_cases": descr_cells}}


def run_worker(kit_root: Path, timeout: int = 900) -> dict[str, Any]:
    """Run the fixed set in a fresh interpreter whose only kit on sys.path is `kit_root` (-I: no script dir, no PYTHON* env; -B: write no bytecode into the worktree)."""
    r = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "worker", "--kit-root", str(kit_root)], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise ToolError("worker failed: " + (r.stderr.strip().splitlines() or ["(no stderr)"])[-1][:400])
    try:
        return json.loads(r.stdout)
    except ValueError as exc:
        raise ToolError(f"worker output is not json: {exc}")


# ------------------------------------------------------------------------------------------------------------------------------ reference
def file_sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cmd_reference(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    out = Path(args.out)
    with contextlib.ExitStack() as stack:
        kit_root = Path(args.kit_root).resolve() if args.kit_root else stack.enter_context(worktree(repo, resolve_sha(repo, REFERENCE_SHA), args.workdir))
        head = git(kit_root, "rev-parse", "HEAD").strip()
        if head != REFERENCE_SHA:
            raise ToolError(f"refused: the kit root is at {head}, the reference is computed only at {REFERENCE_SHA}")
        dirty = bool(git(kit_root, "status", "--porcelain").strip())
        if dirty:
            raise ToolError("refused: the kit root has uncommitted changes (git_dirty); the reference is computed on the clean commit")
        kit_hashes = {p: file_sha256(kit_root / p) for p in KIT_FILES}
        if kit_hashes[ARENA_SCREEN] != REFERENCE_ARENA_SCREEN_SHA256:
            raise ToolError(f"refused: tools/arena_screen.py sha256 {kit_hashes[ARENA_SCREEN]} is not the amendment's {REFERENCE_ARENA_SCREEN_SHA256}")
        w = run_worker(kit_root)
    if w["numpy_version"] != REQUIRED_NUMPY:
        raise ToolError(f"refused: numpy {w['numpy_version']} (the reference is computed under numpy {REQUIRED_NUMPY} only; use /data/mal/venv/bin/python)")
    doc = {"schema": SCHEMA_REFERENCE, "kit_sha": REFERENCE_SHA, "git_head": head, "git_dirty": dirty, "kit_files_sha256": kit_hashes, "numpy_version": w["numpy_version"],
           "python_version": w["python_version"], "fixture": {**w["fixture"], "inputs_sha256": w["inputs_sha256"]}, "outputs": w["outputs"],
           "outputs_sha256": sha256_hex(canon(w["outputs"])),
           "note": "EXP-028 Amendment 1 item 5 (B) reference: screen_p_* outputs on the fixed synthetic set, computed once at the kit sha under numpy " + REQUIRED_NUMPY +
                   ". Synthetic inputs only; no real data, no profit claim."}
    text = canon(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    side = out.with_name(out.name + ".sha256")
    side.write_text(f"{sha256_hex(text)}  {out.name}\n")
    print(json.dumps({"written": str(out), "sha256": sha256_hex(text), "numpy_version": w["numpy_version"], "git_head": head, "git_dirty": dirty,
                      "arena_screen_sha256": kit_hashes[ARENA_SCREEN], "inputs_sha256": w["inputs_sha256"], "outputs_sha256": doc["outputs_sha256"]}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------------------------------------ (A) source identity
class _Mod:
    """One parsed kit module: its top-level bindings, import forms and (kit) module aliases."""

    def __init__(self, path: str, src: str, exists: Any):
        self.path = path
        self.tree = ast.parse(src)
        self.exists = exists
        self.future_annotations = any(isinstance(s, ast.ImportFrom) and s.module == "__future__" and any(a.name == "annotations" for a in s.names) for s in self.tree.body)
        self.bind: dict[str, list[ast.stmt]] = {}
        self.imp: dict[str, tuple] = {}  # name -> (kind, module, level, orig)
        self.self_alias: set[str] = set()
        self.has_star = False
        for stmt in self.tree.body:
            for nm in self._bound(stmt):
                self.bind.setdefault(nm, []).append(stmt)
            if isinstance(stmt, ast.Import):
                for a in stmt.names:
                    self.imp[a.asname or a.name.split(".")[0]] = ("import", a.name, 0, None, a.asname is not None)
            elif isinstance(stmt, ast.ImportFrom):
                for a in stmt.names:
                    if a.name == "*":
                        self.has_star = True
                    else:
                        self.imp[a.asname or a.name] = ("from", stmt.module or "", stmt.level, a.name, True)
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and ast.dump(stmt.value) == ast.dump(ast.parse("sys.modules[__name__]").body[0].value):
                self.self_alias.add(stmt.targets[0].id)

    @staticmethod
    def _targets(t: ast.AST, out: list[str]) -> None:
        if isinstance(t, ast.Name):
            out.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                _Mod._targets(e, out)
        elif isinstance(t, ast.Starred):
            _Mod._targets(t.value, out)

    @classmethod
    def _bound(cls, stmt: ast.stmt) -> list[str]:
        out: list[str] = []
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                cls._targets(t, out)
        elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
            cls._targets(stmt.target, out)
        elif isinstance(stmt, ast.Import):
            out += [a.asname or a.name.split(".")[0] for a in stmt.names]
        elif isinstance(stmt, ast.ImportFrom):
            out += [a.asname or a.name for a in stmt.names if a.name != "*"]
        elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.AsyncFor, ast.AsyncWith)) or type(stmt).__name__ in ("Match", "TryStar"):
            todo = [stmt]
            while todo:
                n = todo.pop()
                for ch in ast.iter_child_nodes(n):
                    if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out.append(ch.name)
                    elif isinstance(ch, ast.Name) and isinstance(ch.ctx, ast.Store):
                        out.append(ch.id)
                    elif isinstance(ch, (ast.Import, ast.ImportFrom)):
                        out += [a.asname or a.name.split(".")[0] for a in ch.names if a.name != "*"]
                        continue
                    elif isinstance(ch, ast.ExceptHandler) and ch.name:
                        out.append(ch.name)
                    todo.append(ch)
        return out

    def node_text(self, stmt: ast.stmt, name: str) -> str:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):  # per alias, so adding an unrelated name to an import line is not a difference
            kind, mod, lvl, orig, _ = self.imp.get(name, ("?", "", 0, None, False))
            return f"import|{kind}|{mod}|{lvl}|{orig}|{name}"
        return ast.dump(stmt)

    def name_hash(self, name: str) -> str:
        return sha256_hex("\n".join(self.node_text(s, name) for s in self.bind[name]))

    def kit_module_for(self, name: str) -> str | None:
        """If `name` is an import alias of a kit module, its path (tools/x.py); self-aliases (F = sys.modules[__name__]) give this file."""
        if name in self.self_alias:
            return self.path
        i = self.imp.get(name)
        if i is None:
            return None
        kind, mod, lvl, orig, _ = i
        if kind == "from" and (mod == "tools" or (lvl >= 1 and mod == "")):
            cand = f"tools/{orig}.py"
            return cand if self.exists(cand) else None
        if kind == "import" and mod.startswith("tools.") and i[4]:
            cand = "tools/" + mod[len("tools."):].replace(".", "/") + ".py"
            return cand if self.exists(cand) else None
        return None

    def kit_name_target(self, name: str) -> tuple[str, str] | None:
        """If `name` is `from tools.x import y [as name]` (or relative), the (path, y) it names."""
        i = self.imp.get(name)
        if i is None or i[0] != "from":
            return None
        _, mod, lvl, orig, _ = i
        if mod.startswith("tools.") or (lvl >= 1 and mod):
            sub = mod[len("tools."):] if mod.startswith("tools.") else mod
            cand = "tools/" + sub.replace(".", "/") + ".py"
            return (cand, orig) if self.exists(cand) else None
        return None

    def unsupported_kit_import(self, name: str) -> bool:
        i = self.imp.get(name)
        return bool(i and i[0] == "import" and i[1].startswith("tools.") and not i[4])  # `import tools.x` binds `tools`: a form this check does not follow

    def refs(self, stmt: ast.stmt) -> tuple[set[str], set[str], set[tuple[str, str]]]:
        skip: set[int] = set()
        if self.future_annotations:  # never evaluated; the annotation text is still part of the node's hash
            for n in ast.walk(stmt):
                anns = []
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = n.args
                    anns = [x.annotation for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg] if x is not None and x.annotation is not None]
                    if n.returns is not None:
                        anns.append(n.returns)
                elif isinstance(n, ast.AnnAssign):
                    anns = [n.annotation]
                for an in anns:
                    skip.update(id(x) for x in ast.walk(an))
        loads: set[str] = set()
        stores: set[str] = set()
        attrs: set[tuple[str, str]] = set()
        for n in ast.walk(stmt):
            if id(n) in skip:
                continue
            if isinstance(n, ast.Name):
                (stores if isinstance(n.ctx, ast.Store) else loads).add(n.id)
            elif isinstance(n, ast.arg):
                stores.add(n.arg)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stores.add(n.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                stores.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                stores.update(a.asname or a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                attrs.add((n.value.id, n.attr))
        return loads, stores, attrs


class Closure:
    """The kit-defined names reachable from the listed roots at one sha."""

    def __init__(self, repo: Path, sha: str, strict_pricing: bool):
        self.repo, self.sha = repo, sha
        self.boundary_names = frozenset() if strict_pricing else PRICING_BOUNDARY
        self._mods: dict[str, _Mod | None] = {}
        self._exists: dict[str, bool] = {}
        self.compared: dict[tuple[str, str], str] = {}
        self.boundary: dict[tuple[str, str], str] = {}
        self.unresolved: set[str] = set()
        self._walk()

    def exists(self, path: str) -> bool:
        if path not in self._exists:
            self._exists[path] = subprocess.run(["git", "-C", str(self.repo), "cat-file", "-e", f"{self.sha}:{path}"], capture_output=True).returncode == 0
        return self._exists[path]

    def mod(self, path: str) -> _Mod | None:
        if path not in self._mods:
            src = git_show(self.repo, self.sha, path)
            self._mods[path] = None if src is None else _Mod(path, src, self.exists)
        return self._mods[path]

    def _walk(self) -> None:
        todo = deque(LISTED_KEYS)
        seen: set[tuple[str, str]] = set()
        while todo:
            key = todo.popleft()
            if key in seen:
                continue
            seen.add(key)
            path, name = key
            m = self.mod(path)
            if m is None:
                self.unresolved.add(f"{path}:{name} (file not found at the sha)")
                continue
            if name not in m.bind:
                self.unresolved.add(f"{path}:{name} (no top-level binding)")
                continue
            if key in self.boundary_names:
                self.boundary[key] = m.name_hash(name)
                continue
            self.compared[key] = m.name_hash(name)
            for stmt in m.bind[name]:
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    tgt = m.kit_name_target(name)
                    if tgt:
                        todo.append(tgt)
                    continue
                loads, stores, attrs = m.refs(stmt)
                for nm in loads:
                    if nm in m.bind:
                        if m.unsupported_kit_import(nm):
                            self.unresolved.add(f"{path}: `import tools.x` form of {nm} is not followed")
                        todo.append((path, nm))
                    elif nm in vars(builtins) or nm in stores:
                        continue
                    else:
                        self.unresolved.add(f"{path}:{name} references {nm!r}, which has no binding in {path}")
                for base, attr in attrs:
                    if base in m.bind and not (attr.startswith("__") and attr.endswith("__")):
                        tgt_mod = m.kit_module_for(base)
                        if tgt_mod is not None:
                            todo.append((tgt_mod, attr))


def check_a(repo: Path, sha: str, ref_sha: str, strict_pricing: bool = False) -> dict[str, Any]:
    cs, cr = Closure(repo, sha, strict_pricing), Closure(repo, ref_sha, strict_pricing)
    keys = sorted(set(cs.compared) | set(cr.compared))
    rows, differs = [], []
    for k in keys:
        a, b = cs.compared.get(k), cr.compared.get(k)
        eq = a is not None and a == b
        rows.append({"file": k[0], "name": k[1], "listed": k in LISTED_KEYS, "sha256_at_sha": a, "sha256_at_ref": b, "equal": eq})
        if not eq:
            differs.append(f"{k[0]}:{k[1]}")
    bkeys = sorted(set(cs.boundary) | set(cr.boundary))
    boundary = [{"file": k[0], "name": k[1], "sha256_at_sha": cs.boundary.get(k), "sha256_at_ref": cr.boundary.get(k), "equal": cs.boundary.get(k) is not None and cs.boundary.get(k) == cr.boundary.get(k),
                 "covered_by": "(C) and the md5 replay rule (QP #761 comment 6278219338 item 5 (C)); not compared by (A)"} for k in bkeys]
    missing_listed = sorted(f"{p}:{n}" for p, n in LISTED_KEYS if (p, n) not in cs.compared and (p, n) not in cs.boundary or (p, n) not in cr.compared and (p, n) not in cr.boundary)
    unresolved = sorted(cs.unresolved | cr.unresolved)
    ok = not differs and not unresolved and not missing_listed
    files = {p: {"sha256_at_sha": git_file_sha256(repo, sha, p), "sha256_at_ref": git_file_sha256(repo, ref_sha, p)} for p in KIT_FILES}
    return {"verdict": "PASS" if ok else "FAIL", "sha": sha, "ref_sha": ref_sha, "strict_pricing": strict_pricing, "listed_names": len(LISTED_KEYS), "compared_names": len(rows), "kit_files": files,
            "unlisted_transitive_names": [f"{r['file']}:{r['name']}" for r in rows if not r["listed"]], "differing": differs, "unresolved_references": unresolved,
            "listed_names_missing": missing_listed, "boundary_not_compared": boundary, "compared": rows}


# ------------------------------------------------------------------------------------------------------------------------------ (B) byte-identical outputs
def reference_problems(ref: dict, repo: Path, ref_sha: str) -> list[str]:
    p = []
    if ref.get("schema") != SCHEMA_REFERENCE:
        p.append("schema")
    if ref.get("numpy_version") != REQUIRED_NUMPY:
        p.append(f"reference numpy_version is not {REQUIRED_NUMPY}")
    if ref.get("git_dirty") is not False:
        p.append("reference git_dirty is not false")
    if ref_sha == REFERENCE_SHA and ref.get("git_head") != REFERENCE_SHA:
        p.append("reference git_head is not the pinned kit sha")
    if ref.get("outputs_sha256") != sha256_hex(canon(ref.get("outputs"))):
        p.append("outputs_sha256 does not match the outputs")
    for path in KIT_FILES:
        src = git_show(repo, ref_sha, path)
        if src is None or sha256_hex(src) != (ref.get("kit_files_sha256") or {}).get(path):
            p.append(f"kit_files_sha256[{path}] differs from the file at the reference sha")
    return p


def _sections_differing(a: Any, b: Any, prefix: str = "") -> list[str]:
    """Names (keys) where two output trees differ, to two levels. Never the values."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{prefix}{k}")
            elif canon(a[k]) != canon(b[k]):
                out += _sections_differing(a[k], b[k], f"{prefix}{k}/") if prefix.count("/") < 1 else [f"{prefix}{k}"]
        return out
    return [prefix.rstrip("/")] if canon(a) != canon(b) else []


def compare_b(worker: dict, ref: dict, ref_problems: list[str]) -> dict[str, Any]:
    ref_bytes, got_bytes = canon(ref.get("outputs")).encode(), canon(worker["outputs"]).encode()
    numpy_equal = worker["numpy_version"] == ref.get("numpy_version") == REQUIRED_NUMPY
    inputs_equal = worker["inputs_sha256"] == (ref.get("fixture") or {}).get("inputs_sha256")
    identical = ref_bytes == got_bytes
    ok = identical and numpy_equal and inputs_equal and not ref_problems
    return {"verdict": "PASS" if ok else "FAIL", "numpy_version": worker["numpy_version"], "reference_numpy_version": ref.get("numpy_version"), "numpy_equal_to_reference_and_required": numpy_equal,
            "inputs_sha256_equal": inputs_equal, "outputs_byte_identical": identical, "outputs_sha256_at_sha": sha256_hex(got_bytes), "outputs_sha256_reference": sha256_hex(ref_bytes),
            "differing_sections": [] if identical else _sections_differing(worker["outputs"], ref.get("outputs") or {}), "reference_problems": ref_problems,
            "python_version": worker.get("python_version"), "kit_screen_p_tests": "NOT_RUN_BY_THIS_TOOL"}


def check_b(repo: Path, sha: str, ref_sha: str, ref: dict, workdir: str | None = None) -> dict[str, Any]:
    problems = reference_problems(ref, repo, ref_sha)
    try:
        with worktree(repo, sha, workdir) as wt:
            w = run_worker(wt)
    except (ToolError, subprocess.TimeoutExpired) as exc:
        return {"verdict": "FAIL", "error": str(exc)[:400], "reference_problems": problems, "kit_screen_p_tests": "NOT_RUN_BY_THIS_TOOL"}
    return compare_b(w, ref, problems)


# ------------------------------------------------------------------------------------------------------------------------------ (C) runtime coupling to the kill's vector
SUB_KEYS = tuple(f"{rule}|{fail}" for rule in RULES for fail in FAILS)


def _is_int(v: Any, want: int) -> bool:
    return type(v) is int and v == want


def check_cell(cell: Any) -> dict[str, Any]:
    """Booleans and counts for one cell. Values are compared silently and never returned."""
    sp = cell.get("screen_p") if isinstance(cell, dict) else None
    stats = cell.get("stats") if isinstance(cell, dict) else None
    sp = sp if isinstance(sp, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    subs = sp.get("sub_cells") if isinstance(sp.get("sub_cells"), dict) else {}
    flags, b_ok, seed_ok = 0, _is_int(sp.get("B"), B_FIXED), _is_int(sp.get("seed"), 1)
    per: dict[str, Any] = {}
    all_equal = True
    for key in SUB_KEYS:
        s = subs.get(key) if isinstance(subs.get(key), dict) else {}
        d = s.get("date_cluster") if isinstance(s.get("date_cluster"), dict) else {}
        k_stats = stats.get(f"1.9|{key.split('|')[0]}|rent0|{key.split('|')[1]}")
        k = k_stats if isinstance(k_stats, dict) else {}
        flags += (s.get("ci90_lo_first1000_equals_stat") is True) + (d.get("ci90_lo_first1000_equals_stat") is True)
        b_ok = b_ok and _is_int(s.get("B"), B_FIXED) and _is_int(d.get("B"), B_FIXED)
        seed_ok = seed_ok and _is_int(s.get("seed"), 1) and _is_int(d.get("seed"), 1)
        n_eq = type(s.get("n")) is int and type(k.get("n")) is int and s["n"] == k["n"]
        kd_eq = type(d.get("k_dates")) is int and type(k.get("days")) is int and d["k_dates"] == k["days"]
        x, m = s.get("xbar"), k.get("mean")
        x_eq = type(x) is float and type(m) is float and x == m
        per[key] = {"n_equal": n_eq, "k_dates_equal": kd_eq, "xbar_equal_mean": x_eq}
        all_equal = all_equal and n_eq and kd_eq and x_eq
    all_keys = set(subs) == set(SUB_KEYS)
    b_ok, seed_ok = b_ok and all_keys, seed_ok and all_keys
    return {"flags_true": flags, "flags_all_true": flags == 8, "B_10000_everywhere": b_ok, "seed_1_everywhere": seed_ok, "sub_cells": per, "pass": bool(flags == 8 and b_ok and seed_ok and all_equal)}


def check_c(paths: list[str]) -> dict[str, Any]:
    files, ok = [], True
    for p in paths:
        try:
            doc = json.loads(Path(p).read_text())
        except (OSError, ValueError) as exc:
            files.append({"file": p, "error": type(exc).__name__, "pass": False})
            ok = False
            continue
        meta = (doc.get("meta") or {}).get("screen_p") if isinstance(doc, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        meta_ok = _is_int(meta.get("B"), B_FIXED) and _is_int(meta.get("seed"), 1)
        cells = doc.get("cells") if isinstance(doc, dict) and isinstance(doc.get("cells"), dict) else {}
        per = {name: check_cell(c) for name, c in cells.items()}
        f_ok = meta_ok and bool(per) and all(c["pass"] for c in per.values())
        files.append({"file": p, "meta_B_10000_seed_1": meta_ok, "cells_checked": len(per), "cells_failing": sum(not c["pass"] for c in per.values()), "cells": per, "pass": f_ok})
        ok = ok and f_ok
    return {"verdict": "PASS" if ok and files else "FAIL", "files": files}


# ------------------------------------------------------------------------------------------------------------------------------ check
def cmd_check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    sha, ref_sha = resolve_sha(repo, args.sha), resolve_sha(repo, args.ref_sha)
    ref_path = Path(args.reference)
    try:
        ref = json.loads(ref_path.read_text())
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read the reference file {ref_path}: {exc}")
    a = check_a(repo, sha, ref_sha, args.strict_pricing)
    b = check_b(repo, sha, ref_sha, ref, args.workdir)
    c = check_c(args.results) if args.results else {"verdict": "NOT_RUN", "reason": "no --results given"}
    rec = {"schema": SCHEMA_RECORD, "sha": sha, "ref_sha": ref_sha, "ref_sha_is_pinned": ref_sha == REFERENCE_SHA, "reference_file": str(ref_path), "reference_file_sha256": file_sha256(ref_path),
           "verdicts": {"A": a["verdict"], "B": b["verdict"], "C": c["verdict"]}, "numpy_version": b.get("numpy_version"), "A": a, "B": b, "C": c,
           "note": "Synthetic fixture only; no real data was read; nothing here is a measurement of any book or a profit claim. The exit code is 0 whenever a record is produced."}
    print(canon(rec), end="")
    return 0


# ------------------------------------------------------------------------------------------------------------------------------ cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("reference", help="compute the reference outputs at 489fa7e (numpy 2.5.3 only)")
    r.add_argument("--repo", default=str(REPO_ROOT))
    r.add_argument("--kit-root", help="a clean worktree at 489fa7e (default: a temp worktree is made)")
    r.add_argument("--out", default=str(REPO_ROOT / REFERENCE_REL))
    r.add_argument("--workdir", help="parent directory for temp worktrees")
    c = sp.add_parser("check", help="(A) source identity, (B) byte-identical outputs, (C) results.json coupling")
    c.add_argument("--sha", required=True, help="the kit sha pinned in the cell's DECLARE")
    c.add_argument("--results", action="append", help="a results.json to check (C); repeatable")
    c.add_argument("--reference", default=str(REPO_ROOT / REFERENCE_REL))
    c.add_argument("--repo", default=str(REPO_ROOT))
    c.add_argument("--ref-sha", default=REFERENCE_SHA, help="TESTS ONLY; the record shows ref_sha_is_pinned")
    c.add_argument("--strict-pricing", action="store_true", help="also follow and compare legs() and what it references in (A)")
    c.add_argument("--workdir", help="parent directory for temp worktrees")
    w = sp.add_parser("worker", help="internal: run the fixed set inside a kit root and print the outputs")
    w.add_argument("--kit-root", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "worker":
            print(canon(fixture_outputs(Path(args.kit_root))), end="")
            return 0
        return cmd_reference(args) if args.cmd == "reference" else cmd_check(args)
    except ToolError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
