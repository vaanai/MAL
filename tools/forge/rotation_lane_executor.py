"""rotation_lane_executor: the DEC-036 rotation lane. Follows the decision rows of ONE FORGE DSL spec (tools/forge/dsl_lane_host.py) and trades them live at small size inside the DEC-036 section 5 envelope.

A sibling of tools/forge/dsl_lane_executor.py. It REUSES that module's bridge mechanics (pins, exits, backstop, ATA close, STOP/HALT, A3 pause, LIVE_OK, gap hold, marks, reconcile) by
subclassing, and replaces everything that decides a limit: this module evaluates limits ONLY from its own ROTATION_* constants (DEC-036 section 9 item 4). Nothing here reads
floor_lane_executor's LANE_* / FLOOR_LANE_* at call time to size or refuse an entry. tools/forge/dsl_lane_executor.py, tools/floor_lane_executor.py, tools/arena_live_bridge.py and
tools/floor_arena_executor.py are unchanged by this module's PR (git diff --stat in the PR body). Nothing says any book makes money.

LIMITS (DEC-036 section 5, code ceilings of section 9 item 3; config may only lower them, and may raise the fee reserve and the wallet floor)
  stake ladder   rungs 0.05 / 0.10 / 0.125 / 0.25 SOL; a new book starts at 0.10; down moves and the one-rung up path of section 9 item 2 are read from the book's last start row
  book cap       full-wipe 0.40 SOL (0.75 once the book has a 0.25 start row), 4 open; lane: daily stop 0.30, open exposure 1.00, month 1.50, lifetime 3.00; wallet slice 40%; floor 0.25 once
  isolation      its own ledger, lock, lane STOP and per-book STOP; never FLOOR's; same-pool refusal reads FLOOR's lane ledger read-only (no lock, no write, pool/mint keys only)
Every full-wipe bound counts each position (open and new) at stake + R (R = fee reserve, 3,860,000) and uses NO unrealised gain (a mark never adds headroom).
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import h5_lane_executor as H
from tools.forge import dsl_lane_executor as D

LIVE_BUILT = True  # removes only the `live_not_built` refusal; a send still needs mode live + --live + LIVE_OK + every limit below
LANE_CLASS = "rotation"

# --- DEC-036 section 9 item 2/3: the code ceilings (owner, Approval 1, 2026-11-18T18:34:17Z) ---------------------------------------------------------------
ROTATION_STAKE_RUNGS_LAMPORTS = (50_000_000, 100_000_000, 125_000_000, 250_000_000)
ROTATION_NEW_BOOK_STAKE_LAMPORTS = 100_000_000
ROTATION_STEP_MIN_CLOSED = 20
ROTATION_FEE_RESERVE_LAMPORTS = 3_860_000
ROTATION_BOOK_LOSS_CAP_FIRST_LAMPORTS = 400_000_000
ROTATION_BOOK_LOSS_CAP_STEPPED_LAMPORTS = 750_000_000
ROTATION_BOOK_MAX_OPEN = 4
ROTATION_DAILY_MAX_LAMPORTS = 300_000_000
ROTATION_OPEN_EXPOSURE_MAX_LAMPORTS = 1_000_000_000
ROTATION_MONTH_BUDGET_MAX_LAMPORTS = 1_500_000_000
ROTATION_LIFETIME_BUDGET_MAX_LAMPORTS = 3_000_000_000
ROTATION_WALLET_SLICE_MAX_BPS = 4000
ROTATION_WALLET_FLOOR_MIN_LAMPORTS = 250_000_000
ROTATION_MAX_LIVE_BOOKS = 3
# Not in DEC-036 (this PR's own choices, flagged in the PR body): bounds on the configured funded amount, the per-day trade count, the mark age.
ROTATION_FUNDED_MIN_LAMPORTS, ROTATION_FUNDED_MAX_LAMPORTS = 500_000_000, 3_000_000_000
ROTATION_MAX_TRADES_PER_DAY = 30
ROTATION_MARK_STALE_MAX_S = 60
ROTATION_RPC_RPS = 2.0
STEP_UP = {50_000_000: 100_000_000, 100_000_000: 250_000_000, 125_000_000: 250_000_000}  # section 5 item 1: the only up path
STEP_DOWN = {250_000_000: (125_000_000, 50_000_000), 125_000_000: (50_000_000,), 100_000_000: (50_000_000,)}  # section 6 item 5 table, warning and alarm
SCHEMA_KEYS = ("schema", "bot_id", "row_id")

# --- DEC-036 section 9 item 5: pinned FLOOR identity (public values only; the key is never read) ---------------------------------------------------------------
FLOOR_BOT_ID = "floor-lane-1"
FLOOR_WALLET_PUBKEY = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # docs/HANDOFF.md "Wallet" line (public key only)
FLOOR_LEDGER_PATHS = ("/var/lib/mal-live/h5-lane/lane-ledger.jsonl",)  # lane_daily_report.LEDGER_NAME under the FLOOR lane dir (the `lane.ledger_path` example of the FLOOR config)
FLOOR_STOP_PATHS = ("/var/lib/mal-live/h5-lane/STOP", "/var/lib/mal-live/STOP", "/var/lib/mal-live/HALT")  # FLOOR's lane STOP and the wallet-wide files: a rotation STOP must never be one of them
FLOOR_LANE_DIRS = ("/var/lib/mal-live/h5-lane",)  # nothing of the rotation lane may live in FLOOR's lane dir (its ledger, backlog files, STOP) or directly in /var/lib/mal-live (wallet-wide STOP/HALT)
WALLET_WIDE_DIR = "/var/lib/mal-live"
# DEC-036 section 9 item 4 / QP M1: the rotation ledger and lane STOP are PINNED in code (config may omit them or must equal them); a book STOP is <dir>/STOP-<bot_id>. A config cannot name a
# fresh ledger to shed the lane-wide budgets or the lifetime STOP.
ROTATION_LEDGER_PATH = "/var/lib/mal-live/rotation-lane/lane-ledger.jsonl"
ROTATION_STOP_PATH = "/var/lib/mal-live/rotation-lane/STOP"
RESERVED_BOT_IDS = ("floor-lane-1", "h5-lane", "l1-floor-v2")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
PUBKEY = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

TOP_KEYS = {"mode", "bot_id", "frozen_sha", "spec_sha256", "max_hold_s", "root", "state_dir", "end_ms", "lane", "live_ok_path", "funded_lamports", "max_trades_per_day", "dry_balance_lamports",
            "wallet_floor_lamports", "a3_record_file", "step_record_sha256", "wallet_pubkey", "floor_ledger_read_path", "stake_lamports"}
LANE_KEYS = {"ledger_path", "stop_path", "book_stop_path", "bot_id", "stake_lamports", "max_open", "fee_reserve_lamports", "mark_stale_s", "daily_loss_lamports", "book_loss_cap_lamports",
             "open_exposure_max_lamports", "month_budget_lamports", "lifetime_budget_lamports", "wallet_slice_bps"}
KEYISH = re.compile(r"key|secret|keypair|credential|seed|mnemonic", re.I)
KEY_ALLOWED = {"frozen_sha", "wallet_pubkey"}  # the names that contain the letters but are public pins


class RotationConfigError(ValueError):
    pass


def bot_id_problem(bot_id: Any) -> str | None:
    why = B.c0.bot_id_problem(bot_id)
    if why:
        return why
    if ":" in bot_id or bot_id in RESERVED_BOT_IDS or bot_id.startswith("hv4-"):
        return "bot_id_reserved_or_invalid"
    return None


def _whole(raw: Mapping[str, Any], key: str, default: Any = None) -> int:
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v):
        raise RotationConfigError(f"lane.{key} must be a whole number")
    return int(v)


def _canon(p: str) -> str:
    return os.path.normpath(os.path.realpath(p))


def floor_path_problem(path: str) -> str | None:
    """DEC-036 section 9 item 5 (and section 2 item 3). None, or why a rotation ledger / STOP path would touch FLOOR's files: the pinned FLOOR ledger and STOP paths, anything in FLOOR's lane dir (its
    backlog files included) or directly in /var/lib/mal-live (the wallet-wide STOP and HALT), or the same inode (st_dev, st_ino) as one of them. Everything is compared after realpath."""
    c = _canon(path)
    floor_files = {_canon(p) for p in (*FLOOR_LEDGER_PATHS, *FLOOR_STOP_PATHS)}
    if c in floor_files:
        return "equals a FLOOR / wallet-wide path"
    if os.path.dirname(c) == _canon(WALLET_WIDE_DIR):
        return "directly in the wallet-wide dir"
    for d in FLOOR_LANE_DIRS:
        cd = _canon(d)
        if c == cd or c.startswith(cd + os.sep):
            return "inside FLOOR's lane dir"
    try:
        mine = os.stat(c)
    except OSError:
        return None
    for f in floor_files:
        try:
            other = os.stat(f)
        except OSError:
            continue
        if (mine.st_dev, mine.st_ino) == (other.st_dev, other.st_ino):
            return "the same inode as a FLOOR file"
    return None


@dataclass(frozen=True)
class RotationLimits:
    """The evaluated limits of ONE instance: each is min(config, code ceiling) (the fee reserve and the wallet floor: max)."""

    fee_reserve: int
    book_cap_cfg: int
    max_open: int
    daily: int
    exposure: int
    month: int
    lifetime: int
    slice_bps: int
    wallet_floor: int
    funded: int
    book_stop_path: Path
    floor_ledger: str
    wallet_pubkey: str


def parse_lane(raw: Any, bot_id: str) -> tuple[H.LaneConfig, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise RotationConfigError("lane must be an object")
    extra = set(raw) - LANE_KEYS
    if extra:
        raise RotationConfigError(f"lane: unknown keys {sorted(extra)}")
    pinned = {"ledger_path": ROTATION_LEDGER_PATH, "stop_path": ROTATION_STOP_PATH, "book_stop_path": os.path.join(os.path.dirname(ROTATION_STOP_PATH), f"STOP-{bot_id}")}
    for key, pin in pinned.items():
        if key in raw and raw[key] != pin:
            raise RotationConfigError(f"lane.{key} must be the pinned rotation path {pin} (or omitted)")
    if raw.get("bot_id", bot_id) != bot_id:
        raise RotationConfigError("lane.bot_id must equal the config bot_id")
    stake = _whole(raw, "stake_lamports")
    if stake not in ROTATION_STAKE_RUNGS_LAMPORTS:
        raise RotationConfigError(f"lane.stake_lamports {stake} is not a rung {ROTATION_STAKE_RUNGS_LAMPORTS}")
    limits: dict[str, int] = {}
    for key, ceiling in (("max_open", ROTATION_BOOK_MAX_OPEN), ("daily_loss_lamports", ROTATION_DAILY_MAX_LAMPORTS), ("book_loss_cap_lamports", ROTATION_BOOK_LOSS_CAP_STEPPED_LAMPORTS),
                         ("open_exposure_max_lamports", ROTATION_OPEN_EXPOSURE_MAX_LAMPORTS), ("month_budget_lamports", ROTATION_MONTH_BUDGET_MAX_LAMPORTS),
                         ("lifetime_budget_lamports", ROTATION_LIFETIME_BUDGET_MAX_LAMPORTS), ("wallet_slice_bps", ROTATION_WALLET_SLICE_MAX_BPS)):
        limits[key] = _whole(raw, key, ceiling)
        if not 1 <= limits[key] <= ceiling:
            raise RotationConfigError(f"lane.{key} must be 1..{ceiling}")
    reserve = _whole(raw, "fee_reserve_lamports", ROTATION_FEE_RESERVE_LAMPORTS)
    if reserve < ROTATION_FEE_RESERVE_LAMPORTS:
        raise RotationConfigError(f"lane.fee_reserve_lamports must be >= {ROTATION_FEE_RESERVE_LAMPORTS}")
    stale = raw.get("mark_stale_s", ROTATION_MARK_STALE_MAX_S)
    if isinstance(stale, bool) or not isinstance(stale, (int, float)) or not math.isfinite(stale) or not 0 < stale <= ROTATION_MARK_STALE_MAX_S:
        raise RotationConfigError(f"lane.mark_stale_s must be in (0, {ROTATION_MARK_STALE_MAX_S}]")
    lane = H.LaneConfig(Path(pinned["ledger_path"]), Path(pinned["stop_path"]), bot_id, stake, limits["max_open"], ROTATION_LIFETIME_BUDGET_MAX_LAMPORTS, limits["daily_loss_lamports"], reserve,
                        float(stale), "", False)
    return lane, {**limits, "fee_reserve": reserve, "book_stop_path": pinned["book_stop_path"]}


def make_config(raw: Mapping[str, Any]) -> tuple[B.Config, H.LaneConfig, str, dict[str, Any]]:
    """(bridge Config, lane config, live_ok_path, pins). Fail closed: ValueError on any pin or start refusal (DEC-036 section 9 item 5) that does not hold."""
    unknown = set(raw) - TOP_KEYS
    for k in sorted(unknown):
        if KEYISH.search(k) and k not in KEY_ALLOWED:
            raise RotationConfigError(f"key_path_in_config: {k!r} (only the unit binds the key)")
    if unknown:
        raise RotationConfigError(f"unknown config keys {sorted(unknown)}")
    mode = raw.get("mode", B.DRY)
    if mode not in (B.DRY, B.LIVE):
        raise RotationConfigError("config mode must be 'dry' or 'live'")
    bot_id = raw.get("bot_id")
    why = bot_id_problem(bot_id)
    if why:
        raise RotationConfigError(f"bot_id refused: {why}")
    sha = raw.get("frozen_sha")
    if not isinstance(sha, str) or not SHA40.match(sha):
        raise RotationConfigError("frozen_sha must be the host checkout's 40-hex HEAD sha")
    spec_sha = raw.get("spec_sha256")
    if not isinstance(spec_sha, str) or not SHA64.match(spec_sha):
        raise RotationConfigError("spec_sha256 must be 64 hex")
    hold = raw.get("max_hold_s")
    if isinstance(hold, bool) or not isinstance(hold, (int, float)) or not math.isfinite(hold) or hold <= 0:
        raise RotationConfigError("max_hold_s must be the spec's exit.max_hold_s (a positive number)")
    for k in ("root", "state_dir"):
        if not raw.get(k):
            raise RotationConfigError(f"config {k} is required")
    end = raw.get("end_ms")
    if not F._num(end) or end <= 0:
        raise RotationConfigError("config end_ms is required (explicit, positive)")
    lane, lim = parse_lane(raw.get("lane"), bot_id)
    if raw.get("stake_lamports") not in (None, lane.stake_lamports):
        raise RotationConfigError("top-level stake_lamports must be absent or equal lane.stake_lamports")
    live_ok = raw.get("live_ok_path", D.live_ok_default(bot_id))
    if not isinstance(live_ok, str) or not os.path.isabs(live_ok):
        raise RotationConfigError("live_ok_path must be an absolute path")
    mtd = raw.get("max_trades_per_day", ROTATION_MAX_TRADES_PER_DAY)
    if not F._num(mtd) or mtd <= 0:
        raise RotationConfigError("config max_trades_per_day must be a positive number")
    step_sha = raw.get("step_record_sha256")
    if step_sha is not None and not (isinstance(step_sha, str) and SHA64.match(step_sha)):
        raise RotationConfigError("step_record_sha256 must be 64 hex")
    # item 5: FLOOR's ledger and STOP paths (a pinned list), FLOOR's wallet public key (a pinned constant)
    floor_ledgers = {_canon(p) for p in FLOOR_LEDGER_PATHS}
    mine = {"lane.ledger_path": lane.ledger_path.as_posix(), "lane.stop_path": lane.stop_path.as_posix(), "lane.book_stop_path": lim["book_stop_path"]}
    for name, p in mine.items():
        why = floor_path_problem(p)
        if why:
            raise RotationConfigError(f"{name} refused: {why}")
    if len({_canon(p) for p in mine.values()}) != 3:
        raise RotationConfigError("lane ledger, lane STOP and book STOP paths must be three different paths")
    live = mode == B.LIVE
    pub = raw.get("wallet_pubkey")
    fl_read = raw.get("floor_ledger_read_path")
    funded = raw.get("funded_lamports")
    if live:
        if not (isinstance(pub, str) and PUBKEY.match(pub)):
            raise RotationConfigError("wallet_pubkey (the rotation wallet's PUBLIC key) is required in live")
        if not (isinstance(fl_read, str) and fl_read and _canon(fl_read) in floor_ledgers):
            raise RotationConfigError("floor_ledger_read_path is required in live and must be FLOOR's pinned ledger path (same-pool refusal is ON)")
        if not (isinstance(funded, int) and not isinstance(funded, bool) and ROTATION_FUNDED_MIN_LAMPORTS <= funded <= ROTATION_FUNDED_MAX_LAMPORTS):
            raise RotationConfigError(f"funded_lamports is required in live ({ROTATION_FUNDED_MIN_LAMPORTS}..{ROTATION_FUNDED_MAX_LAMPORTS})")
    if pub is not None and pub == FLOOR_WALLET_PUBKEY:
        raise RotationConfigError("wallet_pubkey equals floor-lane-1's wallet")
    if mode == B.LIVE and raw.get("a3_record_file") is not None and not os.path.isabs(str(raw["a3_record_file"])):
        raise RotationConfigError("a3_record_file must be an absolute path")
    wf = raw.get("wallet_floor_lamports", 0) or 0
    if isinstance(wf, bool) or not isinstance(wf, int) or wf < 0:
        raise RotationConfigError("wallet_floor_lamports must be a whole number")
    wallet_floor = max(ROTATION_WALLET_FLOOR_MIN_LAMPORTS, wf)
    funded_i = int(funded) if funded is not None else 0
    rl = RotationLimits(lim["fee_reserve"], lim["book_loss_cap_lamports"], lim["max_open"], lim["daily_loss_lamports"], lim["open_exposure_max_lamports"], lim["month_budget_lamports"],
                        lim["lifetime_budget_lamports"], lim["wallet_slice_bps"], wallet_floor, funded_i, Path(lim["book_stop_path"]), str(fl_read or ""), str(pub or ""))
    kw: dict[str, Any] = {}
    if "dry_balance_lamports" in raw:
        kw["dry_balance_lamports"] = int(raw["dry_balance_lamports"])
    cfg = B.Config(bot_id=bot_id, frozen_sha=sha, root=str(raw["root"]), state_dir=str(raw["state_dir"]), end_ms=int(end), max_hold_ms=int(hold * 1000) + D.DSL_BACKSTOP_MARGIN_MS,
                   mode=mode, stake_lamports=lane.stake_lamports, max_open=lane.max_open, max_trades_per_day=min(int(mtd), ROTATION_MAX_TRADES_PER_DAY),
                   daily_loss_lamports=lane.daily_loss_lamports, total_loss_lamports=ROTATION_BOOK_LOSS_CAP_STEPPED_LAMPORTS,
                   per_trade_loss_lamports=lane.stake_lamports + lane.fee_reserve_lamports, wallet_floor_lamports=wallet_floor, sl_backstop=B.MAX_SL_BACKSTOP, **kw)
    return cfg, lane, live_ok, {"spec_sha256": spec_sha, "max_hold_s": float(hold), "rot": rl, "step_record_sha256": step_sha}


# --- the ledger: its own file, lock, STOPs; limits from ROTATION_* only ----------------------------------------------------------------------------------------


def fold_rotation(rows: Sequence[Any]) -> dict[str, Any]:
    """Replay the rotation ledger per book. `open`: key -> {bot, stake, filled, ts_ms, signature, cum}; `realized`: [(ts_ms, bot, lamports)] one entry per closed position (all its rows
    summed, fees and rent included) or per stray fee row; `closed`: filled positions that exited or were written off; `starts`: start rows in order. Raises on a row it cannot read."""
    open_: dict[str, dict[str, Any]] = {}
    realized: list[tuple[int, str, int]] = []
    closed: list[dict[str, Any]] = []
    starts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("ev"), str):
            raise H.LaneLedgerError("ledger row is not an object with an `ev`")
        rid = r.get("row_id")
        if rid is not None:
            if rid in seen:
                continue
            seen.add(rid)
        ev = r["ev"]
        if ev == "snap":
            continue
        if ev == "start":
            starts.append(r)
            continue
        if not isinstance(r.get("mint"), str) or not isinstance(r.get("bot_id"), str):
            raise H.LaneLedgerError("ledger row has no bot_id / mint")
        bot, key, ts = r["bot_id"], f"{r['bot_id']}:{r['mint']}", H._int(r, "ts_ms", True)
        delta = H._int(r, "sol_in_lamports") - H._int(r, "sol_out_lamports")
        if ev == "send_intent":
            open_[key] = {"bot": bot, "stake": H._int(r, "stake_lamports", True), "filled": False, "ts_ms": ts, "signature": r.get("signature"), "cum": delta}
        elif key not in open_:
            realized.append((ts, bot, delta))
        elif ev == "fill":
            p = open_[key]
            p["cum"] += delta
            if r.get("ok") is True:
                p["filled"] = True
            else:
                realized.append((ts, bot, open_.pop(key)["cum"]))
        elif ev in ("exit", "writeoff"):
            p = open_.pop(key)
            realized.append((ts, bot, p["cum"] + delta))
            if p["filled"]:
                closed.append({"bot": bot, "stake": p["stake"], "ts_open": p["ts_ms"], "ts_close": ts})
        else:  # "fee": a failed or superseded sell that paid its fee; the position stays open
            open_[key]["cum"] += delta
    return {"open": open_, "realized": realized, "closed": closed, "starts": starts}


def month_of(ms: int) -> str:
    return time.strftime("%Y-%m", time.gmtime(ms / 1000.0))


def stake_start_problem(rows: Sequence[Any], bot: str, stake: int, step_sha: str | None) -> str | None:
    """DEC-036 section 9 item 2, from the book's last start row in the rotation ledger. None = the start may proceed."""
    if stake not in ROTATION_STAKE_RUNGS_LAMPORTS:
        return "stake_not_a_rung"
    try:
        st = fold_rotation(rows)
    except Exception:  # noqa: BLE001
        return "ledger_unreadable"
    starts = [s for s in st["starts"] if s.get("bot_id") == bot and isinstance(s.get("stake_lamports"), int) and not isinstance(s.get("stake_lamports"), bool)]
    if not starts:
        return None if stake == ROTATION_NEW_BOOK_STAKE_LAMPORTS else "new_book_must_start_at_100000000"
    last = starts[-1]["stake_lamports"]
    if stake == last or stake in STEP_DOWN.get(last, ()):
        return None
    if STEP_UP.get(last) != stake:
        return "stake_move_refused"
    if not (isinstance(step_sha, str) and SHA64.match(step_sha)):
        return "up_step_needs_step_record_sha256"
    change = 0
    for i in range(1, len(starts)):
        if starts[i]["stake_lamports"] != starts[i - 1]["stake_lamports"]:
            change = i
    t0 = int(starts[change].get("ts_ms", 0))
    n = sum(1 for c in st["closed"] if c["bot"] == bot and c["stake"] == last and c["ts_open"] >= t0)
    return None if n >= ROTATION_STEP_MIN_CLOSED else f"up_step_needs_{ROTATION_STEP_MIN_CLOSED}_closed_at_current_rung_since_last_change"


class FloorLedgerUnreadable(Exception):
    pass


def floor_open_keys(path: str) -> set[str]:
    """DEC-036 section 9 item 6. The pool/mint keys of floor-lane-1's open positions, read-only: open(path, 'rb') only, no lock, no write. Open and close rows only (`ev` in send_intent, fill, exit,
    writeoff; `ok` of a fill); nothing else of a row is kept (no stake, no P&L). A last line with no trailing newline is a write in progress and is ignored. A file that cannot be read,
    or a complete line that does not parse, raises FloorLedgerUnreadable (the caller refuses that entry)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise FloorLedgerUnreadable(type(exc).__name__) from None
    lines = data.split(b"\n")
    lines.pop()  # after the split the last element is "" (a clean end) or the torn tail (no newline): neither is a complete line
    held: set[str] = set()
    for raw in lines:
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            raise FloorLedgerUnreadable("unparseable_line") from None
        if not isinstance(r, dict) or not isinstance(r.get("ev"), str):
            raise FloorLedgerUnreadable("bad_row")
        ev = r["ev"]
        if ev in ("start", "snap", "fee") or r.get("bot_id") != FLOOR_BOT_ID:
            continue
        keys = [v for v in (r.get("mint"), r.get("pool")) if isinstance(v, str) and v]
        if not keys:
            raise FloorLedgerUnreadable("no_key")
        if ev == "send_intent" or (ev == "fill" and r.get("ok") is True):
            held.update(keys)
        elif ev in ("exit", "writeoff") or (ev == "fill" and r.get("ok") is not True):
            held.difference_update(keys)
    return held


class RotationLedger(H.LaneLedger):
    """The rotation ledger. `check` and `reserve` replace the base's: every refusal is from the ROTATION_* constants and the book's / the lane's rows. The base's file, lock, backlog, append and STOP
    helpers are reused as they are (they carry no limit)."""

    def __init__(self, cfg: H.LaneConfig, rot: RotationLimits) -> None:
        super().__init__(cfg)
        self.rot = rot
        self.bot = cfg.bot_id
        self.stake = cfg.stake_lamports
        self._marks: dict[str, tuple[int, int]] = {}

    # -- STOP files: the lane's and the book's
    def stop_kind(self) -> str | None:
        for kind, p in (("lane", self.cfg.stop_path), ("book", self.rot.book_stop_path)):
            try:
                os.stat(p)
                return kind
            except FileNotFoundError:
                continue
            except OSError:
                return kind
        return None

    def stop_present(self) -> bool:
        return self.stop_kind() is not None

    def write_book_stop(self, reason: str, now_ms: int) -> None:
        p = self.rot.book_stop_path
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            return
        with os.fdopen(fd, "w") as f:
            f.write(f"{reason} ts_ms={now_ms} bot_id={self.bot}\n")

    # -- the limits
    def book_cap(self, st: Mapping[str, Any]) -> int:
        stepped = any(s.get("bot_id") == self.bot and s.get("stake_lamports") == 250_000_000 for s in st["starts"])
        return min(self.rot.book_cap_cfg, ROTATION_BOOK_LOSS_CAP_STEPPED_LAMPORTS if stepped else ROTATION_BOOK_LOSS_CAP_FIRST_LAMPORTS)

    def decide(self, rows: Sequence[Any], view: H.LaneView, now_ms: int, stake: int) -> tuple[str | None, str | None]:
        """(refusal reason, stop action) for one new entry at `stake`. Pure. stop action: 'book' or 'lane' (the STOP file the caller writes)."""
        rot, R = self.rot, self.rot.fee_reserve
        if stake not in ROTATION_STAKE_RUNGS_LAMPORTS or stake != self.stake:
            return "rotation_stake_out_of_bounds", None
        st = fold_rotation(rows)
        base = view.snaps.get(H.day_of(now_ms))
        if base is not None and (view.pnl - base[0] <= -rot.daily or view.cost - base[1] <= -rot.daily):
            return "rotation_daily_stop", None
        mine = [p for p in st["open"].values() if p["bot"] == self.bot]
        if len(mine) >= min(rot.max_open, ROTATION_BOOK_MAX_OPEN):
            return "rotation_book_max_open", None
        allopen = list(st["open"].values())
        if sum(p["stake"] for p in allopen) + stake > min(rot.exposure, ROTATION_OPEN_EXPOSURE_MAX_LAMPORTS):
            return "rotation_exposure_cap", None
        if sum(p["stake"] + R for p in mine) + stake + R > min(rot.slice_bps, ROTATION_WALLET_SLICE_MAX_BPS) * rot.funded // 10_000:
            return "rotation_wallet_slice", None
        book_realized = sum(a for _t, b, a in st["realized"] if b == self.bot)
        cap = self.book_cap(st)
        if book_realized - sum(p["stake"] + R for p in mine) - (stake + R) <= -cap:
            if not mine and book_realized - (stake + R) <= -cap:
                return "rotation_book_cap_stop", "book"
            return "rotation_book_cap", None
        month = month_of(now_ms)
        month_realized = sum(a for t, _b, a in st["realized"] if month_of(t) == month)
        if month_realized - sum(p["stake"] + R for p in allopen) - (stake + R) <= -min(rot.month, ROTATION_MONTH_BUDGET_MAX_LAMPORTS):
            return "rotation_month_budget", None
        life = sum(a for _t, _b, a in st["realized"])
        limit = min(rot.lifetime, ROTATION_LIFETIME_BUDGET_MAX_LAMPORTS)
        if not allopen and life - (ROTATION_STAKE_RUNGS_LAMPORTS[0] + R) <= -limit:
            return "rotation_lifetime_stop", "lane"
        if life - sum(p["stake"] + R for p in allopen) - (stake + R) <= -limit:
            return "rotation_lifetime_budget", None
        return None, None

    def _act(self, why: str | None, action: str | None, now_ms: int) -> None:
        if action == "book":
            self.write_book_stop(f"{why} bot_id={self.bot}", now_ms)
        elif action == "lane":
            self.write_stop(f"{why}", now_ms)

    def check(self, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        self._marks = marks
        kind = self.stop_kind()
        if kind:
            return f"rotation_{kind}_stop_file"
        if self.backlog_pending():
            return "lane_backlog_pending"
        try:
            with self.locked():
                rows = self.read()
                view = H.lane_fold(rows, marks, now_ms, self.cfg.mark_stale_s, self.bot)
                why, action = self.decide(rows, view, now_ms, stake)
        except Exception:  # noqa: BLE001
            return "lane_ledger_error"
        self._act(why, action, now_ms)
        return why

    def reserve(self, mint: str, signature: str, marks: dict[str, tuple[int, int]], now_ms: int, stake: int, pool_gap_waived: bool = False) -> str | None:
        self._marks = marks
        kind = self.stop_kind()
        if kind:
            return f"rotation_{kind}_stop_file"
        try:
            with self.locked():
                self.drain_locked()
                rows = self.read()
                view = H.lane_fold(rows, marks, now_ms, self.cfg.mark_stale_s, self.bot)
                held = fold_rotation(rows)["open"].get(f"{self.bot}:{mint}")
                if held is not None and held["signature"] == signature:
                    return None  # idempotent re-send after a restart
                self._snap_locked(view, now_ms)
                why, action = self.decide(rows, view, now_ms, stake)
                if why:
                    self._act(why, action, now_ms)
                    return why
                self.append({"ev": "send_intent", "ts_ms": now_ms, "mint": mint, "signature": signature, "stake_lamports": stake, "sol_in_lamports": 0, "sol_out_lamports": 0,
                             "estimated": False, "pool_gap_waived": False})
        except Exception:  # noqa: BLE001
            return "lane_ledger_error"
        return None

    def record(self, ev: str, mint: str, marks: dict[str, tuple[int, int]], now_ms: int, **kw: Any) -> H.LaneView | None:
        """The base's backlog-first write, with no budget stop of the base's (the stops of this lane are written by `decide` at the next entry)."""
        import uuid

        sol_in, sol_out = kw.pop("sol_in", 0), kw.pop("sol_out", 0)
        fee_network, fee_priority, fee_tip = kw.pop("fee_network", 0), kw.pop("fee_priority", 0), kw.pop("fee_tip", 0)
        rent_un, rent_ref, sig, est = kw.pop("rent_unreclaimed", 0), kw.pop("rent_refunded", 0), kw.pop("signature", None), kw.pop("estimated", False)
        row = {"schema": H.LANE_SCHEMA, "bot_id": self.bot, "row_id": uuid.uuid4().hex, "ev": ev, "ts_ms": now_ms, "mint": mint, "signature": sig, "sol_in_lamports": int(sol_in),
               "sol_out_lamports": int(sol_out), "fee_network_lamports": int(fee_network), "fee_priority_lamports": int(fee_priority), "fee_tip_lamports": int(fee_tip),
               "rent_unreclaimed_lamports": int(rent_un), "rent_refunded_lamports": int(rent_ref), "estimated": bool(est), **kw}
        self._write_line(self.backlog_path, row)
        with self.locked():
            view0 = H.lane_fold(self.read(), marks, now_ms, self.cfg.mark_stale_s, self.bot)
            self._snap_locked(view0, now_ms)
            self.drain_locked()
            return H.lane_fold(self.read(), marks, now_ms, self.cfg.mark_stale_s, self.bot)


def take_bot_lock(lane: H.LaneLedger) -> Any:
    f = open(f"{lane.path}.bot.{lane.cfg.bot_id}.lock", "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit(f"rotation lane start refused: another process holds bot_id {lane.cfg.bot_id!r} on this ledger") from None
    return f


def live_books_others(ledger_path: Path, bot_id: str) -> int:
    """Rotation books live now = per-bot flock files of this ledger that another process holds (a held lock is a running instance)."""
    n = 0
    for p in glob.glob(glob.escape(f"{ledger_path}.bot.") + "*.lock"):
        if p == f"{ledger_path}.bot.{bot_id}.lock":
            continue
        try:
            f = open(p, "a")
        except OSError:
            n += 1  # unreadable: count it (fail closed)
            continue
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            n += 1
        finally:
            f.close()
    return n


# --- the bridge ---------------------------------------------------------------------------------------------------------------------------------------------


class RotationLaneBridge(D.DslLaneBridge):
    """The DSL lane bridge with the rotation limits. Everything that sizes or refuses an entry is overridden here; the mechanics are the parent's."""

    def __init__(self, cfg: B.Config, port: B.OrderPort, lane_cfg: H.LaneConfig, pins: Mapping[str, Any], *, now_ms: Callable[[], int] | None = None, live_ok_path: str | Path | None = None,
                 tier_path: Path = B.TIER_PATH, root_checks: bool = True, close_blocked: Callable[[str], bool | None] | None = None, a3_record_file: str = "", bot_lock: Any = None) -> None:
        self.bot = cfg.bot_id
        self.pins = dict(pins)
        self.rot: RotationLimits = pins["rot"]
        self.hold_path = Path(cfg.state_dir) / f"dsl-state-{cfg.mode}.json"
        self.lane_cfg = lane_cfg
        self.lane = None  # type: ignore[assignment]
        self._lane_bot_lock = None
        self._lane_marks: dict[str, tuple[int, int]] = {}
        self._lane_orphans: set[str] = set()
        self._lane_recon_ms = 0
        self.lane_live_ok_path = str(live_ok_path if live_ok_path is not None else D.live_ok_default(cfg.bot_id))
        self.lane_root_checks = root_checks
        live = cfg.mode == B.LIVE
        self.a3_record_file = a3_record_file
        self._a3_pause = None
        self._a3_polled_ms = None
        self._rot_counts: dict[str, int] = {}
        if lane_cfg.bot_id != cfg.bot_id or bot_id_problem(cfg.bot_id):
            raise SystemExit("rotation lane start refused: bot_id pin")
        if live:
            self.lane = RotationLedger(lane_cfg, self.rot)
            if live_books_others(self.lane.path, cfg.bot_id) >= ROTATION_MAX_LIVE_BOOKS:
                raise SystemExit(f"rotation lane start refused: {ROTATION_MAX_LIVE_BOOKS} books already live")
            self._lane_bot_lock = bot_lock if bot_lock is not None else take_bot_lock(self.lane)
        try:
            self._start_guard(cfg, port)
            B.Bridge.__init__(self, cfg, port, now_ms=now_ms, live_ok_path=Path(self.lane_live_ok_path), tier_path=tier_path, root_checks=root_checks, close_blocked=close_blocked)
        except BaseException:
            self.release_lane_lock()
            raise
        if live:
            port.pre_buy = self._lane_pre_buy
            port.on_error = self._latch
            self._lane_record("start", "", code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), frozen_sha=cfg.frozen_sha, book=cfg.bot_id, spec_sha256=self.pins["spec_sha256"],
                              lane_class=LANE_CLASS, stake_lamports=lane_cfg.stake_lamports, step_record_sha256=self.pins.get("step_record_sha256"),
                              lane={"bot_id": self.bot, "stake_lamports": lane_cfg.stake_lamports, "max_open": lane_cfg.max_open, "daily_loss_lamports": lane_cfg.daily_loss_lamports,
                                    "fee_reserve_lamports": lane_cfg.fee_reserve_lamports, "mark_stale_s": lane_cfg.mark_stale_s, "book_loss_cap_lamports": self.rot.book_cap_cfg,
                                    "funded_lamports": self.rot.funded, "wallet_floor_lamports": self.rot.wallet_floor})
            self._lane_reconcile(self.now_ms(), force=True)

    def _start_guard(self, cfg: B.Config, port: B.OrderPort) -> None:
        if self.lane_cfg.stake_lamports not in ROTATION_STAKE_RUNGS_LAMPORTS or cfg.stake_lamports != self.lane_cfg.stake_lamports:
            raise SystemExit(f"rotation lane start refused: stake {cfg.stake_lamports} / lane stake {self.lane_cfg.stake_lamports} is not a rung")
        if cfg.max_open > ROTATION_BOOK_MAX_OPEN or cfg.max_open != self.lane_cfg.max_open:
            raise SystemExit("rotation lane start refused: max_open")
        if cfg.mode == B.LIVE:
            if not (LIVE_BUILT and port.live and isinstance(port, D.X.FloorLanePort)):
                raise SystemExit("rotation lane start refused: a live config needs LIVE_BUILT and a FloorLanePort (the write-ahead pre_buy hook is its only send gate)")
            if not D.X.sell_closes_ata():
                raise SystemExit("rotation lane start refused: the sell does not close the token ATA (rent would be stranded)")
            assert self.lane is not None
            if self.lane.backlog_pending():
                raise SystemExit("rotation lane start refused: ledger backlog pending (the last stake would be read from an incomplete ledger)")
            try:
                with self.lane.locked():
                    rows = self.lane.read()
            except Exception:  # noqa: BLE001
                raise SystemExit("rotation lane start refused: ledger_unreadable") from None
            why = stake_start_problem(rows, self.bot, cfg.stake_lamports, self.pins.get("step_record_sha256"))
            if why:
                raise SystemExit(f"rotation lane start refused: {why}")
        elif port.live:
            raise SystemExit("rotation lane start refused: a DRY config never takes a live port")

    # -- limits: the rotation's
    def tier_limits(self) -> tuple[str, dict[str, int], str | None]:
        c = self.cfg
        return "ROTATION", {"stake_lamports": c.stake_lamports, "max_open": min(c.max_open, ROTATION_BOOK_MAX_OPEN), "max_trades_per_day": min(c.max_trades_per_day, ROTATION_MAX_TRADES_PER_DAY),
                            "daily_loss_lamports": min(c.daily_loss_lamports, ROTATION_DAILY_MAX_LAMPORTS), "total_loss_lamports": ROTATION_BOOK_LOSS_CAP_STEPPED_LAMPORTS}, None

    def _position_worst_case(self, stake: int) -> int:
        return stake + self.rot.fee_reserve

    def floor_problem(self, stake: int, bal: int | None, scope: bool = True) -> str | None:
        """The wallet floor, held ONCE (DEC-036 section 5 item 8, manager ruling). The balance already reflects every LANDED stake, so a landed position's stake is not added again; only buys
        not yet landed (their stake has not left the wallet) are, with the new stake, the new buy's send + rent, a sell reserve for every position, and the floor (once)."""
        if bal is None:
            return "balance_unreadable"
        opens = self.open_orders(scope)
        pending = sum(int(o["stake_lamports"]) for o in opens.values() if o["status"] in F.UNLANDED_BUY_STATUSES)
        need = pending + stake + F.SEND_FEE_LAMPORTS + F.RENT_RESERVE_LAMPORTS + (len(opens) + 1) * F.SELL_RESERVE_LAMPORTS + self.rot.wallet_floor
        return "wallet_floor" if bal < need else None

    def _lane_check(self, now: int, stake: int) -> str | None:
        if self.lane is None:
            return None
        self.a3_pause_tick(now)
        if self._a3_pause:
            return "a3_pause:" + self._a3_pause
        if self._lane_orphans:
            return "lane_unresolved_intent"
        return self.lane.check(self._lane_marks, now, stake)

    def _rcount(self, k: str) -> None:
        self._rot_counts[k] = self._rot_counts.get(k, 0) + 1
        self._count(k)

    def floor_pool_refusal(self, keys: Sequence[Any]) -> str | None:
        """DEC-036 section 9 item 6 (ON). None, or `floor_pool_open` / `floor_ledger_unreadable` (both counted)."""
        try:
            held = floor_open_keys(self.rot.floor_ledger)
        except FloorLedgerUnreadable:
            self._rcount("floor_ledger_unreadable")
            return "floor_ledger_unreadable"
        if any(isinstance(k, str) and k in held for k in keys):
            self._rcount("floor_pool_open")
            return "floor_pool_open"
        return None

    def entry_refusal(self, row: Mapping[str, Any], now: int, lim: dict[str, int], scope: bool = True) -> str | None:
        why = super().entry_refusal(row, now, lim, scope)
        if why or self.lane is None or not scope:
            return why
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        return self.floor_pool_refusal([row.get("pool"), meta.get("mint")])

    def _lane_pre_buy(self, order: Mapping[str, Any]) -> str | None:
        """Immediately before the executor is touched: STOPs, latches, files, the same-pool read, then the limits under the ledger lock with the intent row fsynced (write-ahead)."""
        assert self.lane is not None
        kind = self.lane.stop_kind()
        if kind:
            return f"rotation_{kind}_stop_file"
        self._a3_polled_ms = None
        self.a3_pause_tick(self.now_ms())
        if self._a3_pause:
            return "a3_pause:" + self._a3_pause
        halt, stop = self._files()
        if halt:
            return "halt_file"
        if stop:
            return "stop_file"
        if self.state.latches:
            return "latched:" + sorted(self.state.latches)[0]
        stake = order.get("stake_lamports")
        if not isinstance(stake, int) or isinstance(stake, bool) or stake not in ROTATION_STAKE_RUNGS_LAMPORTS or stake != self.cfg.stake_lamports:
            return "rotation_stake_out_of_bounds"
        why = self.floor_pool_refusal([order.get("mint"), order.get("pool")])
        if why:
            return why
        return self.lane.reserve(str(order["mint"]), f"intent:{order['order_id']}", self._lane_marks, self.now_ms(), stake)


# --- run ------------------------------------------------------------------------------------------------------------------------------------------------


def build_live_port(cfg: B.Config, wallet_pubkey: str = "") -> Any:
    """The LIVE port. The key comes only from the systemd credential (probe_live.load_probe_key); no Claude session reads it. The executor compares the loaded key's PUBLIC key with floor-lane-1's
    pinned wallet and with the config's `wallet_pubkey`, and refuses on either (item 5). Imports are local: a DRY run never imports the solana stack."""
    from tools import probe_executor as pe
    from tools import probe_live as pl
    from tools import pumpswap_simulate as sim

    if not (os.environ.get("HELIUS_API_KEY") or "").strip():
        raise SystemExit("rotation lane live start refused: rpc_key_missing")
    pl.harden_process()
    kp = pl.load_probe_key()
    pub = str(kp.pubkey()) if hasattr(kp, "pubkey") else ""
    if pub == FLOOR_WALLET_PUBKEY:
        raise SystemExit("rotation lane live start refused: the bound key is floor-lane-1's wallet")
    if wallet_pubkey and pub != wallet_pubkey:
        raise SystemExit("rotation lane live start refused: the bound key's public key differs from config wallet_pubkey")
    rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, None, use_env_file=False)), rps=ROTATION_RPC_RPS, max_rps=pl.LIVE_MAX_RPS)
    inner = B.make_live_port(cfg, rpc, kp)
    return D.X.FloorLanePort(inner, cfg)


def start_refusal(cfg: B.Config, live_ok_path: str, *, live: bool, root_checks: bool = True) -> str | None:
    return D.start_refusal(cfg, live_ok_path, live=live, root_checks=root_checks)


def run(raw: Mapping[str, Any], *, live_flag: bool, once: bool = False, root_checks: bool = True, port_factory: Callable[[B.Config], B.OrderPort] | None = None,
        now_ms: Callable[[], int] | None = None, sleep: Callable[[float], None] | None = None) -> int:
    """DRY unless the config says mode live AND `live_flag`."""
    mode = raw.get("mode", B.DRY)
    if mode not in (B.DRY, B.LIVE) or (mode == B.LIVE) != live_flag:
        print("rotation_lane_executor ALERT startup_refused live_flag_mode_mismatch", flush=True)
        return 2
    try:
        cfg, lane, live_ok, pins = make_config(raw)
    except (ValueError, TypeError) as exc:
        print(f"rotation_lane_executor ALERT startup_refused {exc}", flush=True)
        return 2
    a3_file = str(raw.get("a3_record_file") or "")
    if live_flag and not (a3_file and os.path.isabs(a3_file)):
        print("rotation_lane_executor ALERT startup_refused a3_record_file_missing", flush=True)
        return 2
    why = start_refusal(cfg, live_ok, live=live_flag, root_checks=root_checks)
    if why:
        print(f"rotation_lane_executor ALERT startup_refused {why}", flush=True)
        return 2
    lock = None
    if live_flag:
        led = RotationLedger(lane, pins["rot"])
        if live_books_others(led.path, cfg.bot_id) >= ROTATION_MAX_LIVE_BOOKS:
            print("rotation_lane_executor ALERT startup_refused max_live_books", flush=True)
            return 2
        try:
            lock = take_bot_lock(led)
        except SystemExit as exc:
            print(f"rotation_lane_executor ALERT startup_refused {exc}", flush=True)
            return 2
    port = (port_factory or (lambda c: build_live_port(c, pins["rot"].wallet_pubkey)))(cfg) if live_flag else B.DryPort(cfg.dry_balance_lamports)
    try:
        br = RotationLaneBridge(cfg, port, lane, pins, now_ms=now_ms, live_ok_path=live_ok, root_checks=root_checks, a3_record_file=a3_file, bot_lock=lock)
    except SystemExit as exc:
        print(f"rotation_lane_executor ALERT startup_refused {exc}", flush=True)
        return 2
    print(f"rotation_lane_executor mode={cfg.mode} lane_bot={cfg.bot_id} stake={cfg.stake_lamports}", flush=True)
    kw = {"sleep": sleep} if sleep is not None else {}
    br.run(once=once, **kw)
    return 0


def check(raw: Mapping[str, Any], *, live: bool = False, root_checks: bool = True) -> dict[str, Any]:
    try:
        cfg, _lane, live_ok, _pins = make_config(raw)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "problem": f"config: {exc}"}
    why = start_refusal(cfg, live_ok, live=live, root_checks=root_checks)
    return {"ok": why is None, "problem": why}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DEC-036 rotation lane executor (one bot_id per spec). `run` is DRY unless config mode is live AND --live is given; `check` validates only.")
    ap.add_argument("cmd", choices=("run", "check"))
    ap.add_argument("--config", required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    raw = json.loads(Path(a.config).read_text())
    if a.cmd == "check":
        out = check(raw, live=a.live)
        print(json.dumps(out, sort_keys=True))
        return 0 if out["ok"] else 2
    return run(raw, live_flag=a.live, once=a.once)


if __name__ == "__main__":
    sys.exit(main())
