"""H5 executor for the DEC-035 lane book `h5-lane-oc` (the "on-chain re-check" entry rule). A SUBCLASS of tools.h5_lane_executor.H5LaneExecutor in its own
module; tools/h5_executor.py, tools/h5_lane_executor.py, tools/h5_shadow.py, tools/h5_lane_shadow.py and the syn modules are NOT edited.

Ruling: /data/mal/hunt-1008/ops/manager19/QP-RULING-1103-lane-tipauth.md, Ruling 4. THIS IS A NEW ENTRY RULE, NOT H5 v1. It buys triggers the frozen rule
refuses because the rule's own inputs (a print in the chain is unaccounted for) are not known. Its rows are never pooled with, or cited as, H5 v1 or h5-lane-2
evidence. It has NO sim twin: the DEC-035 s5 live-vs-sim gap for the tagged class is "not measured".

Rule. A shadow trigger with an integer `base_breaks_unresolved_settled` == 0 goes to the pinned parser and every pinned gate untouched (this module adds no
check and changes no value for it: md5 decision-equivalence with h5-lane-2 is a test). A trigger whose ONLY refusal is `settled` >= 1 (every other pinned parser
check still applies) becomes an `OCTrigger`, and at entry, inside the 3.5 s pre-send limit (PRESEND_MAX_TRIGGER_AGE_MS, re-checked after the reads), the
executor reads the chain:
  (a) the pool (the pinned two-call snapshot): Q = real quote vault + V (the shadow's own definition) must be <= RULE_Q_MAX_LAMPORTS (40 SOL);
  (b) ONE getAccountInfo of the pool's BOOST vault, ATA(PDA(["boost_vault", pool], PumpSwap), WSOL) (tools/pump_structure_monitor.py boost_vault, the account
      the structure monitor sizes the BOOST budget from): its SPL token `amount` is taken as the BOOST budget remaining. It must be readable, a token account of
      the right mint and authority, and the amount must be above the rule's own floor (1 - BOOST_DONE_FRAC) x 17.585 SOL. Unreadable, closed, wrong shape,
      0 or below the floor: refused, fail closed. NOT VERIFIED here: that this balance is what BOOST's `boost_vault_remaining` counts down (library row
      w2-pump-idl-4 is "untested"); the first live rows are the check, and no claim rests on it.
  The chain read is capped at OC_MAX_CALLS calls and OC_RPC_BUDGET_MS ms (a guard on `self.rpc` for the read); a slower read refuses.
On success the trigger's Q and base reserve are replaced by the chain values (the shadow's view of this pool lacks a print), so the buy's min_out is priced on
chain state; the shadow value is kept as oc_q_shadow_lamports. What the snapshot cannot verify (ruling): that the detected sell was the FIRST qualifying
one, that s0 is right, that BOOST spent at the trigger print was below the floor.
Every row of a rescued trigger (executor ledger rows, lane ledger rows, the counters plan) carries gate_path "onchain_recheck" and oc_* (chain Q, BOOST
remaining, slot, rpc ms and calls). `gate_path` is NOT set on the pinned H5Trigger field, so the pinned tipauth machinery never sees this class.

Lane limits (DEC-035 s2, shared lane ledger, unchanged and binding): stake fixed 20_000_000, per-bot max open 1, mints any other bot_id has a lane-ledger row
for are refused, and a sub-budget of 50_000_000 lamports net loss (config can only lower it) with its OWN STOP file (`lane_oc.stop_path`), written when bot
P&L <= -budget, never removed by this code, re-derived from the ledger if deleted; raising it is a new bot_id (pattern of tools/h5_lane_syn_executor.py).
Recorded risk (as h5-lane-syn): the refusal of a mint another bot traded is one-way; the pinned h5-lane-2 does not refuse a mint this book holds.

MUTUAL EXCLUSION. h5-lane-oc REPLACES h5-lane-2 and h5-lane-3 and NEVER RUNS BESIDE THEM: it trades every settled = 0 trigger exactly as h5-lane-2 does, so two of
them on one shadow would double-buy each trigger. At startup it takes, and holds for its life, the bot lock files of h5-lane-2 and h5-lane-3
(`<ledger>.bot.<bot_id>.lock`, the files the pinned `_take_bot_lock` uses, so the pinned executors refuse to start while this one runs) next to its own, and
exits 2 (no restart loop) if either is held.
RPC bound. During the re-check the client's per-call timeout is cut to the time left of OC_RPC_BUDGET_MS (at most OC_CALL_TIMEOUT_S) and its retries to 0, then
restored; a timeout refuses with `oc_rpc_timeout`.
L6 (QP-RULING-1103-oc-premise, Ruling 3), the live stop rule for the BOOST-vault premise. On every oc_recheck that reaches the vault read, the first of
  (a) a CLEAN comparison (no boost_slice of the pool with slot in (trigger slot, the vault read's own context slot], no feed gap since the trigger) with
      |vault amount - the trigger's boost_vault_remaining_sol x 1e9| > 0 lamports, or
  (b) vault amount > 17.585e9 - round(trigger boost_spent_sol x 1e9) by > 0 lamports, with the trigger's boost_src "pda" or "event_authority",
writes the h5-lane-oc STOP (`write_oc_stop`, O_EXCL, never removed) with reason oc_l6_stop_a / oc_l6_stop_b and refuses that trigger. A STOP halts every new
buy, settled = 0 included (`_kill_reason`, re-read from disk before every send). Limits of what this module can see: (1) the executor's slice view is built
from the boost_slice / gap rows of the SAME intents stream it tails (the pinned executor ignores them in v1), so a slice the shadow never wrote is invisible
(a false "clean" can only cause a stop, the safe direction); (2) the lower bound of the interval is the trigger's slot, a proxy for the slot of the last
BoostBuyAndBurn event the trigger's value was copied from (the record carries no event slot); (3) a trigger without a numeric boost_vault_remaining_sol is
not scored by (a) (nothing to compare); (4) a trigger whose boost_src is missing or not pda / event_authority cannot be scored by (b) and is REFUSED, not
guessed (reason oc_l6_boost_src, no stop). DRY mode: the same checks run and the trigger is refused with oc_l6_would_stop_a / _b, an `oc_l6` row says
would_stop and NO STOP file is written (the ruling speaks of live rechecks only; dry keeps the dry run restartable and its comparisons countable).
Every executor-ledger row of this bot carries bot_id "h5-lane-oc"; the rows of a rescued trigger also carry gate_path "onchain_recheck".
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import math
import os
import struct
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from tools import h5_executor as h
from tools import h5_lane_executor as L
from tools import h5_lane_syn_executor as X  # helpers only (path guards, file presence); that module is not edited
from tools import probe_executor as pe
from tools import pumpswap_tx as tx

BOT_ID = "h5-lane-oc"
GATE_PATH_OC = "onchain_recheck"
OC_STAKE_LAMPORTS = 20_000_000
OC_SUB_BUDGET_LAMPORTS = 50_000_000
OC_MAX_OPEN = 1
OC_MAX_CALLS = 3  # the pinned snapshot is 2 calls, the BOOST vault read is 1
OC_RPC_BUDGET_MS = 1_500  # all chain reads of one re-check; the 3.5 s pre-send limit is re-checked after them
OC_CALL_TIMEOUT_S = 1.5  # per call, and never more than what is left of OC_RPC_BUDGET_MS
EXCLUDED_BOTS = ("h5-lane-2", "h5-lane-3")  # bots whose settled = 0 trading this book replaces
OC_KEYS = {"stop_path", "sub_budget_lamports"}
BOOST_FLOOR_LAMPORTS = int(round((1.0 - h.BOOST_DONE_FRAC) * h.BOOST_BUDGET_SOL * 1e9))  # the rule's condition: spent < 0.999 x budget
H5_LANE_SYN_PATHS = ("/var/lib/mal-live/h5-lane-syn",)
BOOST_BUDGET_LAMPORTS = int(round(h.BOOST_BUDGET_SOL * 1e9))  # the rule's budget (17.585 SOL), imported from the pinned executor
SLICE_POOLS_KEEP, SLICE_SLOTS_KEEP = 2048, 256  # the executor's own slice view for L6: pools remembered, slots per pool


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _l6_inputs(row: dict[str, Any]) -> dict[str, Any]:
    """What L6 needs from the raw trigger row. Anything not a finite non-negative number is None (never guessed)."""
    def lam(v: Any) -> int | None:
        try:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                return None
            return int(round(v * 1e9))
        except (OverflowError, ValueError, TypeError):  # 1e300, a huge int: one bad row must not break the batch
            return None
    src = row.get("boost_src")
    return {"vault_lamports": lam(row.get("boost_vault_remaining_sol")), "spent_lamports": lam(row.get("boost_spent_sol")),
            "boost_src": src if isinstance(src, str) else None}


def boost_vault_address(pool: str) -> tuple[str, str]:
    """(vault ATA, vault authority PDA) of a pool's BOOST vault."""
    auth, _ = Pubkey.find_program_address([b"boost_vault", bytes(Pubkey.from_string(pool))], tx.PUMPSWAP_PROGRAM)
    return str(tx.ata(auth, tx.WSOL_MINT, tx.TOKEN_PROGRAM)), str(auth)


def parse_boost_vault(info: Any, pool: str) -> tuple[int | None, str]:
    """(remaining lamports, "ok") from a getAccountInfo `value`, else (None, reason). Fail closed on every shape problem."""
    if not isinstance(info, dict):
        return None, "boost_vault_missing"
    if info.get("owner") != str(tx.TOKEN_PROGRAM):
        return None, "boost_vault_owner"
    try:
        data = pe.sim._b64(info)
    except Exception:
        return None, "boost_vault_data"
    if len(data) < 165 or data[108] != 1:
        return None, "boost_vault_shape"
    _addr, auth = boost_vault_address(pool)
    if data[0:32] != bytes(tx.WSOL_MINT) or data[32:64] != bytes(Pubkey.from_string(auth)):
        return None, "boost_vault_identity"
    return struct.unpack_from("<Q", data, 64)[0], "ok"


def _parse_oc_config(raw: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SystemExit("lane_oc refused: the config needs a `lane_oc` object")
    extra = set(raw) - OC_KEYS
    if extra:
        raise SystemExit(f"lane_oc refused: unknown keys {sorted(extra)}")
    if not isinstance(raw.get("stop_path"), str) or not raw["stop_path"]:
        raise SystemExit("lane_oc refused: stop_path is required")
    sub = raw.get("sub_budget_lamports", OC_SUB_BUDGET_LAMPORTS)
    if isinstance(sub, bool) or not isinstance(sub, int) or not 1 <= sub <= OC_SUB_BUDGET_LAMPORTS:
        raise SystemExit(f"lane_oc refused: sub_budget_lamports must be 1..{OC_SUB_BUDGET_LAMPORTS}")
    stop = X._rp(raw["stop_path"])
    lane = cfg.get("lane") if isinstance(cfg.get("lane"), dict) else {}
    guarded = [str(lane.get("stop_path") or ""), str(lane.get("ledger_path") or ""), str(cfg.get("state_dir") or ""), str(cfg.get("intents_file") or ""),
               *X.H5_LANE2_PATHS, *H5_LANE_SYN_PATHS]
    for g in guarded:
        if g and X._same_or_inside(stop, X._rp(g)):
            raise SystemExit(f"lane_oc refused: stop_path collides with a lane / h5-lane-2 / h5-lane-syn path ({g})")
    d = Path(raw["stop_path"]).parent
    with contextlib.suppress(OSError):
        d.mkdir(parents=True, exist_ok=True)
    if not d.is_dir() or not os.access(d, os.W_OK | os.X_OK):
        raise SystemExit("lane_oc refused: the directory of stop_path is not writable")
    return {"stop_path": Path(raw["stop_path"]), "sub_budget_lamports": sub}


@dataclass(frozen=True)
class OCTrigger(h.H5Trigger):
    """A trigger the pinned parser refused only for settled >= 1. `oc` is filled by the entry re-check (mutable holder, not part of equality)."""

    oc: dict = field(default_factory=dict, compare=False, repr=False)
    l6: dict = field(default_factory=dict, compare=False, repr=False)  # L6 inputs from the raw shadow row: vault_lamports, spent_lamports, boost_src

    def _tag(self, d: dict[str, Any]) -> dict[str, Any]:
        d = dict(d)
        d.pop("oc", None)
        d.pop("l6", None)
        d["gate_path"] = GATE_PATH_OC
        d.update(self.oc)
        return d

    def public(self) -> dict[str, Any]:
        return self._tag(super().public())

    def log_fields(self) -> dict[str, Any]:
        return self._tag(super().log_fields())

    def adopt_chain(self, q_lamports: int, base_reserve: int) -> None:
        """The chain's Q and base reserve replace the shadow's (its view lacks a print); the one deliberate mutation of a frozen trigger."""
        object.__setattr__(self, "q_lamports", q_lamports)
        object.__setattr__(self, "base_reserve", base_reserve)


class OCLedger(L.LaneLedger):
    """LaneLedger plus the per-bot sub-budget / max open / mint checks, evaluated in the parent's critical section; tags rescued mints' rows."""

    def __init__(self, cfg: L.LaneConfig, oc: dict[str, Any]):
        super().__init__(cfg)
        self.oc = oc
        self.tags: dict[str, dict[str, Any]] = {}
        self.cur_mint: str | None = None
        self.cur_marks: dict[str, tuple[int, int]] = {}
        self._rows: list[Any] = []

    def _write_line(self, path: Path, row: dict[str, Any]) -> None:
        if row.get("ev") in ("start", "snap"):
            return super()._write_line(path, row)
        super()._write_line(path, {**row, **self.tags.get(str(row.get("mint")), {})})

    def read(self) -> list[Any]:
        self._rows = super().read()
        return self._rows

    def oc_stop_present(self) -> bool:
        return X._present(self.oc["stop_path"])

    def write_oc_stop(self, reason: str, now_ms: int) -> None:
        path: Path = self.oc["stop_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            return  # never rewritten, never removed here
        with os.fdopen(fd, "w") as f:
            f.write(f"{reason} ts_ms={now_ms} bot_id={self.cfg.bot_id}\n")

    def bot_view(self, rows: list[Any], marks: dict[str, tuple[int, int]], now_ms: int) -> L.LaneView:
        mine = [r for r in rows if not (isinstance(r, dict) and r.get("ev") not in ("start", "snap") and r.get("bot_id") != self.cfg.bot_id)]
        return L.lane_fold(mine, marks, now_ms, self.cfg.mark_stale_s, self.cfg.bot_id)

    def oc_evaluate(self, rows: list[Any], mint: str | None, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        if stake != OC_STAKE_LAMPORTS:
            return "oc_stake_not_fixed"
        sub = self.oc["sub_budget_lamports"]
        v = self.bot_view(rows, marks, now_ms)
        if v.pnl <= -sub:
            self.write_oc_stop(f"oc_budget_stop pnl_lamports={v.pnl} sub_budget_lamports={sub}", now_ms)
            return "oc_budget_stop"
        if len(v.open) >= OC_MAX_OPEN:
            return "oc_max_open"
        reserve = self.cfg.fee_reserve_lamports * (len(v.open) + 1)
        if v.pnl - (v.open_stakes + stake + reserve) <= -sub:
            return "oc_budget_presend"
        if mint is not None:
            for r in rows:
                if isinstance(r, dict) and r.get("mint") == mint and r.get("bot_id") not in (None, self.cfg.bot_id):
                    return "oc_mint_traded_by_other_bot"
        return None

    def evaluate(self, view: L.LaneView, now_ms: int, stake: int, n_pending_extra: int = 0) -> str | None:
        why = super().evaluate(view, now_ms, stake, n_pending_extra)
        return why or self.oc_evaluate(self._rows, self.cur_mint, self.cur_marks, now_ms, stake)

    def check(self, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        if self.oc_stop_present():
            return "oc_stop_file"
        self.cur_marks = marks
        return super().check(marks, now_ms, stake)

    def reserve(self, mint: str, signature: str, marks: dict[str, tuple[int, int]], now_ms: int, stake: int, pool_gap_waived: bool = False) -> str | None:
        if self.oc_stop_present():
            return "oc_stop_file"
        self.cur_mint, self.cur_marks = mint, marks
        try:
            return super().reserve(mint, signature, marks, now_ms, stake, pool_gap_waived)
        finally:
            self.cur_mint = None

    def record(self, ev: str, mint: str, marks: dict[str, tuple[int, int]], now_ms: int, **kw: Any) -> L.LaneView | None:
        view = super().record(ev, mint, marks, now_ms, **kw)
        with contextlib.suppress(Exception):
            with self.locked():
                v = self.bot_view(self.read(), marks, now_ms)
            if v.pnl <= -self.oc["sub_budget_lamports"]:
                self.write_oc_stop(f"oc_budget_stop pnl_lamports={v.pnl} sub_budget_lamports={self.oc['sub_budget_lamports']}", now_ms)
        return view


class _Guard:
    """The RPC callable for the duration of one re-check: at most OC_MAX_CALLS calls, none started after OC_RPC_BUDGET_MS."""

    def __init__(self, real, now_ms, t0: int):
        self._real, self._now, self._t0, self.calls, self.timed_out = real, now_ms, t0, 0, False

    def __call__(self, method, params):
        if self.calls >= OC_MAX_CALLS or self._now() - self._t0 > OC_RPC_BUDGET_MS:
            self.timed_out = True
            raise pe.RpcError("oc_budget")
        self.calls += 1
        inner = getattr(self._real, "rpc", None)  # a LimitedRpc: cut the client's own timeout (15 s) and retries for this call, then restore
        saved = (getattr(inner, "_timeout", None), getattr(self._real, "retries", None))
        if saved[0] is not None:
            inner._timeout = max(0.05, min(OC_CALL_TIMEOUT_S, (OC_RPC_BUDGET_MS - (self._now() - self._t0)) / 1000.0))
        if saved[1] is not None:
            self._real.retries = 0
        try:
            return self._real(method, params)
        except pe.RpcError as exc:
            if "timeout" in str(exc.args[0] if exc.args else exc) or "oc_budget" in str(exc):
                self.timed_out = True
            raise
        finally:
            if saved[0] is not None:
                inner._timeout = saved[0]
            if saved[1] is not None:
                self._real.retries = saved[1]

    def __getattr__(self, name):
        return getattr(self._real, name)


_Base = L.H5LaneExecutor


class H5LaneOCExecutor(_Base):
    def __init__(self, rpc, cfg: dict[str, Any], keypair, **kw):
        self._oc_cfg = _parse_oc_config(cfg.get("lane_oc"), cfg)
        self._oc_mints: dict[str, dict[str, Any]] = {}  # mint -> oc holder of its latest OC trigger (tags every executor row of the mint)
        self.oc_refusals: dict[str, int] = {}
        self._slices: dict[str, list[int]] = {}  # pool -> slots of the boost_slice rows seen on the intents stream (L6 cleanliness)
        self._l6_latched: str | None = None  # set on a live L6 trip BEFORE the STOP write; never cleared in this process
        self._l6_gap_ms: int | None = None  # now_ms when the last coverage-losing gap row was read
        lane = cfg.get("lane")
        if not isinstance(lane, dict) or lane.get("bot_id") != BOT_ID:
            raise SystemExit(f"lane_oc refused: lane.bot_id must be {BOT_ID!r}")
        if lane.get("stake_lamports") != OC_STAKE_LAMPORTS:
            raise SystemExit(f"lane_oc refused: lane.stake_lamports must be {OC_STAKE_LAMPORTS}")
        if cfg.get("tipauth_gate") not in (None, ""):
            raise SystemExit("lane_oc refused: the tipauth path is off for this book")
        orig, oc = L.LaneLedger, self._oc_cfg
        L.LaneLedger = lambda c: OCLedger(c, oc)  # type: ignore[assignment,misc]  # the parent builds its ledger by this global name
        try:
            super().__init__(rpc, cfg, keypair, **kw)
        finally:
            L.LaneLedger = orig  # type: ignore[misc]
        self._oc_excl: list[Any] = []
        if self.lane is not None:
            self._take_exclusion_locks()

    def _take_exclusion_locks(self) -> None:
        """Hold the bot locks of the books this one replaces; refuse to start (exit 2) if any is held."""
        import fcntl
        for bot in EXCLUDED_BOTS:
            f = open(f"{self.lane.path}.bot.{bot}.lock", "a")  # type: ignore[union-attr]
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                self.release_lane_lock()
                print(f"lane_oc refused: {bot} holds its bot lock; h5-lane-oc replaces it and never runs beside it", file=sys.stderr)
                raise SystemExit(2) from None
            self._oc_excl.append(f)

    def release_lane_lock(self) -> None:
        for f in getattr(self, "_oc_excl", []):
            f.close()
        self._oc_excl = []
        super().release_lane_lock()

    @property
    def h5(self) -> h.H5Limits:
        lim = super().h5
        return replace(lim, max_open=min(lim.max_open, OC_MAX_OPEN))

    def _kill_reason(self) -> str | None:
        why = super()._kill_reason()
        if why:
            return why
        if getattr(self, "_l6_latched", None):
            return self._l6_latched  # in memory: holds even if the STOP file could not be written
        if self.lane is None:
            return why
        return "oc_stop_file" if self.lane.oc_stop_present() else None  # type: ignore[attr-defined]

    # -- parser wrap: only settled >= 1 rows are touched ----------------------------------------------------------------------
    def intent_tick(self) -> int:
        orig = h.parse_shadow_trigger

        def parse(row, *a, **kw):
            s = row.get("base_breaks_unresolved_settled") if isinstance(row, dict) else None
            if not (isinstance(row, dict) and row.get("type") == "trigger" and row.get("variant") == self.trigger_variant
                    and isinstance(s, int) and not isinstance(s, bool) and s >= 1):
                return orig(row, *a, **kw)  # settled = 0 (and everything else): the pinned parser, byte for byte
            trg, bad = orig({**row, "base_breaks_unresolved_settled": 0}, *a, **kw)  # every other pinned parser check still applies
            if trg is None:
                return trg, bad
            oc = OCTrigger(**{f.name: getattr(trg, f.name) for f in dataclasses.fields(h.H5Trigger)})
            return replace(oc, base_breaks_unresolved_settled=s, l6=_l6_inputs(row)), None

        orig_tail = h.tail_lines

        def tail(*a, **kw):
            lines = orig_tail(*a, **kw)
            self._observe(lines)
            return lines

        h.parse_shadow_trigger = parse
        h.tail_lines = tail
        try:
            return super().intent_tick()
        finally:
            h.parse_shadow_trigger = orig
            h.tail_lines = orig_tail

    def _observe(self, lines: list[str]) -> None:
        """L6's slice view: the boost_slice and gap rows of the stream, read-only; it never changes what the pinned reader sees."""
        for line in lines:
            if '"boost_slice"' not in line and '"gap"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("type") == "gap":
                if row.get("flags_pools") is not False:  # the pinned reader's own reading: only an explicit false is harmless
                    self._l6_gap_ms = self.now_ms()
            elif row.get("type") == "boost_slice" and isinstance(row.get("pool"), str) and _is_int(row.get("slot")):
                sl = self._slices.pop(row["pool"], [])
                sl.append(row["slot"])
                self._slices[row["pool"]] = sl[-SLICE_SLOTS_KEEP:]
                while len(self._slices) > SLICE_POOLS_KEEP:
                    del self._slices[next(iter(self._slices))]

    # -- entry ----------------------------------------------------------------------------------------------------------------
    def _hard_refusal(self, trg: h.H5Trigger, now: int) -> str | None:
        if self.lane is not None:
            self.lane.cur_mint = trg.mint  # type: ignore[attr-defined]
        try:
            why = super()._hard_refusal(trg, now)
        finally:
            if self.lane is not None:
                self.lane.cur_mint = None  # type: ignore[attr-defined]
        if why or not isinstance(trg, OCTrigger):
            return why
        return self._oc_recheck(trg)

    def _oc_refuse(self, trg: OCTrigger, reason: str, **kw: Any) -> str:
        self.oc_refusals[reason] = self.oc_refusals.get(reason, 0) + 1
        trg.oc.update(kw)
        self._log("oc_recheck", trg.mint, ok=False, reason=reason, **kw)
        return reason

    def _oc_recheck(self, trg: OCTrigger) -> str | None:
        self._oc_mints[trg.mint] = trg.oc
        t0 = self.now_ms()
        guard = _Guard(self.rpc, self.now_ms, t0)
        real = self.rpc
        self.rpc = guard
        snap = None
        err: str | None = None
        pool = ""
        boost: int | None = None
        why_b = "not_read"
        boost_slot: int | None = None
        try:
            snap, pool, err = self._snapshot(trg.mint)
            if snap is not None and snap.quote_priced is not None and pool == trg.pool and snap.quote_priced <= h.RULE_Q_MAX_LAMPORTS:
                try:
                    vault, _auth = boost_vault_address(trg.pool)
                    res = guard("getAccountInfo", [vault, {"encoding": "base64", "commitment": self.commitment}])
                    boost, why_b = parse_boost_vault(res.get("value") if isinstance(res, dict) else None, trg.pool)
                    ctx = res.get("context") if isinstance(res, dict) else None
                    cs = ctx.get("slot") if isinstance(ctx, dict) else None
                    boost_slot = cs if _is_int(cs) else None
                except (Exception, SystemExit):
                    boost, why_b = None, "boost_vault_unreadable"
        finally:
            self.rpc = real
        ms = self.now_ms() - t0
        meta: dict[str, Any] = dict(oc_rpc_ms=ms, oc_rpc_calls=guard.calls, oc_q_shadow_lamports=trg.q_lamports)
        if guard.timed_out and (snap is None or boost is None):
            return self._oc_refuse(trg, "oc_rpc_timeout", oc_q_error=err, **meta)  # a call hit its timeout or the shared deadline
        if snap is None:
            return self._oc_refuse(trg, "oc_q_unreadable", oc_q_error=err, **meta)
        if snap.quote_priced is None:
            return self._oc_refuse(trg, "oc_q_unreadable", oc_q_error="no_v", **meta)
        if pool != trg.pool:
            return self._oc_refuse(trg, "oc_pool_mismatch", **meta)
        meta["oc_q_lamports"] = snap.quote_priced
        if snap.quote_priced > h.RULE_Q_MAX_LAMPORTS:
            return self._oc_refuse(trg, "oc_q_above_40", **meta)
        if boost is None:
            return self._oc_refuse(trg, "oc_boost_unreadable", oc_boost_error=why_b, **meta)
        meta["oc_boost_remaining_lamports"] = boost
        meta["oc_boost_slot"] = boost_slot
        l6_why = self._l6(trg, boost, boost_slot if boost_slot is not None else snap.slot, meta)
        if l6_why is not None:
            return self._oc_refuse(trg, l6_why, **meta)
        if boost <= BOOST_FLOOR_LAMPORTS:
            return self._oc_refuse(trg, "oc_boost_exhausted", **meta)
        if ms > OC_RPC_BUDGET_MS:
            return self._oc_refuse(trg, "oc_rpc_slow", **meta)
        now = self.now_ms()
        if now - trg.decision_ms > self.h5.max_trigger_age_ms:
            return self._oc_refuse(trg, "stale_trigger", **meta)  # the 3.5 s pre-send limit, after the reads
        trg.adopt_chain(snap.quote_priced, snap.base_reserve)
        trg.oc.update(meta, oc_boost_vault=boost_vault_address(trg.pool)[0], oc_slot=snap.slot)
        self.pool_cache[trg.mint] = (snap.ps, now)  # the entry prices on this read; no second one
        if self.lane is not None:
            self.lane.tags[trg.mint] = {"gate_path": GATE_PATH_OC, **trg.oc}  # type: ignore[attr-defined]
        self._log("oc_recheck", trg.mint, ok=True, reason="ok", **trg.oc)
        return None

    def _l6(self, trg: OCTrigger, boost: int, read_slot: int, meta: dict[str, Any]) -> str | None:
        """L6 (Ruling 3). Annotates `meta` (oc_l6_*), returns a refusal reason or None. A trip writes the oc STOP (live) or only logs (dry)."""
        info = trg.l6
        vault_trg, spent, src = info.get("vault_lamports"), info.get("spent_lamports"), info.get("boost_src")
        clean = (_is_int(read_slot) and read_slot >= trg.trigger_slot
                 and not (self._l6_gap_ms is not None and self._l6_gap_ms >= trg.decision_ms)
                 and not any(trg.trigger_slot <= sl <= read_slot for sl in self._slices.get(trg.pool, ())))  # the trigger's own slot counts (conservative)
        meta["oc_l6_clean"] = clean
        # report-only, SIGNED, on every row that reaches the vault read: A - (17.585e9 - spent); null only when spent is missing. Not clamped.
        meta["oc_l6_b_diff_lamports"] = None if spent is None else boost - (BOOST_BUDGET_LAMPORTS - spent)
        if vault_trg is None:
            meta["oc_l6_a"] = "no_trigger_vault_value"
        elif not clean:
            meta["oc_l6_a"] = "not_clean"
        else:
            meta["oc_l6_a"] = "agree" if boost == vault_trg else "disagree"
            meta["oc_l6_a_diff_lamports"] = abs(boost - vault_trg)
            if boost != vault_trg:
                return self._l6_trip(trg, "a", meta, vault_lamports=boost, trigger_vault_lamports=vault_trg)
        if src not in h.TIPAUTH_BOOST_SRC_OK or spent is None:  # the vault identity must be known on-chain; a behavioural or missing one is not guessed at
            meta["oc_l6_b"] = "no_boost_src" if src is None else "src_not_scored"
            return "oc_l6_boost_src"
        bound = BOOST_BUDGET_LAMPORTS - spent
        meta["oc_l6_b"] = "ok" if boost <= bound else "stale_high"
        if boost > bound:
            return self._l6_trip(trg, "b", meta, vault_lamports=boost, bound_lamports=bound)
        return None

    def _l6_trip(self, trg: OCTrigger, which: str, meta: dict[str, Any], **kw: Any) -> str:
        """The first (a) or (b) hit: STOP in live mode (O_EXCL, never removed), a would-stop row in dry mode."""
        live = not self.dry_run and self.lane is not None
        reason = f"oc_l6_stop_{which}" if live else f"oc_l6_would_stop_{which}"
        stop_written = False
        if self.dry_run is False:
            self._l6_latched = f"oc_l6_latched_{which}"  # first: no further send even if the file cannot be written
        if live:
            detail = " ".join(f"{k}={v}" for k, v in kw.items())
            try:
                self.lane.write_oc_stop(f"oc_l6_stop_{which} pool={trg.pool} {detail}", self.now_ms())  # type: ignore[union-attr]
            except Exception as exc:  # EACCES, a broken link, any OSError: the latch holds; open positions keep being exit-polled
                self._alert("oc_l6_stop_write_failed", trg.mint, label=type(exc).__name__)
            stop_written = self.lane.oc_stop_present()  # type: ignore[union-attr]
        self._log("oc_l6", trg.mint, which=which, would_stop=not live, stop_written=stop_written, pool=trg.pool, trigger_slot=trg.trigger_slot, **kw)
        return reason

    # -- tags on every row of a rescued mint ----------------------------------------------------------------------------------
    def _oc_tag(self, mint: str) -> dict[str, Any] | None:
        got = self._oc_mints.get(mint)
        if got is not None:
            return got
        for src in (self.state.pending.get(mint), self.state.open.get(mint)):  # after a restart: the durable plan says it
            t = ((src or {}).get("h5") or {}).get("trigger") or {}
            if t.get("gate_path") == GATE_PATH_OC:
                return {k: v for k, v in t.items() if k.startswith("oc_")}
        return None

    def _lane_record(self, ev: str, mint: str, **kw: Any) -> None:
        tag = self._oc_tag(mint) if mint else None
        if tag is not None and self.lane is not None and "oc_q_lamports" in tag:
            self.lane.tags[mint] = {"gate_path": GATE_PATH_OC, **tag}  # type: ignore[attr-defined]
        super()._lane_record(ev, mint, **kw)

    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        tag = self._oc_tag(mint) if mint else None
        if tag is not None:
            kw = {**tag, **kw}
            kw["gate_path"] = GATE_PATH_OC
        kw.setdefault("bot_id", BOT_ID)  # every row of this book, tagged class or not
        super()._log(kind, mint, **kw)
        if kind in ("decision", "skip") and mint:
            self._oc_mints.pop(mint, None)  # consumed by the row that ends the trigger; later rows of a held position re-derive it from the durable plan


def main(argv: list[str] | None = None) -> int:
    """tools.h5_lane_executor.main with H5LaneOCExecutor in place of H5LaneExecutor."""
    orig = L.H5LaneExecutor
    L.H5LaneExecutor = H5LaneOCExecutor  # type: ignore[misc]  # L.main rebinds h.H5Executor to this global
    try:
        return L.main(argv)
    finally:
        L.H5LaneExecutor = orig  # type: ignore[misc]


if __name__ == "__main__":
    sys.exit(main())
