"""Live-entry gates of the H5 arena canary (DEC-032 s3 to s6, QP re-check 2026-10-23 "Before a first live send"). KEYLESS: no RPC, no key, no wallet.

Used only by tools/h5_arena_executor.py in a keyed (live) run, which stays unbuilt (LIVE_BUILT False). Three things live here so the executor module stays readable:

 1  V band: a live buy is refused when the pool's quote mint is not WSOL or its virtual quote reserve V is outside [17.5e9, 17.7e9]
    (DEC-031 Amendment 2 item 6; DEC-032 s3 "Venue and universe"). The band is the one the DECs define; nothing here invents one.
 2  ParityRecorder: writes the trip and attempt files in the exact schema tools/arena_live_parity.py validates (its docstring and validate_inputs), from the executor's own
    `buy` / `sell` ledger rows. exit_bound_ms is derived from the book's own constants (the plan's late instant s0 + LATE_SELL_S, else decision + LATE_SELL_S), never read from input.
    The twins file is NOT written here: it comes from tools/arena_paper.py (nofail leg, 1.9 s, per-send fees, no E1) outside the executor and is only checked for being readable.
 3  LiveGates: runs tools.arena_live_parity (rc 0 or no buy) and, once a UTC day has closed since the first send, tools.arena_tieout --check-last-day (rc 0 or no buy).

Per-attempt p_fail_press: the executor has no per-attempt pressure features, so each attempt carries arena_stack.PRESS_P_TARGET (0.289, the refit mean of the pressure leg).
The landing-fail excess then compares the live failure share with max(15%, 28.9%). Stated here and in the PR; it is the only value available without the paper features.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.arena_stack import PRESS_P_TARGET

V_BAND = (17_500_000_000, 17_700_000_000)  # DEC-031 Am.2 item 6, DEC-032 s3 (also tools/arena_live_bridge.py UNIVERSE_V_RANGE)
WSOL = "So11111111111111111111111111111111111111112"
LATE_SELL_MS = 335_000  # tools/h5_executor.py LATE_SELL_S (335 s after s0): the exit bound; asserted equal in the tests
PARITY_INTERVAL_S_MAX = 60  # DEC-032 s3: a parity result older than 60 s is stale (QP 10-23 W1; was 300). Config may only shorten it. The same bound is the freshness window.
PARITY_RUN_TIMEOUT_S = 60
PARITY_RETRY_AFTER_FAIL_MS = 15_000  # a failed run is not retried on every trigger
TIEOUT_CACHE_MS = 60_000
DAY_MS = 86_400_000
HARD_ARENA_HALTS = frozenset({"landed_buy_unreadable", "out_of_rule_entry", "stuck_position", "close_blocked_strand", "a3_pins_changed", "feed_break"})  # unresolved = latched


def v_band_refusal(v_lamports: Any, quote_mint: Any) -> str | None:
    """None when the pool is canonical (WSOL quote, V in the band); else the refusal label. Quote mint first (DEC-031 Am.2 item 6), then V. Fail closed on any non-int."""
    if str(quote_mint) != WSOL:
        return f"v_band:quote_mint_not_wsol:{quote_mint}"
    if isinstance(v_lamports, bool) or not isinstance(v_lamports, int):
        return "v_band:v_unreadable"
    if not V_BAND[0] <= v_lamports <= V_BAND[1]:
        return f"v_band:v_{v_lamports}_outside_{V_BAND[0]}_{V_BAND[1]}"
    return None


def _write_jsonl_atomic(path: Path, rows: list[Mapping[str, Any]]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in rows))
    os.replace(tmp, path)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


class ParityRecorder:
    """The executor's trip and attempt files, rewritten atomically on every change. Trip id = the mint (a mint is bought once per run: state.bought)."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.trips_path, self.attempts_path = directory / "trips.jsonl", directory / "attempts.jsonl"
        self.broken: str | None = None
        self.seq = 0  # bumped on every landed buy and every closed trip: the parity result must postdate it
        self.last_landed_ms: int | None = None
        self.last_gap_ms: int | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self.trips = {r["trip_id"]: r for r in _load_jsonl(self.trips_path)}
            self.attempts = {r["attempt_id"]: r for r in _load_jsonl(self.attempts_path)}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.trips, self.attempts = {}, {}
            self.broken = f"parity_files_unreadable:{type(exc).__name__}"
        if not self.broken and not (self.trips_path.exists() and self.attempts_path.exists()):
            self._save()  # the parity tool refuses a missing file: an empty one is a valid "no trips yet"
        for r in [*self.trips.values(), *self.attempts.values()]:
            for k in ("closed_ms", "exit_landed_ms", "buy_landed_ms", "landed_ms"):
                if isinstance(r.get(k), int):
                    self.last_landed_ms = max(self.last_landed_ms or 0, r[k])

    def _save(self) -> None:
        try:
            _write_jsonl_atomic(self.trips_path, list(self.trips.values()))
            _write_jsonl_atomic(self.attempts_path, list(self.attempts.values()))
        except (OSError, ValueError) as exc:
            self.broken = f"parity_files_unwritable:{type(exc).__name__}"

    def note_gap(self, now_ms: int) -> None:
        """A feed gap record: every trip whose exit has not landed is marked `gap` (DEC-032 s5 item 2); a buy that lands later and was decided before it is marked at landing."""
        self.last_gap_ms = now_ms
        changed = False
        for t in self.trips.values():
            if not t["exit_landed"] and not t["gap"]:
                t["gap"], changed = True, True
        if changed:
            self._save()

    def on_buy(self, mint: str, kw: Mapping[str, Any], now_ms: int, exit_bound_ms: int | None, stake_fallback: int | None = None) -> str | None:
        """One `buy` ledger row (landed true or false, including expired). Writes the attempt, and the trip when tokens arrived.

        QP 10-23 W2: a landed buy whose token or spend amount cannot be read is NEVER a failed attempt with no trip. It opens a trip flagged `unreadable` (stake =
        the row's spend, else `stake_fallback`; price = stake / expected tokens, or 1.0) so that B3, P3, P4 and the stuck rule still see the position. Returns
        "landed_buy_unreadable" for that case (the caller latches), else None. A readable landed buy with zero tokens stays a failed attempt (SOL gone, no position)."""
        decision = kw.get("decision_t_ms")
        decision = decision if isinstance(decision, int) and not isinstance(decision, bool) else now_ms
        landed_ms = kw.get("confirm_seen_ms") if isinstance(kw.get("confirm_seen_ms"), int) else now_ms
        tokens = kw.get("tokens_received")
        spend = kw.get("spend_lamports")
        landed = bool(kw.get("landed"))
        tok_ok = isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0
        spend_ok = isinstance(spend, int) and not isinstance(spend, bool) and spend > 0
        unreadable = landed and (not tok_ok or not spend_ok or kw.get("tokens_source") == "quote_estimate")
        opened = landed and (unreadable or (tok_ok and tokens > 0 and spend_ok))
        aid = str(kw.get("signature") or f"{mint}:{decision}")
        if aid in self.attempts:
            return None  # a row is never booked twice (a restart replays nothing, but the file refuses duplicates)
        att: dict[str, Any] = {"attempt_id": aid, "decision_ms": decision, "landed": opened, "failed_or_expired": not opened,
                               "p_fail_press": PRESS_P_TARGET, "mint": mint}
        if opened:
            att.update(landed_ms=landed_ms, trip_id=mint)
            stake = spend if spend_ok else stake_fallback if isinstance(stake_fallback, int) and stake_fallback > 0 else 1
            exp = kw.get("expected_tokens")
            if unreadable:
                price = stake / exp if isinstance(exp, int) and not isinstance(exp, bool) and exp > 0 else 1.0
            else:
                price = spend / tokens  # stake lamports per raw token: the twins file's entry_price must use the same unit
            self.trips[mint] = {
                "trip_id": mint, "stake_lamports": stake, "decision_ms": decision, "buy_landed_ms": landed_ms, "live_buy_price": price,
                "gap": self.last_gap_ms is not None and self.last_gap_ms >= decision, "exit_landed": False,
                "exit_bound_ms": int(exit_bound_ms if exit_bound_ms is not None else decision + LATE_SELL_MS),
            }
            for k in ("fee_lamports",):  # the landed buy's own fee, as sent (the twin producer prices the twin at it)
                if isinstance(kw.get(k), int) and not isinstance(kw.get(k), bool):
                    self.trips[mint]["buy_fee_lamports"] = kw[k]
            if unreadable:
                self.trips[mint]["unreadable"] = True
            self.last_landed_ms = max(self.last_landed_ms or 0, landed_ms)
            self.seq += 1
        self.attempts[aid] = att
        self._save()
        return "landed_buy_unreadable" if unreadable else None

    def on_sell(self, mint: str, kw: Mapping[str, Any], now_ms: int) -> None:
        """One landed `sell` ledger row that closed the position (it carries pnl_lamports). A failed or expired sell changes nothing."""
        t = self.trips.get(mint)
        if t is None or t["exit_landed"] or not kw.get("landed") or not isinstance(kw.get("pnl_lamports"), int):
            return
        tokens, got = kw.get("tokens"), kw.get("sol_received_lamports")
        if not (isinstance(tokens, int) and tokens > 0 and isinstance(got, int)):
            return
        landed_ms = kw.get("confirm_seen_ms") if isinstance(kw.get("confirm_seen_ms"), int) else now_ms
        t.update(exit_landed=True, exit_landed_ms=landed_ms, closed_ms=landed_ms, exit_reason=str(kw.get("exit_reason") or "unknown"),
                 live_net_lamports=kw["pnl_lamports"], live_sell_price=got / tokens)
        self.last_landed_ms = max(self.last_landed_ms or 0, landed_ms)
        self.seq += 1
        self._save()


Runner = Callable[[list[str]], int]


def default_runner(argv: list[str]) -> int:
    """`python -m <module> ...` in the repo root. A crash, a timeout or a missing interpreter is rc 99 (no buy)."""
    try:
        return subprocess.run([sys.executable, "-m", *argv], cwd=str(Path(__file__).resolve().parent.parent), capture_output=True,
                              timeout=PARITY_RUN_TIMEOUT_S).returncode
    except (OSError, subprocess.SubprocessError):
        return 99


def utc_day(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class LiveGates:
    """Parity and tie-out gates for the live buy path. `state_builder(now_ms)` returns the parity state.json content (the executor stamps only reads that succeeded)."""

    def __init__(self, directory: Path, recorder: ParityRecorder, twins_file: str, tieout_dir: str, stop_file: str,
                 state_builder: Callable[[int], dict[str, Any]], interval_s: float | None = None, runner: Runner | None = None):
        self.dir, self.recorder, self.twins_file, self.tieout_dir, self.stop_file = directory, recorder, twins_file, tieout_dir, stop_file
        self.state_builder = state_builder
        self.interval_ms = int(min(float(interval_s if interval_s is not None else PARITY_INTERVAL_S_MAX), PARITY_INTERVAL_S_MAX) * 1000)
        self.runner: Runner = runner or default_runner
        self.state_path = directory / "state.json"
        self.parity_run: dict[str, Any] | None = None  # {"rc", "at_ms", "seq"}
        self.tieout_ok_until: tuple[str, int] | None = None  # (last closed day, valid until ms)

    # -- parity
    def run_parity(self, now_ms: int) -> int:
        """Write state.json, run the tool. rc 0 only when every file was written and the tool exited 0."""
        rc = 99
        try:
            if self.recorder.broken:
                raise OSError(self.recorder.broken)
            st = self.state_builder(now_ms)
            if self.recorder.last_landed_ms is not None:
                st["last_landed_ms"] = self.recorder.last_landed_ms
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name("state.json.tmp")
            tmp.write_text(json.dumps(st, sort_keys=True, allow_nan=False))
            os.replace(tmp, self.state_path)
            rc = self.runner(["tools.arena_live_parity", "--trips", str(self.recorder.trips_path), "--twins", self.twins_file,
                              "--attempts", str(self.recorder.attempts_path), "--state", str(self.state_path), "--now-ms", str(now_ms),
                              "--stop-file", self.stop_file, "--json", str(self.dir / "parity-result.json")])
        except (Exception, SystemExit):  # nothing may escape into the trading loop; a failed run is no buy
            rc = 99
        self.parity_run = {"rc": rc, "at_ms": now_ms, "seq": self.recorder.seq}
        return rc

    def parity_fresh(self, now_ms: int) -> bool:
        r = self.parity_run
        return r is not None and r["rc"] == 0 and r["seq"] == self.recorder.seq and 0 <= now_ms - r["at_ms"] <= self.interval_ms

    def maybe_run_parity(self, now_ms: int) -> None:
        """Off the hot path (the tick): rerun every N minutes and after every landed buy or closed trip."""
        r = self.parity_run
        if r is None or r["seq"] != self.recorder.seq or now_ms - r["at_ms"] >= self.interval_ms or now_ms < r["at_ms"]:
            if r is not None and r["rc"] != 0 and 0 <= now_ms - r["at_ms"] < PARITY_RETRY_AFTER_FAIL_MS and r["seq"] == self.recorder.seq:
                return
            self.run_parity(now_ms)

    def parity_refusal(self, now_ms: int) -> str | None:
        """Before every live buy: a fresh rc-0 result that postdates the last landed buy or closed trip, else run it now. Anything else is no buy."""
        if self.parity_fresh(now_ms):
            return None
        r = self.parity_run
        if r is not None and r["rc"] != 0 and r["seq"] == self.recorder.seq and 0 <= now_ms - r["at_ms"] < PARITY_RETRY_AFTER_FAIL_MS:
            return f"parity_gate:rc_{r['rc']}"
        rc = self.run_parity(now_ms)
        return None if rc == 0 else f"parity_gate:rc_{rc}"

    # -- tie-out
    def tieout_refusal(self, now_ms: int, first_attempt_ms: int | None) -> str | None:
        """Once a UTC day has closed since the first send: the last closed day needs a signed-off tie-out result (arena_tieout --check-last-day, rc 0)."""
        if first_attempt_ms is None:
            return None  # nothing sent yet: no day of the canary has closed
        last_closed = utc_day(now_ms - (now_ms % DAY_MS) - DAY_MS)
        if last_closed < utc_day(first_attempt_ms):
            return None  # the first-send day is still open
        c = self.tieout_ok_until
        if c is not None and c[0] == last_closed and now_ms < c[1]:
            return None
        rc = self.runner(["tools.arena_tieout", "--check-last-day", self.tieout_dir, "--now-ms", str(now_ms), "--stop-file", self.stop_file])
        if rc != 0:
            self.tieout_ok_until = None
            return f"tieout_gate:rc_{rc}"
        self.tieout_ok_until = (last_closed, now_ms + TIEOUT_CACHE_MS)
        return None
