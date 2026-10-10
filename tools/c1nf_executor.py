"""C1-NF live executor (frozen C1 cascade + the h_top1 <= 0.5 cap; JUDGE-4 section 3.3, EXP-025; DEC-026). A subclass of the H5 executor.

DRY RUN is the default: it builds and simulates transactions with a public payer and never signs, sends or touches a key. Live needs ALL of:
config "mode": "live", the --live flag, a LIVE_OK file at /etc/mal-c1nf/LIVE_OK that Helm creates (root:root 0644), EXP/EXP-025-c1nf-part1-prereg.md
in the deployed tree, an explicit end instant (never later than C1NF_END_MAX_MS, 2026-10-24T00:30Z), the state dir /var/lib/mal-live/c1nf, and the
key from the systemd credential "c1nf-wallet" (never a path).

This is a build, not a claim. It trades nothing by itself and is not gate evidence. DEC-026 section 11 lists what must happen before the first send;
LIVE_OK is the manager's lever for that, this code cannot check it.

What is reused from tools.h5_executor (nothing is edited there; this module subclasses it and calls it): the send / confirm / ATA-close core, the
sell ladder (supersede, escalate, emergency, deadline), the budget stops with the worst-case exposure test, the 35% wallet cap on the total stop
with its tier-start baseline, STOP / HALT (state dir and wallet-wide), the latched halts, the counters and their anti-reset guard, the seal guard
(`_seal_reason`, fail closed), --mark-closed and --clear-halt. What differs (DEC-026 sections 5 to 7, 9):
  limits    Its own code-constant table (C1NF_TIERS). T1 is the canary and the LOWEST tier: stake ceiling 0.10 SOL (the live config lowers it to
            0.05), 2 open, 30 attempts a day, daily stop 0.20 SOL, total stop 0.30 SOL capped at 35% of the wallet at tier start (0.175 at 0.5 SOL).
            505,000 lamports priority on the buy and the sell. Config can only lower. T2 is inactive (no owner line): a TIER file saying T2 runs T1.
  files     LIVE_OK and TIER under /etc/mal-c1nf; STOP / HALT under /var/lib/mal-live/c1nf (and the wallet-wide /var/lib/mal-live/{STOP,HALT}).
  entry     H5 buys on a trigger inside 0-300 s of a pool's first print, Q <= 40 SOL, one buy per mint ever, synthetic pools refused. C1-NF buys a pick
            at any pool age, any Q, re-enters a mint 60 s after its exit (never two positions in one mint), and trades synthetic-migration pools
            (EXP-025 Amendment 2): no refusal by class, and the class is never read from a pick nor written on any record.
  guard     The 1.15 x spot guard is measured against the pick's decision-time q_lamports and base_reserve. A pick without them is refused (fail closed).
  exit      C1-NF anchors on its OWN buy landing: landing + 300 s + 0.55 s. The plan made at the send is provisional (anchored on the send); once the
            buy confirms it is re-anchored on the landed slot, mapped to wall time through the executor's own getSlot history. Escalation at
            landing + 315 s, the emergency market sell at landing + 370 s (the plan's `deadline`).
  halts     No BOOST halts. The fill-selection monitor (below), the landing p50 rule (rolling 20 landed buys, p50 above 1.9 s), H5's out-of-rule
            entry (landing more than 5 s after SD_slot), a pick or heartbeat whose model sha256 is not the pinned one (C1NF_MODEL_SHA256), and
            stuck_position at landing + 600 s (DEC-026 section 7 rule 4; H5 latches it at its own deadline, which here is the 370 s emergency
            sell, so that latch is deferred to 600 s). Exit timing is an ALERT, not a halt (DEC-026 section 7 rule 5): more than 10% of the
            last 20 landed sells past landing + 305 s.
  balance   H5's balance guard with the per-exit reserve raised from 1,000,000 to one escalated send + base fee (1,015,000; c1nf_exit_reserve).
  wallet    Live refuses to start when the credential holds H5's wallet (H5_WALLET_PUBKEY) or anything but the pinned C1-NF wallet
            (C1NF_WALLET_PUBKEY; unset until Helm gives the public key, so live cannot start before that line is a reviewed code change).
  gates     EXP-025 Part 1 in the tree (not EXP-024).
  seal      H5's boolean pick oracle from 2026-10-16T01Z, asked about EVERY mint (wider than EXP-025 section 5.3's keying, fail safe); missing,
            erroring, undecided (None) or non-boolean means no buy. Seal refusals are a count only, never recorded per mint, and the seal is
            checked FIRST, before any refusal that writes a per-mint row. The config's `pick_file` is read through StaleCheckedPickOracle (#509's
            row format, its 60 s heartbeat staleness: DEC-026 section 9.1). H5's seal also needs the FINAL marker <state_dir>/FINAL_WRITTEN
            (live: /var/lib/mal-live/c1nf/FINAL_WRITTEN); without it every buy in the seal window is refused (fail closed). Who writes it, and
            when, is a runbook item (DEC-026 section 11 item 11), not this code.

Input: the C1-NF shadow's output directory (`intents_file`, #503's tools/c1nf_shadow.py JsonlSink), three hourly streams. For each, the newest
hour is followed and the previous one drained on a roll:
  c1nf-picks-<hour>.jsonl     type "c1nf_pick": PICK_SCHEMA below (#503's PICK_FIELDS). Refused if malformed, off the decision grid, not on the
                              canonical pool, without the decision-time state, before 2026-10-10T00Z (`pre_window`, a count only: #503's
                              executor_refusal), or stale (more than 3 s of chain age since SD_slot).
  c1nf-events-<hour>.jsonl    type "c1nf_heartbeat" (feed freshness: older than 150 s, every pick is refused feed_stale) and "c1nf_gap" (buys
                              held for gap_hold_ms, and a pick whose decision minute overlaps the gap's slot range is refused feed_gap: DEC-026
                              section 7 rule 7). "c1nf_start" / "c1nf_stop" are ignored.
  c1nf-outcomes-<hour>.jsonl  type "c1nf_outcome": the monitor's outcome_pct is OUTCOME_SCHEMA's leg (see outcome_pct_of). Offset persisted
                              after the rows are processed; c1nf-extra.json has an anti-reset guard (extra_reset_problem).
  Anything else is ignored.

Fill-selection monitor. Every pick that reaches the executor is ledgered `pick_status` filled or unfilled. When outcomes arrive, the last 30 monitored
picks WITH outcomes are compared: if the mean paper outcome of the unfilled exceeds the mean of the filled by more than 3 percentage points, with at
least 8 unfilled (and at least one filled), the halt `fill_selection_adverse` latches (new buys stop; cleared only by --clear-halt, and a latch needs
fresh evidence afterwards). Not monitored, because they are not fill failures: book refusals (a mint already held, the 60 s cooldown, a duplicate
pick, the open cap), and refusals by a stop (STOP / HALT / a latched halt / a budget stop / LIVE_OK / the balance guard).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from tools import h5_executor as h5
from tools import probe_executor as pe
from tools import probe_live as pl
from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx

RULE_ID = "C1NF-v1"
BOOK = "c1nf_v1"
DRYRUN, LIVE = h5.DRYRUN, h5.LIVE
EXP025_PART1 = "EXP/EXP-025-c1nf-part1-prereg.md"  # live is honoured only if this is in the deployed tree
# DEC-026 section 5: the C1-NF profile's own files. None of them is H5's; the H5 unit never reads them and this one never reads H5's.
LIVE_OK_PATH = Path("/etc/mal-c1nf/LIVE_OK")  # root:root 0644 in a root-owned directory: the same checks as H5's (h5_executor.root_file_problem)
TIER_FILE_PATH = Path("/etc/mal-c1nf/TIER")  # same checks; content exactly T1. Missing, unsafe, unreadable, invalid or an inactive tier means T1
LIVE_STATE_DIR = Path("/var/lib/mal-live/c1nf")  # live refuses any other state_dir; STOP and HALT are <state_dir>/{STOP,HALT}
CREDENTIAL_NAME = "c1nf-wallet"  # LoadCredential=c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json (Helm's install); read only from $CREDENTIALS_DIRECTORY
# The shadow's three hourly streams (#503 tools/c1nf_shadow.py JsonlSink.PREFIX and _FILE_RE). Code constants: the record contract is pinned here.
PICKS_GLOB = "c1nf-picks-????-??-??T??.jsonl"
EVENTS_GLOB = "c1nf-events-????-??-??T??.jsonl"
OUTCOMES_GLOB = "c1nf-outcomes-????-??-??T??.jsonl"
HEARTBEAT_TYPE, GAP_TYPE = "c1nf_heartbeat", "c1nf_gap"  # #503 EVENT_TYPES
PICK_WINDOW_START_MS = 1791590400000  # 2026-10-10T00:00Z = #503 OUTCOME_START_MS (DEC-026 section 8): an earlier decision is never acted on
FEED_HEARTBEAT_MAX_AGE_MS = 150_000  # DEC-026 section 7 rule 7. Config may only lower it; 0 or absent means this (never "off")
GAP_LOOKBACK_S = 60.0  # a c1nf_gap whose slot range overlaps [SD_slot - 60 s, SD_slot] refuses the pick: the minute's features may miss prints
GAPS_KEEP = 200
# The wallets (DEC-026 section 5). Public keys only. H5's wallet is never C1-NF's; C1-NF's own public key is pinned when Helm gives it.
H5_WALLET_PUBKEY = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # DEC-024 / DEC-019 probe wallet
C1NF_WALLET_PUBKEY: str | None = None  # <HELM FILLS> (DEC-026 section 5 table). None: live refuses to start
# The pinned model (DEC-026 section 11 item 12, 2026-10-10): ARTIFACTS/c1nf_model/c1nf_model_exp36.txt, built by tools/c1nf_model_pin.py from the frozen
# EXP-025 recipe on the 36 exploration days only (manifest.json; tools/test_c1nf_model_pin.py checks this set equals the manifest). Empty: live refuses.
C1NF_MODEL_SHA256: frozenset[str] = frozenset({"faf8a01f5fb5019a3c26affc7de49f47270e7b6717eea34767bfcdc759399478"})
MODEL_HALT = "model_sha_mismatch"  # DEC-026 section 7 rule 7: a pick or heartbeat naming another model is a halt
SEAL_ORACLE_STALE_S = 60.0  # DEC-026 section 9.1 / #509 STALE_S_DEFAULT
SEAL_ORACLE_SKEW_S = 5.0  # #509 CLOCK_SKEW_OK_S: a heartbeat this far in the future is still fresh; further is a clock fault (stale)
C1NF_END_MAX_MS = 1792801800000  # 2026-10-24T00:30:00Z, the owner's end (DEC-026 O-4). Config end_ms may be earlier, never later; an extension is a code change

# --- the shadow's records ------------------------------------------------------------------------------------------------
PICK_TYPE, OUTCOME_TYPE = "c1nf_pick", "c1nf_outcome"
PICK_SCHEMA = {
    "type": "c1nf_pick",
    "required": {
        "mint": "base58 pubkey of the base token",
        "pool": "the canonical PumpSwap pool PDA of the mint (tx.canonical_pool); anything else is refused",
        "decision_T_ms": "int, epoch ms of the decision minute: a whole UTC minute (the rule's grid)",
        "SD_slot": "int, the decision slot: the first slot whose block_time >= decision_T_ms",
        "pred": "float, the stage-2 prediction; the rule buys only pred > 0.02",
        "h_top1": "float in [0, 1], the top-holder share; the rule drops h_top1 > 0.5 or NaN",
        "stage1": "literal true: the LAYA stage-1 filter passed",
        "feature_hash": "str, 8 to 128 chars of [0-9A-Za-z_.:-]: the hash of the feature row (traceability only)",
        "q_lamports": "int > 0, pool quote + V at the decision (state after the last print before SD_slot): the reference of the 1.15 x spot guard",
        "base_reserve": "int > 0, raw base reserve at the same state. Without both a pick gets no buy (DEC-026 section 6: fail closed)",
        "v_lamports": "int > 0, that print's V; q_lamports must be above it (#503 validate_pick)",
        "state_slot": "int > 0, the slot of that print, below SD_slot",
        "model_sha": "64 lowercase hex: the model's sha256. Not in C1NF_MODEL_SHA256 (when pinned): the halt model_sha_mismatch",
    },
    "optional": {
        "t_ms": "int, the shadow's envelope: its clock when it wrote the pick (ledgered as t_emit_ms)",
    },
    "never_read": "any other field, the synthetic class among them: only the fields above are read, so no class reaches any record this writes",
}
# The monitor's outcome (pinned): the primary 1.3 s entry leg, the END bound, its `canary` twin (0.05 SOL stake, guarded on the pick's state,
# DEC-026 O-3 / section 6), net of 2 x 505,000 lamports (pnl_505k, rent excluded), in percent of that twin's stake. A leg that is missing or
# incomplete, or a twin without a positive stake, gives no outcome (a count, `outcomes_unpriced`; never a per-mint row).
OUTCOME_LEG, OUTCOME_BOUND, OUTCOME_VARIANT = "1.3", "end", "pnl_505k"
OUTCOME_SCHEMA = {"type": "c1nf_outcome", "required": {"mint": "str", "decision_T_ms": "int, the pick's",
                                                         "legs": f"legs[{OUTCOME_LEG!r}][{OUTCOME_BOUND!r}]['canary'] = {{stake_lamports, {OUTCOME_VARIANT}}}"}}

# --- the frozen rule's own numbers (code constants, never config) ---------------------------------------------------------
PRED_MIN = 0.02  # BUY when the prediction > 0.02
H_TOP1_MAX = 0.5  # drop the row if h_top1 > 0.5
DECISION_GRID_MS = 60_000
HOLD_S = 300.0  # exit deadline = the buy's landing + 300 s ...
EXIT_LAND_OFFSET_S = 0.55  # ... and the sell lands 0.55 s after it
ESCALATE_S = 315.0  # escalated ladder level from landing + 315 s
DEADLINE_S = 370.0  # emergency market sell from landing + 370 s (the plan's `deadline`: an exit stage, NOT the stuck line)
STUCK_S = 600.0  # DEC-026 section 7 rule 4: a position not closed by landing + 600 s latches stuck_position (C1NFExecutor._stuck_tick)
STUCK_HALT = "stuck_position"  # H5's halt name, so the watchdog and --clear-halt treat it as H5's
H5_DEADLINE_STUCK_WHY = "not_sold_by_deadline"  # H5's exit_tick latches STUCK_HALT with this `why` at the plan's deadline: C1-NF defers it
LATE_AFTER_EXIT_S = 5.0  # a sell landing later than landing + 300 + 5 s is late (DEC-026 section 7 rule 5: an ALERT, never a halt)
LATE_SELL_WINDOW = 20  # ... over the last 20 landed sells ...
LATE_SELL_MIN_N = 10  # ... once at least 10 have landed ...
LATE_SELL_ALERT_FRAC = 0.10  # ... more than 10% late alerts `exit_late_share_gt_10pct` (once per crossing; no latch, buys go on)
COOLDOWN_MS = 60_000  # a mint may be re-entered when the pick's decision time >= the previous exit + 60 s
MAX_PICK_AGE_S = 3.0  # a pick older than this in chain terms (slots since SD_slot x measured sps) is refused; config may only lower it
WALL_AGE_BACKSTOP_FACTOR = 2.0  # ... and one older than this many times that by the wall clock since decision_T_ms (gross inconsistency)
ENTRY_LATE_S = h5.ENTRY_LATE_S  # a buy landing later than this after SD_slot is out of the rule: immediate exit and the halt out_of_rule_entry
LANDING_P50_MAX_S = 1.9  # DEC-026 section 7 rule 3: the landing p50 (SD_slot to landed slot) above the binding 1.9 s ...
LANDING_P50_WINDOW = 20  # ... over the first 20 landed buys, then a rolling 20, latches landing_p50_gt_1_9s
LANDING_P50_HALT = "landing_p50_gt_1_9s"

# --- fill-selection monitor ---------------------------------------------------------------------------------------------------
FILL_SEL_WINDOW = 30  # the last picks WITH outcomes
FILL_SEL_MIN_UNFILLED = 8
FILL_SEL_GAP_PP = 3.0  # unfilled mean minus filled mean, percentage points of stake, strictly above
FILL_SEL_HALT = "fill_selection_adverse"
PICKS_KEEP = 400
BOOK_REASONS = frozenset({"duplicate_pick", "already_held", "cooldown", "max_open"})  # DEC-026 rule 1: held, cooldown, duplicate, open cap
STOP_REASONS = frozenset({"halt_file", "stop_file", "live_ok_missing", "live_ok_unsafe", "exp025_part1_missing", "clock_backwards", "total_loss_stop",
                          "daily_loss_stop", "max_trades_day", "max_attempts", "max_days", "end_instant", "balance_floor", "balance_unreadable",
                          "tier_state_legacy", MODEL_HALT, "send_blocked"})

# --- limits: code maxima, config can only lower (the wallet floor: config can only raise) ---------------------------------
# DEC-026 section 6. T1 is the canary and the lowest tier: there is no 0.02 SOL tier. The stake CEILING is 0.10 SOL; the reviewed live config lowers
# it to 0.05 (config may only lower). The total stop is also capped at h5.TIER_WALLET_FRAC (35%) of the wallet at tier start: 0.175 SOL at 0.5 SOL.
C1NF_TIERS: dict[str, dict[str, int]] = {
    "T1": {"stake_lamports": 100_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 200_000_000, "total_loss_lamports": 300_000_000},
}
C1NF_LOWEST_TIER = "T1"
C1NF_INACTIVE_TIERS = ("T2",)  # named by DEC-026 section 10, inactive until the owner's dated line sets its numbers (a reviewed code change)
C1NF_DEFAULT = {
    **C1NF_TIERS["T1"], "max_attempts": 450, "max_days": 15, "buy_priority_lamports": 505_000, "sell_priority_lamports": 505_000,
    "escalated_priority_lamports": 1_010_000, "entry_tolerance_bps": 1500,
}  # 450 = 30 a day x 15 days and 15 days cover 10-09 to 10-24: end_ms and the total stop bind first. Escalated sells pay 2 x 505,000 (manager's term)
C1NF_MAX = dict(C1NF_DEFAULT)  # the defaults ARE the maxima, as in H5: loosening is a reviewed code change
assert all(C1NF_DEFAULT[k] == v for k, v in C1NF_TIERS[C1NF_LOWEST_TIER].items())
NOT_CONFIGURABLE = ("trigger_variant", "exit_land_offset_s", "deadline_s", "max_trigger_age_ms", "late_sell_min_n", "sell_priority_lamports",
                    "escalated_priority_lamports", "intents_glob")  # H5's knobs for another rule, this rule's constants, the pinned stream names
FORBIDDEN_TRUTHY = ("jito_enabled",)

_H5_PRECHECK = h5.h5_precheck
_H5_BUILD_PROBE_CFG = h5.build_probe_cfg


def _c1nf_precheck(probe_cfg: dict[str, Any], _mode: str) -> list[str]:
    """H5's state-dir precheck without its probe cross-check: the C1-NF wallet is a separate wallet, so the decommissioned probe's open positions
    are not its business. (h5_precheck only does that cross-check for mode == live, so it is run as a dry-run precheck here.)"""
    return _H5_PRECHECK(probe_cfg, DRYRUN)


def _c1nf_build_probe_cfg(cfg: dict[str, Any], limits: h5.H5Limits, run_mode: str) -> dict[str, Any]:
    pc = _H5_BUILD_PROBE_CFG(cfg, limits, run_mode)
    pc["book"] = BOOK
    return pc


_TABLE_PATCHES = {"H5_DEFAULT": lambda: dict(C1NF_DEFAULT), "H5_MAX": lambda: dict(C1NF_MAX), "TIERS": lambda: {k: dict(v) for k, v in C1NF_TIERS.items()}}
_INIT_PATCHES = {"RULE_ID": lambda: RULE_ID, "LIVE_OK_PATH": lambda: LIVE_OK_PATH, "TIER_FILE_PATH": lambda: TIER_FILE_PATH,
                 "h5_precheck": lambda: _c1nf_precheck, "build_probe_cfg": lambda: _c1nf_build_probe_cfg}
SCOPED_NAMES = tuple(_TABLE_PATCHES) + tuple(_INIT_PATCHES)


@contextlib.contextmanager
def _patched(patches: dict[str, Callable[[], Any]]):
    saved = {k: getattr(h5, k) for k in patches}
    try:
        for k, make in patches.items():
            setattr(h5, k, make())
        yield
    finally:
        for k, v in saved.items():
            setattr(h5, k, v)


def h5_scope(table: bool = False):
    """For the duration of ONE call (single thread, restored in `finally`) some of the H5 module's names are C1-NF's. Two uses only:
      table=True   H5Limits.from_config, a pure function: the limit table and the tier table (H5_DEFAULT, H5_MAX, TIERS).
      table=False  H5Executor.__init__: the rule id and the LIVE_OK / TIER paths of the start row, the state-dir precheck and the probe config's book.
    Everything else that reads H5's tier table at run time is overridden in C1NFExecutor (h5, tier, _refresh_tier, _config_clamps,
    _tier_step_down_due, _signer_spend_cap, _live_gate), so nothing depends on a patch outside these two calls. This is how h5_executor.py stays
    byte-identical (the same device as its own _h5_precheck_scope); tests assert the module is H5's again after every use."""
    return _patched(_TABLE_PATCHES if table else _INIT_PATCHES)


def c1nf_limits(cfg: dict[str, Any], tier: str = C1NF_LOWEST_TIER) -> h5.H5Limits:
    """The limits for a config at a C1-NF tier, with C1-NF's maxima. end_ms is clamped to C1NF_END_MAX_MS (and set to it when the config has none).
    Raises ValueError for a bad value, an unknown tier, or a key that is not this rule's to set."""
    if tier not in C1NF_TIERS:
        raise ValueError(f"unknown C1-NF tier {tier!r}")
    for key in NOT_CONFIGURABLE:
        if key in cfg:
            raise ValueError(f"{key} is not configurable in the C1-NF executor")
    age = cfg.get("max_pick_age_s")
    if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or not 0.5 <= age):
        raise ValueError("max_pick_age_s must be a finite number >= 0.5")
    hb = cfg.get("feed_heartbeat_max_age_ms")
    if hb is not None and (isinstance(hb, bool) or not isinstance(hb, int) or hb < 0):
        raise ValueError("feed_heartbeat_max_age_ms must be an int >= 0 (0 means the code's 150 s)")
    with h5_scope(table=True):
        lim = h5.H5Limits.from_config(cfg, tier)
    return replace(lim, end_ms=min(lim.end_ms if lim.end_ms is not None else C1NF_END_MAX_MS, C1NF_END_MAX_MS))


def heartbeat_max_age_ms(cfg: dict[str, Any]) -> int:
    """DEC-026 section 7 rule 7, fail closed: the code's 150 s, lowered by config, never raised or switched off (0 or absent = 150 s)."""
    v = cfg.get("feed_heartbeat_max_age_ms")
    return min(FEED_HEARTBEAT_MAX_AGE_MS, v) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else FEED_HEARTBEAT_MAX_AGE_MS


def read_tier(path: Path, root_checks: bool) -> tuple[str, str | None]:
    """(tier, problem), as h5_executor.read_tier but on C1-NF's table: live, `path` must pass h5.root_file_problem; the content must be exactly a
    C1-NF tier. Missing, unsafe, unreadable, invalid (T0 included: C1-NF has none) or inactive (T2) all mean T1, the canary and lowest tier."""
    low = C1NF_LOWEST_TIER
    if root_checks:
        why = h5.root_file_problem(path)
        if why:
            return low, f"tier_file_{why}"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return low, "tier_file_missing"
    except OSError:
        return low, "tier_file_unreadable"
    try:
        text = os.read(fd, 64).decode("ascii", "replace").strip()
    except OSError:
        return low, "tier_file_unreadable"
    finally:
        os.close(fd)
    if text in C1NF_TIERS:
        return text, None
    return low, ("tier_file_inactive" if text in C1NF_INACTIVE_TIERS else "tier_file_invalid")


# --- the exit plan: anchored on our own landing ---------------------------------------------------------------------------


def c1nf_exit_plan(anchor_slot: int, sps: float, lim: h5.H5Limits, anchor_wall_ms: int | None = None) -> h5.ExitPlan:
    """Slots: exit at anchor + round(300 / sps) (python rounding, as the frozen scorer), the sell LANDS 0.55 s later (ceil slots), is sent
    send_lead_ms before that, and is armed arm_lead_ms before it is sent; escalation at anchor + 315 s, the emergency deadline at anchor + 370 s.
    The H5 plan's field names are kept (`s0_slot` / `s0_wall_ms` are the anchor, here the buy's landed slot and its wall time), so the whole H5
    exit scheduler runs on it unchanged. The wall stages bound the slot stages, so a moved slot rate cannot shift an exit by tens of seconds."""
    exit_slot = anchor_slot + int(round(HOLD_S / sps))
    land_slot = exit_slot + h5.ceil_slots(EXIT_LAND_OFFSET_S, sps)
    send_slot = land_slot - h5.ceil_slots(lim.send_lead_ms / 1000.0, sps)
    arm_slot = send_slot - h5.ceil_slots(lim.arm_lead_ms / 1000.0, sps)
    late_s = HOLD_S + LATE_AFTER_EXIT_S  # DEC-026 section 7 rule 5: landing + 305 s
    wall: dict[str, int | None] = {}
    if anchor_wall_ms is not None:
        send_wall = anchor_wall_ms + round((HOLD_S + EXIT_LAND_OFFSET_S) * 1000) - lim.send_lead_ms
        wall = dict(s0_wall_ms=anchor_wall_ms, send_wall_ms=send_wall, arm_wall_ms=send_wall - lim.arm_lead_ms,
                    escalate_wall_ms=anchor_wall_ms + round(ESCALATE_S * 1000), deadline_wall_ms=anchor_wall_ms + round(DEADLINE_S * 1000),
                    late_wall_ms=anchor_wall_ms + round(late_s * 1000))
    return h5.ExitPlan(anchor_slot, sps, exit_slot, land_slot, send_slot, arm_slot, anchor_slot + int(round(ESCALATE_S / sps)),
                       anchor_slot + int(round(DEADLINE_S / sps)), anchor_slot + int(round(late_s / sps)), **wall)


def stuck_due(plan: dict[str, Any], est: int | None, now: int) -> bool:
    """DEC-026 section 7 rule 4's instant, the plan's anchor (the buy's landing) + 600 s, on H5's wall-bounded rule (H5Executor._due): due at
    its wall time whatever the slot clock says, and at its slot only from EARLY_TOLERANCE_MS before that. It is never due before the plan's
    own deadline (the emergency sell at landing + 370 s), so a stand-in plan, whose deadline is far away, never latches here."""
    if not h5.H5Executor._due(plan, "deadline", est, now):
        return False
    wall0 = plan.get("s0_wall_ms")
    stage = {"stuck_slot": plan["s0_slot"] + int(round(STUCK_S / plan["sps"])),
             "stuck_wall_ms": wall0 + round(STUCK_S * 1000) if wall0 is not None else None}
    return h5.H5Executor._due(stage, "stuck", est, now)


def c1nf_exit_reserve(lim: h5.H5Limits) -> int:
    """Lamports the balance guard reserves per exit. H5's SELL_RESERVE_LAMPORTS (1,000,000) was sized for H5's 150,000 escalated priority; one
    C1-NF escalated or emergency send costs 1,010,000 + the base fee, so the reserve is never below that. DEC-026 section 6 quotes 1,000,000 per
    exit; at 1,015,000 a first 0.05 SOL buy needs 103,625,000 lamports, still the DEC's "about 0.104 SOL" (fail closed: only stricter)."""
    return max(h5.SELL_RESERVE_LAMPORTS, lim.escalated_priority_lamports + tx.BASE_FEE_PER_SIGNATURE)


# --- the pick ---------------------------------------------------------------------------------------------------------------

_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_HASH = re.compile(r"^[0-9A-Za-z_.:\-]{8,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class C1NFPick:
    mint: str
    pool: str
    decision_T_ms: int
    SD_slot: int
    pred: float
    h_top1: float
    feature_hash: str
    q_lamports: int  # the decision-state reference of the 1.15 x spot guard (required: no fallback to a read at receipt)
    base_reserve: int
    model_sha: str = ""
    pick_v_lamports: int | None = None  # the pick's own V at the decision state (#503 v_lamports)
    state_slot: int | None = None
    t_emit_ms: int | None = None
    sps: float | None = None  # filled in by the executor: the slot rate it measures at the decision
    v_lamports: int | None = None  # filled in from the receipt snapshot
    guard_ref: str | None = None  # always "decision_state" when set: the guard is measured against the pick's own reserves

    @property
    def decision_ms(self) -> int:
        return self.decision_T_ms

    @property
    def pre_unlinked(self) -> bool:
        return False  # an H5 detector notion (a predecessor print that may be missing); a C1-NF pick has none

    @property
    def trigger_slot(self) -> int:
        return self.SD_slot

    @property
    def pick_id(self) -> str:
        return f"{self.mint}:{self.decision_T_ms}"

    def public(self) -> dict[str, Any]:
        """The record the H5 core keeps with a position (`trigger`): it reads trigger_slot and sps from it."""
        return {**asdict(self), "trigger_slot": self.SD_slot, "decision_ms": self.decision_T_ms, "pick_id": self.pick_id}

    def log_fields(self) -> dict[str, Any]:
        return {k: v for k, v in self.public().items() if k != "mint"}  # the ledger row carries the mint itself


def _num(v: Any) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def parse_pick(row: Any) -> tuple[C1NFPick | None, str | None]:
    """(pick, None) for a good c1nf_pick, (None, reason) for one that cannot be traded, (None, None) for any other record. Only the whitelisted
    fields are read. Refuses: malformed fields, a decision time off the whole-minute grid, a pool that is not the mint's canonical PumpSwap pool,
    pred <= 0.02, h_top1 > 0.5 or not a number, stage1 not literally true, a suppressed (sealed) stub, a decision before 2026-10-10T00Z
    (`pre_window`). intent_tick turns `bad_pick:suppressed` and `pre_window` into counts with no mint."""
    if not isinstance(row, dict) or row.get("type") != PICK_TYPE:
        return None, None
    if row.get("suppressed"):
        return None, "bad_pick:suppressed"
    if h5._is_int(row.get("decision_T_ms")) and row["decision_T_ms"] < PICK_WINDOW_START_MS:
        return None, "pre_window"  # #503 executor_refusal: the shadow writes these for its book's continuity only
    try:
        mint, pool = row["mint"], row["pool"]
        if not (isinstance(mint, str) and isinstance(pool, str) and _B58.match(mint) and _B58.match(pool)):
            return None, "bad_pick:ids"
        for k in ("decision_T_ms", "SD_slot"):
            if not h5._is_int(row[k]) or row[k] <= 0:
                return None, f"bad_pick:{k}"
        if not _num(row["pred"]):
            return None, "bad_pick:pred"
        if not _num(row["h_top1"]) or not 0.0 <= row["h_top1"] <= 1.0:
            return None, "bad_pick:h_top1"
        if row["stage1"] is not True:
            return None, "bad_pick:stage1"
        fh = row["feature_hash"]
        if not (isinstance(fh, str) and _HASH.match(fh)):
            return None, "bad_pick:feature_hash"
    except KeyError as exc:
        return None, f"bad_pick:missing_{exc.args[0]}"
    if row["decision_T_ms"] % DECISION_GRID_MS:
        return None, "bad_pick:off_grid"
    if not row["pred"] > PRED_MIN:
        return None, "bad_pick:pred_not_above_threshold"
    if row["h_top1"] > H_TOP1_MAX:
        return None, "bad_pick:h_top1_over_cap"
    try:
        canonical = str(tx.canonical_pool(Pubkey.from_string(mint)))
    except Exception:
        return None, "bad_pick:ids"
    if canonical != pool:
        return None, "bad_pick:not_canonical"
    q, b, v, ss = row.get("q_lamports"), row.get("base_reserve"), row.get("v_lamports"), row.get("state_slot")
    if q is None or b is None or v is None or ss is None:
        return None, "bad_pick:ref_state_missing"  # DEC-026 section 6: no decision-time state, no buy (fail closed)
    if not (h5._is_int(q) and h5._is_int(b) and h5._is_int(v) and h5._is_int(ss) and q > v > 0 and b > 0 and 0 < ss < row["SD_slot"]):
        return None, "bad_pick:ref_state"
    ms = row.get("model_sha")
    if not (isinstance(ms, str) and _SHA256.match(ms)):
        return None, "bad_pick:model_sha"
    te = row.get("t_ms")
    return C1NFPick(mint, pool, row["decision_T_ms"], row["SD_slot"], float(row["pred"]), float(row["h_top1"]), row["feature_hash"], q, b,
                    ms, v, ss, te if h5._is_int(te) else None), None


def outcome_pct_of(row: dict[str, Any]) -> float | None:
    """The pinned monitor outcome of one #503 c1nf_outcome (OUTCOME_LEG / OUTCOME_BOUND / canary / OUTCOME_VARIANT), percent of the twin's stake.
    None when that leg is missing, incomplete or malformed. Only this path of the record is read."""
    try:
        twin = row["legs"][OUTCOME_LEG][OUTCOME_BOUND]["canary"]
        stake, pnl = twin["stake_lamports"], twin[OUTCOME_VARIANT]
    except (KeyError, TypeError):
        return None
    if not (h5._is_int(stake) and stake > 0 and _num(pnl)):
        return None
    pct = 100.0 * float(pnl) / stake
    return pct if -100.0 - 1e-9 <= pct <= 100_000.0 else None


def parse_outcome(row: Any) -> tuple[tuple[str, int, float] | None, str | None]:
    """((mint, decision_T_ms, outcome_pct), None) for a priced c1nf_outcome, (None, reason) for a malformed or unpriced one (`outcome_unpriced`:
    the pinned leg is missing or incomplete), (None, None) for any other record."""
    if not isinstance(row, dict) or row.get("type") != OUTCOME_TYPE:
        return None, None
    mint, t = row.get("mint"), row.get("decision_T_ms")
    if not (isinstance(mint, str) and _B58.match(mint)):
        return None, "bad_outcome:mint"
    if not h5._is_int(t) or t <= 0:
        return None, "bad_outcome:decision_T_ms"
    pct = outcome_pct_of(row)
    if pct is None:
        return None, "outcome_unpriced"
    return (mint, t, float(pct)), None


class OracleStale(Exception):
    """The CAP-PICK oracle file has no fresh heartbeat: a False cannot be trusted (fail closed)."""


_HB_RE = re.compile(r'"hb"\s*:\s*true')
_TMS_RE = re.compile(r'"t_ms"\s*:\s*(\d{1,16})')


class StaleCheckedPickOracle(h5.JsonlPickOracle):
    """H5's boolean-only picks-file reader plus #509's staleness rule (DEC-026 section 9.1): the exporter writes {"hb":true,"t_ms":...} rows; when
    the newest is older than 60 s (or more than 5 s in the future, or there is none) a False is not trusted: OracleStale, so the seal refuses.
    A True still answers True (a pick never flips, and True refuses the buy anyway). Rows are matched by regex, never parsed as JSON."""

    def __init__(self, path: str | Path, now_ms: Callable[[], int] | None = None, stale_s: float = SEAL_ORACLE_STALE_S):
        super().__init__(path)
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.stale_s = min(float(stale_s), SEAL_ORACLE_STALE_S)
        self.last_hb_ms: int | None = None
        self._hb_off = 0
        self._hb_ino: int | None = None

    def _refresh(self) -> None:
        st = self.path.stat()
        if self._hb_ino != st.st_ino or st.st_size < self._hb_off:
            self._hb_off, self._hb_ino, self.last_hb_ms = 0, st.st_ino, None
        with self.path.open("rb") as fh:
            fh.seek(self._hb_off)
            data = fh.read(pe.MAX_READ_BYTES * 8)
        end = data.rfind(b"\n")
        if end >= 0:
            for raw in data[: end + 1].splitlines():
                line = raw.decode("utf-8", "replace")
                t = _TMS_RE.findall(line)
                if _HB_RE.search(line) and len(t) == 1:
                    self.last_hb_ms = max(self.last_hb_ms or 0, int(t[0]))
            self._hb_off += end + 1
        super()._refresh()

    def __call__(self, mint: str) -> bool:
        self._refresh()
        if self._flags.get(mint) is True:
            return True
        now = self.now_ms()
        if self.last_hb_ms is None or now - self.last_hb_ms > self.stale_s * 1000 or self.last_hb_ms - now > SEAL_ORACLE_SKEW_S * 1000:
            raise OracleStale("stale")
        if mint not in self._flags:
            raise h5.OracleUndecided("undecided")
        return self._flags[mint]


@dataclass
class _Tail:
    """h5.tail_lines's offset state for one stream (the picks stream uses the H5 state's own, persisted with the positions)."""

    started: bool = False
    offset: int = 0
    inode: int | None = None


@dataclass(frozen=True)
class _MintOnly:
    mint: str


# --- persisted extras: cooldown clock and the pick table ------------------------------------------------------------------------


@dataclass
class C1NFExtra:
    """Beside the H5 counters, in its own file (H5Counters rewrites its file without fields it does not know): when each mint last exited, and one
    row per pick the monitor follows. A restart cannot reset the cooldown or the monitor's window."""

    last_exit_ms: dict[str, int] = field(default_factory=dict)
    picks: dict[str, dict[str, Any]] = field(default_factory=dict)  # pick_id -> {mint, T, status, reason, monitored, outcome_pct, oseq}
    oseq: int = 0  # outcomes accepted so far
    monitor_floor: int = 0  # only outcomes with oseq above this count: set when the halt latches, so a clear needs fresh evidence
    outcomes_unmatched: int = 0
    outcomes_unpriced: int = 0
    otail: dict[str, Any] = field(default_factory=dict)  # the outcomes stream's tail: {path, started, offset, inode} (a restart loses no outcome)
    late_window: list[int] = field(default_factory=list)  # 1 = late, over the last LATE_SELL_WINDOW landed sells
    late_alert_on: bool = False
    counts: dict[str, int] = field(default_factory=dict)  # count-only refusals with no mint: pre_window, suppressed, sealed bad picks

    def save(self, path: Path, now_ms: int) -> None:
        for m in [m for m, t in self.last_exit_ms.items() if now_ms - t > 3_600_000]:
            del self.last_exit_ms[m]
        if len(self.picks) > PICKS_KEEP:
            done = sorted((p["T"], pid) for pid, p in self.picks.items() if p["status"] != "pending")
            for _t, pid in done[: len(self.picks) - PICKS_KEEP]:
                del self.picks[pid]
        h5._atomic_json(path, {"schema": "c1nf_extra_v1", **self.__dict__})

    @classmethod
    def load(cls, path: Path) -> "C1NFExtra":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


EXTRA_NAME, COUNTERS_NAME = "c1nf-extra.json", "h5-counters.json"


def extra_reset_problem(mode_dir: Path) -> str | None:
    """The anti-reset guard of c1nf-extra.json (as probe_executor.guard_live_state guards the state file): the extra file is gone while the H5
    counters beside it show the executor has run (a picks stream followed, a buy landed, a sell landed, an exit plan held). Deleting the file
    would silently reset the 60 s cooldown clock, the fill-selection window and its floor, the late-sell window and the outcomes offset. Both
    files are written at the first start (H5's __init__ saves the counters, this one the extra right after), so a missing extra with a
    used counters file means it was removed. None when there is nothing to guard."""
    if (mode_dir / EXTRA_NAME).exists() or not (mode_dir / COUNTERS_NAME).exists():
        return None
    try:
        raw = json.loads((mode_dir / COUNTERS_NAME).read_text())
    except (OSError, ValueError):
        return "c1nf_extra_missing"  # fail closed: the counters cannot even say whether the executor has run
    if not isinstance(raw, dict) or raw.get("tail_path") or raw.get("landing_s") or raw.get("sells_landed") or raw.get("plans"):
        return "c1nf_extra_missing"
    return None


def monitor_stats(extra: C1NFExtra) -> dict[str, Any]:
    """The fill-selection comparison over the last FILL_SEL_WINDOW monitored picks that have an outcome (ordered by decision time)."""
    rows = sorted(((p["T"], pid, p) for pid, p in extra.picks.items()
                   if p.get("monitored") and p["status"] in ("filled", "unfilled") and p.get("outcome_pct") is not None and p.get("oseq", 0) > extra.monitor_floor),
                  key=lambda r: (r[0], r[1]))[-FILL_SEL_WINDOW:]
    fill = [p["outcome_pct"] for _t, _i, p in rows if p["status"] == "filled"]
    unf = [p["outcome_pct"] for _t, _i, p in rows if p["status"] == "unfilled"]
    mean = lambda v: sum(v) / len(v) if v else None  # noqa: E731
    mf, mu = mean(fill), mean(unf)
    gap = (mu - mf) if (mf is not None and mu is not None) else None
    adverse = bool(gap is not None and len(unf) >= FILL_SEL_MIN_UNFILLED and gap > FILL_SEL_GAP_PP)
    return {"n": len(rows), "n_filled": len(fill), "n_unfilled": len(unf), "mean_filled_pct": mf, "mean_unfilled_pct": mu, "gap_pp": gap, "adverse": adverse}


# --- start conditions -----------------------------------------------------------------------------------------------------------


def c1nf_live_ok_valid() -> str | None:
    """H5's own check (lstat / O_NOFOLLOW, root:root, exactly 0644, in a root-owned directory not writable by others) on the C1-NF path."""
    why = h5.root_file_problem(LIVE_OK_PATH)
    return None if why is None else f"live_ok_{why}"


def exp025_part1_present(root: Path) -> bool:
    p = root / EXP025_PART1
    try:
        return p.is_file() and not p.is_symlink() and p.stat().st_size > 0
    except OSError:
        return False


def start_refusal(cfg: dict[str, Any], root: Path | None = None) -> str | None:
    """The live start conditions, keyless. None = may start live."""
    root = root or h5.repo_root()
    if h5.live_path_overrides(cfg):
        return "config_path_override:" + ",".join(h5.live_path_overrides(cfg))
    if Path(str(cfg.get("state_dir", ""))) != LIVE_STATE_DIR:
        return "state_dir_not_c1nf"
    why = c1nf_live_ok_valid()
    if why:
        return why
    if not exp025_part1_present(root):
        return "exp025_part1_missing"
    if cfg.get("end_ms") is None:
        return "end_ms_missing"
    if not C1NF_MODEL_SHA256:
        return "model_unpinned"  # DEC-026 section 11 item 12
    if not C1NF_WALLET_PUBKEY or C1NF_WALLET_PUBKEY == H5_WALLET_PUBKEY:
        return "wallet_unpinned"  # DEC-026 section 5: the second wallet's public key, a reviewed code line
    return None


def wallet_refusal(pubkey: str) -> str | None:
    """DEC-026 section 5, after the key is loaded: never H5's wallet, and only the pinned C1-NF wallet."""
    if pubkey == H5_WALLET_PUBKEY:
        return "wallet_is_h5"  # C1-NF's 35% cap and exposure test would run on H5's balance, blind to H5's open positions
    if not C1NF_WALLET_PUBKEY or pubkey != C1NF_WALLET_PUBKEY:
        return "wallet_not_pinned_c1nf"
    return None


def credential_path() -> str:
    d = os.environ.get("CREDENTIALS_DIRECTORY")
    if not d:
        raise SystemExit("CREDENTIALS_DIRECTORY is not set: live mode only loads the key from the systemd credential")
    return str(Path(d) / CREDENTIAL_NAME)


# --- the executor ---------------------------------------------------------------------------------------------------------------


class C1NFExecutor(h5.H5Executor):
    def __init__(self, rpc: Callable[[str, list], dict], cfg: dict[str, Any], keypair: Keypair | None, *,
                 now_ms: Callable[[], int] | None = None, pick_oracle: Callable[[str], bool] | None = None,
                 root: Path | None = None, rpc_label: str | None = None):
        c1nf_limits(cfg)  # a bad value, or a key that is not this rule's to set, refuses before any file is touched
        if keypair is not None and Path(str(cfg.get("state_dir", ""))) != LIVE_STATE_DIR:
            raise SystemExit(f"live refused: state_dir must be {LIVE_STATE_DIR}")
        if keypair is not None:  # before any file is touched
            why = wallet_refusal(str(keypair.pubkey()))
            if why:
                raise SystemExit(f"live refused: {why}")
            if not C1NF_MODEL_SHA256:
                raise SystemExit("live refused: model_unpinned (DEC-026 section 11 item 12)")
        extra_reset = extra_reset_problem(Path(cfg["state_dir"]) / (LIVE if keypair is not None else DRYRUN))
        if extra_reset and keypair is not None:  # before any file is touched
            raise SystemExit(f"live refused: {extra_reset}: {EXTRA_NAME} is gone but {COUNTERS_NAME} shows the executor ran (the cooldown clock, "
                             "the fill-selection window and the outcomes offset would reset). Restore the file, or reconstruct it by hand")
        with h5_scope():
            super().__init__(rpc, cfg, keypair, now_ms=now_ms, pick_oracle=pick_oracle, root=root, rpc_label=rpc_label)
        self.heartbeat_max_age_ms = heartbeat_max_age_ms(cfg)  # replaces H5's config-only value: never off, never above 150 s
        if not 0 < self.heartbeat_max_age_ms <= FEED_HEARTBEAT_MAX_AGE_MS:
            raise SystemExit("refused: the feed heartbeat guard is not set")
        self.max_pick_age_s = min(MAX_PICK_AGE_S, float(cfg.get("max_pick_age_s") or MAX_PICK_AGE_S))
        self._gaps: list[tuple[int, int]] = []  # recent c1nf_gap slot ranges (in memory: a restart waits for a fresh heartbeat anyway)
        self._ev_tail = _Tail()  # the events stream starts at its end: freshness comes from the next heartbeat
        self._ev_path: Path | None = None
        self._globs: dict[str, tuple[int, Path | None]] = {}
        self.extra_path = self.counters_path.with_name(EXTRA_NAME)
        self.extra = C1NFExtra.load(self.extra_path)
        self._out_tail = _Tail(**{k: self.extra.otail[k] for k in ("started", "offset", "inode") if k in self.extra.otail})
        self._out_path: Path | None = Path(self.extra.otail["path"]) if self.extra.otail.get("path") else None
        self._save_extra()
        self._log("c1nf_start", "", rule=RULE_ID, code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  h5_code_sha256=hashlib.sha256(Path(h5.__file__).read_bytes()).hexdigest(), limits=asdict(self.h5), max_pick_age_s=self.max_pick_age_s,
                  exp025_part1=exp025_part1_present(self.root), live_ok=str(LIVE_OK_PATH) if not self.dry_run else None,
                  tier_file=str(self._tier_path()), tier_table=C1NF_TIERS, model_pinned=bool(C1NF_MODEL_SHA256),
                  model_sha256=sorted(C1NF_MODEL_SHA256), heartbeat_max_age_ms=self.heartbeat_max_age_ms,
                  wallet_pinned=bool(C1NF_WALLET_PUBKEY), seal_oracle=type(self.pick_oracle).__name__ if self.pick_oracle else None,
                  seal_start_ms=h5.SEAL_START_MS, final_marker_present=Path(self.final_marker).is_file(),
                  exit_reserve_lamports=c1nf_exit_reserve(self.h5), stuck_s=STUCK_S)
        if extra_reset:  # a dry run goes on (it has no money to protect) but says so
            self._alert("c1nf_extra_reset", "", why=extra_reset)
        self._seal_provisioning_alerts()

    def _seal_provisioning_alerts(self) -> None:
        """At start, say what will refuse every buy in the seal window (h5._seal_reason, fail closed) before it happens. Neither is a refusal to
        start: buys before 2026-10-16T01Z need neither.
          seal_oracle_missing      the run's end is past SEAL_START_MS and there is no pick oracle (no `pick_file` in the config).
          seal_final_marker_missing  it is past ORACLE_EARLIEST_MS (the DEC-016 FINAL is written by then) and <state_dir>/FINAL_WRITTEN is absent.
        Who sets `pick_file` (#509's exporter output) and who writes FINAL_WRITTEN is DEC-026 section 11 item 11 (the runbook)."""
        end = self.h5.end_ms if self.h5.end_ms is not None else C1NF_END_MAX_MS
        if end <= h5.SEAL_START_MS:
            return
        if self.pick_oracle is None:
            self._alert("seal_oracle_missing", "", seal_start_ms=h5.SEAL_START_MS, end_ms=end,
                        consequence="every buy from seal_start_ms is refused seal_window_no_oracle")
        if self.now_ms() >= h5.ORACLE_EARLIEST_MS and not Path(self.final_marker).is_file():
            self._alert("seal_final_marker_missing", "", final_marker=str(self.final_marker),
                        consequence="every buy in the seal window is refused seal_window_no_oracle")

    def __repr__(self) -> str:
        return f"C1NFExecutor(mode={self.run_mode}, user={self.user})"

    # -- the C1-NF tier table (DEC-026 section 6): every H5 method that reads H5's TIERS at run time is overridden here -------------
    @property  # type: ignore[override]
    def tier(self) -> str:
        return self.__dict__.get("_c1nf_tier", C1NF_LOWEST_TIER)

    @tier.setter
    def tier(self, value: str) -> None:
        """H5's code sets "T0" (its lowest) at start and on a problem read; anything that is not an active C1-NF tier is the lowest C1-NF tier."""
        self.__dict__["_c1nf_tier"] = value if value in C1NF_TIERS else C1NF_LOWEST_TIER

    @property
    def h5(self) -> h5.H5Limits:
        """The limits of the active C1-NF tier, the config tightening them (never loosening)."""
        lim = self._tier_limits.get(self.tier)
        if lim is None:
            lim = self._tier_limits[self.tier] = c1nf_limits(self.h5cfg, self.tier)
        return lim

    def _tier_path(self) -> Path:
        return TIER_FILE_PATH if not self.dry_run else Path(self.h5cfg["state_dir"]) / "TIER"

    def _refresh_tier(self, now: int) -> None:
        """H5's logic on C1-NF's table (see H5Executor._refresh_tier): a problem read (unsafe, unreadable, invalid, inactive) alerts once per
        distinct problem and applies T1 without re-starting the tier the counters hold; a change is a tier_change with fresh baselines."""
        tier, problem = read_tier(self._tier_path(), root_checks=not self.dry_run)
        bad_read = problem not in (None, "tier_file_missing")
        if bad_read and problem != self._tier_problem:
            self._alert("tier_file_problem", "", problem=problem)
        self._tier_problem = problem
        ts = self.counters.tier_state
        if bad_read and ts.get("tier") in C1NF_TIERS:
            self.tier = C1NF_LOWEST_TIER
            return
        if tier != ts.get("tier"):
            self._start_tier(tier, ts.get("tier"), problem, now)
            return
        self.tier = tier
        if ts.get("wallet_lamports") is None:  # the wallet could not be read when the tier started: try again
            wallet = self._balance_value(now)
            if wallet is not None:
                ts["wallet_lamports"] = wallet
                self.counters.save(self.counters_path)

    def _config_clamps(self) -> dict[str, dict[str, int]]:
        lim = asdict(self.h5)
        return {k: {"table": v, "effective": lim[k]} for k, v in C1NF_TIERS[self.tier].items() if lim[k] < v}

    def _tier_step_down_due(self, why: str) -> None:
        if self.dry_run or self.tier == C1NF_LOWEST_TIER:
            return
        super()._tier_step_down_due(why)

    def _signer_spend_cap(self) -> int:
        return self.h5.stake_lamports  # the active C1-NF tier's stake; no H5 T2 impact flag applies to C1-NF

    def _note_buy_landing(self, trigger_slot: int, landed_slot: int, sps: float) -> None:
        """DEC-026 section 7 rule 3 (replaces H5's 10-fill median above 3.0 s): the landing p50, SD_slot to landed slot in seconds of slot time,
        over the first 20 landed buys and then a rolling 20, strictly above 1.9 s latches landing_p50_gt_1_9s."""
        sec = (landed_slot - trigger_slot) * sps
        self.counters.landing_s = [*self.counters.landing_s, round(sec, 4)][-50:]
        self.counters.save(self.counters_path)
        last = self.counters.landing_s[-LANDING_P50_WINDOW:]
        if len(last) >= LANDING_P50_WINDOW and statistics.median(last) > LANDING_P50_MAX_S:
            self._latch(LANDING_P50_HALT, median_s=round(statistics.median(last), 4), n=len(last))

    def _save_extra(self) -> None:
        self.extra.save(self.extra_path, self.now_ms())

    # -- gates ------------------------------------------------------------------------------------------------------------------
    def _live_gate(self) -> str | None:
        """Checked before every live buy send: LIVE_OK (C1-NF's path) present and valid, and EXP-025 Part 1 in the deployed tree."""
        if self.dry_run:
            return None
        why = c1nf_live_ok_valid()
        if why:
            return why
        if not exp025_part1_present(self.root):
            return "exp025_part1_missing"
        return None

    # -- the pick's refusals ------------------------------------------------------------------------------------------------------
    def _pick_refusal(self, pick: C1NFPick, now: int) -> str | None:
        """Order: kill switches and gates, the clock, the feed, a stuck sell, staleness, the slot rate, the book (one position per mint, the 60 s
        cooldown), capacity."""
        why = self._kill_reason() or self._live_gate()
        if why:
            return why
        if now + pl.CLOCK_BACK_TOLERANCE_MS < self.state.max_seen_ms:
            return "clock_backwards"
        if self.feed_gap or now < self._gap_until_ms:
            return "feed_gap"
        if self.feed_last_ms is None or now - self.feed_last_ms > self.heartbeat_max_age_ms:
            return "feed_stale"  # DEC-026 section 7 rule 7: the guard is a code constant (heartbeat_max_age_ms), never off
        if self._gap_covers(pick.SD_slot, now):
            return "feed_gap"  # a c1nf_gap for the decision minute: no pick for it
        if self.sell_stuck():
            return "sell_stuck"
        measured = self.slots.measured_sps(now)  # the exit is timed in slots at the rate we see; with no measurement nothing is timed
        if measured is None:
            return "sps_unmeasured"
        est = self.slots.est(now, measured)
        if est is None or (est - pick.SD_slot) > h5.ceil_slots(self.max_pick_age_s, measured):
            return "stale_pick"  # chain age: slots since the decision slot x our measured rate
        if now - pick.decision_T_ms > self.max_pick_age_s * 1000 * WALL_AGE_BACKSTOP_FACTOR:
            return "stale_pick"
        if pick.pick_id in self.extra.picks:
            return "duplicate_pick"
        if pick.mint in self.state.open or pick.mint in self.state.pending:
            return "already_held"  # never two positions in one mint
        last = self.extra.last_exit_ms.get(pick.mint)
        if last is not None and pick.decision_T_ms < last + COOLDOWN_MS:
            return "cooldown"  # the rule: a pick is taken only if its decision time >= the mint's previous exit + 60 s
        pend = sum(1 for p in self.state.pending.values() if p["kind"] == "buy")
        if len(self.state.open) + pend >= self.h5.max_open:
            return "max_open"
        return None

    def _gap_covers(self, sd_slot: int, now: int) -> bool:
        sps = self.slots.measured_sps(now) or 0.4
        lo_need = sd_slot - h5.ceil_slots(GAP_LOOKBACK_S, sps)
        return any(lo <= sd_slot and hi >= lo_need for lo, hi in self._gaps)

    def _on_gap(self, row: dict[str, Any]) -> None:
        lo, hi = row.get("slot_from"), row.get("slot_to")
        if h5._is_int(lo) and h5._is_int(hi):
            self._gaps = [*self._gaps, (min(lo, hi), max(lo, hi))][-GAPS_KEEP:]
        self._gap_until_ms = self.now_ms() + self.h5.gap_hold_ms
        self._log("feed_gap", "", gap=True, kind_=str(row.get("kind"))[:40], slot_from=lo if h5._is_int(lo) else None,
                  slot_to=hi if h5._is_int(hi) else None)

    def _on_heartbeat(self, row: dict[str, Any]) -> None:
        self.feed_last_ms = self.now_ms()
        shas = row.get("model_shas")
        if C1NF_MODEL_SHA256 and isinstance(shas, list) and any(x not in C1NF_MODEL_SHA256 for x in shas) and MODEL_HALT not in self.counters.halts:
            self._latch(MODEL_HALT, source="heartbeat", got=[str(x)[:16] for x in shas][:4])

    def _count_only(self, reason: str) -> None:
        """A refusal with no mint anywhere: the seal's discipline for records the executor must not tie to a mint."""
        self._count_refusal(reason)
        self.extra.counts[reason] = self.extra.counts.get(reason, 0) + 1
        self._save_extra()

    def _refuse(self, trg: Any, reason: str, **kw: Any) -> None:
        super()._refuse(trg, reason, **kw)
        if isinstance(trg, C1NFPick) and reason not in h5.SEAL_REASONS:  # a seal refusal is a count only: never recorded per mint
            monitored = reason not in BOOK_REASONS and reason not in STOP_REASONS and not reason.startswith("halt_latched")
            if reason == "duplicate_pick":
                return  # the first record of this pick keeps its row
            self._set_status(trg, "unfilled", reason, monitored)

    # -- pick bookkeeping and the fill-selection monitor ---------------------------------------------------------------------------
    def _set_status(self, pick: C1NFPick, status: str, reason: str | None, monitored: bool = True) -> None:
        old = self.extra.picks.get(pick.pick_id)
        if old is not None and old["status"] != "pending":
            return
        self.extra.picks[pick.pick_id] = {"mint": pick.mint, "T": pick.decision_T_ms, "status": status, "reason": reason, "monitored": monitored,
                                          "outcome_pct": (old or {}).get("outcome_pct"), "oseq": (old or {}).get("oseq", 0)}
        if status != "pending":
            self._log("pick_status", pick.mint, pick_id=pick.pick_id, decision_T_ms=pick.decision_T_ms, status=status, reason=reason, monitored=monitored)
        self._save_extra()

    def _resolve_pick(self, pick_id: str | None, filled: bool, reason: str | None) -> None:
        p = self.extra.picks.get(pick_id) if pick_id else None
        if p is None or p["status"] != "pending":
            return
        p["status"], p["reason"] = ("filled", None) if filled else ("unfilled", reason)
        self._log("pick_status", p["mint"], pick_id=pick_id, decision_T_ms=p["T"], status=p["status"], reason=p["reason"], monitored=p["monitored"])
        self._save_extra()

    def on_outcome(self, mint: str, decision_T_ms: int, outcome_pct: float) -> None:
        """The shadow's paper outcome of one pick. Kept for the monitor; the first outcome of a pick stands."""
        pid = f"{mint}:{decision_T_ms}"
        p = self.extra.picks.get(pid)
        if p is None or not p.get("monitored") or p.get("outcome_pct") is not None:
            self.extra.outcomes_unmatched += 1  # book-skipped, stopped, sealed or unknown picks, and repeats: not evidence
            return self._save_extra()
        self.extra.oseq += 1
        p["outcome_pct"], p["oseq"] = round(outcome_pct, 4), self.extra.oseq
        stats = monitor_stats(self.extra)
        self._log("fill_selection_check", "", **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in stats.items()})
        if stats["adverse"] and FILL_SEL_HALT not in self.counters.halts:
            self.extra.monitor_floor = self.extra.oseq  # the next latch needs outcomes that arrive after this one
            self._latch(FILL_SEL_HALT, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in stats.items() if k != "adverse"})
        self._save_extra()

    # -- entry --------------------------------------------------------------------------------------------------------------------
    @pe.critical
    def handle_pick(self, pick: C1NFPick, seen_ms: int | None = None) -> None:
        now = self.now_ms()
        sealed = self._seal_reason(pick, now)  # FIRST: a sealed mint gets no per-mint record of any kind (DEC-026 section 9.1), by construction
        if sealed:
            return self._refuse(pick, sealed)
        self._refresh_tier(now)  # the ONE read of the TIER file for this pick: every limit below is the active tier's
        if C1NF_MODEL_SHA256 and pick.model_sha not in C1NF_MODEL_SHA256:
            if MODEL_HALT not in self.counters.halts:
                self._latch(MODEL_HALT, source="pick", got=pick.model_sha[:16])
            return self._refuse(pick, MODEL_HALT)
        why = self._pick_refusal(pick, now)
        would: str | None = None
        if not why:
            budget = self._budget_stop(now)
            if budget in ("total_loss_stop", "daily_loss_stop"):
                self._tier_step_down_due(budget)
            if budget in h5.T0_ALERT_STOPS and self.tier == C1NF_LOWEST_TIER and not self.dry_run:
                self._t0_budget_stop(budget, now)  # the lowest tier: a stop is never silent (one alert per reason and UTC day)
            if budget and not self.dry_run:
                why = budget
            elif budget:
                would = budget  # dry run: budget stops are recorded, not enforced
        if not why:
            why = self._balance_refusal(now)
        if why:
            return self._refuse(pick, why)
        measured = self.slots.measured_sps(now)
        assert measured is not None
        with self._prio():
            snap, pool, err = self._snapshot(pick.mint)  # always a fresh read: the pick carries no reserves of its own
            if snap is None:
                return self._refuse(pick, err or "no_pool")
            if snap.quote_priced is None:
                return self._refuse(pick, "no_v")
            if pool != pick.pool:
                return self._refuse(pick, "pool_mismatch")
            self.pool_cache[pick.mint] = (snap.ps, now)
            ref_q, ref_b, guard_ref = pick.q_lamports, pick.base_reserve, "decision_state"  # never a read at receipt (DEC-026 section 6)
            pick = replace(pick, sps=round(measured, 5), v_lamports=snap.v, guard_ref=guard_ref)
            terms = h5.entry_terms(ref_q, ref_b, self.h5.stake_lamports, self.h5.entry_tolerance_bps)  # type: ignore[arg-type]
            if terms["expected_tokens"] <= 0 or terms["min_out"] <= 0:
                return self._refuse(pick, "zero_quote")
            drift = (snap.quote_priced / snap.base_reserve) / (ref_q / ref_b) - 1.0  # type: ignore[operator]
            if drift > self.h5.entry_tolerance_bps / 10_000.0:
                return self._refuse(pick, "price_moved", drift_vs_trigger=drift, guard_ref=guard_ref)  # the 1.15 x spot guard would revert it: no send
            anchor = self.slots.est(now, measured)
            if anchor is None:  # never an exit plan anchored on slot 0 (every stage would be due at once): no slot estimate, no buy
                return self._refuse(pick, "stale_pick")
            plan = c1nf_exit_plan(anchor, measured, self.h5, now)  # provisional: anchored on the send, re-anchored at landing
            if self.dry_run:
                self._dry_buy(pick, snap.ps, terms, plan, now, would, drift)
                self._set_status(pick, "filled" if pick.mint in self.state.open else "unfilled", None if pick.mint in self.state.open else "dry_sim_error")
                return
            self._live_buy(pick, snap.ps, terms, plan, now, drift, seen_ms)
            if pick.mint in self.state.pending:
                self._set_status(pick, "pending", None)
            elif pick.pick_id not in self.extra.picks:
                self._set_status(pick, "unfilled", "send_blocked", monitored=False)  # a stop between the gate and the send: not a fill failure

    def _reanchor(self, mint: str, pos: dict[str, Any], landed_slot: int) -> None:
        """The buy confirmed in `landed_slot`: the exit is that slot's wall time + 300 s + 0.55 s. Mapped through our own getSlot history (a fresh
        observation first, so the history reaches the slot); if the history cannot map it, the current slot estimate stands in."""
        self.refresh_slot()
        now = self.now_ms()
        plan = pos["h5"]["plan"]
        sps = self.slots.measured_sps(now) or plan["sps"]
        wall, src = self.slots.wall_of_slot(landed_slot, sps), "slot_map"
        if wall is None:
            est = self.slots.est(now, sps)
            wall, src = (now - int((est - landed_slot) * sps * 1000)) if est is not None else now, "estimate"
        wall = min(wall, now)
        new = c1nf_exit_plan(landed_slot, sps, self.h5, wall).public()
        pos["h5"]["plan"] = new
        pos["h5"]["anchor"] = {"landed_slot": landed_slot, "wall_ms": wall, "source": src}
        if mint in self.counters.plans:
            self.counters.plans[mint]["plan"] = new  # durable: a restart recovers the landing-anchored plan
            self.counters.save(self.counters_path)
        self.save()
        self._log("exit_anchored", mint, landed_slot=landed_slot, anchor_wall_ms=wall, source=src, sps=round(sps, 5), exit_slot=new["exit_slot"],
                  land_slot=new["land_slot"], send_wall_ms=new["send_wall_ms"], escalate_wall_ms=new["escalate_wall_ms"], deadline_wall_ms=new["deadline_wall_ms"])

    @pe.critical
    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        pick_id = ((p.get("h5") or self.counters.plans.get(mint) or {}).get("trigger") or {}).get("pick_id")
        super()._finish_buy(mint, p, m)  # H5's: books the fill, the SD-to-landing median halt, the out-of-rule entry
        pos = self.state.open.get(mint)
        if pos is not None and m.get("slot") and (pos.get("h5") or {}).get("plan"):
            self._reanchor(mint, pos, int(m["slot"]))
        self._resolve_pick(pick_id, pos is not None, None if pos is not None else "buy_failed")

    @pe.critical
    def _resolve_expired(self, mint: str, p: dict[str, Any]) -> None:
        pick_id = ((p.get("h5") or {}).get("trigger") or {}).get("pick_id") if p.get("kind") == "buy" else None
        super()._resolve_expired(mint, p)
        self._resolve_pick(pick_id, False, "buy_expired")

    # -- exit ---------------------------------------------------------------------------------------------------------------------
    def _maybe_replan(self, pos: dict[str, Any], now: int) -> dict[str, Any] | None:
        """As H5's, on the landing anchor: when the measured slot rate moved more than 0.1% the plan's slots are rebuilt from it; the wall stages hold."""
        plan = pos["h5"]["plan"]
        m = self.slots.measured_sps(now)
        if pos["h5"].get("no_plan") or m is None or not (h5.SPS_MIN < m < h5.SPS_MAX) or abs(m / plan["sps"] - 1.0) <= h5.REPLAN_TOLERANCE:
            return None
        new = c1nf_exit_plan(plan["s0_slot"], m, self.h5, plan.get("s0_wall_ms")).public()
        self._log("plan_recomputed", pos["mint"], old_sps=plan["sps"], new_sps=round(m, 5), send_slot=new["send_slot"], deadline_slot=new["deadline_slot"])
        pos["h5"]["plan"] = new
        self.save()
        return new

    def _latch(self, name: str, **detail: Any) -> None:
        """H5's latch, except the stuck_position H5's exit_tick latches at the plan's deadline (why `not_sold_by_deadline`). For C1-NF that
        deadline is the emergency sell at landing + 370 s, not the stuck line: DEC-026 section 7 rule 4 latches at landing + 600 s (_stuck_tick).
        The emergency sell itself is H5's and unchanged; here the deadline is only ledgered."""
        if name == STUCK_HALT and detail.get("why") == H5_DEADLINE_STUCK_WHY:
            self._log("emergency_deadline_passed", str(detail.get("position_mint") or ""),
                      **{k: v for k, v in detail.items() if k not in ("position_mint", "why")}, stuck_s=STUCK_S)
            return
        super()._latch(name, **detail)

    @pe.critical
    def exit_tick(self, now: int) -> None:
        super().exit_tick(now)  # H5's exit scheduler, unchanged (the sell ladder, the emergency sell at landing + 370 s)
        self._stuck_tick()

    def _stuck_tick(self) -> None:
        """DEC-026 section 7 rule 4: a position not closed by landing + 600 s latches stuck_position (new buys stop until --clear-halt) and
        alerts. Once per position. HALT freezes this as it freezes H5's exit_tick. An abandoned position counts: it is not closed. A stand-in
        plan (H5's position_without_plan) has no landing to count from and is H5's alert already (stuck_due is never true for it)."""
        if self._halt_present():
            return
        now = self.now_ms()
        for mint, pos in list(self.state.open.items()):
            h = pos.get("h5") or {}
            plan = h.get("plan")
            if not plan or h.get("no_plan") or pos.get("c1nf_stuck_latched"):
                continue
            est = self.slots.est(now, plan["sps"])
            if stuck_due(plan, est, now):
                self._latch(STUCK_HALT, position_mint=mint, why="not_closed_by_landing_600s", est_slot=est,
                            stuck_slot=plan["s0_slot"] + int(round(STUCK_S / plan["sps"])), landing_slot=plan["s0_slot"],
                            sell_attempts=pos.get("sell_attempts", 0), abandoned=bool(pos.get("abandoned")), sell_pending=mint in self.state.pending)
                pos["c1nf_stuck_latched"] = True  # after the latch is on disk: a crash in between re-latches (a no-op), never skips it
                self.save()

    def _balance_refusal(self, now: int) -> str | None:
        """H5's balance guard (h5.balance_need, unchanged) with the per-exit reserve raised to c1nf_exit_reserve: one escalated send of this
        profile is more than H5's 1,000,000."""
        bal = self._balance_value(now)
        if bal is None:
            return "balance_unreadable"
        pend_stake = sum(int(p.get("spend") or 0) for p in self.state.pending.values() if p["kind"] == "buy")
        n_open = len(self.state.open)
        need = h5.balance_need(self.h5.stake_lamports, self.h5.buy_priority_lamports, n_open, pend_stake, self.h5.wallet_floor_lamports)
        need += (n_open + 1) * (c1nf_exit_reserve(self.h5) - h5.SELL_RESERVE_LAMPORTS)
        return None if bal >= need else "balance_floor"

    def _note_exit(self, mint: str, when_ms: int) -> None:
        self.extra.last_exit_ms[mint] = when_ms
        self._save_extra()
        self._log("exit_recorded", mint, exit_ms=when_ms, cooldown_until_ms=when_ms + COOLDOWN_MS)

    @pe.critical
    def _finish_sell(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        super()._finish_sell(mint, p, m)
        if mint not in self.state.open:  # the position is closed (a landed sell that failed leaves it open)
            now = self.now_ms()
            mapped = self.slots.wall_of_slot(int(m["slot"])) if m.get("slot") else None
            self._note_exit(mint, min(now, mapped) if mapped is not None else now)

    def _dry_sell(self, mint: str, pos: dict[str, Any], est: int, emergency: bool) -> None:
        super()._dry_sell(mint, pos, est, emergency)
        self._note_exit(mint, self.now_ms())

    def _note_sell_landing(self, mint: str, plan: dict[str, Any], landed_slot: int, emergency: bool) -> None:
        """DEC-026 section 7 rule 5: exit timing is REPORTED AND ALERTED, never a halt (C1-NF has no exit cliff). A sell landing after landing +
        305 s is late; more than 10% late over the last 20 landed sells (at least 10) alerts once per crossing. H5's 5% latch is not used."""
        c = self.counters
        c.plans.pop(mint, None)  # the position is closed
        c.sells_landed += 1
        wall0 = plan.get("s0_wall_ms")
        landing_wall = self.slots.wall_of_slot(landed_slot, plan["sps"]) if wall0 is not None else None
        if landing_wall is not None and plan.get("late_wall_ms") is not None:
            landing_s, late = (landing_wall - wall0) / 1000.0, landing_wall > plan["late_wall_ms"]
        else:
            landing_s, late = (landed_slot - plan["s0_slot"]) * plan["sps"], landed_slot > plan["late_slot"]
        c.sells_late += 1 if late else 0
        self._log("exit_landing", mint, landed_slot=landed_slot, exit_slot=plan["exit_slot"], land_slot=plan["land_slot"],
                  error_slots=landed_slot - plan["land_slot"], late=late, emergency=emergency, landing_s=round(landing_s, 3))
        c.save(self.counters_path)
        ex = self.extra
        ex.late_window = [*ex.late_window, 1 if late else 0][-LATE_SELL_WINDOW:]
        n, k = len(ex.late_window), sum(ex.late_window)
        over = n >= LATE_SELL_MIN_N and k / n > LATE_SELL_ALERT_FRAC
        if over and not ex.late_alert_on:
            self._alert("exit_late_share_gt_10pct", "", late=k, window=n, total_late=c.sells_late, total_landed=c.sells_landed)
        ex.late_alert_on = over
        self._save_extra()

    # -- feed rows ----------------------------------------------------------------------------------------------------------------
    def _newest(self, glob: str) -> Path | None:
        """The newest hourly file of one stream in the shadow's directory (re-globbed at most once a second)."""
        d = self.intents_path
        if not d.is_dir():
            return None  # the shadow's output is a directory of three streams; a plain file is not this contract
        now = self.now_ms()
        ms, path = self._globs.get(glob, (None, None))
        if ms is None or now - ms >= 1_000:
            files = sorted(d.glob(glob))
            path = files[-1] if files else None
            self._globs[glob] = (now, path)
        return path

    @staticmethod
    def _tail_stream(path: Path, cur: Path | None, st: Any) -> list[str]:
        """New lines of one stream; on an hour roll the previous file is drained first, then the new one is read from its start."""
        lines: list[str] = []
        if cur is not None and path != cur:
            while True:
                chunk = h5.tail_lines(cur, st)
                if not chunk:
                    break
                lines += chunk
            st.inode = None
        return lines + h5.tail_lines(path, st)

    @staticmethod
    def _rows(lines: list[str]) -> list[dict[str, Any]]:
        out = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out

    def _events_tick(self) -> None:
        path = self._newest(EVENTS_GLOB)
        if path is None:
            return
        rows = self._rows(self._tail_stream(path, self._ev_path, self._ev_tail))
        self._ev_path = path
        for row in rows:
            if row.get("type") == HEARTBEAT_TYPE:
                self._on_heartbeat(row)
            elif row.get("type") == GAP_TYPE:
                self._on_gap(row)

    def _outcomes_tick(self) -> None:
        path = self._newest(OUTCOMES_GLOB)
        if path is None:
            return
        before = (self._out_tail.started, self._out_tail.offset, self._out_tail.inode, self._out_path)
        rows = self._rows(self._tail_stream(path, self._out_path, self._out_tail))
        self._out_path = path
        # The rows are processed BEFORE the new offset is persisted (each on_outcome saves the extra with the OLD otail): a crash in here re-reads
        # these rows at the restart and loses none. A re-read outcome is a repeat (the first outcome of a pick stands, a repeat is counted
        # outcomes_unmatched and is not evidence), so a replay cannot double the monitor's evidence; only the count-only tallies can repeat.
        for row in rows:
            out, bad = parse_outcome(row)
            if out is not None:
                self.on_outcome(*out)
            elif bad == "outcome_unpriced":
                self.extra.outcomes_unpriced += 1
                self._save_extra()
            elif bad:
                self._count_only(bad)  # a malformed outcome: counted, never a per-mint row
        if (self._out_tail.started, self._out_tail.offset, self._out_tail.inode, self._out_path) != before:
            self.extra.otail = {"path": str(path), "started": self._out_tail.started, "offset": self._out_tail.offset, "inode": self._out_tail.inode}
            self._save_extra()

    def _pick_row(self, row: dict[str, Any]) -> C1NFPick | None:
        pick, bad = parse_pick(row)
        if pick is not None or not bad:
            return pick
        if bad in ("pre_window", "bad_pick:suppressed"):
            self._count_only(bad)
            return None
        mint = row.get("mint")
        if isinstance(mint, str) and _B58.match(mint) and self._seal_reason(_MintOnly(mint), self.now_ms()):
            self._count_only("seal_bad_pick")  # a sealed mint's bad pick: no per-mint row either
            return None
        self._bad_intent(row, bad)
        return None

    def intent_tick(self) -> int:
        if self._crit:
            return 0
        self._events_tick()  # heartbeats and gaps first: a gap written before a pick holds it
        path = self._newest(PICKS_GLOB)
        st = self.state
        if path is None:
            self._signals_absent()
            n_picks = 0
        else:
            rolled = self._tail_path is not None and path != self._tail_path
            lines: list[str] = []
            try:
                ps = path.stat()
                idle = not rolled and st.started and st.inode == ps.st_ino and ps.st_size == st.offset
            except FileNotFoundError:
                idle = not rolled
            if not idle:
                before = (st.started, st.offset, st.inode)
                lines = self._tail_stream(path, self._tail_path, st)
                self._tail_path = path
                if self.counters.tail_path != str(path):
                    self.counters.tail_path = str(path)
                    self.counters.save(self.counters_path)
                if (st.started, st.offset, st.inode) != before:
                    self.save()  # the offset first: a crash mid-pick must not replay it
            seen = self.now_ms()
            picks = [p for p in (self._pick_row(r) for r in self._rows(lines) if r.get("type") == PICK_TYPE) if p is not None]
            for pick in picks:  # the buy path is the latency path: outcomes after
                self.handle_pick(pick, seen)
            n_picks = len(picks)
        self._outcomes_tick()
        return n_picks

    # -- the H5 entry points that make no sense here are closed, not inherited ----------------------------------------------------------
    def handle_trigger(self, *_a: Any, **_k: Any) -> None:  # type: ignore[override]
        raise RuntimeError("the C1-NF executor takes c1nf_pick records (handle_pick), not H5 triggers")

    def on_boost_row(self, *_a: Any, **_k: Any) -> None:  # type: ignore[override]
        raise RuntimeError("the C1-NF executor has no BOOST rules")


# --- CLI ------------------------------------------------------------------------------------------------------------------------


def status_report(cfg: dict[str, Any]) -> str:
    """Counters, limits and kill-file state from disk. No key, no RPC."""
    lim = c1nf_limits(cfg)
    sd0 = Path(cfg["state_dir"])
    lines = [f"rule={RULE_ID} stake_sol={lim.stake_lamports / pe.LAMPORTS:.3f} max_open={lim.max_open} max_trades_per_day={lim.max_trades_per_day}",
             f"stop_file={(sd0 / 'STOP').exists()} halt_file={(sd0 / 'HALT').exists()} live_ok={c1nf_live_ok_valid() or 'valid'} ({LIVE_OK_PATH}) "
             f"exp025_part1={exp025_part1_present(h5.repo_root())}"]
    for mode in (DRYRUN, LIVE):
        sd = sd0 / mode
        sp = pe.state_path_for(sd, LIVE)
        if not sp.exists():
            lines.append(f"[{mode}] no state")
            continue
        st = pe.State.load(sp, LIVE)
        c = h5.H5Counters.load(sd / "h5-counters.json", mode) if (sd / "h5-counters.json").exists() else h5.H5Counters(run_mode=mode)
        ex = C1NFExtra.load(sd / "c1nf-extra.json")
        stats = monitor_stats(ex)
        today = c.days.get(h5.day_key(int(time.time() * 1000)), {})
        lines.append(f"[{mode}] attempts={st.attempts}/{lim.max_attempts} realized_sol={st.realized_lamports / pe.LAMPORTS:.6f} open={len(st.open)}/{lim.max_open} "
                     f"pending={len(st.pending)} today_trades={today.get('trades', 0)}/{lim.max_trades_per_day} today_realized_sol={today.get('realized', 0) / pe.LAMPORTS:.6f} "
                     f"halts={sorted(c.halts)} seal_skips={c.seal_skips} sells_landed={c.sells_landed} sells_late={c.sells_late} "
                     f"monitor_n={stats['n']} monitor_unfilled={stats['n_unfilled']} monitor_gap_pp={stats['gap_pp']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="C1-NF executor (dry run by default; live needs config mode AND --live AND LIVE_OK)")
    ap.add_argument("--config", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="the default: build and simulate, never sign or send")
    g.add_argument("--live", action="store_true", help=f'also needs config "mode": "live", {LIVE_OK_PATH} and {EXP025_PART1} in the tree')
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--clear-halt", metavar="NAME")
    ap.add_argument("--mark-closed", metavar="MINT", help="offline: reconcile a position closed by hand; needs --sig and the lock (refused while the unit runs)")
    ap.add_argument("--sig", metavar="SIGNATURE", help="the transaction that sold --mark-closed's mint")
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    ap.add_argument("--rpc-env", metavar="VARNAME", help="dry run only: the NAME of an environment variable holding the RPC URL. Refused with --live")
    args = ap.parse_args(argv)
    if args.rpc_env and args.live:
        ap.error("--rpc-env is allowed with --dry-run only: live reads HELIUS_API_KEY from the environment and never takes a URL")
    if args.rpc_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.rpc_env):
        ap.error("--rpc-env takes the NAME of an environment variable, not a URL")
    cfg = json.loads(Path(args.config).read_text())
    try:
        c1nf_limits(cfg)
    except ValueError as exc:
        raise SystemExit(f"limits refused: {exc}") from None
    if args.status:
        print(status_report(cfg))
        return 0
    if args.mark_closed:
        if not args.sig:
            ap.error("--mark-closed needs --sig SIGNATURE")
        url = (os.environ.get(args.rpc_env) or "").strip() if args.rpc_env else None
        if args.rpc_env and not url:
            raise SystemExit(f"--rpc-env {args.rpc_env}: that environment variable is not set")
        rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(url, args.env_file)), rps=float(cfg.get("rps", 8.0)), max_rps=h5.H5_MAX_RPS)
        return h5.mark_closed(cfg, args.mark_closed, args.sig, rpc)  # same state-dir files and lock as H5's, under the C1-NF state dir
    mode, warn = pe.resolve_mode(cfg.get("mode", DRYRUN), args.live and not args.dry_run)
    if args.clear_halt:
        return h5.clear_halt(cfg, args.clear_halt, mode)
    if warn:
        print(f"c1nf_executor WARNING {warn}", flush=True)
    lock_fd = h5.acquire_lock(Path(cfg["state_dir"]) / "h5-executor.lock")  # the name H5's --mark-closed / --clear-halt look for
    url: str | None = None
    try:
        oracle = StaleCheckedPickOracle(cfg["pick_file"]) if cfg.get("pick_file") else None  # #509's 60 s staleness (DEC-026 s9.1)
        if mode == LIVE:
            why = start_refusal(cfg)
            if why:
                print(f"c1nf_executor ALERT startup_refused {why}", flush=True)
                return 2
            rc = pe.startup_rpc_env_check(True)
            if rc:
                return rc
            pl.harden_process()  # before the key is read
            if "key_path" in cfg:
                raise SystemExit("live mode has no key path override: the key comes from the systemd credential only")
            if not (os.environ.get("HELIUS_API_KEY") or "").strip():
                print("c1nf_executor ALERT startup_refused rpc_key_missing", flush=True)
                return 2
            kp = pl.load_probe_key(credential_path())
            why = wallet_refusal(str(kp.pubkey()))
            if why:
                print(f"c1nf_executor ALERT startup_refused {why}", flush=True)
                return 2
            rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, args.env_file, use_env_file=False)), rps=float(cfg.get("rps", 8.0)), max_rps=h5.H5_MAX_RPS)
            ex = C1NFExecutor(rpc, cfg, kp, pick_oracle=oracle)
        else:
            url = (os.environ.get(args.rpc_env) or "").strip() if args.rpc_env else None
            if args.rpc_env and not url:
                raise SystemExit(f"--rpc-env {args.rpc_env}: that environment variable is not set")
            rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(url, args.env_file)), rps=float(cfg.get("rps", 8.0)), max_rps=h5.H5_MAX_RPS)
            ex = C1NFExecutor(rpc, cfg, None, pick_oracle=oracle, rpc_label=h5.url_label(url))
        shown = f" rpc={h5.url_label(url)}" if url else ""
        print(f"c1nf_executor mode={ex.run_mode} user={ex.user}{shown} paths={ex.paths} limits={ex.h5}", flush=True)
        if args.once:
            ex.tick()
            return 0
        ex.run_loop()
        return 0
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
