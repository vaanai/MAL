"""r4bt_c0_executor: the r4-b-T PROFILE of the r4-b executor (bot_id `r4bt-a1`). DRY by default. The LIVE port (QP-RULING-1106-r4bt-c0 build-list item 10, this module's
`LIVE_BUILT` / `R4btLivePort` / `run` CLI) sends only when ALL of: config mode "live", the `--live` flag, and every gate of `live_buy_problem` holds, re-checked immediately
before EVERY buy. A DRY config can never send (the DRY path gets a `B.DryPort`; a live port with a DRY config is refused at start).

Ruling: /data/mal/hunt-1008/ops/manager20/QP-RULING-1106-r4bt-c0.md build-list item 2 and findings F1, F2, and DEC-032 Amendment 9 (merged; sections 3, 4, 5 F4, 8 control).
Builds on tools/r4b_arena_executor.py (EXP-027's DRY bridge), which is NOT edited: the EXP-027 path (bot_id `r4b-exp027`) keeps its bytes and its behavior. This module is a
separate subclass and config.

WHAT THE PROFILE ADDS (each is a refusal path with a test in tools/test_r4bt_c0_executor.py)
  DECLOG     the bridge tails `<root>/decisions/r4b-exp027/` (DECLOG_ARM), r4-b-T's declog arm, not `decisions/r4bt-a1/` (DEC-031 Am.27 item 6; QP-RULING-1106-r4bt-prereg-risks
             section 3: `r4bt-a1` <-> arm `r4b-exp027`). bot_id, state, logs, LIVE_OK and the owner file keep `r4bt-a1`. For a real (non-synthetic) root, config `root` must be
             the declog root of the registered change_id: a path with the change_id as a component (`.../r4b-dry/<change_id>/declog`).
  PIN        bot_id `r4bt-a1`, W (frozen_sha) and the registered change_id come from config and must equal the code pins (REGISTERED_*); the declog `start` row must repeat
             W (the bridge latches frozen_sha_mismatch) AND the config change_id (this profile latches `change_id_mismatch`). a2: no buy otherwise. A successor run at W
             (Am.27 item 20) needs its change_id added to REGISTERED_CHANGE_IDS in a reviewed PR; no other host, change_id or sha ever feeds the sender (a1).
  F1 TWIN    `twin_round_trip` (tools.r4b_book.round_trip) is REFUSED for r4-b-T (Am.27 item 24). This module imports neither tools.r4b_book nor round_trip (a test reads
             the source). Twins come ONLY from the twin writer's files in config `twin_dir` (the twin builder's schema, PR #1243,
             /data/mal/hunt-1008/ops/reports/2026-11-06/r4bt-c0-twin-interface.md). The executor reads:
               twins.run.json  schema r4bt_c0_twin_run_v1, status "ok", w_sha == W (the twin VERIFIES its modules against W; the gate compares), engine_sha256 ==
                               ENGINE_SHA256, run_ms within TWIN_ROW_GRACE_MS, heartbeat_ms fresh when stamped, twins_sha256 == sha256(twins.jsonl)
               twins.jsonl     schema r4bt_c0_twin_row_v1; from each row `order_id` and `entry_leg.{corrected_slot, sps_entry}` (the F2 origin)
             and refuses a buy unless that gate passes (Am.9 section 3 item 10: the HV4 `twin_gate` + grace pattern; after TWIN_ROW_GRACE_MS an order whose buy landed needs
             its entry-stage row).
  F2 ORIGIN  Am.9 section 4: d_land = buy_landed_slot - the twin row's entry_leg.corrected_slot (the decision row's `ms` on the tape's slot clock, plus d). k = ceil(L / sps_entry)
             via tools.arena_stack.k_slots(L, sps_entry, "ceil") with each trip's OWN sps_entry. The sd(T) figures of EXP-027 are printed beside it (`diag_sdT`), never decide.
  A3         r4-b-T's OWN regime (Am.9 F4; DEC-031 Am.27 item 12; DEC-032 section 5 item 1 raw-record checks): a root-owned CARRY file (config a3_carry_path), JSON, ONE object,
             schema `r4bt_a3_carry_v1`: {"schema", "bot_id", "change_id", "run_utc" (YYYY-MM-DDTHH:MM:SSZ), "verdict" (tools/r4bt_a3.py: NONE | RECORD_ONLY clear; anything
             else latches), "halt": {"flags": {<flag>: {"halt": bool, "evaluated": bool}}}, "warn": {"flags": {"program_changed": {"warn": bool}}}} (the monitor record's
             shape). Missing, unreadable, non-root-owned, malformed, a wrong identity, a run_utc older than 36 h or 300 s in the future, no halt flags, any flag other than the five
             non-halting ones set / unevaluated / malformed, program_changed warn, or a verdict HALT / ERROR / NEEDS_CHECK: LATCH `r4bt_a3` (new buys stop, exits run). The five
             (boost_disabled, boost_share_low, boost_last_slice_early, boost_budget_or_slices_changed, synthetic_share_high) are skipped and counted. DEC-032 Amendment 3's
             pooled guard does NOT apply to r4bt-a1 (Am.9 F4): no a3-applied.jsonl is read.
  READ       config read_result_sha256 and qp_comment_id must both equal the fields of the read-record file (config read_record_path, JSON, `r4bt_read_record_v1`:
             {"schema","bot_id","change_id","status":"PASS","read_result_sha256","qp_comment_id","qp_verdict":"OK"}), else the bridge reads no decision row (latch
             `r4bt_read_record`) and a start on a non-synthetic root is refused (`start_refusal`). A synthetic root (SYNTHETIC_FIXTURE_ROOT marker) is exempt, DRY only.
  OWNER      the owner's confirmation file (config owner_file): ROOT-OWNED, exactly one line `OWNER_CANARY_CONFIRMED: bot_id=r4bt-a1`; per-bot LIVE_OK (content exactly
             `r4bt-a1`) and the per-bot TIER file reading exactly `T0` (DEC-032 section 3, Am.9 section 9 step 5). LIVE gates: `live_gate_problems` lists them; a LIVE start is refused anyway (LIVE_BUILT False), and the DRY bridge
             records which are open at its start (`r4bt_start` log row).
  CAPS       the DEC-032 section 3 C0 caps are REQUIRED in config and VALIDATED, not clipped: stake <= 0.05 SOL, max_open <= 2, max_trades_per_day <= 30, daily_loss <= 0.10 SOL,
             total_loss <= 0.20 SOL (the bridge counts open exposure in the total stop), funded_lamports in [0.35, 1.00] SOL. Above = refused.
  FEE        Am.9 section 8: config send_fee_lamports defaults to the registered 505,000 per send. A lower value is refused unless an explicit reviewed `fee_override`
             {lamports, fill_fee, owner_line, qp_comment_id} names that exact value; a higher value is always refused. Reserves in the bridge stay at 505,000 (conservative).
  PARITY GATE  every buy is refused unless `<state_dir>/r4bt-parity-state.json` (JSON object) exists, is a regular file, has schema r4bt_parity_state_v1, int `exit_code` 0, `stop_required` false, an int `at_ms` at most 60 s old (not
             future-stamped), and `ledger_sha256` / `attempts_sha256` equal to the sha256 of the CURRENT order ledger and attempts file (any change needs a new parity run before the next buy).
  A3 FRESHNESS the carry's `written_utc` (the carry writer's stamp) must be present and no older than config `a3_carry_max_age_ms` (default 2 h; at most 36 h), else LATCH
             (`a3_carry_written_stale` / `a3_carry_no_written_utc`): a stalled copy cannot serve a stale clean carry, whatever the record's own run_utc says.
  PARITY     (tools/r4bt_c0_parity.py, PR #1250) <state_dir>/STOP counts as STOP (new buys stop, exits run) whatever else is set. The ledger rows also carry
             live_buy_price / live_sell_price (SOL per token, the twin's Qe/Be convention), live_net_lamports (the trip's chain wallet delta), exit_landed_ms and gap_through,
             filled from the port events of the same names (buy_landed: live_buy_price; sell_landed: live_sell_price, live_net_lamports, landed_ms, gap_through). A DRY port
             leaves them null and the row has `fee_source: dry_default`: the parity tool must not refuse a DRY row for missing live fields (it should key on fee_source).
             A non-DRY event without them latches `r4bt_fill_unreported`. `r4bt-attempts.jsonl` (ATTEMPTS_NAME) holds one row per buy send outcome INCLUDING failures
             ({attempt_id "a:<order_id>", trip_id (landed only), decision_ms, landed, landed_ms, failed_or_expired, p_fail_press 0.0 = the flat 15 % floor of P2}).
  LEDGER     the executor writes its order ledger (`r4bt-order-ledger.jsonl` in the state dir, atomic rewrite, one row per SENT order) for the twin writer, with the ACTUAL
             entry_fee_lamports and exit_fee_lamports: the sum of the `fees_lamports` the port reports on each leg's events (every send, escalated resends included). A DRY
             port's events are marked `fee_source: dry_default` (the configured per-send fee); a non-DRY event without `fees_lamports` latches `r4bt_fee_unreported`.

Nothing here says r4-b-T makes money. r4-b-T has no read. No book has cleared the promotion gate. A C0 is an execution measurement, never gate evidence.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import arena_live_bridge as B
from tools import arena_stack as AS
from tools import hv4_live_c0 as HV
from tools import r4b_arena_executor as X
from tools.arena_declog import DecisionLog

LIVE_BUILT = True  # THE single constant of the live port (QP-RULING-1106-r4bt-c0 item 10). It flips only in a reviewed diff. True removes the `live_not_built` refusal and nothing else:
# a live send still needs config mode "live" + `--live` + every live gate (LIVE_OK, TIER T0, owner file, read record, A3 carry, twin, parity, caps) before EVERY buy.
BOT_ID = "r4bt-a1"
DECLOG_ARM = "r4b-exp027"  # r4-b-T's declog arm: <root>/decisions/r4b-exp027/ under $HOME/mal-data/r4b-dry/<change_id>/declog (DEC-031 Am.27 item 6; bot_id stays r4bt-a1)
REGISTERED_W = "17e3b05a7696900cda7fcf93e4af4008d8fa0184"  # SEALS-BRIEF r4-b-T reader line; the tree $HOME/mal-install/r4bt-W
REGISTERED_CHANGE_IDS = ("r4bt-a1-1107a",)  # a successor run at W (Am.27 item 20) is added here in its own reviewed PR
ENGINE_SHA256 = "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514"  # tools/arena_paper.py (Am.27 item 24, item 29 A6)

# --- DEC-032 section 3 C0 table (the same numbers as r4b_arena_executor.C0 / hv4_live_c0.C0_LIMITS; a test compares them)
C0_CAPS = dict(X.C0)
FUNDED_MIN_LAMPORTS, FUNDED_MAX_LAMPORTS = X.FUNDED_MIN_LAMPORTS, X.FUNDED_MAX_LAMPORTS

# --- latches / refusals of this profile
LATCH_PIN = "change_id_mismatch"
LATCH_A3 = "r4bt_a3"
LATCH_READ = "r4bt_read_record"
LATCH_TWIN_PIN = "r4bt_twin_pin"
TWIN_ROW_GRACE_MS = 9_000_000  # Am.9 section 3 item 10: >= 1 h + the writer's close / compaction / verification lag + one twin run (the twin reads closed verified hours only); 2.5 h is a declared choice, not measured
TWIN_INTERVAL_S_DEFAULT, TWIN_REPLAY_S_DEFAULT = 300, 300  # the twin wrapper sleeps 300 s between runs; the full-length replay time is NOT measured yet (a declared placeholder)
TWIN_HEARTBEAT_DEFAULT_MS = 2 * TWIN_INTERVAL_S_DEFAULT * 1000 + TWIN_REPLAY_S_DEFAULT * 1000  # 900 s: >= 2 x interval + replay (QP on #1246); the twin stamps heartbeat_ms at the START of a run
TWIN_FUTURE_SKEW_MS = 300_000
TWIN_MAX_BYTES = 32 * 1024 * 1024
TWIN_FINAL_NO_ENTRY = ("refused", "excluded")  # final twin statuses without an entry stage: counted `no_twin` for P7 (DEC-032 section 5), not a block
RUN_RECORD_NAME, TWINS_NAME = "twins.run.json", "twins.jsonl"
RUN_SCHEMA, TWIN_SCHEMA = "r4bt_c0_twin_run_v1", "r4bt_c0_twin_row_v1"
CARRY_SCHEMA, READ_SCHEMA = "r4bt_a3_carry_v1", "r4bt_read_record_v1"
CARRY_CLEAR = ("NONE", "RECORD_ONLY")  # the classifier verdicts that are not an item 12 hard halt
NOT_HALTING_FLAGS = ("boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed", "synthetic_share_high")  # Am.9 F4 / DEC-031 Am.27 item 12
REGISTERED_SEND_LAMPORTS = X.SEND_FEE_LAMPORTS  # 505,000: Am.9 section 8 "Send priority"
LEDGER_NAME = "r4bt-order-ledger.jsonl"
LATCH_FEE = "r4bt_fee_unreported"
LATCH_FILL = "r4bt_fill_unreported"
STOP_NAME = "STOP"  # <state_dir>/STOP, written by tools/r4bt_c0_parity.py (tools.arena_live_parity.write_stop); any entry at that path is a STOP
HEALTH_PATH_DEFAULT = "/var/lib/mal-r4b-c0/health.json"  # read by the watchdog (PR #1256): booleans and short enums ONLY, no order, price, mint, P&L or trade count
HEALTH_SCHEMA = "r4bt_c0_health_v1"
HEALTH_KEYS = ("schema", "at_ms", "latched", "latch_names", "stop_present", "live_ok_present", "tier", "dry")
LATCH_NAME_RE = re.compile(r"[a-z0-9_]{1,48}")
PARITY_STATE_NAME = "r4bt-parity-state.json"  # written by the parity run (tools/r4bt_c0_parity.py)
PARITY_SCHEMA = "r4bt_parity_state_v1"
PARITY_MAX_AGE_MS = 60_000  # a buy needs a parity result at most this old
PARITY_FUTURE_SKEW_MS = 5_000  # the parity state is written by a job on the SAME host clock (the job passes the real clock): a stamp more than ~5 s ahead is refused
PRICE_UNIT = "lamports per raw base-token unit"  # the twin pins Qe/Be (quote reserve lamports / base reserve raw units); live_buy_price / live_sell_price use the same unit (see fill_facts)
BASE_FEE_LAMPORTS = X.BASE_FEE_LAMPORTS
LATCH_FEE_OVER = "r4bt_fee_over_registered"
LATCH_BUY_ERROR = "r4bt_buy_error"  # an exception anywhere in the buy path: PERSISTED (the state file), so a restarted unit stays latched
HEALTH_MIN_INTERVAL_MS = 1_000  # health.json is rewritten at most once a second, except on a latch or a STOP change
A3_CARRY_MAX_AGE_DEFAULT_MS = 2 * 3_600_000  # written_utc bound: the carry copy is hourly, so 2 h; a stalled copy cannot serve a stale clean carry
ATTEMPTS_NAME = "r4bt-attempts.jsonl"  # per buy-send attempt rows (tools.arena_live_parity attempts schema), read by the parity tool's P2 via --attempts
FILE_MAX_BYTES = 1 << 20
A3_MAX_AGE_MS, A3_FUTURE_SKEW_MS = HV.A3_MAX_AGE_MS, HV.A3_FUTURE_SKEW_MS
OWNER_FILE_DEFAULT = "/etc/mal-r4bt-c0/r4bt-a1/CANARY_DEC.md"
LIVE_OK_DEFAULT = "/etc/mal-arena-live/r4bt-c0/r4bt-a1/LIVE_OK"
TIER_DEFAULT = "/etc/mal-arena-live/r4bt-c0/r4bt-a1/TIER"  # per bot, beside its LIVE_OK; content exactly T0
TIER_REQUIRED = "T0"
SHA64_RE = re.compile(r"[0-9a-f]{64}")
CHANGE_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

_PROFILE_KEYS = ("change_id", "twin_dir", "a3_carry_path", "read_record_path", "read_result_sha256", "qp_comment_id", "owner_file", "live_ok_path", "tier_path", "send_fee_lamports", "fee_override", "twin_interval_s", "twin_replay_s", "twin_heartbeat_max_ms", "a3_carry_max_age_ms", "health_path")


class Refused(ValueError):
    """A config or start refusal of the r4-b-T profile."""


class RouteRefused(RuntimeError):
    """F1: the r4b_book.round_trip route is refused for r4-b-T."""


def twin_round_trip(*_a: Any, **_k: Any) -> Any:
    """F1 (Am.27 item 24): `tools.r4b_book.round_trip` is refused for r4-b-T, as the deciding route and as a report-only figure. Twins come from the twin writer only."""
    raise RouteRefused("twin_round_trip / r4b_book.round_trip is refused for r4-b-T; twins come only from the twin writer's twins.jsonl and run record")


def _num(v: Any) -> bool:
    return X._num(v)


def _int(v: Any) -> bool:
    return X._int(v)


# --- config ---------------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class R4btConfig(X.R4bConfig):
    """The EXP-027 DRY config plus the r4-b-T profile fields (all required by `make_config`)."""

    change_id: str = ""
    twin_dir: str = ""
    a3_carry_path: str = ""
    read_record_path: str = ""
    read_result_sha256: str = ""
    qp_comment_id: str = ""
    owner_file: str = OWNER_FILE_DEFAULT
    live_ok_path: str = LIVE_OK_DEFAULT
    tier_path: str = TIER_DEFAULT
    send_fee_lamports: int = REGISTERED_SEND_LAMPORTS  # per send (priority + base): the registered 505,000 unless a reviewed override is present (Am.9 section 8)
    fee_override: Any = None
    twin_interval_s: int = TWIN_INTERVAL_S_DEFAULT  # the twin wrapper's sleep between runs
    twin_replay_s: int = TWIN_REPLAY_S_DEFAULT  # the declared full-length replay time (measure it in the DRY smoke; owed before install)
    twin_heartbeat_max_ms: int = TWIN_HEARTBEAT_DEFAULT_MS  # run-record heartbeat bound; floor 2 x interval + replay
    a3_carry_max_age_ms: int = A3_CARRY_MAX_AGE_DEFAULT_MS  # bound on the carry's written_utc
    health_path: str = HEALTH_PATH_DEFAULT  # the watchdog's health file


def twin_heartbeat_problem(interval_s: Any, replay_s: Any, max_ms: Any) -> str | None:
    """The run-record freshness bound must cover the twin's cycle: twin_heartbeat_max_ms >= 2 x twin_interval_s + twin_replay_s (QP on #1246). The twin stamps heartbeat_ms
    when a run STARTS and sleeps `interval` between runs, so a shorter bound refuses buys for most of every cycle."""
    if not all(_int(v) and v > 0 for v in (interval_s, replay_s, max_ms)):
        return "twin_interval_s, twin_replay_s and twin_heartbeat_max_ms must be positive ints"
    if max_ms < (2 * interval_s + replay_s) * 1000:
        return f"twin_heartbeat_max_ms {max_ms} is below 2 x twin_interval_s + twin_replay_s = {(2 * interval_s + replay_s) * 1000}"
    return None


def send_fee_problem(fee: Any, override: Any) -> str | None:
    """Am.9 section 8 "Send priority": the per-send fee stays the registered 505,000. A lower fee only with an explicit reviewed override naming that exact value, `fill_fee`
    (the FILL:fee text), `owner_line` and `qp_comment_id` (QP's sighting of >= 30 landed sends at that priority, p90 <= 4 slots). Anything else is refused."""
    if not _int(fee) or fee <= 0:
        return "send_fee_lamports must be a positive int"
    if fee == REGISTERED_SEND_LAMPORTS:
        return None if override is None else "fee_override given at the registered fee: remove it"
    if fee > REGISTERED_SEND_LAMPORTS:
        return f"send_fee_lamports {fee} is above the registered {REGISTERED_SEND_LAMPORTS}"
    if fee <= X.BASE_FEE_LAMPORTS:
        return "send_fee_lamports leaves no priority"
    ov = override
    if (not isinstance(ov, dict) or ov.get("lamports") != fee or not all(isinstance(ov.get(k), str) and ov[k].strip() for k in ("fill_fee", "owner_line"))
            or not isinstance(ov.get("qp_comment_id"), str) or not re.fullmatch(r"[0-9]{6,20}", ov["qp_comment_id"])):
        return f"send_fee_lamports {fee} is below the registered {REGISTERED_SEND_LAMPORTS}: needs a reviewed fee_override {{lamports, fill_fee, owner_line, qp_comment_id}}"
    return None


def make_config(d: Mapping[str, Any]) -> R4btConfig:
    """Fail closed: any pin that does not hold raises Refused. Caps are VALIDATED against DEC-032 section 3 (a value above is refused, not clipped) and required."""
    d = dict(d)
    mode = d.get("mode", B.DRY)
    if mode not in (B.DRY, B.LIVE):
        raise Refused("config mode must be 'dry' or 'live'")
    if mode == B.LIVE and not LIVE_BUILT:
        raise Refused("live_not_built: LIVE_BUILT is False, only mode 'dry' exists")
    if mode == B.LIVE and any(d.get(k) for k in ("stop_file", "halt_file")):
        raise Refused("live refused: stop_file / halt_file cannot be set in live (the wallet-wide files are pinned)")
    if d.get("bot_id") != BOT_ID:
        raise Refused(f"bot_id must be {BOT_ID}")
    if d.get("frozen_sha") != REGISTERED_W:
        raise Refused("frozen_sha is not the registered W of r4-b-T")
    cid = d.get("change_id")
    if not isinstance(cid, str) or not CHANGE_RE.fullmatch(cid) or cid not in REGISTERED_CHANGE_IDS:
        raise Refused("change_id is not a registered r4-b-T change_id")
    for k, cap in C0_CAPS.items():
        v = d.get(k)
        if not _num(v) or v <= 0:
            raise Refused(f"config {k} is required (positive number): the C0 caps are validated, not defaulted")
        if v > cap:
            raise Refused(f"config {k} {v} is above the DEC-032 section 3 C0 cap {cap}")
    funded = d.get("funded_lamports")
    if not _num(funded) or not FUNDED_MIN_LAMPORTS <= funded <= FUNDED_MAX_LAMPORTS:
        raise Refused("funded_lamports is required, 0.35 to 1.00 SOL")
    for k in ("twin_dir", "a3_carry_path", "read_record_path"):
        if not isinstance(d.get(k), str) or not os.path.isabs(d[k]):
            raise Refused(f"config {k} is required (absolute path)")
    if not isinstance(d.get("read_result_sha256"), str) or not SHA64_RE.fullmatch(d["read_result_sha256"]):
        raise Refused("config read_result_sha256 is required (64 hex)")
    if not isinstance(d.get("qp_comment_id"), str) or not re.fullmatch(r"[0-9]{6,20}", d["qp_comment_id"]):
        raise Refused("config qp_comment_id is required (digits)")
    hb_problem = twin_heartbeat_problem(d.get("twin_interval_s", TWIN_INTERVAL_S_DEFAULT), d.get("twin_replay_s", TWIN_REPLAY_S_DEFAULT),
                                        d.get("twin_heartbeat_max_ms", TWIN_HEARTBEAT_DEFAULT_MS))
    if hb_problem:
        raise Refused(hb_problem)
    if isinstance(d.get("root"), str) and not HV.synthetic_root(d["root"]) and d["change_id"] not in Path(d["root"]).parts:
        raise Refused(f"config root {d['root']} is not under the change_id root (.../r4b-dry/{d['change_id']}/declog)")
    if not isinstance(d.get("health_path", HEALTH_PATH_DEFAULT), str) or not os.path.isabs(d.get("health_path", HEALTH_PATH_DEFAULT)):
        raise Refused("config health_path must be an absolute path")
    age = d.get("a3_carry_max_age_ms", A3_CARRY_MAX_AGE_DEFAULT_MS)
    if not _int(age) or not 0 < age <= A3_MAX_AGE_MS:
        raise Refused(f"a3_carry_max_age_ms must be a positive int, at most {A3_MAX_AGE_MS} (36 h)")
    fee_problem = send_fee_problem(d.get("send_fee_lamports", REGISTERED_SEND_LAMPORTS), d.get("fee_override"))
    if fee_problem:
        raise Refused(fee_problem)
    base = {k: v for k, v in d.items() if k not in _PROFILE_KEYS and k not in ("bot_id", "mode")}
    try:
        cfg = X.make_config(base)  # the EXP-027 validator (bot_id defaults to r4b-exp027 there): pins W as 40 hex, slot_s, end_ms, no sl_backstop, floor from funded
    except ValueError as exc:
        raise Refused(str(exc)) from None
    if cfg.stake_lamports != d["stake_lamports"] or cfg.max_open != d["max_open"] or cfg.total_loss_lamports != d["total_loss_lamports"]:
        raise Refused("caps changed in validation")  # unreachable unless the EXP-027 validator changes: the caps are not clipped here
    extra = {k: d[k] for k in _PROFILE_KEYS if k in d}
    return R4btConfig(**{**{f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)}, "bot_id": BOT_ID, "mode": mode, **extra})


# --- safe small-file reads ------------------------------------------------------------------------------------------------------------------------


def _read_small(path: Any, root_checks: bool, max_bytes: int = FILE_MAX_BYTES, mode_mask: int = 0o022) -> tuple[bytes | None, str | None, os.stat_result | None]:
    """(bytes, problem, stat). Regular file, not a link, single link, not group/other writable, owned by root (root_checks) or by root or the running user (tests),
    opened O_NOFOLLOW, same inode as the lstat, within max_bytes."""
    try:
        ls = os.lstat(path)
    except FileNotFoundError:
        return None, "missing", None
    except OSError:
        return None, "unreadable", None
    if stat.S_ISLNK(ls.st_mode) or not stat.S_ISREG(ls.st_mode) or ls.st_nlink != 1:
        return None, "not_regular", None
    if ls.st_mode & mode_mask or ls.st_uid != 0 and (root_checks or ls.st_uid != os.getuid()):
        return None, "owner_mode", None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None, "unreadable", None
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino) != (ls.st_dev, ls.st_ino) or fs.st_size > max_bytes:
            return None, "swapped_or_large", None
        data = b""
        while len(data) <= max_bytes:
            b = os.read(fd, 1 << 16)
            if not b:
                break
            data += b
    except OSError:
        return None, "unreadable", None
    finally:
        os.close(fd)
    return (data, None, fs) if len(data) <= max_bytes else (None, "swapped_or_large", None)


def _json_obj(path: Any, root_checks: bool, what: str, last_line: bool = False, mode_mask: int = 0o022) -> tuple[dict[str, Any] | None, str | None, os.stat_result | None]:
    data, why, st = _read_small(path, root_checks, mode_mask=mode_mask)
    if why:
        return None, f"{what}_{why}", None
    try:
        text = data.decode("utf-8")
        o = json.loads([ln for ln in text.splitlines() if ln.strip()][-1] if last_line else text)
    except (ValueError, IndexError):
        return None, f"{what}_malformed", None
    return (o, None, st) if isinstance(o, dict) else (None, f"{what}_malformed", None)


def _iso_ms(v: Any) -> int | None:
    try:
        return int(datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


# --- read-record gate -----------------------------------------------------------------------------------------------------------------------------


def read_record_problem(cfg: R4btConfig, root_checks: bool) -> str | None:
    """None only when the read-record file shows a PASS for this bot and change_id, QP OK, and BOTH its read_result_sha256 and its qp_comment_id equal the config's."""
    rec, why, _ = _json_obj(cfg.read_record_path, root_checks, "read_record")
    if why:
        return why
    if rec.get("schema") != READ_SCHEMA or rec.get("bot_id") != BOT_ID or rec.get("change_id") != cfg.change_id:
        return "read_record_identity"
    if rec.get("status") != "PASS" or rec.get("qp_verdict") != "OK":
        return "read_record_not_pass_ok"
    if rec.get("read_result_sha256") != cfg.read_result_sha256:
        return "read_record_sha_mismatch"
    if str(rec.get("qp_comment_id")) != cfg.qp_comment_id:
        return "read_record_qp_mismatch"
    return None


# --- twin gate (F1) -------------------------------------------------------------------------------------------------------------------------------


def twin_gate_problem(twin_dir: str, now_ms: int, orders: Mapping[str, Mapping[str, Any]] | None, cfg: R4btConfig, root_checks: bool = True,
                      counts: dict[str, int] | None = None) -> str | None:
    """Fail closed (Am.9 section 3 item 10: the HV4 twin_gate + grace pattern; the twin builder's schema, PR #1243). The run record must exist and have schema
    r4bt_c0_twin_run_v1, status "ok", this W in `w_sha` (the twin VERIFIES its modules against W; the gate compares it to the pinned W), the engine sha256 pin, run_ms
    within TWIN_ROW_GRACE_MS, an int heartbeat_ms no older than cfg.twin_heartbeat_max_ms, and `twins_sha256` equal to the sha256 of twins.jsonl. Then every order whose buy
    landed more than TWIN_ROW_GRACE_MS ago must have an entry-stage row (entry_leg not null) in twins.jsonl (before the entry leg is observable the run record is the
    only twin). A pin mismatch is `twin_gate:pin_mismatch` (the bridge latches it). Returns the refusal label or None."""
    rec, why, _ = _json_obj(os.path.join(twin_dir, RUN_RECORD_NAME), root_checks, "twin_run_record")
    if why:
        return "twin_gate:" + why
    if rec.get("schema") != RUN_SCHEMA:
        return "twin_gate:run_record_schema"
    if rec.get("w_sha") != cfg.frozen_sha or rec.get("engine_sha256") != ENGINE_SHA256:
        return "twin_gate:pin_mismatch"
    if rec.get("status") != "ok":
        return "twin_gate:status_not_ok"
    run = rec.get("run_ms")
    if not _int(run):
        return "twin_gate:run_ms_missing"
    if run - now_ms > TWIN_FUTURE_SKEW_MS:
        return "twin_gate:run_ms_future"
    if now_ms - run > TWIN_ROW_GRACE_MS:
        return "twin_gate:stale"
    hb = rec.get("heartbeat_ms")
    if not _int(hb):
        return "twin_gate:heartbeat_missing"  # the twin always writes it: a record without one is malformed
    if hb - now_ms > TWIN_FUTURE_SKEW_MS:
        return "twin_gate:heartbeat_future"
    if now_ms - hb > cfg.twin_heartbeat_max_ms:
        return "twin_gate:heartbeat_stale"
    data, why, _ = _read_small(os.path.join(twin_dir, TWINS_NAME), root_checks, TWIN_MAX_BYTES)
    if why:
        return "twin_gate:twins_unreadable"
    if rec.get("twins_sha256") != hashlib.sha256(data).hexdigest():
        return "twin_gate:twins_sha_mismatch"  # the run record is written AFTER twins.jsonl and names its sha256
    due = [oid for oid, o in (orders or {}).items() if _int(o.get("buy_landed_ms")) and now_ms - int(o["buy_landed_ms"]) > TWIN_ROW_GRACE_MS]
    if due:
        have = _twin_rows(data)
        for oid in due:
            row = have.get(oid)
            if row is None or (not isinstance(row.get("entry_leg"), dict) and row.get("status") not in TWIN_FINAL_NO_ENTRY):
                return "twin_gate:row_missing"  # an ABSENT row, or a pending_tape row without its entry stage, past the grace
            if not isinstance(row.get("entry_leg"), dict) and counts is not None:
                counts["no_twin"] = counts.get("no_twin", 0) + 1  # a twin that refused or excluded the buy: a P7 no_twin, never a block
    return None


def _twin_rows(data: bytes) -> dict[str, dict[str, Any]]:
    """order_id -> row, for the well-formed rows (schema r4bt_c0_twin_row_v1) of twins.jsonl bytes. A malformed line is skipped, never trusted."""
    out: dict[str, dict[str, Any]] = {}
    for ln in data.decode("utf-8", "replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("schema") == TWIN_SCHEMA and isinstance(r.get("order_id"), str):
            out[r["order_id"]] = r
    return out


def twin_entry_legs(twin_dir: str, root_checks: bool = True) -> dict[str, dict[str, Any]]:
    """order_id -> {corrected_slot, sps_entry} from the entry-stage rows of twins.jsonl (the F2 origin). Empty when the file is unreadable; rows whose fields are not
    ints / positive numbers are left out (the order is then unmeasured, never guessed)."""
    data, why, _ = _read_small(os.path.join(twin_dir, TWINS_NAME), root_checks, TWIN_MAX_BYTES)
    if why:
        return {}
    out = {}
    for oid, r in _twin_rows(data).items():
        leg = r.get("entry_leg")
        if isinstance(leg, dict) and _int(leg.get("corrected_slot")) and _num(leg.get("sps_entry")) and leg["sps_entry"] > 0:
            out[oid] = {"corrected_slot": leg["corrected_slot"], "sps_entry": float(leg["sps_entry"])}
    return out


# --- A3 regime (Am.9 F4: DEC-031 Am.27 item 12 hard halts, and DEC-032 section 5 item 1 raw-record checks) -----------------------------------------------------


def a3_problem(cfg: R4btConfig, now_ms: int, root_checks: bool = True) -> tuple[str | None, list[str]]:
    """(latch, skipped). Reads the root-owned CARRY file (module docstring). Latch (new buys stop, exits run) when: the carry is missing, unreadable, linked, not root-owned,
    oversize or malformed; has another bot_id / change_id; run_utc is missing, older than 36 h or more than 300 s in the future; its classifier verdict is HALT, ERROR,
    NEEDS_CHECK or unknown (item 12 hard halts, earliest instant); `halt.flags` is missing or empty, or any flag other than the five non-halting ones is set, not `evaluated: true` or
    malformed; or `warn.flags.program_changed.warn` is true (section 5 item 1 is stricter than the read: any pins_changed and any program_changed latch at once). The five
    non-halting flags (boost_*, synthetic_share_high) are skipped and returned in `skipped` when set (the bridge counts them for the tie-out). Amendment 3's pooled guard does
    NOT apply to r4bt-a1 (Am.9 F4): no a3-applied.jsonl is read."""
    car, why, _ = _json_obj(cfg.a3_carry_path, root_checks, "a3_carry")
    if why:
        return why, []
    if car.get("schema") != CARRY_SCHEMA or car.get("bot_id") != BOT_ID or car.get("change_id") != cfg.change_id:
        return "a3_carry_identity", []
    rec_ms = _iso_ms(car.get("run_utc"))
    if rec_ms is None:
        return "a3_carry_no_run_utc", []
    if rec_ms - now_ms > A3_FUTURE_SKEW_MS:
        return "a3_carry_future", []
    if now_ms - rec_ms > A3_MAX_AGE_MS:
        return "a3_carry_stale", []
    w_ms = _iso_ms(car.get("written_utc"))
    if w_ms is None:
        return "a3_carry_no_written_utc", []
    if w_ms - now_ms > A3_FUTURE_SKEW_MS:
        return "a3_carry_written_future", []
    if now_ms - w_ms > cfg.a3_carry_max_age_ms:
        return "a3_carry_written_stale", []
    v = car.get("verdict")
    if v not in CARRY_CLEAR:
        return f"a3_carry:{v}" if isinstance(v, str) else "a3_carry_verdict_unknown", []
    halt = car.get("halt")
    flags = halt.get("flags") if isinstance(halt, dict) else None
    if not isinstance(flags, dict) or not flags:
        return "a3_record_no_halt_flags", []
    skipped = sorted(k for k, f in flags.items() if k in NOT_HALTING_FLAGS and isinstance(f, dict) and f.get("halt") is True)
    bad = sorted(k for k, f in flags.items() if k not in NOT_HALTING_FLAGS and (not (isinstance(f, dict) and isinstance(f.get("halt"), bool)) or f["halt"] or f.get("evaluated") is not True))
    warn = car.get("warn")
    pc = ((warn.get("flags") or {}).get("program_changed")) if isinstance(warn, dict) and isinstance(warn.get("flags"), dict) else None
    if isinstance(pc, dict) and pc.get("warn") is True:
        bad.append("program_changed")
    return (("a3_flags:" + ",".join(bad)) if bad else None), skipped


# --- live gates (the owner's confirmation, LIVE_OK, TIER) -----------------------------------------------------------------------------------------


def live_gate_problems(cfg: R4btConfig, root_checks: bool = True) -> list[str]:
    """Every LIVE gate that is not met, in order. Empty = all met. Per bot: the owner's confirmation line, LIVE_OK (exactly the bot_id), and the per-bot TIER file reading exactly T0."""
    out = []
    root_check = (lambda p: None if root_checks is False else _root_label(p))
    why = HV.owner_line_problem(cfg.owner_file, BOT_ID, root_check)
    if why:
        out.append(why)
    data, why, _ = _read_small(cfg.live_ok_path, root_checks)
    if why:
        out.append("live_ok_" + why)
    elif HV.live_ok_problem(data.decode("utf-8", "replace"), BOT_ID):
        out.append("live_ok_other_book")
    tier, why = B.read_tier(Path(cfg.tier_path), root_checks, allowed=(TIER_REQUIRED,))
    if why:
        out.append(why)
    return out


def _root_label(p: Any) -> str | None:
    try:
        st = os.lstat(p)
    except OSError:
        return "unreadable"
    return None if st.st_uid == 0 and st.st_gid == 0 and not st.st_mode & 0o022 else "not_root_owned"


def start_refusal(cfg: R4btConfig, *, live: bool = False, root_checks: bool = True) -> str | None:
    """The start gate. A LIVE start (the `--live` flag AND config mode live) needs LIVE_BUILT, every live gate and the recorded PASS read, on any root (no synthetic exemption);
    the flag and the config mode must agree (a DRY config with --live, or a live config without it, is refused here, never half-live). A DRY start on a non-synthetic root
    needs the read record (a4: no start against the real declog before the screen PASS has QP's OK). Returns a refusal label or None."""
    if live and not LIVE_BUILT:
        return "live_not_built"
    if live != (cfg.mode == B.LIVE):
        return "live_flag_mode_mismatch"
    if live:
        gates = live_gate_problems(cfg, root_checks)
        if gates:
            return "live_gate:" + gates[0]
        why = read_record_problem(cfg, root_checks)
        return ("start_" + why) if why else None
    if not HV.synthetic_root(cfg.root):
        why = read_record_problem(cfg, root_checks)
        if why:
            return "start_" + why
    return None


def live_buy_problem(cfg: R4btConfig, now_ms: int, orders: Mapping[str, Mapping[str, Any]], order: Mapping[str, Any], state: Any, *, root_checks: bool = True,
                     open_count: int = 0, day_trades: int = 0, day_realized: int = 0, committed_loss: int = 0, first_send_ms: int | None = None) -> str | None:
    """EVERY gate a LIVE buy needs, evaluated afresh from the files at `now_ms`; the first problem as a label, or None. Called by R4btLivePort immediately before the send
    (and by effective_live for routing), so nothing checked earlier in handle_entry is trusted: LIVE_OK / TIER T0 / owner file (live_gate_problems), the recorded read
    (read_record_problem), the A3 carry incl. its written_utc bound (a3_problem), the twin gate, the fresh parity gate (parity_state_problem: <= 60 s, ledger and attempts
    sha match), the order's stake against the C0 caps and the C0 counters (`open_count` includes this order). Latches and STOP / HALT files are the caller's."""
    if not LIVE_BUILT or cfg.mode != B.LIVE:
        return "not_live"
    gates = live_gate_problems(cfg, root_checks)
    if gates:
        return "live_gate:" + gates[0]
    why = read_record_problem(cfg, root_checks)
    if why:
        return why
    why, _skipped = a3_problem(cfg, now_ms, root_checks)
    if why:
        return "a3:" + why
    why = twin_gate_problem(cfg.twin_dir, now_ms, orders, cfg, root_checks, {})
    if why:
        return why
    why = parity_state_problem(cfg.state_dir, now_ms)
    if why:
        return why
    stake = order.get("stake_lamports")
    if isinstance(stake, bool) or not isinstance(stake, int) or not 0 < stake <= min(cfg.stake_lamports, C0_CAPS["stake_lamports"]):
        return "caps:stake"
    if open_count > min(cfg.max_open, C0_CAPS["max_open"]):
        return "caps:max_open"
    if day_trades > min(cfg.max_trades_per_day, C0_CAPS["max_trades_per_day"]):
        return "caps:max_trades_per_day"
    if day_realized <= -min(cfg.daily_loss_lamports, C0_CAPS["daily_loss_lamports"]):
        return "caps:daily_loss"
    if stake != cfg.stake_lamports:
        return "caps:stake_not_cfg"  # the executor sends cfg.stake_lamports: the order's stake must be that amount
    if getattr(state, "realized_lamports", 0) <= -min(cfg.total_loss_lamports, C0_CAPS["total_loss_lamports"]):
        return "caps:total_loss"
    if committed_loss > min(cfg.total_loss_lamports, C0_CAPS["total_loss_lamports"]):
        return "caps:committed_loss"  # realized loss + the worst case of every open / in-flight position INCLUDING this order (the 0.20 SOL total stop, Am.9 section 7)
    if now_ms >= cfg.end_ms:
        return "end_ms"
    if first_send_ms is not None and now_ms > int(first_send_ms) + X.CANARY_MAX_MS:
        return "canary_expired"  # the 14 days run from the first buy attempt
    return None


# --- latency (F2) ---------------------------------------------------------------------------------------------------------------------------------


def landed_buy_latencies_t(orders: Mapping[str, Mapping[str, Any]], legs: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One record per landed buy, landing order (Am.9 section 4). d_land = buy_landed_slot - the twin row's entry_leg.corrected_slot (the decision row's `ms` mapped to the
    tape's slot clock, plus d). k19 / k30 = tools.arena_stack.k_slots(L, sps_entry, "ceil") with the sps_entry of THAT trip's twin row. x19 / x30 = d_land - k. A buy
    without an entry-stage twin row, or without a landed slot, is unmeasured. `sdT_slots` = landed slot - decision_slot (the EXP-027 origin) is carried beside as a
    diagnostic and never decides."""
    rows = []
    for oid, o in orders.items():
        if not _int(o.get("buy_landed_ms")):
            continue
        leg, ls, ds = legs.get(oid), o.get("buy_landed_slot"), o.get("decision_slot")
        d = (ls - leg["corrected_slot"]) if leg and _int(ls) else None
        k19 = AS.k_slots(X.MATCH_P50_S, leg["sps_entry"], "ceil") if leg else None
        k30 = AS.k_slots(X.P5_SOFT_HALT_S, leg["sps_entry"], "ceil") if leg else None
        rows.append({"order_id": oid, "landed_ms": o["buy_landed_ms"], "slots": d, "k19": k19, "k30": k30, "x19": None if d is None else d - k19,
                     "x30": None if d is None else d - k30, "sps_entry": leg["sps_entry"] if leg else None, "gap": bool(o.get("gap")),
                     "sdT_slots": (ls - ds) if _int(ds) and _int(ls) and ls >= ds else None})
    rows.sort(key=lambda r: (r["landed_ms"], r["order_id"]))
    return rows


def latency_summary_t(orders: Mapping[str, Mapping[str, Any]], legs: Mapping[str, Mapping[str, Any]], *, window: int = X.P5_WINDOW) -> dict[str, Any]:
    """The `X.latency_summary` keys that `X.latency_check` reads, counted from the twin's corrected slot (the decision row's instant); the sd(T) figures of EXP-027 sit
    beside it under `diag_sdT`."""
    rows = landed_buy_latencies_t(orders, legs)
    meas = [r for r in rows if r["slots"] is not None]
    last = meas[-window:]
    sd = [r["sdT_slots"] for r in rows if r["sdT_slots"] is not None]
    return {"origin": "twin row corrected_slot (decision row ms + d); sd(T) is a diagnostic beside it", "rounding": "ceil, each trip's own sps_entry", "landed_buys": len(rows),
            "measured": len(meas), "unmeasured": len(rows) - len(meas), "window": window, "window_n": len(last),
            "window_x30_p50_slots": X._med([r["x30"] for r in last]), "all_x19_p50_slots": X._med([r["x19"] for r in meas]),
            "all_slots_p50": X._med([r["slots"] for r in meas]), "wall_measured": 0, "wall_missing": len(rows), "wall_window_p50_s": None, "wall_all_p50_s": None,
            "diag_sdT": {"measured": len(sd), "all_slots_p50": X._med(sd), "origin": "sd(T) = decision_slot: diagnostic only"}}


# --- the order ledger (the twin's input; Am.9 section 3 item 4 (2)) ----------------------------------------------------------------------------------


def ledger_rows(orders: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per CONFIRMED sent order (status not refused AND its buy event reported the entry fee; in-flight orders are left out until then), the keys the twin reads (r4bt-c0-twin-interface.md): order_id, sent, pool, mint, rank, t_mark, stake_sol, entry_ms,
    exit_ms, entry_fee_lamports, exit_fee_lamports (the ACTUAL totals of every send of the leg incl. escalated resends, as the port reported them; null = not reported),
    restart, gap_hold, decision_slot, landed_slot_entry, landed_slot_exit, exit_reason; then, for tools/r4bt_c0_parity.py: live_buy_price, live_sell_price, live_net_lamports,
    exit_landed_ms, gap_through (null from a DRY port) and fee_source ("dry_default" from a DRY port)."""
    out = []
    for oid, o in sorted(orders.items(), key=lambda kv: (kv[1].get("decision_ms") or 0, kv[0])):
        if o.get("status") == "refused" or not _int(o.get("entry_fee_lamports")) or not _int(o.get("decision_ms")):
            continue  # a sent order whose buy has no event yet is PENDING: the twin rejects a sent row without int entry_ms / entry_fee_lamports (exit 3 for the whole ledger)
        out.append({"order_id": oid, "sent": True, "pool": o.get("pool"), "mint": o.get("mint"), "rank": o.get("rank"), "t_mark": o.get("t_mark"),
                    "stake_sol": o["stake_lamports"] / 1e9, "entry_ms": o.get("decision_ms"), "exit_ms": o.get("exit_decision_ms"),
                    "entry_fee_lamports": o.get("entry_fee_lamports"), "exit_fee_lamports": o.get("exit_fee_lamports"), "restart": bool(o.get("restart_exit")),
                    "gap_hold": bool(o.get("gap")), "decision_slot": o.get("decision_slot"), "landed_slot_entry": o.get("buy_landed_slot"),
                    "landed_slot_exit": o.get("sell_landed_slot"), "exit_reason": o.get("sell_reason"), "live_buy_price": o.get("live_buy_price"),
                    "live_sell_price": o.get("live_sell_price"), "live_net_lamports": o.get("live_net_lamports"), "exit_landed_ms": o.get("exit_landed_ms"),
                    "gap_through": o.get("gap_through"), "fee_source": o.get("fee_source")})
    return out


def parity_state_problem(state_dir: str, now_ms: int) -> str | None:
    """The fresh-parity gate (schema `r4bt_parity_state_v1`, written by tools/r4bt_c0_parity.py on every run; ops/reports/2026-11-06/r4bt-parity-state-schema.md). None only
    when `<state_dir>/r4bt-parity-state.json` is a regular file, schema r4bt_parity_state_v1, int exit_code 0, stop_required false (a bool), an int at_ms (= the run instant)
    at most PARITY_MAX_AGE_MS old and not in the future, ledger_sha256 equal to the sha256 of the CURRENT order ledger and attempts_sha256 equal to the sha256 of the CURRENT
    attempts file (a file changed since the run makes the result stale). Fail closed otherwise."""
    st, why, _ = _json_obj(os.path.join(state_dir, PARITY_STATE_NAME), False, "parity_state", mode_mask=0o002)  # the executor's own user writes these: group-writable (umask 002) is fine, world-writable is not
    if why:
        return "parity_gate:" + why
    if st.get("schema") != PARITY_SCHEMA:
        return "parity_gate:schema"
    if not _int(st.get("exit_code")) or st["exit_code"] != 0:
        return "parity_gate:exit_code"
    if st.get("stop_required") is not False:
        return "parity_gate:stop_required"
    run = st.get("at_ms")
    if not _int(run):
        return "parity_gate:at_ms_missing"
    if run - now_ms > PARITY_FUTURE_SKEW_MS:
        return "parity_gate:at_ms_future"
    if now_ms - run > PARITY_MAX_AGE_MS:
        return "parity_gate:stale"
    for name, key in ((LEDGER_NAME, "ledger_sha256"), (ATTEMPTS_NAME, "attempts_sha256")):
        data, why, _ = _read_small(os.path.join(state_dir, name), False, 64 * 1024 * 1024, 0o002)
        if why:
            return f"parity_gate:{key[:-7]}_{why}"
        if st.get(key) != hashlib.sha256(data).hexdigest():
            return f"parity_gate:{key[:-7]}_sha_mismatch"
    return None


def attempt_rows(orders: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per buy SEND OUTCOME, failures included (tools.arena_live_parity attempts schema; P2 reads it). A landed buy: landed true, trip_id = the order, landed_ms the
    buy event's. A failed or expired buy (buy_failed): landed false, failed_or_expired true, no trip_id. p_fail_press is 0.0: the executor does not know the pressure leg, so
    P2's threshold is the flat 15 % floor (the stricter side). An order whose buy has no event yet has no row."""
    out = []
    for oid, o in sorted(orders.items(), key=lambda kv: (kv[1].get("decision_ms") or 0, kv[0])):
        out_ = o.get("buy_outcome")
        if out_ not in ("landed", "failed") or not _int(o.get("decision_ms")):
            continue
        landed = out_ == "landed"
        out.append({"attempt_id": "a:" + oid, "trip_id": oid if landed else None, "decision_ms": o["decision_ms"], "landed": landed,
                    "landed_ms": o.get("buy_event_ms") if landed else None, "failed_or_expired": not landed, "p_fail_press": 0.0})
    return out


# --- the live port: fill facts, fees and the pre-buy gate --------------------------------------------------------------------------------------------

_EVENT_KIND = {"buy_landed": "buy", "buy_failed": "buy", "sell_landed": "sell", "sell_failed": "sell"}


def fill_facts(event: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """The facts the executor and the parity tool read, from the probe executor's own fill row of the SAME send outcome (probe_live._finish_buy / _finish_sell /
    _resolve_expired). Keys are left out, never guessed, when the row does not hold them (the bridge then latches r4bt_fee_unreported / r4bt_fill_unreported).

      fees_lamports   the chain fee of THIS transaction (`fee_lamports`: base + priority as paid), or 0 for an EXPIRED send (it never landed, nothing was paid). Every send of a
                      leg, escalated resends included, is its own event, and apply_events sums them per leg: entry_fee_lamports / exit_fee_lamports.
      live_buy_price  PINNED UNIT = PRICE_UNIT = lamports per raw base-token unit = spend_lamports / tokens_received (pool leg: the stake sent, tokens as received). This is the
                      ratio convention of the twin's pool price Qe / Be (tools/r4bt_c0_parity._price: quote reserve lamports / base reserve raw units), so g_entry and the live price
                      compare like with like. Not SOL per UI token: no decimals factor is applied on either side.
      live_sell_price the same unit: sol_received_lamports (pool leg, no fee, no rent) / tokens sold (raw units).
      live_net_lamports the trip's chain wallet delta: the sell row's pnl_lamports (SOL after the sell - SOL before the buy, failed-attempt costs and ATA rent included).
      landed_ms       the confirm instant the executor saw (`confirm_seen_ms`)."""
    out: dict[str, Any] = {}
    fee = row.get("fee_lamports")
    if _int(fee) and fee >= 0:
        out["fees_lamports"] = int(fee)
    elif row.get("fail_class") == "expired":
        out["fees_lamports"] = 0
    if _int(row.get("confirm_seen_ms")):
        out["landed_ms"] = int(row["confirm_seen_ms"])
    if event == "buy_landed":
        spend, tok = row.get("spend_lamports"), row.get("tokens_received")
        if _int(spend) and _int(tok) and spend > 0 and tok > 0:
            out["live_buy_price"] = spend / tok
    elif event == "sell_landed":
        got, tok = row.get("sol_received_lamports"), row.get("tokens")
        if _int(got) and _int(tok) and got > 0 and tok > 0:
            out["live_sell_price"] = got / tok
        if _int(row.get("pnl_lamports")):
            out["live_net_lamports"] = int(row["pnl_lamports"])
    return out


class R4btLivePort(B.OrderPort):
    """The LIVE order port: wraps the bridge's probe_live.LiveExecutor port (B.make_live_port; the key only from the systemd credential, loaded by the caller) and (1) refuses
    every buy unless `pre_buy(order)` returns None, called immediately before the executor is touched; (2) adds fees_lamports / live_* fill facts to each event from the
    executor's own fill rows. It exists only for a live config with LIVE_BUILT. Exits are never gated."""

    name = "live"
    live = True

    def __init__(self, inner: B.OrderPort, cfg: R4btConfig, pre_buy: Any = None, on_error: Any = None) -> None:
        if not LIVE_BUILT or cfg.mode != B.LIVE or not getattr(inner, "live", False) or getattr(inner, "ex", None) is None:
            raise SystemExit("r4bt live port refused: needs LIVE_BUILT, a live config and a live inner port with an executor")
        self.inner, self.cfg, self.pre_buy, self.on_error = inner, cfg, pre_buy, on_error
        self._rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
        ex = getattr(inner, "ex", None)
        if ex is not None:
            orig = ex._log

            def _log(kind: str, mint: str, **kw: Any) -> None:
                if kind in ("buy", "sell"):
                    self._rows.setdefault((kind, mint), []).append(dict(kw))
                orig(kind, mint, **kw)

            ex._log = _log
            if cfg.send_fee_lamports != REGISTERED_SEND_LAMPORTS:  # a reviewed fee_override: the executor's priority is the override's, minus the base fee
                object.__setattr__(ex.limits, "priority_lamports", cfg.send_fee_lamports - BASE_FEE_LAMPORTS)

    def mint_for_pool(self, pool: str, meta: Mapping[str, Any]) -> str | None:
        return self.inner.mint_for_pool(pool, meta)

    def universe_problem(self, pool: str, mint: str) -> str | None:
        return self.inner.universe_problem(pool, mint)

    def mint_screen_problem(self, mint: str) -> str | None:
        return self.inner.mint_screen_problem(mint)

    def balance(self) -> int | None:
        return self.inner.balance()

    def buy(self, order: Mapping[str, Any]) -> tuple[bool, str | None]:
        if self.pre_buy is None:
            return False, "gate:no_pre_buy_check"  # fail closed: a live port without its gate never buys
        try:
            why = self.pre_buy(order)
        except Exception:  # noqa: BLE001 - deny on error, and latch (persisted) so a restarted unit does not keep buying after whatever raised
            self._error("pre_buy raised")
            return False, "gate:pre_buy_error"
        if why is not None:
            return False, "gate:" + str(why)
        sent = getattr(getattr(self.inner.ex, "limits", None), "size_lamports", None)
        if sent is not None and sent != order.get("stake_lamports"):
            return False, "gate:stake_sent_differs"  # the executor sends limits.size_lamports: it must be the order's stake
        try:
            return self.inner.buy(order)
        except BaseException as exc:  # a tx may already be pending in the probe state: latch, then re-raise (the write-ahead order stays for reconcile)
            self._error(f"inner.buy raised {type(exc).__name__}")
            raise

    def _error(self, why: str) -> None:
        if self.on_error is not None:
            try:
                self.on_error(LATCH_BUY_ERROR, why)
            except Exception:  # noqa: BLE001 - the latch is best effort here; the caller still denies / re-raises
                pass

    def sell(self, order: Mapping[str, Any], reason: str) -> tuple[bool, str | None]:
        return self.inner.sell(order, reason)

    def holdings(self) -> dict[str, str] | None:
        return self.inner.holdings()

    def warm(self, mints: list[str]) -> None:
        self.inner.warm(mints)

    def position_return(self, mint: str) -> float | None:
        return self.inner.position_return(mint)

    def drain_ledger(self) -> list[tuple[str, str, dict[str, Any]]]:
        return self.inner.drain_ledger()

    def poll(self) -> list[dict[str, Any]]:
        out = []
        for ev in self.inner.poll():
            kind = _EVENT_KIND.get(ev.get("event"))
            q = self._rows.get((kind, ev.get("mint"))) if kind else None
            row = q.pop(0) if q else None
            out.append({**ev, **fill_facts(ev["event"], row)} if row is not None else dict(ev))
        return out


# --- the bridge -----------------------------------------------------------------------------------------------------------------------------------


class _ArmLog(DecisionLog):
    """A DecisionLog whose every call reads the r4-b-T arm (DECLOG_ARM), whatever name the bridge passes (it passes cfg.bot_id = r4bt-a1)."""

    def files(self, name: str) -> list[Path]:
        return super().files(DECLOG_ARM)

    def rows(self, name: str) -> list[dict]:
        return super().rows(DECLOG_ARM)

    def verify(self, name: str) -> Any:
        return super().verify(DECLOG_ARM)


class R4btBridge(X.R4bBridge):
    """The EXP-027 DRY bridge with the r4-b-T profile. It skips R4bBridge.__init__ (which pins bot_id to EXP-027's) and repeats its setup; X is not edited."""

    twin_round_trip = staticmethod(twin_round_trip)  # F1: refused

    @property
    def dlog(self) -> DecisionLog:
        return self._dlog

    @dlog.setter
    def dlog(self, value: DecisionLog) -> None:
        self._dlog = _ArmLog(value.dir.parent)  # the bridge assigns DecisionLog(cfg.root); this profile reads the r4b-exp027 arm of the same root

    def __init__(self, cfg: R4btConfig, port: B.OrderPort, *, now_ms: Any = None, dry_replay_caps: bool = False, live_ok_path: Path | None = None,
                 tier_path: Path | None = None, root_checks: bool = True, close_blocked: Any = None, slot_s_by_date: Mapping[str, float] | None = None,
                 gap_clear_dir: Path | str | None = None, require_gap_review: bool = False) -> None:
        if not isinstance(cfg, R4btConfig) or cfg.bot_id != BOT_ID or cfg.frozen_sha != REGISTERED_W or cfg.change_id not in REGISTERED_CHANGE_IDS:
            raise SystemExit("r4bt executor start refused: bot / W / change_id pin")
        if cfg.mode == B.DRY and port.live:
            raise SystemExit("r4bt executor start refused: a DRY config never takes a live port")
        if cfg.mode == B.LIVE and not (LIVE_BUILT and port.live and isinstance(port, R4btLivePort)):
            raise SystemExit("r4bt executor start refused: live_not_built (a live config needs LIVE_BUILT and an R4btLivePort)")
        live = cfg.mode == B.LIVE
        if dry_replay_caps:
            raise SystemExit("r4bt executor start refused: dry_replay_caps is EXP-027's replay tool, not an r4-b-T mode")
        why = start_refusal(cfg, live=live, root_checks=root_checks)
        if why:
            raise SystemExit(f"r4bt executor start refused: {why}")
        other = "dry" if live else "live"
        for n in (f"bridge-state-{other}.json", f"r4b-state-{other}.json"):
            if os.path.lexists(os.path.join(cfg.state_dir, n)):  # DRY and live never share a state dir: the ledger / attempts files carry no mode
                raise SystemExit(f"r4bt executor start refused: other_mode_state_present ({n}): use a separate state dir per mode")
        self.root_checks_r4bt = bool(root_checks)
        self._health_ms = 0
        self._health_stop: bool | None = None
        self.slot_s_by_date = dict(slot_s_by_date or {})
        if any(not _num(v) or not X.SLOT_S_MIN <= v <= X.SLOT_S_MAX for v in self.slot_s_by_date.values()):
            raise SystemExit("r4bt executor start refused: slot_s_by_date out of range")
        self.dry_replay_caps = False
        self.require_gap_review = bool(require_gap_review) or live  # live always needs the manager's gap review
        self._a3_refuse_t: str | None = None
        self.es_path = Path(cfg.state_dir) / f"r4b-state-{cfg.mode}.json"
        self.es = self._load_es()
        B.Bridge.__init__(self, cfg, port, now_ms=now_ms, live_ok_path=live_ok_path or Path(cfg.live_ok_path), tier_path=tier_path or Path(cfg.tier_path),
                          root_checks=root_checks, close_blocked=close_blocked, gap_clear_dir=gap_clear_dir)
        if live:
            port.pre_buy = self.live_buy_gate
            port.on_error = self._latch
        self._save()  # writes the mode's state files (the other-mode marker above) and the ledger  # the parity gate hashes both files: they exist from the start (empty until the first confirmed buy)
        self._log("r4bt_start", w=cfg.frozen_sha, change_id=cfg.change_id, live_gates_open=live_gate_problems(cfg, root_checks))

    # -- the live route: a live-configured bridge sends only while EVERY gate holds; otherwise the entry is routed to the DRY port (logged, never sent)
    def effective_live(self) -> tuple[bool, str | None]:
        if self.cfg.mode != B.LIVE or not LIVE_BUILT:
            return False, "mode_dry"
        gates = live_gate_problems(self.cfg, self.root_checks_r4bt)
        if gates:
            return False, "live_gate:" + gates[0]
        return True, None

    def live_buy_gate(self, order: Mapping[str, Any]) -> str | None:
        """Called by R4btLivePort.buy immediately before the executor sends: EVERY live gate afresh (live_buy_problem) plus the bridge's latches and STOP / HALT files."""
        now = self.now_ms()
        if self.state.latches:
            return "latched:" + sorted(self.state.latches)[0]
        halt, stop = self._files()
        if halt or stop:
            return "halt_file" if halt else "stop_file"
        day = self.state.days.get(B.utc_day(now), {})
        return live_buy_problem(self.cfg, now, self.state.orders, order, self.state, root_checks=self.root_checks_r4bt, open_count=len(self.open_orders(True)),
                                day_trades=day.get("trades", 0), day_realized=day.get("realized", 0), committed_loss=self.committed_loss_lamports(True),
                                first_send_ms=self.es.get("first_send_ms"))

    def handle_entry(self, row: dict[str, Any]) -> None:
        """Any exception in the buy path latches `r4bt_buy_error` (persisted) before it propagates: the loop never continues buying after an error."""
        try:
            super().handle_entry(row)
        except BaseException as exc:
            self._latch(LATCH_BUY_ERROR, f"handle_entry raised {type(exc).__name__}")
            raise

    # -- health.json for the watchdog: written atomically every loop and on every latch; ONLY the HEALTH_KEYS (a test pins it)
    def health_file(self) -> Path:
        """The live unit writes cfg.health_path (the watchdog's file); a DRY run writes `<stem>.dry<suffix>` beside it: the two modes never share a health file."""
        p = Path(self.cfg.health_path)
        return p if self.cfg.mode == B.LIVE else p.with_name(p.stem + ".dry" + p.suffix)

    def health_record(self) -> dict[str, Any]:
        """`dry` is true unless the LIVE gates (LIVE_OK, TIER T0, owner file) are open under a live config. It does NOT say a buy would be sent: the read record, A3,
        twin and parity gates are not in it."""
        latches = sorted(n for n in (str(k) for k in self.state.latches) if LATCH_NAME_RE.fullmatch(n))
        _halt, stop = self._files()
        live_ok = os.path.lexists(self.cfg.live_ok_path)
        data, why, _ = _read_small(self.cfg.tier_path, False)
        tier = TIER_REQUIRED if (why is None and data.decode("utf-8", "replace").strip() == TIER_REQUIRED) else None
        return {"schema": HEALTH_SCHEMA, "at_ms": int(self.now_ms()), "latched": bool(latches), "latch_names": latches, "stop_present": bool(stop),
                "live_ok_present": bool(live_ok), "tier": tier, "dry": not self.effective_live()[0]}

    def write_health(self, force: bool = False) -> None:
        try:
            now = int(self.now_ms())
            if not force and 0 <= now - self._health_ms < HEALTH_MIN_INTERVAL_MS and self._health_stop == os.path.lexists(os.path.join(self.cfg.state_dir, STOP_NAME)):
                return
            rec = self.health_record()
            assert tuple(rec) == HEALTH_KEYS
            self._health_ms, self._health_stop = now, rec["stop_present"]
            path = self.health_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(rec, sort_keys=True))
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - a health write never stops the executor (the watchdog sees a stale at_ms)
            pass

    def _latch(self, name: str, why: str) -> None:
        super()._latch(name, why)
        self.write_health(force=True)

    def tick(self) -> int:
        n = super().tick()
        self.write_health()
        return n

    # -- reads: nothing is read without the recorded PASS read (a4)
    def read_new_rows(self) -> list[dict[str, Any]]:
        if LATCH_READ in self.state.latches:
            return []
        if not HV.synthetic_root(self.cfg.root):
            why = read_record_problem(self.cfg, self.root_checks_r4bt)
            if why:
                self._latch(LATCH_READ, why)
                return []
        return super().read_new_rows()

    # -- a2: the start row repeats W (the bridge) and the registered change_id (here)
    def handle_row(self, row: dict[str, Any]) -> None:
        if row.get("kind") == "start" and row.get("change_id") != self.cfg.change_id:
            self._latch(LATCH_PIN, f"start row change_id {str(row.get('change_id'))[:32]} pinned {self.cfg.change_id}")
        super().handle_row(row)

    # -- gates before a buy
    def entry_refusal(self, row: Mapping[str, Any], now: int, lim: dict[str, int], scope: bool = True) -> str | None:
        why = super().entry_refusal(row, now, lim, scope)
        if why:
            return why
        latch, skipped = a3_problem(self.cfg, now, self.root_checks_r4bt)
        for k in skipped:
            self._count("a3_skipped_" + k)  # the five non-halting flags: skipped and counted (Am.9 F4), disclosed in the daily tie-out
        if latch:
            self._latch(LATCH_A3, latch)
            return "latched:" + LATCH_A3
        nt: dict[str, int] = {}
        why = twin_gate_problem(self.cfg.twin_dir, now, self.state.orders, self.cfg, self.root_checks_r4bt, nt)
        if nt.get("no_twin"):
            self.es["no_twin_seen"] = max(int(self.es.get("no_twin_seen", 0)), nt["no_twin"])  # P7 no_twin: the count of twin-refused / excluded trips past the grace
        if why:
            if why.startswith("twin_gate:pin_mismatch"):
                self._latch(LATCH_TWIN_PIN, why)
            self._count("twin_gate_refusal")
            return why
        why = parity_state_problem(self.cfg.state_dir, now)
        if why:
            self._count("parity_gate_refusal")
            return why
        return None

    # -- F2: the latency origin is the twin row's corrected slot (the decision row's instant + d); ceil with each trip's own sps_entry
    def latency_report(self) -> dict[str, Any]:
        s = latency_summary_t(self.state.orders, twin_entry_legs(self.cfg.twin_dir, self.root_checks_r4bt))
        return {"summary": s, "check": X.latency_check(s)}

    # -- the actual fee of every send (entry and exit, escalated resends included), as the port reports it in `fees_lamports` of the event
    def apply_events(self, events: list[dict[str, Any]]) -> None:
        by_mint = {o["mint"]: k for k, o in self.state.orders.items()}
        for ev in events:
            leg = {"buy_landed": "entry", "buy_failed": "entry", "sell_landed": "exit", "sell_failed": "exit"}.get(ev.get("event"))
            o = self.state.orders.get(ev.get("order_id") or by_mint.get(ev.get("mint"))) if leg else None
            if o is None:
                continue
            fee = ev.get("fees_lamports")
            if _int(fee) and fee >= 0:
                o[f"{leg}_fee_lamports"] = int(o.get(f"{leg}_fee_lamports") or 0) + fee
                if fee > self.cfg.send_fee_lamports and not ev.get("dry"):
                    self._latch(LATCH_FEE_OVER, f"{ev.get('event')} of {o['order_id']} paid {fee} above the configured {self.cfg.send_fee_lamports} per send")
            elif ev.get("dry"):  # a DRY port sends nothing: the configured per-send fee, once per event (marked)
                o[f"{leg}_fee_lamports"] = int(o.get(f"{leg}_fee_lamports") or 0) + self.cfg.send_fee_lamports
                o["fee_source"] = "dry_default"
            else:
                self._latch(LATCH_FEE, f"{ev.get('event')} of {o['order_id']} carries no fees_lamports")
            self._note_fill(o, ev, leg)
        super().apply_events(events)

    def _files(self) -> tuple[bool, bool]:
        """(HALT, STOP). `<state_dir>/STOP` (the parity tool's) is a STOP whatever else is set, any entry at that path (a link included); an unstat-able state dir is a STOP."""
        halt, stop = super()._files()
        if not stop:
            try:
                stop = os.path.lexists(os.path.join(self.cfg.state_dir, STOP_NAME))
            except OSError:
                stop = True
            if stop:
                self._count("parity_stop_file_refusal")
        return halt, stop

    def _note_fill(self, o: dict[str, Any], ev: Mapping[str, Any], leg: str) -> None:
        """The live fill facts the parity tool needs, from the port event: buy_landed live_buy_price; sell_landed live_sell_price, live_net_lamports, landed_ms (the exit's
        landing instant), gap_through. Also the buy outcome row for the attempts file. A DRY event leaves the facts null; a non-DRY landed event without them latches."""
        names = {"buy_landed": ("live_buy_price",), "sell_landed": ("live_sell_price", "live_net_lamports")}.get(ev.get("event"), ())
        for k in names:
            v = ev.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf"):
                o[k] = v
            elif not ev.get("dry"):
                self._latch(LATCH_FILL, f"{ev.get('event')} of {o['order_id']} carries no {k}")
        if ev.get("event") == "sell_landed" and _int(ev.get("landed_ms")):
            o["exit_landed_ms"] = int(ev["landed_ms"])
        if isinstance(ev.get("gap_through"), bool):
            o["gap_through"] = bool(o.get("gap_through")) or ev["gap_through"]
        if ev.get("event") in ("buy_landed", "buy_failed") and o.get("buy_outcome") is None:
            o["buy_outcome"] = "landed" if ev["event"] == "buy_landed" else "failed"
            o["buy_event_ms"] = int(ev["landed_ms"]) if _int(ev.get("landed_ms")) else self.now_ms()

    def _save(self) -> None:
        super()._save()
        self.write_ledger()

    def write_ledger(self) -> None:
        """Rewrite the order ledger (one row per sent order) atomically in the state dir, for the twin writer."""
        sd = Path(self.cfg.state_dir)
        sd.mkdir(parents=True, exist_ok=True)
        for name, rows in ((LEDGER_NAME, ledger_rows(self.state.orders)), (ATTEMPTS_NAME, attempt_rows(self.state.orders))):
            tmp = sd / (name + ".tmp")
            with tmp.open("w") as fh:
                for r in rows:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, sd / name)


# --- CLI: the config check and the run loop -------------------------------------------------------------------------------------------------------


def check(config_path: str, *, live: bool = False, root_checks: bool = True) -> dict[str, Any]:
    """Validate a config file: make_config, start_refusal and the open live gates. Returns {"ok", "problem", "live_gates_open"}; never starts anything."""
    try:
        cfg = make_config(json.loads(Path(config_path).read_text()))
    except (OSError, ValueError) as exc:
        return {"ok": False, "problem": f"config: {exc}", "live_gates_open": None}
    why = start_refusal(cfg, live=live, root_checks=root_checks)
    return {"ok": why is None, "problem": why, "live_gates_open": live_gate_problems(cfg, root_checks)}


def build_live_port(cfg: R4btConfig) -> R4btLivePort:
    """The LIVE port (only `run --live` with a live config reaches this). The key comes ONLY from the systemd credential (probe_live.load_probe_key reads
    $CREDENTIALS_DIRECTORY/...; no path argument), is held by the executor object and is never printed. Imports are local: a DRY run never imports the solana stack."""
    from tools import probe_executor as pe
    from tools import probe_live as pl
    from tools import pumpswap_simulate as sim

    if not (os.environ.get("HELIUS_API_KEY") or "").strip():
        raise SystemExit("r4bt live start refused: rpc_key_missing")
    pl.harden_process()
    kp = pl.load_probe_key()
    rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, None, use_env_file=False)), rps=float(pe.MAX_RPS), max_rps=pl.LIVE_MAX_RPS)
    return R4btLivePort(B.make_live_port(cfg, rpc, kp), cfg)


def run(raw: Mapping[str, Any], *, live_flag: bool, once: bool = False, root_checks: bool = True, port_factory: Any = None, now_ms: Any = None,
        sleep: Any = None) -> int:
    """The run loop: tail the declog arm r4b-exp027 under the change_id root and act per Am.9 (R4btBridge.run). DRY unless the config says mode live AND `live_flag`;
    one without the other runs DRY (the live flag never upgrades a DRY config; a live config never goes live on its own). Returns the process exit code."""
    mode = raw.get("mode", B.DRY)
    if mode not in (B.DRY, B.LIVE):
        print(f"r4bt_c0_executor ALERT startup_refused unknown_mode", flush=True)
        return 2
    if (mode == B.LIVE) != live_flag:  # never half-live and never a silent DRY: a live config without --live (or --live on a DRY config) does not start
        print("r4bt_c0_executor ALERT startup_refused live_flag_mode_mismatch", flush=True)
        return 2
    want_live = live_flag
    try:
        cfg = make_config({**raw, "mode": mode})
    except (ValueError, TypeError) as exc:
        print(f"r4bt_c0_executor ALERT startup_refused {exc}", flush=True)
        return 2
    why = start_refusal(cfg, live=want_live, root_checks=root_checks)
    if why:
        print(f"r4bt_c0_executor ALERT startup_refused {why}", flush=True)
        return 2
    port = (port_factory or build_live_port)(cfg) if want_live else B.DryPort(cfg.dry_balance_lamports)
    br = R4btBridge(cfg, port, now_ms=now_ms, root_checks=root_checks)
    print(f"r4bt_c0_executor mode={cfg.mode} bot={cfg.bot_id} change_id={cfg.change_id} stake={cfg.stake_lamports}", flush=True)
    kw = {"sleep": sleep} if sleep is not None else {}
    br.run(once=once, **kw)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="r4-b-T C0 executor. `run` is DRY unless config mode is live AND --live is given; `check` (or the bare --config form) validates only.")
    if args and args[0] == "run":
        ap.add_argument("cmd")
        ap.add_argument("--config", required=True)
        ap.add_argument("--live", action="store_true", help="with config mode 'live': send real transactions")
        ap.add_argument("--once", action="store_true")
        a = ap.parse_args(args)
        return run(json.loads(Path(a.config).read_text()), live_flag=a.live, once=a.once)
    if args and args[0] == "check":
        args = args[1:]
    ap.add_argument("--config", required=True)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args(args)
    out = check(a.config, live=a.live)
    print(json.dumps(out, sort_keys=True))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
