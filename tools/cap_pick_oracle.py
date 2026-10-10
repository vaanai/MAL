"""CAP-PICK pick oracle: a boolean-only exporter and one shared reader (EXP-022 section 9 seal; EXP-025 5.3; DEC-024 section 6).

Why it exists. From 2026-10-16T01Z to the end of EXP-022's read, no other job may open, price or print the outcome of a counted CAP-PICK
pick. The H5 shadow, the H5 executor and the C1-NF shadow therefore need to know, per mint, one thing: "did the CAP-PICK gate pick it?".
They get a boolean and nothing else. This module is the only place that turns gate decision records into that boolean.

Two halves.

1. EXPORTER (`export`, runs as a MiScusi job on the host that holds the live gate's output, mal-fast-0).
   Tails decision records with a NARROW parser and appends rows to an append-only `picks.jsonl`:

       {"mint":"<base58>","pick":true|false,"t_ms":<exporter wall clock, ms>}      one per gate decision
       {"hb":true,"t_ms":<exporter wall clock, ms>}                                 staleness heartbeat

   Nothing else ever leaves the exporter: no score, no feature, no price, no P&L, no outcome, no decision time. A source line is NOT parsed
   as JSON. Two regexes pull the mint and the flag (the approach of the H5 executor's first pick reader); the rest of the line is never
   read into a value. A line with zero or two mints, or zero or two flags, is rejected and only counted.

   Source kinds (all are decision records; none is a position, fill, P&L or outcome file):
     gate     `exp012-gate.jsonl`, schema forward_paper_exp012_gate_v1: one row per gate decision. pick = its `entered` flag
              (the model gate passed; the same label the replay calls "pick"). A row that mentions gate_error is skipped
              (undecided, fail closed).
     intents  `intents.jsonl`, rows with schema forward_paper_intent_v1 (DEC-024 section 6 "decision-time intents"): pick = true.
              forward_paper_arm_v1 rows in the same file are ignored.
     replay   a `cap_pick_gate_replay_v1` decision list (EXP-022 section 2; the E0-pinned commit
              6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572): kind "decision" -> pick = entered; kind "dead" (pre-restart) -> pick = false.

   The exporter is idempotent and restart-safe: it loads the rows already in its output, writes a row only when it adds information
   (a first sight of a mint, or false -> true), and resumes by re-reading the sources from the start.
   The heartbeat is written only after a pass in which every configured source was readable, so a lost source goes stale in 60 s.

   FINAL GATE (EXP-022 section 9 "Nothing before the FINAL"; DEC-016:95, runner-side reads go only through tools/runner_timing_read.py
   until the read). The gate log and the intents are runner-side files, and their `entered` flags over the forward window are EXP-012's
   forward book. So the exporter opens NO source, and creates no output, until BOTH hold: the FINAL marker (`--final-marker`, written by the
   manager after the DEC-016 FINAL) is a file, and the wall clock is at or after EXPORT_EARLIEST_MS (2026-10-16T02:00:00Z, the same
   instant as tools/h5_executor.py ORACLE_EARLIEST_MS). A live run waits (stat calls only, no heartbeat, so every reader stays None);
   `--once` refuses (exit 3). It may therefore be submitted early.

2. READER (`PickOracle`), shared by the shadow, the executor and the C1-NF shadow. `PickOracle(mint) -> bool | None`.
     True   some source said pick. STICKY: a later false never undoes it, and it survives file rotation inside the process.
     False  at least one source decided the mint and no source picked it.
     None   undecided (no source has a row), OR the live feed is stale (no heartbeat within `stale_s`, default 60 s), OR the
            FINAL marker is not there, OR anything raised. A known True still answers True when stale (it can never flip).
   Every caller treats None as REFUSE. The union of several sources is the point: the live file (online decisions) plus the replay file
   (walk-2 replay picks, which cover receive-time and uptime misses of the live gate) are OR-ed.

   TIMING. The live gate decides on the runner's first PumpSwap print of the mint, after the runner's 300 ms holdback, the tape write and
   this exporter's poll. A consumer that sees the pool's first print directly (the H5 shadow) asks BEFORE the row exists, so the first
   answer is usually None. A caller must therefore never freeze a None it got at pool open: ask again at each decision point, freeze only
   a True (a pick never flips back), and treat None as "not yet, refuse for now".

   Wiring (three lines each):
     H5 executor     oracle = PickOracle(live=[cfg["pick_file"]], replay=cfg.get("pick_replay_files") or [])
                     (the executor already refuses on None and on a non-bool: seal_oracle_error). PR claude/h5-oracle-wire.
     H5 shadow       Engine(..., pick_oracle=oracle)    # the three-state answer, re-asked per decision point (not `.suppress` frozen at
                     s0, which would seal every pool: see TIMING). PR claude/h5-oracle-wire.
     C1-NF shadow    --pick-oracle tools.cap_pick_oracle:default_oracle        # reads the CAP_PICK_* environment below
   Environment for `default_oracle` / `from_env()`: CAP_PICK_LIVE and CAP_PICK_REPLAY (os.pathsep lists), CAP_PICK_FINAL_MARKER
   (REQUIRED: without it the oracle answers None for everything), CAP_PICK_STALE_S (default 60).

No key, no RPC, no transaction. It reads decision records only, never prints a mint or a flag, and prints counts only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

PICKS_SCHEMA_NOTE = "rows are exactly {mint,pick,t_ms} or {hb,t_ms}; see the module docstring"
STALE_S_DEFAULT = 60.0
HB_S_DEFAULT = 5.0
POLL_S_DEFAULT = 0.2
MAX_CHUNK = 8 * 1024 * 1024  # bytes per read call
MAX_PER_REFRESH = 256 * 1024 * 1024  # a reader never spends longer than this on one refresh
CLOCK_SKEW_OK_S = 5.0  # a heartbeat this far in the future is still fresh; further is a clock fault (fail closed)
# The exporter reads no runner file before this instant AND the FINAL marker (EXP-022 s9; DEC-016:95). 2026-10-16T02:00:00Z, the same instant
# as tools/h5_executor.py ORACLE_EARLIEST_MS. A code constant: no flag or environment variable moves it.
EXPORT_EARLIEST_MS = 1_792_116_000_000
FINAL_WAIT_POLL_S = 10.0  # while the FINAL gate is closed the exporter only stats the marker, this often

# The only two things pulled out of a decision line (plus three tag tests that read no value).
_MINT_RE = re.compile(r'"mint"\s*:\s*"([1-9A-HJ-NP-Za-km-z]{32,44})"')
_PICK_RE = re.compile(r'"pick"\s*:\s*(true|false)')
_ENTERED_RE = re.compile(r'"entered"\s*:\s*(true|false)')
_GATE_TAG_RE = re.compile(r'"schema"\s*:\s*"forward_paper_exp012_gate_v1"')
_INTENT_TAG_RE = re.compile(r'"schema"\s*:\s*"forward_paper_intent_v1"')
_REPLAY_DECISION_RE = re.compile(r'"kind"\s*:\s*"decision"')
_REPLAY_DEAD_RE = re.compile(r'"kind"\s*:\s*"dead"')
# Reader side: the heartbeat row and its clock.
_HB_RE = re.compile(r'"hb"\s*:\s*true')
_TMS_RE = re.compile(r'"t_ms"\s*:\s*([0-9]{1,16})\b')
_GATE_ERROR = '"gate_error"'

SOURCE_KINDS = ("gate", "intents", "replay")


# --- the narrow parser ---------------------------------------------------------------------------------------------------


def extract(kind: str, line: str) -> tuple[str, bool] | None:
    """(mint, pick) from one decision-record line, or None. The line is not parsed as JSON and nothing else of it is read into a value."""
    if kind == "gate":
        if not _GATE_TAG_RE.search(line) or _GATE_ERROR in line:
            return None
        flag_re = _ENTERED_RE
    elif kind == "intents":
        if not _INTENT_TAG_RE.search(line):
            return None
        m = _MINT_RE.findall(line)
        return (m[0], True) if len(m) == 1 else None
    elif kind == "replay":
        if _REPLAY_DEAD_RE.search(line) and not _REPLAY_DECISION_RE.search(line):
            m = _MINT_RE.findall(line)
            return (m[0], False) if len(m) == 1 else None
        if not _REPLAY_DECISION_RE.search(line):
            return None
        flag_re = _ENTERED_RE
    else:
        raise ValueError(f"unknown source kind {kind!r}")
    m, f = _MINT_RE.findall(line), flag_re.findall(line)
    if len(m) != 1 or len(f) != 1:
        return None
    return m[0], f[0] == "true"


def decision_row(mint: str, pick: bool, t_ms: int) -> str:
    """The one place an output decision row is built: three keys, no more."""
    if not isinstance(pick, bool) or not _MINT_RE.search('"mint":"%s"' % mint):
        raise ValueError("decision_row takes a base58 mint and a bool")
    return json.dumps({"mint": mint, "pick": pick, "t_ms": int(t_ms)}, separators=(",", ":"))


def heartbeat_row(t_ms: int) -> str:
    return json.dumps({"hb": True, "t_ms": int(t_ms)}, separators=(",", ":"))


# --- incremental line tailer (shared by exporter and reader) --------------------------------------------------------------


class Tailer:
    """Complete lines appended to a file since the last call. A partial last line waits. A new inode or a shorter file restarts at 0
    (`reset` is then True on the call that noticed it). A missing file raises OSError."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._off = 0
        self._ino: int | None = None

    def read_new(self, max_bytes: int = MAX_PER_REFRESH) -> tuple[list[str], bool]:
        st = self.path.stat()
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"not a regular file: {self.path.name}")
        reset = False
        if self._ino is not None and (st.st_ino != self._ino or st.st_size < self._off):
            self._off, reset = 0, True
        self._ino = st.st_ino
        lines: list[str] = []
        if st.st_size <= self._off:
            return lines, reset  # nothing appended: one stat, no open (the H5 shadow asks on every decision point of a pool)
        spent = 0
        with self.path.open("rb") as fh:
            while spent < max_bytes:
                fh.seek(self._off)
                data = fh.read(MAX_CHUNK)
                end = data.rfind(b"\n")
                if end < 0:
                    break
                lines.extend(x.decode("utf-8", "replace") for x in data[: end + 1].splitlines())
                self._off += end + 1
                spent += end + 1
                if len(data) < MAX_CHUNK:
                    break
        return lines, reset


# --- exporter ------------------------------------------------------------------------------------------------------------


class PicksWriter:
    """Append-only picks file. Remembers what it already holds so a restart or a duplicate source row writes nothing new."""

    def __init__(self, path: str | Path, now_ms: Callable[[], int]):
        self.path = Path(path)
        self.now_ms = now_ms
        self.known: dict[str, bool] = {}
        self.decisions_written = 0
        self.heartbeats_written = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            lines, _ = Tailer(self.path).read_new()
            for ln in lines:
                m, f = _MINT_RE.findall(ln), _PICK_RE.findall(ln)
                if len(m) == 1 and len(f) == 1:
                    self.known[m[0]] = self.known.get(m[0], False) or f[0] == "true"
        self._fh = self.path.open("a", encoding="utf-8")

    def decision(self, mint: str, pick: bool) -> bool:
        have = self.known.get(mint)
        if have is True or (have is False and not pick):
            return False  # nothing new: sticky true, or a repeated false
        self._fh.write(decision_row(mint, pick, self.now_ms()) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())  # decisions are rare (a handful per hour); the row is durable before a reader can want it
        self.known[mint] = pick
        self.decisions_written += 1
        return True

    def heartbeat(self) -> None:
        self._fh.write(heartbeat_row(self.now_ms()) + "\n")
        self._fh.flush()
        self.heartbeats_written += 1

    def close(self) -> None:
        self._fh.close()


class FinalNotWritten(RuntimeError):
    """`--once` before the FINAL gate opened: nothing was read."""


def final_gate_open(final_marker: str | Path | None, now: int) -> bool:
    """The exporter may open a source only when the FINAL marker is a file AND the clock is at or after EXPORT_EARLIEST_MS. Stats only."""
    if final_marker is None or now < EXPORT_EARLIEST_MS:
        return False
    try:
        return Path(final_marker).is_file()
    except OSError:
        return False


def run_export(sources: Sequence[tuple[str, str | Path]], out: str | Path, *, final_marker: str | Path | None, poll_s: float = POLL_S_DEFAULT,
               hb_s: float = HB_S_DEFAULT, once: bool = False, max_seconds: float | None = None, now_ms: Callable[[], int] | None = None,
               sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] | None = None) -> dict[str, int]:
    """Tail every source, append boolean rows to `out`. Returns counts only. `once`: one pass over what is there, then stop.
    Heartbeats are written only when `once` is False and at least one source is a live kind (gate or intents).
    Nothing is opened, and `out` is not created, until `final_gate_open` (see FINAL GATE in the module docstring): a live run waits for it,
    `once` raises FinalNotWritten. `final_marker` has no default on purpose."""
    clock = now_ms or (lambda: int(time.time() * 1000))
    for kind, _ in sources:
        if kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source kind {kind!r}")
    if not sources:
        raise ValueError("no source")
    counts = {"lines": 0, "rejected": 0, "source_errors": 0, "passes": 0, "final_wait_s": 0}
    t_start = clock()
    if not final_gate_open(final_marker, clock()):
        if once:
            raise FinalNotWritten("the FINAL gate is closed (no marker, or before 2026-10-16T02:00Z): nothing was read")
        if log:
            log("cap_pick_oracle export waiting_for_final (no source is opened before the FINAL marker and 2026-10-16T02:00Z)")
        while not final_gate_open(final_marker, clock()):
            if max_seconds is not None and clock() - t_start >= max_seconds * 1000:
                counts["final_wait_s"] = (clock() - t_start) // 1000
                counts.update(decisions_written=0, heartbeats_written=0)
                if log:
                    log("cap_pick_oracle export stopped_waiting_for_final " + " ".join(f"{k}={v}" for k, v in counts.items()))
                return counts
            sleep(FINAL_WAIT_POLL_S)
        counts["final_wait_s"] = (clock() - t_start) // 1000
    writer = PicksWriter(out, clock)
    tails = [(kind, Tailer(p)) for kind, p in sources]
    live = any(k in ("gate", "intents") for k, _ in sources) and not once
    last_hb: int | None = None
    try:
        while True:
            all_ok = True
            for kind, tail in tails:
                try:
                    lines, reset = tail.read_new()
                except OSError:
                    all_ok = False
                    counts["source_errors"] += 1
                    continue
                for ln in lines:
                    counts["lines"] += 1
                    hit = extract(kind, ln)
                    if hit is None:
                        counts["rejected"] += 1
                    else:
                        writer.decision(*hit)
            counts["passes"] += 1
            if live and all_ok and (last_hb is None or clock() - last_hb >= hb_s * 1000):
                writer.heartbeat()
                last_hb = clock()
            if once or (max_seconds is not None and clock() - t_start >= max_seconds * 1000):
                break
            sleep(poll_s)
    finally:
        writer.close()
    counts["decisions_written"] = writer.decisions_written
    counts["heartbeats_written"] = writer.heartbeats_written
    if log:
        log("cap_pick_oracle export " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return counts


# --- reader --------------------------------------------------------------------------------------------------------------


class _Source:
    def __init__(self, path: str | Path, live: bool):
        self.tail, self.live = Tailer(path), live
        self.flags: dict[str, bool] = {}
        self.last_hb_ms: int | None = None
        self.readable = False

    def refresh(self, sticky_true: set[str]) -> None:
        try:
            lines, reset = self.tail.read_new()
        except OSError:
            self.readable = False
            return
        self.readable = True
        if reset:
            self.flags, self.last_hb_ms = {}, None  # the file was replaced: its heartbeat and falses are gone; trues are kept in sticky_true
        for ln in lines:
            m, f = _MINT_RE.findall(ln), _PICK_RE.findall(ln)
            if len(m) == 1 and len(f) == 1:
                if f[0] == "true":
                    sticky_true.add(m[0])
                self.flags[m[0]] = self.flags.get(m[0], False) or f[0] == "true"
            elif not m and not f and _HB_RE.search(ln):
                t = _TMS_RE.findall(ln)
                if len(t) == 1:
                    self.last_hb_ms = max(self.last_hb_ms or 0, int(t[0]))


class PickOracle:
    """`oracle(mint) -> bool | None`. See the module docstring for the contract. Never raises from a call."""

    def __init__(self, live: Sequence[str | Path], replay: Sequence[str | Path] = (), *, stale_s: float = STALE_S_DEFAULT,
                 now_ms: Callable[[], int] | None = None, final_marker: str | Path | None = None):
        if not live:
            raise ValueError("a PickOracle needs at least one live picks file")
        self._src = [_Source(p, True) for p in live] + [_Source(p, False) for p in replay]
        self._true: set[str] = set()
        self.stale_s = float(stale_s)
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.final_marker = None if final_marker is None else Path(final_marker)
        self.counters = {"errors": 0, "stale": 0, "undecided": 0, "no_final": 0}

    def _final_ok(self) -> bool:
        return self.final_marker is None or self.final_marker.is_file()

    def staleness_s(self) -> float | None:
        """Seconds since the OLDEST live source's last heartbeat; None when a live source has never beaten, is unreadable, or its clock is in
        the future by more than the skew allowance. The replay files do not beat and do not count."""
        if not self._final_ok():
            return None
        now = self._now_ms()
        worst = 0.0
        for s in self._src:
            if not s.live:
                continue
            s.refresh(self._true)
            if not s.readable or s.last_hb_ms is None:
                return None
            age = (now - s.last_hb_ms) / 1000.0
            if age < -CLOCK_SKEW_OK_S:
                return None
            worst = max(worst, age)
        return max(worst, 0.0)

    def __call__(self, mint: str) -> bool | None:
        try:
            if not isinstance(mint, str) or not self._final_ok():
                self.counters["no_final"] += 1
                return None
            age = self.staleness_s()  # refreshes every live source
            for s in self._src:
                if not s.live:
                    s.refresh(self._true)
            if mint in self._true:
                return True
            if age is None or age > self.stale_s:
                self.counters["stale"] += 1
                return None
            if any(mint in s.flags for s in self._src):
                return False
            self.counters["undecided"] += 1
            return None
        except Exception:  # noqa: BLE001 - fail closed
            self.counters["errors"] += 1
            return None

    def suppress(self, mint: str | None) -> bool:
        """The H5 shadow's `suppress_outcome` hook: True = withhold. Only an exact False lets a mint through."""
        return mint is None or self(mint) is not False


def from_env(environ: dict[str, str] | None = None) -> "PickOracle | _ClosedOracle":
    env = os.environ if environ is None else environ
    live = [p for p in env.get("CAP_PICK_LIVE", "").split(os.pathsep) if p]
    replay = [p for p in env.get("CAP_PICK_REPLAY", "").split(os.pathsep) if p]
    marker = env.get("CAP_PICK_FINAL_MARKER", "")
    if not live or not marker:
        return _ClosedOracle()
    return PickOracle(live, replay, stale_s=float(env.get("CAP_PICK_STALE_S", STALE_S_DEFAULT)), final_marker=marker)


class _ClosedOracle:
    """Misconfigured (no live file or no FINAL marker in the environment): answers None for everything, so every caller refuses."""

    def staleness_s(self) -> None:
        return None

    def __call__(self, mint: str) -> None:
        return None

    def suppress(self, mint: str | None) -> bool:
        return True


class _LazyEnvOracle:
    """`tools.cap_pick_oracle:default_oracle` for the C1-NF shadow's `--pick-oracle`. Builds from the environment on first use."""

    def __init__(self) -> None:
        self._o: "PickOracle | _ClosedOracle | None" = None

    def _get(self):
        if self._o is None:
            self._o = from_env()
        return self._o

    def __call__(self, mint: str) -> bool | None:
        return self._get()(mint)

    def staleness_s(self) -> float | None:
        return self._get().staleness_s()

    def suppress(self, mint: str | None) -> bool:
        return self._get().suppress(mint)


default_oracle = _LazyEnvOracle()


# --- CLI -----------------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="CAP-PICK boolean pick exporter (EXP-022 section 9 seal). Prints counts only.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="tail decision records and append boolean rows to picks.jsonl")
    e.add_argument("--gate-log", action="append", default=[], metavar="PATH", help="exp012-gate.jsonl of the live gate runner (repeatable)")
    e.add_argument("--intents", action="append", default=[], metavar="PATH", help="intents.jsonl of the live gate runner (repeatable)")
    e.add_argument("--replay", action="append", default=[], metavar="PATH", help="a cap_pick_gate_replay_v1 decision list (repeatable)")
    e.add_argument("--out", required=True, help="the append-only booleans file")
    e.add_argument("--final-marker", required=True, metavar="PATH",
                   help="the FINAL marker the manager writes after the DEC-016 FINAL; no source is opened before it exists and before "
                        "2026-10-16T02:00Z (a live run waits, --once refuses with exit 3)")
    e.add_argument("--once", action="store_true", help="one pass over what is there, then stop (no heartbeat); the replay conversion mode")
    e.add_argument("--poll-s", type=float, default=POLL_S_DEFAULT)
    e.add_argument("--hb-s", type=float, default=HB_S_DEFAULT)
    e.add_argument("--max-seconds", type=float, default=None)
    c = sub.add_parser("check", help="print counts and the oracle's staleness for a picks file (no mint, no flag)")
    c.add_argument("--live", action="append", required=True, metavar="PATH")
    c.add_argument("--replay", action="append", default=[], metavar="PATH")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "check":
        o = PickOracle(a.live, a.replay)
        age = o.staleness_s()
        n_dec = sum(len(s.flags) for s in o._src)
        print(json.dumps({"staleness_s": age, "decided_mints": n_dec, "fresh": age is not None and age <= o.stale_s}))
        return 0 if age is not None and age <= o.stale_s else 1
    sources = [("gate", p) for p in a.gate_log] + [("intents", p) for p in a.intents] + [("replay", p) for p in a.replay]
    if not sources:
        print("refusing: give at least one of --gate-log, --intents, --replay", file=sys.stderr)
        return 2
    missing = [k for k, p in sources if not Path(p).is_file()]
    if missing:
        print(f"refusing: {len(missing)} source file(s) missing at start ({', '.join(sorted(set(missing)))})", file=sys.stderr)
        return 2
    try:
        counts = run_export(sources, a.out, final_marker=a.final_marker, poll_s=a.poll_s, hb_s=a.hb_s, once=a.once, max_seconds=a.max_seconds,
                            log=lambda s: print(s, file=sys.stderr, flush=True))
    except FinalNotWritten as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
