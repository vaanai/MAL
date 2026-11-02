"""r4bt_read: the READ WRAPPER of the arena book r4-b-T (`r4bt-a1`; DEC-031 Amendment 25 DRAFT item 27, PR #1187). PAPER ONLY. READ-ONLY on the ledger. No key, no network.

It is the ONE way to read `r4bt-a1`. `tools/arena_score.py` must never be run on this book directly (it would score the unfiltered rows at the unfiltered boundary).
It wraps `tools.arena_am6_read.run` (Amendment 6: the hour table, the 10% excluded-hour class, the 5% cap on exclusions, THE exclusions output, the parity guard, then
`arena_score.score_bot` and `render`) and adds, in this order, each a refusal (exit 2, nothing printed that looks like a score) unless said otherwise:

  1. `--i-am-the-read`, and EXP-027's read shown written (`tools/r4bt_gate.exp027_read_written`; Amendment 25 item 4 and condition (ii)). No path under a closed root.
  2. The engine and scorer pins (sha256 of `tools/arena_paper.py` and `tools/arena_score.py`) equal the values pasted from the registration record; the registry line is
     r4bt-a1's (deciding stake 0.25, no models); `--as-of-ms` is not in the future.
  3. The outcome-blind BOUNDARY of `tools/r4bt_attempts.py count` (its JSON, pinned by sha256, status "boundary", chain verified, this arm and clock). The wrapper's own
     boundary (arena_score.blind_read_instant over the ledger's attempts that survive Amendment 6's excluded-hour class) must EQUAL it, and the scored attempt count must
     equal the counter's `attempts_before_boundary`. A difference is a refusal: the two counters cannot disagree silently.
  4. Amendment 2 item 8's CONFIG CHECK, counts only, before any score: every key of `StackConfig.as_dict()` on every engine start row and open row equals the pin
     (rent 0, e1_floor_per_005 384022, strand_rule "both", k_rounding and feed_delay_slots as quant-proof pinned them, Q5). A mismatch makes the read NOT_DECIDABLE, final,
     and nothing is scored or re-priced. `gap_slots` 150 and `gap_silence_ms` 60000 are pinned statically by the pricer (`pinned_config`).
  5. The scorer runs through arena_am6_read (items above). The rent-charged leg of Amendment 2 item 7 is printed beside every deciding figure as an exact transform of the
     SAME rows (flat leg minus 0.85 x 2,039,280 lamports; pressure leg minus (1 - p_i) x 2,039,280, p_i the cell's refit probability; only trips whose
     `calc[lat].by_rule[rule].state` is "filled"). The wrapper re-derives the deciding trades and checks its rent-0 cells equal the scorer's, cell by cell (a drift guard).
     A book that passes only at rent 0 is printed in those words.
  6. The multiplicity block of Amendment 3 item 3 (N and c are the manager's parameters, `--n-books` and `--c`: the plain 1 - 0.95^N, 0.05/c, the bootstrap p of each
     deciding sub-cell as `tools.arena_h5_read.boot_p` computes it, the book p = the max) and the item 23 disclosures. Ex-best-day is arena_score's.

The §1.4 5% cap on the item 7 union of gap sources is the scorer's excluded share: the pricer feeds the declog's data_loss gap rows and the derived copy's gap rows to the
same engine, so both enter as `excluded` rows. A PASS is a SCREEN (DEC-031 section 2): the best of N, in-sample for selection. No book has cleared the promotion gate.

    python -m tools.r4bt_read --i-am-the-read --ledger-root L --registry R --health H.jsonl --health-sha S --restarts F --as-of-ms MS --boundary-file B --boundary-sha256 S \\
        --exp027-record E --exp027-record-sha256 S --arena-paper-sha256 S --arena-score-sha256 S --feed-delay-slots d --k-rounding K --n-books N --c C [--record --reads-dir D] [--json F]

--record writes THE read once (arena_score.record_read; a second one is refused). The confirmation read (DEC-031 section 2 step 4) is NOT built here.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_am6_read as AM6
from tools import arena_score as S
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools.arena_paper import Ledger, load_registry, utc_day

RENT = 2_039_280
RENT_FLAT_SHARE = 0.85
LAT = "1p9"
DISCLOSURES = (
    "walk-2-hour information (DEC-030 section 2 R1): the tip tape is a near byte-copy of walk 2's content; r4-b-T outcomes over Forward walk 2 calendar hours are walk-2-hour OUTCOME information",
    "EXP-027's read ended NOT_DECIDABLE (A3 halts 2026-10-20T07:11:27Z and 2026-10-21T17:55:29Z pins_changed; 2026-10-29T07:11:08Z synthetic); r4-b-T re-runs a rule whose registered read ended ND, chosen with that verdict in view",
    "Option Z and the DRY host dry-long-1101a (P0 counts only); the A3 regime of Amendment 25 item 12 and its record-only events",
    "the item 7 gap counts and the section 1.4 exclusions; the Helius-tail causes (DEC-032 Amendment 8 Erratum 4 item 3)",
    "train/serve skew of the 52 h warm-up; the rule was selected before walk 2 on in-sample exploration evidence (EXP-027 section 10.4)",
    "latency runs from the decision instant (the decision row's ms), stricter than EXP-027's sd(T) + k by the host's decide time",
)


def parse_boundary_line(lines: Sequence[str]) -> int | None:
    for ln in lines:
        if ln.startswith("read_boundary_ms "):
            return int(ln.split()[1])
    return None


def check_config(rows: Sequence[Mapping[str, Any]], pin: Mapping[str, Any]) -> list[str]:
    """Amendment 2 item 8: every key of StackConfig.as_dict() on every start row and open row equals the pin. Counts and key names only."""
    bad: list[str] = []
    for r in rows:
        if r.get("kind") not in ("start", "open"):
            continue
        st = r.get("stack") or {}
        for k, v in pin.items():
            if st.get(k) != v:
                bad.append(k)
    return (sorted(set(bad)) + [f"rows_with_a_mismatch={len(bad)}"]) if bad else []


def deciding_trades(rows: Sequence[Mapping[str, Any]], clock_ms: int, b: int, stake: float) -> list[Mapping[str, Any]]:
    """arena_score._score_bot's partition of the deciding stake's priced trades (late-print gap and missing-V removed), over the rows the scorer saw."""
    covered = [r for r in rows if r.get("kind") != "start" and clock_ms <= r["decision_ms"]["entry"] < b]
    late_gap = {r["position_id"] for r in covered if r["kind"] == "late_print" and r.get("gap_overlap")}
    out = []
    for r in covered:
        if r["kind"] != "trade" or r["stake_sol"] != stake or r["position_id"] in late_gap:
            continue
        vs = r.get("v_src") or {}
        if "none" in (vs.get("entry"), vs.get("exit")) or "none" in (vs.get("span") or {}):
            continue
        out.append(r)
    return out


def _arrays(trades, rule):
    pnl = np.array([r["calc"][LAT]["by_rule"][rule]["pnl_nofail"] for r in trades], float)
    fil = np.array([r["calc"][LAT]["by_rule"][rule]["filled"] for r in trades], bool)
    strand = np.array([r["calc"][LAT]["by_rule"][rule]["strand"] for r in trades], bool)
    ssb = np.array([r["calc"][LAT]["ssb"] for r in trades], float)
    near = np.array([r["calc"][LAT]["nearby_lamports"] for r in trades], float)
    return pnl, fil, strand, ssb, near, float(trades[0]["stack"]["send"])


def deciding_cells(trades: Sequence[Mapping[str, Any]], rules: Sequence[str]) -> dict[str, dict]:
    """The four deciding sub-cells at rent 0 (as the scorer builds them) and at rent charged (Amendment 2 item 7), each with its per-trade SOL vector."""
    days = [utc_day(r["decision_ms"]["entry"]) for r in trades]
    out: dict[str, dict] = {}
    for rule in rules:
        pnl, fil, strand, ssb, near, send = _arrays(trades, rule)
        flat, press, _c, p = S._legs(rule, pnl, fil, strand, ssb, near, send)
        sel = []
        for r in trades:
            stt = r["calc"][LAT]["by_rule"][rule].get("state")
            if stt is None:
                raise G.Refused(f"{r['trade_id']}: calc.by_rule[{rule}] has no leg state: the rent-charged transform cannot be selected (Amendment 2 item 7)")
            sel.append(stt == "filled")
        sel = np.array(sel, bool)
        legs = {"flat": (flat, flat - np.where(sel, RENT_FLAT_SHARE * RENT, 0.0)), "press": (press, press - np.where(sel, (1.0 - np.asarray(p, float)) * RENT, 0.0))}
        for model, (x0, x1) in legs.items():
            st0, st1 = S.cell_stats(x0, days), S.cell_stats(x1, days)
            out[f"{model}_{LAT}_{rule}"] = {"rent0": dict(st0, **S.cell_verdict(st0)), "rent_charged": dict(st1, **S.cell_verdict(st1)), "x0_sol": (x0 / 1e9).tolist()}
    return out


def multiplicity(cells: Mapping[str, Mapping], n_books: int, c: int) -> dict[str, Any]:
    from tools.arena_h5_read import boot_p

    ps = {k: boot_p(v["x0_sol"]) for k, v in cells.items()}
    book_p = max((v["p"] for v in ps.values() if v), default=None)
    return {"N": n_books, "c": c, "plain_1_minus_0.95_pow_N": 1 - 0.95 ** n_books, "alpha_over_c": 0.05 / c, "sub_cell_p": {k: (v["p"] if v else None) for k, v in ps.items()},
            "book_p": book_p, "note": "N and c are the manager's parameters; the Holm rank against other books is report-only on a screen read"}


def read(*, ledger_root: str, registry: str, health_texts: Sequence[str], health_shas: Sequence[str], restarts_text: str, as_of_ms: int, boundary_file: str, boundary_sha256: str,
         exp027_record: str | None, exp027_record_sha256: str | None, pins: Mapping[str, str], feed_delay_slots: int, k_rounding: str, n_books: int, c: int,
         i_am_the_read: bool, record: bool = False, reads_dir: str | None = None, now_ms: int | None = None, repo: str | Path | None = None,
         bot: str = G.BOT_ID, arm: str = G.ARM) -> tuple[list[str], dict | None]:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if not i_am_the_read:
        raise G.Refused("--i-am-the-read is required: this is the book's one read (Amendment 25 item 27)")
    G.exp027_read_written(exp027_record, exp027_record_sha256, now)
    G.check_path(ledger_root, "the ledger root")
    G.check_path(registry, "the registry")
    if not (isinstance(n_books, int) and n_books >= 1 and isinstance(c, int) and c >= 1):
        raise G.Refused("--n-books and --c are the manager's parameters (integers >= 1); the tool counts nothing for them")
    if as_of_ms > now:
        raise G.Refused("--as-of-ms is in the future")
    if set(pins) != set(P.PIN_KEYS):
        raise G.Refused(f"pins must name exactly {P.PIN_KEYS}")
    pin_hashes = G.check_pins(Path(repo) if repo else Path(__file__).resolve().parent.parent, pins)
    reg = load_registry(registry)
    e = reg.get(bot)
    if e is None or e.get("deciding_stake") != P.DECIDING_STAKE or e.get("model_ids") != []:
        raise G.Refused(f"{bot}: not registered with deciding_stake 0.25 and no models")
    clock = int(e["clock_start_ms"])
    cfg_pin = {k: v for k, v in P.pinned_config(feed_delay_slots=feed_delay_slots, k_rounding=k_rounding).as_dict().items()}
    bnd = P.load_boundary(boundary_file, boundary_sha256, clock, arm)
    b_ms = int(bnd["boundary_ms"])
    if as_of_ms < b_ms:
        raise G.Refused("--as-of-ms is before the boundary")
    led = Ledger(ledger_root)
    rows, problems = led.scan_bot(bot)
    out = [f"R4BT READ  bot {bot}  as_of_ms {as_of_ms}  boundary_ms {b_ms}  boundary_file_sha256 {boundary_sha256}  arena_paper {pin_hashes[P.PIN_KEYS[0]]}  arena_score {pin_hashes[P.PIN_KEYS[1]]}",
           "A PASS is a SCREEN (DEC-031 section 2): the best of N books, in-sample for selection. No book has cleared the promotion gate."]
    mism = check_config(rows, cfg_pin)
    if mism:
        out.append(f"NOT_DECIDABLE: stack config differs from the Amendment 2 item 8 pin on {mism} (final for the bot_id; nothing scored, nothing re-priced)")
        res_nd = {"bot_id": bot, "status": "NOT_DECIDABLE", "reasons": ["config_pin_mismatch:" + ",".join(mism)], "read_boundary_ms": b_ms, "boundary_source": "natural", "attempts": None}
        return out, res_nd
    with tempfile.TemporaryDirectory() as td:
        excl = os.path.join(td, "excl.jsonl")
        code, lines, res = AM6.run(ledger_root, registry, bot, list(health_texts), as_of_ms, restarts_text, list(health_shas), excl_out=excl, record=False)
        got = parse_boundary_line(lines)
        if got is None:
            raise G.Refused("the ledger has no outcome-blind boundary though the counter shows one: the two counters disagree")
        if got != b_ms:
            raise G.Refused(f"the ledger's boundary {got} != the attempt counter's {b_ms}: the two counters disagree")
        out += lines
        if res is None:  # an exclusions-tool verdict_cap (NOT_DECIDABLE_*), final; recorded only through arena_am6_read's own path
            if record:
                code, lines2, res2 = AM6.run(ledger_root, registry, bot, list(health_texts), as_of_ms, restarts_text, list(health_shas), excl_out=excl, record=True,
                                             reads_dir=reads_dir)
                out += [ln for ln in lines2 if ln.startswith("recorded:")]
            return out, None
        rb = res["bots"][bot]
        if rb.get("attempts") is not None and rb["attempts"] != int(bnd["attempts_before_boundary"]):
            raise G.Refused(f"scored attempts {rb['attempts']} != the counter's attempts_before_boundary {bnd['attempts_before_boundary']}: the two counters disagree")
        extras: dict[str, Any] = {"disclosures": list(DISCLOSURES)}
        dk = str(e["deciding_stake"])
        if rb["status"] in ("PASS", "FAIL") and rb.get("books", {}).get(dk, {}).get("cells"):
            ex_lines = Path(excl).read_text().splitlines()
            status = {aid: r["status"] for aid, r in AM6._by_id(ex_lines).items()}
            kept = AM6.filter_rows(AM6.asof_rows(rows, clock, as_of_ms), status, clock, as_of_ms)
            trades = deciding_trades(kept, clock, b_ms, float(dk))
            bk = rb["books"][dk]
            cells = deciding_cells(trades, bk["rules"])
            for k, v in cells.items():  # drift guard: the wrapper's rent-0 cells are the scorer's
                sc = bk["cells"][k]
                if sc["n"] != v["rent0"]["n"] or sc["mean_sol"] != v["rent0"]["mean_sol"] or sc["ci90_lo_sol"] != v["rent0"]["ci90_lo_sol"]:
                    raise G.Refused(f"rent-0 cell {k} differs from the scorer's (n/mean/CI): the wrapper's partition drifted from arena_score")
            extras["rent_charged"] = {k: v["rent_charged"] for k, v in cells.items()}
            extras["multiplicity"] = multiplicity(cells, n_books, c)
            out.append("RENT-CHARGED LEG (Amendment 2 item 7), beside each rent-0 deciding figure (1.9 s, 0.25 SOL):")
            for k, v in sorted(cells.items()):
                r0, r1 = v["rent0"], v["rent_charged"]
                out.append(f"  {k}: rent0 n {r0['n']} mean {r0['mean_sol']:+.6f} ci90_lo {r0['ci90_lo_sol']:+.6f} {'PASS' if r0['pass'] else 'miss'} | "
                           f"rent_charged mean {r1['mean_sol']:+.6f} ci90_lo {r1['ci90_lo_sol']:+.6f} {'PASS' if r1['pass'] else 'miss'}")
            if rb["status"] == "PASS" and any(not v["rent_charged"]["pass"] for v in v_all(cells)):
                extras["passes_only_at_rent_0"] = True
                out.append("  PASSES ONLY AT RENT 0: at least one deciding sub-cell fails when rent is charged (Amendment 2 item 7; say it in those words)")
            m = extras["multiplicity"]
            out.append(f"MULTIPLICITY (Amendment 3 item 3): N {m['N']}  1-0.95^N {m['plain_1_minus_0.95_pow_N']:.4f}  c {m['c']}  0.05/c {m['alpha_over_c']:.6f}  book p {m['book_p']}")
        else:
            out.append(f"(no deciding figure to print: status {rb['status']})")
        out.append("DISCLOSURES (Amendment 25 item 23): " + " | ".join(DISCLOSURES))
        rb["r4bt"] = extras
        if record:
            out.append("recorded: " + str(S.record_read(reads_dir or S.READS_DIR, rb)))
    return out, rb


def v_all(cells):
    return list(cells.values())


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="r4-b-T read wrapper (the book's one read).")
    ap.add_argument("--i-am-the-read", action="store_true")
    for a in ("ledger-root", "registry", "restarts", "boundary-file", "boundary-sha256", "arena-paper-sha256", "arena-score-sha256"):
        ap.add_argument("--" + a, required=True)
    ap.add_argument("--health", required=True, action="append")
    ap.add_argument("--health-sha", required=True, action="append")
    ap.add_argument("--as-of-ms", required=True, type=int)
    ap.add_argument("--exp027-record", default=None)
    ap.add_argument("--exp027-record-sha256", default=None)
    ap.add_argument("--feed-delay-slots", type=int, default=None)
    ap.add_argument("--k-rounding", default=None)
    ap.add_argument("--n-books", type=int, default=None)
    ap.add_argument("--c", type=int, default=None)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--reads-dir", default=S.READS_DIR)
    ap.add_argument("--json", default=None)
    return ap


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if None in (a.feed_delay_slots, a.k_rounding, a.n_books, a.c):
            raise G.Refused("--feed-delay-slots, --k-rounding, --n-books and --c are required (no defaults)")
        lines, res = read(ledger_root=a.ledger_root, registry=a.registry, health_texts=[Path(h).read_text() for h in a.health], health_shas=a.health_sha,
                          restarts_text=Path(a.restarts).read_text(), as_of_ms=a.as_of_ms, boundary_file=a.boundary_file, boundary_sha256=a.boundary_sha256,
                          exp027_record=a.exp027_record, exp027_record_sha256=a.exp027_record_sha256,
                          pins={"tools/arena_paper.py": a.arena_paper_sha256, "tools/arena_score.py": a.arena_score_sha256}, feed_delay_slots=a.feed_delay_slots,
                          k_rounding=a.k_rounding, n_books=a.n_books, c=a.c, i_am_the_read=a.i_am_the_read, record=a.record, reads_dir=a.reads_dir, now_ms=now_ms)
    except Exception as e:  # noqa: BLE001 - every failure is a refusal (exit 2), never a traceback
        print(f"r4bt_read: REFUSED: {e}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if a.json and res is not None:
        Path(a.json).write_text(json.dumps(res, indent=1, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
