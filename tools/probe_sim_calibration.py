#!/usr/bin/env python3
"""Probe-vs-simulator calibration. ARITHMETIC ON n LIVE TRADES, NOT EVIDENCE.

For every LIVE probe round trip, re-simulate the same trade on the tip tape with the paper / probe machinery
(V-priced PumpSwap buy, the executor's `exit_check` tp50/sl30/30-minute rule with our own buy in the book, live
fee structure) and compare with what the live fills realized.

SEAL RULE (EXP-012 forward read, DEC-016): this tool simulates ONLY mints that appear in the live fills file.
Those outcomes are already known through live fills. It never computes an outcome for any other mint on tape
from 2026-10-02 onwards (the forward-read window, read once ~10-16). Enforcement:
  * the fills file is the ONLY source of mints; there is no mint, pool or tape-wide scan argument;
  * the tape reader drops every row whose mint is not in that set at read time (`read_tape_rows`), and
    simulate_trade() raises if handed a mint that is not in the fills set;
  * only the tape hours spanning the live trades are opened;
  * the paper runner's positions.jsonl, runner-status*, pnl and decisions/intents files are never opened.

Tape row assumption (UNVERIFIED): `quote_reserve` / `base_reserve` / `virtual_quote_reserve` on a row are the pool
state after that trade; the state "before slot S" is the last row with slot < S.

    python -m tools.probe_sim_calibration --fills FILLS.jsonl --tape-dir DIR --out-dir OUT
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools import latency_curve as lc
from tools import paper_curve_math as pcm
from tools import pumpswap_virtual_adapter as psva
from tools.paper_price_path import TapePrint, print_from_trade_row
from tools import probe_executor as pe
from tools import pumpswap_tx as tx

# Live builds as (start_ms, sha) boundaries, ascending; a trade belongs to the last boundary at or before its buy ts.
# Override on the CLI with repeated --build START_MS:SHA (the override replaces this list).
BUILDS: tuple[tuple[int, str], ...] = (
    (0, "8a6849b"),               # before 2026-10-05T17:53:03Z
    (1791222783000, "a25eb17"),   # from 2026-10-05T17:53:03Z
    (1791232766000, "7004b16"),   # from 2026-10-05T20:39:26Z
    (1791241796000, "faa3192"),   # from 2026-10-05T23:09:56Z: mark from our own buy tx (#331) + log-only drift (#332)
    # (<start_ms>, "<next build sha>"),  # add the next build's boundary here
)
SIZES_SOL = (0.05, 0.1, 0.25, 0.5)
PRIORITY_ALT = 150_000
EXIT_MARGIN_MS = 5 * 60_000
LAMPORTS = 1_000_000_000


def build_of(ts_ms: int, builds: Iterable[tuple[int, str]] | None = None) -> str:
    label = "unknown"  # before the first boundary
    for start, sha in sorted(builds if builds is not None else BUILDS):
        if ts_ms >= start:
            label = sha
    return label


def parse_builds(specs: list[str]) -> tuple[tuple[int, str], ...]:
    out = []
    for sp in specs:
        start, _, sha = sp.partition(":")
        if not sha or not start.isdigit():
            raise ValueError(f"--build wants START_MS:SHA, got {sp!r}")
        out.append((int(start), sha))
    return tuple(sorted(out))


# --- fills ----------------------------------------------------------------------------------------


def load_fills(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("schema") == pe.SCHEMA_FILL and r.get("mode") == "live" and r.get("kind") in ("buy", "sell"):
                rows.append(r)
    return rows


def pair_trades(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Landed buys paired with the next landed sell of the same mint. `sell` is None when never sold."""
    rows = sorted(fills, key=lambda r: r.get("ts_ms", 0))
    open_buy: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r.get("landed"):
            continue
        m = r["mint"]
        if r["kind"] == "buy" and (r.get("tokens_received") or 0) > 0:
            if m in open_buy:
                out.append({"buy": open_buy[m], "sell": None})
            open_buy[m] = r
        elif r["kind"] == "sell" and m in open_buy:
            out.append({"buy": open_buy.pop(m), "sell": r})
    out.extend({"buy": b, "sell": None} for b in open_buy.values())
    out.sort(key=lambda t: t["buy"]["ts_ms"])
    return out


# --- tape -----------------------------------------------------------------------------------------


def _hour_name(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H")


def hours_needed(trades: list[dict[str, Any]]) -> list[str]:
    hrs: set[str] = set()
    for t in trades:
        lo = t["buy"]["ts_ms"] - 3_600_000  # entry state may sit in the previous hour for a quiet pool
        hi = max((t["sell"] or {}).get("ts_ms", 0), t["buy"]["ts_ms"] + pe.EXIT_RULE.max_hold_ms) + EXIT_MARGIN_MS
        h = lo // 3_600_000 * 3_600_000
        while h <= hi:
            hrs.add(_hour_name(h))
            h += 3_600_000
    return sorted(hrs)


def read_tape_rows(tape_dir: Path, hours: Iterable[str], mints: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Stream `trades-<hour>.jsonl` (or .jsonl.zst via zstdcat) and keep ONLY PumpSwap rows of `mints`."""
    keep: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in hours:
        plain, zst = tape_dir / f"trades-{h}.jsonl", tape_dir / f"trades-{h}.jsonl.zst"
        proc = None
        if plain.exists():
            fh: Any = plain.open(encoding="utf-8")
        elif zst.exists():
            proc = subprocess.Popen(["zstdcat", str(zst)], stdout=subprocess.PIPE, text=True)
            fh = proc.stdout
        else:
            continue
        try:
            for line in fh:
                if '"pumpswap"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("venue") != "pumpswap" or r.get("mint") not in mints:  # seal: foreign mints dropped here
                    continue
                if not all(isinstance(r.get(k), int) for k in ("slot", "quote_reserve", "base_reserve")):
                    continue
                pr = print_of(r)  # the exploration simulator's own print (V-priced), built from the full row
                keep[r["mint"]].append({"_print": pr, **{k: r.get(k) for k in (
                    "slot", "tx_index", "event_index", "t_recv_ms", "block_time", "quote_reserve", "base_reserve",
                    "virtual_quote_reserve", "pool", "side")}})
        finally:
            fh.close()
            if proc:
                proc.wait()
    for rows in keep.values():
        rows.sort(key=lambda r: (r["slot"], r.get("tx_index") or 0, r.get("event_index") or 0))
    return keep


def print_of(r: dict[str, Any]) -> TapePrint | None:
    """The exploration simulator's print for a tape row, V-priced exactly as the V-priced curve runs it
    (`print_from_trade_row` wrapped by pumpswap_virtual_adapter.make_wrapper; V = the row's virtual_quote_reserve).
    None when the simulator would not price the row, or V is unknown (never mix a V-less price in)."""
    if "_print" in r:
        return r["_print"]
    v = r.get("virtual_quote_reserve")
    pool = r.get("pool")
    if not isinstance(v, int) or v <= 0 or not isinstance(pool, str):
        return None
    try:
        res = psva.make_wrapper(print_from_trade_row, {pool: v})(r)
    except Exception:
        return None
    return res[1] if res else None


def cap_fields(rows: list[dict[str, Any]], landed_slot: int, spend: int) -> dict[str, Any]:
    """Would the exploration simulator have refused this live entry on its slippage cap?
    `p_mig_first_print`: price of the first priced PumpSwap print of the migration slot (latency_curve `_fills_for`:
    first fillable print with slot == mig_slot; the migration slot is the mint's first PumpSwap print slot in the
    tape we read, as `mint.mig_slot` in latency_curve, so a pool whose first print precedes the tape window is not
    reproduced). `drift_land`: price of the last print before landed_slot over it, minus 1. `sim_cap_miss`: the real
    `latency_curve._try_buy` refuses with the ref and fills without it (so other refusal reasons do not count)."""
    out: dict[str, Any] = {"p_mig_first_print": None, "drift_land": None, "sim_cap_miss": None}
    prints = [pr for pr in (print_of(r) for r in rows) if pr is not None]
    prints.sort(key=lambda pr: (pr.t_recv_ms, pr.slot, pr.tx_index, pr.event_index))
    if not prints:
        return out
    mig_slot = min(pr.slot for pr in prints)
    ref = next((pr.price_sol for pr in prints if pr.slot == mig_slot and pr.price_sol > 0), None)
    state = None
    for pr in prints:
        if pr.slot < landed_slot:
            state = pr
    if ref is None:
        return out
    out["p_mig_first_print"] = ref
    if state is None:
        return out
    out["drift_land"] = state.price_sol / ref - 1.0
    refused = lc._try_buy(state, spend, pcm.PORTAL_FEE_PPM, ref) is None
    out["sim_cap_miss"] = bool(refused and lc._try_buy(state, spend, pcm.PORTAL_FEE_PPM, None) is not None)
    return out


def row_ms(r: dict[str, Any]) -> int | None:
    if isinstance(r.get("t_recv_ms"), int):
        return r["t_recv_ms"]
    if isinstance(r.get("block_time"), int):
        return r["block_time"] * 1000
    return None


def snap_of(r: dict[str, Any]) -> pe.Snapshot:
    v = r.get("virtual_quote_reserve")
    return pe.Snapshot(ps=None, slot=r["slot"], quote_vault=r["quote_reserve"], base_reserve=r["base_reserve"],  # type: ignore[arg-type]
                       v=v if isinstance(v, int) else None)


def last_before(rows: list[dict[str, Any]], slot: int) -> dict[str, Any] | None:
    best = None
    for r in rows:
        if r["slot"] < slot:
            best = r
        else:
            break
    return best


# --- simulation -----------------------------------------------------------------------------------


def sim_buy(row: dict[str, Any], spend: int) -> dict[str, Any] | None:
    snap = snap_of(row)
    if snap.quote_priced is None or snap.base_reserve <= 0:
        return None
    q = pe.entry_quote(snap, spend)
    return q if q["tokens"] > 0 else None


BOOKS = ("executor", "correct")  # "executor" = legacy double-count (pre-#324 executor); key kept so old JSON stays readable; executor-identical (adds our buy again) vs raw tape book (our buy already in the tape)
SCHEMA_VERSION = 3  # 3: live_* = mark from the post-buy state (the buy-tx method); live_legacy_* = send-state mark; live_snapshot_* = #330 first-snapshot mark. 2 (#330) meant live_* = first snapshot.
POSITIONS = ("sim", "live", "live_legacy", "live_snapshot")
VARIANTS = tuple(f"{p}_{b}" for p in POSITIONS for b in BOOKS)


def eval_book(pos: dict[str, Any], row: dict[str, Any], book: str) -> dict[str, Any]:
    """ret / sell proceeds at one tape row. `executor`: pe.exit_check, which re-adds our buy to the book
    (reserves_with_our_buy, same_venue=True): a double count when the tape row already holds our real buy.
    `correct`: raw tape state, our position applied only as the sell (tokens out)."""
    snap = snap_of(row)
    if book == "executor":
        r = pe.exit_check(pos, snap, pos["t_entry_ms"])  # now == entry: tp/sl only, the time stop is handled here
        return {"ret": r["ret"], "out": r["quote_out"], "reason": r["reason"] if r["reason"] in ("tp", "sl") else None}
    q = snap.quote_priced
    if not q or snap.base_reserve <= 0:
        return {"ret": None, "out": None, "reason": None}
    ret = pcm.spot_sol_per_ui(q, snap.base_reserve) / pos["mark"] - 1.0
    out = tx.cp_sell_out(pos["tokens"], q, snap.base_reserve, pe.fee_ppm_for(q, snap.base_reserve))
    reason = "tp" if ret >= pe.EXIT_RULE.tp else ("sl" if ret <= -pe.EXIT_RULE.sl else None)
    return {"ret": ret, "out": out, "reason": reason}


def walk_exit(rows: list[dict[str, Any]], entry_slot: int, entry_row: dict[str, Any], pos: dict[str, Any],
              delay_slots: int | None, book: str, deadline_covered: bool = True) -> dict[str, Any]:
    """First exit after `entry_slot`. tp/sl on the row that crosses; time stop once a row is past the deadline, priced
    at the LAST ROW BEFORE the deadline (quiet pools included, when the tape covers the deadline). The delayed sell is
    priced at the last row with slot < trigger + delay, never at our own sell's row."""
    deadline = pos["t_entry_ms"] + pe.EXIT_RULE.max_hold_ms
    last = entry_row
    trig: dict[str, Any] | None = None
    for r in rows:
        if r["slot"] <= entry_slot:
            continue
        now = row_ms(r)
        if now is None:
            continue
        if now > deadline:
            trig = {"reason": "time_stop", "row": last, "ms": deadline}
            break
        ev = eval_book(pos, r, book)
        if ev["reason"] and ev["out"] is not None:
            trig = {"reason": ev["reason"], "row": r, "ms": now}
            break
        last = r
    if trig is None:
        if not deadline_covered:
            return {"reason": None}
        trig = {"reason": "time_stop", "row": last, "ms": deadline}
    row = trig["row"]
    ev = eval_book(pos, row, book)
    if ev["out"] is None:
        return {"reason": None}
    res: dict[str, Any] = {"reason": trig["reason"], "ret": ev["ret"], "trigger_slot": row["slot"], "trigger_ms": trig["ms"],
                           "out_lamports": ev["out"], "delayed_out_lamports": None, "delayed_slot": None}
    if delay_slots is not None:
        dr = last_before(rows, row["slot"] + delay_slots)
        if dr is None or dr["slot"] < row["slot"]:
            dr = row
        dv = eval_book(pos, dr, book)
        if dv["out"] is not None:
            res["delayed_out_lamports"], res["delayed_slot"] = dv["out"], dr["slot"]
    return res


def side_fee(fill: dict[str, Any] | None, prio: int | None) -> int:
    """Fee of one side in lamports; `prio` swaps the priority part (base fee kept). LIVE-derived input."""
    if fill is None:
        return 0
    f = int(fill.get("fee_lamports") or 0)
    if prio is None:
        return f
    live_prio = fill.get("priority_fee_lamports")
    live_prio = int(live_prio) if live_prio is not None else max(0, f - tx.BASE_FEE_PER_SIGNATURE)
    return f - live_prio + prio


def rent_net(b: dict[str, Any], s: dict[str, Any] | None) -> int:
    """ATA rent charged on the buy minus rent refunded on the sell (0 when it nets out)."""
    if s is None:
        return 0
    return int(b.get("rent_charged_lamports") or 0) - int(s.get("rent_refunded_lamports") or 0)


def pnl_of(out: int, spend: int, b: dict[str, Any], s: dict[str, Any] | None, prio: int | None = None) -> int:
    """Live parity: proceeds - spend - both fees - failed-sell cost (extra_cost) - net ATA rent."""
    extra = int((s or {}).get("failed_attempt_cost_lamports") or 0)
    return out - spend - side_fee(b, prio) - side_fee(s, prio) - extra - rent_net(b, s)


def variant_result(name: str, ex: dict[str, Any], b: dict[str, Any], s: dict[str, Any] | None) -> dict[str, Any]:
    if ex["reason"] is None:
        return {"reason": None}
    spend = int(b["spend_lamports"])
    d = ex["delayed_out_lamports"]
    v: dict[str, Any] = {"reason": ex["reason"], "ret": ex["ret"], "trigger_slot": ex["trigger_slot"], "trigger_ms": ex["trigger_ms"],
                         "pnl_immediate_lamports": pnl_of(ex["out_lamports"], spend, b, s),
                         "pnl_delayed_lamports": None if d is None else pnl_of(d, spend, b, s)}
    if s:
        live_trig_ms = s.get("first_send_ms") or s.get("ts_ms")
        v["trigger_time_diff_ms"] = ex["trigger_ms"] - live_trig_ms if live_trig_ms else None
        if s.get("snapshot_slot"):
            v["trigger_slot_diff"] = ex["trigger_slot"] - int(s["snapshot_slot"])
        v["reason_agree"] = ex["reason"] == s.get("exit_reason")
        v["tp_sl_disagree"] = (ex["reason"] in ("tp", "sl") and s.get("exit_reason") in ("tp", "sl")
                               and ex["reason"] != s["exit_reason"])
        if s.get("pnl_lamports") is not None:
            used = v["pnl_delayed_lamports"] if d is not None else v["pnl_immediate_lamports"]
            v["pnl_gap_lamports"] = s["pnl_lamports"] - used  # live minus sim
    return v


def live_position(b: dict[str, Any], er: dict[str, Any], q: dict[str, Any]) -> dict[str, Any]:
    """`live_*` variants, the CURRENT executor's mark (buy-tx method): live tokens, net_in = spend - pool_fee_est, and the
    mark = the post-buy spot = the pre-landing tape state (last row before the landing slot) plus our buy applied with
    the live tokens, V-priced. On chain this is the pool's vault state right after our own tx, which the executor reads
    from the buy tx's postTokenBalances. mark/net_in are derived, not recorded in the fills."""
    spend, tokens = int(b["spend_lamports"]), int(b["tokens_received"])
    net = spend - int(b["pool_fee_est_lamports"]) if b.get("pool_fee_est_lamports") is not None else q["net_in"]
    snap = snap_of(er)
    mark = pcm.spot_sol_per_ui(snap.quote_priced + net, snap.base_reserve - tokens)
    return {"tokens": tokens, "net_in": net, "mark": mark, "mark_source": "buy_tx_post", "t_entry_ms": b["ts_ms"]}


def live_legacy_position(b: dict[str, Any], rows: list[dict[str, Any]], er: dict[str, Any], q: dict[str, Any]) -> dict[str, Any]:
    """`live_legacy_*`: live tokens, but the SEND-state mark (builds 8a6849b, a25eb17, 7004b16): the entry quote's
    mark on the tape row at the buy's send-state slot (`state_slot`), else on the pre-landing row."""
    pos = live_position(b, er, q)
    sr = last_before(rows, int(b["state_slot"]) + 1) if isinstance(b.get("state_slot"), int) else None
    sq = sim_buy(sr, int(b["spend_lamports"])) if sr else None
    pos["mark"], pos["mark_source"] = (sq or q)["mark"], "send_state"
    return pos


def live_snapshot_position(b: dict[str, Any], rows: list[dict[str, Any]], er: dict[str, Any], q: dict[str, Any]) -> dict[str, Any]:
    """`live_snapshot_*`: the #330 method (never installed), for comparison: the raw spot of the first tape row at or
    after the landing slot (up to ~5 s of post-landing drift folded in), else the fill price."""
    pos = live_position(b, er, q)
    first = next((r for r in rows if r["slot"] >= int(b["landed_slot"])), None)
    snap = snap_of(first) if first else None
    if snap is not None and snap.quote_priced and snap.base_reserve > 0:
        pos["mark"], pos["mark_source"] = pcm.spot_sol_per_ui(snap.quote_priced, snap.base_reserve), "landed_snapshot"
    else:
        pos["mark"], pos["mark_source"] = pos["net_in"] / (pos["tokens"] * 1000), "fill_price"
    return pos


def simulate_trade(trade: dict[str, Any], rows: list[dict[str, Any]], fill_mints: set[str],
                   own_trade_in_tape: bool = True, deadline_covered: bool = True,
                   builds: Iterable[tuple[int, str]] | None = None) -> dict[str, Any]:
    b, s = trade["buy"], trade["sell"]
    mint = b["mint"]
    if mint not in fill_mints:  # seal
        raise ValueError("seal: mint is not in the live fills file")
    spend = int(b["spend_lamports"])
    primary = "sim_correct" if own_trade_in_tape else "sim_executor"
    out: dict[str, Any] = {"mint": mint, "build": build_of(b["ts_ms"], builds), "buy_ts_ms": b["ts_ms"], "landed_slot": b["landed_slot"],
                           "live_tokens": b["tokens_received"], "spend_lamports": spend, "live_entry_vs_quote_bps": b.get("entry_vs_quote_bps"),
                           "live_exit_reason": (s or {}).get("exit_reason"), "live_ret": (s or {}).get("ret"),
                           "live_pnl_lamports": (s or {}).get("pnl_lamports"), "live_hold_ms": (s or {}).get("hold_ms"),
                           "rent_net_lamports": rent_net(b, s), "primary_variant": primary}
    out.update(cap_fields(rows, int(b["landed_slot"]), spend))
    er = last_before(rows, int(b["landed_slot"]))
    q = sim_buy(er, spend) if er else None
    if q is None:
        out["status"] = "no_entry_state"
        return out
    out.update(status="ok", entry_row_slot=er["slot"], sim_tokens=q["tokens"],
               entry_gap_bps=(q["tokens"] / b["tokens_received"] - 1) * 1e4)
    delay = None
    if s and s.get("landed_slot") and s.get("snapshot_slot"):
        delay = int(s["landed_slot"]) - int(s["snapshot_slot"])
    out["live_sell_delay_slots"] = delay
    slot = int(b["landed_slot"])
    pos_sim = {"tokens": q["tokens"], "net_in": q["net_in"], "mark": q["mark"], "t_entry_ms": b["ts_ms"]}
    has_live = bool(b.get("tokens_received"))
    pos_live = live_position(b, er, q) if has_live else None  # current: post-buy state (buy-tx mark)
    pos_legacy = live_legacy_position(b, rows, er, q) if has_live else None  # send-state mark
    pos_snapshot = live_snapshot_position(b, rows, er, q) if has_live else None  # #330 first-snapshot mark
    walks: dict[str, dict[str, Any]] = {}
    variants: dict[str, Any] = {}
    for pname, pos in (("sim", pos_sim), ("live", pos_live), ("live_legacy", pos_legacy), ("live_snapshot", pos_snapshot)):
        if pos is None:
            continue
        for book in BOOKS:
            ex = walk_exit(rows, slot, er, pos, delay, book, deadline_covered)
            walks[f"{pname}_{book}"] = ex
            variants[f"{pname}_{book}"] = variant_result(f"{pname}_{book}", ex, b, s)
    out["variants"] = variants
    pv = variants[primary]
    out["sim_exit_reason"] = pv["reason"]
    if pv["reason"] is None:
        out["status"] = "no_exit_in_tape"
        return out
    for k_out, k_v in (("sim_ret", "ret"), ("sim_trigger_slot", "trigger_slot"), ("sim_trigger_ms", "trigger_ms"),
                       ("trigger_time_diff_ms", "trigger_time_diff_ms"), ("trigger_slot_diff", "trigger_slot_diff"),
                       ("reason_agree", "reason_agree"), ("tp_sl_disagree", "tp_sl_disagree"),
                       ("sim_pnl_immediate_lamports", "pnl_immediate_lamports"), ("sim_pnl_delayed_lamports", "pnl_delayed_lamports"),
                       ("pnl_gap_lamports", "pnl_gap_lamports")):
        if k_v in pv:
            out[k_out] = pv[k_v]
    # double count: ret of the two books at the LIVE trigger row (the executor's snapshot slot), same sim position
    if s and s.get("snapshot_slot"):
        lr = last_before(rows, int(s["snapshot_slot"]) + 1)
        if lr is not None and lr["slot"] > slot:
            e, c = eval_book(pos_sim, lr, "executor"), eval_book(pos_sim, lr, "correct")
            if e["ret"] is not None and c["ret"] is not None:
                out["ret_executor_at_live_trigger"], out["ret_correct_at_live_trigger"] = e["ret"], c["ret"]
                out["ret_diff_executor_minus_correct"] = e["ret"] - c["ret"]
    pw = walks[primary]
    used = pw["delayed_out_lamports"] if pw["delayed_out_lamports"] is not None else pw["out_lamports"]
    sens: dict[str, Any] = {}
    sens["priority_150k"] = {
        "sim_pnl_lamports": pnl_of(used, spend, b, s, PRIORITY_ALT),
        "live_pnl_adjusted_lamports": None if out["live_pnl_lamports"] is None else out["live_pnl_lamports"]
        + (side_fee(b, None) + side_fee(s, None) - side_fee(b, PRIORITY_ALT) - side_fee(s, PRIORITY_ALT)),
    }
    book = "correct" if own_trade_in_tape else "executor"
    for sz in SIZES_SOL:
        sp = int(sz * LAMPORTS)
        q2 = sim_buy(er, sp)  # price impact re-simulated against the same pool state with V
        if q2 is None:
            sens[f"size_{sz}"] = None
            continue
        p2 = {"tokens": q2["tokens"], "net_in": q2["net_in"], "mark": q2["mark"], "t_entry_ms": b["ts_ms"]}
        e2 = walk_exit(rows, slot, er, p2, delay, book, deadline_covered)
        if e2["reason"] is None:
            sens[f"size_{sz}"] = {"exit_reason": None}
            continue
        o2 = e2["delayed_out_lamports"] if e2["delayed_out_lamports"] is not None else e2["out_lamports"]
        pn = pnl_of(o2, sp, b, s)
        sens[f"size_{sz}"] = {"exit_reason": e2["reason"], "entry_impact_bps_vs_spot": _impact_bps(er, sp, q2),
                              "sim_pnl_lamports": pn, "pnl_pct_of_size": pn / sp * 100}
    out["sensitivity"] = sens
    return out


def _impact_bps(row: dict[str, Any], spend: int, q: dict[str, Any]) -> float | None:
    snap = snap_of(row)
    if not q["tokens"] or snap.quote_priced is None:
        return None
    spot = snap.quote_priced / snap.base_reserve
    return ((spend / q["tokens"]) / spot - 1) * 1e4


# --- aggregates / report --------------------------------------------------------------------------


def _stats(xs: list[Any]) -> dict[str, Any]:
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "mean": statistics.fmean(xs) if xs else None, "median": statistics.median(xs) if xs else None}


def _variant_agg(rs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    vs = [r["variants"][name] for r in rs if r.get("variants", {}).get(name, {}).get("reason") and "reason_agree" in r["variants"][name]]
    dis = sum(1 for v in vs if v["tp_sl_disagree"])
    return {"n_paired": len(vs), "exit_reason_agree": sum(1 for v in vs if v["reason_agree"]),
            "tp_sl_disagree": dis,
            "trigger_time_diff_ms": _stats([v.get("trigger_time_diff_ms") for v in vs]),
            "pnl_gap_lamports_live_minus_sim": _stats([v.get("pnl_gap_lamports") for v in vs]),
            "sim_pnl_lamports": _stats([v["pnl_delayed_lamports"] if v.get("pnl_delayed_lamports") is not None else v["pnl_immediate_lamports"]
                                        for v in vs]),
            "ret": _stats([v.get("ret") for v in vs])}


def _cap_agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
    """Live trades the exploration simulator would have refused on its 15% slippage cap (scored a MISS there),
    versus the others: counts and summed live pnl. Only live-traded mints are ever in `rs` (seal)."""
    flagged = [r for r in rs if r.get("sim_cap_miss") is True]
    others = [r for r in rs if r.get("sim_cap_miss") is False]

    def pnl(xs: list[dict[str, Any]]) -> int:
        return sum(r["live_pnl_lamports"] for r in xs if r.get("live_pnl_lamports") is not None)

    return {"cap": lc.SLIPPAGE_CAP, "n_trades": len(rs), "n_sim_cap_miss": len(flagged), "n_not_cap_miss": len(others),
            "n_unknown": len(rs) - len(flagged) - len(others),
            "live_pnl_lamports_cap_miss": pnl(flagged), "live_pnl_lamports_others": pnl(others),
            "n_cap_miss_without_pnl": sum(1 for r in flagged if r.get("live_pnl_lamports") is None)}


def aggregate(results: list[dict[str, Any]], builds: Iterable[tuple[int, str]] | None = None) -> dict[str, Any]:
    """One aggregate per build (every configured build, even with no trades, then any other label present), then "all"."""
    out: dict[str, Any] = {}
    names = list(dict.fromkeys([sha for _, sha in sorted(builds if builds is not None else BUILDS)] + [r["build"] for r in results] + ["all"]))
    names = [n for n in names if n != "all"] + ["all"]
    for name in names:
        rs = [r for r in results if name == "all" or r["build"] == name]
        ok = [r for r in rs if r.get("sim_exit_reason")]
        paired = [r for r in ok if "reason_agree" in r]
        both = [r for r in paired if r["sim_exit_reason"] in ("tp", "sl") and r["live_exit_reason"] in ("tp", "sl")]
        sens: dict[str, Any] = {}
        for k in ("priority_150k", *(f"size_{z}" for z in SIZES_SOL)):
            vals = [r["sensitivity"][k] for r in ok if r.get("sensitivity", {}).get(k)]
            sens[k] = _stats([v.get("sim_pnl_lamports") for v in vals])
        dis = sum(1 for r in both if r["tp_sl_disagree"])
        rents = [r["rent_net_lamports"] for r in rs if r.get("live_pnl_lamports") is not None]
        out[name] = {
            "n_trades": len(rs), "n_sim_ok": len(ok),
            "entry_gap_bps": _stats([r.get("entry_gap_bps") for r in rs]),
            "exit_reason_agree": sum(1 for r in paired if r["reason_agree"]), "n_paired": len(paired),
            "trigger_time_diff_ms": _stats([r.get("trigger_time_diff_ms") for r in paired]),
            "tp_sl_disagree": dis, "n_tp_sl_both": len(both), "tp_sl_disagree_share": dis / len(both) if both else None,
            "pnl_gap_lamports_live_minus_sim": _stats([r.get("pnl_gap_lamports") for r in paired]),
            "live_pnl_lamports": _stats([r.get("live_pnl_lamports") for r in paired]),
            "sim_pnl_lamports": _stats([r["sim_pnl_delayed_lamports"] if r.get("sim_pnl_delayed_lamports") is not None
                                        else r["sim_pnl_immediate_lamports"] for r in paired]),
            "ret_diff_executor_minus_correct_at_live_trigger": _stats([r.get("ret_diff_executor_minus_correct") for r in rs]),
            "ata_rent_net_lamports": {"n": len(rents), "n_nonzero": sum(1 for x in rents if x), "sum": sum(rents)},
            "sim_cap_miss": _cap_agg(rs),
            "variants": {v: _variant_agg(rs, v) for v in VARIANTS},
            "sensitivity_sim_pnl_lamports": sens,
        }
    return out


def to_markdown(results: list[dict[str, Any]], agg: dict[str, Any]) -> str:
    L = ["# Probe vs simulator comparison", "",
         "ARITHMETIC ON n LIVE TRADES, NOT EVIDENCE. Not a calibration. Seal: only mints from the live fills were simulated.",
         "Sell delay and fees are LIVE-derived inputs (fee_lamports per side, sell landed_slot - sell snapshot_slot), "
         "so the pnl gap does not test them. Live-position variants derive mark and net_in from the fill (not recorded).", ""]
    for name, a in agg.items():
        n = a["n_paired"]
        L += [f"## {name}", "",
              f"Sim reproduces the executor's exit decisions on {a['exit_reason_agree']}/{n} (n={n}) (primary variant); "
              f"tp-vs-sl disagree {a['tp_sl_disagree']}/{a['n_tp_sl_both']}.", "",
              f"- trades {a['n_trades']}, simulated {a['n_sim_ok']}",
              f"- entry gap bps (sim tokens vs live): {a['entry_gap_bps']}",
              f"- trigger time diff ms (sim - live send): {a['trigger_time_diff_ms']}",
              f"- pnl gap lamports (live - sim; delay and fees are live-derived inputs): {a['pnl_gap_lamports_live_minus_sim']}",
              f"- live pnl {a['live_pnl_lamports']}; sim pnl {a['sim_pnl_lamports']}",
              f"- ret diff, legacy double-count (pre-#324 executor) minus correct book, at the live trigger row (double count): "
              f"{a['ret_diff_executor_minus_correct_at_live_trigger']}",
              f"- sim cap (latency_curve SLIPPAGE_CAP) would have refused: {a['sim_cap_miss']}",
              f"- ATA rent charged minus refunded: {a['ata_rent_net_lamports']}",
              f"- sensitivity (sim pnl lamports): {a['sensitivity_sim_pnl_lamports']}", "",
              "| variant | exit reason agree | tp/sl disagree | trigger dt ms | pnl gap (live-sim) | sim pnl | ret |", "|---|---|---|---|---|---|---|"]
        for v, va in a["variants"].items():
            L.append(f"| {v} | {va['exit_reason_agree']}/{va['n_paired']} | {va['tp_sl_disagree']} | {va['trigger_time_diff_ms']['mean']} | "
                     f"{va['pnl_gap_lamports_live_minus_sim']['mean']} | {va['sim_pnl_lamports']['mean']} | {va['ret']['mean']} |")
        L.append("")
    L += ["## Per trade (primary variant)", "",
          "| build | mint | status | entry gap bps | live exit | sim exit | trig dt ms | ret diff exec-correct | live pnl | sim pnl | gap |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]

    def f(x: Any) -> str:
        return "" if x is None else (f"{x:.4g}" if isinstance(x, float) else str(x))

    for r in results:
        sp = r.get("sim_pnl_delayed_lamports")
        sp = sp if sp is not None else r.get("sim_pnl_immediate_lamports")
        L.append(f"| {r['build']} | {r['mint'][:8]} | {r['status']} | {f(r.get('entry_gap_bps'))} | {f(r['live_exit_reason'])} | "
                 f"{f(r.get('sim_exit_reason'))} | {f(r.get('trigger_time_diff_ms'))} | {f(r.get('ret_diff_executor_minus_correct'))} | "
                 f"{f(r['live_pnl_lamports'])} | {f(sp)} | {f(r.get('pnl_gap_lamports'))} |")
    L += ["", "## Per trade, every variant (exit reason / ret; sim_correct is the tape-sim entry, live_* use the live fill)", "",
          "| build | mint | live exit / ret | sim_correct | live_correct (buy-tx mark) | live_snapshot_correct (#330) | live_legacy_correct (send-state) |",
          "|---|---|---|---|---|---|---|"]

    def vr(r: dict[str, Any], name: str) -> str:
        v = (r.get("variants") or {}).get(name)
        return "" if not v else f"{f(v.get('reason'))} / {f(v.get('ret'))}"

    for r in results:
        L.append(f"| {r['build']} | {r['mint'][:8]} | {f(r['live_exit_reason'])} / {f(r.get('live_ret'))} | "
                 + " | ".join(vr(r, v) for v in ("sim_correct", "live_correct", "live_snapshot_correct", "live_legacy_correct")) + " |")
    L += ["", "Unverified: tape reserves are post-trade state; live trigger time approximated by the sell's first_send_ms; "
          "sell delay = live sell landed_slot - live sell snapshot_slot; a time stop is emitted when the tape file of the "
          "deadline hour exists, even if no row follows."]
    return "\n".join(L) + "\n"


def run(fills_path: Path, tape_dir: Path, out_dir: Path, own_trade_in_tape: bool = True,
        builds: Iterable[tuple[int, str]] | None = None) -> dict[str, Any]:
    trades = pair_trades(load_fills(fills_path))
    fill_mints = {t["buy"]["mint"] for t in trades}
    hours = hours_needed(trades)
    tape = read_tape_rows(tape_dir, hours, fill_mints)
    present = {h for h in hours if (tape_dir / f"trades-{h}.jsonl").exists() or (tape_dir / f"trades-{h}.jsonl.zst").exists()}
    results = []
    for t in trades:
        dl = t["buy"]["ts_ms"] + pe.EXIT_RULE.max_hold_ms
        results.append(simulate_trade(t, tape.get(t["buy"]["mint"], []), fill_mints, own_trade_in_tape,
                                      deadline_covered=_hour_name(dl) in present, builds=builds))
    agg = aggregate(results, builds)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "calibration.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "label": "arithmetic on n trades, not evidence", "own_trade_in_tape": own_trade_in_tape,
                    "builds": [list(b) for b in sorted(builds if builds is not None else BUILDS)],
                    "aggregate": agg, "trades": results}, indent=1))
    (out_dir / "calibration.md").write_text(to_markdown(results, agg))
    return agg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fills", type=Path, required=True)
    ap.add_argument("--tape-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--own-trade-in-tape", action=argparse.BooleanOptionalAction, default=True,
                    help="primary variant prices exits on the raw tape book (our buy is already in post-landing rows); "
                         "default on. Both books are always reported.")
    ap.add_argument("--build", action="append", default=[], metavar="START_MS:SHA",
                    help="build boundary, repeatable; replaces the BUILDS constant (a trade belongs to the last boundary at or before its buy)")
    a = ap.parse_args(argv)
    agg = run(a.fills, a.tape_dir, a.out_dir, a.own_trade_in_tape, parse_builds(a.build) if a.build else None)
    print(json.dumps({k: {"n_trades": v["n_trades"], "n_sim_ok": v["n_sim_ok"]} for k, v in agg.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
