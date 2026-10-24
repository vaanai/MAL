"""Arena H5 report legs (hunt20): the two REPORT-ONLY legs the H5 arena read must carry beside its deciding figures. PAPER ONLY. READ-ONLY on the ledger and the shadow
strips. It never changes a deciding figure and writes nothing except an optional --json file.

    python -m tools.arena_h5_legs --bot h5v1-boostfloor-a1 --ledger-root <arena>/ledger --read-instant MS --i-am-the-read [--shadow-dir <arena>/h5-DATE ...] [--json OUT] [--now-ms MS]

WHY A SEPARATE TOOL. tools/arena_report_legs.py (PR #747) cannot serve H5: an H5 ledger row has no `calc` / `by_rule` / `stack` (tools/arena_h5_score.py, LEDGER section), its states live in
the shadow's STRIP records (which #747 refuses by name), and H5's own cost layer (tools/arena_h5_cost.py) blends a stranded trip with the fail leg (`flat_leg` / `press_leg` blend every state
that is not "guarded") where the arena engine's "total" rule bypasses it. So the cells here are priced with arena_h5_cost's own functions, the pressure intercept is arena_h5_score.refit_cells
(per stake and latency leg, on the counted rows before the read instant), and the statistics are tools.arena_score.cell_stats / cell_verdict, the gate's own. From tools/arena_report_legs.py
(PR #747) this file depends on three functions by NAME and copies none of them: `slot_states`, `worst_index`, `end_minus_worst`. If #747 renames them, this file stops at import.
Strand rules: H5's "dec031" strand class is labelled "total" here, as in DEC-031 section 2; "sc" is the same.

THE H5 BOOKS DECIDE RENT-CHARGED (DEC-031 Amendment 2 item 10): 2,039,280 lamports on every trip whose leg state is `filled`. Nothing here changes that.

LEG (a), RENT 0 (Am.2 item 9 and item 10, QP ruling on PR #747 item 11 (a)). The same rows with the ATA rent refunded:
    flat leg      plus 0.85 x 2,039,280 lamports
    pressure leg  plus (1 - p_i) x 2,039,280 lamports, p_i = the cell's OWN refit probability for that trip (arena_h5_score.refit_cells, unchanged: rent moves no input of it)
The selector is the leg STATE under the strand rule of the cell: only `state == "filled"` is credited. Rows in state guarded, stranded, entry_invalid or exit_invalid are unchanged under both
rules, so an `entry_invalid` / `exit_invalid` row under "sc" is never credited (its pnl carries the 2,039,280 of strand rent). Every cell carries `n_credited`. It is an exact transform; the
tests compare it with a full re-price at rent 0. LABEL: "report-only, added after the clock, never deciding" (Am.2 item 10: a post-clock switch in the favourable direction is a researcher
degree of freedom, which is why it never decides).

LEG (b), WORST-IN-SLOT (QP ruling R7, ARTIFACTS/lab/qp-ruling-1019-cohort2.md lines 161-172; QP comment 6275640945 items 1-3 and 11 (b)), from the H5 books' own state rows: the shadow's `strip`
records (pre-trade Q incl. V and base of every print from the trigger print on, and the state after the last). The scorer fills END-bound: the pre-trade state of the first print in a slot later
than the landing slot (arena_h5_score.Strip.state_at). Worst-in-slot instead prices each leg at the worst state its slot could have shown:
    states of slot x   the PRE-trade state of every print whose slot is x, plus the END state (the first print later than x, or the state after the last print)
    entry              the highest-price state among the states of the leg's landing slot (meta.landing_slots)
    exit               the lowest-price state among the states of the exit landing slot (meta.exit_trigger_slots + meta.exit_lag_slots)
    price of a state   Q / B (Q includes V)          ties: the first state in strip order
The decision-time spot (meta.spot), ssb, nb, stake and the stack are the ledger's. The own buy is re-injected into the exit state by arena_h5_cost (qa = qx + net, ba = bx - tokens).
The pressure intercept is refit on THIS cell's filled set (cost.press_intercept); both intercepts are printed. The cell is rent-charged, as H5 decides, and is also given stacked with the rent-0 leg.
Printed beside it (QP items 2 and 3):
    * the END-minus-worst gap (mean and total) from #747's `end_minus_worst`;
    * a COMPANION "worst-in-slot, no guard rescue": a trip that fills at END and is guarded at its worst entry state keeps its END-priced row under that rule and leg (with the 1.15 guard a
      spot-selected worst entry can revert a trip that loses at END, so the plain leg is not a bound). R7's canary condition uses the LOWER of the two legs (`canary_lower`, #747's semantics: min of the means and of the CI90 bounds, with `leg_of_mean`);
    * the END -> worst state transitions per cell, the trips whose worst pick was a non-finite or invalid state, and the derived_last count (END taken after the pool's last print) with the gap
      recomputed without those trips. The END member of a slot is the scorer's own END state, read from the same strip, so it cannot differ from what the scorer priced.
Consistency check: for every leg of every trip the END-priced pnl and state recomputed from the strip must equal the ledger row's (both strand rules). A mismatch (a strip that is not the one
the scorer priced, a non-monotone slot column) makes the leg "worst-in-slot not measured: END consistency failed (k state, m pnl mismatches)": no worst-in-slot figure is printed or written,
the rent-0 leg still prints, and the exit code is 4. Without --shadow-dir the leg says "worst-in-slot not measured (no --shadow-dir)": R7 then allows no H5 canary until it is measured.

THE SEAL. The H5 books' outputs are sealed until the read (EXP-024 Look-2 seal; DEC-031 section 3.5). This tool refuses to open any ledger row unless ALL of these hold, checked before the first row
is opened: --i-am-the-read is given; --read-instant (ms) is at or after the book's RECORDED read instant (<ledger>/<bot>/read-instant.json, a counts-only file written by `arena_h5_score score`);
--now-ms (default: now) is not before it; no attempt decided before it is still open; the ledger holds every attempt and refusal the record counts. The trade set is the scorer's own
(arena_h5_score.ledger_attempts_before at the RECORDED instant). The gate applies to every path, real or fixture. Both the bot_id and every path (ledger root, shadow dirs, --json) are
refused if they contain probe, canary or _shakedown, the other closed names of arena_feed_delay.FORBIDDEN_PARTS, or a live-shadow / live-executor path. There is no default for any path.
Nothing here is a verdict and nothing is a profit claim; a PASS elsewhere is a screen (best of N, winner's curse).
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_h5_books as B
from tools import arena_h5_cost as cost
from tools import arena_h5_score as h5s
from tools.arena_score import Refused, cell_stats, cell_verdict

try:
    from tools import arena_report_legs as RL  # PR #747: slot_states, worst_index, end_minus_worst (depended on by name, not copied)
except ImportError as exc:  # pragma: no cover - depends on the merge order
    raise ImportError("tools/arena_h5_legs.py depends on tools/arena_report_legs.py (PR #747: slot_states, worst_index, end_minus_worst): merge #747 first") from exc

RENT = float(cost.RENT)  # 2,039,280: the deciding rent of the H5 books
RULES = ("total", "sc")  # "total" = arena_h5_cost strand "dec031"
SFXS = tuple(sfx for _, sfx, _ in h5s.LEGS)  # ("1p3", "1p9")
LEG_OF = {sfx: leg for leg, sfx, _ in h5s.LEGS}
FORBIDDEN_PARTS = ("probe", "canary", "_shakedown", "sealed", "walk", "exp011", "exp012", "tip-tape", "meta-capture", "fresh-08", "truth-", "forward-10")
LIVE_PARTS = ("/data/h5-shadow", "mal-h5-shadow", "mal-live")  # the live shadow's and the live executor's paths (scripts/research/arena-h5.sh refuses them too)
PARITY_PNL_ATOL = 1e-6
LABEL_RENT0 = "report-only, added after the clock, never deciding"
BANNER = ("REPORT-ONLY LEGS beside the H5 deciding figures (DEC-031 Amendment 2 items 9 and 10; QP ruling R7). The H5 books DECIDE RENT-CHARGED (2,039,280 lamports per filled trip). "
          f"The rent-0 leg is {LABEL_RENT0}. Worst-in-slot is never deciding. Nothing here is a profit claim.")


# ------------------------------------------------------------------------------------------------------------------------------ paths and the seal
def _check_text(text: str, what: str) -> None:
    low = text.lower()
    for part in FORBIDDEN_PARTS + LIVE_PARTS:
        if part in low:
            raise Refused(f"{what} contains {part!r}: this report never reads probe, canary, shakedown, sealed or live-shadow material")


def check_path(path: str | os.PathLike, what: str = "path") -> Path:
    """Refuse a path that contains a closed name. The string is checked BEFORE any filesystem access, then again with symlinks resolved."""
    _check_text(os.path.abspath(os.fspath(path)), f"{what} {os.fspath(path)!r}")
    _check_text(os.path.realpath(os.fspath(path)), f"{what} {os.fspath(path)!r} (resolved)")
    return Path(path)


class Ended(Refused):
    """The Q50 book ENDED NOT_DECIDABLE at the end-seal (DEC-031 Amendment 11 item 2): final, counts only, no ledger row is opened. `.doc` is the write-once record."""

    def __init__(self, msg: str, doc: Mapping[str, Any]):
        super().__init__(msg)
        self.doc = dict(doc)


def gate(bot_id: str, ledger_root: str | os.PathLike, read_instant_ms: int | None, i_am_the_read: bool, now_ms: int, books: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The seal. Raises Refused before any ledger row is opened; returns the recorded read-instant document (counts only). `books` (tools/arena_h5_books.load_books) widens the
    accepted ids to the registered books file; without it only the scorer's a1 BOTS are accepted, as before. The record is the book's own instant file
    (arena_h5_books.instant_file): for Q50 the Amendment 11 write-once file, never the scorer's read-instant.json."""
    _check_text(bot_id, "bot_id")
    if books is None and bot_id not in h5s.BOTS:
        raise Refused(f"{bot_id!r} is not an H5 arena book {sorted(h5s.BOTS)}")
    if books is not None and bot_id not in books:
        raise Refused(f"{bot_id!r} is not in the books file {sorted(books)}")
    check_path(ledger_root, "ledger root")
    if not i_am_the_read:
        raise Refused("--i-am-the-read is required: the H5 outputs are sealed until the read job")
    if read_instant_ms is None:
        raise Refused("--read-instant is required: the H5 outputs are sealed until the book's recorded read instant")
    fname = h5s.READ_INSTANT_FILE if books is None else B.instant_file(bot_id)
    rec = Path(ledger_root) / bot_id / fname
    if not rec.is_file():
        raise Refused(f"{bot_id}: no recorded read instant ({fname} absent): the score has not reached it, nothing is opened")
    doc = json.loads(rec.read_text())
    if fname == B.qi.INSTANT_FILE:
        if doc.get("rule") != B.qi.RULE or doc.get("bot_id") != bot_id:
            raise Refused(f"{bot_id}: {fname} is not an Amendment 11 record of this book")
        if doc.get("status") == B.qi.ENDED:
            raise Ended(f"{bot_id}: ENDED NOT_DECIDABLE at the end-seal (DEC-031 Amendment 11 item 2): no read instant, nothing is opened", doc)
        if doc.get("status") != B.qi.READ:
            raise Refused(f"{bot_id}: {fname} status {doc.get('status')!r} is not READ")
    ri = int(doc["read_instant_ms"])
    if int(read_instant_ms) < ri:
        raise Refused(f"--read-instant {int(read_instant_ms)} is before the book's recorded read instant {ri}: sealed")
    if int(now_ms) < ri:
        raise Refused(f"now {int(now_ms)} is before the book's recorded read instant {ri}: sealed")
    if doc.get("open_before_read"):
        raise Refused("attempts decided before the read instant are still open")
    return doc


def load_book_rows(bot_id: str, ledger_root: str | os.PathLike, doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The scorer's trade set at the recorded instant (arena_h5_score.ledger_attempts_before, as cmd_refit uses it), with cmd_refit's reconciliation refusals."""
    ri = int(doc["read_instant_ms"])
    led = h5s.ledger_attempts_before(Path(ledger_root) / bot_id, ri)
    if led["superseded_trade_pools"]:
        raise Refused(f"the ledger holds trade rows for {led['superseded_trade_pools']} pool(s) that are refusals under DEC-031 Amendment 1: they need a ruling")
    if led["attempts"] != doc.get("attempts_before") or len(led["refusals"]) != doc.get("refused_before"):
        raise Refused("the ledger does not hold every attempt and every refusal decided before the read instant (score again)")
    rows = led["rows"]
    for r in rows:
        m = r.get("meta") or {}
        if m.get("schema") != h5s.SCHEMA or m.get("stack") != cost.STACK_ID:
            raise Refused(f"trade {r.get('trade_id')}: schema {m.get('schema')!r} / stack {m.get('stack')!r} is not {h5s.SCHEMA} / {cost.STACK_ID}: the rent delta would not be {RENT:.0f}")
    return rows


# ------------------------------------------------------------------------------------------------------------------------------ cells
def leg_state(row: Mapping[str, Any], sfx: str, rule: str) -> tuple[float, str]:
    """(no-fail pnl in lamports, leg state) of a ledger row under a strand rule."""
    f = row["legs"][f"flat_{sfx}"]
    d = f if rule == "total" else f["sc"]
    return float(d["nofail_lamports"]), str(d["state"])


def items_from_rows(rows: Sequence[Mapping[str, Any]], sfx: str) -> list[dict[str, Any]]:
    """The cell members of one latency leg, as the scorer sees them: rows whose leg was priced (the deciding leg always is)."""
    out = []
    for r in rows:
        if r["legs"][f"flat_{sfx}"].get("net_lamports") is None:
            continue
        pr = r["legs"][f"press_{sfx}"]
        out.append({"trade_id": r["trade_id"], "pnl": {ru: leg_state(r, sfx, ru) for ru in RULES}, "ssb": pr["ssb"], "nb": pr["nb_lamports"], "day": h5s.utc_day(r["decision_ms"]),
                    "derived_last": "post" in tuple((r["meta"].get("state_src") or {}).get(sfx) or ())})
    return out


def own_intercept(items: Sequence[Mapping[str, Any]]) -> float:
    """The cell's own refit (cost.press_intercept on its filled set: every state but guarded, the same under both strand rules)."""
    return cost.press_intercept([it["ssb"] for it in items], [it["nb"] for it in items], [cost.is_filled(it["pnl"]["total"][1]) for it in items])


def cell_values(items: Sequence[Mapping[str, Any]], rule: str, intercept: float, rent0: bool = False) -> dict[str, np.ndarray]:
    """Per-trade flat and pressure legs (lamports) of one (strand rule, latency) cell with arena_h5_cost's own functions. rent0 credits RENT x (1 - fail share) on `state == "filled"` trips only."""
    nf = [it["pnl"][rule][0] for it in items]
    st = [it["pnl"][rule][1] for it in items]
    p = np.array([cost.press_p(intercept, it["ssb"], it["nb"]) for it in items], float)
    flat = np.array([cost.flat_leg(x, s) for x, s in zip(nf, st)], float)
    press = np.array([cost.press_leg(x, s, pp) for x, s, pp in zip(nf, st, p)], float)
    cred = np.array([s == "filled" for s in st], bool)
    if rent0:
        flat = flat + (1.0 - cost.FAIL_FLAT) * RENT * cred
        press = press + (1.0 - p) * RENT * cred
    return {"flat": flat, "press": press, "p": p, "credited": cred}


def cells_of(items_by_sfx: Mapping[str, Sequence[Mapping[str, Any]]], intercepts: Mapping[str, float] | None = None, rent0: bool = False) -> dict[str, dict[str, Any]]:
    """The scorer's gate statistics of every model x latency x strand-rule cell of one (bot, stake) book. intercepts None: each cell refits its own."""
    cells: dict[str, dict[str, Any]] = {}
    for sfx, items in items_by_sfx.items():
        if not items:
            continue
        c = (intercepts or {}).get(sfx)
        c = own_intercept(items) if c is None else c
        days = [it["day"] for it in items]
        for rule in RULES:
            v = cell_values(items, rule, c, rent0)
            for model in ("flat", "press"):
                s = cell_stats(v[model], days)
                gc = cell_verdict(s)
                cells[f"{model}_{sfx}_{rule}"] = dict(s, intercept=c if model == "press" else None, n_credited=int(v["credited"].sum()) if rent0 else 0,
                                                      gate_check={"report_only": True, "criteria_met": gc["pass"], "misses": gc["misses"]})
    return cells


def check_ledger_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    """The ledger's own nets must be what arena_h5_cost gives from the row's no-fail pnl, state and frozen exploration intercept (both strand rules), or these are not the scorer's rows."""
    for r in rows:
        for sfx in SFXS:
            f, pr = r["legs"][f"flat_{sfx}"], r["legs"][f"press_{sfx}"]
            if f.get("net_lamports") is None:
                continue
            p = cost.press_p(cost.H5_PRESS_INTERCEPT[LEG_OF[sfx]], pr["ssb"], pr["nb_lamports"])
            for rule in RULES:
                nf, st = leg_state(r, sfx, rule)
                fl = f if rule == "total" else f["sc"]
                pl = pr if rule == "total" else pr["sc"]
                for got, want, what in ((fl["net_lamports"], cost.flat_leg(nf, st), "flat"), (pl["net_lamports"], cost.press_leg(nf, st, p), "press")):
                    if abs(got - want) > PARITY_PNL_ATOL + 1e-9 * abs(want):
                        raise Refused(f"trade {r['trade_id']} {what}_{sfx} {rule}: ledger net {got!r} != arena_h5_cost's {want!r}: these are not the scorer's rows")


# ------------------------------------------------------------------------------------------------------------------------------ worst-in-slot
def load_strips(shadow_dirs: Sequence[str | os.PathLike], pools: set[str]) -> dict[str, dict[str, Any]]:
    """The shadow's strip records of the wanted pools (the scorer's own reader, first record of a pool wins, dirs in name order as score_book sorts them). Nothing else is kept."""
    out: dict[str, dict[str, Any]] = {}
    counts: collections.Counter = collections.Counter()
    for d in sorted((Path(x) for x in shadow_dirs), key=lambda x: x.name):
        for rec in h5s.read_records(d, counts):
            if rec.get("type") == "strip" and rec.get("pool") in pools:
                out.setdefault(rec["pool"], rec)
    return out


def strip_arrays(rec: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """sl (slot per print), QX / BX (the PRE-trade state of print i for i < n, the state after the last print at n; Q includes V), the layout RL.slot_states and RL.worst_index read."""
    rows, pl = rec["rows"], rec["post_last"]
    return {"sl": np.array([r[0] for r in rows], np.int64), "QX": np.array([float(r[1]) for r in rows] + [float(pl[1])], float),
            "BX": np.array([float(r[3]) for r in rows] + [float(pl[3])], float)}


def _pair(lam: int, qe: float, be: float, spot: float, qx: float, bx: float) -> dict[str, dict[str, Any]]:
    return {"total": cost.deciding_round_trip(lam, qe, be, spot, qx, bx), "sc": cost.deciding_round_trip(lam, qe, be, spot, qx, bx, strand="sc")}


def _bad(sa: Mapping[str, np.ndarray], i: int) -> bool:
    with np.errstate(all="ignore"):
        return bool(not np.isfinite(sa["QX"][i] / sa["BX"][i]) or sa["BX"][i] <= 0)


def worst_trip(row: Mapping[str, Any], sfx: str, rec: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """One ledger row and latency leg re-priced at the worst states of its entry and exit slots. Returns ({rule: {"end", "worst", "companion"}}, diag)."""
    sa = strip_arrays(rec)
    m = row["meta"]
    lam = int(round(row["stake_sol"] * 1e9))
    land, xland = int(m["landing_slots"][sfx]), int(m["exit_trigger_slots"][sfx]) + int(m["exit_lag_slots"])
    diag: dict[str, Any] = {"state_mismatch": [], "pnl_mismatch": [], "entry_multi_print": 0, "entry_moved": 0, "exit_multi_print": 0, "exit_moved": 0, "invalid_pick": 0}
    if len(sa["sl"]) == 0 or bool(np.any(np.diff(sa["sl"]) < 0)):
        diag["state_mismatch"].append(f"{row['trade_id']}:{sfx}:strip slot column is empty or not monotone")
        return {}, diag
    idx_e, je_e = RL.slot_states(sa, land)
    idx_x, je_x = RL.slot_states(sa, xland)
    we, wx = RL.worst_index(sa, idx_e, "buy"), RL.worst_index(sa, idx_x, "sell")
    diag.update(entry_multi_print=int(len(idx_e) > 2), exit_multi_print=int(len(idx_x) > 2), entry_moved=int(we != je_e), exit_moved=int(wx != je_x),
                invalid_pick=int(_bad(sa, we)) + int(_bad(sa, wx)))
    st = h5s.Strip(rec)
    qe_s, be_s, _ = st.state_at(land)
    qx_s, bx_s, _ = st.state_at(xland)
    if not (qe_s == sa["QX"][je_e] and be_s == sa["BX"][je_e] and qx_s == sa["QX"][je_x] and bx_s == sa["BX"][je_x]):
        diag["state_mismatch"].append(f"{row['trade_id']}:{sfx}")
    spot = float(m["spot"])
    end = _pair(lam, float(sa["QX"][je_e]), float(sa["BX"][je_e]), spot, float(sa["QX"][je_x]), float(sa["BX"][je_x]))
    worst = _pair(lam, float(sa["QX"][we]), float(sa["BX"][we]), spot, float(sa["QX"][wx]), float(sa["BX"][wx]))
    out: dict[str, Any] = {}
    for rule in RULES:
        want_nf, want_st = leg_state(row, sfx, rule)
        if end[rule]["state"] != want_st or abs(end[rule]["pnl"] - want_nf) > PARITY_PNL_ATOL + 1e-9 * abs(want_nf):
            diag["pnl_mismatch"].append(f"{row['trade_id']}:{sfx}:{rule}")
        rescued = end[rule]["state"] != "guarded" and worst[rule]["state"] == "guarded"
        out[rule] = {"end": (end[rule]["pnl"], end[rule]["state"]), "worst": (worst[rule]["pnl"], worst[rule]["state"]),
                     "companion": (end[rule]["pnl"], end[rule]["state"]) if rescued else (worst[rule]["pnl"], worst[rule]["state"])}
    return out, diag


def worst_in_slot(rows: Sequence[Mapping[str, Any]], strips: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The worst-in-slot leg of every stake book. Either {"measured": True, ...cells...} or {"measured": False, "why": "worst-in-slot not measured: ..."}; never partial figures."""
    missing = sorted({r["pool"] for r in rows} - set(strips))
    if missing:
        return {"measured": False, "why": f"worst-in-slot not measured: {len(missing)} pool(s) have no strip record in the shadow dirs given"}
    per: dict[str, dict[str, dict[str, list]]] = {}  # stake -> variant -> sfx -> items
    diag: dict[str, Any] = {"trips": 0, "legs": 0, "derived_last": 0, "derived_last_legs": 0, "entry_multi_print": 0, "entry_moved": 0, "exit_multi_print": 0,
                            "exit_moved": 0, "invalid_pick": 0, "state_mismatch": [], "pnl_mismatch": []}
    trans: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    flips: collections.Counter = collections.Counter()
    nderived: collections.Counter = collections.Counter()  # stake -> trips with a derived_last leg
    for r in rows:
        stake = f"{r['stake_sol']:.2f}"
        diag["trips"] += 1
        trip_derived = False
        for sfx in SFXS:
            base = items_from_rows([r], sfx)
            if not base:
                continue
            res, d = worst_trip(r, sfx, strips[r["pool"]])
            diag["legs"] += 1
            for k in ("entry_multi_print", "entry_moved", "exit_multi_print", "exit_moved", "invalid_pick"):
                diag[k] += d[k]
            diag["state_mismatch"] += d["state_mismatch"]
            diag["pnl_mismatch"] += d["pnl_mismatch"]
            if not res:
                continue
            it0 = base[0]
            diag["derived_last_legs"] += int(it0["derived_last"])
            trip_derived = trip_derived or it0["derived_last"]
            for variant in ("end", "worst", "companion"):
                it = dict(it0, pnl={ru: res[ru][variant] for ru in RULES})
                per.setdefault(stake, {}).setdefault(variant, {}).setdefault(sfx, []).append(it)
            for ru in RULES:
                if res[ru]["end"][1] != res[ru]["worst"][1]:
                    trans[f"{stake}|{sfx}|{ru}"][f"{res[ru]['end'][1]}->{res[ru]['worst'][1]}"] += 1
                if (res[ru]["end"][1] == "guarded") != (res[ru]["worst"][1] == "guarded"):  # the guard (1.15) status changed between END and the worst entry state
                    flips[f"{stake}|{sfx}|{ru}"] += 1
        diag["derived_last"] += int(trip_derived)  # trips (as #747 counts), not legs
        nderived[stake] += int(trip_derived)
    if diag["state_mismatch"] or diag["pnl_mismatch"]:
        return {"measured": False, "diag": diag, "why": f"worst-in-slot not measured: END consistency failed ({len(diag['state_mismatch'])} state, {len(diag['pnl_mismatch'])} pnl mismatches)"}
    diag["parity_ok"] = True
    diag["transitions"] = {k: dict(v) for k, v in sorted(trans.items())}
    diag["guard_flips"] = dict(sorted(flips.items()))
    stakes: dict[str, Any] = {}
    for stake, variants in per.items():
        end_rows = [x for x in rows if f"{x['stake_sol']:.2f}" == stake]
        end_items = {sfx: items_from_rows(end_rows, sfx) for sfx in SFXS}
        refit = h5s.refit_cells(end_rows)
        ic = {sfx: refit[f"{stake}|{sfx}"]["intercept"] for sfx in SFXS if f"{stake}|{sfx}" in refit}
        end_cells = cells_of(end_items, ic)
        w_cells, c_cells = cells_of(variants["worst"]), cells_of(variants["companion"])
        nd = {sfx: [it for it in its if not it["derived_last"]] for sfx, its in end_items.items()}
        nd_w = {sfx: [it for it in its if not it["derived_last"]] for sfx, its in variants["worst"].items()}
        # key names are #747's (legs_for_book): cells, end_minus_worst, no_guard_rescue {cells, end_minus_worst}, canary_lower, end_minus_worst_without_derived_last, n_derived_last
        stakes[stake] = {"cells": w_cells, "end_minus_worst": RL.end_minus_worst(end_cells, w_cells), "no_guard_rescue": {"cells": c_cells, "end_minus_worst": RL.end_minus_worst(end_cells, c_cells)},
                         "canary_lower": canary_lower(w_cells, c_cells), "n_derived_last": nderived[stake],
                         "end_minus_worst_without_derived_last": RL.end_minus_worst(cells_of(nd, None), cells_of(nd_w)) if any(nd.values()) else {},
                         "rent_0_stacked": cells_of(variants["worst"], None, True), "intercepts": {"as_scored": ic, "worst": {k: v["intercept"] for k, v in w_cells.items() if v["intercept"] is not None},
                                                                                                       "companion": {k: v["intercept"] for k, v in c_cells.items() if v["intercept"] is not None}}}
    return {"measured": True, "diag": diag, "stakes": stakes}


def canary_lower(worst: Mapping[str, Mapping[str, Any]], ng: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """R7's canary condition, with #747's (QP-approved) semantics: the LOWER of the worst-in-slot and the no-guard-rescue legs, mean and CI90 lower bound each, cell by cell, and the leg
    that sets the mean (`no_guard_rescue` if its mean is strictly lower, else `worst_in_slot`). Cells without a mean are skipped. A missing CI bound stays missing."""
    lo = lambda x, y: None if x is None or y is None else min(x, y)  # noqa: E731
    return {k: {"mean_sol": min(worst[k]["mean_sol"], ng[k]["mean_sol"]), "ci90_lo_sol": lo(worst[k]["ci90_lo_sol"], ng[k]["ci90_lo_sol"]),
                "leg_of_mean": "no_guard_rescue" if ng[k]["mean_sol"] < worst[k]["mean_sol"] else "worst_in_slot"} for k in worst if worst[k]["mean_sol"] is not None}


# ------------------------------------------------------------------------------------------------------------------------------ one bot
def build_report(bot_id: str, ledger_root: str | os.PathLike, *, read_instant_ms: int | None, i_am_the_read: bool, shadow_dirs: Sequence[str | os.PathLike] = (),
                 now_ms: int | None = None, books: Mapping[str, str] | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    doc = gate(bot_id, ledger_root, read_instant_ms, i_am_the_read, now, books)  # the seal: before any row is opened
    for d in shadow_dirs:
        check_path(d, "shadow dir")
    rows = load_book_rows(bot_id, ledger_root, doc)
    check_ledger_rows(rows)
    ri = int(doc["read_instant_ms"])
    refit = h5s.refit_cells(rows)
    refit_file = Path(ledger_root) / bot_id / f"refit-{h5s.utc_day(ri)}.json"
    rf = "absent"
    if refit_file.is_file():
        rec = json.loads(refit_file.read_text())["cells"]
        for k, v in refit.items():
            if k not in rec or abs(rec[k]["intercept"] - v["intercept"]) > 1e-12:
                raise Refused(f"cell {k}: the recorded refit {refit_file.name} differs from the intercept recomputed from the ledger rows")
        rf = "matched"
    rep: dict[str, Any] = {"bot_id": bot_id, "report_only": True, "banner": BANNER, "read_instant_ms": ri, "deciding_stake": h5s.DECIDING_STAKE, "deciding_leg": "1p9",
                           "deciding_rent_lamports": int(RENT), "deciding_rent_note": "H5 decides RENT-CHARGED (DEC-031 Am.2 item 10); the rent-0 leg is " + LABEL_RENT0,
                           "n_trade_rows": len(rows), "refit": refit, "refit_file": rf, "books": {}}
    by_stake: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by_stake[f"{r['stake_sol']:.2f}"].append(r)
    if shadow_dirs:
        w = worst_in_slot(rows, load_strips(shadow_dirs, {r["pool"] for r in rows}))
    else:
        w = {"measured": False, "why": "worst-in-slot not measured (no --shadow-dir)"}
    rep["worst_in_slot_diag"] = w.get("diag")
    d0 = w.get("diag") or {}
    rep["end_check"] = {"ok": d0.get("parity_ok") if d0 else None, "state_mismatch": d0.get("state_mismatch", []), "pnl_mismatch": d0.get("pnl_mismatch", [])}  # as #747's end_check
    rep["worst_in_slot_measured"] = bool(w["measured"])
    for stake in sorted(by_stake):
        rs = by_stake[stake]
        items = {sfx: items_from_rows(rs, sfx) for sfx in SFXS}
        ic = {sfx: refit[f"{stake}|{sfx}"]["intercept"] for sfx in SFXS if f"{stake}|{sfx}" in refit}
        rep["books"][stake] = {"n_trades": len(rs), "as_scored_rent_charged": cells_of(items, ic), "rent_0": cells_of(items, ic, True),
                               "worst_in_slot": w["stakes"].get(stake) if w["measured"] else w["why"]}
    if books is not None:
        rep["dec032_worst_in_slot"] = dec032_figure(rep)
    return rep


DEC032_NOTE = ("DEC-032 section 1 item 2: the worst-in-slot mean and its CI90 lower bound of the deciding cell (1.9 s leg at the deciding stake, rent-charged), quoted as R7's LOWER of the "
               "worst-in-slot and no-guard-rescue legs, and the lowest over the four sub-cells (flat / pressure x total / sc). 'not measured' means no proposal; for an event-triggered book "
               "(H5) a mean at or below 0 is not proposed. Report-only, never deciding; in-sample.")


def dec032_figure(rep: Mapping[str, Any]) -> dict[str, Any]:
    """The one worst-in-slot figure DEC-032 section 1 item 2 quotes, from this report's canary_lower cells (no new pricing)."""
    b = rep["books"].get(rep["deciding_stake"])
    w = None if b is None else b["worst_in_slot"]
    if not isinstance(w, Mapping):
        why = "no trade at the deciding stake" if b is None else w
        return {"measured": False, "why": why, "proposal": "no proposal (worst-in-slot not measured)", "note": DEC032_NOTE}
    cells = {k: dict(v) for k, v in w["canary_lower"].items() if f"_{rep['deciding_leg']}_" in k}
    if len(cells) != 4:
        return {"measured": False, "why": f"{len(cells)} of 4 deciding sub-cells have a worst-in-slot mean", "proposal": "no proposal (worst-in-slot not measured)", "note": DEC032_NOTE}
    mk = min(cells, key=lambda k: (cells[k]["mean_sol"], k))
    los = [v["ci90_lo_sol"] for v in cells.values()]
    lk = None if any(x is None for x in los) else min(cells, key=lambda k: (cells[k]["ci90_lo_sol"], k))
    mean = cells[mk]["mean_sol"]
    return {"measured": True, "wis_mean_sol": mean, "wis_mean_cell": mk, "wis_ci90_lo_sol": None if lk is None else cells[lk]["ci90_lo_sol"], "wis_ci90_lo_cell": lk,
            "not_proposed_mean_le_0": mean <= 0, "proposal": "not proposed (worst-in-slot mean <= 0)" if mean <= 0 else "worst-in-slot mean > 0 (one of DEC-032's conditions only)",
            "cells": cells, "note": DEC032_NOTE}


# ------------------------------------------------------------------------------------------------------------------------------ render
def _f(z: Any) -> str:
    return "None" if z is None else f"{z:+.6f}"


def _line(k: str, c: Mapping[str, Any]) -> str:
    return (f"    {k:16s} n {c['n']:4d} days {c['days']:2d} pos {c['days_pos']:2d} mean {_f(c['mean_sol'])} total {_f(c['total_sol'])} ci90lo {_f(c['ci90_lo_sol'])} "
            f"ex-top3 {_f(c['ex_top3_sol'])} ex-best-day {_f(c['ex_best_day_sol'])} n_credited {c.get('n_credited', 0)}")


def render(rep: Mapping[str, Any]) -> str:
    L = [f"ARENA H5 REPORT LEGS  bot {rep['bot_id']}  read instant {rep['read_instant_ms']}  trade rows {rep['n_trade_rows']}  refit file {rep['refit_file']}", f"*** {rep['banner']} ***"]
    dk = rep["deciding_stake"]
    for st, b in rep["books"].items():
        L.append(f"\n  stake {st}{' (deciding)' if st == dk else ' (report-only)'}  trades {b['n_trades']}")
        sections = [("AS SCORED, RENT-CHARGED (DECIDES)", b["as_scored_rent_charged"]), (f"RENT-0 leg ({LABEL_RENT0})", b["rent_0"])]
        w = b["worst_in_slot"]
        if isinstance(w, str):
            L.append(f"   WORST-IN-SLOT: {w}")
        else:
            sections += [("WORST-IN-SLOT leg (rent-charged)", w["cells"]), ("WORST-IN-SLOT, NO GUARD RESCUE (report-only companion)", w["no_guard_rescue"]["cells"]),
                         ("WORST-IN-SLOT + RENT-0 (stacked, report-only)", w["rent_0_stacked"])]
        for title, cells in sections:
            L.append(f"   {title}:")
            L += [_line(k, c) for k, c in cells.items() if st != dk or "_1p9_" in k]
        if not isinstance(w, str):
            L.append("   R7 CANARY CONDITION: quote the LOWER of the worst-in-slot and the no-guard-rescue legs (mean and CI90 lower bound). A guard-rescued worst-in-slot leg can beat END, "
                     "so it is not a bound on its own. Lower of the two, 1.9 s cells:")
            for k, g in w["canary_lower"].items():
                if st != dk or "_1p9_" in k:
                    L.append(f"    {k:16s} mean {_f(g['mean_sol'])} ({g['leg_of_mean']})  ci90lo {_f(g['ci90_lo_sol'])}")
            L.append("   END minus worst-in-slot (SOL per trade / SOL total), then the same without the derived_last trips (report-only), then minus the no-guard-rescue companion:")
            for k, g in w["end_minus_worst"].items():
                if st != dk or "_1p9_" in k:
                    g2, g3 = w["end_minus_worst_without_derived_last"].get(k), w["no_guard_rescue"]["end_minus_worst"][k]
                    L.append(f"    {k:16s} {_f(g['mean_sol'])} / {_f(g['total_sol'])}" + ("" if g2 is None else f"    without derived_last ({w['n_derived_last']} trips): {_f(g2['mean_sol'])} / {_f(g2['total_sol'])}")
                             + f"    vs companion: {_f(g3['mean_sol'])} / {_f(g3['total_sol'])}")
            L.append(f"   pressure intercepts, as scored {w['intercepts']['as_scored']}; worst {w['intercepts']['worst']}; companion {w['intercepts']['companion']}")
    g = rep.get("dec032_worst_in_slot")
    if g:
        if g["measured"]:
            L.append(f"\n  DEC-032 WORST-IN-SLOT FIGURE (deciding cell, lower of the two legs, lowest sub-cell): mean {_f(g['wis_mean_sol'])} ({g['wis_mean_cell']})  "
                     f"ci90lo {_f(g['wis_ci90_lo_sol'])} ({g['wis_ci90_lo_cell']})  -> {g['proposal']}")
        else:
            L.append(f"\n  DEC-032 WORST-IN-SLOT FIGURE: not measured ({g['why']}) -> {g['proposal']}")
        L.append(f"  {g['note']}")
    d = rep.get("worst_in_slot_diag")
    if d:
        L.append(f"\n  worst-in-slot diagnostics: trips {d['trips']} legs {d['legs']} entry slot >1 print {d['entry_multi_print']} moved {d['entry_moved']}  exit slot >1 print {d['exit_multi_print']} "
                 f"moved {d['exit_moved']}  invalid picks {d['invalid_pick']}  derived_last {d['derived_last']}  END consistency {'ok' if d.get('parity_ok') else 'MISMATCH'}")
        L.append(f"  END -> worst state transitions by stake|latency|rule: {d.get('transitions') or 'none'}   guard flips (guarded status changed): {d.get('guard_flips') or 'none'}")
        for q in (d["state_mismatch"] + d["pnl_mismatch"])[:8]:
            L.append(f"    END mismatch: {q}")
    return "\n".join(L)


# ------------------------------------------------------------------------------------------------------------------------------ cli
def _add_books_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--books-file", help="the registered books file (v2 schema): the read accepts its ids, Q50 included; without it only the a1 books (unchanged)")
    ap.add_argument("--books-sha256", help=f"the books file's pinned sha256 (default {B.BOOKS_V2_SHA256}, the registered v2)")


BOOKS_AP = argparse.ArgumentParser(add_help=False)
_add_books_args(BOOKS_AP)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bot", required=True)
    ap.add_argument("--ledger-root", required=True)
    ap.add_argument("--read-instant", type=int, help="ms; must be at or after the book's recorded read instant")
    ap.add_argument("--i-am-the-read", action="store_true", help="only the read job sets this: the H5 outputs are sealed until then")
    ap.add_argument("--shadow-dir", action="append", default=[], help="the shadow dir(s) that fed the book; without one worst-in-slot prints 'not measured'")
    ap.add_argument("--json")
    ap.add_argument("--now-ms", type=int)
    pre, _ = BOOKS_AP.parse_known_args(argv)
    if pre.books_file:
        _add_books_args(ap)
    a = ap.parse_args(argv)
    try:
        if a.json:
            check_path(a.json, "json output")
        books = B.load_books(check_path(a.books_file, "books file"), a.books_sha256) if pre.books_file else None
        rep = build_report(a.bot, a.ledger_root, read_instant_ms=a.read_instant, i_am_the_read=a.i_am_the_read, shadow_dirs=a.shadow_dir, now_ms=a.now_ms, books=books)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    diag = rep.get("worst_in_slot_diag")
    print(render(rep))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(rep, fh, indent=1, sort_keys=True, allow_nan=False)
    if diag and not diag.get("parity_ok"):
        print("END CONSISTENCY FAILED: the strips given are not the ones the scorer priced; worst-in-slot is NOT MEASURED (rent-0 leg above is unaffected)", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
