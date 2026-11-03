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
"""
from __future__ import annotations

import contextlib
import dataclasses
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
OC_KEYS = {"stop_path", "sub_budget_lamports"}
BOOST_FLOOR_LAMPORTS = int(round((1.0 - h.BOOST_DONE_FRAC) * h.BOOST_BUDGET_SOL * 1e9))  # the rule's condition: spent < 0.999 x budget
H5_LANE_SYN_PATHS = ("/var/lib/mal-live/h5-lane-syn",)


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

    def _tag(self, d: dict[str, Any]) -> dict[str, Any]:
        d = dict(d)
        d.pop("oc", None)
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
        self._real, self._now, self._t0, self.calls = real, now_ms, t0, 0

    def __call__(self, method, params):
        if self.calls >= OC_MAX_CALLS or self._now() - self._t0 > OC_RPC_BUDGET_MS:
            raise pe.RpcError("oc_budget")
        self.calls += 1
        return self._real(method, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


_Base = L.H5LaneExecutor


class H5LaneOCExecutor(_Base):
    def __init__(self, rpc, cfg: dict[str, Any], keypair, **kw):
        self._oc_cfg = _parse_oc_config(cfg.get("lane_oc"), cfg)
        self._oc_mints: dict[str, dict[str, Any]] = {}  # mint -> oc holder of its latest OC trigger (tags every executor row of the mint)
        self.oc_refusals: dict[str, int] = {}
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

    @property
    def h5(self) -> h.H5Limits:
        lim = super().h5
        return replace(lim, max_open=min(lim.max_open, OC_MAX_OPEN))

    def _kill_reason(self) -> str | None:
        why = super()._kill_reason()
        if why or self.lane is None:
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
            return replace(oc, base_breaks_unresolved_settled=s), None

        h.parse_shadow_trigger = parse
        try:
            return super().intent_tick()
        finally:
            h.parse_shadow_trigger = orig

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
        try:
            snap, pool, err = self._snapshot(trg.mint)
            if snap is not None and snap.quote_priced is not None and pool == trg.pool and snap.quote_priced <= h.RULE_Q_MAX_LAMPORTS:
                try:
                    vault, _auth = boost_vault_address(trg.pool)
                    res = guard("getAccountInfo", [vault, {"encoding": "base64", "commitment": self.commitment}])
                    boost, why_b = parse_boost_vault(res.get("value") if isinstance(res, dict) else None, trg.pool)
                except (Exception, SystemExit):
                    boost, why_b = None, "boost_vault_unreadable"
        finally:
            self.rpc = real
        ms = self.now_ms() - t0
        meta: dict[str, Any] = dict(oc_rpc_ms=ms, oc_rpc_calls=guard.calls, oc_q_shadow_lamports=trg.q_lamports)
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
        super()._log(kind, mint, **kw)


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
