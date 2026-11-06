"""r4bt_c0_executor: the r4-b-T PROFILE of the DRY r4-b executor (bot_id `r4bt-a1`). PAPER / DRY ONLY: LIVE_BUILT is False, no key, no wallet, no send.

Ruling: /data/mal/hunt-1008/ops/manager20/QP-RULING-1106-r4bt-c0.md build-list item 2 and findings F1, F2 (a1 to a5). Builds on tools/r4b_arena_executor.py (EXP-027's
DRY bridge), which is NOT edited: the EXP-027 path (bot_id `r4b-exp027`) keeps its bytes and its behavior. This module is a separate subclass and config; it refuses
nothing of EXP-027's and shares none of its state files (state names carry the r4-b-T bot_id through the bridge's `cfg.bot_id`).

WHAT THE PROFILE ADDS (each is a refusal path with a test in tools/test_r4bt_c0_executor.py)
  PIN        bot_id `r4bt-a1`, W (frozen_sha) and the registered change_id come from config and must equal the code pins (REGISTERED_*); the declog `start` row must repeat
             W (the bridge latches frozen_sha_mismatch) AND the config change_id (this profile latches `change_id_mismatch`). a2: no buy otherwise. A successor run at W
             (Am.27 item 20) needs its change_id added to REGISTERED_CHANGE_IDS in a reviewed PR; no other host, change_id or sha ever feeds the sender (a1).
  F1 TWIN    `twin_round_trip` (tools.r4b_book.round_trip) is REFUSED for r4-b-T (Am.27 item 24: "refused, as the deciding route and as a report-only figure"). This module
             imports neither tools.r4b_book nor round_trip (a test reads the source). Twins come ONLY from the twin writer's files in config `twin_dir`:
               twins.run.json   the run record   (RUN_RECORD_NAME)
               twins.jsonl      one row per sent order (TWINS_NAME)
             and the buy is refused unless the run record is fresh (the HV4 `twin_gate` + grace pattern): see `twin_gate_problem` and TWIN INTERFACE = the twin builder's, PR #1243: /data/mal/hunt-1008/ops/reports/2026-11-06/r4bt-c0-twin-interface.md. The executor reads only:
  twins.run.json  schema r4bt_c0_twin_run_v1, status "ok", run_ms (fresh within TWIN_HEARTBEAT_MAX_MS), w_sha == W, engine_sha256 == ENGINE_SHA256, twins_sha256 == sha256(twins.jsonl)
  twins.jsonl     schema r4bt_c0_twin_row_v1 rows; only `order_id` is read (a row exists for every sent order once its entry leg is observable)
`twin_gate_problem` is the only reader. The run record carries no bot_id / change_id: the pin is w_sha and the engine sha. The executor's ORDER LEDGER for the twin (the
twin's input) is not written by this DRY build; it belongs to the live port.

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
from tools import hv4_live_c0 as HV
from tools import r4b_arena_executor as X

LIVE_BUILT = False  # the live port is a later, separate PR; a live config is refused
BOT_ID = "r4bt-a1"
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
TWIN_GRACE_MS = 60_000  # the HV4 pattern (hv4_c0_parity.TWIN_GRACE_MS): a closed trip's twin row may arrive this long after its sell landed
TWIN_HEARTBEAT_MAX_MS = 300_000  # a declared choice, not measured: a run record older than this refuses buys
TWIN_FUTURE_SKEW_MS = 300_000
TWIN_MAX_BYTES = 32 * 1024 * 1024
RUN_RECORD_NAME, TWINS_NAME = "twins.run.json", "twins.jsonl"
RUN_SCHEMA, TWIN_SCHEMA = "r4bt_c0_twin_run_v1", "r4bt_c0_twin_row_v1"
CARRY_SCHEMA, READ_SCHEMA = "r4bt_a3_carry_v1", "r4bt_read_record_v1"
CARRY_VERDICTS = ("NONE", "RECORD_ONLY", "NEEDS_CHECK", "HALT", "ERROR")
CARRY_CLEAR = ("NONE", "RECORD_ONLY")
FILE_MAX_BYTES = 1 << 20
A3_MAX_AGE_MS, A3_FUTURE_SKEW_MS = HV.A3_MAX_AGE_MS, HV.A3_FUTURE_SKEW_MS
OWNER_FILE_DEFAULT = "/etc/mal-r4bt-c0/r4bt-a1/CANARY_DEC.md"
LIVE_OK_DEFAULT = "/etc/mal-arena-live/r4bt-c0/r4bt-a1/LIVE_OK"
TIER_DEFAULT = "/etc/mal-arena-live/TIER"
SHA64_RE = re.compile(r"[0-9a-f]{64}")
CHANGE_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

_PROFILE_KEYS = ("change_id", "twin_dir", "a3_carry_path", "a3_applied_path", "read_record_path", "read_result_sha256", "qp_comment_id", "owner_file", "live_ok_path", "tier_path")


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
    a3_applied_path: str = ""
    read_record_path: str = ""
    read_result_sha256: str = ""
    qp_comment_id: str = ""
    owner_file: str = OWNER_FILE_DEFAULT
    live_ok_path: str = LIVE_OK_DEFAULT
    tier_path: str = TIER_DEFAULT


def make_config(d: Mapping[str, Any]) -> R4btConfig:
    """Fail closed: any pin that does not hold raises Refused. Caps are VALIDATED against DEC-032 section 3 (a value above is refused, not clipped) and required."""
    d = dict(d)
    if d.get("mode", B.DRY) != B.DRY or LIVE_BUILT:
        raise Refused("live_not_built: LIVE_BUILT is False, only mode 'dry' exists")
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
    for k in ("twin_dir", "a3_carry_path", "a3_applied_path", "read_record_path"):
        if not isinstance(d.get(k), str) or not os.path.isabs(d[k]):
            raise Refused(f"config {k} is required (absolute path)")
    if not isinstance(d.get("read_result_sha256"), str) or not SHA64_RE.fullmatch(d["read_result_sha256"]):
        raise Refused("config read_result_sha256 is required (64 hex)")
    if not isinstance(d.get("qp_comment_id"), str) or not re.fullmatch(r"[0-9]{6,20}", d["qp_comment_id"]):
        raise Refused("config qp_comment_id is required (digits)")
    base = {k: v for k, v in d.items() if k not in _PROFILE_KEYS and k != "bot_id"}
    try:
        cfg = X.make_config(base)  # the EXP-027 validator (bot_id defaults to r4b-exp027 there): pins W as 40 hex, slot_s, end_ms, no sl_backstop, floor from funded
    except ValueError as exc:
        raise Refused(str(exc)) from None
    if cfg.stake_lamports != d["stake_lamports"] or cfg.max_open != d["max_open"] or cfg.total_loss_lamports != d["total_loss_lamports"]:
        raise Refused("caps changed in validation")  # unreachable unless the EXP-027 validator changes: the caps are not clipped here
    extra = {k: d[k] for k in _PROFILE_KEYS if k in d}
    return R4btConfig(**{**{f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)}, "bot_id": BOT_ID, **extra})


# --- safe small-file reads ------------------------------------------------------------------------------------------------------------------------


def _read_small(path: Any, root_checks: bool, max_bytes: int = FILE_MAX_BYTES) -> tuple[bytes | None, str | None, os.stat_result | None]:
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
    if ls.st_mode & 0o022 or ls.st_uid != 0 and (root_checks or ls.st_uid != os.getuid()):
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


def _json_obj(path: Any, root_checks: bool, what: str, last_line: bool = False) -> tuple[dict[str, Any] | None, str | None, os.stat_result | None]:
    data, why, st = _read_small(path, root_checks)
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


def twin_gate_problem(twin_dir: str, now_ms: int, orders: Mapping[str, Mapping[str, Any]] | None, cfg: R4btConfig, root_checks: bool = True) -> str | None:
    """Fail closed (the HV4 twin_gate + grace pattern). The twin writer's run record must exist, carry this bot_id / change_id / W / the engine pin, and be fresh
    (heartbeat within TWIN_HEARTBEAT_MAX_MS, not future-stamped). Then every closed order whose sell landed more than TWIN_GRACE_MS ago must have a row in twins.jsonl
    (inside the grace the twin can still arrive). A pin mismatch is `twin_gate:pin_mismatch` (the bridge latches it). Returns the refusal label or None."""
    rec, why, _ = _json_obj(os.path.join(twin_dir, RUN_RECORD_NAME), root_checks, "twin_run_record")
    if why:
        return "twin_gate:" + why
    if rec.get("schema") != RUN_SCHEMA:
        return "twin_gate:run_record_schema"
    if rec.get("w_sha") != cfg.frozen_sha or rec.get("engine_sha256") != ENGINE_SHA256:
        return "twin_gate:pin_mismatch"
    if rec.get("status") != "ok":
        return "twin_gate:status_not_ok"
    hb = rec.get("run_ms")
    if not _int(hb):
        return "twin_gate:heartbeat_missing"
    if hb - now_ms > TWIN_FUTURE_SKEW_MS:
        return "twin_gate:heartbeat_future"
    if now_ms - hb > TWIN_HEARTBEAT_MAX_MS:
        return "twin_gate:stale"
    data, why, _ = _read_small(os.path.join(twin_dir, TWINS_NAME), root_checks, TWIN_MAX_BYTES)
    if why:
        return "twin_gate:twins_unreadable"
    if rec.get("twins_sha256") != hashlib.sha256(data).hexdigest():
        return "twin_gate:twins_sha_mismatch"  # the run record is written AFTER twins.jsonl and names its sha256
    due = [oid for oid, o in (orders or {}).items() if o.get("status") == "closed" and _int(o.get("sell_landed_ms")) and now_ms - int(o["sell_landed_ms"]) > TWIN_GRACE_MS]
    if due:
        have = _order_ids(data)
        if any(oid not in have for oid in due):
            return "twin_gate:row_missing"
    return None


def _order_ids(data: bytes) -> set[str]:
    """The order ids that have a well-formed row (schema r4bt_c0_twin_row_v1) in twins.jsonl bytes. A malformed line is skipped, never trusted."""
    out: set[str] = set()
    for ln in data.decode("utf-8", "replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("schema") == TWIN_SCHEMA and isinstance(r.get("order_id"), str):
            out.add(r["order_id"])
    return out


# --- A3 regime (item 12) and the Am.3 pooled guard, read side -------------------------------------------------------------------------------------


def a3_problem(cfg: R4btConfig, now_ms: int, root_checks: bool = True) -> tuple[str | None, str | None]:
    """(latch, refuse). See the module docstring (A3). Fail closed: any unreadable input is a latch."""
    car, why, st = _json_obj(cfg.a3_carry_path, root_checks, "a3_carry")
    if why:
        return why, None
    if car.get("schema") != CARRY_SCHEMA or car.get("bot_id") != BOT_ID or car.get("change_id") != cfg.change_id:
        return "a3_carry_identity", None
    rec_ms = _iso_ms(car.get("run_utc"))
    if rec_ms is None:
        return "a3_carry_no_run_utc", None
    if rec_ms - now_ms > A3_FUTURE_SKEW_MS:
        return "a3_carry_future", None
    if now_ms - rec_ms > A3_MAX_AGE_MS:
        return "a3_carry_stale", None
    v = car.get("verdict")
    if v not in CARRY_VERDICTS:
        return "a3_carry_verdict_unknown", None
    if v in ("HALT", "ERROR"):
        return f"a3_carry:{v}", None
    refuse = None
    if v == "NEEDS_CHECK":
        dl = car.get("deadline_ms")
        if not _int(dl) or now_ms >= dl:
            return "a3_needs_check_deadline", None
        refuse = "a3_needs_check"
    # DEC-032 Am.3 pooled guard: the newest line of a3-applied.jsonl for the SAME run
    line, why, _ = _json_obj(cfg.a3_applied_path, root_checks, "a3_guard", last_line=True)
    if why:
        return "a3_guard_unreadable", None
    app_ms = _iso_ms(line.get("run_utc"))
    verdict = line.get("verdict")
    if app_ms is None or verdict not in HV.A3_GUARD_VERDICTS:
        return "a3_guard_malformed", None
    if app_ms > rec_ms:
        return "a3_guard_ahead", None
    if app_ms < rec_ms:
        if now_ms - max(rec_ms, int(st.st_mtime * 1000)) <= HV.A3_GUARD_LAG_MS:
            return None, "a3_guard_pending"
        return "a3_guard_missing", None
    if verdict not in HV.A3_GUARD_CLEAR:
        return "a3_guard:" + str(verdict), None
    return None, refuse


# --- live gates (the owner's confirmation, LIVE_OK, TIER) -----------------------------------------------------------------------------------------


def live_gate_problems(cfg: R4btConfig, root_checks: bool = True) -> list[str]:
    """Every LIVE gate that is not met, in order. Empty = all met. Per bot: the owner's confirmation line, LIVE_OK (exactly the bot_id), and the TIER file reading T1."""
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
    tier, why = B.read_tier(Path(cfg.tier_path), root_checks, allowed=(HV.TIER_NAME,))
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
    """The start gate. LIVE is refused (LIVE_BUILT False). A DRY start on a non-synthetic root needs the read record (a4: no start against the real declog before the
    screen PASS has QP's OK). Returns a refusal label or None."""
    if live or LIVE_BUILT:
        return "live_not_built"
    if not HV.synthetic_root(cfg.root):
        why = read_record_problem(cfg, root_checks)
        if why:
            return "start_" + why
    return None


# --- latency (F2) ---------------------------------------------------------------------------------------------------------------------------------


def landed_buy_latencies_t(orders: Mapping[str, Mapping[str, Any]], slot_s: float, slot_s_by_date: Mapping[str, float] | None = None) -> list[dict[str, Any]]:
    """One record per landed buy, landing order. `slots` = (buy_landed_ms - decision_ms) / 1000 / slot_s: the origin is the DECISION ROW's instant `ms` (decision_ms).
    k19 / k30 = round(1.9 / slot_s), round(3.0 / slot_s) (np.round), x19 / x30 = slots - k. `sdT_slots` = landed_slot - decision_slot (the EXP-027 origin) is carried beside
    as a diagnostic and never decides."""
    rows = []
    for oid, o in orders.items():
        if not _int(o.get("buy_landed_ms")):
            continue
        ss, src = X.order_slot_s(o, slot_s, slot_s_by_date)
        dm = o.get("decision_ms")
        lat = (o["buy_landed_ms"] - dm) / 1000.0 if _int(dm) and o["buy_landed_ms"] >= dm else None
        d = None if lat is None else lat / ss
        k19, k30 = X.k_slots(X.MATCH_P50_S, ss), X.k_slots(X.P5_SOFT_HALT_S, ss)
        ds, ls = o.get("decision_slot"), o.get("buy_landed_slot")
        rows.append({"order_id": oid, "landed_ms": o["buy_landed_ms"], "slots": d, "lat_s": lat, "k19": k19, "k30": k30, "x19": None if d is None else d - k19,
                     "x30": None if d is None else d - k30, "slot_s_used": ss, "slot_s_source": src, "gap": bool(o.get("gap")),
                     "sdT_slots": (ls - ds) if _int(ds) and _int(ls) and ls >= ds else None})
    rows.sort(key=lambda r: (r["landed_ms"], r["order_id"]))
    return rows


def latency_summary_t(orders: Mapping[str, Mapping[str, Any]], slot_s: float, *, window: int = X.P5_WINDOW, slot_s_by_date: Mapping[str, float] | None = None) -> dict[str, Any]:
    """The `X.latency_summary` keys that `X.latency_check` reads, counted from the decision row's instant; the sd(T) figures of EXP-027 sit beside it under `diag_sdT`."""
    rows = landed_buy_latencies_t(orders, slot_s, slot_s_by_date)
    meas = [r for r in rows if r["slots"] is not None]
    last = meas[-window:]
    sd = [r["sdT_slots"] for r in rows if r["sdT_slots"] is not None]
    return {"origin": "decision row ms (sd(T) is a diagnostic beside it)", "slot_s": slot_s, "landed_buys": len(rows), "measured": len(meas), "unmeasured": len(rows) - len(meas),
            "window": window, "window_n": len(last), "window_p50_s": X._med([r["lat_s"] for r in last]), "all_p50_s": X._med([r["lat_s"] for r in meas]),
            "non_gap_p50_s": X._med([r["lat_s"] for r in meas if not r["gap"]]), "window_x30_p50_slots": X._med([r["x30"] for r in last]),
            "all_x19_p50_slots": X._med([r["x19"] for r in meas]), "all_slots_p50": X._med([r["slots"] for r in meas]),
            "wall_measured": 0, "wall_missing": len(rows), "wall_window_p50_s": None, "wall_all_p50_s": None,
            "diag_sdT": {"measured": len(sd), "all_slots_p50": X._med(sd), "origin": "sd(T) = decision_slot: diagnostic only"}}


# --- the bridge -----------------------------------------------------------------------------------------------------------------------------------


class R4btBridge(X.R4bBridge):
    """The EXP-027 DRY bridge with the r4-b-T profile. It skips R4bBridge.__init__ (which pins bot_id to EXP-027's) and repeats its setup; X is not edited."""

    twin_round_trip = staticmethod(twin_round_trip)  # F1: refused

    def __init__(self, cfg: R4btConfig, port: B.OrderPort, *, now_ms: Any = None, dry_replay_caps: bool = False, live_ok_path: Path | None = None,
                 tier_path: Path | None = None, root_checks: bool = True, close_blocked: Any = None, slot_s_by_date: Mapping[str, float] | None = None,
                 gap_clear_dir: Path | str | None = None, require_gap_review: bool = False) -> None:
        if not isinstance(cfg, R4btConfig) or cfg.bot_id != BOT_ID or cfg.frozen_sha != REGISTERED_W or cfg.change_id not in REGISTERED_CHANGE_IDS:
            raise SystemExit("r4bt executor start refused: bot / W / change_id pin")
        if cfg.mode != B.DRY or port.live or LIVE_BUILT:
            raise SystemExit("r4bt executor start refused: live_not_built")
        if dry_replay_caps:
            raise SystemExit("r4bt executor start refused: dry_replay_caps is EXP-027's replay tool, not an r4-b-T mode")
        why = start_refusal(cfg, root_checks=root_checks)
        if why:
            raise SystemExit(f"r4bt executor start refused: {why}")
        self.root_checks_r4bt = bool(root_checks)
        self.slot_s_by_date = dict(slot_s_by_date or {})
        if any(not _num(v) or not X.SLOT_S_MIN <= v <= X.SLOT_S_MAX for v in self.slot_s_by_date.values()):
            raise SystemExit("r4bt executor start refused: slot_s_by_date out of range")
        self.dry_replay_caps = False
        self.require_gap_review = bool(require_gap_review)
        self._a3_refuse_t: str | None = None
        self.es_path = Path(cfg.state_dir) / f"r4b-state-{cfg.mode}.json"
        self.es = self._load_es()
        B.Bridge.__init__(self, cfg, port, now_ms=now_ms, live_ok_path=live_ok_path or Path(cfg.live_ok_path), tier_path=tier_path or Path(cfg.tier_path),
                          root_checks=root_checks, close_blocked=close_blocked, gap_clear_dir=gap_clear_dir)
        self._log("r4bt_start", w=cfg.frozen_sha, change_id=cfg.change_id, live_gates_open=live_gate_problems(cfg, root_checks))

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
        latch, refuse = a3_problem(self.cfg, now, self.root_checks_r4bt)
        if latch:
            self._latch(LATCH_A3, latch)
            return "latched:" + LATCH_A3
        if refuse:
            self._count(refuse + "_refusal")
            return refuse
        why = twin_gate_problem(self.cfg.twin_dir, now, self.state.orders, self.cfg, self.root_checks_r4bt)
        if why:
            if why.startswith("twin_gate:pin_mismatch"):
                self._latch(LATCH_TWIN_PIN, why)
            self._count("twin_gate_refusal")
            return why
        return None

    # -- F2: latency origin is the decision row's instant
    def latency_report(self) -> dict[str, Any]:
        s = latency_summary_t(self.state.orders, self.cfg.slot_s, slot_s_by_date=self.slot_s_by_date)
        return {"summary": s, "check": X.latency_check(s)}


# --- CLI: config check only (no executor loop, no send) ---------------------------------------------------------------------------------------------


def check(config_path: str, *, live: bool = False, root_checks: bool = True) -> dict[str, Any]:
    """Validate a config file: make_config, start_refusal and the open live gates. Returns {"ok", "problem", "live_gates_open"}; never starts anything."""
    try:
        cfg = make_config(json.loads(Path(config_path).read_text()))
    except (OSError, ValueError) as exc:
        return {"ok": False, "problem": f"config: {exc}", "live_gates_open": None}
    why = start_refusal(cfg, live=live, root_checks=root_checks)
    return {"ok": why is None, "problem": why, "live_gates_open": live_gate_problems(cfg, root_checks)}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b-T C0 executor profile: config check only. LIVE_BUILT is False.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args(argv)
    out = check(a.config, live=a.live)
    print(json.dumps(out, sort_keys=True))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
