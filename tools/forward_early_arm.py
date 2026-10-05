"""Early arm: the EXP-012 gate result at bonding-curve `complete` time (paper only, no keys).

The `migrate` trigger fires on the first PumpSwap swap. The gate's features use only bonding prints
with chain time before `mig_ms`, `mig_ms` itself and creator history, and the curve's `complete`
event lands about 3 slots before the migrate tx. So the gate can be answered at `complete` time for a
small set of candidate `mig_ms` seconds, and the executor can be told early.

Every evaluated complete is also audited (one row per complete and book, pass or fail, with an outcome)
to a SEPARATE file, arm-audit.jsonl, plus an hourly summary of outcome counts. The audit carries no P&L
or position field.

This module is a read-only observer. It never calls `engine.push_*`, never mutates engine state, never
touches a ledger. It reads `engine.exp012` (via the pure `features_at`), `engine.exp012_gates` and a
non-mutating risk check, and appends one `forward_paper_arm_v1` row per (book, mint) to the intents
log. The pinned executor's `parse_intent` accepts only `forward_paper_intent_v1`, so an arm row is
ignored by it. Any failure here is counted and swallowed: a side file must never stop the runner.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from typing import Any, Deque

SCHEMA_ARM = "forward_paper_arm_v1"
SCHEMA_AUDIT = "forward_paper_arm_audit_v1"
SCHEMA_AUDIT_SUMMARY = "forward_paper_arm_audit_summary_v1"
HOUR_MS = 3_600_000
CANDIDATE_OFFSETS_S = (0, 1, 2, 3, 4)  # mig_ms = complete chain second + k seconds
MAX_PENDING = 4096


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


class EarlyArm:
    def __init__(self, engine: Any, *, stale_ms: int = 30_000) -> None:
        self.engine = engine
        self.stale_ms = stale_ms
        self.pending: Deque[dict[str, Any]] = deque()
        self.seen: set[str] = set()
        self.rows_written = 0
        self.counts: dict[str, int] = {}
        self._hour: int | None = None
        self.errors = 0
        self._err_logged_s = 0

    # --- intake (rows from migrations-<hour>.jsonl; nothing else) -------------
    def note_row(self, row: dict[str, Any], now_ms: int | None = None) -> bool:
        """Queue a `complete` row. Returns True if queued."""
        if row.get("type") != "complete":
            return False
        mint = row.get("mint")
        ts = _int(row.get("event_ts"))
        if ts is None:
            ts = _int(row.get("block_time"))
        t_recv = _int(row.get("t_recv_ms"))
        if not isinstance(mint, str) or not mint or ts is None or ts < 1_000_000_000 or t_recv is None:
            return False
        if mint in self.seen or len(self.pending) >= MAX_PENDING:
            return False
        if now_ms is not None and now_ms - t_recv > self.stale_ms:
            self._audit({"mint": mint, "complete_slot": _int(row.get("slot")), "complete_t_recv_ms": t_recv}, None, "skipped_stale", [], now_ms)
            return False  # catching up after a restart: not an early signal
        self.seen.add(mint)
        self.pending.append({"mint": mint, "ts": ts, "t_recv_ms": t_recv, "slot": _int(row.get("slot"))})
        return True

    # --- evaluation ------------------------------------------------------------
    def poll(self, clock_ms: int | None) -> None:
        """Evaluate queued rows once the engine has applied every print received up to the
        complete row's own receive time (the engine drains with a holdback, so the same block's
        bonding prints may still be in its inbox before that)."""
        if clock_ms is None:
            return
        hour = clock_ms // HOUR_MS
        if self._hour is None:
            self._hour = hour
        elif hour != self._hour:
            self.flush_summary(clock_ms)
            self._hour = hour
        while self.pending and self.pending[0]["t_recv_ms"] <= clock_ms:
            item = self.pending.popleft()
            try:
                self._arm(item, clock_ms)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                try:
                    self._audit(item, None, "error", [], clock_ms, error=type(exc).__name__)
                except Exception:  # noqa: BLE001
                    pass
                wall = int(time.time())
                if wall - self._err_logged_s >= 60:
                    self._err_logged_s = wall
                    print(f"forward_paper early_arm_error n={self.errors} {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        if len(self.seen) > 4 * MAX_PENDING:
            self.seen.clear()

    def _audit(self, item: dict[str, Any], book: str | None, outcome: str, cands: list[dict[str, Any]], clock_ms: int | None, *, error: str | None = None) -> None:
        key = outcome.split(":", 1)[0] if outcome.startswith("skipped_risk") else outcome
        self.counts[key] = self.counts.get(key, 0) + 1
        log = self.engine.logs.get("arm_audit")
        if log is None:
            return
        log.write(
            {
                "schema": SCHEMA_AUDIT,
                "mint": item["mint"],
                "book": book,
                "complete_slot": item.get("slot", item.get("complete_slot")),
                "complete_t_recv_ms": item.get("t_recv_ms", item.get("complete_t_recv_ms")),
                "eval_clock_ms": clock_ms,
                "candidates": cands,
                "pass_any": any(c["pass"] for c in cands) if cands else False,
                "pass_all": all(c["pass"] for c in cands) if cands else False,
                "outcome": outcome,
                "error": error,
            }
        )

    def flush_summary(self, clock_ms: int | None = None) -> None:
        """Counts per outcome since the last summary (one row an hour; also at shutdown)."""
        log = self.engine.logs.get("arm_audit")
        if log is None or not self.counts:
            self.counts = {}
            return
        log.write({"schema": SCHEMA_AUDIT_SUMMARY, "eval_clock_ms": clock_ms, "counts": dict(sorted(self.counts.items())), "rows_written": self.rows_written, "errors": self.errors})
        self.counts = {}

    def _arm(self, item: dict[str, Any], clock_ms: int) -> None:
        eng = self.engine
        ex = eng.exp012
        if ex is None:
            return
        mint = item["mint"]
        track = eng.tracks.get(mint)
        acc = ex.acc.get(mint)
        if (acc is not None and acc.migrated) or (track is not None and track.migrate_done):
            self._audit(item, None, "skipped_migrated", [], clock_ms)
            return
        if eng.kill_file.is_file():
            self._audit(item, None, "skipped_kill", [], clock_ms)
            return
        creator = (acc.feat.creator or None) if acc is not None else None
        base_ms = item["ts"] * 1000
        for run in eng.books:
            spec = run.spec
            if spec.kind != "migrate" or spec.book_id not in eng.exp012_gates:
                continue
            risk = eng._risk_reason(run, mint, creator, base_ms, spec.size_lamports, ledger=run.ceiling, capped=True, readonly=True)
            gate = eng.exp012_gates[spec.book_id]
            cands: list[dict[str, Any]] = []
            for k in CANDIDATE_OFFSETS_S:
                mig_ms = base_ms + k * 1000
                feats, reason = ex.features_at(mint, mig_ms)
                score: float | None = None
                ok = False
                if feats is not None:
                    try:
                        score = float(gate.score(feats))
                        ok = score >= gate.threshold
                    except Exception:  # noqa: BLE001
                        score, ok, reason = None, False, "gate_error"
                cands.append({"mig_ms": mig_ms, "score": score, "pass": ok, "reason": reason})
            pass_any = any(c["pass"] for c in cands)
            if risk:
                self._audit(item, spec.book_id, f"skipped_risk:{risk}", cands, clock_ms)
                continue
            if not pass_any:
                self._audit(item, spec.book_id, "fail", cands, clock_ms)
                continue
            now = eng.latency.now_ms() if eng.latency.now_ms is not None else int(time.time() * 1000)
            lead_ms = now - item["t_recv_ms"]
            row = {
                "schema": SCHEMA_ARM,
                "book": spec.book_id,
                "ledger": "ceiling",
                "mint": mint,
                "creator": creator,
                "complete_slot": item["slot"],
                "complete_ts": item["ts"],
                "complete_t_recv_ms": item["t_recv_ms"],
                "pass_all": all(c["pass"] for c in cands),
                "pass_any": pass_any,
                "candidates": cands,
                "threshold": gate.threshold,
                "written_ms": now,
                "lead_ms": lead_ms,
                "runner_kill": False,
            }
            log = eng.logs.get("intents")
            if log is not None:
                log.write(row)
                self.rows_written += 1
            self._audit(item, spec.book_id, "armed", cands, clock_ms)
