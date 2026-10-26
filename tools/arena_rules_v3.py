#!/usr/bin/env python3
"""arena_rules_v3: the rule-bot host with a STATE-CARRYING segment hand-off (P35 item (b); docs/research/p35-longbook-handoff/DESIGN.md section 3,
with QP-RULING-1026 Q2/Q3). PAPER only. No key, no network.

tools/arena_rules.py and tools/arena_rules_v2.py are NOT edited. `HostV3` subclasses `HostV2` (pool table) and adds:

BOOK LOCK. flock on <root>/decisions/<bot_id>/.book.lock for the process life, so two segments never append to one chain.

SEGMENT END (`end_segment(ho_ms)`, or `run(..., segment_end_ms=HO)`). After the last tick before HO, every row with t_recv_ms < HO is dispatched (as the
continuous host's step(HO) would before its tick). Then, at one tape cursor, it checkpoints:
  * the engine (tools/arena_paper_ckpt.EngineCkpt);
  * ArenaState (tools/arena_state_ckpt.StateCkpt);
  * the pool table (HostV2.save_table).
It appends one `segment_end` row per bot. The row carries:
  * HO;
  * each file's sha256 and state digest;
  * the log head before the row;
  * the host's own carried fields (highest slot, sim clock, the trim / sweep / table timers, the retry clocks of open positions).
Then it releases the locks. The process should exit.

SEGMENT RESUME (`resume()`). If every hosted bot's log ends in a `segment_end` with the same HO and checkpoint shas, the host:
  * verifies every file sha256 and the table file's sha256;
  * restores the engine and ArenaState (each recomputes its digest; any mismatch -> HostError, loud; `allow_fallback=True` instead runs the plain
    restart path, every open position booked engine_restart: fail closed);
  * restores the carried host fields;
  * runs HostV2.resume() (chain verify, books from the log, recover() is a no-op for checkpointed positions, start records).
A log that does not end in `segment_end` (a crash) takes the plain restart path, as today.

DOWN INTERVAL D = [HO, resume_ms) (QP-RULING-1026 Q2):
  (a) D = resume_ms - HO from the host's own instants: live = the wall clock when resume() ends; replay = the `resume_ms` given (a simulation).
  (c) d_max_ms = min(5000, the measured dispatch-lag p99, B-P3), given by the caller and logged.
  path `live_lag` if D <= d_max_ms: rows and ticks of D reach the rules as a live lag. But (b): a row of D whose age at dispatch would exceed
      stale_row_ms (10 s) switches the rest of D to the probe at that row (logged `handoff_stale_switch`). It is never dropped silently and never
      decided stale.
  path `probe` otherwise: from HO, rows and ticks of D go to the engine and the table (dispatch off) and to the rule in a PROBE (orders captured,
      never submitted). A captured Sell of an open position -> a `handoff_down_exit` row; at the first tick after D the position is sold and the
      engine books it EXCLUDED, reason handoff_down. A captured Buy -> counted (`handoff_down_missed`, attempt-key hashes only); no position.
  (e) handoff_down exclusions are ordinary `excluded` ledger rows: the scorer counts them in the 5% cap.
`segment_resume` (one per bot) records HO, resume_ms, D, d_max_ms, the path and the restored digests.

Replay dispatch clock for D (tests, B-P1): a row of D is "dispatched" at resume_ms + dispatch_delay_ms (dispatch_delay_ms simulates a slow catch-up).
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import fcntl
import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_rules as R
from tools import arena_rules_v2 as V2
from tools.arena_paper_ckpt import CheckpointInvalid

D_MAX_MS_CAP = 5_000
SEGMENT_END = "segment_end"
SEGMENT_RESUME = "segment_resume"


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class HostV3(V2.HostV2):
    def __init__(self, cfg: R.HostConfig, rules: Sequence[R.Rule], state: Any, engine: Any, now_fn: Callable[[], int] | None = None,
                 binding: Mapping[str, Any] | None = None, table_path: str | os.PathLike | None = None, table: Any = None, *,
                 ckpt_dir: str | os.PathLike | None = None, d_max_ms: int = D_MAX_MS_CAP, resume_ms: int | None = None, dispatch_delay_ms: int = 0,
                 allow_fallback: bool = False) -> None:
        if not 0 <= int(d_max_ms) <= D_MAX_MS_CAP:
            raise R.HostError(f"d_max_ms must be in [0, {D_MAX_MS_CAP}] (QP-RULING-1026 Q2 (c))")
        super().__init__(cfg, rules, state, engine, now_fn, binding, table_path, table)
        self.ckpt_dir = Path(ckpt_dir) if ckpt_dir else Path(cfg.root) / "handoff"
        self.d_max_ms, self.sim_resume_ms, self.dispatch_delay_ms, self.allow_fallback = int(d_max_ms), resume_ms, int(dispatch_delay_ms), allow_fallback
        self.handoff: dict[str, Any] | None = None
        self._probe_from: int | None = None
        self._down_exits: list[str] = []
        self._down_missed: set[str] = set()
        self._down_done = False
        self._ended = False
        self._lock_fds: list[int] = []
        self._acquire_locks()

    # -- book lock
    def _acquire_locks(self) -> None:
        for r in self.rules:
            d = Path(self.cfg.root) / "decisions" / r.bot_id
            d.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(d / ".book.lock"), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                self.close()
                raise R.HostError(f"another process holds the book lock of {r.bot_id}: two segments never write one chain") from exc
            self._lock_fds.append(fd)

    def close(self) -> None:
        for fd in self._lock_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._lock_fds = []

    # -- segment end
    def _carry(self) -> dict[str, Any]:
        return {"slot": self.slot, "sim_now": self.sim_now, "last_ckpt": self._last_ckpt, "last_sweep": self._last_sweep, "last_trim": self._last_trim,
                "retry": {b: {pid: [p.next_try_ms, p.first_try_ms] for pid, p in sorted(bk.open.items())} for b, bk in self.books.items()}}

    def end_segment(self, ho_ms: int) -> dict[str, Any]:
        """Dispatch every row before HO, checkpoint engine / state / table at that cursor, append segment_end per bot, release the locks."""
        self._raise_fatal()
        try:
            self.state.poll(until_ms=ho_ms - 1)
        except R._Halt:
            self._raise_fatal()
            raise
        self._raise_fatal()
        heads = {r.bot_id: list(self.log.head(r.bot_id)) for r in self.rules}
        tag = str(int(ho_ms))
        self.save_table()
        eng = self.engine.checkpoint(self.ckpt_dir / f"engine-{tag}.ckpt", ho_ms=ho_ms, log_heads=heads)
        st = self.state.checkpoint(self.ckpt_dir / f"state-{tag}.ckpt", ho_ms=ho_ms, log_heads=heads)
        table = {"path": str(self.table_path), "sha256": _sha_file(self.table_path), "hwm_ms": getattr(self.table, "hwm_ms", None)}
        body = {"kind": SEGMENT_END, "ho_ms": int(ho_ms), "engine": {"path": str(self.ckpt_dir / f"engine-{tag}.ckpt"), "sha256": eng["sha256"],
                                                                      "state_digest": eng["state_digest"], "positions": eng["positions"]},
                "state": {"path": str(self.ckpt_dir / f"state-{tag}.ckpt"), "sha256": st["sha256"], "state_digest": st["state_digest"]},
                "table": table, "host": self._carry()}
        dec = max(self._now(ho_ms), ho_ms)
        for r in self.rules:
            self.log.append(r.bot_id, dec, {**body, "log_head_before": heads[r.bot_id]})
        self._ended = True
        self.close()
        return body

    def run(self, t_from: int, t_to: int, sleep: Callable[[float], None] = R.time.sleep, *, segment_end_ms: int | None = None) -> None:  # type: ignore[override]
        if self._ended:
            raise R.HostError("this segment has ended")
        if segment_end_ms is not None and (segment_end_ms - t_from) % R.TICK_MS:
            raise R.HostError("segment_end_ms must lie on the tick grid of t_from (the continuous host ticks there)")
        end = t_to if segment_end_ms is None else min(t_to, segment_end_ms)
        super().run(t_from, end, sleep)
        if segment_end_ms is not None and segment_end_ms <= t_to:
            self.end_segment(segment_end_ms)

    # -- resume
    def _segment_end_rows(self) -> dict[str, dict] | None:
        last: dict[str, dict] = {}
        for r in self.rules:
            self.log.verify(r.bot_id)
            rows = self.log.rows(r.bot_id)
            if not rows or rows[-1].get("kind") != SEGMENT_END:
                return None
            last[r.bot_id] = rows[-1]
            if len(rows) >= 2:
                prev = rows[-2]
                if [prev["sha"], prev["seq"]] != rows[-1].get("log_head_before"):
                    raise R.HostError(f"{r.bot_id}: segment_end does not follow the log head it recorded")
        keys = {(x["ho_ms"], x["engine"]["sha256"], x["state"]["sha256"], x["table"]["sha256"]) for x in last.values()}
        if len(keys) != 1:
            raise R.HostError("the hosted books' segment_end rows disagree")
        return last

    def resume(self) -> dict[str, int]:
        seg = self._segment_end_rows()
        if seg is None:
            return super().resume()  # no planned hand-off: today's restart path (a crash: engine_restart exclusions)
        s = next(iter(seg.values()))
        try:
            if _sha_file(Path(s["table"]["path"])) != s["table"]["sha256"] or Path(s["table"]["path"]) != Path(self.table_path):
                raise CheckpointInvalid("the pool table file is not the one segment_end recorded")
            ed = self.engine.restore(s["engine"]["path"], expect_sha256=s["engine"]["sha256"])
            sd = self.state.restore(s["state"]["path"], expect_sha256=s["state"]["sha256"])
        except (CheckpointInvalid, OSError) as exc:
            if not self.allow_fallback:
                raise R.HostError(f"segment hand-off refused: {exc}") from exc
            n = super().resume()
            self._log_resume(s, None, None, "fallback_restart", reason=str(exc)[:160])
            return n
        h = s["host"]
        self.slot, self.sim_now = int(h["slot"]), int(h["sim_now"])
        self._last_ckpt, self._last_sweep, self._last_trim = int(h["last_ckpt"]), int(h["last_sweep"]), int(h["last_trim"])
        n = super().resume()
        for b, retry in h["retry"].items():
            for pid, (nxt, first) in retry.items():
                p = self.books[b].open.get(pid)
                if p is not None:
                    p.next_try_ms, p.first_try_ms = int(nxt), first
        ho = int(s["ho_ms"])
        resume_ms = int(self.now_fn()) if self.cfg.live else int(self.sim_resume_ms if self.sim_resume_ms is not None else ho)
        d = resume_ms - ho
        path = "live_lag" if d <= self.d_max_ms else "probe"
        self.handoff = {"ho_ms": ho, "resume_ms": resume_ms, "d_ms": d, "d_max_ms": self.d_max_ms, "path": path}
        self._probe_from = ho if path == "probe" else None
        self._log_resume(s, ed, sd, path)
        return n

    def _log_resume(self, s: Mapping[str, Any], ed: str | None, sd: str | None, path: str, **extra: Any) -> None:
        h = self.handoff or {}
        dec = max(self._now(int(s["ho_ms"])), int(s["ho_ms"]))
        for r in self.rules:
            self.log.append(r.bot_id, dec, {"kind": SEGMENT_RESUME, "ho_ms": int(s["ho_ms"]), "resume_ms": h.get("resume_ms"), "d_ms": h.get("d_ms"),
                                            "d_max_ms": self.d_max_ms, "path": path, "engine_digest": ed, "state_digest": sd, **extra})

    # -- the down interval
    def _in_d(self, t: int) -> bool:
        h = self.handoff
        return h is not None and h["ho_ms"] <= t < h["resume_ms"]

    def _dispatch_now(self, t: int) -> int:
        return int(self.now_fn()) if self.cfg.live else int(self.handoff["resume_ms"]) + self.dispatch_delay_ms

    def _on_row(self, row: dict) -> None:
        t = row.get("t_recv_ms")
        if self._dispatch and isinstance(t, int) and self._in_d(t) and self.fatal is None:
            if self._probe_from is None and self._dispatch_now(t) - t > self.cfg.stale_row_ms:
                self._probe_from = t  # QP-RULING-1026 Q2 (b): this row would be stale at dispatch; the rest of D goes to the probe
                self._count("handoff_stale_switch")
                for r in self.rules:
                    self.log.append(r.bot_id, self._now(t), {"kind": "handoff_stale_switch", "at_row_ms": t, "ho_ms": self.handoff["ho_ms"],
                                                             "age_ms": self._dispatch_now(t) - t, "stale_row_ms": self.cfg.stale_row_ms})
            if self._probe_from is not None and t >= self._probe_from:
                self._dispatch = False
                try:
                    super()._on_row(row)  # the table and the engine take the row; no rule sees it live
                finally:
                    self._dispatch = True
                self._probe(lambda rule, ctx: rule.on_row(ctx, row), t)
                return
        super()._on_row(row)

    def _probe(self, call: Callable[[Any, Any], list], at_ms: int) -> None:
        for rule in self.rules:
            ctx = self._ctx[rule.bot_id]
            ctx.now_ms = self._now(at_ms)
            try:
                orders = call(rule, ctx) or []
            except Exception as e:  # noqa: BLE001
                self._hook_error(f"{rule.bot_id}.probe", e)
                continue
            book = self.books[rule.bot_id]
            for o in orders:
                if isinstance(o, R.Sell):
                    p = book.open.get(o.position_id)
                    if p is not None and p.exit_reason is None and o.position_id not in self._down_exits:
                        self._down_exits.append(o.position_id)
                        self.log.append(rule.bot_id, self._now(at_ms), {"kind": "handoff_down_exit", "order_id": o.position_id, "probe_reason": str(o.reason),
                                                                       "at_ms": at_ms})
                elif isinstance(o, R.Buy):
                    key = o.attempt_key if o.attempt_key is not None else o.pool
                    self._down_missed.add(f"{rule.bot_id}|{key}")

    def _finish_down(self, dec: int) -> None:
        """At the first tick after D: every probe exit is marked handoff_down in the engine and sold now (excluded); missed entries are logged as a count."""
        self._down_done = True
        if self.handoff is None or self._probe_from is None:
            return
        for rule in self.rules:
            book = self.books[rule.bot_id]
            pids = [p for p in self._down_exits if p in book.open]
            for pid in pids:
                self.engine.mark_handoff_down(pid)
                book.open[pid].exit_reason = "handoff_down"
                book.open[pid].next_try_ms = 0
            book.touch()
            missed = sorted(k for k in self._down_missed if k.startswith(rule.bot_id + "|"))
            self.log.append(rule.bot_id, dec, {"kind": "handoff_down_missed", "n_exits": len(pids), "n_missed_entries": len(missed),
                                               "missed_key_sha256": [hashlib.sha256(k.encode()).hexdigest()[:16] for k in missed],
                                               "probe_from_ms": self._probe_from, "resume_ms": self.handoff["resume_ms"]})

    def _step(self, T: int) -> None:
        h = self.handoff
        if h is not None and not self._down_done and self._probe_from is not None and h["ho_ms"] <= T < h["resume_ms"] and T >= self._probe_from:
            self.state.poll(until_ms=T - 1)  # rows of D: _on_row routes them to the probe
            if not self.cfg.live:
                self.sim_now = max(self.sim_now, T)
            self._raise_fatal()
            for rule in self.rules:
                self._retry_sells(rule, self.books[rule.bot_id], self._now(T))  # sells decided BEFORE HO keep retrying (not a new decision)
                if rule.uses_ticks and T >= self.cfg.clock_start_ms:
                    self._probe(lambda r_, ctx, T=T: r_.on_tick(ctx, T), T)
            self._raise_fatal()
            return
        if h is not None and not self._down_done and T >= h["resume_ms"]:
            self.state.poll(until_ms=min(T, h["resume_ms"]) - 1)  # the rest of D's rows (probe or live), and ONLY D's, before the probe exits are marked
            self._raise_fatal()
            self._finish_down(self._now(T))
        super()._step(T)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the production entry point is PR 7's (rule wiring + registration)
    print("arena_rules_v3: no production entry point yet (P35 PR 7 wires the FLOOR-SLOW rule and its registration)", file=R.sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
