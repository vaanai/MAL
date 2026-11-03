"""H5 executor for the DEC-035 lane book `h5-lane-syn` (H5-BOOSTFLOOR v1 on synthetic-migration pools only). A SUBCLASS of
tools.h5_lane_executor.H5LaneExecutor in its own module; tools/h5_executor.py, tools/h5_lane_executor.py and tools/h5_shadow.py are NOT edited.

Ruling: /data/mal/hunt-1008/ops/manager19/QP-RULING-1103-synthetic-lane.md (C1, C4, C5, C6). Added to the lane (all tighter than DEC-035 s2; the shared
lane limits still bind across bots and run unchanged):

  - Population gate (C4, fail closed). The pinned parser refuses every row whose `synthetic` is not the literal False. This module wraps the parser for the
    duration of `intent_tick` (the pattern H5LaneExecutor uses for gaps): a shadow trigger row is accepted only if `synthetic is True`, `synthetic_src` is "ws" or
    "rpc", `population == "synthetic"` and the row's `s0_block_time` is >= SYN_CUT_MS (2026-11-06T00:00:00Z, re-checked here). It is handed to the pinned parser
    with the class alias (synthetic False, src "rpc") and the TRUE class is kept in a side table keyed by the returned trigger object. A trigger with no side
    entry (a plain pool, a trigger from any other path) is refused `syn_not_synthetic`. Executor and ledger rows carry the true class, not the alias.
  - Per-bot sub-budget 60_000_000 lamports net loss (config can only lower it) with its OWN STOP file (`lane_syn.stop_path`). It is written when bot P&L <=
    -budget, is never removed by this code, survives restarts, and is re-derived from the ledger on the next check if someone deletes it; raising it is a new
    bot_id. A send also needs bot P&L - (bot open stakes + new stake + fee reserve x (bot open + 1)) > -budget.
  - Per-bot max open 1 (the lane's own max_open stays lane-wide, 3 at most). Stake fixed 20_000_000.
  - Mints any other bot_id has a lane-ledger row for are refused (checked in the early refusal and again under the ledger lock at the reserve).
  - Every lane-ledger row carries bot_id, population "synthetic_only", and (per mint) synthetic_src and s0_block_time_ms.
  - Pause (C6): a manual pause file `lane_syn.pause_path` (manager creates it on an A3 `pins_changed`, or until a synthetic-share flag is cleared by the decode
    check). The executor does NOT read the A3 flag itself (tools/h5_executor.py has no A3 reader), so this is the manual path. An unreadable pause file pauses.
    Entries only: exits and the sell path are unaffected.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from tools import h5_executor as h
from tools import h5_lane_executor as L
from tools.h5_lane_syn_shadow import POPULATION, POPULATION_SEAL, SYN_CUT_MS, SYN_SRC_OK, after_cut, s0_ms_of

BOT_ID = "h5-lane-syn"
SYN_STAKE_LAMPORTS = 20_000_000
SYN_SUB_BUDGET_LAMPORTS = 60_000_000
SYN_MAX_OPEN = 1
SYN_KEYS = {"stop_path", "pause_path", "sub_budget_lamports"}


def parse_syn_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SystemExit("lane_syn refused: the config needs a `lane_syn` object")
    extra = set(raw) - SYN_KEYS
    if extra:
        raise SystemExit(f"lane_syn refused: unknown keys {sorted(extra)}")
    for k in ("stop_path", "pause_path"):
        if not isinstance(raw.get(k), str) or not raw[k]:
            raise SystemExit(f"lane_syn refused: {k} is required")
    sub = raw.get("sub_budget_lamports", SYN_SUB_BUDGET_LAMPORTS)
    if isinstance(sub, bool) or not isinstance(sub, int) or not 1 <= sub <= SYN_SUB_BUDGET_LAMPORTS:
        raise SystemExit(f"lane_syn refused: sub_budget_lamports must be 1..{SYN_SUB_BUDGET_LAMPORTS}")
    return {"stop_path": Path(raw["stop_path"]), "pause_path": Path(raw["pause_path"]), "sub_budget_lamports": sub}


def _present(path: Path) -> bool:
    try:
        os.stat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True  # unreadable: blocked


class SynLedger(L.LaneLedger):
    """LaneLedger plus the per-bot checks, evaluated inside the same critical section as the parent's (via `read` and `evaluate`)."""

    def __init__(self, cfg: L.LaneConfig, syn: dict[str, Any]):
        super().__init__(cfg)
        self.syn = syn
        self.tags: dict[str, dict[str, Any]] = {}  # mint -> extra fields for every row of that mint
        self.cur_mint: str | None = None
        self.cur_marks: dict[str, tuple[int, int]] = {}
        self._rows: list[Any] = []

    # -- tags on every row ----------------------------------------------------------------------------------------------------
    def _write_line(self, path: Path, row: dict[str, Any]) -> None:
        extra = dict(self.tags.get(str(row.get("mint")), {}))
        super()._write_line(path, {**row, "population": POPULATION_SEAL, **extra})

    def read(self) -> list[Any]:
        self._rows = super().read()
        return self._rows

    # -- the files ------------------------------------------------------------------------------------------------------------
    def syn_stop_present(self) -> bool:
        return _present(self.syn["stop_path"])

    def syn_paused(self) -> bool:
        return _present(self.syn["pause_path"])

    def write_syn_stop(self, reason: str, now_ms: int) -> None:
        path: Path = self.syn["stop_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            return  # never rewritten, never removed here
        with os.fdopen(fd, "w") as f:
            f.write(f"{reason} ts_ms={now_ms} bot_id={self.cfg.bot_id}\n")

    def _blocked(self) -> str | None:
        if self.syn_stop_present():
            return "syn_stop_file"
        if self.syn_paused():
            return "syn_paused"
        return None

    # -- checks ---------------------------------------------------------------------------------------------------------------
    def bot_view(self, rows: list[Any], marks: dict[str, tuple[int, int]], now_ms: int) -> L.LaneView:
        mine = [r for r in rows if not (isinstance(r, dict) and r.get("ev") not in ("start", "snap") and r.get("bot_id") != self.cfg.bot_id)]
        return L.lane_fold(mine, marks, now_ms, self.cfg.mark_stale_s, self.cfg.bot_id)

    def syn_evaluate(self, rows: list[Any], mint: str | None, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        if stake != SYN_STAKE_LAMPORTS:
            return "syn_stake_not_fixed"
        sub = self.syn["sub_budget_lamports"]
        v = self.bot_view(rows, marks, now_ms)
        if v.pnl <= -sub:
            self.write_syn_stop(f"syn_budget_stop pnl_lamports={v.pnl} sub_budget_lamports={sub}", now_ms)
            return "syn_budget_stop"
        if len(v.open) >= SYN_MAX_OPEN:
            return "syn_max_open"
        reserve = self.cfg.fee_reserve_lamports * (len(v.open) + 1)
        if v.pnl - (v.open_stakes + stake + reserve) <= -sub:
            return "syn_budget_presend"
        if mint is not None:
            for r in rows:
                if isinstance(r, dict) and r.get("mint") == mint and r.get("bot_id") not in (None, self.cfg.bot_id):
                    return "syn_mint_traded_by_other_bot"
        return None

    def evaluate(self, view: L.LaneView, now_ms: int, stake: int, n_pending_extra: int = 0) -> str | None:
        why = super().evaluate(view, now_ms, stake, n_pending_extra)
        if why:
            return why
        return self.syn_evaluate(self._rows, self.cur_mint, self.cur_marks, now_ms, stake)

    def check(self, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        why = self._blocked()
        if why:
            return why
        self.cur_marks = marks
        return super().check(marks, now_ms, stake)

    def reserve(self, mint: str, signature: str, marks: dict[str, tuple[int, int]], now_ms: int, stake: int, pool_gap_waived: bool = False) -> str | None:
        why = self._blocked()
        if why:
            return why
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
            if v.pnl <= -self.syn["sub_budget_lamports"]:
                self.write_syn_stop(f"syn_budget_stop pnl_lamports={v.pnl} sub_budget_lamports={self.syn['sub_budget_lamports']}", now_ms)
        return view


_Base = L.H5LaneExecutor


class H5LaneSynExecutor(_Base):
    def __init__(self, rpc, cfg: dict[str, Any], keypair, **kw):
        self._syn_cfg = parse_syn_config(cfg.get("lane_syn"))
        self._syn_side: dict[int, tuple[Any, str, int]] = {}  # id(trigger) -> (trigger, true src, s0 block time ms)
        self.syn_refusals: dict[str, int] = {}
        self._syn_logged: set[tuple[str, str]] = set()
        lane = cfg.get("lane")
        if not isinstance(lane, dict) or lane.get("bot_id") != BOT_ID:
            raise SystemExit(f"lane_syn refused: lane.bot_id must be {BOT_ID!r}")
        if lane.get("stake_lamports") != SYN_STAKE_LAMPORTS:
            raise SystemExit(f"lane_syn refused: lane.stake_lamports must be {SYN_STAKE_LAMPORTS}")
        if cfg.get("tipauth_gate") not in (None, ""):
            raise SystemExit("lane_syn refused: the tipauth path is off for this book")
        orig = L.LaneLedger
        syn = self._syn_cfg
        L.LaneLedger = lambda c: SynLedger(c, syn)  # type: ignore[assignment,misc]  # the parent builds its ledger by this global name
        try:
            super().__init__(rpc, cfg, keypair, **kw)
        finally:
            L.LaneLedger = orig  # type: ignore[misc]

    # -- limits ---------------------------------------------------------------------------------------------------------------
    @property
    def h5(self) -> h.H5Limits:
        lim = super().h5
        return replace(lim, max_open=min(lim.max_open, SYN_MAX_OPEN))

    def _kill_reason(self) -> str | None:
        why = super()._kill_reason()
        if why or self.lane is None:
            return why
        return self.lane._blocked()  # type: ignore[union-attr]  # syn STOP / manual pause: new entries only, no sell path reads _kill_reason

    # -- population gate ------------------------------------------------------------------------------------------------------
    def _syn_refuse(self, row: dict[str, Any], reason: str) -> None:
        self.syn_refusals[reason] = self.syn_refusals.get(reason, 0) + 1
        key = (str(row.get("pool") or row.get("mint") or ""), reason)
        if key not in self._syn_logged and len(self._syn_logged) < 4096:
            self._syn_logged.add(key)
            self._log("skip", str(row.get("mint") or ""), reason=reason, pool=str(row.get("pool") or ""))

    def syn_row_reason(self, row: dict[str, Any]) -> str | None:
        """None when a shadow trigger row may be traded by this book, else the refusal reason."""
        if row.get("population") != POPULATION:
            return "syn_population"
        if row.get("synthetic") is not True:
            return "syn_not_synthetic"
        if row.get("synthetic_src") not in SYN_SRC_OK:
            return "syn_src"
        if not after_cut(row.get("s0_block_time")):
            return "syn_pre_window"
        return None

    def intent_tick(self) -> int:
        orig = h.parse_shadow_trigger
        side = self._syn_side
        side.clear()

        def parse(row, *a, **kw):
            if not isinstance(row, dict) or row.get("type") != "trigger":
                return orig(row, *a, **kw)
            if row.get("variant") != self.trigger_variant:
                return orig(row, *a, **kw)
            why = self.syn_row_reason(row)
            if why:
                self._syn_refuse(row, why)
                return None, None
            alias = {**row, "synthetic": False, "synthetic_src": "rpc"}  # for the pinned parser only; the true class goes to the side table
            trg, bad = orig(alias, *a, **kw)
            if trg is not None:
                side[id(trg)] = (trg, str(row["synthetic_src"]), s0_ms_of(row["s0_block_time"]) or 0)
            return trg, bad

        h.parse_shadow_trigger = parse
        try:
            return super().intent_tick()
        finally:
            h.parse_shadow_trigger = orig
            side.clear()

    def _hard_refusal(self, trg: h.H5Trigger, now: int) -> str | None:
        got = self._syn_side.get(id(trg))
        if got is None or got[0] is not trg:
            return "syn_not_synthetic"  # plain pool, or a trigger that did not come through the gate above
        if not after_cut(got[2] / 1000):
            return "syn_pre_window"
        if self.lane is not None:
            self.lane.tags[trg.mint] = {"synthetic_src": got[1], "s0_block_time_ms": got[2]}  # type: ignore[attr-defined]
            self.lane.cur_mint = trg.mint  # type: ignore[attr-defined]
        try:
            return super()._hard_refusal(trg, now)
        finally:
            if self.lane is not None:
                self.lane.cur_mint = None  # type: ignore[attr-defined]

    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        if kind == "decision" and self.lane is not None and mint in self.lane.tags:  # type: ignore[attr-defined]
            t = self.lane.tags[mint]  # type: ignore[attr-defined]
            kw["synthetic"], kw["synthetic_src"], kw["population"] = True, t["synthetic_src"], POPULATION_SEAL  # the alias is never ledgered as the class
        super()._log(kind, mint, **kw)


def main(argv: list[str] | None = None) -> int:
    """tools.h5_lane_executor.main with H5LaneSynExecutor in place of H5LaneExecutor."""
    orig = L.H5LaneExecutor
    L.H5LaneExecutor = H5LaneSynExecutor  # type: ignore[misc]  # L.main rebinds h.H5Executor to this global
    try:
        return L.main(argv)
    finally:
        L.H5LaneExecutor = orig  # type: ignore[misc]


if __name__ == "__main__":
    sys.exit(main())
