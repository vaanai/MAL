#!/usr/bin/env python3
"""Arena H5 scorer: prices the fresh-forward shadow's records into the arena ledger on DEC-031 rev 2's deciding stack. Paper only. Prints COUNTS ONLY.

BOOKS. Two books, one trigger rule, two exits, registered in bots.json before their clock (`register`):
  h5v1-boostfloor-a1   exit at s0 + 330 s (the rule's exit).
  h5v2-boostclock-a1   the v2 BOOSTCLOCK exit (cell C10, EXP-026): the same trigger and entry legs, the exit instant from the BOOST signer's observed slices.
Only the `pv` variant (per-print V) is a trade; `fv` is not scored. A bot_id is bound to ONE frozen_sha. The scorer refuses a shadow dir whose start record's code_sha is
null, differs from the registered sha, is marked dirty, or whose process started before the registration; and it counts a trade only if its decision instant is at or
after the book's clock and before its window end. RESTART POLICY: a restart at the SAME code_sha continues the same book (the downtime is a gap, logged as such in the counts,
and the pools in it are untracked; a pool still open at the restart is an `incomplete` exclusion). Any other change makes a NEW bot_id with a new clock.
A3 RULE: an A3 HALT flag ends both books' counted windows at the flag's instant (`halt`): nothing decided at or after it counts; a new bot_id can start after review.

DECIDING STACK (DEC-031 s1.4, rev 2; tools/arena_h5_cost.py): 505,000 lamports per send, rent 2,039,280 per filled trip, the DEC-021 haircut with the E1 floor (the larger
of the haircut and 384,022 lamports per 0.05 SOL of stake), the 1.15 spot guard, the strand class as a total loss, exit lag ceil(0.55 s / sps) slots and at least 2, flat 15%
and pressure fail legs, entry latency 1.3 s and 1.9 s. LANDING is measured from the decision instant on the CHAIN clock, so the feed delay is not free: age = t_detect_ms - block_time*1000, where block_time is the trigger print's
on-chain Clock timestamp in whole seconds; the age is therefore the feed delay (a feed at confirmed commitment lags the chain) + the host's detect delay + 0 to 1 s of rounding (overstated, never
understated), and landing slot = trigger slot + ceil((age + latency) / sps) ~ the trigger print's slot + d feed-delay slots + ceil(latency / sps), with d measured on every trade (age / (1000 sps)) instead of
a configured constant. Each row records feed_delay_ms, feed_delay_slots, the uncorrected landing slots (from the trigger print's slot) and the corrected ones. The states, the pressure inputs and the spot are recomputed from the shadow's `strip`
(pre-trade reserves from the trigger print to the end of the pool's life; `buys` from 50 slots before it) and `trigger` records. The deciding cell is the 1.9 s leg at the
stake pinned in bots.json (0.30 SOL); 1.3 s and the other stakes (0.10, 0.25) are reported beside it and never decide. The pressure intercept that DECIDES is refit at the read,
per bot and cell, on the counted 1.9 s set after exclusions (`refit`); the ledger's press_* values carry the frozen exploration intercept and are provisional.
REPORT-ONLY legs (row.report_only.h5_frozen_55k): the H5 pre-registered stack (55,000 per send, no rent, haircut, floor or guard) at the shadow's own landing and exit states.

EXCLUSIONS (counted, never dropped silently; no_block_time = a trigger without a chain timestamp, never aged from the receipt; written to <ledger>/<bot_id>/exclusions-<day>.jsonl, not to the trades file): gap_flag (a feed gap flagged the pool anywhere from its first print to the end of the trade; a gap after the exit landing does not count),
base_breaks (base_breaks_unresolved_settled not 0, the executor refuses it), trigger_age (decision more than 3,500 ms after the trigger's block time), strip (no strip, a
truncated strip or a truncated buys list), exit_before_landing (the deciding leg's exit is not after its landing), incomplete (the outcome was cut by a stop or restart).
If more than 5% of a book's attempts are excluded, the read is NOT_DECIDABLE (`not_decidable`). A bx10 error or a missing bx10 record is PRICED at v1's exit, not skipped.

REPORT beside the counts: feed_delay_ms p50 / p90 over every attempt that has a block time (counted or excluded), age_clamped_to_zero (host-versus-chain skew), and trigger_age_exclusions on
its own line. The age carries an error of order +-1 s (whole-second chain timestamp, host clock drift); the executor's binding age check uses its own slot clock, so the scorer can exclude
a late trade the executor would send (true age about 2.5 to 3.5 s): the 5% cap bounds it, and the count is printed.
READ GATING. One read per bot_id at the outcome-blind instant: the first 00:00Z at which the book has at least 100 attempts (every pv trigger in the window, excluded or not) on
at least 5 UTC dates, counted from trigger timestamps by `read_instant`, which takes a list of integers and loads no P&L. Until then, and after it, this tool prints counts
only; the gate itself is tools/arena_score.py's. `score` records the book's ONE read instant (read-instant.json, computed from every attempt, excluded and open ones included); `refit` reads that instant and never computes its own, refuses before it, while an attempt decided before it is still open, or if trades plus exclusions in the ledger do not add up to the recorded attempts.

BOOST TIMING (EXP-024 s11): the share of counted trades whose BOOST last slice (the pool record's boost_last_slice_s) comes before the 1.9 s leg's exit landing; above 15% the
book is "not proposed for live".

LEDGER. <ledger-root>/<bot_id>/trades-<UTC day of decision_ms>.jsonl, strict JSON, WRITE-ONCE per trade_id ("<bot_id>:<pool>:<stake>"; one row per stake). Fields: trade_id, bot_id,
frozen_sha, decision_ms, exit_ms, pool, mint, prog "pumpswap", stake_sol, legs {flat_1p3, flat_1p9, press_1p3, press_1p9} (net_lamports, nofail_lamports, state, p_fail; press
also ssb, nb_lamports, intercept), report_only, meta. An unparsable line in an existing file is counted (bad_ledger_lines) and the scorer stops without writing.

Run:  python -m tools.arena_h5_score register --arena-root R --frozen-sha SHA --sockets 3 --endpoint E ...
      python -m tools.arena_h5_score score [--arena-root R] [--shadow-dir D ...] [--dry-run]
      python -m tools.arena_h5_score halt --arena-root R --at-ms N          (A3 HALT flag)
      python -m tools.arena_h5_score refit --arena-root R                   (only at or after the read instant)
Pure python (no numpy).
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_h5_score.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import fcntl
import glob
import json
import math
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from tools import arena_h5_cost as cost
from tools import h5_shadow as h5

SCHEMA = "arena_h5_trade_v1"
STACK = cost.STACK_ID
BOTS = {"h5v1-boostfloor-a1": "v1", "h5v2-boostclock-a1": "v2"}  # bot id -> exit rule
VARIANT = "pv"
LEGS = (("primary", "1p3", 1.3), ("binding", "1p9", 1.9))  # the shadow's leg name, the ledger suffix, the entry latency (s)
DECIDING_LEG = "binding"
STAKES = (("0.10", 100_000_000), ("0.25", 250_000_000), ("0.30", 300_000_000))
DECIDING_STAKE = "0.30"  # pinned in bots.json before the clock; the lab's impact limit for H5
MAX_STAKE_LAMPORTS = 300_000_000
MAX_TRIGGER_AGE_MS = 3_500  # the executor's pre-send age limit (tools/h5_executor.PRESEND_MAX_TRIGGER_AGE_MS)
EXCLUDED_CAP = 0.05
BOOST_BEFORE_EXIT_CAP = 0.15
READ_MIN_ATTEMPTS, READ_MIN_DATES = 100, 5
EXIT_LAG_S = 0.55
PRESSURE_WINDOW_S = 2.0
FREEZE_SHA256 = "5368cc7b757a2f7a6cb064c71193b4e0cd740583f7348a60cb9a864401c190e0"  # FREEZE.md of H5-BOOSTCLOCK-EXIT (v2); not pinned in code before
EXCLUSION_REASONS = ("gap_flag", "base_breaks", "trigger_age", "no_block_time", "strip", "exit_before_landing", "incomplete")
DAY_MS = 86_400_000


# ---------------------------------------------------------------------------------------------------------------- slots and states
def raw_trigger_age_ms(trig: dict) -> int:
    """Decision instant (host wall clock) minus the trigger print's CHAIN time: the print's block_time, the on-chain Clock unix timestamp in whole seconds. This is the feed delay
    (a feed at confirmed commitment lags the chain) plus the host's own detect delay plus the 0 to 1 s the whole-second timestamp rounds down: the age is overstated by 0 to 1 s
    (about 0.5 s on average), never understated. A trigger without a block time has no chain clock: price_trade makes it a counted exclusion (`no_block_time`), it is never
    aged from the receipt of the print (that would drop the feed delay and favour the book)."""
    return int(trig["t_detect_ms"]) - int(trig["block_time"]) * 1000


def trigger_age_ms(trig: dict) -> int:
    """The raw age, floored at 0 (a negative age is host-versus-chain clock skew; counted as `age_clamped_to_zero` and flagged on the row)."""
    return max(0, raw_trigger_age_ms(trig))


def landing_slot(trigger_slot: int, sps: float, age_ms: int, latency_s: float) -> int:
    return int(trigger_slot) + math.ceil((age_ms + latency_s * 1000.0) / (sps * 1000.0) - 1e-9)


def exit_lag_slots(sps: float) -> int:
    return max(2, math.ceil(EXIT_LAG_S / sps - 1e-9))


class Strip:
    """A shadow strip record: pre-trade (Q incl. V, base) of every print from the trigger print on, the state after the last print, and the buys."""

    def __init__(self, rec: dict) -> None:
        self.rows = rec["rows"]  # [slot, q_pv_pre, q_fv_pre, base_pre]
        self.post_last = rec["post_last"]  # [slot, q_pv_post, q_fv_post, base_post]
        self.buys = rec.get("buys")
        self.truncated = bool(rec.get("truncated")) or bool(rec.get("buys_truncated")) or self.buys is None
        self.from_slot = rec["from_slot"]

    def state_at(self, slot: int) -> tuple[float, float, str]:
        """END bound (the shadow's _state_at): the pre-trade state of the first print in a slot later than `slot`, else the state after the last print."""
        for r in self.rows:
            if r[0] > slot:
                return float(r[1]), float(r[3]), "next_pre"
        return float(self.post_last[1]), float(self.post_last[3]), "post"

    def pressure_at(self, slot: int, sps: float) -> tuple[int, int]:
        """(buys in the landing slot, buy lamports in the 2 s up to and including it), as the shadow computes them."""
        w = int(round(PRESSURE_WINDOW_S / sps))
        ssb = sum(1 for s, _ in self.buys if s == slot)
        nb = sum(x for s, x in self.buys if slot - w <= s <= slot)
        return ssb, int(nb)


def decision_spot(trig: dict) -> float:
    """The pool's price right after the trigger print (known at the decision): post-trade Q (incl. V) over the post-trade base (base_pre + the sold tokens)."""
    return float(trig["q_trigger_sol"]) * 1e9 / (float(trig["base_pre"]) + float(trig["sell_token_raw"]))


def bx10_exit_trigger(bx: dict, o: dict, landing: int) -> int:
    """The v2 exit trigger slot for an entry landing slot: tools.h5_shadow.bx10_exit on the BOOST signer's slices (as the shadow does), capped at v1's exit."""
    sps, s0, v1_slot = float(o["sps"]), int(o["s0"]), int(o["exit"]["trigger_slot"])
    t = [off * sps for off, _, _ in bx["slices"]]
    a = [x for _, x, _ in bx["slices"]]
    tau, fired, _, _ = h5.bx10_exit(t, a, (landing - s0) * sps, h5.EXIT_AFTER_S0_S)
    return min(max(s0 + math.ceil(tau / sps - 1e-9), landing + 1), v1_slot) if fired else v1_slot


# ---------------------------------------------------------------------------------------------------------------- reading a shadow dir
def read_records(shadow_dir: Path, counts: collections.Counter) -> Iterator[dict]:
    """Records of the dir's hourly files, file order then line order. A line that is not strict JSON is counted and skipped."""
    for f in sorted(shadow_dir.glob("h5-shadow-????-??-??T??.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line, parse_constant=_no_constant)
                except ValueError:
                    counts["bad_shadow_lines"] += 1


def _no_constant(name: str) -> Any:
    raise ValueError(f"non-strict JSON constant {name}")


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class ShadowDir:
    """Everything the scorer needs from one dir: the start records, and the pv trigger / outcome / outcome_bx10 / strip / pool records by pool, each with the start record
    of the run that wrote it. `downtime_ms`: the gaps between a run's last record and the next run's start."""

    def __init__(self, path: Path, counts: collections.Counter) -> None:
        self.path = path
        self.starts: list[dict] = []
        self.triggers: dict[str, dict] = {}
        self.outcomes: dict[str, dict] = {}
        self.bx: dict[str, dict] = {}
        self.strips: dict[str, dict] = {}
        self.pools: dict[str, dict] = {}
        self.downtime_ms = 0
        cur: dict | None = None
        last_t: int | None = None
        for rec in read_records(path, counts):
            t = rec.get("type")
            if t == "start":
                if last_t is not None and rec.get("start_ms") is not None:
                    self.downtime_ms += max(0, int(rec["start_ms"]) - last_t)
                cur = rec
                self.starts.append(rec)
            if rec.get("t_ms") is not None:
                last_t = int(rec["t_ms"])
            pool = rec.get("pool")
            if pool is None or t not in ("trigger", "outcome", "outcome_bx10", "strip", "pool"):
                continue
            if t in ("trigger", "outcome", "outcome_bx10") and rec.get("variant") != VARIANT:
                continue
            store = {"trigger": self.triggers, "outcome": self.outcomes, "outcome_bx10": self.bx, "strip": self.strips, "pool": self.pools}[t]
            store.setdefault(pool, {**rec, "_start": cur})

    def identity_problem(self, entry: dict) -> str | None:
        """Why this dir may not feed the book (None: it may). Every start record in it must be the registered one."""
        if not self.starts:
            return "no_start_record"
        for s in self.starts:
            if s.get("fresh_forward") != h5.FRESH_FORWARD_REF:
                return "not_fresh_forward"
            if not s.get("code_sha"):
                return "null_code_sha"
            if s["code_sha"] != entry["frozen_sha"]:
                return "sha_mismatch"
            if s.get("dirty"):
                return "dirty"
            if s.get("start_ms") is None or int(s["start_ms"]) < int(entry["registered_at_ms"]):
                return "started_before_registration"
        return None


# ---------------------------------------------------------------------------------------------------------------- registry
def _atomic_write_json(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(doc, f, sort_keys=True, indent=1, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_once_json(path: Path, doc: dict) -> None:
    """Write `path` only if it does not exist (hard link of a fsynced temp file: atomic, and FileExistsError instead of an overwrite)."""
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    with open(tmp, "w") as f:
        json.dump(doc, f, sort_keys=True, indent=1, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, path)
    finally:
        os.unlink(tmp)


def load_registry(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text()).get("bots", {}))


def _with_registry_lock(path: Path, fn):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(path.suffix + ".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        return fn()


def register_books(path: Path, *, frozen_sha: str, sockets: int, endpoints: Sequence[str], host: str, clock_start_ms: int | None = None, now_ms: int | None = None,
                   stake: str = DECIDING_STAKE, generation: str = "a1") -> dict[str, dict]:
    """Write the two books' lines. Before the clock; never edited after (a line with the same bot_id and a different sha or clock is refused; no line is ever removed).
    The shadow process must START after registered_at_ms (the scorer enforces it)."""
    if not frozen_sha or len(frozen_sha) != 40 or any(c not in "0123456789abcdef" for c in frozen_sha):
        raise ValueError("frozen_sha must be a 40-hex git sha")
    reg_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    clock = int(clock_start_ms) if clock_start_ms is not None else reg_ms
    out = {}
    for bot, rule in BOTS.items():
        bot_id = bot[:-2] + generation if generation != "a1" else bot
        entry = {"frozen_sha": frozen_sha, "clock_start_ms": clock, "registered_at_ms": reg_ms, "deciding_stake_sol": float(stake), "deciding_cell": "1p9",
                 "stack": STACK, "rule": "H5-BOOSTFLOOR v1", "exit_rule": rule, "sockets": int(sockets), "endpoints": list(endpoints), "host": host,
                 "window_end_ms": None, "stopped_ms": None, "scorer": "tools/arena_h5_score.py", "cost_module": "tools/arena_h5_cost.py"}
        if rule == "v2":
            entry["freeze_sha256"] = FREEZE_SHA256
        out[bot_id] = entry

    def go():
        bots = load_registry(path)
        for bot_id, entry in out.items():
            if bot_id in bots:
                old = bots[bot_id]
                if any(old.get(k) != entry[k] for k in ("frozen_sha", "clock_start_ms", "deciding_stake_sol")):
                    raise ValueError(f"{bot_id} is registered with a different sha, clock or stake: a change is a new bot_id")
                out[bot_id] = old
            else:
                if clock < reg_ms:
                    raise ValueError("clock_start_ms is before registered_at_ms")
                bots[bot_id] = entry
        _atomic_write_json(path, {"bots": bots})
        return out

    return _with_registry_lock(path, go)


def set_window_end(path: Path, at_ms: int) -> dict[str, dict]:
    """The A3 rule: an A3 HALT flag ends BOTH books' counted windows at the flag's instant. Set once per book (an earlier end is never moved later); lines are not removed."""
    def go():
        bots = load_registry(path)
        if not bots:
            raise ValueError("no books are registered")
        for bot_id, e in bots.items():
            if not bot_id.startswith(("h5v1-boostfloor-", "h5v2-boostclock-")):
                continue
            cur = e.get("window_end_ms")
            if cur is None or at_ms < cur:
                e["window_end_ms"] = int(at_ms)
                e["stopped_ms"] = int(at_ms)
        _atomic_write_json(path, {"bots": bots})
        return bots

    return _with_registry_lock(path, go)


# ---------------------------------------------------------------------------------------------------------------- pricing one trade
class Excluded(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def gap_in_span(trig: dict, o: dict, span_end_slot: int) -> bool:
    """True when a feed gap flagged this pool at any point from its first print to the end of the trade (decision to exit landing, `span_end_slot`): DEC-031 s1.4 excludes
    such a trade by that rule alone. The shadow lists the first 5 gaps in chronological order, each with from_slot when it is a slot jump; a gap without a slot (a socket
    reconnect, a silence) or a flag without a list counts as inside. A gap that starts after the span (the pool's last seconds, up to s0 + 400 s) does not."""
    if not (trig.get("gap") or o.get("gap")):
        return False
    gaps = list(trig.get("gaps") or []) + list(o.get("gaps") or [])
    if not gaps:
        return True
    return any(g.get("from_slot") is None or int(g["from_slot"]) <= span_end_slot for g in gaps)


def price_trade(bot: str, o: dict, trig: dict, strip_rec: dict | None, bx: dict | None, pool_rec: dict | None, start: dict) -> dict[str, Any]:
    """Rows data of one pv trade for one bot (all stakes, both legs) or raises Excluded(reason). The exclusion order is the module doc's."""
    if not o.get("complete") or not (o.get("legs")):
        raise Excluded("incomplete")
    if not trig.get("block_time"):
        raise Excluded("no_block_time")  # no chain clock, no feed delay: excluded and counted under the 5% cap, never aged from t_recv_ms
    if gap_in_span(trig, o, int(o["exit"]["trigger_slot"]) + exit_lag_slots(float(o["sps"]))):
        raise Excluded("gap_flag")
    if trig.get("base_breaks_unresolved_settled") != 0:
        raise Excluded("base_breaks")
    raw_age = raw_trigger_age_ms(trig)
    age = max(0, raw_age)
    if age > MAX_TRIGGER_AGE_MS:
        raise Excluded("trigger_age")
    if strip_rec is None:
        raise Excluded("strip")
    strip = Strip(strip_rec)
    if strip.truncated:
        raise Excluded("strip")
    sps, tslot, v1_slot = float(o["sps"]), int(o["trigger_slot"]), int(o["exit"]["trigger_slot"])
    el = exit_lag_slots(sps)
    spot = decision_spot(trig)
    bx_priced_v1 = False
    if bot.startswith("h5v2"):
        bx_priced_v1 = bx is None or "error" in bx or "legs" not in bx or "slices" not in bx  # missing or errored: priced at v1's exit, counted
    legs: dict[str, dict] = {}
    for shadow_leg, sfx, lat in LEGS:
        land = landing_slot(tslot, sps, age, lat)
        land_uncorrected = landing_slot(tslot, sps, 0, lat)  # measured from the trigger print's slot: what the shadow itself records, with the feed delay for free
        if bot.startswith("h5v2") and not bx_priced_v1:
            xtrig = bx10_exit_trigger(bx, o, land)
        else:
            xtrig = v1_slot
        if xtrig <= land:
            legs[shadow_leg] = {"ok": False, "landing": land, "landing_uncorrected": land_uncorrected, "exit_trigger": xtrig}
            continue
        xland = xtrig + el
        qe, be, esrc = strip.state_at(land)
        qx, bx_, xsrc = strip.state_at(xland)
        ssb, nb = strip.pressure_at(land, sps)
        legs[shadow_leg] = {"ok": True, "landing": land, "landing_uncorrected": land_uncorrected, "exit_trigger": xtrig, "exit_landing": xland, "qe": qe, "be": be, "qx": qx, "bx": bx_, "ssb": ssb, "nb": nb,
                            "state_src": (esrc, xsrc)}
    if not legs[DECIDING_LEG]["ok"]:
        raise Excluded("exit_before_landing")
    return {"legs": legs, "spot": spot, "age_ms": age, "age_raw_ms": raw_age, "age_clamped": raw_age < 0, "sps": sps,
            "bx_priced_v1": bx_priced_v1, "el": el}


def ledger_rows(bot: str, o: dict, trig: dict, priced: dict, start: dict, bx: dict | None, pool_rec: dict | None) -> list[dict]:
    """One ledger row per stake from price_trade's result. The report-only 55k legs use the shadow's own landing and exit states."""
    sps, age = priced["sps"], priced["age_ms"]
    anchor = int(trig["t_detect_ms"]) - age
    decision_ms = int(trig["t_detect_ms"])
    exits = [lg["exit_landing"] for lg in priced["legs"].values() if lg["ok"]]
    exit_ms = anchor + int(round((max(exits) - int(o["trigger_slot"])) * sps * 1000))
    rows = []
    for label, lam in STAKES:
        if lam > MAX_STAKE_LAMPORTS:
            raise ValueError("stake above the H5 impact limit")
        legs: dict[str, Any] = {}
        for shadow_leg, sfx, _ in LEGS:
            lg = priced["legs"][shadow_leg]
            if not lg["ok"]:
                legs[f"flat_{sfx}"] = {"net_lamports": None, "state": "exit_before_landing"}
                legs[f"press_{sfx}"] = {"net_lamports": None, "state": "exit_before_landing"}
                continue
            t = cost.deciding_round_trip(lam, lg["qe"], lg["be"], priced["spot"], lg["qx"], lg["bx"])
            t_sc = cost.deciding_round_trip(lam, lg["qe"], lg["be"], priced["spot"], lg["qx"], lg["bx"], strand="sc")
            p = cost.press_p(cost.H5_PRESS_INTERCEPT[shadow_leg], lg["ssb"], lg["nb"])
            sc_flat = {"net_lamports": cost.flat_leg(t_sc["pnl"], t_sc["state"]), "nofail_lamports": t_sc["pnl"], "state": t_sc["state"]}
            sc_press = {"net_lamports": cost.press_leg(t_sc["pnl"], t_sc["state"], p), "nofail_lamports": t_sc["pnl"], "state": t_sc["state"]}
            legs[f"flat_{sfx}"] = {"net_lamports": cost.flat_leg(t["pnl"], t["state"]), "nofail_lamports": t["pnl"], "state": t["state"], "p_fail": cost.FAIL_FLAT, "sc": sc_flat}
            legs[f"press_{sfx}"] = {"net_lamports": cost.press_leg(t["pnl"], t["state"], p), "nofail_lamports": t["pnl"], "state": t["state"], "p_fail": p, "ssb": lg["ssb"],
                                    "nb_lamports": lg["nb"], "intercept": cost.H5_PRESS_INTERCEPT[shadow_leg], "intercept_kind": "frozen_report_only",
                                    "provisional": True, "e1_extra": t["e1_extra"], "sc": sc_press}
        rows.append({
            "trade_id": f"{bot}:{o['pool']}:{label}", "bot_id": bot, "frozen_sha": start.get("code_sha"), "decision_ms": decision_ms, "exit_ms": exit_ms,
            "pool": o["pool"], "mint": o.get("mint"), "prog": "pumpswap", "stake_sol": lam / 1e9, "legs": legs,
            "report_only": {"h5_frozen_55k": _report_only_55k(bot, o, bx, priced, lam)},
            "meta": {"schema": SCHEMA, "stack": STACK, "variant": VARIANT, "start_ms": int(start["start_ms"]), "sps": sps, "age_ms": age, "spot": priced["spot"],
                     "feed_delay_ms": age, "feed_delay_slots": age / (sps * 1000.0), "age_clock": "block_time_s",
                     "age_raw_ms": priced["age_raw_ms"], "age_clamped": priced["age_clamped"],
                     "landing_slots_uncorrected": {sfx: priced["legs"][sl]["landing_uncorrected"] for sl, sfx, _ in LEGS},
                     "exit_lag_slots": priced["el"], "exit_rule": ("330s" if bot.startswith("h5v1") else ("v1_exit_after_bx10_error" if priced["bx_priced_v1"] else "bx10")),
                     "landing_slots": {sfx: priced["legs"][sl]["landing"] for sl, sfx, _ in LEGS},
                     "state_src": {sfx: priced["legs"][sl].get("state_src") for sl, sfx, _ in LEGS},
                     "exit_trigger_slots": {sfx: priced["legs"][sl]["exit_trigger"] for sl, sfx, _ in LEGS},
                     "chain_breaks": o.get("chain_breaks"), "base_breaks": o.get("base_breaks"), "boost_src": trig.get("boost_src"),
                     "boost_last_slice_s": (pool_rec or {}).get("boost_last_slice_s")},
        })
    return rows


def _report_only_55k(bot: str, o: dict, bx: dict | None, priced: dict, lam: int) -> dict[str, Any]:
    """H5's pre-registered stack at the shadow's own states (landing from the trigger print, exit lag ceil(0.55/sps)): report-only."""
    out: dict[str, Any] = {}
    for shadow_leg, sfx, _ in LEGS:
        ent = o["legs"].get(shadow_leg) or {}
        if bot.startswith("h5v2") and not priced["bx_priced_v1"]:
            b = ((bx or {}).get("legs") or {}).get(shadow_leg) or {}
            xq, xb = b.get("q_sol"), b.get("base")
        else:
            xq, xb = o["exit"]["q_sol"], o["exit"]["base"]
        if ent.get("net") is None or xq is None:
            out[f"flat_{sfx}"] = {"net_lamports": None}
            out[f"press_{sfx}"] = {"net_lamports": None}
            continue
        nf = cost.h5_frozen_round_trip(lam, ent["q_sol"] * 1e9, ent["base"], xq * 1e9, xb)
        p = cost.press_p(cost.H5_PRESS_INTERCEPT[shadow_leg], ent["ssb"], ent["nb_lamports"])
        out[f"flat_{sfx}"] = {"net_lamports": (1 - cost.FAIL_FLAT) * nf + cost.FAIL_FLAT * -cost.H5_SEND, "nofail_lamports": nf}
        out[f"press_{sfx}"] = {"net_lamports": (1 - p) * nf + p * -cost.H5_SEND, "nofail_lamports": nf, "p_fail": p, "intercept_kind": "frozen_report_only"}
    return out


# ---------------------------------------------------------------------------------------------------------------- one book over some dirs
def read_instant(decisions_ms: Sequence[int], now_ms: int) -> int | None:
    """The outcome-blind read instant: the first 00:00Z at which at least READ_MIN_ATTEMPTS decisions lie before it on at least READ_MIN_DATES UTC dates. Takes decision
    timestamps ONLY (a list of integers: it loads no fill, exit or P&L). None until that boundary is in the past (`now_ms`)."""
    ds = sorted(int(x) for x in decisions_ms)
    if len(ds) < READ_MIN_ATTEMPTS:
        return None
    first_day = ds[0] // DAY_MS
    last_day = ds[-1] // DAY_MS
    for day in range(first_day + 1, last_day + 2):
        boundary = day * DAY_MS
        before = [d for d in ds if d < boundary]
        if len(before) >= READ_MIN_ATTEMPTS and len({d // DAY_MS for d in before}) >= READ_MIN_DATES:
            return boundary if boundary <= now_ms else None
    return None


class BookResult:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.exclusions: list[dict] = []
        self.decisions: list[int] = []  # every attempt in the window (pv trigger), excluded or not, for the read instant
        self.open_decisions: list[int] = []  # the attempts among them with no outcome record yet
        self.ages: list[int] = []  # feed delay (age, ms, floored at 0) of every attempt that has a block time, counted or excluded
        self.counts: collections.Counter = collections.Counter()
        self.boost_before_exit = 0
        self.boost_known = 0
        self.refused: dict[str, str] = {}
        self.restarts = 0
        self.downtime_ms = 0


def score_book(bot: str, entry: dict, dirs: Sequence[ShadowDir]) -> BookResult:
    res = BookResult()
    clock, end = int(entry["clock_start_ms"]), entry.get("window_end_ms")
    seen: set[str] = set()
    for d in sorted(dirs, key=lambda x: x.path.name):
        problem = d.identity_problem(entry)
        if problem:
            res.refused[str(d.path)] = problem
            res.counts["refused_dirs"] += 1
            continue
        res.restarts += max(0, len(d.starts) - 1)
        res.downtime_ms += d.downtime_ms
        for pool, trig in sorted(d.triggers.items(), key=lambda kv: kv[1]["t_detect_ms"]):
            decision = int(trig["t_detect_ms"])
            start = trig["_start"]
            if decision < clock or decision < int(start["start_ms"]):
                res.counts["skipped_before_clock"] += 1
                continue
            if end is not None and decision >= int(end):
                res.counts["skipped_after_window"] += 1
                continue
            if trig.get("h5_look2_sealed"):
                res.counts["sealed_decision_only"] += 1  # s0 from 2026-11-06T00Z: no outcome was written (the Look-2 seal's end); not part of the book
                continue
            if pool in seen:
                res.counts["duplicate_pool"] += 1
                continue
            seen.add(pool)
            res.decisions.append(decision)
            if trig.get("block_time"):
                raw = raw_trigger_age_ms(trig)
                res.ages.append(max(0, raw))
                if raw < 0:
                    res.counts["age_clamped_to_zero"] += 1  # host-versus-chain clock skew: floored at 0, flagged on the row, counted here
            o = d.outcomes.get(pool)
            if o is None:
                res.open_decisions.append(decision)
                res.counts["open"] += 1  # decided, no outcome record yet (still open, or cut by a stop: exits are awaited before a read)
                continue
            bx = d.bx.get(pool)
            try:
                priced = price_trade(bot, o, trig, d.strips.get(pool), bx, d.pools.get(pool), trig["_start"])
            except Excluded as ex:
                res.counts[f"excluded_{ex.reason}"] += 1
                res.exclusions.append({"exclusion_id": f"{bot}:{pool}", "bot_id": bot, "pool": pool, "mint": trig.get("mint"), "reason": ex.reason, "decision_ms": decision,
                                       "frozen_sha": start.get("code_sha")})
                continue
            if priced["bx_priced_v1"]:
                res.counts["bx10_error_priced_v1"] += 1
            dl = priced["legs"][DECIDING_LEG].get("state_src") or ()
            if "post" in dl:
                res.counts["derived_last_deciding_leg"] += 1  # a state taken after the pool's last print: counted at its value, never excluded (quant-proof, #708)
            res.counts["counted"] += 1
            res.rows.extend(ledger_rows(bot, o, trig, priced, trig["_start"], bx, d.pools.get(pool)))
            pr = d.pools.get(pool) or {}
            last = pr.get("boost_last_slice_s")
            if last is not None:
                res.boost_known += 1
                xl = priced["legs"][DECIDING_LEG]["exit_landing"]
                if last < (xl - int(o["s0"])) * priced["sps"]:
                    res.boost_before_exit += 1
    return res


def _pct(xs: Sequence[int], q: float) -> int | None:
    """Nearest-rank percentile of integers (the k-th smallest, k = ceil(q n)); None for an empty list."""
    if not xs:
        return None
    ys = sorted(xs)
    return ys[max(1, math.ceil(q * len(ys))) - 1]


def summarize(bot: str, res: BookResult, now_ms: int) -> dict[str, Any]:
    n_exc = len(res.exclusions)
    attempts = len(res.decisions)
    decided = res.counts["counted"] + n_exc  # attempts with an outcome record
    share = n_exc / decided if decided else 0.0
    ri = read_instant(res.decisions, now_ms)
    bshare = res.boost_before_exit / res.boost_known if res.boost_known else None
    return {"attempts": attempts, "counted": res.counts["counted"], "excluded": n_exc, "excluded_share": share, "not_decidable": share > EXCLUDED_CAP,
            "excluded_by_reason": {r: res.counts[f"excluded_{r}"] for r in EXCLUSION_REASONS if res.counts[f"excluded_{r}"]},
            "open": res.counts["open"], "age_clamped_to_zero": res.counts["age_clamped_to_zero"],
            "trigger_age_exclusions": res.counts["excluded_trigger_age"], "no_block_time_exclusions": res.counts["excluded_no_block_time"],
            "feed_delay_ms_p50": _pct(res.ages, 0.5), "feed_delay_ms_p90": _pct(res.ages, 0.9), "feed_delay_n": len(res.ages), "derived_last_deciding_leg": res.counts["derived_last_deciding_leg"], "bx10_error_priced_v1": res.counts["bx10_error_priced_v1"], "skipped_before_clock": res.counts["skipped_before_clock"],
            "skipped_after_window": res.counts["skipped_after_window"], "duplicate_pool": res.counts["duplicate_pool"],
            "sealed_decision_only": res.counts["sealed_decision_only"], "refused_dirs": dict(res.refused),
            "restarts": res.restarts, "downtime_ms": res.downtime_ms, "read_instant_ms": ri, "read_reached": ri is not None,
            "boost_before_exit_share": bshare, "boost_known": res.boost_known,
            "boost_not_proposed_for_live": bool(bshare is not None and bshare > BOOST_BEFORE_EXIT_CAP)}


# ---------------------------------------------------------------------------------------------------------------- ledger
def _strict(row: dict) -> str:
    return json.dumps(row, separators=(",", ":"), allow_nan=False, sort_keys=False)


def existing_ids(bot_dir: Path, prefix: str, counts: collections.Counter) -> set[str]:
    """Ids in the bot's day files. An unparsable line is COUNTED (bad_ledger_lines); the caller stops before writing if there is one."""
    ids: set[str] = set()
    key = "trade_id" if prefix == "trades" else "exclusion_id"
    for f in sorted(bot_dir.glob(f"{prefix}-????-??-??.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        ids.add(json.loads(line)[key])
                    except (ValueError, KeyError):
                        counts["bad_ledger_lines"] += 1
    return ids


def write_ledger(bot: str, rows: Sequence[dict], exclusions: Sequence[dict], ledger_root: Path, counts: collections.Counter, dry_run: bool = False) -> None:
    """Append what is not yet there. Write-once: an existing id is never touched. A non-finite number is refused (strict JSON). A torn line in an existing file stops the
    write for this book (counted), so a trade is never appended twice after a tear."""
    bot_dir = ledger_root / bot
    if not dry_run:
        bot_dir.mkdir(parents=True, exist_ok=True)
    lock = None if dry_run else open(bot_dir / ".lock", "a")
    try:
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        for prefix, items, key in (("trades", rows, "trade_id"), ("exclusions", exclusions, "exclusion_id")):
            before_bad = counts["bad_ledger_lines"]
            seen = existing_ids(bot_dir, prefix, counts) if bot_dir.exists() else set()
            if counts["bad_ledger_lines"] > before_bad:
                counts["write_stopped_bad_ledger"] += 1
                continue
            for r in sorted(items, key=lambda x: (x["decision_ms"], x[key])):
                if r[key] in seen:
                    counts[f"duplicate_{prefix}"] += 1
                    continue
                line = _strict(r)
                seen.add(r[key])
                counts[f"written_{prefix}"] += 1
                if dry_run:
                    continue
                with open(bot_dir / f"{prefix}-{utc_day(r['decision_ms'])}.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
    finally:
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


# ---------------------------------------------------------------------------------------------------------------- the refit at the read
def refit_cells(rows: Sequence[dict]) -> dict[str, dict[str, Any]]:
    """The deciding pressure intercept, per (stake, latency leg) cell of ONE book's counted rows: the intercept with mean p over the cell's filled sends = 0.289 (cost.press_intercept).
    Given rows of one book only. Returns {cell: {intercept, n, n_filled}}; the cell's press P&L is cost.press_leg(nofail, state, press_p(intercept, ssb, nb))."""
    cells: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        for _, sfx, _ in LEGS:
            leg = r["legs"].get(f"press_{sfx}") or {}
            if leg.get("net_lamports") is not None:
                cells[f"{r['stake_sol']:.2f}|{sfx}"].append(leg)
    out = {}
    for cell, legs in sorted(cells.items()):
        filled = [cost.is_filled(l["state"]) for l in legs]
        c = cost.press_intercept([l["ssb"] for l in legs], [l["nb_lamports"] for l in legs], filled)
        out[cell] = {"intercept": c, "n": len(legs), "n_filled": sum(filled)}
    return out


# ---------------------------------------------------------------------------------------------------------------- CLI
def default_arena_root() -> str | None:
    """$HOME/data/arena on mal-fast-0 (where the H5 book runs); nowhere else: off mal-fast-0 the flag is required, so a run on research-0 cannot silently read the wrong root."""
    return os.path.join(os.path.expanduser("~"), *h5.ARENA_HOME_SUBDIR) if socket.gethostname() == "mal-fast-0" else None


def _arena_root(path: str | None) -> Path:
    if not path:
        path = default_arena_root()
    if not path:
        raise ValueError("--arena-root is required (it defaults to $HOME/data/arena only on mal-fast-0)")
    real = os.path.realpath(path)
    if real not in (os.path.realpath(h5.ARENA_ROOT), os.path.realpath(os.path.join(os.path.expanduser("~"), *h5.ARENA_HOME_SUBDIR))):
        raise ValueError(f"--arena-root {path!r} must be {h5.ARENA_ROOT} or $HOME/{'/'.join(h5.ARENA_HOME_SUBDIR)}")
    return Path(real)


def default_shadow_dirs(root: str) -> list[str]:
    return sorted(glob.glob(os.path.join(root, "h5-shadow-*")))


def cmd_register(a: argparse.Namespace) -> int:
    root = _arena_root(a.arena_root)
    out = register_books(root / "bots.json", frozen_sha=a.frozen_sha, sockets=a.sockets, endpoints=a.endpoint or ["default"], host=a.host or os.uname().nodename,
                         clock_start_ms=a.clock_start_ms, stake=a.stake)
    print(json.dumps({"registered": sorted(out), "registry": str(root / "bots.json")}, sort_keys=True))
    return 0


def cmd_halt(a: argparse.Namespace) -> int:
    root = _arena_root(a.arena_root)
    bots = set_window_end(root / "bots.json", int(a.at_ms))
    print(json.dumps({"window_end_ms": {b: e.get("window_end_ms") for b, e in sorted(bots.items()) if e.get("window_end_ms") is not None}}, sort_keys=True))
    return 0


def cmd_score(a: argparse.Namespace) -> int:
    counts: collections.Counter = collections.Counter()
    root = _arena_root(a.arena_root)
    ledger_root = Path(a.ledger_root) if a.ledger_root else root / "ledger"
    if ".." in ledger_root.parts or not (os.path.realpath(ledger_root) + "/").startswith(str(root) + "/"):
        raise ValueError(f"--ledger-root {a.ledger_root!r} must be under {root}")
    registry = load_registry(root / "bots.json")
    dirs = [h5.check_fresh_out_dir(d) for d in (a.shadow_dir if a.shadow_dir is not None else default_shadow_dirs(str(root)))]
    now_ms = int(a.now_ms) if a.now_ms is not None else int(time.time() * 1000)
    loaded = [ShadowDir(d, counts) for d in dirs]
    report: dict[str, Any] = {"schema": SCHEMA, "stack": STACK, "shadow_dirs": [str(d) for d in dirs], "dry_run": bool(a.dry_run), "books": {}}
    for bot in BOTS:
        entry = registry.get(bot)
        if entry is None:
            report["books"][bot] = {"registered": False}
            continue
        res = score_book(bot, entry, loaded)
        write_ledger(bot, res.rows, res.exclusions, ledger_root, counts, dry_run=a.dry_run)
        summ = summarize(bot, res, now_ms)
        if summ["read_instant_ms"] is not None:
            record_read_instant(bot, summ["read_instant_ms"], res, ledger_root, counts, dry_run=a.dry_run)
        report["books"][bot] = {"registered": True, **summ, "rows_scored": len(res.rows)}
    report["counts"] = dict(sorted(counts.items()))
    print(json.dumps(report, sort_keys=True))
    return 0


READ_INSTANT_FILE = "read-instant.json"


def record_read_instant(bot: str, ri: int, res: BookResult, ledger_root: Path, counts: collections.Counter, dry_run: bool = False) -> None:
    """The ONE read instant of the book, computed by score_book from every attempt (excluded and open ones included, DEC-031 s2) and recorded for `refit`. The instant itself is
    never changed once written (a different later value is counted as read_instant_conflict and the old one kept); `open_before_read` and `attempts_before` are refreshed
    on each run, so a refit can tell that every attempt before the boundary has exited and that the ledger holds all of them."""
    before = [d for d in res.decisions if d < ri]
    doc = {"bot_id": bot, "read_instant_ms": int(ri), "attempts_before": len(before), "dates": len({d // DAY_MS for d in before}),
           "open_before_read": sum(1 for d in res.open_decisions if d < ri)}
    path = ledger_root / bot / READ_INSTANT_FILE
    if path.exists():
        old = json.loads(path.read_text())
        if int(old["read_instant_ms"]) != int(ri):
            counts["read_instant_conflict"] += 1
            return
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, doc)


def ledger_attempts_before(bot_dir: Path, ri: int) -> tuple[list[dict], int]:
    """(trade rows decided before ri, the number of distinct attempts before ri in trades plus exclusions): one attempt per pool."""
    rows = [json.loads(l) for f in sorted(bot_dir.glob("trades-????-??-??.jsonl")) for l in f.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r["decision_ms"] < ri]
    pools = {r["pool"] for r in rows}
    for f in sorted(bot_dir.glob("exclusions-????-??-??.jsonl")):
        for l in f.read_text().splitlines():
            if l.strip():
                e = json.loads(l)
                if e["decision_ms"] < ri:
                    pools.add(e["pool"])
    return rows, len(pools)


def cmd_refit(a: argparse.Namespace) -> int:
    root = _arena_root(a.arena_root)
    registry = load_registry(root / "bots.json")
    ledger_root = root / "ledger"
    now_ms = int(a.now_ms) if a.now_ms is not None else int(time.time() * 1000)
    out: dict[str, Any] = {}
    for bot in BOTS:
        if bot not in registry:
            continue
        bot_dir = ledger_root / bot
        rec = bot_dir / READ_INSTANT_FILE
        if not rec.exists():
            out[bot] = {"refused": "read instant not recorded (score has not reached it)"}
            continue
        doc = json.loads(rec.read_text())
        ri = int(doc["read_instant_ms"])  # the instant score_book computed, from every attempt: the refit never computes its own
        if now_ms < ri:
            out[bot] = {"refused": "read instant not reached"}
            continue
        if doc["open_before_read"]:
            out[bot] = {"refused": "attempts decided before the read instant are still open"}
            continue
        rows, n_attempts = ledger_attempts_before(bot_dir, ri)
        if n_attempts != doc["attempts_before"]:
            out[bot] = {"refused": "the ledger does not hold every attempt decided before the read instant (score again)"}
            continue
        target = bot_dir / f"refit-{utc_day(ri)}.json"
        if target.exists():
            out[bot] = {"refused": f"{target.name} exists: the refit is write-once"}
            continue
        cells = refit_cells(rows)
        try:
            _write_once_json(target, {"bot_id": bot, "read_instant_ms": ri, "attempts_before": n_attempts, "trades_pools": len({r["pool"] for r in rows}), "cells": cells})
        except FileExistsError:
            out[bot] = {"refused": f"{target.name} exists: the refit is write-once"}
            continue
        out[bot] = {"read_instant_ms": ri, "attempts_before": n_attempts, "cells": len(cells)}
    print(json.dumps(out, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="arena H5 scorer (counts only)")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("register", help="write both books' lines in bots.json before the clock")
    r.add_argument("--arena-root", required=True)
    r.add_argument("--frozen-sha", required=True)
    r.add_argument("--sockets", type=int, required=True)
    r.add_argument("--endpoint", action="append", default=None)
    r.add_argument("--host", default=None)
    r.add_argument("--clock-start-ms", type=int, default=None)
    r.add_argument("--stake", default=DECIDING_STAKE)
    h = sub.add_parser("halt", help="A3 HALT flag: end both books' counted windows at --at-ms")
    h.add_argument("--arena-root", required=True)
    h.add_argument("--at-ms", type=int, required=True)
    s = sub.add_parser("score", help="price the shadow dirs into the ledger (default)")
    s.add_argument("--arena-root", default=None, help="required, except on mal-fast-0 where it defaults to $HOME/data/arena")
    s.add_argument("--shadow-dir", action="append", default=None)
    s.add_argument("--ledger-root", default=None)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--now-ms", type=int, default=None)
    f = sub.add_parser("refit", help="write the per-cell deciding intercepts; refuses before the read instant")
    f.add_argument("--arena-root", default=None, help="required, except on mal-fast-0 where it defaults to $HOME/data/arena")
    f.add_argument("--now-ms", type=int, default=None)
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("register", "halt", "score", "refit", "-h", "--help"):
        argv = ["score", *argv]
    a = ap.parse_args(argv)
    try:
        return {"register": cmd_register, "halt": cmd_halt, "score": cmd_score, "refit": cmd_refit}[a.cmd](a)
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
