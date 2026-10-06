#!/usr/bin/env python3
"""EXP-017 batch 1: two cheap screens (H3, H4; H1 and H2 were dropped before any outcome read, plan section 11) and a report-only cost check (C0) on the cached EXP-015 per-migration cells.

**EXPLORATION ONLY. NO EDGE CLAIM.** Plan (follow it exactly): EXP/EXP-017-batch1-cheap-screens-plan.md. A pass earns only a one-shot
confirmation read on an unread reserved block under a later pre-registration.

Modes
  --precount     outcome-blind: cell counts, feature coverage, manifest check. Cell rows are parsed with an object hook that DROPS the net
                 fields (net0, status, sides, p_press) at parse time, so no outcome number enters memory; `cell_nets` is never called.
  --guards-only  guards only (pins, manifests, reserved paths, prior tries, run lock); reads no row.
  (full)         refuses before `started` on a pin, coverage or zero-cell failure; then takes RUN.lock (O_EXCL), logs one `started` try per H cell,
                 evaluates, writes screen.json, screen.md, result.v1 files and the `completed` tries lines. A second run is refused.

Data: the cached V-pass rows of the EXP-015 screen (/data/mal/exp015-screen/scratch/cache/v_P*.rows.jsonl, schema exp015_tape_cache_v1).
Each row: mint, spec, day, mig_ms, gap_ms, features (frozen order), cells [{k, lag, size, censored, filled, status, net0, sides, p_press}].
The cache has size 0.05 SOL cells only. H4 (2x stake) and C0 (0.25 / 0.5 SOL) use cells re-simulated at those sizes by `tools/exp017_resim.py`
(a sealed sized cache). The full run refuses until `--sized-cache` is given and its manifest matches the `SIZED_MANIFEST_SHA256 = <sha>` line
a plan amendment adds after the outcome-blind build. H1 / H2 are report-only counts (dropped from the family before any outcome read).
"""

from __future__ import annotations

import argparse
import bisect
import glob
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import tools.exp015_screen as e15
from tools import mal_result
from tools.latency_curve import _iter_trades

TOOL = "tools.exp017_screen"
SCHEMA = "exp017_screen_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = REPO_ROOT / "EXP" / "EXP-017-batch1-cheap-screens-plan.md"
CANONICAL_TRIES = REPO_ROOT / "data" / "tries.jsonl"  # the lab's canonical log, as EXP-016 (the job checkout's copy)
LAMPORTS = 1_000_000_000

# --- pins (see the plan; every number here is fixed before any outcome is read) ---------------------------------------
DEFAULT_SCRATCH = "/data/mal/exp015-screen/scratch"
RAW_MANIFEST_SHA256 = "0b7c9afae44678a3b8d54451d4be79aad847ad76290f8cd8ff7d478dcc3562ae"  # 71 files, scratch/v_P*/*.jsonl
CACHE_MANIFEST_SHA256 = "72b9bd1a355953c114835a77fd7ea2f2273e75b21393cf5be2d48a16523ba31b"  # 12 files, scratch/cache/v_P*.{rows.jsonl,manifest.json}
RAW_MANIFEST_FILES, CACHE_MANIFEST_FILES = 71, 12
CACHE_CODE_SHA = "cc366d4c7d8597d6429575c165f164cce35ce39d"  # head recorded in every v_P*.manifest.json (EXP-015 screen, job #242)
SIZED_MANIFEST_SHA256: str | None = None  # None: read the pin from the plan (see sized_pin); no pin = H4 / C0 cannot run
THR90 = e15.FROZEN_THRESHOLD  # 0.8030766588450794 = p90 of ARTIFACTS/exp012/oof_scores.json
THR95 = 0.8352960347743753  # p95 of the same 8,801 OOF scores, index round(0.95 * (n - 1)) (asserted by a test against the artifact)
V_CANON_LO, V_CANON_HI = 16_700_000_000, 18_460_000_000  # V0 class "canonical": 17.58 SOL +/- 5 %
H3_WINDOW_MS = 6 * 3_600_000
H3_MIN_AGE_MS = 35 * 60_000  # = EXP-015 PURGE_MIN: hold capped at landing + 30 min, landing ~2.4 s after the first print, plus the exit lag
H3_MIN_WINDOW_N = 30
H3_LEFT_CENSOR_MS = 24 * 3_600_000
H3_LEVEL = 0.0
H1_MIN_COVERAGE = 0.90
BOOT_DRAWS_P = 10_000
BOOT_SEED = 1
FAMILY_ALPHA = 0.05
SIZE_1X = 50_000_000
SIZE_2X = 100_000_000
C0_SIZES = (250_000_000, 500_000_000)
SIZED_COMBOS = ((6, 0.05, 2), (6, 0.10, 2), (6, 0.25, 2), (6, 0.5, 2))  # the re-sim's cells; 0.05 is the decision-equivalence proof against the cache
SIZED_PATTERNS = ("v_P*.rows.jsonl", "v_P*.manifest.json")
PRIMARY = e15.PRIMARY_CELL  # (6, 2)
LEGS = e15.LEGS
HCELLS = ("H3", "H4")  # H1 / H2 were dropped before any outcome read (plan section 11); they stay as report-only counts
REPORT_ONLY_CELLS = ("H1", "H2")
NET_KEYS = frozenset({"net0", "status", "sides", "p_press"})
BLIND_CELL_KEYS = ("k", "lag", "size", "censored")
SOURCES = e15.SOURCES
NON_P1 = ("P2", "P3", "P4")
OUT_SCREEN, OUT_MD = "screen.json", "screen.md"

DEFAULT_VIEWS: dict[str, list[str]] = {
    "P1A": ["/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00"],
    "P1C": ["/data/mal/clean-view/oracle-insample-2026-09-22_25"],
    "P1B": ["/data/mal/clean-view/oracle-live-2026-09-25_27"],
    "P2": [f"/data/mal/clean-view/explore-0814/w{i}" for i in range(1, 8)],
    "P3": [f"/data/mal/blocks-clean/fresh-0903/w{i}" for i in range(1, 4)],
    "P4": ["/data/mal/clean-view/exp011-0909/b", "/data/mal/clean-view/exp011-0909/c"],
}


class Refused(Exception):
    """A pre-declared refusal. Before `started`: nothing is logged, no try is spent."""


# --- manifests (hash bytes only; no field is parsed) ------------------------------------------------------------------


def _file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(scratch: str | Path, patterns: Sequence[str]) -> tuple[list[str], str]:
    """Lines `<sha256>  <relpath>` sorted by relpath; the manifest sha256 is over the lines joined by newline plus a final newline."""
    lines = []
    for pat in patterns:
        for p in sorted(glob.glob(os.path.join(str(scratch), pat))):
            lines.append(f"{_file_sha256(p)}  {os.path.relpath(p, str(scratch))}")
    lines.sort(key=lambda s: s.split("  ", 1)[1])
    return lines, hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


RAW_PATTERNS = ("v_P*/*.jsonl",)
CACHE_PATTERNS = ("cache/v_P*.rows.jsonl", "cache/v_P*.manifest.json")


def check_manifests(scratch: str | Path, raw: str = RAW_MANIFEST_SHA256, cache: str = CACHE_MANIFEST_SHA256) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, pats, pin in (("raw", RAW_PATTERNS, raw), ("cache", CACHE_PATTERNS, cache)):
        lines, sha = manifest(scratch, pats)
        out[name] = {"n_files": len(lines), "sha256": sha, "pinned": pin}
        if sha != pin:
            raise Refused(f"{name} manifest sha256 {sha} ({len(lines)} files) != pinned {pin}")
    return out


def check_cache_heads(scratch: str | Path) -> dict[str, str]:
    heads = {}
    for src in SOURCES:
        p = Path(scratch) / "cache" / f"v_{src}.manifest.json"
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refused(f"{p} unreadable") from None
        heads[src] = (m.get("meta") or {}).get("head")
        if heads[src] != CACHE_CODE_SHA or (m.get("meta") or {}).get("vmap_sha256") != e15.VMAP_0909_SHA256:
            raise Refused(f"{p}: head/vmap pin differs from CACHE_CODE_SHA / the pinned V map")
    return heads


# --- loading ----------------------------------------------------------------------------------------------------------


def _drop_net(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k not in NET_KEYS}


def read_cache_rows(path: str | Path, blind: bool) -> list[dict[str, Any]]:
    """blind=True drops the net fields at parse time (object_hook): no outcome number is ever bound to a name."""
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line, object_hook=_drop_net) if blind else json.loads(line))
    return rows


def load_universe(scratch: str | Path, blind: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    v_rows = {src: read_cache_rows(Path(scratch) / "cache" / f"v_{src}.rows.jsonl", blind) for src in SOURCES}
    return e15.build_universe(v_rows, {}, True)


def selected_text(universe: Sequence[Mapping[str, Any]], scores: Sequence[float]) -> str:
    """The selected-mints file the re-sim writes and the screen re-derives: sorted mints with frozen score >= THR90, one JSON list plus newline."""
    return json.dumps(sorted(u["mint"] for u, s in zip(universe, scores) if s >= THR90)) + "\n"


def selected_sha256(universe: Sequence[Mapping[str, Any]], scores: Sequence[float]) -> str:
    return hashlib.sha256(selected_text(universe, scores).encode("utf-8")).hexdigest()


def check_sized_meta(sized_dir: str | Path, sel_sha: str) -> dict[str, Any]:
    """Per-source manifest meta of the sized cache: V map sha, combos, selected_sha256, one common 40-hex head; the rows file hashes to its manifest."""
    heads = set()
    for src in SOURCES:
        mp = Path(sized_dir) / f"v_{src}.manifest.json"
        rp = Path(sized_dir) / f"v_{src}.rows.jsonl"
        try:
            man = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refused(f"sized manifest {mp} unreadable") from None
        meta = man.get("meta") or {}
        if meta.get("vmap_sha256") != e15.VMAP_0909_SHA256:
            raise Refused(f"{mp}: V map sha differs from the pin")
        if [list(c) for c in meta.get("combos", [])] != [list(c) for c in SIZED_COMBOS]:
            raise Refused(f"{mp}: combos differ from SIZED_COMBOS")
        if meta.get("selected_sha256") != sel_sha:
            raise Refused(f"{mp}: selected_sha256 differs from the frozen selection re-derived from the pinned cache")
        if man.get("rows_sha256") != _file_sha256(rp):
            raise Refused(f"{rp}: rows sha256 differs from its manifest")
        h = str(meta.get("head", ""))
        if len(h) != 40:
            raise Refused(f"{mp}: head is not a 40-hex sha")
        heads.add(h)
    if len(heads) != 1:
        raise Refused(f"sized cache sources were built at {len(heads)} different heads")
    return {"head": next(iter(heads))}


def load_sized_rows(sized_dir: str | Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for src in SOURCES:
        p = Path(sized_dir) / f"v_{src}.rows.jsonl"
        if not p.is_file():
            raise Refused(f"sized cache {p} missing")
        for r in read_cache_rows(p, blind=False):
            out[r["mint"]] = r
    return out


def load_sized_cells(sized_dir: str | Path, rows: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[int, dict[str, Any]]]:
    """mint -> {size_lamports: the (k=6, lag=2) cell}, from a re-simulated cache with the same row schema (v_P*.rows.jsonl)."""
    out: dict[str, dict[int, dict[str, Any]]] = {}
    for r in (load_sized_rows(sized_dir) if rows is None else rows).values():
        for c in r["cells"]:
            if (int(c["k"]), int(c.get("lag", 0))) == PRIMARY:
                out.setdefault(r["mint"], {})[int(c["size"])] = {**c, "lag": int(c.get("lag", 0))}
    return out


def _digest(row: Mapping[str, Any]) -> str | None:
    cell = [c for c in row["cells"] if (int(c["k"]), int(c.get("lag", 0)), int(c["size"])) == (6, 2, SIZE_1X)]
    if len(cell) != 1:
        return None
    return hashlib.sha256(json.dumps([row["mint"], row["mig_ms"], row["features"], cell[0]], sort_keys=True).encode("utf-8")).hexdigest()


def equivalence_check(cache_rows: Mapping[str, Mapping[str, Any]], sized_rows: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Decision-equivalence proof (before `started`): per mint, sha256 of [mint, mig_ms, features, the (6, 2, 0.05 SOL) cell] in the re-sim equals
    the same digest in the EXP-015 cache (code cc366d4). Counts only are returned or printed; any mismatch or an empty comparison refuses."""
    match = bad = 0
    for m, r in sized_rows.items():
        a, b = _digest(r), (_digest(cache_rows[m]) if m in cache_rows else None)
        if a is not None and a == b:
            match += 1
        else:
            bad += 1
    if bad or not match:
        raise Refused(f"decision-equivalence proof failed: {match} matched, {bad} mismatched (re-sim 0.05 SOL cell vs the EXP-015 cache)")
    return {"matched": match, "mismatched": bad}


def h4_missing_cells(universe: Sequence[Mapping[str, Any]], scores: Sequence[float]) -> int:
    """Blind keys only (presence and `censored`): H4 2x-tier rows lacking an uncensored (6, 2, 0.10 SOL) cell. A missing cell would score x = -frozen net."""
    n = 0
    for u, s in zip(universe, scores):
        if s >= THR95:
            c = (u.get("sized") or {}).get(SIZE_2X)
            n += int(c is None or bool(c.get("censored")))
    return n


def attach_sized(universe: Sequence[dict[str, Any]], sized: Mapping[str, Mapping[int, Mapping[str, Any]]]) -> None:
    for u in universe:
        u["sized"] = dict(sized.get(u["mint"], {}))


def frozen_scores(universe: Sequence[Mapping[str, Any]], artifact_dir: Path | None = None, scorer: Callable[[Any], Sequence[float]] | None = None) -> list[float]:
    """The frozen EXP-012 score per row (model never refit). `scorer` is for tests."""
    import numpy as np

    import tools.exp011_freeze as fz
    import tools.exp011_score as e11
    from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR

    x = np.asarray([u["features"] for u in universe], dtype=np.float64).reshape(len(universe), len(fz.FROZEN_FEATURE_NAMES))
    if scorer is None:
        model, _thr, names = e11.load_frozen_spec(artifact_dir or DEFAULT_ARTIFACT_DIR)
        assert list(names) == list(fz.FROZEN_FEATURE_NAMES)
        return [float(s) for s in model.predict(x, num_threads=1)]
    return [float(s) for s in scorer(x)]


# --- side information: create rows (is_mayhem_mode) and the canonical pool (V0) ---------------------------------------


def scan_creates(view_dirs: Mapping[str, Sequence[str | Path]], wanted: Mapping[str, set[str]]) -> dict[str, dict[str, Any]]:
    """mint -> {"mayhem": bool | None, "block_time": int | None} from `create` rows only. Reads three fields: mint, is_mayhem_mode, block_time.
    `wanted[source]` = mints of that source. Earliest block_time wins when a mint has two create rows."""
    out: dict[str, dict[str, Any]] = {}
    for src, dirs in view_dirs.items():
        want = wanted.get(src, set())
        if not want:
            continue
        for d in dirs:
            for p in sorted(glob.glob(os.path.join(str(d), "creates", "*"))):
                if not (p.endswith(".jsonl") or p.endswith(".jsonl.zst")):
                    continue
                for r in _iter_trades(Path(p)):
                    m = r.get("mint")
                    if r.get("type") != "create" or m not in want:
                        continue
                    bt = r.get("block_time")
                    bt = bt if isinstance(bt, int) and not isinstance(bt, bool) else None
                    mh = r.get("is_mayhem_mode")
                    mh = mh if isinstance(mh, bool) else None
                    prev = out.get(m)
                    if prev is None or (bt is not None and (prev["block_time"] is None or bt < prev["block_time"])):
                        out[m] = {"mayhem": mh, "block_time": bt}
    return out


def mayhem_flags(universe: Sequence[Mapping[str, Any]], creates: Mapping[str, Mapping[str, Any]]) -> list[bool | None]:
    """True / False when a create row carries a boolean is_mayhem_mode and block_time at or before the migration time (the entry cutoff is later still, so the field is pre-cutoff; 43.9% of non-mayhem mints graduate in the same slot as their create (bundled launches, many inside the create tx), so their cache mig_ms equals the create second and a strict < would drop them);
    None otherwise (no create row, no field, or a create not strictly earlier than the migration)."""
    out: list[bool | None] = []
    for u in universe:
        c = creates.get(u["mint"])
        ok = c is not None and c["mayhem"] is not None and c["block_time"] is not None and c["block_time"] * 1000 <= u["mig_ms"]
        out.append(c["mayhem"] if ok else None)
    return out


def coverage_by_source(universe: Sequence[Mapping[str, Any]], flags: Sequence[bool | None]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {s: {"n": 0, "with_field": 0, "mayhem_true": 0} for s in SOURCES}
    for u, f in zip(universe, flags):
        d = out[u["source"]]
        d["n"] += 1
        d["with_field"] += int(f is not None)
        d["mayhem_true"] += int(f is True)
    for d in out.values():
        d["coverage"] = (d["with_field"] / d["n"]) if d["n"] else None
    return out


def check_coverage(cov: Mapping[str, Mapping[str, Any]]) -> None:
    bad = [s for s in NON_P1 if cov[s]["n"] and (cov[s]["coverage"] or 0.0) < H1_MIN_COVERAGE]
    if bad:
        raise Refused(f"is_mayhem_mode coverage below {H1_MIN_COVERAGE:.0%} on non-P1 source(s) {bad}: {[cov[s]['coverage'] for s in bad]}")


def canonical_pool(mint: str) -> str:
    from tools.exp016_screen import canonical_pool_str

    return canonical_pool_str(mint)


def v0_class(universe: Sequence[Mapping[str, Any]], vmap: Mapping[str, int | None], pool_fn: Callable[[str], str] = canonical_pool) -> list[str]:
    """Per row: "canonical" (V0 in [V_CANON_LO, V_CANON_HI]), "zero" (V0 == 0), "other" (any other number), "none" (null / absent in the map or no pool)."""
    out = []
    for u in universe:
        try:
            v = vmap.get(pool_fn(u["mint"]))
        except Exception:  # noqa: BLE001 - an un-derivable pool is class "none"
            v = None
        out.append("none" if v is None else "zero" if v == 0 else "canonical" if V_CANON_LO <= v <= V_CANON_HI else "other")
    return out


# --- cell definitions (selection masks; H3's mask needs outcomes and lives in h3_mask) --------------------------------


def frozen_mask(scores: Sequence[float]) -> list[bool]:
    return [s >= THR90 for s in scores]


def h1_mask(scores: Sequence[float], flags: Sequence[bool | None]) -> list[bool]:
    return [s >= THR90 and f is not True for s, f in zip(scores, flags)]


def h2_mask(scores: Sequence[float], klass: Sequence[str]) -> list[bool]:
    return [s >= THR90 and k == "canonical" for s, k in zip(scores, klass)]


def h4_tier(scores: Sequence[float]) -> list[int]:
    """Stake multiplier per row: 2 for score >= p95, 1 for [p90, p95), 0 below."""
    return [2 if s >= THR95 else 1 if s >= THR90 else 0 for s in scores]


def block_start_ms(block: str) -> int:
    return e15.hour_ms(e15.BLOCKS[block][0])


def left_censored(universe: Sequence[Mapping[str, Any]]) -> list[bool]:
    return [u["mig_ms"] < block_start_ms(u["block"]) + H3_LEFT_CENSOR_MS for u in universe]


def h3_gate(universe: Sequence[Mapping[str, Any]], level: float = H3_LEVEL) -> tuple[list[bool], list[int]]:
    """OUTCOME-READING (flat leg, haircut net of the unfiltered migrations). Gate for row m: the mean flat net of every unfiltered row j with
    mig_ms_j in (t_m - 6 h, t_m - 35 min] (hold capped at landing + 30 min, landing ~2.4 s after the first print, plus the exit lag: closed inside 35 min) is > `level` AND the
    window holds at least H3_MIN_WINDOW_N rows. Returns (gate, window_n); the left-censored rows are excluded by the caller."""
    order = sorted(range(len(universe)), key=lambda i: (universe[i]["mig_ms"], universe[i]["mint"]))
    ts = [universe[i]["mig_ms"] for i in order]
    pref = [0.0]
    for i in order:
        pref.append(pref[-1] + e15.cell_nets(universe[i]["cells"][PRIMARY])["flat"])
    gate = [False] * len(universe)
    wn = [0] * len(universe)
    for pos, i in enumerate(order):
        t = ts[pos]
        lo = bisect.bisect_right(ts, t - H3_WINDOW_MS)  # first j with ts > t - 6 h
        hi = bisect.bisect_right(ts, t - H3_MIN_AGE_MS)  # one past the last j with ts <= t - 35 min
        n = hi - lo
        wn[i] = max(n, 0)
        gate[i] = n >= H3_MIN_WINDOW_N and (pref[hi] - pref[lo]) / n > level
    return gate, wn


# --- evaluation -------------------------------------------------------------------------------------------------------


def row_net(u: Mapping[str, Any], size: int | None = None) -> dict[str, float] | None:
    """Per-leg haircut net of row u at the primary cell; size=None -> the cached 0.05 SOL cell, else the re-simulated cell of that size."""
    c = u["cells"].get(PRIMARY) if size is None or size == SIZE_1X else (u.get("sized") or {}).get(size)
    return e15.cell_nets(c)


def per_row_nets(universe: Sequence[Mapping[str, Any]], mult: Sequence[int]) -> list[dict[str, float] | None]:
    """The net a cell books per row: None when not entered, else the net at stake mult x 0.05 SOL (mult 1 = cached cell; 2 = re-simulated 0.10)."""
    out: list[dict[str, float] | None] = []
    for u, m in zip(universe, mult):
        out.append(None if m == 0 else row_net(u, SIZE_1X if m == 1 else SIZE_2X))
    return out


def trades_of(universe: Sequence[Mapping[str, Any]], nets: Sequence[Mapping[str, float] | None], rows: Sequence[int], mult: Sequence[int] | None = None) -> list[dict[str, Any]]:
    out = []
    for i in rows:
        n = nets[i]
        if n is None:
            continue
        u = universe[i]
        c = (u.get("sized") or {}).get(SIZE_2X) if (mult is not None and mult[i] == 2) else u["cells"][PRIMARY]
        out.append({"mint": u["mint"], "day": u["date"], "filled": bool(c.get("filled")), "flat": n["flat"], "press": n["press"], "source": u["source"], "block": u["block"],
                    "stake": SIZE_1X * (1 if mult is None or mult[i] < 1 else mult[i])})
    return out


def boot_p(by_date: Mapping[str, Sequence[float]], draws: int = BOOT_DRAWS_P, seed: int = BOOT_SEED) -> float | None:
    """One-sided date-cluster bootstrap p for H0: mean <= 0. Resamples whole UTC dates; p = (1 + #{draw mean <= 0}) / (1 + draws)."""
    keys = sorted(k for k, v in by_date.items() if len(v))
    if not keys:
        return None
    sums = [sum(by_date[k]) for k in keys]
    cnts = [len(by_date[k]) for k in keys]
    rng = random.Random(seed)
    le0 = 0
    for _ in range(draws):
        pick = [rng.randrange(len(keys)) for _ in keys]
        n = sum(cnts[i] for i in pick)
        if n and sum(sums[i] for i in pick) / n <= 0:
            le0 += 1
    return (1 + le0) / (1 + draws)


def paired(universe: Sequence[Mapping[str, Any]], nets: Sequence[Mapping[str, float] | None], fnets: Sequence[Mapping[str, float] | None], rows: Sequence[int]) -> dict[str, Any]:
    """x_m = (cell net - frozen net) per migration over `rows` (0 where a book does not enter): mean > 0 and date-cluster CI90 lower bound > 0
    under both legs; p = max over legs of the one-sided date-cluster bootstrap p (10,000 draws, seed 1). Also the ex-top-3 of sum x."""
    out: dict[str, Any] = {"n_migrations": len(rows)}
    ok = True
    ps = []
    for leg in LEGS:
        by_date: dict[str, list[float]] = {}
        for i in rows:
            a = nets[i][leg] if nets[i] is not None else 0.0
            b = fnets[i][leg] if fnets[i] is not None else 0.0
            by_date.setdefault(universe[i]["date"], []).append(a - b)
        allx = sorted((v for xs in by_date.values() for v in xs), reverse=True)
        mean = (sum(allx) / len(allx) / LAMPORTS) if allx else None
        ci = e15.date_cluster_ci(by_date)
        p = boot_p(by_date)
        ps.append(p)
        ex3 = (sum(allx[3:]) / LAMPORTS) if len(allx) > 3 else None
        leg_ok = mean is not None and mean > 0 and ci is not None and ci[0] > 0 and ex3 is not None and ex3 > 0
        ok = ok and leg_ok
        out[leg] = {"mean_x_sol": mean, "ci90_date_sol": ci, "p_one_sided": p, "x_sum_ex_top3_sol": ex3, "pass": bool(leg_ok)}
    out["p"] = None if any(p is None for p in ps) else max(ps)
    out["pass_bar"] = bool(ok)
    return out


def holm(pvals: Mapping[str, float | None], alpha: float = FAMILY_ALPHA) -> dict[str, Any]:
    """Holm step-down over the family. A missing p counts as 1.0. Returns {cell: {"p", "threshold", "reject"}}."""
    items = sorted(((c, 1.0 if p is None else p) for c, p in pvals.items()), key=lambda t: (t[1], t[0]))
    m = len(items)
    out: dict[str, Any] = {}
    stop = False
    for j, (c, p) in enumerate(items):
        thr = alpha / (m - j)
        rej = (not stop) and p <= thr
        stop = stop or not rej
        out[c] = {"p": pvals[c], "threshold": thr, "reject": bool(rej)}
    return out


def scope_stats(trades: Sequence[Mapping[str, Any]], n_dates: int) -> dict[str, Any]:
    return e15.scope_report(trades, n_dates)


def evaluate_cell(universe: Sequence[Mapping[str, Any]], nets: Sequence[Mapping[str, float] | None], fnets: Sequence[Mapping[str, float] | None], scope: Sequence[int]) -> dict[str, Any]:
    """Bars 1-6 on the non-P1 dates (`scope` = the non-P1 rows the cell is judged on; H3 excludes its left-censored rows). P1 is report-only.
    B1 gate (n >= 100, >= 5 dates, majority of dates positive, CI lower bound > 0 both resamplers, ex-top-3 > 0), both legs.
    B2 paired vs frozen EXP-012: mean x > 0, date-cluster CI lower > 0, ex-top-3 of x > 0, both legs (Holm is applied across the family afterwards).
    B3 concentration (no date > 20 % of the positive total; ex-best-date total > 0), both legs.
    B4 P2 + P4 only: mean > 0 both legs (EXP-015 bar 5 analog).
    B5 August (P2) replication, no level fit on August: mean > 0 and a majority of P2 dates positive, both legs (EXP-015 bar 6 analog for level-free cells).
    B6 September-only (P3 + P4) mean > 0 both legs (the block the frozen model's selection was tuned near; stability across the two Septembers)."""
    non_rows = [i for i in scope if universe[i]["block"] != "P1"]
    n_non = len({universe[i]["date"] for i in non_rows})  # majority of days is over dates with >= 1 eligible row ("of those days")
    tr_non = trades_of(universe, nets, non_rows)
    rep = scope_stats(tr_non, n_non)
    pr = paired(universe, nets, fnets, non_rows)
    bars: dict[str, Any] = {
        "B1": {"report": rep, "pass": bool(rep["gate_all"])},
        "B2": {"report": pr, "pass": bool(pr["pass_bar"])},
        "B3": {"pass": bool(rep["concentration_all"]), "concentration": {leg: rep[leg]["concentration"] for leg in LEGS}},
    }

    def mean_pos(blocks: Sequence[str], majority: bool = False) -> dict[str, Any]:
        tr = trades_of(universe, nets, [i for i in non_rows if universe[i]["block"] in blocks])
        res = {}
        ok = True
        for leg in LEGS:
            st = e15.leg_stats(tr, leg)
            nd = len({universe[i]["date"] for i in non_rows if universe[i]["block"] in blocks})
            leg_ok = st["mean_sol"] is not None and st["mean_sol"] > 0 and (not majority or st["dates_positive"] * 2 > nd)
            ok = ok and leg_ok
            res[leg] = {"n": st["n"], "mean_sol": st["mean_sol"], "dates_positive": st["dates_positive"], "n_dates": nd, "pass": bool(leg_ok)}
        return {"detail": res, "pass": bool(ok)}

    bars["B4"] = mean_pos(["P2", "P4"])
    bars["B5"] = mean_pos(["P2"], majority=True)
    bars["B6"] = mean_pos(["P3", "P4"])
    p1_rows = [i for i in scope if universe[i]["block"] == "P1"]
    p1_rep = scope_stats(trades_of(universe, nets, p1_rows), len(e15.block_dates("P1")))
    return {"bars": bars, "bars_all": all(b["pass"] for b in bars.values()), "p": pr["p"], "n_trades_non_p1": len(tr_non), "p1_report_only": {"n": p1_rep["flat"]["n"], "flat_mean_sol": p1_rep["flat"]["mean_sol"], "press_mean_sol": p1_rep["press"]["mean_sol"]}}


def c0_report(universe: Sequence[Mapping[str, Any]], frozen: Sequence[bool], with_sized: bool) -> dict[str, Any]:
    """Report-only, outside the Holm family: the frozen EXP-012 selection at 0.25 / 0.5 SOL (re-simulated cells), non-P1 dates."""
    if not with_sized:
        return {"status": "NOT_RUN: needs the re-simulated sized cache (tools/exp017_resim.py)"}
    non = [i for i, u in enumerate(universe) if u["block"] != "P1"]
    out: dict[str, Any] = {"status": "report-only"}
    for size in (SIZE_1X, SIZE_2X, *C0_SIZES):  # SIZE_2X = "uniform 0.10 on all frozen-selected": separates a score-tier effect (H4) from stake size
        nets = [row_net(u, size) if f else None for u, f in zip(universe, frozen)]
        miss = sum(1 for i in non if frozen[i] and nets[i] is None)
        out[str(size / LAMPORTS)] = {"n_without_cell": miss, "report": scope_stats(trades_of(universe, nets, non), len(e15.non_p1_dates(True)))}
    return out


# --- cells ------------------------------------------------------------------------------------------------------------


def build_cells(universe: Sequence[Mapping[str, Any]], scores: Sequence[float], flags: Sequence[bool | None], klass: Sequence[str]) -> dict[str, Any]:
    """Selections per cell as per-row stake multipliers (0 / 1 / 2) plus the row scope each cell is judged on."""
    n = len(universe)
    allrows = list(range(n))
    frozen = frozen_mask(scores)
    cells: dict[str, Any] = {
        "frozen": {"mult": [int(f) for f in frozen], "scope": allrows},
        "H1": {"mult": [int(x) for x in h1_mask(scores, flags)], "scope": allrows},
        "H2": {"mult": [int(x) for x in h2_mask(scores, klass)], "scope": allrows},
        "H4": {"mult": h4_tier(scores), "scope": allrows},
    }
    return cells


def n_selected(cells: Mapping[str, Mapping[str, Any]], universe: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for name, c in cells.items():
        d = {b: 0 for b in ("P1", *NON_P1)}
        for u, m in zip(universe, c["mult"]):
            d[u["block"]] += int(m > 0)
        d["non_p1"] = sum(d[b] for b in NON_P1)
        out[name] = d
    return out


def precount(universe: Sequence[Mapping[str, Any]], stats: Mapping[str, Any], scores: Sequence[float], flags: Sequence[bool | None], klass: Sequence[str]) -> dict[str, Any]:
    """Outcome-blind counts only. H3's gate reads outcomes, so only its left-censor effect on n is counted here."""
    cells = build_cells(universe, scores, flags, klass)
    lc = left_censored(universe)
    frozen = cells["frozen"]["mult"]
    h3_cens = {b: sum(1 for u, c in zip(universe, lc) if c and u["block"] == b) for b in ("P1", *NON_P1)}
    h3_frozen_lost = {b: sum(1 for u, c, f in zip(universe, lc, frozen) if c and f and u["block"] == b) for b in ("P1", *NON_P1)}
    vc: dict[str, dict[str, int]] = {s: {} for s in SOURCES}
    for u, k in zip(universe, klass):
        vc[u["source"]][k] = vc[u["source"]].get(k, 0) + 1
    return {
        "n_universe": len(universe), "by_source": stats["by_source"],
        "n_selected": n_selected(cells, universe),
        "h3_left_censored_rows": h3_cens, "h3_left_censored_frozen_selected": h3_frozen_lost,
        "mayhem_coverage": coverage_by_source(universe, flags), "v0_class_by_source": vc,
        "h4_tiers_non_p1": {t: sum(1 for u, m in zip(universe, cells["H4"]["mult"]) if m == t and u["block"] != "P1") for t in (1, 2)},
    }


def zero_cell_check(counts: Mapping[str, Any]) -> None:
    """Refuse before `started` when a cell whose selection is outcome-blind (H1, H2, H4) has zero non-P1 trades. H3's gate reads outcomes, so a
    zero H3 cell is detected after `started` and reported as a failed cell (try spent), never as a refusal."""
    zero = [c for c in ("H4",) if counts["n_selected"][c]["non_p1"] == 0]
    if zero:
        raise Refused(f"zero-cell refusal: {zero} select no non-P1 migration (H3 is outcome-gated and is checked after `started`)")


# --- tries, lock, outputs ---------------------------------------------------------------------------------------------

CELL_DESC = {
    "H1": "frozen EXP-012 selection minus mints whose create row has is_mayhem_mode == True",
    "H2": "frozen EXP-012 selection restricted to canonical-V0 pools (V0 in [16.70, 18.46] SOL)",
    "H3": "frozen EXP-012 selection gated on the trailing 6 h (migrations >= 30 min old) unfiltered flat net > 0, min 30 rows, first 24 h of each block excluded",
    "H4": "frozen EXP-012 selection, 2x stake for score >= p95, 1x in [p90, p95)",
}


def prior_exp017_lines(log: Path) -> list[dict[str, Any]]:
    out = []
    if Path(log).is_file():
        for line in Path(log).read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("tool") == TOOL or str((rec.get("config") or {}).get("key", "")).startswith("exp017_"):
                out.append(rec)
    return out


def prior_tries_per_pool(log: Path) -> dict[str, int]:
    """Lines in the log (this tool's excluded) whose data blocks overlap each pool's counted window."""
    out = {b: 0 for b in e15.active_blocks(True)}
    if Path(log).is_file():
        for line in Path(log).read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("tool") == TOOL:
                continue
            for b in out:
                a, z = e15.BLOCKS[b]
                if any(str(d.get("start_hour", "")) < z and str(d.get("end_hour_exclusive", "")) > a for d in rec.get("data_blocks") or []):
                    out[b] += 1
    return out


def log_cell_tries_all(logs: Sequence[Path], out_dir: Path, cells: Sequence[str], status: str) -> dict[str, dict[str, Any]]:
    """Write to the ops log and the canonical log (when they differ); returns the first (ops) log's info."""
    info: dict[str, dict[str, Any]] = {}
    seen: set[Path] = set()
    for lg in logs:
        if Path(lg).resolve() in seen:
            continue
        seen.add(Path(lg).resolve())
        r = log_cell_tries(lg, out_dir, cells, status)
        info = info or r
    return info


def check_ops_tries_log(arg: str | None) -> Path:
    """Screen mode writes an absolute ops log (--tries-log) as well as the canonical repo log."""
    if not arg:
        raise Refused("screen mode needs an absolute --tries-log (e.g. /data/mal/ops/tries-exp017-screen.jsonl)")
    if not Path(arg).is_absolute():
        raise Refused(f"--tries-log {arg} is relative: give an absolute path")
    return Path(arg)


def check_canonical_tries(path: Path) -> None:
    """The canonical repo data/tries.jsonl must exist and hold exp015_ lines."""
    p = Path(path)
    if not p.is_file():
        raise Refused(f"canonical tries log {p} does not exist")
    if not e15.prior_exp015_lines(p):
        raise Refused(f"canonical tries log {p} has no exp015_ lines: not the lab's canonical log")


def check_no_prior_tries(*logs: Path) -> None:
    seen: set[Path] = set()
    for lg in logs:
        r = Path(lg).resolve()
        if r in seen:
            continue
        seen.add(r)
        prior = prior_exp017_lines(r)
        if prior:
            raise Refused(f"{len(prior)} earlier exp017 line(s) in {r}: a second run is refused")


def log_cell_tries(log: Path, out_dir: Path, cells: Sequence[str], status: str) -> dict[str, dict[str, Any]]:
    """`started`: one line per cell on the bookkeeping block. Other statuses: one line per (cell, pool group), so each pool counts the try."""
    groups = {"universe": e15.UNIVERSE_BLOCKS} if status == "started" else e15.pool_group_blocks(True)
    info: dict[str, dict[str, Any]] = {}
    for c in cells:
        info[c] = {"groups": {}}
        for g, blocks in groups.items():
            ret = mal_result.append_try(
                log, tool=TOOL,
                config={"key": f"exp017_{c.lower()}", "experiment": "EXP-017 batch 1", "cell": c, "status": status, "pool_group": g, "desc": CELL_DESC[c], "k": 6, "exit_lag": 2, "fee_lamports": e15.FEE, "pricing": "V"},
                data_blocks=list(blocks), result_path=out_dir / OUT_SCREEN, role="exploration")
            info[c]["groups"][g] = ret
            info[c].update(ret)  # the last group's data_key / variant_n at top level; every group's is under "groups"
    return info


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


SIZED_PIN_RE = re.compile(r"^SIZED_MANIFEST_SHA256 = ([0-9a-f]{64})\s*$", re.MULTILINE)


def sized_pin(plan: Path = PLAN) -> str | None:
    """The sized-cache manifest pin: the constant, else the single `SIZED_MANIFEST_SHA256 = <sha>` line a plan amendment adds after the
    outcome-blind re-sim build. Two different pins in the plan refuse."""
    if SIZED_MANIFEST_SHA256 is not None:
        return SIZED_MANIFEST_SHA256
    try:
        found = set(SIZED_PIN_RE.findall(Path(plan).read_text(encoding="utf-8")))
    except OSError:
        return None
    if len(found) > 1:
        raise Refused(f"{plan} carries {len(found)} different SIZED_MANIFEST_SHA256 lines")
    return next(iter(found), None)


def _fmt(v: Any) -> str:
    return "n/a" if v is None else f"{v:+.5f}" if isinstance(v, float) else str(v)


def render_md(rep: Mapping[str, Any]) -> str:
    L = ["# EXP-017 batch 1 screen", "", "**Exploration only. No edge claim.** A pass means one confirmation read of an unread reserved block, under a later pre-registration.", "",
         f"Outcome: {rep['outcome']}", "", "| Cell | n non-P1 | p (paired) | Holm threshold | Holm reject | B1 | B2 | B3 | B4 | B5 | B6 | all bars |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in HCELLS:
        r = rep["cells"][c]
        h = rep["holm"][c]
        b = r["bars"]
        L.append(f"| {c} | {r['n_trades_non_p1']} | {_fmt(h['p'])} | {h['threshold']:.4f} | {h['reject']} | " + " | ".join(str(b[f'B{i}']['pass']) for i in range(1, 7)) + f" | {r['bars_all']} |")
    L += ["", "C0 (report-only, outside Holm): " + json.dumps(rep["c0"], default=str)[:2000], "", "## Caveats"] + [f"- {x}" for x in rep["caveats"]]
    return "\n".join(L) + "\n"


CAVEATS = (
    "Exploration, not a pre-registration of an edge. Cells reuse the 27 non-P1 dates the EXP-015 screen already read; every cell here is a further look at the same dates.",
    "Winner's curse: the frozen EXP-012 cell is itself best-of-many; filters on top of it inherit that selection.",
    "The cached cells are 0.05 SOL; sized cells (H4, C0) come from a separate re-simulation whose manifest was pinned by amendment.",
    "H3's gate reads outcomes of earlier migrations only (hold closed); its left-censor removes the first 24 h of each block.",
    "P1 dates are report-only.",
)


def h4_vs_uniform(cells: Mapping[str, Mapping[str, Any]], c0: Mapping[str, Any]) -> dict[str, Any]:
    """H4 is a SCORE effect only if its non-P1 total SOL exceeds the non-P1 total SOL of uniform 0.10 SOL on every frozen-selected row, under BOTH
    fail models (equivalently: the [THR90, THR95) tier's sum of (0.10 net - 0.05 net) is < 0 on both legs). Otherwise it is a size effect."""
    key = str(SIZE_2X / LAMPORTS)
    try:
        ref = c0[key]["report"]
        out = {leg: {"h4_total_sol": cells["H4"]["bars"]["B1"]["report"][leg]["total_sol"], "uniform_0p10_total_sol": ref[leg]["total_sol"]} for leg in LEGS}
    except (KeyError, TypeError):
        return {"score_effect": False, "reason": "uniform-0.10 reference unavailable"}
    for leg in LEGS:
        out[leg]["h4_beats_uniform"] = bool(out[leg]["h4_total_sol"] is not None and out[leg]["uniform_0p10_total_sol"] is not None and out[leg]["h4_total_sol"] > out[leg]["uniform_0p10_total_sol"])
    out["uniform_n_without_cell"] = c0[key].get("n_without_cell")
    # a frozen-selected row without a 0.10 cell makes the uniform reference incomplete: never a score effect
    out["score_effect"] = all(out[leg]["h4_beats_uniform"] for leg in LEGS) and out["uniform_n_without_cell"] == 0
    return out


def decide(cells: Mapping[str, Mapping[str, Any]], hm: Mapping[str, Mapping[str, Any]], uniform: Mapping[str, Any] | None = None) -> str:
    wins = [c for c in HCELLS if hm[c]["reject"] and cells[c]["bars_all"]]
    if "H4" in wins and not (uniform or {}).get("score_effect", False):
        wins.remove("H4")
        note = " H4: size effect, not score -- earns nothing."
    else:
        note = ""
    if not wins:
        return "SCREEN NONE: no cell is Holm-significant with bars 1-6 passing. Nothing goes to confirmation." + note
    best = max(wins, key=lambda c: cells[c]["bars"]["B1"]["report"]["press"]["mean_sol"] or float("-inf"))
    return f"SCREEN PASS: {', '.join(wins)} clear Holm and bars 1-6; {best} has the largest pressure mean. This means 'worth one confirmation read', never 'has an edge'." + note


def run_full(universe: Sequence[Mapping[str, Any]], scores: Sequence[float], flags: Sequence[bool | None], klass: Sequence[str], with_sized: bool) -> dict[str, Any]:
    cells = build_cells(universe, scores, flags, klass)
    frozen_nets = per_row_nets(universe, cells["frozen"]["mult"])
    res: dict[str, Any] = {}
    trades: dict[str, list[dict[str, Any]]] = {}
    # H3: outcome-reading gate, left-censored rows leave BOTH the cell and the paired comparison.
    gate, wn = h3_gate(universe)
    lc = left_censored(universe)
    h3_mult = [int(f and g and not c) for f, g, c in zip(cells["frozen"]["mult"], gate, lc)]
    cells["H3"] = {"mult": h3_mult, "scope": [i for i in range(len(universe)) if not lc[i]]}
    for c in HCELLS:
        mult = cells[c]["mult"]
        if c == "H4" and not with_sized:
            raise Refused("H4 needs the sized cache")
        nets = per_row_nets(universe, mult)
        res[c] = evaluate_cell(universe, nets, frozen_nets, cells[c]["scope"])
        res[c]["n_selected"] = n_selected({c: cells[c]}, universe)[c]
        trades[c] = trades_of(universe, nets, [i for i in cells[c]["scope"] if universe[i]["block"] != "P1"], mult)
    hm = holm({c: res[c]["p"] for c in HCELLS})
    for c in HCELLS:
        res[c]["bars"]["B2"]["pass"] = bool(res[c]["bars"]["B2"]["pass"] and hm[c]["reject"])  # B2 includes Holm significance
        res[c]["bars_all"] = all(b["pass"] for b in res[c]["bars"].values())
    c0 = c0_report(universe, [bool(m) for m in cells["frozen"]["mult"]], with_sized)
    uniform = h4_vs_uniform(res, c0)
    return {"trades": trades, "cells": res, "holm": hm, "c0": c0, "h4_vs_uniform_0p10": uniform,
            "h3_window": {"left_censored_rows": sum(lc), "gate_open_rows": sum(1 for g, c in zip(gate, lc) if g and not c), "rows_with_window_n_lt_min": sum(1 for n in wn if n < H3_MIN_WINDOW_N)},
            "outcome": decide(res, hm, uniform), "caveats": list(CAVEATS)}


# --- CLI --------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", default=DEFAULT_SCRATCH)
    ap.add_argument("--out-dir", type=Path, default=Path("/data/mal/exp017-screen"))
    ap.add_argument("--tries-log", default=None, help="screen mode: absolute ops log, e.g. /data/mal/ops/tries-exp017-screen.jsonl")
    ap.add_argument("--canonical-tries", type=Path, default=CANONICAL_TRIES)
    ap.add_argument("--sized-cache", default=None)
    ap.add_argument("--vmap", default=e15.VMAP_0909_PATH)
    ap.add_argument("--artifact-dir", type=Path, default=None)
    ap.add_argument("--precount", action="store_true")
    ap.add_argument("--guards-only", action="store_true")
    for s in SOURCES:
        ap.add_argument(f"--view-{s.lower()}", action="append", default=None, help=f"clean view dir(s) holding creates/ for {s}")
    return ap


def view_dirs(args: argparse.Namespace) -> dict[str, list[str]]:
    out = {}
    for s in SOURCES:
        dirs = getattr(args, f"view_{s.lower()}") or DEFAULT_VIEWS[s]
        for d in dirs:
            e15.refuse_reserved(d, f"view {s}")
        out[s] = list(dirs)
    return out


def run_guards(args: argparse.Namespace, logs: Sequence[Path]) -> dict[str, Any]:
    g: dict[str, Any] = {}
    g["manifests"] = check_manifests(args.scratch)
    g["cache_heads"] = check_cache_heads(args.scratch)
    g["vmap_sha256"] = e15.check_vmap(args.vmap, e15.VMAP_0909_SHA256, "V map")
    g["views"] = view_dirs(args)
    if args.sized_cache:
        e15.refuse_reserved(args.sized_cache, "sized cache")
    check_no_prior_tries(*logs)
    return g


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out_dir: Path = args.out_dir
    tries_path: Path | None = None
    canonical: Path | None = None
    try:
        if args.precount:
            if args.tries_log is not None:
                raise Refused("--precount reads no tries log; do not pass --tries-log")
            logs: list[Path] = []
        else:
            tries_path = check_ops_tries_log(args.tries_log)
            canonical = Path(args.canonical_tries).resolve()
            check_canonical_tries(canonical)
            logs = [tries_path, canonical]
        g = run_guards(args, logs)
        if args.guards_only:
            print(json.dumps({"guards": "ok", **{k: g[k] for k in ("manifests", "vmap_sha256")}}, default=str))
            return 0
        blind = bool(args.precount)
        e15.check_run_lock(out_dir) if not blind else None
        universe, stats = load_universe(args.scratch, blind=blind)
        scores = frozen_scores(universe, args.artifact_dir)
        wanted: dict[str, set[str]] = {s: set() for s in SOURCES}
        for u in universe:
            wanted[u["source"]].add(u["mint"])
        flags = mayhem_flags(universe, scan_creates(g["views"], wanted))
        cov = coverage_by_source(universe, flags)
        from tools.pumpswap_virtual import load_map

        klass = v0_class(universe, load_map(Path(args.vmap)))
        counts = precount(universe, stats, scores, flags, klass)
        if blind:
            print(json.dumps({"mode": "precount", "counts": counts, "outcome_blind": True}, indent=2, default=str))
            return 0
        zero_cell_check(counts)
        with_sized = False
        if args.sized_cache:
            pin = sized_pin()
            if pin is None:
                raise Refused("no SIZED_MANIFEST_SHA256 pin in the plan: an amendment must pin the re-simulated cache before the full run")
            _, sha = manifest(args.sized_cache, SIZED_PATTERNS)
            if sha != pin:
                raise Refused(f"sized cache manifest {sha} != pinned {pin}")
            meta = check_sized_meta(args.sized_cache, selected_sha256(universe, scores))
            sized_rows = load_sized_rows(args.sized_cache)
            cache_rows = {r["mint"]: r for src in SOURCES for r in read_cache_rows(Path(args.scratch) / "cache" / f"v_{src}.rows.jsonl", blind=False)}
            eq = equivalence_check(cache_rows, sized_rows)
            del cache_rows
            attach_sized(universe, load_sized_cells(args.sized_cache, sized_rows))
            miss = h4_missing_cells(universe, scores)
            if miss:
                raise Refused(f"{miss} H4 2x-tier row(s) lack an uncensored (6, 2, 0.10 SOL) cell: a missing cell would score x = -frozen net")
            print(f"sized cache ok: equivalence matched={eq['matched']} head={meta['head'][:8]}", file=sys.stderr)
            with_sized = True
        else:
            raise Refused("H4 (2x stake) needs --sized-cache; the re-simulation is tools/exp017_resim.py")
        check_no_prior_tries(*logs)  # re-checked right before the spend point
        head = e15.git_state()["head"]
        e15.take_lock(out_dir, head, hashlib.sha256(json.dumps(sorted(vars(args).items(), key=lambda kv: kv[0]), default=str).encode()).hexdigest())
    except (Refused, e15.Refused) as exc:
        print(f"refusing (before started, no tries line): {exc}", file=sys.stderr)
        return 2
    assert tries_path is not None and canonical is not None
    t0 = time.time()
    started = False
    status = "aborted_after_read"
    try:
        log_cell_tries_all([tries_path, canonical], out_dir, HCELLS, "started")
        started = True
        rep = run_full(universe, scores, flags, klass, with_sized)
        rep.update({"schema": SCHEMA, "head": head, "counts": counts, "disclosures": {"cache_code_sha": CACHE_CODE_SHA, "prior_tries_per_pool": prior_tries_per_pool(canonical)}})
        trades = rep.pop("trades")
        write_json(out_dir / OUT_SCREEN, rep)
        (out_dir / OUT_MD).write_text(render_md(rep), encoding="utf-8")
        info = log_cell_tries_all([tries_path, canonical], out_dir, HCELLS, "completed")
        write_results(out_dir, trades, info, tries_path, head, time.time() - t0)
        status = "completed"
        print(rep["outcome"])
        return 0
    except Exception as exc:  # noqa: BLE001 - tries are spent after `started`; record and stop
        print(f"aborted after started: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        e15.write_record(out_dir, status, started, {c: status for c in HCELLS})


def _rv1(x: Mapping[str, Any], leg: str) -> dict[str, Any]:
    sol = x[leg] / LAMPORTS
    return {"sol": sol, "pct": sol / (x["stake"] / LAMPORTS), "day": x["day"], "filled": bool(x["filled"])}


def write_results(out_dir: Path, trades: Mapping[str, Sequence[Mapping[str, Any]]], info: Mapping[str, Mapping[str, Any]], log: Path, head: str, runtime_s: float) -> None:
    """One result.v1 per cell: the cell's non-P1 trades under both legs. (The trade lists are rebuilt from the same masks by the caller's code path.)"""
    for c in HCELLS:
        t = trades.get(c)
        if t is None:
            continue
        tries = {"data_key": info[c]["data_key"], "variant_n": info[c]["variant_n"], **{"of_m": mal_result.tries_summary(log, info[c]["data_key"])["of_m"]}}
        res = mal_result.build_result(
            tool=TOOL, git_sha=head, command="python -m tools.exp017_screen", config={"cell": c, "desc": CELL_DESC[c]}, role="exploration",
            data_blocks=[b for g in ("P2", "P3", "P4") for b in e15.pool_group_blocks(True)[g]], stage="screen",
            trades_flat=[_rv1(x, "flat") for x in t], trades_pressure_s1=[_rv1(x, "press") for x in t],
            tries=tries, runtime_s=runtime_s, notes="EXP-017 exploration screen; no edge claim; tries per pool group: " + json.dumps(info[c].get("groups", {}), sort_keys=True))
        mal_result.write_result(out_dir / f"result_{c}.json", res)


if __name__ == "__main__":
    raise SystemExit(main())
