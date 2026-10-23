"""Pinned producer of the paper-twins file for the arena live parity tool (DEC-032 s5 "Twin comparator", QP 10-23 PR #825 twins item). PAPER ONLY: no RPC, no key, no wallet.

    python -m tools.arena_twin_writer --trips TRIPS.jsonl --ledger-dir DIR_OF_THE_BOOKS_LEDGER --bot-id BOT --out TWINS.jsonl [--buy-send N] [--sell-send N] [--join-tol-ms 10000]

For each live trip (trip_id = mint) it finds the book's own paper position in the arena ledger (`<ledger-dir>/trades-<UTC day>.jsonl`, kind "trade", same mint, entry decision
closest to the trip's and within --join-tol-ms) and RE-PRICES it with tools/arena_paper.price_leg from the row's recorded pool states (`calc["1p9"]`):
  * the book's REGISTERED config: the row's own `stack` block (h_in, h_out, k_rounding, strand_rule, e1 floor, feed delay, pressure intercept);
  * the NOFAIL leg (price_leg "pnl", no 15% / pressure blend) at the 1.9 s latency leg;
  * the trip's own stake (the canary stake, not the book's deciding stake): the engine's impact at that stake;
  * the executor's per-send fees: the trip's `buy_fee_lamports` and `sell_fee_lamports` (the landed fees as sent, escalated resends included) when the trips file has them,
    else --buy-send / --sell-send (default 60,000 = 55,000 priority + 5,000 base, tools/h5_executor.py H5_DEFAULT). The engine has one `send`; it is set to the buy fee and the
    sell fee difference is applied to a filled trip's pnl (exact for a filled trip; the strand and guard states use the buy fee only, as the engine does);
  * rent = RENT_DECIDING (0): the executor's sell closes the ATA in the same transaction (DEC-031 Am.2 item 5). Not configurable here;
  * the E1 floor EXCLUDED in `pnl_lamports` (this decides); `pnl_e1_lamports` is the same trip with the registered floor, reported beside.
Strand rule: the first of the book's rules (`total` when "both"), the deciding strand class; `pnl_by_rule` lists every rule.
Units match the live recorder: entry_price = stake / raw tokens bought; exit_price = proceeds / raw tokens sold (lamports per raw token).
Output rows: trip_id, pnl_lamports, pnl_e1_lamports, entry_price, exit_price, state, buy_send_lamports, sell_send_lamports, position_id, bot_id.
A paper position that was excluded, guarded, stranded or invalid gets {"trip_id", "price_failed": true, "reason"} (the parity tool counts no_twin). A trip with NO paper position
in the ledger gets NO row (also no_twin), so an output with zero rows while trips exist stays a fail-closed signal in the parity tool. Bonding positions are price_failed (the arena canary is PumpSwap).
H5 BOOKS (QP 10-23 ruling 4): a ledger row of tools/arena_h5_score (decision_ms an int, meta.stack DEC-031-rev2, legs.flat_1p9) is priced by the H5 adapter (price_h5_trip): legs.flat_1p9.nofail_lamports of the row at the canary stake, rent-charged (pnl_rent0_lamports beside); no entry/exit price (open). Its --config-hash is h5_config_hash(sha256 of tools/arena_h5_cost.py).
CONFIG PIN (QP 10-23 condition 2): --config-hash is the sha256 of the book's REGISTERED stack block (`stack_hash`: the row's `stack` without the per-position sps_entry / sps_exit), pinned
in the DEC copy. A joined ledger row whose stack hashes differently refuses the whole run (exit 2, nothing written): the twin would be priced at a config the book did not register.
RUN RECORD (condition 1): after the twins file, `<out>.run.json` is written atomically: run_ms (the writer's own clock, or --now-ms), covered_trip_ids (EVERY trip in the trips file read),
twin_rows, twins_sha256 (of the file just written), config_hash, code_sha256. The executor's parity state takes the twin stamp from run_ms only (never from "the file parses"), and only while
the record covers every live trip and matches the file. A stalled or failed writer (exit 2 writes nothing) therefore goes stale: the parity tool's 60 s / last-landed-tx rule gives P7.
FEE BASIS (condition 5): a trip's buy fee is the LANDED buy's own fee; failed earlier buy sends are EXCLUDED from the twin. The live side excludes them too (the executor's pnl is the landed sell delta
minus the landed buy's cost; a failed buy's fee is booked as realized loss but is not part of the trip), so d_i compares like with like; their cost shows in the landing-fail excess (P2) and the
daily tie-out. The sell fee is the landed sell plus earlier failed SELL attempts (extra_cost), which the live pnl does include. Rows carry `fee_basis`.
The file is replaced atomically on every run (same inputs, same bytes). Exit 0 written; 2 unusable input (nothing written). Nothing here says a book made money.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_stack as F
from tools.arena_paper import StackConfig, price_leg, rules_of

LAT = 1.9
DEFAULT_SEND = 60_000  # tools/h5_executor.py H5_DEFAULT: priority 55,000 + base 5,000
DAY_MS = 86_400_000
RUN_SCHEMA = "arena_twin_run_v1"
FEE_BASIS = "landed_buy_fee; landed_sell_fee_plus_failed_sells; failed_earlier_buy_sends_excluded"


def stack_hash(stack: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of a ledger row's stack block without the per-position slot rates."""
    core = {k: v for k, v in stack.items() if not str(k).startswith("sps_")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class BadInput(Exception):
    pass


H5_STACK_ID = "DEC-031-rev2"  # tools/arena_h5_cost.STACK_ID; the H5 books' registered paper stack
H5_ADAPTER = "h5_twin_adapter_v1"
H5_COST_PATH = Path(__file__).with_name("arena_h5_cost.py")


def h5_config_hash(cost_sha256: str) -> str:
    """The config pin of an H5 book: STACK_ID plus the sha256 of tools/arena_h5_cost.py at the book's registration sha (QP 10-23 ruling 4)."""
    return hashlib.sha256(json.dumps({"adapter": H5_ADAPTER, "arena_h5_cost_sha256": cost_sha256, "stack_id": H5_STACK_ID}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def is_h5_row(r: Mapping[str, Any]) -> bool:
    return isinstance(r.get("legs"), Mapping) and _isint(r.get("decision_ms")) and (r.get("meta") or {}).get("stack") == H5_STACK_ID


def entry_ms(r: Mapping[str, Any]) -> Any:
    d = r.get("decision_ms")
    return d.get("entry") if isinstance(d, Mapping) else d


def price_h5_trip(trip: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], buy_send: int, sell_send: int, h5: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """H5 adapter: the book's registered cost model (tools/arena_h5_cost, rent-charged 2,039,280 decides, no feed delay), priced at the LIVE trip's own stake.
    The ledger row nearest the live stake (the scorer records only a few stakes) is the check row: the frozen fill model, rebuilt from the scorer's own pool states, must reproduce
    its `nofail_lamports` within 1 lamport (else price_failed), and the SAME code path then prices the trip at the live stake (impact at that size). A row whose `frozen_sha` is not
    the book's pinned sha is price_failed. Entry and exit prices come from tools/arena_h5_twin_prices at the scorer's landing and exit-trigger states (pre-trade reserves incl. V),
    with the scorer and cost module of the registration export. The scorer row is frozen and gains no field. Without `h5` every H5 row is price_failed (fail closed).
    `entry_price` = stake / tokens bought BEFORE the haircut; live pays on the tokens actually received, so g_entry is biased by about +26 bps (fail-safe; declare in the DEC copy).
    COMPARABLE BASIS (QP 10-23 ruling 6 (1)): `pnl_lamports` / `pnl_e1_lamports` stay the registered model's figures (rent charged, 505,000 per send). `pnl_live_basis_lamports` is what
    arena_live_parity compares live_net with: rent 0 (the live sell closes the ATA, so the rent is refunded), E1 EXCLUDED, and the executor's actual per-send fees:
    = P&L(E1 excluded, rent charged, 505,000 per send) + 2,039,280 + `fee_delta_lamports`, with fee_delta_lamports = (505,000 - buy fee) + (505,000 - sell fee), about 690,000 at
    160,000 per send. `pnl_e1_live_basis_lamports` is the same with the E1 floor included. `pnl_rent0_lamports` is kept (pnl_lamports with the rent refunded, E1 and 505,000 sends)."""
    out: dict[str, Any] = {"trip_id": trip["trip_id"], "bot_id": rows[0].get("bot_id"), "position_id": rows[0].get("trade_id"), "buy_send_lamports": buy_send,
                           "sell_send_lamports": sell_send, "fee_basis": "h5_registered_cost_model_505000_per_send", "adapter": H5_ADAPTER, "stack": H5_STACK_ID}
    lam = float(trip["stake_lamports"])
    near = sorted((r for r in rows if isinstance(r.get("stake_sol"), (int, float)) and not isinstance(r.get("stake_sol"), bool) and r["stake_sol"] > 0),
                  key=lambda r: (abs(float(r["stake_sol"]) * 1e9 - lam), float(r["stake_sol"])))
    if not near:
        return {**out, "price_failed": True, "reason": "h5_no_check_row"}
    row = near[0]
    if not h5 or not h5.get("expect_frozen_sha"):
        return {**out, "price_failed": True, "reason": "h5_no_price_inputs"}
    if row.get("frozen_sha") != h5["expect_frozen_sha"]:
        return {**out, "price_failed": True, "reason": "h5_row_frozen_sha_not_the_books"}
    leg = (row["legs"] or {}).get("flat_1p9")
    if not isinstance(leg, Mapping) or not isinstance(leg.get("nofail_lamports"), (int, float)) or isinstance(leg.get("nofail_lamports"), bool) or not np.isfinite(leg["nofail_lamports"]):
        return {**out, "price_failed": True, "reason": f"h5_leg_unpriced:{(leg or {}).get('state') if isinstance(leg, Mapping) else None}"}
    out["state"] = leg.get("state")
    if leg.get("state") != "filled":
        return {**out, "price_failed": True, "reason": f"paper_state:{leg.get('state')}"}
    from tools import arena_h5_twin_prices as P
    try:
        sc, cost = h5["modules"]
        px = P.derive(row, P.find_strip(h5["shadow_dir"], str(row.get("pool"))), sc, cost, lam)
    except P.PriceFailed as exc:
        return {**out, "price_failed": True, "reason": f"h5_price:{exc}"}
    send = int(cost.SEND)
    delta = (send - buy_send) + (send - sell_send)
    rent = float(cost.RENT)
    pnl, pnl0 = float(px["pnl_derived"]), float(px["pnl_noe1_lamports"])
    return {**out, "pnl_lamports": pnl, "pnl_e1_lamports": pnl, "pnl_rent0_lamports": pnl + rent, "entry_price": px["entry_price"], "exit_price": px["exit_price"],
            "fee_delta_lamports": delta, "twin_send_lamports": send, "pnl_live_basis_lamports": pnl0 + rent + delta, "pnl_e1_live_basis_lamports": pnl + rent + delta,
            "twin_stake_lamports": px["twin_stake_lamports"], "check_stake_lamports": px["check_stake_lamports"]}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _isint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except (OSError, ValueError) as exc:
        raise BadInput(f"{path}: unreadable ({exc})") from None
    if not all(isinstance(r, dict) for r in rows):
        raise BadInput(f"{path}: a row is not an object")
    return rows


def ledger_rows(ledger_dir: Path, bot_id: str, decision_ms: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in (decision_ms - DAY_MS, decision_ms, decision_ms + DAY_MS):
        f = ledger_dir / f"trades-{_day(d)}.jsonl"
        if f.exists():
            out += [r for r in load_jsonl(f) if r.get("bot_id") == bot_id and (r.get("kind") in ("trade", "excluded") or is_h5_row(r))]
    return out


def config_of(row: Mapping[str, Any], send: int, e1: bool) -> StackConfig:
    s = row.get("stack")
    if not isinstance(s, Mapping):
        raise BadInput("ledger row without a stack block")
    try:
        return StackConfig(send=int(send), rent=F.RENT_DECIDING, h_in=float(s["h_in"]), h_out=float(s["h_out"]), k_rounding=str(s["k_rounding"]), portal_ppm=int(s.get("portal_ppm") or 0),
                           press_intercept=float(s["press_intercept_provisional"]), strand_rule=str(s["strand_rule"]),
                           e1_floor_per_005=int(s["e1_floor_per_005"]) if e1 else 0, feed_delay_slots=s.get("feed_delay_slots"))
    except (KeyError, TypeError, ValueError) as exc:
        raise BadInput(f"stack block incomplete ({exc!r})") from None


def states_of(row: Mapping[str, Any]) -> dict[str, Any]:
    c = row["calc"]["1p9"]
    return {"ref": c["spot"], "Qe": {LAT: c["Qe"]}, "Be": {LAT: c["Be"]}, "ssb": {LAT: c["ssb"]}, "nearby": {LAT: c["nearby_lamports"]}, "Qx": c["Qx"], "Bx": c["Bx"]}


def fills(stake: float, st: Mapping[str, Any], cfg: StackConfig) -> tuple[float, float]:
    """(entry_price, exit_price) of a filled PumpSwap trip, from round_trip's own formulas (arena_stack.round_trip docstring)."""
    Qe, Be, Qx, Bx = st["Qe"][LAT], st["Be"][LAT], st["Qx"], st["Bx"]
    net = stake * (1 - float(F.fee_ppm(Qe, Be)) / 1e6)
    tok = Be * net / (Qe + net)
    tokh = tok / (1 + cfg.h_in)
    Qa, Ba = Qx + net, Bx - tok
    gross = tokh * Qa / (Ba + tokh)
    proceeds = gross * (1 - float(F.fee_ppm(Qa, Ba)) / 1e6) * (1 - cfg.h_out)
    return stake / tok, proceeds / tokh


def price_trip(trip: Mapping[str, Any], row: Mapping[str, Any], buy_send: int, sell_send: int) -> dict[str, Any]:
    out: dict[str, Any] = {"trip_id": trip["trip_id"], "position_id": row.get("position_id"), "bot_id": row.get("bot_id"), "buy_send_lamports": buy_send, "sell_send_lamports": sell_send, "fee_basis": FEE_BASIS}
    if row.get("kind") != "trade" or not row.get("calc"):
        return {**out, "price_failed": True, "reason": f"paper_{row.get('kind')}:{row.get('exclusion_reason')}"}
    if row.get("prog") != "pumpswap":
        return {**out, "price_failed": True, "reason": f"unsupported_prog:{row.get('prog')}"}
    stake = float(trip["stake_lamports"])
    st = states_of(row)
    cfg0, cfg1 = config_of(row, buy_send, e1=False), config_of(row, buy_send, e1=True)
    by_rule: dict[str, Any] = {}
    for rule in rules_of(cfg0):
        r0, r1 = price_leg("pumpswap", stake, st, LAT, cfg0, rule), price_leg("pumpswap", stake, st, LAT, cfg1, rule)
        adj = float(sell_send - buy_send) if r0["state"] == "filled" else 0.0
        by_rule[rule] = {"state": r0["state"], "pnl": r0["pnl"] - adj, "pnl_e1": r1["pnl"] - adj}
    rule = rules_of(cfg0)[0]
    pick = by_rule[rule]
    out["pnl_by_rule"] = by_rule
    out["state"] = pick["state"]
    if pick["state"] != "filled" or not all(np.isfinite(v) for v in (pick["pnl"], pick["pnl_e1"])):
        return {**out, "price_failed": True, "reason": f"paper_state:{pick['state']}"}
    entry, exit_ = fills(stake, st, cfg0)
    if not (np.isfinite(entry) and np.isfinite(exit_) and entry > 0 and exit_ > 0):
        return {**out, "price_failed": True, "reason": "prices_not_finite"}
    return {**out, "pnl_lamports": pick["pnl"], "pnl_e1_lamports": pick["pnl_e1"], "entry_price": entry, "exit_price": exit_,
            "pnl_live_basis_lamports": pick["pnl"], "pnl_e1_live_basis_lamports": pick["pnl_e1"]}  # the arena twin is already rent 0, E1 excluded, actual fees


def build(trips: Sequence[Mapping[str, Any]], ledger_dir: Path, bot_id: str, buy_send: int, sell_send: int, join_tol_ms: int, config_hash: str, h5: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    rows, notes = [], []
    for t in sorted(trips, key=lambda x: str(x.get("trip_id"))):
        if not isinstance(t.get("trip_id"), str) or not _isint(t.get("decision_ms")) or not isinstance(t.get("stake_lamports"), (int, float)) or t["stake_lamports"] <= 0:
            raise BadInput(f"trip row unusable: {t.get('trip_id')!r}")
        cand = [r for r in ledger_rows(ledger_dir, bot_id, t["decision_ms"]) if r.get("mint") == t["trip_id"]
                and isinstance(entry_ms(r), int) and not isinstance(entry_ms(r), bool) and abs(entry_ms(r) - t["decision_ms"]) <= join_tol_ms]
        if not cand:
            notes.append(f"{t['trip_id']}: no paper position in the ledger (no row written)")
            continue
        cand.sort(key=lambda r: (abs(entry_ms(r) - t["decision_ms"]), r.get("kind", "trade") != "trade", str(r.get("trade_id"))))
        bs = t["buy_fee_lamports"] if _isint(t.get("buy_fee_lamports")) else buy_send
        ss = t["sell_fee_lamports"] if _isint(t.get("sell_fee_lamports")) else sell_send
        if is_h5_row(cand[0]):  # H5 book: the pin is STACK_ID + the sha256 of the cost module this writer sits next to
            want = h5_config_hash(hashlib.sha256(H5_COST_PATH.read_bytes()).hexdigest())
            if want != config_hash:
                raise BadInput(f"H5 config pin {config_hash} is not {want} (DEC-031-rev2 + sha256 of tools/arena_h5_cost.py): refused")
            same = [r for r in cand if is_h5_row(r) and abs(entry_ms(r) - entry_ms(cand[0])) == 0]
            rows.append(price_h5_trip(t, same, bs, ss, h5))
            continue
        got = stack_hash(cand[0].get("stack") if isinstance(cand[0].get("stack"), Mapping) else {})
        if got != config_hash:
            raise BadInput(f"ledger row {cand[0].get('trade_id')!r} of trip {t['trip_id']!r}: stack hash {got} is not the pinned registered config {config_hash}: refused")
        rows.append(price_trip(t, cand[0], bs, ss))
    return rows, notes


def write_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in rows))
    os.replace(tmp, path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    for a in ("trips", "ledger-dir", "bot-id", "out"):
        ap.add_argument(f"--{a}", required=True)
    ap.add_argument("--buy-send", type=int, default=DEFAULT_SEND)
    ap.add_argument("--sell-send", type=int, default=DEFAULT_SEND)
    ap.add_argument("--join-tol-ms", type=int, default=10_000)
    ap.add_argument("--config-hash", required=True)
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--run-record")
    ap.add_argument("--shadow-dir", type=Path, help="H5 books: the book's arena shadow dir (the strip records price the twin)")
    ap.add_argument("--frozen-root", type=Path, help="H5 books: a separate export at the book's registration sha (scorer + arena_h5_cost)")
    ap.add_argument("--expect-frozen-sha", help="H5 books: the book's pinned frozen sha; a ledger row with another frozen_sha is price_failed")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        trips = load_jsonl(Path(a.trips))
        h5 = None
        if a.frozen_root and a.shadow_dir:
            from tools import arena_h5_twin_prices as P
            try:
                h5 = {"shadow_dir": a.shadow_dir, "expect_frozen_sha": a.expect_frozen_sha, "modules": P.load_frozen(a.frozen_root, hashlib.sha256(H5_COST_PATH.read_bytes()).hexdigest())}
            except P.PriceFailed as exc:
                raise BadInput(f"frozen root refused: {exc}") from None
        rows, notes = build(trips, Path(a.ledger_dir), a.bot_id, a.buy_send, a.sell_send, a.join_tol_ms, a.config_hash, h5)
        out = Path(a.out)
        write_atomic(out, rows)
        rec = {"schema": RUN_SCHEMA, "run_ms": int(a.now_ms if a.now_ms is not None else time.time() * 1000), "covered_trip_ids": sorted(t["trip_id"] for t in trips),
               "twin_rows": len(rows), "twins_sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "config_hash": a.config_hash,
               "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        rp = Path(a.run_record) if a.run_record else out.with_name(out.name + ".run.json")
        tmp = rp.with_name(rp.name + ".tmp")
        tmp.write_text(json.dumps(rec, sort_keys=True, allow_nan=False) + "\n")
        os.replace(tmp, rp)
    except (BadInput, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"arena_twin_writer: UNUSABLE INPUT, nothing written: {exc}", file=sys.stderr)
        return 2
    for n in notes:
        print("note:", n)
    print(f"arena_twin_writer: {len(rows)} twin row(s) written; code sha256 {hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
