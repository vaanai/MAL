"""Arena report legs (hunt20): the two REPORT-ONLY legs that DEC-031 Amendment 2 item 7 and quant-proof's 2026-10-19 cohort-2 ruling (R1 (f), R7) require beside
every arena read figure. PAPER ONLY. READ-ONLY on the ledger and the tape. It never changes a scorer figure and writes nothing except an optional --json file.

    python -m tools.arena_report_legs --bot BOT_ID --ledger-root DIR --registry FILE [--tape-dir DIR] [--close-blocked FILE] [--vmap FILE] [--json OUT]
                                      [--now-ms MS] [--settle-hours H] [--tape-pad-hours H]

The legs are not verdicts and change no figure of the scorer. The one deciding change is the close-blocked reclassification of Am.2 item 6, applied only when
--close-blocked supplies a list, and printed as "DECIDING (DEC-031 Am.2 item 6)" with the post-reclassification verdict. The deciding cell is fixed by DEC-031 section 2 (1.9 s at the pinned stake, flat + pressure, strand rules sc + total) and is printed by
tools/arena_score.py. These legs are printed BESIDE it:

LEG 1, RENT-CHARGED (DEC-031 Am.2 item 7; QP R1 (f)). The deciding rent of a covered book is 0 on every trip whose leg state is `filled`. The rent-charged leg
re-prices those trips with rent 2,039,280 lamports charged, as an exact transform of the same rows:
    flat leg      minus 0.85 x 2,039,280 lamports
    pressure leg  minus (1 - p_i) x 2,039,280 lamports, p_i = the cell's OWN refit probability for that trip (the scorer's refit, unchanged: rent moves no input of it)
The selector is the leg STATE, not the `filled` / `strand` flags: only trips with `calc[lat].by_rule[rule].state == "filled"` are charged. Rows in state guarded,
stranded, entry_invalid or exit_invalid are unchanged under both fail models. (Under "sc" an entry_invalid / exit_invalid row has filled True and strand False but its pnl
already carries 2,039,280 of strand rent, so selecting on the flags would charge it twice.) The amount charged per trip is 2,039,280 minus the rent the ledger rows were
priced at (`stack.rent`; 0 for a covered book), so a book that was already priced at 2,039,280 gets delta 0 and is never charged twice.
CLOSE-BLOCKED TRIPS (Am.2 item 6). `--close-blocked FILE` (JSON list of position_ids, the result of the structure-only mint fetch, produced elsewhere) reclassifies the
FILLED trips of those positions BEFORE item 7 is applied, in the base figure and in every leg: priced the way arena_stack.round_trip prices a caller-stranded trip
(pnl_nofail = -stake - send - 2,039,280), state "stranded", `filled` stays True (so p_i and the pressure refit do not change); under "total" the strand flag is set (it
bypasses the fail blend), under "sc" it is blended like entry_invalid / exit_invalid. A close-blocked trip leaves state `filled`, so it is not charged rent a second time.

LEG 2, WORST-IN-SLOT (QP ruling R7, ARTIFACTS/lab/qp-ruling-1019-cohort2.md lines 161-172, the definition is on line 165: "buy at the highest-price state among the landing
slot's states, sell at the lowest of the exit slot's"). The engine fills END-bound: the PRE-trade state of the first print at or after landing slot + 1, the pool after
the whole landing slot. Worst-in-slot instead prices each filled entry and exit at the worst state its slot could have shown, with no order needed:
    states of slot x   the PRE-trade state of every print whose slot is x, plus the END state (the pool after the whole slot, i.e. the engine's own fill state);
                       PumpSwap rows carry absolute PRE-trade reserves, so each state is read off the tape and does not depend on within-slot order.
                       A slot with no print has one state, the END state.
    price of a state   (quote reserve + V) / base reserve, V per print as the engine's own pool history has it
    entry              the state of the highest price among the states of the landing slot (calc[lat].landing_slot)
    exit               the state of the lowest price among the states of the exit landing slot (corrected exit decision slot + exit lag slots)
    ties               the first state in tape order
Everything else is the ledger's: the guard reference (calc.spot), ssb, nearby, stake, stack config, E1 floor, strand rules. The trip is re-priced with
arena_paper.price_leg on the worst states (the own buy is re-injected into the worst exit state), the pressure intercept is refit on THIS cell's filled set (the scorer's
refit), and the same cell statistics are printed. The END-minus-worst gap (R7) is the as-scored cell minus the worst-in-slot cell, in SOL per trade and in total.
Consistency check: for every trip the END state recomputed from the tape and the END-priced pnl must equal the ledger's calc values; a mismatch (a tape that is not the one
the engine priced, a late print, a different V source) is counted and listed, and the CLI exits 4. `state_src == derived_last` trips are exempt from the state check (the
engine derived their END state from the last print it had) and are counted. Selection is by spot price, as the ruling says, not by realised outcome.
Without --tape-dir the leg prints "worst-in-slot not measured" (R7: no canary is proposed from a book until it is measured). Bonding rows are not supported (their fills
are POST-trade; such a book is REPORT_ONLY in the scorer anyway) and are refused.

The trade set is the scorer's own: the clock filter, the read boundary, the DEC-031 exclusions (late_print_gap, missing_v). The as-scored cells recomputed here must equal
the scorer's cells (n, mean, total, CI90, ex-top-3) or the report is refused. A leg is printed with the scorer's status beside it, whatever that status is.
The bot_id and every path (ledger, registry, tape, vmap, close-blocked file, json output) are refused if they contain one of FORBIDDEN_PARTS, the list of
tools/arena_feed_delay.py (sealed, forward, shadow, canary, probe, walk, exp011, exp012, tip-tape, meta-capture, fresh-08, truth-) plus _shakedown. There is no default for
the ledger, registry or tape path. A ledger priced at a rent above 2,039,280 is refused: the tool never credits rent.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import numpy as np

from tools import arena_stack as F
from tools.arena_paper import LAT_TAG, ArenaEngine, Ledger, PoolHist, StackConfig, load_registry, price_leg, utc_day
from tools.arena_score import LATS, RULES, SETTLE_MS, Refused, _entry, _legs, cell_stats, cell_verdict, score_bot

RENT_CHARGED = F.RENT_REPORT  # 2,039,280 lamports per filled trip: the amount Am.2 item 7 charges
STRAND_RENT = F.RENT_STRANDED  # 2,039,280: a strand (and a close-blocked trip) loses stake + the buy send + this
# tools/arena_feed_delay.py FORBIDDEN_PARTS (sealed, forward, shadow, canary, probe, walk, exp011, exp012, tip-tape, meta-capture, fresh-08, truth-) plus _shakedown
FORBIDDEN_PARTS = ("sealed", "forward", "shadow", "canary", "probe", "walk", "exp011", "exp012", "tip-tape", "meta-capture", "fresh-08", "truth-", "_shakedown")
TAG_LAT = {v: k for k, v in LAT_TAG.items()}  # "1p3" -> 1.3
TAPE_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
HOUR_MS = 3_600_000
PARITY_RTOL = 1e-12
PARITY_PNL_ATOL = 1e-3  # lamports
BANNER = ("REPORT-ONLY LEGS beside the deciding figures (DEC-031 Amendment 2 item 7, QP ruling R1 (f) and R7). The legs never decide and change no figure of the scorer. "
          "The one deciding change is the close-blocked reclassification of Am.2 item 6 when a list is supplied: its cells are labelled DECIDING. "
          "A book that passes only at rent 0 is described in those words. Nothing here is a profit claim.")


# ------------------------------------------------------------------------------------------------------------------------------ paths
def _check_text(text: str, what: str) -> None:
    low = text.lower()
    for part in FORBIDDEN_PARTS:
        if part in low:
            raise Refused(f"{what} contains {part!r}: this report never reads sealed, forward, walk, probe, shadow, canary, shakedown or other closed material")


def check_path(path: str | os.PathLike, what: str = "path") -> Path:
    """Refuse a path that contains one of FORBIDDEN_PARTS (probe, shadow, canary, _shakedown, sealed, ...). The string is checked BEFORE any filesystem access, then again after symlinks are resolved."""
    _check_text(os.path.abspath(os.fspath(path)), f"{what} {os.fspath(path)!r}")
    _check_text(os.path.realpath(os.fspath(path)), f"{what} {os.fspath(path)!r} (resolved)")
    return Path(path)


# ------------------------------------------------------------------------------------------------------------------------------ the trade set (the scorer's own)
def book_rent(trades: Sequence[Mapping[str, Any]]) -> int:
    """The rent (lamports per filled trip) the rows were priced at: one value or the report is refused."""
    rents = {int(r["stack"]["rent"]) for r in trades}
    if len(rents) != 1:
        raise Refused(f"the rows carry {len(rents)} different stack rents {sorted(rents)}: refused")
    return rents.pop()


def covered_trades(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], boundary_ms: int) -> dict[float, list[dict[str, Any]]]:
    """The scorer's trade set at the read boundary: clock filter, covered attempts (entry decision before the boundary), then the DEC-031 exclusions of
    arena_score._score_bot (a late print over a gap; a missing V on the entry, the exit or in the span). By stake, in file order (the bootstrap depends on it)."""
    clock = reg[bot_id]["clock_start_ms"]
    covered = [r for r in rows if _entry(r) >= clock and _entry(r) < boundary_ms]
    late_gap = {r["position_id"] for r in covered if r["kind"] == "late_print" and r.get("gap_overlap")}
    out: dict[float, list[dict[str, Any]]] = {}
    for r in covered:
        if r["kind"] != "trade":
            continue
        vs = r.get("v_src") or {}
        if r["position_id"] in late_gap or "none" in (vs.get("entry"), vs.get("exit")) or "none" in (vs.get("span") or {}):
            continue
        out.setdefault(r["stake_sol"], []).append(r)
    return out


# ------------------------------------------------------------------------------------------------------------------------------ close-blocked reclassification
def reclassify_close_blocked(trades: Sequence[Mapping[str, Any]], position_ids: Sequence[str] | set[str]) -> list[dict[str, Any]]:
    """Am.2 item 6: the FILLED trips of the given positions become caller-stranded: pnl_nofail = -stake - send - 2,039,280, state "stranded", `filled` True,
    strand True under "total" (bypasses the fail blend) and False under "sc" (blended like entry_invalid / exit_invalid). Rows that are not in state "filled"
    under a rule (guarded: nothing was bought; stranded, entry_invalid, exit_invalid: already priced as a strand) are unchanged under that rule."""
    ids = set(position_ids)
    out: list[dict[str, Any]] = []
    for r in trades:
        if r["position_id"] not in ids:
            out.append(dict(r))
            continue
        r2 = copy.deepcopy(dict(r))
        stake, send = float(round(r["stake_sol"] * 1e9)), float(r["stack"]["send"])
        changed = False
        for lat in LATS:
            for rule, br in r2["calc"][lat]["by_rule"].items():
                if br["state"] == "filled":
                    br.update(pnl_nofail=-stake - send - STRAND_RENT, filled=True, state="stranded", strand=(rule == "total"), e1_extra=0.0)
                    changed = True
        if changed:  # `reclassified` marks only a trip that actually left state "filled"; a listed position whose trip was guarded or already a strand is not counted
            r2["reclassified"] = "close_blocked"
            out.append(r2)
        else:
            out.append(dict(r))
    return out


def check_close_blocked_ids(ids: Sequence[str], by_stake: Mapping[float, Sequence[Mapping[str, Any]]]) -> None:
    """Refuse position_ids that are not in the book's covered trade set (an unknown id is a list from another book or a typo; it must not be counted silently)."""
    known = {r["position_id"] for ts in by_stake.values() for r in ts}
    unknown = sorted(set(ids) - known)
    if unknown:
        raise Refused(f"{len(unknown)} close-blocked position_id(s) are not in this book's covered trade set (first: {unknown[:3]}): refused")

# ------------------------------------------------------------------------------------------------------------------------------ cells
def leg_values(trades: Sequence[Mapping[str, Any]], rule: str, lat: str, rent_delta: float = 0.0) -> dict[str, Any]:
    """Per-trade legs of one (strand rule, latency) cell, in lamports. The pressure intercept is refit on this cell's filled set exactly as the scorer does.
    With rent_delta > 0 the trips whose leg state is "filled" are charged: flat - 0.85 x delta, pressure - (1 - p_i) x delta. Returns flat, press, p, c and the mask."""
    by = [r["calc"][lat]["by_rule"][rule] for r in trades]
    pnl = np.array([b["pnl_nofail"] for b in by], float)
    fil = np.array([b["filled"] for b in by], bool)
    strand = np.array([b["strand"] for b in by], bool)
    ssb = np.array([r["calc"][lat]["ssb"] for r in trades], float)
    near = np.array([r["calc"][lat]["nearby_lamports"] for r in trades], float)
    flat, press, c, p = _legs(rule, pnl, fil, strand, ssb, near, float(trades[0]["stack"]["send"]))
    sel = np.array([b["state"] == "filled" for b in by], bool)
    if rent_delta:
        flat = flat - (1.0 - F.FAIL_FLAT) * float(rent_delta) * sel
        press = press - (1.0 - p) * float(rent_delta) * sel
    return {"flat": flat, "press": press, "p": p, "c": float(c), "charged": sel, "filled": fil}


def rules_of_trades(trades: Sequence[Mapping[str, Any]]) -> list[str]:
    return [ru for ru in RULES if trades and ru in trades[0]["legs"]]


def cells_of(trades: Sequence[Mapping[str, Any]], rent_delta: float = 0.0) -> dict[str, dict[str, Any]]:
    """The scorer's gate statistics (arena_score.cell_stats) of every model x latency x strand-rule cell of one (bot, stake) book, optionally rent-charged.
    `gate_check` is the scorer's cell_verdict on that leg: information only, never a verdict."""
    if not trades:
        return {}
    days = [utc_day(r["decision_ms"]["entry"]) for r in trades]
    cells: dict[str, dict[str, Any]] = {}
    for rule in rules_of_trades(trades):
        for lat in LATS:
            v = leg_values(trades, rule, lat, rent_delta)
            for model in ("flat", "press"):
                s = cell_stats(v[model], days)
                gc = cell_verdict(s)
                cells[f"{model}_{lat}_{rule}"] = dict(s, intercept=v["c"] if model == "press" else None, n_charged=int(v["charged"].sum()) if rent_delta else 0,
                                                      gate_check={"report_only": True, "criteria_met": gc["pass"], "misses": gc["misses"]})
    return cells


def check_parity_with_scorer(stake: float, mine: Mapping[str, Mapping[str, Any]], scored: Mapping[str, Mapping[str, Any]]) -> None:
    """The as-scored cells recomputed here must equal the scorer's, field for field, or the trade set is not the scorer's and nothing is reported."""
    if set(mine) != set(scored):
        raise Refused(f"stake {stake}: the cells recomputed here {sorted(mine)} are not the scorer's {sorted(scored)}")
    for k, m in mine.items():
        for f in ("n", "days", "days_pos", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol"):
            if m[f] != scored[k][f]:
                raise Refused(f"stake {stake} cell {k}: {f} recomputed {m[f]!r} != the scorer's {scored[k][f]!r}: the trade set is not the scorer's, refused")


def end_minus_worst(end: Mapping[str, Mapping[str, Any]], worst: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """R7: the END-bound cell minus the worst-in-slot cell, SOL per trade (mean) and SOL (total)."""
    out: dict[str, dict[str, Any]] = {}
    for k, e in end.items():
        w = worst[k]
        out[k] = {"mean_sol": None if e["mean_sol"] is None or w["mean_sol"] is None else e["mean_sol"] - w["mean_sol"], "total_sol": e["total_sol"] - w["total_sol"]}
    return out


# ------------------------------------------------------------------------------------------------------------------------------ worst-in-slot
def stack_config_of(row: Mapping[str, Any]) -> StackConfig:
    s = row["stack"]
    return StackConfig(send=int(s["send"]), rent=int(s["rent"]), h_in=float(s["h_in"]), h_out=float(s["h_out"]), k_rounding=str(s["k_rounding"]),
                       portal_ppm=int(s.get("portal_ppm", 0)), press_intercept=float(s["press_intercept_provisional"]), strand_rule=str(s["strand_rule"]),
                       e1_floor_per_005=int(s["e1_floor_per_005"]), feed_delay_slots=s.get("feed_delay_slots"))


def state_arrays(h: PoolHist) -> dict[str, np.ndarray]:
    """sl (slot per print), QX / BX (state before print i for i < n, the state derived after the last print at n; Q includes V) and vok, built exactly as
    arena_stack.mint_states builds them."""
    a = h.arrays()
    sl, ib, so, tk, q, b, v = a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"]
    if len(sl) == 0:
        raise Refused(f"pool {h.pool}: no prints")
    qL = q[-1] + so[-1] * (1 - F.FINAL_FEE) if ib[-1] else q[-1] - so[-1] / (1 - F.FINAL_FEE)
    bL = b[-1] - tk[-1] if ib[-1] else b[-1] + tk[-1]
    return {"sl": sl, "QX": np.append(q, qL) + np.append(v, v[-1]), "BX": np.append(b, bL), "vok": a["vok"]}


def slot_states(sa: Mapping[str, np.ndarray], x: int) -> tuple[np.ndarray, int]:
    """Indices of the states of slot x: the PRE-trade state of every print with slot x, then the END state (index je: the first print with slot >= x + 1, or the
    state derived after the last print). A slot with no print has one state, the END state. Returns (indices, je)."""
    sl = sa["sl"]
    ix, je = int(np.searchsorted(sl, x, "left")), int(np.searchsorted(sl, x + 1, "left"))
    return np.arange(ix, je + 1), je


def worst_index(sa: Mapping[str, np.ndarray], idx: np.ndarray, side: str) -> int:
    """The highest-price state for a buy, the lowest-price state for a sell, price = Q / B (Q includes V). Ties: the first. A state whose price is not finite is
    the worst (the stack prices an invalid state as a strand)."""
    with np.errstate(all="ignore"):
        price = sa["QX"][idx] / sa["BX"][idx]
    bad = ~np.isfinite(price) | (sa["BX"][idx] <= 0)
    if bad.any():
        return int(idx[int(np.argmax(bad))])
    return int(idx[int(np.argmax(price) if side == "buy" else np.argmin(price))])


def _close(a: float, b: float) -> bool:
    return bool(np.isclose(a, b, rtol=PARITY_RTOL, atol=0.0)) or (a == b)


def worst_trip(row: Mapping[str, Any], h: PoolHist) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-price one trade row at the worst state of its entry and exit slots, for every latency and strand rule. Returns (calc, diag): the new `calc` and what moved."""
    if row.get("prog") != "pumpswap":
        raise Refused(f"trade {row.get('trade_id')}: prog {row.get('prog')!r}: worst-in-slot is defined for PumpSwap PRE-trade states only")
    sa = state_arrays(h)
    cfg = stack_config_of(row)
    stake = float(round(row["stake_sol"] * 1e9))
    xl = int(row["corrected_slot"]["exit"]) + F.exit_lag_slots(float(row["stack"]["sps_exit"]))
    idx_x, je_x = slot_states(sa, xl)
    wx = worst_index(sa, idx_x, "sell")
    diag: dict[str, Any] = {"state_mismatch": [], "pnl_mismatch": [], "entry_multi_print": 0, "entry_moved": 0, "exit_multi_print": int(len(idx_x) > 2),
                            "exit_moved": int(wx != je_x), "derived_last": row.get("state_src") == "derived_last"}
    new_calc: dict[str, Any] = {}
    for tag in LATS:
        c = row["calc"][tag]
        x = int(c["landing_slot"])
        idx_e, je_e = slot_states(sa, x)
        we = worst_index(sa, idx_e, "buy")
        for i in list(idx_e) + list(idx_x):
            if i < len(sa["vok"]) and not sa["vok"][i]:
                raise Refused(f"trade {row.get('trade_id')}: a state in the entry or exit slot has no V on the tape: the engine would have excluded it (missing_v)")
        lat = TAG_LAT[tag]
        st_end = {"ref": c["spot"], "Qe": {lat: float(sa["QX"][je_e])}, "Be": {lat: float(sa["BX"][je_e])}, "ssb": {lat: c["ssb"]}, "nearby": {lat: c["nearby_lamports"]},
                  "Qx": float(sa["QX"][je_x]), "Bx": float(sa["BX"][je_x])}
        st_w = {"ref": c["spot"], "Qe": {lat: float(sa["QX"][we])}, "Be": {lat: float(sa["BX"][we])}, "ssb": {lat: c["ssb"]}, "nearby": {lat: c["nearby_lamports"]},
                "Qx": float(sa["QX"][wx]), "Bx": float(sa["BX"][wx])}
        if not diag["derived_last"] and not (_close(st_end["Qe"][lat], c["Qe"]) and _close(st_end["Be"][lat], c["Be"]) and _close(st_end["Qx"], c["Qx"])
                                             and _close(st_end["Bx"], c["Bx"])):
            diag["state_mismatch"].append(f"{row['trade_id']}:{tag}")
        diag["entry_multi_print"] = max(diag["entry_multi_print"], int(len(idx_e) > 2))
        diag["entry_moved"] = max(diag["entry_moved"], int(we != je_e))
        by_rule: dict[str, Any] = {}
        for rule in c["by_rule"]:
            end = price_leg("pumpswap", stake, st_end, lat, cfg, rule)
            if not diag["derived_last"] and abs(end["pnl"] - c["by_rule"][rule]["pnl_nofail"]) > PARITY_PNL_ATOL + 1e-9 * abs(end["pnl"]):
                diag["pnl_mismatch"].append(f"{row['trade_id']}:{tag}:{rule}")
            w = price_leg("pumpswap", stake, st_w, lat, cfg, rule)
            by_rule[rule] = {"pnl_nofail": w["pnl"], "filled": w["filled"], "state": w["state"], "strand": w["strand"], "e1_extra": w["e1_extra"],
                             "p_press_provisional": w["p"]}
        new_calc[tag] = dict(c, Qe=st_w["Qe"][lat], Be=st_w["Be"][lat], Qx=st_w["Qx"], Bx=st_w["Bx"], by_rule=by_rule)
    return new_calc, diag


def worst_in_slot_rows(trades: Sequence[Mapping[str, Any]], pools: Mapping[str, PoolHist]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every trade re-priced at the worst states of its slots. Returns (rows, diag): `rows` are the ledger rows with `calc` replaced (so cells_of, the rent
    transform and the close-blocked reclassification compose on them), diag counts what moved and every END-consistency mismatch."""
    out: list[dict[str, Any]] = []
    diag = {"trips": 0, "derived_last": 0, "entry_multi_print": 0, "entry_moved": 0, "exit_multi_print": 0, "exit_moved": 0, "state_mismatch": [], "pnl_mismatch": []}
    for r in trades:
        h = pools.get(r.get("pool"))
        if h is None:
            raise Refused(f"trade {r['trade_id']}: pool {r.get('pool')} is not on the tape given (--tape-dir does not hold the tape the engine priced)")
        calc, d = worst_trip(r, h)
        out.append(dict(r, calc=calc))
        diag["trips"] += 1
        for k in ("entry_multi_print", "entry_moved", "exit_multi_print", "exit_moved"):
            diag[k] += d[k]
        diag["derived_last"] += int(d["derived_last"])
        diag["state_mismatch"] += d["state_mismatch"]
        diag["pnl_mismatch"] += d["pnl_mismatch"]
    diag["parity_ok"] = not diag["state_mismatch"] and not diag["pnl_mismatch"]
    return out, diag


# ------------------------------------------------------------------------------------------------------------------------------ tape
def tape_files(tape_dir: Path, lo_ms: int | None = None, hi_ms: int | None = None) -> list[Path]:
    by_hour: dict[str, Path] = {}
    for f in sorted(tape_dir.iterdir()):
        m = TAPE_RE.match(f.name)
        if not m:
            continue
        t = int(datetime.strptime(m.group(1), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
        if (lo_ms is not None and t + HOUR_MS <= lo_ms) or (hi_ms is not None and t > hi_ms):
            continue
        if m.group(1) not in by_hour or not f.name.endswith(".zst"):  # a closed hour present both ways: the .jsonl is preferred (arena_zst docstring)
            by_hour[m.group(1)] = f
    return [by_hour[h] for h in sorted(by_hour)]


def _lines(p: Path) -> Iterator[bytes]:
    if p.name.endswith(".zst"):
        from tools.arena_zst import open_zst

        r = open_zst(p)
        buf = b""
        try:
            while True:
                chunk = r.read()
                if not chunk:
                    break
                buf += chunk
                *whole, buf = buf.split(b"\n")
                yield from whole
        finally:
            r.close()
        if buf:
            yield buf
    else:
        with open(p, "rb") as fh:
            yield from fh


def iter_tape_rows(files: Sequence[Path], pools: set[str]) -> Iterator[dict[str, Any]]:
    """Trade rows of the wanted pools, in file order. Other rows are not parsed past the type test."""
    for p in files:
        for line in _lines(p):
            if b'"trade"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type", "trade") == "trade" and (r.get("pool") or r.get("mint")) in pools:
                yield r


def load_pools(rows: Iterator[Mapping[str, Any]] | Sequence[Mapping[str, Any]], vmap: Mapping[str, Any] | None = None) -> dict[str, PoolHist]:
    """Pool histories built by the engine's own on_row (its order key, dedupe and V source order), with its gap detection off. Nothing is written: the engine's
    ledger and registry paths point at an empty temp dir that is removed."""
    tmp = tempfile.mkdtemp(prefix="arena_report_legs_")
    try:
        eng = ArenaEngine(ledger_root=os.path.join(tmp, "ledger"), registry_path=os.path.join(tmp, "bots.json"),
                          cfg=StackConfig(feed_delay_slots=0, gap_slots=None, gap_silence_ms=None), vmap=vmap, code_state_fn=lambda: (None, None))
        for r in rows:
            eng.on_row(r)
        return eng.pools
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------------------------------ one bot
def legs_for_book(trades: Sequence[Mapping[str, Any]], pools: Mapping[str, PoolHist] | None, close_blocked: Sequence[str] | None = None) -> dict[str, Any]:
    """All legs of one (bot, stake) book: the base figure, the rent-charged leg, and (when pools are given) the worst-in-slot leg, the END-minus-worst gap and the
    two stacked. Close-blocked positions are reclassified in every one of them, before the rent transform. close_blocked None = no list supplied (the mint fetch has
    not been applied); [] = a list was supplied and holds no id."""
    cb = list(close_blocked or ())
    ledger_rent = book_rent(trades)
    if ledger_rent > RENT_CHARGED:
        raise Refused(f"the rows were priced at rent {ledger_rent}, above the {RENT_CHARGED} this leg charges: a negative delta would credit rent, and this tool never credits rent")
    delta = float(RENT_CHARGED - ledger_rent)
    out: dict[str, Any] = {"n_trades": len(trades), "ledger_rent": ledger_rent, "rent_delta": delta, "as_scored": cells_of(trades)}
    base = reclassify_close_blocked(trades, cb) if cb else list(trades)
    out["n_close_blocked"] = sum(1 for r in base if r.get("reclassified"))
    out["close_blocked"] = {"supplied": close_blocked is not None, "listed": len(set(cb)), "trips_changed": out["n_close_blocked"]}
    out["base"] = cells_of(base) if out["n_close_blocked"] else out["as_scored"]
    out["rent_charged"] = cells_of(base, delta)
    if pools is None:
        out["worst_in_slot"] = "worst-in-slot not measured (no --tape-dir)"
        return out
    wrows, diag = worst_in_slot_rows(trades, pools)
    wbase = reclassify_close_blocked(wrows, cb) if cb else wrows
    worst = cells_of(wbase)
    out["worst_in_slot"] = {"cells": worst, "diag": diag, "end_minus_worst": end_minus_worst(out["base"], worst),
                            "rent_charged_stacked": cells_of(wbase, delta)}
    return out


def build_report(bot_id: str, ledger_root: str | os.PathLike, registry: str | os.PathLike, *, tape_dir: str | os.PathLike | None = None,
                 close_blocked: Sequence[str] | None = None, vmap: Mapping[str, Any] | None = None, now_ms: int | None = None, settle_ms: int = SETTLE_MS,
                 tape_pad_hours: float = 2.0) -> dict[str, Any]:
    _check_text(bot_id, "bot_id")
    check_path(ledger_root, "ledger root")
    check_path(registry, "registry")
    if tape_dir is not None:
        check_path(tape_dir, "tape dir")
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    reg = load_registry(registry)
    led = Ledger(ledger_root)
    rows, problems = led.scan_bot(bot_id)
    incidents, inc_problems = led.read_incidents(bot_id)
    scored = score_bot(bot_id, reg, rows, now, None, settle_ms, None, incidents=incidents, ledger_problems=problems + inc_problems)
    rep: dict[str, Any] = {"bot_id": bot_id, "report_only": True, "banner": BANNER, "scorer_status": scored["status"], "scorer_reasons": scored["reasons"],
                           "read_boundary_ms": scored.get("read_boundary_ms"), "deciding_stake": scored["deciding_stake"], "config": scored["config"],
                           "rent_charged_lamports": RENT_CHARGED, "close_blocked": {"supplied": close_blocked is not None, "listed": len(set(close_blocked or ()))}, "books": {}}
    if "books" not in scored:
        rep["note"] = "the scorer has not reached the read boundary: no legs"
        return rep
    by_stake = covered_trades(bot_id, reg, rows, scored["read_boundary_ms"])
    check_close_blocked_ids(close_blocked or (), by_stake)
    pools = None
    if tape_dir is not None:
        pids = {r.get("pool") for ts in by_stake.values() for r in ts}
        lo = min((r["decision_ms"]["entry"] for ts in by_stake.values() for r in ts), default=None)
        hi = max((r["decision_ms"]["exit"] or r["decision_ms"]["entry"] for ts in by_stake.values() for r in ts), default=None)
        pad = int(tape_pad_hours * HOUR_MS)
        files = tape_files(Path(tape_dir), None if lo is None else lo - pad, None if hi is None else hi + pad)
        pools = load_pools(iter_tape_rows(files, pids), vmap)
        rep["tape_files"] = [f.name for f in files]
    for st in sorted(by_stake):
        trades = by_stake[st]
        legs = legs_for_book(trades, pools, close_blocked)
        check_parity_with_scorer(st, legs["as_scored"], scored["books"][str(st)]["cells"])
        rep["books"][str(st)] = legs
    dk = str(scored["deciding_stake"])
    dec = rep["books"].get(dk)
    if dec and dec["n_close_blocked"]:  # Am.2 item 6 / QP R1 (e): a close-blocked trip is in the DECIDING cell, so these cells are deciding, not report-only
        for k, c in dec["base"].items():
            if "_1p9_" in k:
                c["gate_check"].update(report_only=False, label="DECIDING (DEC-031 Am.2 item 6)")
        subs = {k: c["gate_check"] for k, c in dec["base"].items() if "_1p9_" in k}
        ok = len(subs) == 4 and all(g["criteria_met"] for g in subs.values())
        before = scored["status"]
        rep["post_reclassification"] = {"label": "DECIDING (DEC-031 Am.2 item 6)", "deciding_stake": dk, "scorer_status_before_reclassification": before,
                                        "subcells": {k: {"criteria_met": g["criteria_met"], "misses": g["misses"]} for k, g in sorted(subs.items())},
                                        "verdict": ("PASS" if ok else "FAIL") if before in ("PASS", "FAIL") else f"{before} (unchanged: not a PASS / FAIL status)"}
    return rep


# ------------------------------------------------------------------------------------------------------------------------------ render
def _f(z: Any) -> str:
    return "None" if z is None else f"{z:+.6f}"


def _cell_line(k: str, c: Mapping[str, Any], tag: str = "") -> str:
    return (f"    {tag}{k:16s} n {c['n']:4d} days {c['days']:2d} pos {c['days_pos']:2d} mean {_f(c['mean_sol'])} total {_f(c['total_sol'])} ci90lo {_f(c['ci90_lo_sol'])} "
            f"ex-top3 {_f(c['ex_top3_sol'])} ex-best-day {_f(c['ex_best_day_sol'])}  [{c['gate_check'].get('label') or 'report-only'}: criteria "
            f"{'met' if c['gate_check']['criteria_met'] else 'missed'}]")


def render(rep: Mapping[str, Any]) -> str:
    L = [f"ARENA REPORT LEGS  bot {rep['bot_id']}  scorer status {rep['scorer_status']}  read boundary {rep.get('read_boundary_ms')}", f"*** {rep['banner']} ***"]
    for r in rep.get("scorer_reasons", []):
        L.append(f"  scorer reason: {r}")
    if rep.get("note"):
        L.append(f"  {rep['note']}")
    cbi = rep["close_blocked"]
    L.append("  close-blocked list: " + ("NOT SUPPLIED (the mint-account fetch of Am.2 item 6 has not been applied; every figure below is as scored)" if not cbi["supplied"]
                                        else f"supplied, {cbi['listed']} position_id(s)"))
    pr = rep.get("post_reclassification")
    if pr:
        L.append(f"  *** the scorer status above ({pr['scorer_status_before_reclassification']}) is BEFORE the close-blocked reclassification. "
                 f"{pr['label']} verdict after it, stake {pr['deciding_stake']}: {pr['verdict']} ***")
        for k, g in pr["subcells"].items():
            L.append(f"      {k}: {'criteria met' if g['criteria_met'] else '; '.join(g['misses'])}")
    dk = str(rep["deciding_stake"])
    for st, b in rep["books"].items():
        L.append(f"\n  stake {st}{' (deciding)' if st == dk else ' (report-only)'}  trades {b['n_trades']}  ledger rent {b['ledger_rent']}  the leg charges {RENT_CHARGED} "
                 f"(delta {b['rent_delta']:.0f})  close-blocked trips {b['n_close_blocked']}")
        w = b["worst_in_slot"]
        for title, cells in (("as scored (the scorer's own cells)", b["as_scored"]), (("DECIDING (DEC-031 Am.2 item 6) after close-blocked reclassification" if st == dk else "base after close-blocked reclassification (not the deciding stake)"),
                              b["base"] if b["n_close_blocked"] else None),
                             ("RENT-CHARGED leg", b["rent_charged"]), ("WORST-IN-SLOT leg", None if isinstance(w, str) else w["cells"]),
                             ("WORST-IN-SLOT + RENT-CHARGED (stacked)", None if isinstance(w, str) else w["rent_charged_stacked"])):
            if cells is None:
                if title.startswith("WORST") and isinstance(w, str):
                    L.append(f"   {title}: {w}")
                continue
            L.append(f"   {title}:")
            for k, c in cells.items():
                if "_1p9_" in k or st != dk:
                    L.append(_cell_line(k, c))
        if not isinstance(w, str):
            d = w["diag"]
            L.append(f"   worst-in-slot diagnostics: trips {d['trips']}  entry slot held >1 print {d['entry_multi_print']}  entry state moved {d['entry_moved']}  exit slot held >1 print "
                     f"{d['exit_multi_print']}  exit state moved {d['exit_moved']}  derived_last {d['derived_last']}  END consistency {'ok' if d['parity_ok'] else 'MISMATCH'}")
            for q in (d["state_mismatch"] + d["pnl_mismatch"])[:8]:
                L.append(f"     END mismatch: {q}")
            L.append("   END minus worst-in-slot (SOL per trade / SOL total):")
            for k, g in w["end_minus_worst"].items():
                if "_1p9_" in k or st != dk:
                    L.append(f"    {k:16s} {_f(g['mean_sol'])} / {_f(g['total_sol'])}")
    return "\n".join(L)


# ------------------------------------------------------------------------------------------------------------------------------ cli
def load_json_ids(path: str) -> list[str]:
    d = json.loads(Path(path).read_text())
    ids = d.get("position_ids") if isinstance(d, dict) else d
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        raise Refused(f"{path}: expected a JSON list of position_id strings (or {{\"position_ids\": [...]}})")
    return ids


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bot", required=True)
    ap.add_argument("--ledger-root", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--tape-dir", help="the arena tape the engine priced from; without it the worst-in-slot leg prints 'not measured'")
    ap.add_argument("--close-blocked", help="JSON list of position_ids made close-blocked by the structure-only mint fetch (DEC-031 Am.2 item 6)")
    ap.add_argument("--vmap", help="JSON {pool: V} if the engine that priced the book used a V map")
    ap.add_argument("--json")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--settle-hours", type=float, default=SETTLE_MS / HOUR_MS)
    ap.add_argument("--tape-pad-hours", type=float, default=2.0, help="tape hours read either side of the first decision and the last exit")
    a = ap.parse_args(argv)
    try:
        for what, p in (("close-blocked file", a.close_blocked), ("vmap file", a.vmap), ("json output", a.json)):
            if p:
                check_path(p, what)
        cb = load_json_ids(a.close_blocked) if a.close_blocked else None  # None: no list supplied; [] (an empty file list): supplied, zero blocked
        vmap = json.loads(Path(a.vmap).read_text()) if a.vmap else None
        rep = build_report(a.bot, a.ledger_root, a.registry, tape_dir=a.tape_dir, close_blocked=cb, vmap=vmap, now_ms=a.now_ms, settle_ms=int(a.settle_hours * HOUR_MS),
                           tape_pad_hours=a.tape_pad_hours)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(render(rep))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(rep, fh, indent=1, sort_keys=True, allow_nan=False)
    bad = [st for st, b in rep["books"].items() if not isinstance(b["worst_in_slot"], str) and not b["worst_in_slot"]["diag"]["parity_ok"]]
    if bad:
        print(f"END CONSISTENCY FAILED for stake(s) {bad}: the tape given is not the tape the engine priced (or a late print); the worst-in-slot gap is not reliable", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
