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
  - Start-record provenance (C4). Before any trigger is accepted the executor finds the LAST `start` record of the shadow's output (the file, or the newest
    hourly file that has one in the directory; later files are followed incrementally, so a restart of the shadow or a roll is re-judged) and refuses every
    trigger (`syn_start_missing` / `syn_start_bad:<field>`) unless it carries: seal.population "synthetic_only", seal.lane "DEC-035", seal.oracle
    "lane_dec035_not_a_pick", bot_id "h5-lane-syn", syn_cut_ms = the cut, the H5 Look-2 seal on (h5_look2.observed false, no amendment_ref, stock start/end),
    wrapper_module "tools.h5_lane_syn_shadow" with wrapper_sha256 equal to the sha256 of that module's file in THIS checkout, an out_dir named
    h5-shadow-lane-syn, and no fresh_forward / q_star_sol key.
  - True class in persisted state. The pinned parser is handed a stand-in class; the trigger it returns is re-made as `SynTrigger`, whose `public()` and
    `log_fields()` (the only writers of the counters plan, the pending / open state and the decision row) carry synthetic true, the true synthetic_src and
    population "synthetic_only". The stand-in never reaches a file.
  - Config path guards. lane_syn.stop_path / pause_path are refused at startup if they are equal to each other, equal to or inside any lane path (lane STOP,
    lane ledger and its .lock / .backlog.* / .bot.* siblings, state_dir, the intents path) or any path h5-lane-2 uses (H5_LANE2_PATHS), or if the STOP's
    directory is not writable.
  - `lane.waive_pool_gap` is allowed and the example sets it true (as h5-lane-2): a trigger is not refused for its pool's past FEED-LOSS gap flag alone; the
    current-feed gap hold and every other refusal are unchanged. The HOLDOUT_LEDGER entry for this book must say so; analysis splits on `pool_gap_waived`.
  - Cross-bot overlap (recorded risk). The refusal of a mint another bot has traded is ONE-WAY: the pinned h5-lane-2 does not refuse a mint this book holds, so
    it can buy it on a classifier disagreement, and with one shared wallet a sell would sell the combined balance. This executor logs a `cross_bot_overlap`
    ALERT row if it ever sees another bot's row on a mint it holds.
  - Pause (C6): a manual pause file `lane_syn.pause_path` (manager creates it on an A3 `pins_changed`, or until a synthetic-share flag is cleared by the decode
    check). The executor does NOT read the A3 flag itself (tools/h5_executor.py has no A3 reader), so this is the manual path. An unreadable pause file pauses.
    Entries only: exits and the sell path are unaffected.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tools import h5_executor as h
from tools import h5_lane_executor as L
from tools import h5_shadow as hs
from tools.h5_lane_syn_shadow import (OUT_DIR_NAME, POPULATION, POPULATION_SEAL, SYN_CUT_MS, SYN_SRC_OK, WRAPPER_MODULE, after_cut, s0_ms_of,
                                       wrapper_sha256)

BOT_ID = "h5-lane-syn"
SYN_STAKE_LAMPORTS = 20_000_000
SYN_SUB_BUDGET_LAMPORTS = 60_000_000
SYN_MAX_OPEN = 1
SYN_KEYS = {"stop_path", "pause_path", "sub_budget_lamports"}


# Paths h5-lane-2 runs on (scripts/mal-fast/h5-lane-live.json.example): its lane ledger and STOP are shared by design and are guarded above; its state dir is not ours.
H5_LANE2_PATHS = ("/var/lib/mal-live/h5-lane/state", "/var/lib/mal-live/h5-lane/STOP", "/var/lib/mal-live/h5-lane/lane-ledger.jsonl")


def _rp(path: Any) -> str:
    return os.path.realpath(str(path))


def _same_or_inside(path: str, other: str) -> bool:
    return path == other or path.startswith(other.rstrip("/") + "/") or path.startswith(other + ".")  # `ledger.jsonl.lock`, `.backlog.x`, `.bot.x.lock`


def check_syn_paths(syn: dict[str, Any], cfg: dict[str, Any]) -> None:
    stop, pause = _rp(syn["stop_path"]), _rp(syn["pause_path"])
    if stop == pause:
        raise SystemExit("lane_syn refused: stop_path and pause_path are the same path")
    lane = cfg.get("lane") if isinstance(cfg.get("lane"), dict) else {}
    guarded = [str(lane.get("stop_path") or ""), str(lane.get("ledger_path") or ""), str(cfg.get("state_dir") or ""), str(cfg.get("intents_file") or ""),
               *H5_LANE2_PATHS]
    for name, path in (("stop_path", stop), ("pause_path", pause)):
        for g in guarded:
            if g and _same_or_inside(path, _rp(g)):
                raise SystemExit(f"lane_syn refused: {name} collides with a lane / h5-lane-2 path ({g})")
    d = Path(syn["stop_path"]).parent
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if not d.is_dir() or not os.access(d, os.W_OK | os.X_OK):
        raise SystemExit("lane_syn refused: the directory of stop_path is not writable")


def parse_syn_config(raw: Any, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    out = _parse_syn_config(raw)
    if cfg is not None:
        check_syn_paths(out, cfg)
    return out


def _parse_syn_config(raw: Any) -> dict[str, Any]:
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


@dataclass(frozen=True)
class SynTrigger(h.H5Trigger):
    """The trigger the pinned code works on carries the stand-in class (synthetic False / src "rpc") so its own gates pass; every row it writes through
    `public()` / `log_fields()` (counters plan, pending and open state, decision row) is corrected here to the TRUE class."""

    true_src: str | None = None

    def _true(self, d: dict[str, Any]) -> dict[str, Any]:
        d = dict(d)
        d.pop("true_src", None)
        d["synthetic"], d["synthetic_src"], d["population"] = True, self.true_src, POPULATION
        return d

    def public(self) -> dict[str, Any]:
        return self._true(super().public())

    def log_fields(self) -> dict[str, Any]:
        return self._true(super().log_fields())


def start_record_reason(rec: Any) -> str | None:
    """None when a shadow `start` record carries every C3 tag, else `syn_start_bad:<field>`."""
    if not isinstance(rec, dict) or rec.get("type") != "start":
        return "syn_start_bad:type"
    seal = rec.get("seal") if isinstance(rec.get("seal"), dict) else {}
    l2 = rec.get("h5_look2") if isinstance(rec.get("h5_look2"), dict) else {}
    checks = (("seal.population", seal.get("population") == POPULATION), ("seal.lane", seal.get("lane") == "DEC-035"),
              ("seal.oracle", seal.get("oracle") == "lane_dec035_not_a_pick"), ("bot_id", rec.get("bot_id") == BOT_ID),
              ("syn_cut_ms", rec.get("syn_cut_ms") == SYN_CUT_MS and not isinstance(rec.get("syn_cut_ms"), bool)),
              ("h5_look2.observed", l2.get("observed") is False and l2.get("amendment_ref") is None),
              ("h5_look2.window", l2.get("start_ms") == hs.H5_LOOK2_START_MS and l2.get("end_ms") == hs.H5_LOOK2_END_MS),
              ("wrapper_module", rec.get("wrapper_module") == WRAPPER_MODULE), ("wrapper_sha256", rec.get("wrapper_sha256") == wrapper_sha256()),
              ("out_dir", isinstance(rec.get("out_dir"), str) and Path(rec["out_dir"]).name == OUT_DIR_NAME),
              ("mode", "fresh_forward" not in rec and "q_star_sol" not in rec))
    for name, ok in checks:
        if not ok:
            return f"syn_start_bad:{name}"
    return None


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
        if row.get("ev") in ("start", "snap"):
            return super()._write_line(path, row)  # lane-wide rows are not this book's
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
        self._syn_cfg = parse_syn_config(cfg.get("lane_syn"), cfg)
        self._syn_side: dict[int, tuple[Any, str, int]] = {}  # id(trigger) -> (trigger, true src, s0 block time ms)
        self.syn_refusals: dict[str, int] = {}
        self._syn_logged: set[tuple[str, str]] = set()
        self._syn_start: dict[str, Any] | None = None  # the last shadow `start` record seen
        self._syn_offsets: dict[str, int] = {}
        self._syn_scanned = False
        self._syn_overlap_ms = 0
        self._syn_overlap_seen: set[tuple[str, str]] = set()
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

    # -- start-record provenance ----------------------------------------------------------------------------------------------
    def _syn_files(self) -> list[Path]:
        p = self.intents_path
        try:
            if p.is_dir():
                return sorted(x for x in p.glob("h5-shadow-*.jsonl") if x.is_file())
        except OSError:
            return []
        return [p] if p.is_file() else []

    def _syn_read_new(self, f: Path, from_end_scan: bool = False) -> None:
        try:
            size = f.stat().st_size
            off = self._syn_offsets.get(str(f), 0)
            if size < off:
                off = 0  # truncated or replaced: read it again
            with f.open("rb") as fh:
                fh.seek(off)
                data = fh.read(size - off)
        except OSError:
            return
        cut = data.rfind(b"\n") + 1  # only whole lines; a partial last line waits for the next look
        self._syn_offsets[str(f)] = off + cut
        for line in data[:cut].splitlines():
            if b'"type":"start"' in line or b'"type": "start"' in line:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "start":
                    self._syn_start = rec

    def _syn_start_reason(self) -> str | None:
        files = self._syn_files()
        if not self._syn_scanned:
            self._syn_scanned = True
            for f in reversed(files):  # newest first, stop at the first file that has a start; older files are never read
                self._syn_read_new(f)
                if self._syn_start is not None:
                    break
            for f in files:  # files older than the one with the start: skipped for good
                with contextlib.suppress(OSError):
                    self._syn_offsets.setdefault(str(f), f.stat().st_size)
        for f in files:  # growth of known files, and any file that appeared since (read from its start)
            self._syn_read_new(f)
        if self._syn_start is None:
            return "syn_start_missing"
        return start_record_reason(self._syn_start)

    def intent_tick(self) -> int:
        orig = h.parse_shadow_trigger
        side = self._syn_side
        side.clear()

        def parse(row, *a, **kw):
            if not isinstance(row, dict) or row.get("type") != "trigger":
                return orig(row, *a, **kw)
            if row.get("variant") != self.trigger_variant:
                return orig(row, *a, **kw)
            why = self.syn_row_reason(row) or self._syn_start_reason()
            if why:
                self._syn_refuse(row, why)
                return None, None
            alias = {**row, "synthetic": False, "synthetic_src": "rpc"}  # for the pinned parser only; the true class goes to the side table
            trg, bad = orig(alias, *a, **kw)
            if trg is not None:
                trg = SynTrigger(**{f.name: getattr(trg, f.name) for f in dataclasses.fields(h.H5Trigger)}, true_src=str(row["synthetic_src"]))
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
        if not isinstance(got[0], SynTrigger):
            return "syn_not_synthetic"
        if self._syn_start is None or start_record_reason(self._syn_start) is not None:
            return "syn_start_missing"
        if self.lane is not None:
            self.lane.tags[trg.mint] = {"synthetic_src": got[1], "s0_block_time_ms": got[2]}  # type: ignore[attr-defined]
            self.lane.cur_mint = trg.mint  # type: ignore[attr-defined]
        try:
            return super()._hard_refusal(trg, now)
        finally:
            if self.lane is not None:
                self.lane.cur_mint = None  # type: ignore[attr-defined]

    def tick(self) -> int:
        n = super().tick()
        self.syn_check_overlap()  # type: ignore[attr-defined]
        return n

    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        if kind == "decision" and self.lane is not None and mint in self.lane.tags:  # type: ignore[attr-defined]
            t = self.lane.tags[mint]  # type: ignore[attr-defined]
            kw["synthetic"], kw["synthetic_src"], kw["population"] = True, t["synthetic_src"], POPULATION_SEAL  # the alias is never ledgered as the class
        super()._log(kind, mint, **kw)


def _cross_bot_overlap(self: "H5LaneSynExecutor") -> None:
    """ALERT `cross_bot_overlap` (once per mint and bot) if another bot has a row on a mint this book holds open."""
    if self.lane is None or self.dry_run:
        return
    now = self.now_ms()
    if now - self._syn_overlap_ms < 5_000:
        return
    self._syn_overlap_ms = now
    try:
        with self.lane.locked():
            rows = self.lane.read()
            held = {k.split(":", 1)[1] for k in self.lane.bot_view(rows, {}, now).open}  # type: ignore[attr-defined]
    except Exception:
        return
    if not held:
        return
    for r in rows:
        if isinstance(r, dict) and r.get("mint") in held and r.get("bot_id") not in (None, BOT_ID):
            key = (str(r["mint"]), str(r["bot_id"]))
            if key not in self._syn_overlap_seen:
                self._syn_overlap_seen.add(key)
                self._alert("cross_bot_overlap", str(r["mint"]), other_bot=str(r["bot_id"]), ev=str(r.get("ev")))


H5LaneSynExecutor.syn_check_overlap = _cross_bot_overlap  # type: ignore[attr-defined]


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
