"""hv4_c0_parity: parity, twin and tie-out wiring of the HV4 live C0 adapter (PR 3). KEYLESS: no RPC, no key, no wallet, no HV4 ledger.

Glue only. The checks themselves are the arena canary's, unchanged:
  * `tools.h5_arena_gates.ParityRecorder` writes the trips and attempts files in the schema `tools.arena_live_parity` validates, from the executor's own
    `buy` / `sell` fill rows (landed buy fee, landed sell fee plus earlier failed sells = the fees actually sent, escalated resends included);
  * `tools.h5_arena_gates.LiveGates` runs `tools.arena_live_parity` (P1 to P7; rc 0 or no buy; its STOP-file contract) and the daily tie-out gate, and checks
    the twin writer's run record against the pins (`hv4_live_c0.twin_pins()`);
  * `tools.arena_twin_writer` is the twin: see `tools/hv4_c0_twin.py` for the pinned wrapper.
This module adds what is HV4-specific: the gate construction with the C0 pins, the parity state (`parity_state`: a stamp only for a read that succeeded
just now), the exit bound, the stake check (live spend must equal the order's S) and the path config. The Bridge calls these only for profile hv4_c0.
Nothing here says a book makes money. It is a build, not edge evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from tools import hv4_live_c0 as c0
from tools.h5_arena_gates import HARD_ARENA_HALTS, LiveGates, ParityRecorder, Runner, twin_run_stamp

PATH_KEYS = ("parity_dir", "twins_file", "tieout_dir")
CHAIN_FRESH_MS = 5_000  # the decision_chain stamp needs a verified read this young; a stalled reader loses the stamp (P7) instead of being stamped `now`
RC_HARD = 11  # tools.arena_live_parity: a hard halt (P4); cleared only by the manager's written review and the owner
RC_SOFT = 10
RC_STOP_UNWRITABLE = 3  # tools.arena_live_parity EXIT_STOP_UNWRITABLE: a STOP was required (soft, hard, bad input or internal error) but could not be written
# The parity and tie-out tools write their STOP here, inside parity_dir, which the hv4_c0 unit owns and can write. The bridge's own stop_file in live is the
# wallet-wide /var/lib/mal-live/STOP, which the hardened unit cannot write: passing it to the tools made every halt come back as rc 3, which hid the hard/soft
# class and latched nothing. The bridge treats this file as STOP (no new buys; exits continue) for as long as it exists, across restarts; only the manager removes it.
PARITY_STOP_NAME = "STOP"
PARITY_STOP_UNWRITABLE_LATCH = "parity_stop_unwritable"  # rc 3: the class is unknown, so it is held as a hard latch (fail closed); the manager clears it


def parity_stop_path(parity_dir: str) -> str:
    return str(Path(parity_dir) / PARITY_STOP_NAME)
# Latches that the Bridge treats as unresolved hard halts for the parity state (the tool's `unresolved_hard_halts`).
HARD_LATCHES = frozenset({"parity_hard_halt", PARITY_STOP_UNWRITABLE_LATCH, "landed_buy_unreadable", "stake_mismatch", "twin_pin_mismatch", "stuck_sell", "chain_break", "sealed_path", "read_record",
                          "frozen_sha_mismatch", "tieout_mismatch"}) | HARD_ARENA_HALTS


def paths_refusal(cfg: Mapping[str, Any]) -> str | None:
    """A LIVE hv4_c0 start needs the three parity paths (the trips/attempts directory, the twins file, the tie-out results directory). Absolute paths only."""
    for k in PATH_KEYS:
        v = cfg.get(k)
        if not isinstance(v, str) or not v or not Path(v).is_absolute():
            return f"parity_path_missing:{k}"
    return None


def exit_bound_ms(decision_ms: int, max_hold_ms: int, backstop_extra_ms: int) -> int:
    """The latest instant by which the exit should have landed: the book's own time stop (max hold + the bridge's backstop margin), from the decision. Derived from
    the config the bridge already holds, never from the parity files."""
    return int(decision_ms) + int(max_hold_ms) + int(backstop_extra_ms)


def build_gates(parity_dir: str, twins_file: str, tieout_dir: str, stop_file: str, state_builder: Callable[[int], dict[str, Any]],
                runner: Runner | None = None, interval_s: float | None = None) -> LiveGates:
    """LiveGates with the C0 twin pins. The recorder writes `trips.jsonl` / `attempts.jsonl` under `parity_dir`."""
    d = Path(parity_dir)
    return LiveGates(d, ParityRecorder(d), twins_file, tieout_dir, stop_file, state_builder, interval_s=interval_s, runner=runner, twin_pins=c0.twin_pins())


def parity_state(now_ms: int, *, loss_ok: bool, balance_ms: int | None, twins_file: str, trip_ids: Iterable[str], chain_ok: bool, control_ok: bool,
                 hard_halts: Iterable[str]) -> dict[str, Any]:
    """The parity tool's state.json. A name is stamped only for a read that succeeded; a missing stamp is P7 (no buy). `twin` is the twin WRITER's own run time
    (never `now`): a stalled writer goes stale."""
    at: dict[str, int] = {"parity": now_ms}
    if loss_ok:
        at["loss"] = now_ms
    if balance_ms is not None:
        at["balance"] = balance_ms
    stamp = twin_run_stamp(twins_file, list(trip_ids))
    if stamp is not None:
        at["twin"] = stamp
    if chain_ok:
        at["decision_chain"] = now_ms
    if control_ok:
        at["control_files"] = now_ms
    return {"at_ms": at, "unresolved_hard_halts": sorted(HARD_LATCHES & set(hard_halts))}


def stake_problem(kw: Mapping[str, Any], order_stake_lamports: int) -> str | None:
    """A landed buy must have spent exactly the order's S (live stake = S = twin stake). None when equal or when the buy did not land / has no readable spend
    (the recorder handles an unreadable landed buy itself)."""
    if not kw.get("landed"):
        return None
    spend = kw.get("spend_lamports")
    if isinstance(spend, bool) or not isinstance(spend, int):
        return None
    return None if spend == int(order_stake_lamports) else f"stake_mismatch:spent_{spend}_order_{order_stake_lamports}"
