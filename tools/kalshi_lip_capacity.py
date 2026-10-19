#!/usr/bin/env python3
"""Kalshi LIP-F1 capacity Book C runner (hunt18 / kalshi-lip).  Report-only; paper only; public data only.

Runs the FROZEN LIP-F1 simulator (``tools/kalshi_lip_sim.py``, md5 pinned by DECLARE-FORWARD-PINS-1) on
Book C, the report-only capacity book of

    DECLARE-FORWARD-CAPACITY-1.md   sha256 a430e040ed1245fba0c0d2de11b8056cb9856ddecf750f7901878b8ac63fd34b
                                    (frozen 2026-10-19T08:22:27Z)

without changing one byte of the simulator.  Book C is LIP-F1 exactly (Book A parameters S = 25, I_max = 50,
cells U0 / U15 / BQ, same reward model, fills, P&L, stress cells and statistics) with two differences:

  1. the universe: 43 KXAAAGASD* daily series, disjoint from LIP-F1's 15 (the 8 gas states and the 7 others);
  2. the freeze instant FC = 2026-10-19T08:22:27Z (the first-seen rule uses FC).

How the override works (the only mechanism): ``book_c_universe()`` is a context manager that, for the duration
of ONE call into the simulator, rebinds seven module constants of ``tools.kalshi_lip_sim`` and restores them in a
``finally`` (``FREEZE_ISO``, ``FREEZE_US``, ``GAS_SERIES`` = the 43, ``OTHER_SERIES`` = (), ``SERIES_ADMITTED``
= the 43, ``PRIMARY_RUN`` = obrec-1019e so the simulator's coverage block reads Book C's own recorder, and
``BASELINE_GAP_S``, whose key "obrec-1019c" (the one ``coverage()`` indexes) carries Book C's 350.0 s per round,
QP ruling (2) on #736; lipsnap stays 900.0).  The simulator's functions read those names at call time, so no
simulator code is touched, copied or wrapped.  Importing this module changes nothing in the simulator.

What Book C is not: not a gate book, never promotable, no read instant of its own (R is passed in and is
LIP-F1's R; ``--scan`` is refused), no kill condition.  Every output says so ("book": "C", "report_only": true,
"promotable": false) and goes only to ``--out``, which must not hold LIP-F1 simulator files.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from pathlib import Path

try:  # run as ``python -m tools.kalshi_lip_capacity`` (repo root) or as a script / from tools/ in tests
    from tools import kalshi_lip_sim as sim
    from tools import kalshi_public as kp
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kalshi_lip_sim as sim
    import kalshi_public as kp

# ------------------------------------------------------------------------------------------ the declaration
CAPACITY_DECLARE_SHA256 = "a430e040ed1245fba0c0d2de11b8056cb9856ddecf750f7901878b8ac63fd34b"
REGISTRY_1019E_SHA256 = "82f617517d7b3c19e910e7cc1efb94e8494060e3e5d0d815b7f367acacc02ed7"
SIM_MD5_PINNED = "3dc4134aa575752927bea18db0e6a341"        # DECLARE-FORWARD-PINS-1: tools/kalshi_lip_sim.py
BOOK = "C"
FC_ISO = "2026-10-19T08:22:27Z"
BOOK_C_SERIES = tuple(f"KXAAAGASD{s}" for s in (
    "", "AK", "AL", "AR", "AZ", "CA", "CO", "CT", "DE", "HI", "IA", "ID", "IN", "KS", "KY", "LA", "MA", "MD", "ME",
    "MN", "MO", "MS", "MT", "ND", "NE", "NH", "NJ", "NM", "NV", "OK", "OR", "RI", "SC", "SD", "TN", "TX", "UT", "VA",
    "VT", "WA", "WI", "WV", "WY"))
BOOK_C_RECORDER = "obrec-1019e"
# QP ruling (2) on #736: the coverage baseline of Book C is the round time stated in the frozen declaration
# (section 1: "a round takes roughly 350 s"), NOT a median measured on Book C's own rows.
BOOK_C_BASELINE_S = 350.0
BASELINE_RULING = (
    "QP ruling (2) on #736: B_C = 350.0 s per round of obrec-1019e, the round time stated in the frozen "
    "DECLARE-FORWARD-CAPACITY-1 (section 1, frozen before any obrec-1019e row existed); expected rounds per UTC day "
    "= 86400 / 350 = 246.9. The 70% rule of DECLARE-FORWARD section 10 (two consecutive UTC days) applies unchanged "
    "to obrec-1019e rounds (weighted n_snaps_ok / n_open) and to lipsnap-1019 polls (900.0 s, unchanged). 222.7 s is "
    "LIP-F1's obrec-1019c baseline and is not used for Book C. The measured median gap is a disclosure only, never "
    "a baseline. During the simulator call the dict key 'obrec-1019c' (the one coverage() reads) carries 350.0; in "
    "this file it is relabelled obrec-1019e.")
DISCLOSURE_DAY = ("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")
REGISTRY_PINS = {BOOK_C_RECORDER: REGISTRY_1019E_SHA256}
SIZE, IMAX = 25.0, 50.0                                       # Book A parameters
DEFAULT_BOOKS = "/data/mal/kalshi/obrec-1019e"
DEFAULT_PROGRAMS = "/data/mal/kalshi/lipsnap-1019"
DEFAULT_META = "/data/mal/kalshi/meta-1019e"
DEFAULT_RUNS = "/data/mal/kalshi/lipdl-cap1/run-*"          # trades and settled markets, per-run stores
MARKER = "BOOK-C-REPORT-ONLY.json"
SIM_OUTPUT_FILES = ("units.parquet", "fills.parquet", "days.parquet", "gate.json", "table.txt", "snaps.parquet")

FC_US = sim.iso_to_us(FC_ISO)

# The simulator constants the context manager rebinds, and their LIP-F1 values, snapshotted at import (before
# anything of ours ran).  ``book_c_universe`` refuses to start unless the simulator still holds exactly these.
PATCHED = ("FREEZE_ISO", "FREEZE_US", "GAS_SERIES", "OTHER_SERIES", "SERIES_ADMITTED", "PRIMARY_RUN",
           "BASELINE_GAP_S")
F1_VALUES = {n: getattr(sim, n) for n in PATCHED}
F1_SERIES = tuple(F1_VALUES["SERIES_ADMITTED"])               # LIP-F1's 15 admitted series

LABEL_KEYS = ("book", "report_only", "promotable", "book_label", "capacity_declare_sha256", "capacity_freeze",
              "capacity_universe", "capacity_recorder", "capacity_registry_pins", "capacity_sim_md5_pinned",
              "capacity_tool_md5", "capacity_notes", "capacity_disclosures")
D1_TEXT = ("CAP stays 450 s (DECLARE 4, unchanged) against a recorder cadence of about 350 s per round. At the 70% "
           "coverage threshold the mean round is 500 s, which is above CAP, so a Book C that counts as decidable can "
           "still be blind on most gaps. Book C measures the rule at a different blindness than Book A: it is not a "
           "clean 'same rule at 6x the markets' read, and any capacity scaling taken from it is low-confidence in "
           "either direction.")
D2_TEXT = ("Book C is outside the promotion gate and is never evidence of profit. It is never promotable, and none of "
           "its numbers, positive or not, may be quoted as LIP-F1 evidence or as profit.")
NOTES = [
    "Book C is NOT a gate book: never promotable; it cannot rescue, replace or condition LIP-F1's verdict, gates, "
    "kill condition or read instant (DECLARE-FORWARD-CAPACITY-1 section 2).",
    "Book C has no kill condition and is never stopped early. The simulator's 'kill' block and a DEAD_AS_FROZEN "
    "part of a verdict mean K1/K2 would have triggered under LIP-F1's rule; Book C is not stopped by it.",
    "R is LIP-F1's read instant, passed in. Book C has no counts rule of its own (--scan is refused).",
    "The CAP = 450 s pull rule applies unchanged; obrec-1019e takes about 350 s per round (LIP-F1's gas recorder "
    "about 45 s), so Book C is blinder than Book A. Disclosed in the declaration, not corrected (D1).",
    BASELINE_RULING + " A coverage NOT_DECIDABLE here is a real data gap and is reported as one.",
    "All 43 units are gas units: G4b clusters every unit by date(E).",
]


class CapacityRefusal(Exception):
    """A Book C run was refused before the simulator ran (wrong pin, F1 series, --scan, bad --out ...)."""


# ------------------------------------------------------------------------------------------ declaration
def parse_declaration(text):
    """-> (series tuple, FC iso) read from the text of DECLARE-FORWARD-CAPACITY-1.md.  Section 1.1's series line is
    the only line made of KXAAAGASD* tickers separated by commas; FC is the instant after 'FC ='."""
    import re
    lines = re.findall(r"^\s*(KXAAAGASD[A-Z0-9]*(?:,KXAAAGASD[A-Z0-9]*)+)\s*$", text, re.M)
    if len(lines) != 1:
        raise CapacityRefusal(f"expected exactly one series line in the declaration, found {len(lines)}")
    fcs = set(re.findall(r"\bFC = (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", text))
    if len(fcs) != 1:
        raise CapacityRefusal(f"expected exactly one 'FC = <instant>' in the declaration, found {sorted(fcs)}")
    return tuple(lines[0].split(",")), next(iter(fcs))


def check_universe(series):
    """A Book C universe must be the declared 43, unique, all gas daily, and disjoint from LIP-F1's 15."""
    series = tuple(series)
    leak = sorted(set(series) & set(F1_SERIES))
    if leak:
        raise CapacityRefusal(f"LIP-F1 series in the Book C universe: {leak}")
    if len(set(series)) != len(series):
        raise CapacityRefusal("duplicate series in the Book C universe")
    if not series or not all(s.startswith("KXAAAGASD") for s in series):
        raise CapacityRefusal("the Book C universe is KXAAAGASD* series only")
    if series != tuple(BOOK_C_SERIES):
        raise CapacityRefusal("the universe differs from the declared 43 series")
    return series


def load_capacity_declaration(path):
    """sha256-pin the declaration, parse it and assert it equals the constants hard-coded here.  -> sha256."""
    if not path:
        raise CapacityRefusal("--capacity-declare is required")
    p = Path(path)
    if not p.is_file():
        raise CapacityRefusal(f"--capacity-declare {path} is not a file")
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != CAPACITY_DECLARE_SHA256:
        raise CapacityRefusal(f"capacity declaration sha256 {got} does not match the pinned {CAPACITY_DECLARE_SHA256}")
    series, fc = parse_declaration(p.read_text())
    if fc != FC_ISO:
        raise CapacityRefusal(f"declaration FC {fc} differs from the hard-coded {FC_ISO}")
    if series != tuple(BOOK_C_SERIES):
        raise CapacityRefusal("declaration series differ from the hard-coded Book C universe")
    check_universe(series)
    return got


# ------------------------------------------------------------------------------------------ the override
def check_sim_pin():
    """The simulator file must be the pinned one; a Book C number from another file would be unattributable."""
    got = sim.file_md5(sim.__file__)
    if got != SIM_MD5_PINNED:
        raise CapacityRefusal(f"tools/kalshi_lip_sim.py md5 {got} is not the pinned {SIM_MD5_PINNED}")
    return got


@contextlib.contextmanager
def book_c_universe(series=None):
    """Rebind the simulator's universe / freeze constants to Book C for the duration of the block; restore on exit
    (also on an exception).  Not thread-safe; not re-entrant (refuses to nest)."""
    series = check_universe(BOOK_C_SERIES if series is None else series)
    now = {n: getattr(sim, n) for n in PATCHED}
    if now != F1_VALUES:
        raise CapacityRefusal("the simulator constants are not LIP-F1's (already overridden?): refusing to nest")
    # a NEW dict: the simulator's own baseline dict is never mutated; coverage() indexes the key "obrec-1019c"
    baseline = dict(F1_VALUES["BASELINE_GAP_S"], **{"obrec-1019c": BOOK_C_BASELINE_S})
    override = {"FREEZE_ISO": FC_ISO, "FREEZE_US": FC_US, "GAS_SERIES": series, "OTHER_SERIES": (),
                "SERIES_ADMITTED": series, "PRIMARY_RUN": BOOK_C_RECORDER, "BASELINE_GAP_S": baseline}
    assert set(override) == set(PATCHED)
    try:
        for n, v in override.items():
            setattr(sim, n, v)
        yield
    finally:
        for n, v in F1_VALUES.items():
            setattr(sim, n, v)


# ------------------------------------------------------------------------------------------ labels
def labels():
    return {"book": BOOK, "report_only": True, "promotable": False,
            "book_label": "LIP-F1 capacity Book C, report-only (DECLARE-FORWARD-CAPACITY-1): not a gate book",
            "capacity_declare_sha256": CAPACITY_DECLARE_SHA256, "capacity_freeze": FC_ISO,
            "capacity_universe": list(BOOK_C_SERIES), "capacity_recorder": BOOK_C_RECORDER,
            "capacity_registry_pins": dict(REGISTRY_PINS), "capacity_sim_md5_pinned": SIM_MD5_PINNED,
            "capacity_tool_md5": sim.file_md5(__file__), "capacity_notes": list(NOTES),
            "capacity_disclosures": {"D1": {"text": D1_TEXT}, "D2": D2_TEXT}}


def banner():
    return (f"REPORT-ONLY CAPACITY BOOK C (DECLARE-FORWARD-CAPACITY-1 sha256 {CAPACITY_DECLARE_SHA256[:8]}): "
            f"{len(BOOK_C_SERIES)} gas daily series, freeze {FC_ISO}; not a gate book, never promotable; "
            "LIP-F1's read, gates, kill condition and verdict are unchanged")


def table_header():
    return "\n".join([banner(), "D1: " + D1_TEXT, "D2: " + D2_TEXT]) + "\n"


# ------------------------------------------------------------------------------------------ disclosures
def blind_share(out):
    """D1, measured: the blind time of the opened units as the simulator counts it (``blind_s``: the part of each gap
    between consecutive in-window snapshots that exceeds CAP; the head and the tail of a window are not counted),
    against the unit windows [quote_start, E_eff) and against the quoted time.  Read back from our own units.parquet."""
    p = Path(out) / "units.parquet"
    if not p.exists():
        return {"available": False, "reason": "no units.parquet in --out"}
    import pyarrow.parquet as pq
    cols = ["cell", "status", "S0", "first_poll_ts", "E_eff", "quoted_s", "blind_s", "blind_gaps"]
    rows = pq.read_table(p, columns=cols).to_pylist()
    res = {"available": True, "cap_s": sim.CAP_US / sim.US,
           "note": "blind_s as the simulator counts it; share_of_window = blind_s / (E_eff - quote_start), "
                   "share_of_quoted_time = blind_s / quoted_s; sums over opened units"}
    for cell in sim.CELLS:
        sel = [r for r in rows if r["cell"] == cell and r["status"] == "opened"]
        win = sum((sim.iso_to_us(r["E_eff"]) - max(sim.iso_to_us(r["S0"]), sim.iso_to_us(r["first_poll_ts"]))) / sim.US
                  for r in sel)
        blind, quoted = sum(r["blind_s"] or 0.0 for r in sel), sum(r["quoted_s"] or 0.0 for r in sel)
        res[cell] = {"n_units": len(sel), "blind_gaps": sum(r["blind_gaps"] or 0 for r in sel), "blind_s": round(blind, 3),
                     "quoted_s": round(quoted, 3), "window_s": round(win, 3),
                     "share_of_window": round(blind / win, 6) if win else None,
                     "share_of_quoted_time": round(blind / quoted, 6) if quoted else None}
    return res


def median_gap_disclosure(root, read_us, strict_runs=False):
    """DISCLOSURE ONLY, never a baseline (QP ruling (2) on #736): the measured per-market gap between consecutive
    snapshots of the Book C recorder over 2026-10-20T00:00Z..2026-10-21T00:00Z, from timestamps only, reported
    when that day is complete in the data: sealed snapshots exist before the day and at/after its end (and
    before R) and R is not before the end of the day."""
    import re
    import numpy as np
    lo, hi = (sim.iso_to_us(x) for x in DISCLOSURE_DAY)
    out = {"purpose": "disclosure only; never a baseline", "recorder": BOOK_C_RECORDER, "timestamps_only": True,
           "window": list(DISCLOSURE_DAY), "available": False}
    if read_us is None or read_us < hi:
        out["reason"] = "the read instant is before the end of the day"
        return out
    try:
        entries, _unsealed = sim.sealed_entries(root, strict_runs=strict_runs)
        by_date = {}
        for f in sim.sealed_files(root, entries, "orderbook_snaps"):
            m = re.search(r"date=(\d{4}-\d{2}-\d{2})", str(f))
            if m:
                by_date.setdefault(m.group(1), []).append(f)
        day, nxt = DISCLOSURE_DAY[0][:10], DISCLOSURE_DAY[1][:10]
        if not any(d < day for d in by_date) or day not in by_date or nxt not in by_date:
            out["reason"] = "no sealed snapshot partitions before, during and after the day"
            return out
        t_nxt = sim.read_sealed(root, entries, "orderbook_snaps", ("ts",), files=by_date[nxt])
        ts_nxt = sim.ts_col_us(t_nxt["ts"])
        if not ((ts_nxt >= hi) & (ts_nxt < read_us)).any():
            out["reason"] = "no sealed snapshot at/after the end of the day before R"
            return out
        t = sim.read_sealed(root, entries, "orderbook_snaps", ("ts", "ticker"), files=by_date[day])
    except (kp.SealError, OSError, ValueError, KeyError) as e:
        out["reason"] = f"{type(e).__name__}: {e}"
        return out
    ts, tk = sim.ts_col_us(t["ts"]), np.asarray(t["ticker"].to_pylist(), dtype=object)
    keep = (ts >= lo) & (ts < hi) & (ts < read_us)
    ts, tk = ts[keep], tk[keep]
    gaps, meds = [], []
    for name in sorted(set(tk.tolist())):
        g = np.diff(np.sort(ts[tk == name]))
        if len(g):
            gaps.append(g)
            meds.append(float(np.median(g)))
    if not gaps:
        out["reason"] = "no market has two snapshots in the day"
        return out
    allg = np.concatenate(gaps)
    out.update({"available": True, "n_markets": len(gaps), "n_gaps": int(len(allg)),
                "median_gap_s_pooled": round(float(np.median(allg)) / sim.US, 3),
                "median_of_market_median_gaps_s": round(float(np.median(meds)) / sim.US, 3)})
    return out


def relabel_coverage(gate, disclosure=None):
    """In a gate dict: the coverage baselines are Book C's (the simulator's dict key 'obrec-1019c' carried 350.0 during
    the call): relabel the key obrec-1019e, cite the ruling, and put the median-gap disclosure beside the block."""
    cov = gate.get("coverage")
    if not isinstance(cov, dict):
        return gate
    b = cov.get("baselines_s")
    if isinstance(b, dict) and "obrec-1019c" in b:
        b = dict(b)
        b[BOOK_C_RECORDER] = b.pop("obrec-1019c")
        cov["baselines_s"] = b
    cov["baseline_ruling"] = BASELINE_RULING
    if disclosure is not None:
        cov["disclosure_only"] = {"median_gap_obrec_1019e_2026_10_20": disclosure}
    return gate


def table_footer(gate):
    d1 = (gate.get("capacity_disclosures") or {}).get("D1", {}).get("measured")
    lines = []
    if isinstance(d1, dict) and d1.get("available"):
        for c in sim.CELLS:
            m = d1[c]
            lines.append(f"D1 measured {c}: blind {m['blind_s']} s of {m['window_s']} s window "
                         f"(share {m['share_of_window']}), of {m['quoted_s']} s quoted ({m['share_of_quoted_time']}), "
                         f"{m['blind_gaps']} blind gaps over {m['n_units']} units")
    w = (gate.get("capacity_disclosures") or {}).get("D1", {}).get("w_above_2x_median_gap")
    if w:
        lines.append(f"D1 w_above_2x_median_gap (obrec-1019e): {w}")
    cov = gate.get("coverage") or {}
    if "baselines_s" in cov:
        lines.append(f"coverage baselines (s per round / poll): {cov['baselines_s']}; {BASELINE_RULING.split('.')[0]}")
    md = (cov.get("disclosure_only") or {}).get("median_gap_obrec_1019e_2026_10_20")
    if md:
        lines.append(f"disclosure only, never a baseline: {md}")
    return "\n".join(lines) + ("\n" if lines else "")


# ------------------------------------------------------------------------------------------ guards
def make_cfg(**kw):
    """A ``sim.Cfg`` for Book C.  The Book C identity (book, freeze, registry pin, no scan) cannot be passed in."""
    for k in ("book", "freeze_us", "registry_pins", "scan", "asof_us"):
        if k in kw:
            raise CapacityRefusal(f"{k} is fixed for Book C and cannot be passed")
    return sim.Cfg(book=BOOK, freeze_us=FC_US, registry_pins=dict(REGISTRY_PINS), **kw)


def check_cfg(cfg):
    if cfg.book != BOOK or cfg.freeze_us != FC_US:
        raise CapacityRefusal("not a Book C configuration (book / freeze)")
    if cfg.scan or cfg.asof_us is not None:
        raise CapacityRefusal("--scan / --asof are refused for Book C: it has no counts rule or read instant of its own; "
                              "pass LIP-F1's R with --read-instant")
    if float(cfg.size) != SIZE or float(cfg.imax) != IMAX:
        raise CapacityRefusal(f"Book C runs at Book A parameters S={SIZE:g}, I_max={IMAX:g}")
    if not cfg.dry_run and cfg.read_us is None:
        raise CapacityRefusal("--read-instant R (LIP-F1's R) is required without --dry-run")
    if not cfg.dry_run and not cfg.out:
        raise CapacityRefusal("--out is required without --dry-run")
    if cfg.read_us is not None and cfg.read_us <= FC_US:
        raise CapacityRefusal("the read instant must be after the freeze FC")
    if not cfg.books or any(Path(b).name != BOOK_C_RECORDER for b in cfg.books):
        raise CapacityRefusal(f"Book C reads order books from {BOOK_C_RECORDER} only (got {list(cfg.books)})")
    if cfg.force_source not in (None, BOOK_C_RECORDER):
        raise CapacityRefusal(f"--force-source must be {BOOK_C_RECORDER}")
    if cfg.registry_pins != REGISTRY_PINS:
        raise CapacityRefusal("the registry pin is fixed for Book C")


def _literal_prefix(item):
    parts = Path(str(item)).parts
    lit = []
    for p in parts:
        if any(c in p for c in "*?["):
            break
        lit.append(p)
    pre = Path(*lit) if lit else None
    return None if pre is None or pre == Path(pre.anchor) else pre


def _is_book_c_json(path):
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("book") == BOOK and d.get("report_only") is True and d.get("promotable") is False


def check_out(out, inputs=()):
    """--out may be new, empty, a dir without simulator files, or a previous Book C output.  It must not hold
    LIP-F1 simulator files and must not sit inside an input store."""
    out = Path(out)
    if out.exists() and not out.is_dir():
        raise CapacityRefusal(f"--out {out} exists and is not a directory")
    ro = out.resolve()
    for spec in inputs:
        for item in ([spec] if isinstance(spec, (str, Path)) else list(spec or [])):
            pre = _literal_prefix(item)
            if pre is not None and (ro == pre.resolve() or pre.resolve() in ro.parents):
                raise CapacityRefusal(f"--out {out} is inside the input store {pre}")
    if not out.exists():
        return
    held = [f for f in SIM_OUTPUT_FILES if (out / f).exists()]
    if not held:
        return
    mine = (out / MARKER).is_file() and _is_book_c_json(out / MARKER)
    if mine and (out / "gate.json").exists():
        mine = _is_book_c_json(out / "gate.json")
    if not mine:
        raise CapacityRefusal(f"--out {out} already holds LIP-F1 simulator files {held} that are not a Book C output; "
                              "Book C writes to its own directory")


# ------------------------------------------------------------------------------------------ the run
def _dump(d):
    return json.dumps(d, sort_keys=True, indent=1, default=str) + "\n"


def run_book_c(cfg):
    """Run the frozen simulator on Book C.  Returns the simulator's dict (gate, or the dry-run structure report)
    with the Book C labels merged in; the files in cfg.out carry the same labels."""
    check_sim_pin()
    check_cfg(cfg)
    lab = labels()
    out = Path(cfg.out) if cfg.out else None
    writing = out is not None and not cfg.dry_run
    if writing:
        check_out(out, [cfg.books, cfg.programs, cfg.meta, cfg.trades, cfg.markets, cfg.series])
    with book_c_universe():
        if writing:
            out.mkdir(parents=True, exist_ok=True)
            (out / MARKER).write_text(_dump(dict(lab, files=list(SIM_OUTPUT_FILES))))
        res = sim.run_sim(cfg)
    if cfg.dry_run:
        res.update(lab)
        return res
    # measured disclosures (full runs only): D1 blind share and the simulator's own gap report; the median-gap
    # disclosure beside the coverage block
    disclosure = None
    if isinstance(res.get("coverage"), dict):
        disclosure = median_gap_disclosure(cfg.books[0], cfg.read_us, cfg.strict_runs)
    d1 = lab["capacity_disclosures"]["D1"]
    d1["measured"] = blind_share(out) if "cells" in res and res["cells"] else {"available": False, "reason": "no cells computed"}
    d1["w_above_2x_median_gap"] = (res.get("w_above_2x_median_gap") or {}).get(BOOK_C_RECORDER)
    d1["w_above_2x_median_gap_source"] = ("the simulator's gate.w_above_2x_median_gap report for obrec-1019e "
                                          "(mapped to the CONDITIONS 13 count named by the QP verdict on #736)")
    g = out / "gate.json"
    if g.exists():
        gate = json.loads(g.read_text())
        gate.update(lab)
        g.write_text(_dump(relabel_coverage(gate, disclosure)))
    res.update(lab)
    relabel_coverage(res, disclosure)
    t = out / "table.txt"
    if t.exists():
        t.write_text(table_header() + t.read_text() + table_footer(res))
    return res


# ------------------------------------------------------------------------------------------ CLI
def build_parser():
    with book_c_universe():            # so --freeze's help names FC, not LIP-F1's freeze
        p = sim.build_parser()
    p.prog = "kalshi_lip_capacity"
    p.description = ("LIP-F1 capacity Book C (report-only): the frozen simulator on the 43-series universe; "
                     "defaults: --books " + DEFAULT_BOOKS + " --programs " + DEFAULT_PROGRAMS + " --meta " + DEFAULT_META
                     + " --trades/--markets '" + DEFAULT_RUNS + "'")
    p.add_argument("--capacity-declare", required=True,
                   help=f"DECLARE-FORWARD-CAPACITY-1.md (sha256 pinned {CAPACITY_DECLARE_SHA256[:8]}...)")
    return p


def _refuse_args(a):
    try:
        freeze = sim.iso_to_us(a.freeze)
    except ValueError as e:
        raise CapacityRefusal(f"--freeze: {e}")
    if freeze != FC_US:
        raise CapacityRefusal(f"--freeze must be {FC_ISO}")
    if a.scan or a.asof:
        raise CapacityRefusal("--scan / --asof are refused for Book C (no counts rule of its own); "
                              "pass LIP-F1's R with --read-instant")
    for item in a.registry_sha256:
        name, _, hexd = item.partition("=")
        if REGISTRY_PINS.get(name) != hexd:
            raise CapacityRefusal(f"--registry-sha256 {item}: the registry pin is fixed for Book C")
    if not a.dry_run:
        for need in ("out", "series"):
            if not getattr(a, need):
                raise CapacityRefusal(f"--{need} is required without --dry-run")
        if not a.read_instant:
            raise CapacityRefusal("give LIP-F1's read instant with --read-instant R")


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        load_capacity_declaration(a.capacity_declare)
        _refuse_args(a)
        if not a.dry_run:
            sim._verify_pin(a.declare, sim.DECLARE_SHA256, "declare")
            sim._verify_pin(a.conditions, sim.CONDITIONS_SHA256, "conditions")
            sim._verify_pin(a.conditions2, sim.CONDITIONS2_SHA256, "conditions2")
        cfg = make_cfg(books=a.books or [DEFAULT_BOOKS], programs=a.programs or DEFAULT_PROGRAMS,
                       meta=a.meta or DEFAULT_META, trades=a.trades or [DEFAULT_RUNS], markets=a.markets or [DEFAULT_RUNS],
                       series=a.series, out=a.out, size=a.size, imax=a.imax,
                       read_us=None if not a.read_instant else sim.iso_to_us(a.read_instant), dry_run=a.dry_run,
                       strict_runs=a.strict_runs, force_source=a.force_source, mem_cap_mb=a.mem_cap_mb,
                       debug_snaps=a.debug_snaps, units_evidence=a.units_evidence, stress=not a.no_stress)
    except (CapacityRefusal, sim.InputError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    try:
        res = run_book_c(cfg)
    except CapacityRefusal as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except (sim.InputError, kp.SealError, MemoryError, ValueError, KeyError, OSError) as e:
        print(f"refused: {type(e).__name__}: {e}", file=sys.stderr)
        return 4
    if cfg.dry_run:
        print(json.dumps(res, sort_keys=True, indent=1, default=str))
    else:
        print(table_header() + sim.format_table(res))
        print(table_footer(res), end="")
        print(f"peak RSS {res.get('_peak_rss_mb')} MB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
