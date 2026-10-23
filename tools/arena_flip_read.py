"""DEC-031 Amendment 8 item 7: the dust-rule flip read for a covered (160,000-lamport) book. PAPER ONLY. READ-ONLY on the ledger. No tape.

    python -m tools.arena_flip_read --bot BOT_ID --ledger-root DIR --registry FILE [--close-blocked FILE] [--expect-send 160000] [--json OUT] [--now-ms MS]
                                    [--settle-hours H]            (any other argument is ignored, so the argv of tools.arena_report_legs can be passed whole)

Why it exists. Under the strand rule "total" a sell whose proceeds are below ONE SEND is a strand (arena_stack.round_trip, auto_stranded). The line moves with the send:
a sell worth 160,000 to 505,000 lamports is a strand at 505,000 and a tiny fill at 160,000. That flip is optimistic for the covered stack (a strand costs
stake + send + 2,039,280 rent, a tiny fill about -stake - send), 7 to 8 times the 690,000 round-trip saving. Am.8 item 7 therefore requires every read of a covered
book to print (1) the count of flipped legs and their SOL and (2) the gate re-checked with the flipped legs priced as strands, and says a book that passes only with the
flipped legs priced as fills does not pass.

What is a flipped leg here. A (trip, latency) leg under rule "total" whose ledger state is "filled" and whose proceeds `sv` satisfy  row send <= sv < 505,000.
`sv` is recovered from the ledger row itself:  pnl_nofail + e1_extra + stake + 2 x send + rent  (pnl_nofail is after the E1 floor; arena_stack.round_trip:
pnl = sv - stake - 2 x send - rent). Rule "sc" has no dust rule and no flips. Rows already in another state (guarded, stranded, entry_invalid, exit_invalid) never flip.

How the re-check prices a flipped leg (the narrowest reading of Am.8 item 7). It becomes a strand exactly as arena_report_legs.reclassify_close_blocked prices a strand:
pnl_nofail = -stake - send - 2,039,280 with `send` the row's own send (160,000), state "stranded", strand True under "total" (it bypasses the fail blend),
`filled` stays True (so p_i and the pressure refit do not change), e1_extra 0. A second, harsher variant prices the strand at the 505,000 send the Am.8 item 7 figures
(2,384,280 to 2,729,280 per flip) are quoted against; it is printed beside the first and also decides nothing by itself. Close-blocked trips (Am.2 item 6), when a list is
given, are reclassified first, as in arena_report_legs; a trip already stranded by that cannot flip.

The verdict line is the scorer's own cell_verdict over the deciding stake's four 1.9 s sub-cells (flat and pressure x strand rules total and sc), as arena_report_legs
prints for the close-blocked case. It is not a promotion decision: the 90% CI, the top-3 and day criteria are cell_verdict's, and quant-proof reads the result.
A book that is not covered (rows at 505,000) has no flips by construction and says so. `--expect-send` refuses rows at any other send, as tools.arena_stack_v2 does.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from typing import Any, Mapping, Sequence

from tools import arena_report_legs as RL
from tools.arena_paper import Ledger, load_registry
from tools.arena_score import LATS, SETTLE_MS, Refused, score_bot

DUST_V1 = 505_000  # the pinned stack's send: below it a "total" sell is a strand at the old line
STRAND_RENT = RL.STRAND_RENT
HOUR_MS = RL.HOUR_MS
BANNER = ("REPORT-ONLY (DEC-031 Amendment 8 item 7). The flip read changes no scorer figure and is no promotion decision. Nothing here is a profit claim.")


def proceeds_of(row: Mapping[str, Any], br: Mapping[str, Any]) -> float:
    """sv of a "filled" leg, from the row: pnl_nofail + e1_extra + stake + 2 send + rent (arena_stack.round_trip)."""
    stack = row["stack"]
    return float(br["pnl_nofail"]) + float(br.get("e1_extra") or 0.0) + float(round(row["stake_sol"] * 1e9)) + 2.0 * float(stack["send"]) + float(stack["rent"])


def reclassify_dust_flips(trades: Sequence[Mapping[str, Any]], threshold: int = DUST_V1, strand_send: float | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rows with every flipped leg (rule "total", state "filled", send <= sv < threshold) re-priced as a strand, and the list of flips. `strand_send` None = the row's own send."""
    out: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    for r in trades:
        send = float(r["stack"]["send"])
        if send >= threshold:
            out.append(dict(r))
            continue
        r2 = None
        for lat in LATS:
            br = r["calc"][lat]["by_rule"].get("total")
            if not br or br["state"] != "filled":
                continue
            sv = proceeds_of(r, br)
            if not (sv < threshold):
                continue
            r2 = r2 or copy.deepcopy(dict(r))
            stake = float(round(r["stake_sol"] * 1e9))
            s = send if strand_send is None else float(strand_send)
            new = -stake - s - STRAND_RENT
            flips.append({"position_id": r["position_id"], "stake_sol": r["stake_sol"], "lat": lat, "proceeds_lamports": sv, "pnl_before": float(br["pnl_nofail"]),
                          "pnl_after": new, "move_lamports": new - float(br["pnl_nofail"])})
            r2["calc"][lat]["by_rule"]["total"].update(pnl_nofail=new, filled=True, state="stranded", strand=True, e1_extra=0.0)
        if r2 is not None:
            r2["reclassified"] = (r2.get("reclassified") or "") + "dust_flip"
            out.append(r2)
        else:
            out.append(dict(r))
    return out, flips


def flip_summary(flips: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts and SOL by latency (the raw no-fail pnl move of the flipped legs; the effect on each cell's total is in `cell_effect`)."""
    by_lat: dict[str, dict[str, float]] = {}
    for f in flips:
        d = by_lat.setdefault(f["lat"], {"n": 0, "move_sol": 0.0, "proceeds_min": f["proceeds_lamports"], "proceeds_max": f["proceeds_lamports"]})
        d["n"] += 1
        d["move_sol"] += f["move_lamports"] / 1e9
        d["proceeds_min"] = min(d["proceeds_min"], f["proceeds_lamports"])
        d["proceeds_max"] = max(d["proceeds_max"], f["proceeds_lamports"])
    return {"n": len(flips), "n_positions": len({f["position_id"] for f in flips}), "move_sol": sum(f["move_lamports"] for f in flips) / 1e9, "by_lat": by_lat}


def _verdict(cells: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    subs = {k: c["gate_check"] for k, c in cells.items() if "_1p9_" in k}
    ok = len(subs) == 4 and all(g["criteria_met"] for g in subs.values())
    return {"pass": ok, "subcells": {k: {"criteria_met": g["criteria_met"], "misses": g["misses"]} for k, g in sorted(subs.items())}}


def flip_book(trades: Sequence[Mapping[str, Any]], close_blocked: Sequence[str] | None = None) -> dict[str, Any]:
    """The flip read of one (bot, stake) book."""
    cb = list(close_blocked or ())
    base = RL.reclassify_close_blocked(trades, cb) if cb else list(trades)
    sends = sorted({int(r["stack"]["send"]) for r in trades})
    asf = RL.cells_of(base)
    own_rows, flips = reclassify_dust_flips(base)
    hard_rows, flips505 = reclassify_dust_flips(base, strand_send=DUST_V1)
    as_strand, as_strand505 = RL.cells_of(own_rows), RL.cells_of(hard_rows)

    def effect(after: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
        return {k: after[k]["total_sol"] - asf[k]["total_sol"] for k in asf}

    return {"n_trades": len(trades), "sends": sends, "covered": sends == [160_000], "flips": flip_summary(flips), "flip_rows": flips,
            "as_fills": asf, "as_strands": as_strand, "as_strands_at_505k_send": as_strand505,
            "cell_effect": effect(as_strand), "cell_effect_at_505k_send": effect(as_strand505),
            "verdict_as_fills": _verdict(asf), "verdict_as_strands": _verdict(as_strand), "verdict_as_strands_at_505k_send": _verdict(as_strand505),
            "flips_at_505k_send": flip_summary(flips505)["n"]}


def build_flip_report(bot_id: str, ledger_root: str, registry: str, *, close_blocked: Sequence[str] | None = None, now_ms: int | None = None, settle_ms: int = SETTLE_MS,
                      expect_send: int | None = None) -> dict[str, Any]:
    import time
    RL._check_text(bot_id, "bot_id")
    RL.check_path(ledger_root, "ledger root")
    RL.check_path(registry, "registry")
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    reg = load_registry(registry)
    led = Ledger(ledger_root)
    rows, problems = led.scan_bot(bot_id)
    incidents, inc_problems = led.read_incidents(bot_id)
    scored = score_bot(bot_id, reg, rows, now, None, settle_ms, None, incidents=incidents, ledger_problems=problems + inc_problems)
    rep: dict[str, Any] = {"bot_id": bot_id, "report_only": True, "banner": BANNER, "scorer_status": scored["status"], "scorer_reasons": scored["reasons"],
                           "read_boundary_ms": scored.get("read_boundary_ms"), "deciding_stake": scored["deciding_stake"],
                           "close_blocked": {"supplied": close_blocked is not None, "listed": len(set(close_blocked or ()))}, "books": {}}
    if "books" not in scored:
        rep["note"] = "the scorer has not reached the read boundary: no flip read"
        return rep
    by_stake = RL.covered_trades(bot_id, reg, rows, scored["read_boundary_ms"])
    RL.check_close_blocked_ids(close_blocked or (), by_stake)
    if expect_send is not None:
        seen = {int(r["stack"]["send"]) for ts in by_stake.values() for r in ts}
        if seen - {int(expect_send)}:
            raise Refused(f"the rows of {bot_id} carry send {sorted(seen)}, the book pins {expect_send}")
    for st in sorted(by_stake):
        rep["books"][str(st)] = flip_book(by_stake[st], close_blocked)
    dk = str(scored["deciding_stake"])
    dec = rep["books"].get(dk)
    if dec:
        before = scored["status"]
        gated = before in ("PASS", "FAIL")
        ok = dec["verdict_as_fills"]["pass"] and dec["verdict_as_strands"]["pass"]
        rep["gate_with_flips_as_strands"] = {
            "deciding_stake": dk, "scorer_status": before, "flips_deciding_1p9": dec["flips"]["by_lat"].get("1p9", {}).get("n", 0),
            "passes_as_fills": dec["verdict_as_fills"]["pass"], "passes_as_strands": dec["verdict_as_strands"]["pass"],
            "passes_as_strands_at_505k_send": dec["verdict_as_strands_at_505k_send"]["pass"],
            "verdict": ("PASS" if ok and before == "PASS" else "FAIL") if gated else f"{before} (unchanged: not a PASS / FAIL status)",
            "note": "Am.8 item 7: a book that passes only with the flipped legs priced as fills does not pass."}
    return rep


def _f(z: Any) -> str:
    return "None" if z is None else f"{z:+.6f}"


def render(rep: Mapping[str, Any]) -> str:
    L = [f"ARENA FLIP READ (DEC-031 Am.8 item 7)  bot {rep['bot_id']}  scorer status {rep['scorer_status']}  read boundary {rep.get('read_boundary_ms')}", f"*** {rep['banner']} ***"]
    if rep.get("note"):
        L.append(f"  {rep['note']}")
    cb = rep["close_blocked"]
    L.append("  close-blocked list: " + ("NOT SUPPLIED (every figure is as scored)" if not cb["supplied"] else f"supplied, {cb['listed']} position_id(s), applied first"))
    dk = str(rep["deciding_stake"])
    for st, b in rep["books"].items():
        L.append(f"\n  stake {st}{' (deciding)' if st == dk else ' (report-only)'}  trades {b['n_trades']}  ledger send(s) {b['sends']}  "
                 + ("covered stack" if b["covered"] else "NOT a 160,000 book: flips are counted only where the row's send is below 505,000"))
        fl = b["flips"]
        L.append(f"   FLIPPED LEGS (rule total, sell proceeds {'' if not b['sends'] else b['sends'][0]}..{DUST_V1 - 1} lamports): {fl['n']} legs in {fl['n_positions']} trips; "
                 f"no-fail pnl move if priced as strands {fl['move_sol']:+.6f} SOL")
        for lat, d in sorted(fl["by_lat"].items()):
            L.append(f"     latency {lat}: {int(d['n'])} legs, move {d['move_sol']:+.6f} SOL, proceeds {d['proceeds_min']:.0f}..{d['proceeds_max']:.0f} lamports")
        if not fl["by_lat"]:
            L.append("     none")
        for title, cells in (("flips as fills (as the 160,000 ledger prices them)", b["as_fills"]), ("flips PRICED AS STRANDS (strand at the row's send)", b["as_strands"]),
                             ("flips priced as strands at the 505,000 send (harsher variant)", b["as_strands_at_505k_send"])):
            L.append(f"   {title}:")
            for k, c in cells.items():
                if "_1p9_" in k or st != dk:
                    L.append(RL._cell_line(k, c))
        L.append("   total SOL effect of pricing the flips as strands (cell total, flat/pressure blends included), 1.9 s cells:")
        for k, v in b["cell_effect"].items():
            if "_1p9_" in k:
                L.append(f"    {k:16s} {_f(v)}   (at the 505,000 send: {_f(b['cell_effect_at_505k_send'][k])})")
    g = rep.get("gate_with_flips_as_strands")
    if g:
        L.append(f"\n  *** GATE WITH FLIPPED LEGS PRICED AS STRANDS, stake {g['deciding_stake']} (1.9 s sub-cells, flat + pressure x total + sc): {g['verdict']} ***")
        L.append(f"      scorer status {g['scorer_status']}; flipped 1.9 s legs {g['flips_deciding_1p9']}; sub-cells pass as fills: {g['passes_as_fills']}; as strands: "
                 f"{g['passes_as_strands']}; as strands at the 505,000 send: {g['passes_as_strands_at_505k_send']}")
        L.append(f"      {g['note']}")
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bot", required=True)
    ap.add_argument("--ledger-root", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--close-blocked")
    ap.add_argument("--expect-send", type=int)
    ap.add_argument("--json")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--settle-hours", type=float, default=SETTLE_MS / HOUR_MS)
    a, _rest = ap.parse_known_args(argv)
    try:
        for what, p in (("close-blocked file", a.close_blocked), ("json output", a.json)):
            if p:
                RL.check_path(p, what)
        cb = RL.load_json_ids(a.close_blocked) if a.close_blocked else None
        rep = build_flip_report(a.bot, a.ledger_root, a.registry, close_blocked=cb, now_ms=a.now_ms, settle_ms=int(a.settle_hours * HOUR_MS), expect_send=a.expect_send)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(render(rep))
    if a.json:
        slim = {k: v for k, v in rep.items()}
        with open(a.json, "w") as fh:
            json.dump(slim, fh, indent=1, sort_keys=True, allow_nan=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
