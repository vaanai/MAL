"""H5 executor for the DEC-035 lane book `h5-lane-h` ("A-R tip-replay heal"). A SUBCLASS of tools.h5_lane_executor.H5LaneExecutor in its own module;
tools/h5_executor.py, tools/h5_lane_executor.py, tools/h5_shadow.py and tools/h5_lane_oc_executor.py are NOT edited.

Ruling: /data/mal/hunt-1008/ops/manager20/QP-RULING-1104-oc-feedfix.md, section 3.2. THIS IS A NEW BOOK (new bot_id), NOT H5 v1: its decision inputs are the
websocket prints merged with the getBlock tip's rows. Its rows are never pooled with, or cited as, H5 v1, h5-lane-2, -3, -oc or -syn evidence. Nothing here
says that any lane book makes money.

The shadow (tools/h5_lane_shadow.py --lane-heal, a separate PR) writes `lane_heal` on every trigger record and an int `lane_gap_seq` on every flagged gap
row. This module only READS them. It re-implements nothing of the rule (Q, V, BOOST, spent, window): the pinned shadow Engine decided; this executor decides
only whether the pinned refusals that the heal speaks to are lifted.

Waiver (all of): `lane_heal` is a dict, v == 1, status == "healed", healed_through_seq an int >= 0; the trigger was read from a shadow row this tick; the
CURRENT feed gap (`self.feed_gap`, G-cur) is false. Then
  - the pool gap flag (trg.gap) is cleared;
  - the 20 s gap hold is ignored iff EVERY flagged gap row tailed in the last hold span has an int lane_gap_seq <= healed_through_seq; otherwise nothing is
    waived and the hold refuses as feed_gap exactly as before;
  - a base_breaks_unresolved_settled >= 1 is treated as 0 for the pinned bad_intent check ONLY (the row's real value stays on the trigger and the ledger),
    and only when the hold is not blocking, so a trigger that stays refused is refused with the token h5-lane-2 gives it.
Everything else runs in the pinned order and is never waived: feed_stale, sell_stuck, stale_trigger, stale_trigger_chain, outside_rule_window, sps, the
s0_minus_announced check, seals, the on-chain entry price check and every lane limit. Missing or malformed `lane_heal`, a status other than "healed", or a
trigger not read from a shadow row: exactly h5-lane-2 behaviour with waive_pool_gap false (md5 equivalence is a test).

Decision tokens are unchanged. New FIELDS on decision, skip, send_intent and the other lane rows of the mint: heal_applied, heal_kind (gap | break |
gap+break | null), heal_reason, heal_n_gaps, heal_tip_only_in_gaps_n, heal_boost_buys_tip_only_n, heal_through_seq, feed_gap_components {current, pool, hold}.
Every executor-ledger row of this bot carries bot_id "h5-lane-h".

Lane limits (DEC-035 s2, shared lane ledger): stake fixed 20_000_000, per-bot max open 1, mints any other bot_id has a lane-ledger row for are refused, and a
sub-budget of 50_000_000 lamports net loss (config can only lower it) with its OWN STOP file (`lane_heal.stop_path`), never removed by this code and
re-derived from the ledger if deleted (pattern of tools/h5_lane_oc_executor.py). `lane.waive_pool_gap` must be false: the executor refuses to start if it is true.

MUTUAL EXCLUSION. At startup it takes, and holds for its life, the bot lock files of h5-lane-2, h5-lane-3 and h5-lane-oc (`<ledger>.bot.<bot_id>.lock`, the files
the pinned `_take_bot_lock` uses) next to its own, and exits 2 (no restart loop) if any is held.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from tools import h5_executor as h
from tools import h5_lane_executor as L
from tools import h5_lane_syn_executor as X  # helpers only (path guards, file presence); that module is not edited

BOT_ID = "h5-lane-h"
HEAL_STAKE_LAMPORTS = 20_000_000
HEAL_SUB_BUDGET_LAMPORTS = 50_000_000
HEAL_MAX_OPEN = 1
EXCLUDED_BOTS = ("h5-lane-2", "h5-lane-3", "h5-lane-oc")
HEAL_KEYS = {"stop_path", "sub_budget_lamports"}
GUARDED_DIRS = ("/var/lib/mal-live/h5-lane-syn", "/var/lib/mal-live/h5-lane-oc")
HOLD_SLACK_MS = 2_000  # the hold span is the executor's hold plus this slack (the pinned hold starts a moment after the tail): conservative
KEEP_MAX = 4096  # flagged gap rows remembered
HEAL_FIELDS = ("heal_applied", "heal_kind", "heal_reason", "heal_n_gaps", "heal_tip_only_in_gaps_n", "heal_boost_buys_tip_only_n", "heal_through_seq",
               "feed_gap_components")
_REASON_RE = re.compile(r"[a-z0-9_:+\-]{1,64}")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_lane_heal(raw: Any) -> dict[str, Any] | None:
    """The raw `lane_heal` of a trigger record -> a sanitized view, or None if it is missing or malformed. `healed` is True only for a well-formed
    v1 record with status "healed" and an int healed_through_seq >= 0; every other value of any field fails closed (never guessed)."""
    if not isinstance(raw, dict):
        return None
    reason = raw.get("reason")
    out: dict[str, Any] = {
        "reason": reason if isinstance(reason, str) and _REASON_RE.fullmatch(reason) else None,
        "n_gaps": raw["n_gaps_full"] if _is_int(raw.get("n_gaps_full")) else None,
        "tip_only_in_gaps_n": raw["tip_only_in_gaps_n"] if _is_int(raw.get("tip_only_in_gaps_n")) else None,
        "boost_buys_tip_only_n": raw["boost_buys_tip_only_n"] if _is_int(raw.get("boost_buys_tip_only_n")) else None,
        "through_seq": raw["healed_through_seq"] if _is_int(raw.get("healed_through_seq")) and raw["healed_through_seq"] >= 0 else None,
    }
    out["healed"] = bool(_is_int(raw.get("v")) and raw.get("v") == 1 and raw.get("status") == "healed" and out["through_seq"] is not None)
    return out


def _parse_heal_config(raw: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SystemExit("lane_heal refused: the config needs a `lane_heal` object")
    extra = set(raw) - HEAL_KEYS
    if extra:
        raise SystemExit(f"lane_heal refused: unknown keys {sorted(extra)}")
    if not isinstance(raw.get("stop_path"), str) or not raw["stop_path"]:
        raise SystemExit("lane_heal refused: stop_path is required")
    sub = raw.get("sub_budget_lamports", HEAL_SUB_BUDGET_LAMPORTS)
    if isinstance(sub, bool) or not isinstance(sub, int) or not 1 <= sub <= HEAL_SUB_BUDGET_LAMPORTS:
        raise SystemExit(f"lane_heal refused: sub_budget_lamports must be 1..{HEAL_SUB_BUDGET_LAMPORTS}")
    stop = X._rp(raw["stop_path"])
    lane = cfg.get("lane") if isinstance(cfg.get("lane"), dict) else {}
    guarded = [str(lane.get("stop_path") or ""), str(lane.get("ledger_path") or ""), str(cfg.get("state_dir") or ""), str(cfg.get("intents_file") or ""),
               *X.H5_LANE2_PATHS, *GUARDED_DIRS]
    for g in guarded:
        if g and X._same_or_inside(stop, X._rp(g)):
            raise SystemExit(f"lane_heal refused: stop_path collides with a lane / other book path ({g})")
    d = Path(raw["stop_path"]).parent
    with contextlib.suppress(OSError):
        d.mkdir(parents=True, exist_ok=True)
    if not d.is_dir() or not os.access(d, os.W_OK | os.X_OK):
        raise SystemExit("lane_heal refused: the directory of stop_path is not writable")
    return {"stop_path": Path(raw["stop_path"]), "sub_budget_lamports": sub}


@dataclass(frozen=True)
class HealTrigger(h.H5Trigger):
    """A trigger read from a shadow row. `heal` is the per-trigger tag, filled by the entry check (mutable holder, not part of equality)."""

    heal: dict = field(default_factory=dict, compare=False, repr=False)
    raw: dict = field(default_factory=dict, compare=False, repr=False)  # parse-time facts: the sanitized lane_heal view and whether settled was zeroed

    def _tag(self, d: dict[str, Any]) -> dict[str, Any]:
        d = dict(d)
        d.pop("heal", None)
        d.pop("raw", None)
        d.update(self.heal)
        return d

    def public(self) -> dict[str, Any]:
        return self._tag(super().public())

    def log_fields(self) -> dict[str, Any]:
        return self._tag(super().log_fields())


class HealLedger(L.LaneLedger):
    """LaneLedger plus the per-bot sub-budget / max open / mint checks, evaluated in the parent's critical section; tags the rows of a mint."""

    def __init__(self, cfg: L.LaneConfig, heal: dict[str, Any]):
        super().__init__(cfg)
        self.heal = heal
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

    def heal_stop_present(self) -> bool:
        return X._present(self.heal["stop_path"])

    def write_heal_stop(self, reason: str, now_ms: int) -> None:
        path: Path = self.heal["stop_path"]
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

    def heal_evaluate(self, rows: list[Any], mint: str | None, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        if stake != HEAL_STAKE_LAMPORTS:
            return "heal_stake_not_fixed"
        sub = self.heal["sub_budget_lamports"]
        v = self.bot_view(rows, marks, now_ms)
        if v.pnl <= -sub:
            self.write_heal_stop(f"heal_budget_stop pnl_lamports={v.pnl} sub_budget_lamports={sub}", now_ms)
            return "heal_budget_stop"
        if len(v.open) >= HEAL_MAX_OPEN:
            return "heal_max_open"
        reserve = self.cfg.fee_reserve_lamports * (len(v.open) + 1)
        if v.pnl - (v.open_stakes + stake + reserve) <= -sub:
            return "heal_budget_presend"
        if mint is not None:
            for r in rows:
                if isinstance(r, dict) and r.get("mint") == mint and r.get("bot_id") not in (None, self.cfg.bot_id):
                    return "heal_mint_traded_by_other_bot"
        return None

    def evaluate(self, view: L.LaneView, now_ms: int, stake: int, n_pending_extra: int = 0) -> str | None:
        why = super().evaluate(view, now_ms, stake, n_pending_extra)
        return why or self.heal_evaluate(self._rows, self.cur_mint, self.cur_marks, now_ms, stake)

    def check(self, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        if self.heal_stop_present():
            return "heal_stop_file"
        self.cur_marks = marks
        return super().check(marks, now_ms, stake)

    def reserve(self, mint: str, signature: str, marks: dict[str, tuple[int, int]], now_ms: int, stake: int, pool_gap_waived: bool = False) -> str | None:
        if self.heal_stop_present():
            return "heal_stop_file"
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
            if v.pnl <= -self.heal["sub_budget_lamports"]:
                self.write_heal_stop(f"heal_budget_stop pnl_lamports={v.pnl} sub_budget_lamports={self.heal['sub_budget_lamports']}", now_ms)
        return view


_Base = L.H5LaneExecutor


class H5LaneHealExecutor(_Base):
    def __init__(self, rpc, cfg: dict[str, Any], keypair, **kw):
        lane = cfg.get("lane")
        if not isinstance(lane, dict) or lane.get("bot_id") != BOT_ID:
            raise SystemExit(f"lane_heal refused: lane.bot_id must be {BOT_ID!r}")
        if lane.get("waive_pool_gap", False) is not False:
            raise SystemExit("lane_heal refused: lane.waive_pool_gap must be false (the heal is the only waiver of this book)")
        if lane.get("stake_lamports") != HEAL_STAKE_LAMPORTS:
            raise SystemExit(f"lane_heal refused: lane.stake_lamports must be {HEAL_STAKE_LAMPORTS}")
        if cfg.get("tipauth_gate") not in (None, ""):
            raise SystemExit("lane_heal refused: the tipauth path is off for this book")
        self._heal_cfg = _parse_heal_config(cfg.get("lane_heal"), cfg)
        self._heal_rows: dict[str, dict[str, Any]] = {}  # mint -> tag of its latest trigger (consumed by the row that ends the trigger)
        self._flag_rows: list[tuple[int, int | None]] = []  # (tail ms, lane_gap_seq or None) of every flagged gap row tailed
        self._tick_flagged = False  # a flagged gap row was tailed in the current tick (the pinned hold is not set yet when triggers are parsed)
        self._heal_excl: list[Any] = []
        orig, hc = L.LaneLedger, self._heal_cfg
        L.LaneLedger = lambda c: HealLedger(c, hc)  # type: ignore[assignment,misc]  # the parent builds its ledger by this global name
        try:
            super().__init__(rpc, cfg, keypair, **kw)
        finally:
            L.LaneLedger = orig  # type: ignore[misc]
        if self.lane is not None:
            self._take_exclusion_locks()

    def _take_exclusion_locks(self) -> None:
        import fcntl
        for bot in EXCLUDED_BOTS:
            f = open(f"{self.lane.path}.bot.{bot}.lock", "a")  # type: ignore[union-attr]
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                self.release_lane_lock()
                print(f"lane_heal refused: {bot} holds its bot lock; h5-lane-h never runs beside it", file=sys.stderr)
                raise SystemExit(2) from None
            self._heal_excl.append(f)

    def release_lane_lock(self) -> None:
        for f in getattr(self, "_heal_excl", []):
            f.close()
        self._heal_excl = []
        super().release_lane_lock()

    @property
    def h5(self) -> h.H5Limits:
        lim = super().h5
        return replace(lim, max_open=min(lim.max_open, HEAL_MAX_OPEN))

    def _kill_reason(self) -> str | None:
        why = super()._kill_reason()
        if why:
            return why
        if self.lane is None:
            return why
        return "heal_stop_file" if self.lane.heal_stop_present() else None  # type: ignore[attr-defined]

    # -- the tail and parser wraps ----------------------------------------------------------------------------------------------
    def intent_tick(self) -> int:
        orig = h.parse_shadow_trigger
        orig_tail = h.tail_lines
        self._tick_flagged = False

        def parse(row, *a, **kw):
            if not (isinstance(row, dict) and row.get("type") == "trigger" and row.get("variant") == self.trigger_variant):
                return orig(row, *a, **kw)
            heal = parse_lane_heal(row.get("lane_heal"))
            s = row.get("base_breaks_unresolved_settled")
            zero = bool(heal and heal["healed"] and _is_int(s) and s >= 1 and not self.feed_gap and not self._hold_blocks(heal, self.now_ms(), parsing=True))
            trg, bad = orig({**row, "base_breaks_unresolved_settled": 0} if zero else row, *a, **kw)  # every other pinned parser check still applies
            if trg is None:
                return trg, bad
            ht = HealTrigger(**{f.name: getattr(trg, f.name) for f in dataclasses.fields(h.H5Trigger)})
            if zero:
                ht = replace(ht, base_breaks_unresolved_settled=s)  # the real value stays on the trigger and the rows
            ht.raw.update(heal=heal, settled_zeroed=zero)
            return ht, bad

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
        """Flagged gap rows with their lane_gap_seq, by tail time. Read-only: it never changes what the pinned reader sees."""
        now = self.now_ms()
        for line in lines:
            if '"gap"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") != "gap" or row.get("flags_pools") is False:  # the pinned reading: only an explicit false is harmless
                continue
            seq = row.get("lane_gap_seq")
            self._flag_rows.append((now, seq if _is_int(seq) else None))
            self._tick_flagged = True
        span = self.h5.gap_hold_ms + HOLD_SLACK_MS
        self._flag_rows = [r for r in self._flag_rows if r[0] > now - span][-KEEP_MAX:]

    def _hold_active(self, now: int, parsing: bool) -> bool:
        return now < self._gap_until_ms or (parsing and self._tick_flagged)

    def _hold_covered(self, through: int, now: int) -> bool:
        """Every flagged gap row tailed in the last hold span has an int lane_gap_seq <= through."""
        span = self.h5.gap_hold_ms + HOLD_SLACK_MS
        rows = [r for r in self._flag_rows if r[0] > now - span]
        return bool(rows) and all(sq is not None and sq <= through for _t, sq in rows)

    def _hold_blocks(self, heal: dict[str, Any], now: int, parsing: bool = False) -> bool:
        """The hold is active and NOT covered by the heal: it refuses as feed_gap exactly as today."""
        return self._hold_active(now, parsing) and not self._hold_covered(heal["through_seq"], now)

    # -- entry ------------------------------------------------------------------------------------------------------------------
    def _hard_refusal(self, trg: h.H5Trigger, now: int) -> str | None:
        comp = {"current": bool(self.feed_gap), "pool": trg.gap is True, "hold": bool(now < self._gap_until_ms)}
        raw = trg.raw if isinstance(trg, HealTrigger) else {}
        heal = raw.get("heal")
        use = None
        if isinstance(trg, HealTrigger) and heal and heal["healed"] and not self.feed_gap and not self._hold_blocks(heal, now):
            use = heal
        zeroed = bool(use and raw.get("settled_zeroed"))
        cl_gap = bool(use and comp["pool"])
        cl_hold = bool(use and comp["hold"])
        applied = cl_gap or cl_hold or zeroed
        kind = None
        if applied:
            gk, bk = cl_gap or cl_hold, zeroed
            kind = "gap+break" if gk and bk else ("gap" if gk else "break")
        tag = {"heal_applied": applied, "heal_kind": kind, "heal_reason": (heal or {}).get("reason"), "heal_n_gaps": (heal or {}).get("n_gaps"),
               "heal_tip_only_in_gaps_n": (heal or {}).get("tip_only_in_gaps_n"), "heal_boost_buys_tip_only_n": (heal or {}).get("boost_buys_tip_only_n"),
               "heal_through_seq": (heal or {}).get("through_seq"), "feed_gap_components": comp}
        if isinstance(trg, HealTrigger):
            trg.heal.clear()
            trg.heal.update(tag)
        self._heal_rows[trg.mint] = tag
        if self.lane is not None and trg.mint not in self.state.open and trg.mint not in self.state.pending:  # a held position keeps its own tag
            self.lane.tags[trg.mint] = dict(tag)  # type: ignore[attr-defined]
        t2, saved = (replace(trg, gap=False) if cl_gap else trg), self._gap_until_ms
        if cl_hold:
            self._gap_until_ms = 0
        if self.lane is not None:
            self.lane.cur_mint = trg.mint  # type: ignore[attr-defined]
        try:
            return super()._hard_refusal(t2, now)
        finally:
            self._gap_until_ms = saved
            if self.lane is not None:
                self.lane.cur_mint = None  # type: ignore[attr-defined]

    # -- tags on every row of a mint ----------------------------------------------------------------------------------------------
    def _heal_tag(self, mint: str) -> dict[str, Any] | None:
        got = self._heal_rows.get(mint)
        if got is not None:
            return got
        for src in (self.state.pending.get(mint), self.state.open.get(mint)):  # after a restart: the durable plan says it
            t = ((src or {}).get("h5") or {}).get("trigger") or {}
            if "heal_applied" in t:
                return {k: t.get(k) for k in HEAL_FIELDS}
        return None

    def _lane_record(self, ev: str, mint: str, **kw: Any) -> None:
        tag = self._heal_tag(mint) if mint else None
        if tag is not None and self.lane is not None:
            self.lane.tags[mint] = dict(tag)  # type: ignore[attr-defined]
        super()._lane_record(ev, mint, **kw)

    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        tag = self._heal_tag(mint) if mint else None
        if tag is not None:
            kw = {**tag, **kw}
        kw.setdefault("bot_id", BOT_ID)  # every row of this book
        super()._log(kind, mint, **kw)
        if kind in ("decision", "skip") and mint:
            self._heal_rows.pop(mint, None)  # consumed by the row that ends the trigger; later rows of a held position re-derive it from the durable plan


def main(argv: list[str] | None = None) -> int:
    """tools.h5_lane_executor.main with H5LaneHealExecutor in place of H5LaneExecutor."""
    orig = L.H5LaneExecutor
    L.H5LaneExecutor = H5LaneHealExecutor  # type: ignore[misc]  # L.main rebinds h.H5Executor to this global
    try:
        return L.main(argv)
    finally:
        L.H5LaneExecutor = orig  # type: ignore[misc]


if __name__ == "__main__":
    sys.exit(main())
